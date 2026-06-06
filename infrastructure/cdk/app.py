#!/usr/bin/env python3
"""CDK app entry point for gamatrix infrastructure."""

import os

import aws_cdk as cdk

from config import load_deploy_config
from gamatrix_stack import GamatrixStack

app = cdk.App()
GamatrixStack(
    app,
    "GamatrixStack",
    deploy_config=load_deploy_config(),
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "ca-central-1"),
    ),
)
app.synth()
