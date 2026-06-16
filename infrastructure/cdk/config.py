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
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT.parent / "gamatrix-configs"
UX_TEMPLATES = ("default", "modern")


@dataclass
class DeployConfig:
    hosted_zone: str | None = None
    site_domain: str | None = None
    email_from: str | None = None
    # Extra hostnames to alias to the same API Gateway endpoint. Each must be a
    # subdomain of alias_hosted_zone (a Route53-managed zone). The primary cert
    # is extended with SANs for all alias_domains so there are no cert warnings.
    alias_hosted_zone: str | None = None
    alias_domains: list[str] = field(default_factory=list)
    ux_template: str = "default"

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

    alias_domains = data.get("alias_domains") or []
    # YAML happily parses `alias_domains: games.other.com` as a scalar string.
    # Without this guard it would later be iterated character-by-character,
    # silently wiring up bogus single-letter "domains". Require a real list.
    if not isinstance(alias_domains, list):
        kind = type(alias_domains).__name__
        raise ValueError(f"alias_domains must be a list of hostnames, got {kind}")
    alias_hosted_zone = data.get("alias_hosted_zone")
    if alias_domains and not alias_hosted_zone:
        raise ValueError("alias_hosted_zone is required when alias_domains is set")
    ux_template = data.get("ux_template", "default")
    if ux_template not in UX_TEMPLATES:
        raise ValueError(f"ux_template must be one of: {', '.join(UX_TEMPLATES)}")

    return DeployConfig(
        hosted_zone=data.get("hosted_zone"),
        site_domain=data.get("site_domain"),
        email_from=data.get("email_from"),
        alias_hosted_zone=alias_hosted_zone,
        alias_domains=alias_domains,
        ux_template=ux_template,
    )
