#!/usr/bin/env python3
"""Seed the local environment with the test users and their game libraries.

Mirrors the browser upload path (`/upload/complete`): for each locally generated
fixture, create the account, then ingest the fixture so its library + game stubs
land in DynamoDB and an enrichment job is queued for the local worker. The
fixtures encode an overlapping ownership matrix (see
``scripts/sample_data/generate_fixtures.py``) so the compare view has real
common/uncommon games to work with.

The fixtures + manifest are not committed; generate them first from your own GOG
Galaxy DB with ``just gen-fixtures db=<path>`` (see AGENTS.md / README).

Safety: by default this refuses to run if the users table already has rows,
because mixed old/new local accounts make the requested sample state ambiguous.
Pass ``--hard-reset-existing-users`` to purge existing local users first, then
seed the latest manifest from scratch. The ``just seed-local`` recipe uses that
reset mode automatically.

    just seed-local      # or: python scripts/seed_sample_data.py
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from gamatrix.gogdb.ingest import ingest_db_file
from gamatrix.storage.dynamo import get_repository
from gamatrix.storage.queue import get_queue
from seed_users import DEFAULT_PASSWORD, create_user

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("seed_sample_data")

SAMPLE_DIR = Path(__file__).resolve().parent / "sample_data"


def parse_args() -> argparse.Namespace:
    """Parse CLI flags for local sample-data seeding."""
    parser = argparse.ArgumentParser(
        description="Seed local sample users and libraries from generated fixtures."
    )
    parser.add_argument(
        "--hard-reset-existing-users",
        action="store_true",
        help="Delete all existing local users before seeding the manifest.",
    )
    return parser.parse_args()


def ensure_seedable_state(repo, *, hard_reset_existing_users: bool) -> None:
    """Refuse or reset when local users already exist.

    The sample manifest is intended to describe the full local user set. If the
    table already contains users, either wipe them first with the explicit reset
    flag or stop immediately so the operator does not accidentally mix states.
    """
    existing_users = repo.scan_users()
    if not existing_users:
        return

    if not hard_reset_existing_users:
        raise SystemExit(
            "Existing users were found in DynamoDB. Re-run with "
            "--hard-reset-existing-users to delete them before seeding."
        )

    log.info("Hard-resetting %s existing users before seeding", len(existing_users))
    for user in existing_users:
        repo.purge_user_account(user)


def main() -> None:
    args = parse_args()
    manifest_path = SAMPLE_DIR / "seed_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(
            f"No fixtures found at {manifest_path}. Generate them from your own GOG "
            "Galaxy DB first:\n"
            "    just gen-fixtures db=<path-to-galaxy-2.0.db>\n"
            "(or run scripts/sample_data/generate_fixtures.py directly)."
        )
    manifest = json.loads(manifest_path.read_text())
    repo = get_repository()
    queue = get_queue()
    ensure_seedable_state(
        repo, hard_reset_existing_users=args.hard_reset_existing_users
    )

    for entry in manifest:
        # Create the account first so ingest can link its db_updated_at by user_id.
        create_user(
            repo,
            email=entry["email"],
            username=entry["username"],
            password=DEFAULT_PASSWORD,
            user_id=entry["user_id"],
            is_admin=entry["admin"],
        )
        fixture = SAMPLE_DIR / entry["fixture"]
        user_id, job_id = ingest_db_file(str(fixture), repo, queue)
        log.info(
            "Seeded %s (user_id=%s) from %s; enrichment job %s",
            entry["email"],
            user_id,
            entry["fixture"],
            job_id,
        )

    log.info(
        "Done. Log in as %s / %s. Run `just worker` to enrich via IGDB.",
        manifest[0]["email"],
        DEFAULT_PASSWORD,
    )


if __name__ == "__main__":
    main()
