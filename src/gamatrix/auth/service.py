"""Auth primitives: password hashing, JWT sessions, reset tokens, and email."""

from __future__ import annotations

import logging
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import quote

import bcrypt
from jose import JWTError, jwt

from gamatrix.config import Settings, get_settings, resolve_jwt_secret
from gamatrix.helpers import now_iso, parse_iso
from gamatrix.storage.dynamo import Repository

log = logging.getLogger(__name__)

COOKIE_NAME = "gamatrix_session"

# bcrypt operates on at most 72 bytes; longer passwords are truncated to match.
_BCRYPT_MAX_BYTES = 72


def _encode(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_encode(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_encode(password), password_hash.encode("utf-8"))
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# JWT sessions
# ---------------------------------------------------------------------------
def create_session_token(email: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_ttl_hours)
    payload = {"sub": email.lower(), "exp": expire}
    secret = resolve_jwt_secret(settings)
    return jwt.encode(payload, secret, algorithm=settings.jwt_algorithm)


def decode_session_token(token: str, settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    secret = resolve_jwt_secret(settings)
    try:
        payload = jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def authenticate(repo: Repository, email: str, password: str) -> dict | None:
    user = repo.get_user(email)
    if user is None or "password_hash" not in user:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------
def begin_password_reset(
    repo: Repository, email: str, settings: Settings | None = None
) -> None:
    """Create a reset token and email a link. Silent if the user is unknown
    (so the endpoint can't be used to probe for valid accounts)."""
    settings = settings or get_settings()
    user = repo.get_user(email)
    if user is None:
        log.info("Password reset requested for unknown email %s", email)
        return

    token = str(uuid.uuid4())
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.reset_token_ttl_minutes
    )
    repo.update_user(
        email,
        {"reset_token": token, "reset_token_expires": expires.isoformat()},
    )
    link = (
        f"{settings.app_base_url}/auth/reset-password"
        f"?token={token}&email={quote(email)}"
    )
    _send_email(
        settings,
        to=email,
        subject="Reset your gamatrix password",
        body=(
            "Someone requested a password reset for your gamatrix account.\n\n"
            f"Reset it here (valid for {settings.reset_token_ttl_minutes} "
            f"minutes):\n{link}\n\n"
            "If you didn't request this, you can ignore this email."
        ),
    )


def complete_password_reset(
    repo: Repository, email: str, token: str, new_password: str
) -> bool:
    user = repo.get_user(email)
    if user is None or not user.get("reset_token"):
        return False
    if user["reset_token"] != token:
        return False
    if datetime.now(timezone.utc) > parse_iso(user["reset_token_expires"]):
        return False

    repo.update_user(
        email,
        {
            "password_hash": hash_password(new_password),
            "reset_token": None,
            "reset_token_expires": None,
            "password_updated_at": now_iso(),
        },
    )
    return True


def _send_email(settings: Settings, to: str, subject: str, body: str) -> None:
    if settings.smtp_host:
        # Local dev: send to mailhog over plain SMTP.
        msg = EmailMessage()
        msg["From"] = settings.email_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.send_message(msg)
        log.info("Sent email to %s via SMTP %s", to, settings.smtp_host)
    else:
        # AWS: send via SES.
        import boto3

        ses = boto3.client("ses", region_name=settings.aws_region)
        ses.send_email(
            Source=settings.email_from,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject},
                "Body": {"Text": {"Data": body}},
            },
        )
        log.info("Sent email to %s via SES", to)
