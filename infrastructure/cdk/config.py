"""Deployment config loader.

Reads optional deployment settings (custom domain, SES sender) from a private
config file kept outside this public repo. Defaults to
``../gamatrix-configs/cdk-config.yaml`` relative to the repo root; override the
directory with the ``GAMATRIX_CONFIG_DIR`` environment variable.

If the file (or the hosted_zone/site_domain keys) is absent, the stack deploys
without a custom domain or SES identity -- it just exposes the default API
Gateway URL -- so anyone can clone and deploy this repo without owning a domain.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT.parent / "gamatrix-configs"


@dataclass
class DeployConfig:
    hosted_zone: str | None = None
    site_domain: str | None = None
    email_from: str | None = None

    @property
    def has_custom_domain(self) -> bool:
        return bool(self.hosted_zone and self.site_domain)


def load_deploy_config() -> DeployConfig:
    config_dir = Path(os.environ.get("GAMATRIX_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    path = config_dir / "cdk-config.yaml"
    if not path.exists():
        return DeployConfig()

    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    return DeployConfig(
        hosted_zone=data.get("hosted_zone"),
        site_domain=data.get("site_domain"),
        email_from=data.get("email_from"),
    )
