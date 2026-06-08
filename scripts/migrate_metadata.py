#!/usr/bin/env python3
"""Load metadata overrides from a v1 config YAML into DynamoDB.

Usage (against production):
    TABLE_PREFIX=gamatrix uv run python scripts/migrate_metadata.py /path/to/config.yaml

Usage (against local dev stack):
    uv run python scripts/migrate_metadata.py /path/to/config.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("migrate_metadata")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate v1 metadata to DynamoDB")
    parser.add_argument("config", help="Path to v1 config.yaml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching DynamoDB",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="Delete all existing override rows before importing "
        "(clears stale keys left by the old slug format)",
    )
    args = parser.parse_args()

    path = Path(args.config)
    if not path.exists():
        sys.exit(f"File not found: {path}")

    data = yaml.safe_load(path.read_text()) or {}
    metadata: dict[str, dict] = data.get("metadata") or {}
    if not metadata:
        log.warning("No 'metadata' section found in %s", path)
        return

    from gamatrix.helpers import get_slug_from_title
    from gamatrix.storage.dynamo import get_repository

    repo = None if args.dry_run else get_repository()

    if args.wipe:
        if args.dry_run:
            log.info("[dry-run] would delete all existing override rows first")
        else:
            removed = repo.clear_metadata()  # type: ignore[union-attr]
            log.info("Wiped %d existing override row(s)", removed)

    written = 0
    for title, fields in metadata.items():
        slug = get_slug_from_title(title)
        item: dict = {"slug": slug}
        if "max_players" in fields:
            item["max_players"] = fields["max_players"]
        if fields.get("comment"):
            item["comment"] = fields["comment"]
        if fields.get("url"):
            item["url"] = str(fields["url"])

        if args.dry_run:
            log.info("[dry-run] %s → %s", title, item)
        else:
            repo.put_metadata(item)  # type: ignore[union-attr]
            log.info("Wrote %s → slug=%s", title, slug)
        written += 1

    log.info("%s%d item(s) processed.", "[dry-run] " if args.dry_run else "", written)


if __name__ == "__main__":
    main()
