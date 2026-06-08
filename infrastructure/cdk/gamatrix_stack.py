"""Gamatrix AWS infrastructure.

Defines everything the app needs: DynamoDB tables, the upload bucket, the
enrichment queue, three Lambda functions (web/enricher/parser), an HTTP API in
front of the web Lambda, the IGDB secret, SSM parameters, and -- when a
deployment config supplies a domain -- a custom domain, ACM cert, Route 53
alias, and SES identity.

The Lambdas are built from the project's container image targets (see
Dockerfile), so the same code that runs locally runs in AWS.

Domain/email values are not hardcoded here; they come from a private config
file (see config.py / DeployConfig). With no config the stack still deploys,
exposing the default API Gateway URL and skipping SES.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as sources
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as route53_targets
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_notifications as s3n
from aws_cdk import aws_secretsmanager as secrets
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from config import DeployConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TABLE_PREFIX = "gamatrix"
DEFAULT_EMAIL_FROM = "noreply@example.com"


def _project_version() -> str:
    """Latest semver git tag, passed into the image build as the package version.

    The Docker build context excludes .git, so setuptools_scm can't derive the
    version itself; we resolve it here at synth time and forward it as a build
    arg (SETUPTOOLS_SCM_PRETEND_VERSION).
    """
    try:
        return subprocess.check_output(
            [
                "git",
                "describe",
                "--tags",
                "--abbrev=0",
                "--match",
                "[0-9]*.[0-9]*.[0-9]*",
            ],
            cwd=PROJECT_ROOT,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "0.0.0"


class GamatrixStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        deploy_config: DeployConfig | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cfg = deploy_config or DeployConfig()

        tables = self._create_tables()
        upload_bucket = self._create_bucket()
        # Dead-letter queue for enrichment jobs the enricher can't process.
        # Without this, a failing/timing-out job is redelivered for the full
        # retention period, and each redelivery re-runs the job — historically
        # inflating progress counts (#131). Capping redeliveries stops the
        # storm; dead messages are retained longer for inspection/manual redrive.
        enrichment_dlq = sqs.Queue(
            self,
            "EnrichmentDLQ",
            retention_period=Duration.days(14),
        )
        queue = sqs.Queue(
            self,
            "EnrichmentQueue",
            visibility_timeout=Duration.minutes(16),  # > enricher timeout
            retention_period=Duration.days(1),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=enrichment_dlq,
            ),
        )
        igdb_secret = secrets.Secret(
            self, "IgdbCredentials", secret_name="gamatrix/igdb"
        )
        # JWT signing key, generated once and stable across deploys (so existing
        # sessions survive redeploys). The web Lambda reads it via JWT_SECRET_NAME.
        jwt_secret = secrets.Secret(
            self,
            "JwtSecret",
            secret_name="gamatrix/jwt-secret",
            generate_secret_string=secrets.SecretStringGenerator(
                password_length=64,
                exclude_punctuation=True,
            ),
        )
        self._create_ssm_params()

        # Custom domain / SES are wired only when the deployment config supplies
        # a hosted zone + site domain. Otherwise the stack still deploys and the
        # app is reached via the default API Gateway URL.
        hosted_zone = None
        alias_hosted_zone = None
        if cfg.has_custom_domain:
            hosted_zone = route53.HostedZone.from_lookup(
                self, "HostedZone", domain_name=cfg.hosted_zone
            )
            if cfg.alias_domains and cfg.alias_hosted_zone:
                # Reuse the primary zone lookup if they happen to be the same.
                if cfg.alias_hosted_zone == cfg.hosted_zone:
                    alias_hosted_zone = hosted_zone
                else:
                    alias_hosted_zone = route53.HostedZone.from_lookup(
                        self, "AliasHostedZone", domain_name=cfg.alias_hosted_zone
                    )

        common_env = {
            "TABLE_PREFIX": TABLE_PREFIX,
            "UPLOAD_BUCKET": upload_bucket.bucket_name,
            "ENRICHMENT_QUEUE_URL": queue.queue_url,
            "IGDB_SECRET_NAME": igdb_secret.secret_name,
            "JWT_SECRET_NAME": jwt_secret.secret_name,
            "EMAIL_FROM": cfg.email_from or DEFAULT_EMAIL_FROM,
            "IGDB_STALE_DAYS": "30",
        }

        web_fn = self._lambda(
            "WebFn",
            "lambda-web",
            "gamatrix.lambda_handler.handler",
            env=common_env,
            timeout=Duration.seconds(30),
        )
        enricher_fn = self._lambda(
            "EnricherFn",
            "lambda-enricher",
            "handler.handler",
            env=common_env,
            timeout=Duration.minutes(15),
        )
        parser_fn = self._lambda(
            "ParserFn",
            "lambda-parser",
            "handler.handler",
            env=common_env,
            timeout=Duration.minutes(5),
            memory=1024,
        )

        # Wire permissions.
        for table in tables.values():
            for fn in (web_fn, enricher_fn, parser_fn):
                table.grant_read_write_data(fn)
        upload_bucket.grant_read_write(web_fn)
        upload_bucket.grant_read_write(parser_fn)
        queue.grant_send_messages(web_fn)
        queue.grant_send_messages(parser_fn)
        igdb_secret.grant_read(enricher_fn)
        jwt_secret.grant_read(web_fn)
        # SES send for the web Lambda (password-reset email).
        web_fn.add_to_role_policy(self._ses_send_policy())

        # Triggers.
        enricher_fn.add_event_source(sources.SqsEventSource(queue, batch_size=1))
        upload_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(parser_fn),
            s3.NotificationKeyFilter(prefix="uploads/"),
        )

        http_api = self._http_api(web_fn, cfg, hosted_zone, alias_hosted_zone)

        # The app builds password-reset links from APP_BASE_URL. Use the custom
        # domain when configured, else the default API Gateway URL.
        if cfg.has_custom_domain:
            base_url = f"https://{cfg.site_domain}"
        else:
            base_url = http_api.url or ""
        web_fn.add_environment("APP_BASE_URL", base_url)

        CfnOutput(self, "SiteUrl", value=base_url)
        CfnOutput(self, "ApiUrl", value=http_api.url or "")
        CfnOutput(self, "UploadBucketName", value=upload_bucket.bucket_name)
        CfnOutput(self, "IgdbSecretArn", value=igdb_secret.secret_arn)

    # ------------------------------------------------------------------
    def _http_api(
        self,
        web_fn: lambda_.IFunction,
        cfg: DeployConfig,
        hosted_zone: route53.IHostedZone | None,
        alias_hosted_zone: route53.IHostedZone | None = None,
    ) -> apigw.HttpApi:
        integration = integrations.HttpLambdaIntegration("WebIntegration", web_fn)

        if not (cfg.has_custom_domain and hosted_zone is not None):
            return apigw.HttpApi(self, "HttpApi", default_integration=integration)

        # Build a validation map so primary and alias domains can live in
        # different Route 53 hosted zones.
        validation_map: dict[str, route53.IHostedZone] = {
            cfg.site_domain: hosted_zone  # type: ignore[index]
        }
        aliases_to_wire: list[str] = []
        if alias_hosted_zone and cfg.alias_domains:
            for alias in cfg.alias_domains:
                validation_map[alias] = alias_hosted_zone
                aliases_to_wire.append(alias)

        certificate = acm.Certificate(
            self,
            "SiteCertificate",
            domain_name=cfg.site_domain,
            subject_alternative_names=aliases_to_wire or None,
            validation=acm.CertificateValidation.from_dns_multi_zone(validation_map),
        )
        domain_name = apigw.DomainName(
            self,
            "ApiDomain",
            domain_name=cfg.site_domain,
            certificate=certificate,
        )
        http_api = apigw.HttpApi(
            self,
            "HttpApi",
            default_integration=integration,
            default_domain_mapping=apigw.DomainMappingOptions(domain_name=domain_name),
        )
        route53.ARecord(
            self,
            "SiteAliasRecord",
            zone=hosted_zone,
            record_name=cfg.site_domain,
            target=route53.RecordTarget.from_alias(
                route53_targets.ApiGatewayv2DomainProperties(
                    domain_name.regional_domain_name,
                    domain_name.regional_hosted_zone_id,
                )
            ),
        )

        # Wire each alias: its own API Gateway domain name + mapping + A record.
        for i, alias in enumerate(aliases_to_wire):
            alias_dn = apigw.DomainName(
                self,
                f"AliasDomain{i}",
                domain_name=alias,
                certificate=certificate,
            )
            apigw.ApiMapping(
                self,
                f"AliasMapping{i}",
                api=http_api,
                domain_name=alias_dn,
            )
            route53.ARecord(
                self,
                f"AliasARecord{i}",
                zone=alias_hosted_zone,  # type: ignore[arg-type]
                record_name=alias,
                target=route53.RecordTarget.from_alias(
                    route53_targets.ApiGatewayv2DomainProperties(
                        alias_dn.regional_domain_name,
                        alias_dn.regional_hosted_zone_id,
                    )
                ),
            )

        return http_api

    def _create_tables(self) -> dict[str, dynamodb.Table]:
        tables: dict[str, dynamodb.Table] = {}

        def table(name: str, pk: str, sk: str | None = None) -> dynamodb.Table:
            t = dynamodb.Table(
                self,
                name,
                table_name=f"{TABLE_PREFIX}_{name.lower()}",
                partition_key=dynamodb.Attribute(
                    name=pk, type=dynamodb.AttributeType.STRING
                ),
                sort_key=(
                    dynamodb.Attribute(name=sk, type=dynamodb.AttributeType.STRING)
                    if sk
                    else None
                ),
                billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
                removal_policy=RemovalPolicy.RETAIN,
                point_in_time_recovery=True,
            )
            tables[name] = t
            return t

        table("games", "release_key")
        table("users", "email")
        libraries = table("user_libraries", "user_id", "release_key")
        libraries.add_global_secondary_index(
            index_name="release_key-index",
            partition_key=dynamodb.Attribute(
                name="release_key", type=dynamodb.AttributeType.STRING
            ),
        )
        table("enrichment_jobs", "job_id")
        table("metadata_overrides", "slug")
        table("profile_pics", "user_id")
        table("config", "key")
        return tables

    def _create_bucket(self) -> s3.Bucket:
        return s3.Bucket(
            self,
            "UploadBucket",
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(1))],
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.POST],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                )
            ],
        )

    def _create_ssm_params(self) -> None:
        ssm.StringParameter(
            self,
            "HiddenGames",
            parameter_name="/gamatrix/hidden-games",
            string_value="[]",
        )
        ssm.StringParameter(
            self,
            "SinglePlayerGames",
            parameter_name="/gamatrix/single-player-games",
            string_value="[]",
        )
        ssm.StringParameter(
            self,
            "IgdbStaleDays",
            parameter_name="/gamatrix/igdb-stale-days",
            string_value="30",
        )

    def _lambda(
        self,
        construct_id: str,
        image_target: str,
        cmd: str,
        env: dict,
        timeout: Duration,
        memory: int = 512,
    ) -> lambda_.DockerImageFunction:
        return lambda_.DockerImageFunction(
            self,
            construct_id,
            code=lambda_.DockerImageCode.from_image_asset(
                str(PROJECT_ROOT),
                file="Dockerfile",
                target=image_target,
                cmd=[cmd],
                build_args={
                    "SETUPTOOLS_SCM_PRETEND_VERSION": _project_version(),
                },
                exclude=[
                    ".git",
                    ".venv",
                    "cdk.out",
                    "infrastructure/cdk/cdk.out",
                    "__pycache__",
                    "*.pyc",
                    "*.pyo",
                    ".mypy_cache",
                    ".pytest_cache",
                    "htmlcov",
                    ".coverage",
                    "dist",
                    "build",
                    "node_modules",
                ],
            ),
            environment=env,
            timeout=timeout,
            memory_size=memory,
        )

    def _ses_send_policy(self):
        from aws_cdk import aws_iam as iam

        return iam.PolicyStatement(
            actions=["ses:SendEmail", "ses:SendRawEmail"],
            resources=["*"],
        )
