"""Black-box route tests and moto-backed persistence tests for passkeys."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import CredentialDeviceType

from gamatrix.app import app
from gamatrix.auth import passkeys, service
from gamatrix.auth.dependencies import get_repo


def _client(repo):
    app.dependency_overrides[get_repo] = lambda: repo
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _user(repo):
    user = {
        "email": "user@example.com",
        "username": "User",
        "password_hash": service.hash_password("password"),
    }
    repo.put_user(user)
    return user


def _login(client):
    response = client.post(
        "/auth/login",
        data={"email": "user@example.com", "password": "password"},
        follow_redirects=False,
    )
    assert response.status_code == 302


def test_login_page_offers_explicit_and_conditional_passkeys(repo):
    for client in _client(repo):
        response = client.get("/auth/login")
        assert response.status_code == 200
        assert "Sign in with a passkey" in response.text
        assert 'autocomplete="username webauthn"' in response.text
        assert (
            "isConditionalMediationAvailable" in client.get("/static/passkeys.js").text
        )


def test_passkey_management_page_prefills_name_and_explains_password(repo):
    _user(repo)
    for client in _client(repo):
        _login(client)
        response = client.get("/auth/passkeys")
        assert response.status_code == 200
        assert 'value="Gamatrix passkey for User"' in response.text
        assert (
            "Required to verify your identity before adding a passkey" in response.text
        )
        assert "Waiting for passkey creation to complete..." in response.text


def test_preferences_page_moves_manage_passkeys_into_page_body(repo):
    _user(repo)
    for client in _client(repo):
        _login(client)
        response = client.get("/preferences")
        assert response.status_code == 200
        assert (
            '<div class="right"><a href="/games">Back to games</a></div>'
            in response.text
        )
        assert "Manage passkeys</a>" not in response.text
        assert "Manage passkeys</button>" in response.text
        assert "Add, review, or remove passkeys for this account." in response.text


def test_registration_options_require_password_and_discoverable_uv(repo):
    _user(repo)
    for client in _client(repo):
        _login(client)
        denied = client.post(
            "/auth/passkeys/register/options",
            json={"password": "wrong", "friendly_name": "Laptop"},
        )
        assert denied.status_code == 403

        response = client.post(
            "/auth/passkeys/register/options",
            json={"password": "password", "friendly_name": "Laptop"},
        )
        assert response.status_code == 200
        options = response.json()["publicKey"]
        assert options["authenticatorSelection"]["residentKey"] == "required"
        assert options["authenticatorSelection"]["userVerification"] == "required"
        assert options["attestation"] == "none"
        assert repo.get_user("user@example.com")["webauthn_user_id"]


def test_registration_route_stores_verified_passkey_and_rejects_replay(
    repo, monkeypatch
):
    _user(repo)
    verified = SimpleNamespace(
        credential_id=b"credential-id",
        credential_public_key=b"public-key",
        sign_count=0,
        credential_device_type=CredentialDeviceType.MULTI_DEVICE,
        credential_backed_up=True,
    )
    monkeypatch.setattr(
        passkeys, "verify_registration_response", lambda **kwargs: verified
    )

    for client in _client(repo):
        _login(client)
        options = client.post(
            "/auth/passkeys/register/options",
            json={"password": "password", "friendly_name": "Phone"},
        ).json()
        payload = {
            "challenge_id": options["challenge_id"],
            "credential": {
                "id": bytes_to_base64url(b"credential-id"),
                "response": {"transports": ["hybrid"]},
            },
        }
        response = client.post("/auth/passkeys/register/verify", json=payload)
        assert response.status_code == 200
        assert response.json()["friendly_name"] == "Phone"
        assert response.json()["backup_eligible"] is True
        assert (
            client.post("/auth/passkeys/register/verify", json=payload).status_code
            == 400
        )


def test_passkey_login_issues_session_and_zero_counter_remains_usable(
    repo, monkeypatch
):
    user = _user(repo)
    handle = repo.ensure_webauthn_user_id(user["email"], "opaque-handle")
    credential_id = bytes_to_base64url(b"credential-id")
    repo.put_passkey(
        {
            "credential_id": credential_id,
            "public_key": bytes_to_base64url(b"public-key"),
            "user_handle": handle,
            "email": user["email"],
            "sign_count": 0,
            "friendly_name": "Phone",
        }
    )
    monkeypatch.setattr(
        passkeys,
        "verify_authentication_response",
        lambda **kwargs: SimpleNamespace(
            new_sign_count=0,
            credential_backed_up=True,
        ),
    )

    for client in _client(repo):
        options = client.post("/auth/passkeys/authenticate/options").json()
        response = client.post(
            "/auth/passkeys/authenticate/verify",
            json={
                "challenge_id": options["challenge_id"],
                "credential": {
                    "id": credential_id,
                    "response": {"userHandle": handle},
                },
            },
        )
        assert response.status_code == 200
        assert response.json()["redirect"] == "/games"
        assert service.COOKIE_NAME in response.cookies
        assert repo.get_passkey(credential_id)["sign_count"] == 0


def test_expired_challenge_and_unknown_credential_are_rejected(repo):
    expired = {
        "challenge_id": "expired",
        "challenge": bytes_to_base64url(b"challenge"),
        "ceremony": "authentication",
        "expires_at": int(datetime.now(timezone.utc).timestamp()) - 1,
    }
    repo.put_auth_challenge(expired)
    for client in _client(repo):
        response = client.post(
            "/auth/passkeys/authenticate/verify",
            json={"challenge_id": "expired", "credential": {"id": "unknown"}},
        )
        assert response.status_code == 400

        options = client.post("/auth/passkeys/authenticate/options").json()
        response = client.post(
            "/auth/passkeys/authenticate/verify",
            json={
                "challenge_id": options["challenge_id"],
                "credential": {"id": "unknown"},
            },
        )
        assert response.status_code == 400


def test_listing_hides_key_material_and_deletion_requires_password(repo):
    user = _user(repo)
    handle = repo.ensure_webauthn_user_id(user["email"], "opaque-handle")
    repo.put_passkey(
        {
            "credential_id": "credential",
            "public_key": "secret-public-key",
            "user_handle": handle,
            "email": user["email"],
            "sign_count": 0,
            "friendly_name": "Laptop",
        }
    )
    for client in _client(repo):
        _login(client)
        listed = client.get("/auth/passkeys/list")
        assert listed.status_code == 200
        assert "Laptop" in listed.text
        assert "Back to preferences" in listed.text
        assert "Add a passkey" not in listed.text
        assert "Current password" not in listed.text
        assert "public_key" not in listed.text
        assert "secret-public-key" not in listed.text

        assert (
            client.request(
                "DELETE", "/auth/passkeys/credential", json={"password": "wrong"}
            ).status_code
            == 403
        )
        assert (
            client.request(
                "DELETE", "/auth/passkeys/credential", json={"password": "password"}
            ).status_code
            == 200
        )
        assert repo.get_passkey("credential") is None


def test_challenge_consumption_is_atomic(repo):
    repo.put_auth_challenge(
        {
            "challenge_id": "once",
            "challenge": "value",
            "ceremony": "authentication",
            "expires_at": 9999999999,
        }
    )
    assert repo.consume_auth_challenge("once") is True
    assert repo.consume_auth_challenge("once") is False


def test_nonzero_counter_update_rejects_stale_concurrent_write(repo):
    repo.put_passkey(
        {
            "credential_id": "counter",
            "user_handle": "handle",
            "sign_count": 4,
        }
    )
    assert repo.update_passkey_after_authentication("counter", 4, 5, False, "first")
    assert not repo.update_passkey_after_authentication(
        "counter", 4, 5, False, "second"
    )
