"""Personal API tokens for unattended (scripted) DB uploads.

A token authenticates the upload endpoints without an interactive browser login,
so a scheduled task can refresh a user's library (issue #129). The token is
shown to the user exactly once at creation; only a SHA-256 hash of its secret is
stored, so a database leak can't be replayed.

Token format: ``gmx_<token_id>_<secret>``
  - ``token_id``  uuid4 hex — the public handle (DynamoDB key + revocation id).
  - ``secret``    url-safe random — never stored in the clear.

Lookup is O(1): split off the ``token_id``, read that row, and compare the hash
of the presented secret in constant time. Tokens only gate the upload routes
(the only place the bearer dependency is attached), so they can't stand in for a
session cookie on the rest of the app.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid

from gamatrix.helpers import now_iso
from gamatrix.storage.dynamo import Repository

TOKEN_PREFIX = "gmx"
_PARTS = 3  # prefix, token_id, secret


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def create_api_token(repo: Repository, email: str, name: str) -> str:
    """Mint a token for `email`, persist its hash, and return the one-time
    plaintext for the user to copy."""
    token_id = uuid.uuid4().hex
    secret = secrets.token_urlsafe(32)
    repo.put_api_token(
        {
            "token_id": token_id,
            "email": email.lower(),
            "name": name,
            "secret_hash": _hash_secret(secret),
            "created_at": now_iso(),
            "last_used_at": None,
        }
    )
    return f"{TOKEN_PREFIX}_{token_id}_{secret}"


def resolve_token(repo: Repository, token: str) -> dict | None:
    """Return the user a valid token authenticates, or None. Records last use."""
    # maxsplit=2 keeps the secret intact even though url-safe base64 can itself
    # contain underscores; the token_id is uuid hex and never does.
    parts = token.split("_", 2)
    if len(parts) != _PARTS or parts[0] != TOKEN_PREFIX:
        return None
    _, token_id, secret = parts
    record = repo.get_api_token(token_id)
    if record is None:
        return None
    if not hmac.compare_digest(record.get("secret_hash", ""), _hash_secret(secret)):
        return None
    user = repo.get_user(record["email"])
    if user is None:
        # Account removed but token lingered; treat as invalid.
        return None
    repo.touch_api_token(token_id, now_iso())
    return user
