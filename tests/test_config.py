"""Tests for Settings validation and JWT secret resolution."""

from __future__ import annotations

import pytest

from gamatrix import config
from gamatrix.config import DEFAULT_JWT_SECRET, Settings, resolve_jwt_secret


def test_production_rejects_default_jwt_secret():
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(
            local_dev=False,
            jwt_secret=DEFAULT_JWT_SECRET,
            jwt_secret_name=None,
            webauthn_rp_id="games.example.com",
            webauthn_origins=["https://games.example.com"],
        )


def test_local_dev_allows_default_jwt_secret():
    s = Settings(local_dev=True, jwt_secret=DEFAULT_JWT_SECRET, jwt_secret_name=None)
    assert s.jwt_secret == DEFAULT_JWT_SECRET


def test_secret_name_satisfies_production_guard():
    s = Settings(
        local_dev=False,
        jwt_secret=DEFAULT_JWT_SECRET,
        jwt_secret_name="gamatrix/jwt-secret",
        webauthn_rp_id="games.example.com",
        webauthn_origins=["https://games.example.com"],
    )
    assert s.jwt_secret_name == "gamatrix/jwt-secret"


def test_resolve_jwt_secret_prefers_plain_value():
    s = Settings(local_dev=True, jwt_secret="plain-secret")
    assert resolve_jwt_secret(s) == "plain-secret"


def test_resolve_jwt_secret_reads_secrets_manager(monkeypatch):
    s = Settings(
        local_dev=False,
        jwt_secret_name="gamatrix/jwt-secret",
        webauthn_rp_id="games.example.com",
        webauthn_origins=["https://games.example.com"],
    )
    monkeypatch.setattr(config, "_fetch_secret_string", lambda name, region: "from-sm")
    assert resolve_jwt_secret(s) == "from-sm"


def test_production_requires_canonical_https_passkey_domain():
    with pytest.raises(ValueError, match="Production passkeys"):
        Settings(local_dev=False, jwt_secret="secret")
