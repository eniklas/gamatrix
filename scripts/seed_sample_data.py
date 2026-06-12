#!/usr/bin/env python3
"""Seed the local environment with 3 test users and their game libraries.

Mirrors the browser upload path (`/upload/complete`): for each committed
fixture, create the account, then ingest the fixture so its library + game stubs
land in DynamoDB and an enrichment job is queued for the local worker. The
fixtures encode an overlapping ownership matrix (see
``scripts/sample_data/generate_fixtures.py``) so the compare view has real
common/uncommon games to work with.

Idempotent: existing users are skipped and libraries are replaced wholesale, so
re-running resets cleanly.

    just seed-local      # or: python scripts/seed_sample_data.py
"""

from __future__ import annotations

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


def main() -> None:
    manifest = json.loads((SAMPLE_DIR / "seed_manifest.json").read_text())
    repo = get_repository()
    queue = get_queue()

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
