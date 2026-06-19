"""Tests for personal API tokens and the bearer-authenticated upload path
that restores unattended DB uploads (issue #129)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from gamatrix import upload
from gamatrix.app import app
from gamatrix.auth import service, tokens
from gamatrix.auth.dependencies import get_repo
from gamatrix.config import get_settings


# ---------------------------------------------------------------------------
# Token service / repository
# ---------------------------------------------------------------------------
def _seed_user(repo, email="user@example.com", password="password"):
    repo.put_user(
        {
            "email": email,
            "username": "User",
            "password_hash": service.hash_password(password),
        }
    )


def test_token_roundtrip_resolves_to_user(repo):
    _seed_user(repo)
    token = tokens.create_api_token(repo, "user@example.com", "laptop")
    assert token.startswith("gmx_")
    user = tokens.resolve_token(repo, token)
    assert user is not None and user["email"] == "user@example.com"


def test_resolve_token_records_last_used(repo):
    _seed_user(repo)
    token = tokens.create_api_token(repo, "user@example.com", "laptop")
    listed = repo.list_api_tokens("user@example.com")
    assert listed[0]["last_used_at"] is None
    tokens.resolve_token(repo, token)
    assert repo.list_api_tokens("user@example.com")[0]["last_used_at"] is not None


def test_resolve_token_rejects_garbage_and_tampering(repo):
    _seed_user(repo)
    token = tokens.create_api_token(repo, "user@example.com", "laptop")
    assert tokens.resolve_token(repo, "not-a-token") is None
    assert tokens.resolve_token(repo, "gmx_deadbeef_secret") is None  # unknown id
    # Right id, wrong secret.
    token_id = token.split("_", 2)[1]
    assert tokens.resolve_token(repo, f"gmx_{token_id}_wrong") is None


def test_delete_api_token_is_owner_scoped(repo):
    _seed_user(repo, "a@example.com")
    _seed_user(repo, "b@example.com")
    token = tokens.create_api_token(repo, "a@example.com", "laptop")
    token_id = token.split("_", 2)[1]
    # Someone else can't delete it; the owner can.
    assert repo.delete_api_token(token_id, "b@example.com") is False
    assert repo.delete_api_token(token_id, "a@example.com") is True
    assert tokens.resolve_token(repo, token) is None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def _client(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _login(client):
    response = client.post(
        "/auth/login",
        data={"email": "user@example.com", "password": "password"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_create_token_requires_correct_password(repo):
    _seed_user(repo)
    for client in _client(repo):
        _login(client)
        denied = client.post(
            "/auth/tokens", json={"name": "laptop", "password": "wrong"}
        )
        assert denied.status_code == 403
        assert repo.list_api_tokens("user@example.com") == []


def test_create_token_returns_secret_and_setup_snippet(repo):
    _seed_user(repo)
    for client in _client(repo):
        _login(client)
        management = client.get("/auth/tokens")
        assert management.status_code == 200
        expected = f'/static/templates/{get_settings().ux_template}/style.css?v='
        assert expected in management.text
        created = client.post(
            "/auth/tokens", json={"name": "laptop", "password": "password"}
        )
        assert created.status_code == 200
        body = created.json()
        assert body["token"].startswith("gmx_")
        # The snippet is ready to paste: it carries the real token and the
        # presign-bearing site, and stores the token with locked-down perms.
        assert body["token"] in body["snippet"]
        assert "/upload/presign" not in body["snippet"]  # the script handles that
        assert "Set-Acl" in body["snippet"]
        assert "/auth/upload-gamatrix.ps1" in body["snippet"]
        # A v2-specific task name, so it can't collide with a leftover v1 task.
        assert "gamatrix v2 DB upload" in body["snippet"]
        # The new token shows up on the management page.
        listing = client.get("/auth/tokens/list")
        assert "laptop" in listing.text


def test_uploader_scripts_point_at_the_configured_site(repo):
    # The scripts ship with a placeholder host; serving them swaps in this
    # deployment's real base URL so the download works without hand-editing.
    from gamatrix.config import get_settings

    base_url = get_settings().app_base_url.rstrip("/")
    for client in _client(repo):
        for name in ("upload-gamatrix.ps1", "upload-gamatrix.sh"):
            resp = client.get(f"/auth/{name}")
            assert resp.status_code == 200
            assert "gamatrix.example.com" not in resp.text
            assert base_url in resp.text


def test_revoke_token_removes_it(repo):
    _seed_user(repo)
    token = tokens.create_api_token(repo, "user@example.com", "laptop")
    token_id = token.split("_", 2)[1]
    for client in _client(repo):
        _login(client)
        ok = client.request(
            "DELETE",
            f"/auth/tokens/{token_id}",
            json={"password": "password"},
        )
        assert ok.status_code == 200
        assert repo.list_api_tokens("user@example.com") == []
        # Revoking an unknown token is a 404.
        missing = client.request(
            "DELETE", "/auth/tokens/nope", json={"password": "password"}
        )
        assert missing.status_code == 404


# ---------------------------------------------------------------------------
# The point of #129: presign works with a bearer token, no cookie.
# ---------------------------------------------------------------------------
class _S3Stub:
    def presigned_upload(self, key, max_bytes):
        return {"url": "https://s3.example/bucket", "fields": {"key": key}}


def test_presign_authenticates_with_bearer_token(repo, monkeypatch):
    monkeypatch.setattr(upload, "get_s3", lambda: _S3Stub())
    _seed_user(repo)
    token = tokens.create_api_token(repo, "user@example.com", "scheduled")
    for client in _client(repo):
        # No login/cookie — just the token, the way a scheduled task would.
        resp = client.get(
            "/upload/presign", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.json()["key"] == "uploads/user@example.com.db"


def test_presign_rejects_bad_bearer_token(repo, monkeypatch):
    monkeypatch.setattr(upload, "get_s3", lambda: _S3Stub())
    _seed_user(repo)
    for client in _client(repo):
        resp = client.get(
            "/upload/presign", headers={"Authorization": "Bearer gmx_bad_token"}
        )
        assert resp.status_code == 401


def test_presign_still_works_with_session_cookie(repo, monkeypatch):
    monkeypatch.setattr(upload, "get_s3", lambda: _S3Stub())
    _seed_user(repo)
    for client in _client(repo):
        _login(client)
        resp = client.get("/upload/presign")
        assert resp.status_code == 200
