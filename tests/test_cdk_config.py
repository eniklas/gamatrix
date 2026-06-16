"""Regression tests for CDK deploy-config alias validation.

These tests load the infrastructure deployment config from a temporary
``cdk-config.yaml`` file and assert that invalid alias-domain settings are
rejected before stack synthesis. The expected result is a fast failure for bad
input, rather than silently skipping alias wiring or treating a scalar string as
an iterable of one-character domains.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CDK_CONFIG_PATH = PROJECT_ROOT / "infrastructure" / "cdk" / "config.py"


def _load_cdk_config_module() -> ModuleType:
    """Load the CDK config module directly from the infrastructure tree."""
    spec = importlib.util.spec_from_file_location(
        "gamatrix_cdk_config_test", CDK_CONFIG_PATH
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_load_deploy_config_rejects_alias_domains_scalar(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "cdk-config.yaml").write_text(
        "\n".join(
            [
                "hosted_zone: example.com",
                "site_domain: games.example.com",
                "alias_hosted_zone: other.com",
                "alias_domains: games.other.com",
            ]
        )
    )
    monkeypatch.setenv("GAMATRIX_CONFIG_DIR", str(config_dir))

    config = _load_cdk_config_module()

    with pytest.raises(ValueError, match="alias_domains"):
        config.load_deploy_config()


def test_load_deploy_config_requires_alias_hosted_zone(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "cdk-config.yaml").write_text(
        "\n".join(
            [
                "hosted_zone: example.com",
                "site_domain: games.example.com",
                "alias_domains:",
                "  - games.other.com",
            ]
        )
    )
    monkeypatch.setenv("GAMATRIX_CONFIG_DIR", str(config_dir))

    config = _load_cdk_config_module()

    with pytest.raises(ValueError, match="alias_hosted_zone"):
        config.load_deploy_config()


def test_load_deploy_config_reads_ux_template(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "cdk-config.yaml").write_text("ux_template: modern")
    monkeypatch.setenv("GAMATRIX_CONFIG_DIR", str(config_dir))

    config = _load_cdk_config_module()

    assert config.load_deploy_config().ux_template == "modern"


def test_load_deploy_config_rejects_unknown_ux_template(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "cdk-config.yaml").write_text("ux_template: ../../outside")
    monkeypatch.setenv("GAMATRIX_CONFIG_DIR", str(config_dir))

    config = _load_cdk_config_module()

    with pytest.raises(ValueError, match="ux_template"):
        config.load_deploy_config()
