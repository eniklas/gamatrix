#!/usr/bin/env python3
"""Create DynamoDB tables + the S3 upload bucket locally, then seed test users.

Run once after starting the local stack:

    just init-local      # or: python scripts/init_local.py

Idempotent: existing tables/buckets are left alone.
"""

from __future__ import annotations

import logging

import boto3
from botocore.exceptions import ClientError

from gamatrix.config import get_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("init_local")


def _table_defs(s):
    return [
        {
            "TableName": s.games_table,
            "KeySchema": [{"AttributeName": "release_key", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "release_key", "AttributeType": "S"}
            ],
        },
        {
            "TableName": s.users_table,
            "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "email", "AttributeType": "S"}],
        },
        {
            "TableName": s.libraries_table,
            "KeySchema": [
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "release_key", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "release_key", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "release_key-index",
                    "KeySchema": [{"AttributeName": "release_key", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        },
        {
            "TableName": s.jobs_table,
            "KeySchema": [{"AttributeName": "job_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "job_id", "AttributeType": "S"}],
        },
        {
            "TableName": s.metadata_table,
            "KeySchema": [{"AttributeName": "slug", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "slug", "AttributeType": "S"}],
        },
        {
            "TableName": s.profile_pics_table,
            "KeySchema": [{"AttributeName": "user_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "user_id", "AttributeType": "S"}
            ],
        },
        {
            "TableName": s.config_table,
            "KeySchema": [{"AttributeName": "key", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "key", "AttributeType": "S"}],
        },
        {
            "TableName": s.passkeys_table,
            "KeySchema": [{"AttributeName": "credential_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "credential_id", "AttributeType": "S"},
                {"AttributeName": "user_handle", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "user_handle-index",
                    "KeySchema": [{"AttributeName": "user_handle", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        },
        {
            "TableName": s.auth_challenges_table,
            "KeySchema": [{"AttributeName": "challenge_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "challenge_id", "AttributeType": "S"}
            ],
        },
        {
            "TableName": s.api_tokens_table,
            "KeySchema": [{"AttributeName": "token_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [
                {"AttributeName": "token_id", "AttributeType": "S"},
                {"AttributeName": "email", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "email-index",
                    "KeySchema": [{"AttributeName": "email", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        },
    ]


def create_tables(settings) -> None:
    ddb = boto3.client(
        "dynamodb",
        region_name=settings.aws_region,
        endpoint_url=settings.dynamodb_endpoint_url,
    )
    existing = set(ddb.list_tables()["TableNames"])
    for defn in _table_defs(settings):
        name = defn["TableName"]
        if name in existing:
            log.info("Table %s already exists", name)
            continue
        ddb.create_table(BillingMode="PAY_PER_REQUEST", **defn)
        if name == settings.auth_challenges_table:
            ddb.update_time_to_live(
                TableName=name,
                TimeToLiveSpecification={
                    "Enabled": True,
                    "AttributeName": "expires_at",
                },
            )
        log.info("Created table %s", name)


def create_bucket(settings) -> None:
    s3 = boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.s3_endpoint_url,
    )
    try:
        s3.head_bucket(Bucket=settings.upload_bucket)
        log.info("Bucket %s already exists", settings.upload_bucket)
    except ClientError:
        s3.create_bucket(Bucket=settings.upload_bucket)
        log.info("Created bucket %s", settings.upload_bucket)


def main() -> None:
    settings = get_settings()
    create_tables(settings)
    create_bucket(settings)

    # Seed the title filter lists into the config table (SSM in AWS).
    from gamatrix.storage.dynamo import get_repository

    repo = get_repository()
    if repo.get_config("hidden") is None:
        repo.put_config("hidden", [])
    if repo.get_config("single_player") is None:
        repo.put_config("single_player", [])

    from seed_users import seed_default_users

    seed_default_users(repo)
    log.info("Local environment ready.")


if __name__ == "__main__":
    main()
