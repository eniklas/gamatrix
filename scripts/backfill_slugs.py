#!/usr/bin/env python3
"""Recompute each game's stored slug from its title.

Existing rows in the games table may hold a slug in the old alphanumeric-only
format (e.g. ``groundbranch``). This rewrites every game whose slug differs from
``get_slug_from_title(title)`` so override matching lines up without waiting for
the next upload to refresh it.

Usage (against production):
    TABLE_PREFIX=gamatrix uv run python scripts/backfill_slugs.py

Usage (against local dev stack):
    uv run python scripts/backfill_slugs.py
"""

from __future__ import annotations

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("backfill_slugs")


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute games-table slugs")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without touching DynamoDB",
    )
    args = parser.parse_args()

    from gamatrix.helpers import get_slug_from_title
    from gamatrix.storage.dynamo import get_repository

    repo = get_repository()

    updated = 0
    scanned = 0
    for game in repo.scan_all_games():
        scanned += 1
        title = game.get("title")
        if not title:
            continue
        new_slug = get_slug_from_title(title)
        if new_slug == game.get("slug"):
            continue

        if args.dry_run:
            log.info("[dry-run] %s: %r -> %r", title, game.get("slug"), new_slug)
        else:
            repo.put_game({**game, "slug": new_slug})
            log.info("%s: %r -> %r", title, game.get("slug"), new_slug)
        updated += 1

    log.info(
        "%s%d of %d game(s) updated.",
        "[dry-run] " if args.dry_run else "",
        updated,
        scanned,
    )


if __name__ == "__main__":
    main()
