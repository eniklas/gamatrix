"""WebAuthn passkey ceremonies and persistence."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from gamatrix.config import Settings, get_settings
from gamatrix.helpers import now_iso
from gamatrix.storage.dynamo import Repository


class PasskeyError(Exception):
    """A passkey ceremony could not be completed."""


def _new_challenge(
    repo: Repository,
    settings: Settings,
    ceremony: str,
    user_handle: str | None = None,
    friendly_name: str | None = None,
) -> tuple[str, bytes]:
    challenge_id = secrets.token_urlsafe(32)
    challenge = secrets.token_bytes(32)
    now = int(datetime.now(timezone.utc).timestamp())
    item: dict[str, Any] = {
        "challenge_id": challenge_id,
        "challenge": bytes_to_base64url(challenge),
        "ceremony": ceremony,
        "expires_at": now + settings.webauthn_challenge_ttl_seconds,
    }
    if user_handle:
        item["user_handle"] = user_handle
    if friendly_name:
        item["friendly_name"] = friendly_name
    repo.put_auth_challenge(item)
    return challenge_id, challenge


def _load_challenge(repo: Repository, challenge_id: str, ceremony: str) -> dict:
    item = repo.get_auth_challenge(challenge_id)
    now = int(datetime.now(timezone.utc).timestamp())
    if not item or item.get("ceremony") != ceremony or item["expires_at"] < now:
        raise PasskeyError("The passkey request has expired or is invalid.")
    return item


def _user_handle(repo: Repository, user: dict) -> str:
    existing = user.get("webauthn_user_id")
    if existing:
        return str(existing)
    return repo.ensure_webauthn_user_id(
        user["email"], bytes_to_base64url(secrets.token_bytes(32))
    )


def registration_options(
    repo: Repository,
    user: dict,
    friendly_name: str,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    name = friendly_name.strip()
    if not name or len(name) > 100:
        raise PasskeyError("Passkey name must be between 1 and 100 characters.")
    user_handle = _user_handle(repo, user)
    challenge_id, challenge = _new_challenge(
        repo, settings, "registration", user_handle, name
    )
    existing = repo.list_passkeys(user_handle)
    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=base64url_to_bytes(user_handle),
        user_name=user["email"],
        user_display_name=user.get("username", user["email"]),
        challenge=challenge,
        timeout=settings.webauthn_challenge_ttl_seconds * 1000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(item["credential_id"]))
            for item in existing
        ],
    )
    return {
        "challenge_id": challenge_id,
        "publicKey": json.loads(options_to_json(options)),
    }


def verify_registration(
    repo: Repository,
    user: dict,
    challenge_id: str,
    credential: dict,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    challenge = _load_challenge(repo, challenge_id, "registration")
    user_handle = _user_handle(repo, user)
    if challenge.get("user_handle") != user_handle:
        raise PasskeyError("The passkey request does not belong to this account.")
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge["challenge"]),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origins,
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError("Passkey registration verification failed.") from exc
    if not repo.consume_auth_challenge(challenge_id):
        raise PasskeyError("The passkey request has already been used.")

    credential_id = bytes_to_base64url(verified.credential_id)
    response = credential.get("response", {})
    passkey = {
        "credential_id": credential_id,
        "public_key": bytes_to_base64url(verified.credential_public_key),
        "user_handle": user_handle,
        "email": user["email"].lower(),
        "sign_count": verified.sign_count,
        "transports": response.get("transports", []),
        "backup_eligible": verified.credential_device_type.value == "multi_device",
        "backed_up": verified.credential_backed_up,
        "friendly_name": challenge["friendly_name"],
        "created_at": now_iso(),
        "last_used_at": None,
    }
    if not repo.put_passkey(passkey):
        raise PasskeyError("That passkey is already registered.")
    return passkey


def authentication_options(repo: Repository, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    challenge_id, challenge = _new_challenge(repo, settings, "authentication")
    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        challenge=challenge,
        timeout=settings.webauthn_challenge_ttl_seconds * 1000,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return {
        "challenge_id": challenge_id,
        "publicKey": json.loads(options_to_json(options)),
    }


def verify_authentication(
    repo: Repository,
    challenge_id: str,
    credential: dict,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()
    challenge = _load_challenge(repo, challenge_id, "authentication")
    passkey = repo.get_passkey(credential.get("id", ""))
    if not passkey:
        raise PasskeyError("Unknown passkey.")
    supplied_handle = credential.get("response", {}).get("userHandle")
    if supplied_handle and supplied_handle != passkey["user_handle"]:
        raise PasskeyError("Passkey user handle does not match.")
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge["challenge"]),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origins,
            credential_public_key=base64url_to_bytes(passkey["public_key"]),
            credential_current_sign_count=passkey["sign_count"],
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError("Passkey authentication verification failed.") from exc
    if not repo.consume_auth_challenge(challenge_id):
        raise PasskeyError("The passkey request has already been used.")

    if not repo.update_passkey_after_authentication(
        passkey["credential_id"],
        passkey["sign_count"],
        verified.new_sign_count,
        verified.credential_backed_up,
        now_iso(),
    ):
        raise PasskeyError("The passkey signature counter was already used.")
    user = repo.get_user(passkey["email"])
    if not user or user.get("webauthn_user_id") != passkey["user_handle"]:
        raise PasskeyError("The passkey account no longer exists.")
    return user
