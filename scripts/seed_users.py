#!/usr/bin/env python3
"""Provision user accounts.

Accounts are pre-provisioned (no self-registration). For local development
seed_default_users() creates the known group with a shared default password.
For production, call create_user() from a one-off script with real emails and
generated passwords, then have each user reset via "forgot password".
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from gamatrix.auth.service import hash_password
from gamatrix.helpers import now_iso
from gamatrix.storage.dynamo import Repository, get_repository

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_users")

# The default local-dev group (real emails/GOG ids) lives outside this public
# repo. It is read from ../gamatrix-configs/seed_users.json by default; override
# the directory with GAMATRIX_CONFIG_DIR. If the file is absent, --defaults
# seeds nothing and you create users one at a time via the CLI flags.
DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[1].parent / "gamatrix-configs"
DEFAULT_PASSWORD = "changeme"


def _load_default_users() -> list[dict]:
    config_dir = Path(os.environ.get("GAMATRIX_CONFIG_DIR", DEFAULT_CONFIG_DIR))
    path = config_dir / "seed_users.json"
    if not path.exists():
        log.warning("No seed_users.json at %s; nothing to seed", path)
        return []
    return json.loads(path.read_text())


def create_user(
    repo: Repository,
    email: str,
    username: str,
    password: str,
    user_id: str | None = None,
    pic: str | None = None,
    is_admin: bool = False,
) -> None:
    if repo.get_user(email):
        log.info("User %s already exists; skipping", email)
        return
    repo.put_user(
        {
            "email": email,
            "username": username,
            "password_hash": hash_password(password),
            "user_id": user_id,
            "pic": pic,
            "is_admin": is_admin,
            "preferences": {},
            "created_at": now_iso(),
        }
    )
    log.info("Created user %s (admin=%s)", email, is_admin)


def seed_default_users(repo: Repository | None = None) -> None:
    repo = repo or get_repository()
    for entry in _load_default_users():
        create_user(
            repo,
            email=entry["email"],
            username=entry["username"],
            password=entry.get("password", DEFAULT_PASSWORD),
            user_id=entry.get("user_id"),
            pic=entry.get("pic"),
            is_admin=entry.get("admin", False),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a gamatrix user")
    parser.add_argument("--email")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--user-id")
    parser.add_argument("--pic")
    parser.add_argument("--admin", action="store_true")
    parser.add_argument(
        "--defaults", action="store_true", help="Seed the default local user group"
    )
    args = parser.parse_args()

    repo = get_repository()
    if args.defaults:
        seed_default_users(repo)
        return
    if not (args.email and args.username and args.password):
        parser.error(
            "--email, --username and --password are required (or use --defaults)"
        )
    create_user(
        repo,
        email=args.email,
        username=args.username,
        password=args.password,
        user_id=args.user_id,
        pic=args.pic,
        is_admin=args.admin,
    )


if __name__ == "__main__":
    main()
