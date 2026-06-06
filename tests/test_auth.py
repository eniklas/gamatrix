"""Tests for auth primitives and the password-reset flow."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from gamatrix.auth import service


def test_password_hash_roundtrip():
    h = service.hash_password("hunter2")
    assert h != "hunter2"
    assert service.verify_password("hunter2", h)
    assert not service.verify_password("wrong", h)


def test_jwt_roundtrip(settings):
    token = service.create_session_token("user@x.com", settings)
    assert service.decode_session_token(token, settings) == "user@x.com"


def test_jwt_rejects_tampered_token(settings):
    assert service.decode_session_token("not.a.jwt", settings) is None


def test_authenticate(repo):
    repo.put_user(
        {
            "email": "user@x.com",
            "username": "User",
            "password_hash": service.hash_password("pw"),
        }
    )
    assert service.authenticate(repo, "user@x.com", "pw") is not None
    assert service.authenticate(repo, "user@x.com", "bad") is None
    assert service.authenticate(repo, "missing@x.com", "pw") is None


def test_password_reset_flow(repo, settings, monkeypatch):
    sent = {}

    def fake_send(s, to, subject, body):
        sent["to"] = to
        sent["body"] = body

    monkeypatch.setattr(service, "_send_email", fake_send)

    repo.put_user(
        {
            "email": "user@x.com",
            "username": "User",
            "password_hash": service.hash_password("old"),
        }
    )
    service.begin_password_reset(repo, "user@x.com", settings)

    user = repo.get_user("user@x.com")
    token = user["reset_token"]
    assert token and sent["to"] == "user@x.com"
    assert token in sent["body"]

    # Wrong token rejected.
    assert not service.complete_password_reset(repo, "user@x.com", "bad", "new")
    # Correct token accepted and password updated.
    assert service.complete_password_reset(repo, "user@x.com", token, "new")
    assert service.authenticate(repo, "user@x.com", "new") is not None
    # Token cleared after use.
    assert repo.get_user("user@x.com").get("reset_token") is None


def test_password_reset_unknown_email_is_silent(repo, settings, monkeypatch):
    monkeypatch.setattr(service, "_send_email", lambda *a, **k: None)
    # Should not raise for an unknown account.
    service.begin_password_reset(repo, "ghost@x.com", settings)


def test_password_reset_link_url_encodes_plus_email(repo, settings, monkeypatch):
    """Emails with a '+' must survive the reset link round-trip; an unencoded
    '+' in a query string decodes to a space and breaks the lookup."""
    sent = {}

    def fake_send(s, to, subject, body):
        sent["body"] = body

    monkeypatch.setattr(service, "_send_email", fake_send)
    repo.put_user(
        {
            "email": "user+games@x.com",
            "username": "User",
            "password_hash": service.hash_password("old"),
        }
    )
    service.begin_password_reset(repo, "user+games@x.com", settings)

    reset_link = next(
        line for line in sent["body"].splitlines() if "/auth/reset-password?" in line
    )
    query = parse_qs(urlparse(reset_link).query)
    assert query["email"] == ["user+games@x.com"]
