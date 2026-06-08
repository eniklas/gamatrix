"""Application configuration, sourced from environment variables.

In AWS, values come from Lambda environment variables, Secrets Manager (IGDB
credentials), and SSM Parameter Store (hidden/single-player lists). Locally,
they come from the .env file loaded by docker-compose. A single Settings
object is shared process-wide via get_settings().
"""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Runtime mode ---
    # True when running under docker-compose against local AWS replacements.
    local_dev: bool = False

    # --- AWS endpoints (overridden locally to point at dynamodb-local/minio) ---
    aws_region: str = "ca-central-1"
    dynamodb_endpoint_url: str | None = None
    s3_endpoint_url: str | None = None
    sqs_endpoint_url: str | None = None

    # --- DynamoDB ---
    table_prefix: str = "gamatrix"

    # --- S3 ---
    upload_bucket: str = "gamatrix-gog-db-uploads"
    # Browser-facing S3 endpoint. Locally the app container talks to minio on the
    # Docker network, but the browser must upload to a host-reachable URL.
    public_s3_endpoint_url: str | None = None

    # --- SQS (unset locally; the local_worker polls the jobs table instead) ---
    enrichment_queue_url: str | None = None

    # --- Auth ---
    jwt_secret: str = DEFAULT_JWT_SECRET
    # Secrets Manager secret name holding the JWT signing key; when set (in AWS)
    # it takes precedence over the plain jwt_secret above.
    jwt_secret_name: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_ttl_hours: int = 24
    reset_token_ttl_minutes: int = 60

    # --- IGDB / Twitch ---
    igdb_client_id: str = ""
    igdb_client_secret: str = ""
    # Secrets Manager secret name holding {client_id, client_secret}; when set
    # (in AWS) it takes precedence over the plain env vars above.
    igdb_secret_name: str | None = None

    # --- Email (SES in AWS, SMTP/mailhog locally) ---
    email_from: str = "gamatrix@example.com"
    smtp_host: str | None = None
    smtp_port: int = 1025

    # --- Behavior ---
    app_base_url: str = "http://localhost:8080"
    igdb_stale_days: int = 30

    # SSM parameter names for the title filter lists (AWS only). Locally these
    # are seeded into DynamoDB config and read from there.
    hidden_games_param: str = "/gamatrix/hidden-games"
    single_player_param: str = "/gamatrix/single-player-games"

    # --- Derived table names ---
    @property
    def games_table(self) -> str:
        return f"{self.table_prefix}_games"

    @property
    def users_table(self) -> str:
        return f"{self.table_prefix}_users"

    @property
    def libraries_table(self) -> str:
        return f"{self.table_prefix}_user_libraries"

    @property
    def jobs_table(self) -> str:
        return f"{self.table_prefix}_enrichment_jobs"

    @property
    def metadata_table(self) -> str:
        return f"{self.table_prefix}_metadata_overrides"

    @property
    def profile_pics_table(self) -> str:
        """Holds user-uploaded profile-pic bytes, keyed by user_id."""
        return f"{self.table_prefix}_profile_pics"

    @property
    def config_table(self) -> str:
        """Small key/value table; locally holds the hidden/single-player lists."""
        return f"{self.table_prefix}_config"

    @model_validator(mode="after")
    def _require_jwt_secret(self) -> "Settings":
        """Fail loudly rather than silently signing sessions with the public
        default secret in production. Either set JWT_SECRET directly or point
        JWT_SECRET_NAME at a Secrets Manager secret."""
        if (
            not self.local_dev
            and not self.jwt_secret_name
            and self.jwt_secret == DEFAULT_JWT_SECRET
        ):
            raise ValueError(
                "JWT_SECRET must be set in production (or set JWT_SECRET_NAME to "
                "source it from Secrets Manager)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def resolve_igdb_credentials(settings: Settings) -> tuple[str, str]:
    """Return (client_id, client_secret), preferring Secrets Manager in AWS."""
    if settings.igdb_secret_name:
        import boto3

        client = boto3.client("secretsmanager", region_name=settings.aws_region)
        secret = json.loads(
            client.get_secret_value(SecretId=settings.igdb_secret_name)["SecretString"]
        )
        return secret["client_id"], secret["client_secret"]
    return settings.igdb_client_id, settings.igdb_client_secret


@lru_cache
def _fetch_secret_string(secret_name: str, region: str) -> str:
    import boto3

    client = boto3.client("secretsmanager", region_name=region)
    return client.get_secret_value(SecretId=secret_name)["SecretString"]


def resolve_jwt_secret(settings: Settings) -> str:
    """Return the JWT signing secret, preferring Secrets Manager in AWS. The
    Secrets Manager value is cached so warm Lambdas don't fetch it per request."""
    if settings.jwt_secret_name:
        return _fetch_secret_string(settings.jwt_secret_name, settings.aws_region)
    return settings.jwt_secret
