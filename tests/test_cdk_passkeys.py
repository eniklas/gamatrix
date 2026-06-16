"""CDK assertions for passkey storage and canonical RP configuration."""

from __future__ import annotations

import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
from aws_cdk import assertions
from aws_cdk import aws_route53 as route53

CDK_DIR = Path(__file__).resolve().parents[1] / "infrastructure" / "cdk"
sys.path.insert(0, str(CDK_DIR))

from config import DeployConfig  # noqa: E402
from gamatrix_stack import GamatrixStack  # noqa: E402


def _stack(monkeypatch):
    app = cdk.App()

    def imported_zone(scope, construct_id, **kwargs):
        return route53.HostedZone.from_hosted_zone_attributes(
            scope,
            construct_id,
            hosted_zone_id="Z123456789",
            zone_name=kwargs["domain_name"],
        )

    monkeypatch.setattr(route53.HostedZone, "from_lookup", imported_zone)
    return GamatrixStack(
        app,
        "TestStack",
        deploy_config=DeployConfig(
            hosted_zone="example.com",
            site_domain="games.example.com",
        ),
        env=cdk.Environment(account="123456789012", region="ca-central-1"),
    )


def test_stack_requires_stable_custom_domain():
    with pytest.raises(ValueError, match="stable custom"):
        GamatrixStack(cdk.App(), "NoDomain", deploy_config=DeployConfig())


def test_stack_creates_passkey_tables_ttl_gsi_and_rp_environment(monkeypatch):
    template = assertions.Template.from_stack(_stack(monkeypatch))
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "gamatrix_passkeys",
            "GlobalSecondaryIndexes": assertions.Match.array_with(
                [assertions.Match.object_like({"IndexName": "user_handle-index"})]
            ),
        },
    )
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "gamatrix_auth_challenges",
            "TimeToLiveSpecification": {
                "AttributeName": "expires_at",
                "Enabled": True,
            },
        },
    )
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "WEBAUTHN_RP_ID": "games.example.com",
                        "WEBAUTHN_ORIGINS": '["https://games.example.com"]',
                        "UX_TEMPLATE": "default",
                    }
                )
            }
        },
    )
