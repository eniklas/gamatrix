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
from aws_cdk import aws_ses as ses
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_ssm as ssm
from constructs import Construct

from config import DeployConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TABLE_PREFIX = "gamatrix"
DEFAULT_EMAIL_FROM = "noreply@example.com"


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
        queue = sqs.Queue(
            self,
            "EnrichmentQueue",
            visibility_timeout=Duration.minutes(16),  # > enricher timeout
            retention_period=Duration.days(1),
        )
        igdb_secret = secrets.Secret(
            self, "IgdbCredentials", secret_name="gamatrix/igdb"
        )
        self._create_ssm_params()

        # Custom domain / SES are wired only when the deployment config supplies
        # a hosted zone + site domain. Otherwise the stack still deploys and the
        # app is reached via the default API Gateway URL.
        hosted_zone = None
        if cfg.has_custom_domain:
            # Existing Route 53 hosted zone. Used to DNS-validate the ACM cert,
            # alias the custom domain, and add SES DKIM records.
            hosted_zone = route53.HostedZone.from_lookup(
                self, "HostedZone", domain_name=cfg.hosted_zone
            )
            # Verify the domain for sending via SES; DKIM CNAMEs are added to
            # the zone automatically so the sender address can send once
            # deployed.
            ses.EmailIdentity(
                self,
                "EmailIdentity",
                identity=ses.Identity.public_hosted_zone(hosted_zone),
            )

        common_env = {
            "TABLE_PREFIX": TABLE_PREFIX,
            "UPLOAD_BUCKET": upload_bucket.bucket_name,
            "ENRICHMENT_QUEUE_URL": queue.queue_url,
            "IGDB_SECRET_NAME": igdb_secret.secret_name,
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
        # SES send for the web Lambda (password-reset email).
        web_fn.add_to_role_policy(self._ses_send_policy())

        # Triggers.
        enricher_fn.add_event_source(sources.SqsEventSource(queue, batch_size=1))
        upload_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(parser_fn),
            s3.NotificationKeyFilter(prefix="uploads/"),
        )

        http_api = self._http_api(web_fn, cfg, hosted_zone)

        # The app builds password-reset links from APP_BASE_URL. Use the custom
        # domain when configured, else the default API Gateway URL.
        if cfg.has_custom_domain:
            base_url = f"https://{cfg.site_domain}"
        else:
            base_url = http_api.url or ""
        web_fn.add_environment("APP_BASE_URL", base_url)

        CfnOutput(self, "SiteUrl", value=base_url)
        CfnOutput(self, "ApiUrl", value=http_api.url or "")
        CfnOutput(self, "UploadBucket", value=upload_bucket.bucket_name)
        CfnOutput(self, "IgdbSecretArn", value=igdb_secret.secret_arn)

    # ------------------------------------------------------------------
    def _http_api(
        self,
        web_fn: lambda_.IFunction,
        cfg: DeployConfig,
        hosted_zone: route53.IHostedZone | None,
    ) -> apigw.HttpApi:
        integration = integrations.HttpLambdaIntegration("WebIntegration", web_fn)

        if not (cfg.has_custom_domain and hosted_zone is not None):
            return apigw.HttpApi(self, "HttpApi", default_integration=integration)

        # TLS cert for the custom domain, DNS-validated through Route 53.
        certificate = acm.Certificate(
            self,
            "SiteCertificate",
            domain_name=cfg.site_domain,
            validation=acm.CertificateValidation.from_dns(hosted_zone),
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
        # Alias record so the hostname resolves to the API Gateway domain.
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
