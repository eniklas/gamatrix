"""Shared test fixtures.

Sets a test table prefix and dummy secret before importing the app, then mocks
all AWS services with moto so the storage layer runs against in-memory tables.
"""

from __future__ import annotations

import os

os.environ.setdefault("TABLE_PREFIX", "test")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("LOCAL_DEV", "true")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_DEFAULT_REGION", "ca-central-1")

import boto3  # noqa: E402
import pytest  # noqa: E402
from moto import mock_aws  # noqa: E402

from gamatrix.config import Settings  # noqa: E402
from gamatrix.storage.dynamo import Repository  # noqa: E402


def _create_tables(settings: Settings) -> None:
    ddb = boto3.client("dynamodb", region_name=settings.aws_region)
    common = {"BillingMode": "PAY_PER_REQUEST"}
    ddb.create_table(
        TableName=settings.games_table,
        KeySchema=[{"AttributeName": "release_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "release_key", "AttributeType": "S"}],
        **common,
    )
    ddb.create_table(
        TableName=settings.users_table,
        KeySchema=[{"AttributeName": "email", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "email", "AttributeType": "S"}],
        **common,
    )
    ddb.create_table(
        TableName=settings.libraries_table,
        KeySchema=[
            {"AttributeName": "user_id", "KeyType": "HASH"},
            {"AttributeName": "release_key", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "user_id", "AttributeType": "S"},
            {"AttributeName": "release_key", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "release_key-index",
                "KeySchema": [{"AttributeName": "release_key", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        **common,
    )
    for name, pk in [
        (settings.jobs_table, "job_id"),
        (settings.metadata_table, "slug"),
        (settings.profile_pics_table, "user_id"),
        (settings.config_table, "key"),
        (settings.auth_challenges_table, "challenge_id"),
    ]:
        ddb.create_table(
            TableName=name,
            KeySchema=[{"AttributeName": pk, "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": pk, "AttributeType": "S"}],
            **common,
        )
    ddb.create_table(
        TableName=settings.passkeys_table,
        KeySchema=[{"AttributeName": "credential_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "credential_id", "AttributeType": "S"},
            {"AttributeName": "user_handle", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "user_handle-index",
                "KeySchema": [{"AttributeName": "user_handle", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        **common,
    )
    ddb.create_table(
        TableName=settings.api_tokens_table,
        KeySchema=[{"AttributeName": "token_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "token_id", "AttributeType": "S"},
            {"AttributeName": "email", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "email-index",
                "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        **common,
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        table_prefix="test",
        jwt_secret="test-secret",
        local_dev=True,
        dynamodb_endpoint_url=None,
        s3_endpoint_url=None,
        sqs_endpoint_url=None,
        public_s3_endpoint_url=None,
    )


@pytest.fixture
def repo(settings):
    with mock_aws():
        _create_tables(settings)
        yield Repository(settings=settings)
