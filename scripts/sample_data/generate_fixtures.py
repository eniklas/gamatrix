#!/usr/bin/env python3
"""One-time generator: derive slim GOG Galaxy fixtures from a real DB.

Reads a full GOG Galaxy SQLite DB, selects a fixed number of owned games using
the real ``GogDBParser`` (so only parseable, IGDB-resolvable releases are kept),
partitions them into an overlapping ownership matrix, and writes one slim
fixture per test user plus ``seed_manifest.json``.

This is a maintainer step, not part of the dev flow: its outputs are committed
so every developer gets identical sample data with no real DB on hand. Re-run it
only to refresh the sample set. The source DB never gets committed (it lives in
the git-ignored ``tmp/``).

    docker compose run --rm -v "$PWD/tmp:/data" app \
        python scripts/sample_data/generate_fixtures.py --source /data/dereks-galaxy-2.0.db

Ownership matrix (40 distinct games, 20 per user):

    group  size  user1  user2  user3
    ALL     5      x      x      x
    P12     5      x      x
    P13     5      x             x
    U1      5      x
    U2     10             x
    U3     10                    x
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path

from gamatrix.gogdb.parser import GogDBParser

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("generate_fixtures")

OUT_DIR = Path(__file__).resolve().parent

# Test accounts paired with the fixture that holds each one's library. user_ids
# are arbitrary but stable; they become the GOG user id in the fixture's Users
# table and the account's user_id at seed time.
USERS = [
    {
        "user_id": "1001",
        "email": "alice@example.com",
        "username": "Alice",
        "admin": True,
        "fixture": "sample_user1.db",
    },
    {
        "user_id": "1002",
        "email": "bob@example.com",
        "username": "Bob",
        "admin": False,
        "fixture": "sample_user2.db",
    },
    {
        "user_id": "1003",
        "email": "carol@example.com",
        "username": "Carol",
        "admin": False,
        "fixture": "sample_user3.db",
    },
]

# Disjoint game groups and their sizes (total 40).
GROUP_SIZES = {"ALL": 5, "P12": 5, "P13": 5, "U1": 5, "U2": 10, "U3": 10}

# Which groups each user owns (keyed by user_id). Sums to 20 each.
USER_GROUPS = {
    "1001": ["ALL", "P12", "P13", "U1"],
    "1002": ["ALL", "P12", "U2"],
    "1003": ["ALL", "P13", "U3"],
}

# Column counts for the tables we copy rows into.
GamePieceTypes_COLS = 2
GamePieces_COLS = 5
ProductPurchaseDates_COLS = 4

# The parser's _installed_games() query (parser.py) joins these tables with no
# graceful fallback, so they must exist. We create them empty: nothing shows as
# installed, which is fine for sample data. (The LibraryReleases/LicensedReleases
# Game-Pass tables ARE optional — that query is wrapped in try/except — so we
# leave them out.)
EMPTY_TABLES = ("InstalledExternalProducts", "Platforms", "InstalledProducts")


def select_release_keys(source_path: str, needed: int) -> list[str]:
    """Return ``needed`` release keys from the source, chosen deterministically."""
    parser = GogDBParser(source_path)
    try:
        parsed = parser.parse()
    finally:
        parser.close()
    games = sorted(parsed.games, key=lambda g: (g["title"].lower(), g["release_key"]))
    if len(games) < needed:
        raise SystemExit(
            f"Source has only {len(games)} parseable games; need {needed}."
        )
    chosen = games[:needed]
    for g in chosen:
        log.info("  selected %-14s %s", g["release_key"], g["title"])
    return [g["release_key"] for g in chosen]


def partition(keys: list[str]) -> dict[str, list[str]]:
    """Slice the flat key list into the named groups by GROUP_SIZES order."""
    groups: dict[str, list[str]] = {}
    i = 0
    for name, size in GROUP_SIZES.items():
        groups[name] = keys[i : i + size]
        i += size
    return groups


def keys_for_user(user_id: str, groups: dict[str, list[str]]) -> list[str]:
    keys: list[str] = []
    for group_name in USER_GROUPS[user_id]:
        keys.extend(groups[group_name])
    return keys


def _copy_schema(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> None:
    row = src.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None:
        raise SystemExit(f"Source DB has no table {table!r}")
    dst.execute(row[0])


def build_fixture(
    src: sqlite3.Connection, user_id: str, release_keys: list[str], out_path: Path
) -> None:
    out_path.unlink(missing_ok=True)
    dst = sqlite3.connect(out_path)
    try:
        # Users: one row, the distinct GOG id the parser reads back.
        dst.execute("CREATE TABLE Users('id' INT64 NOT NULL PRIMARY KEY)")
        dst.execute("INSERT INTO Users(id) VALUES (?)", (int(user_id),))

        # Recreate the parser's tables with their real schema.
        for table in ("GamePieceTypes", "GamePieces", "ProductPurchaseDates"):
            _copy_schema(src, dst, table)
        # Tables the installed-games query needs; created empty.
        for table in EMPTY_TABLES:
            _copy_schema(src, dst, table)

        # GamePieceTypes is tiny and id-stable; copy it whole.
        types = src.execute("SELECT * FROM GamePieceTypes").fetchall()
        dst.executemany(
            f"INSERT INTO GamePieceTypes VALUES ({','.join('?' * GamePieceTypes_COLS)})",
            types,
        )

        placeholders = ",".join("?" * len(release_keys))
        pieces = src.execute(
            f"SELECT * FROM GamePieces WHERE releaseKey IN ({placeholders})",
            release_keys,
        ).fetchall()
        dst.executemany(
            f"INSERT INTO GamePieces VALUES ({','.join('?' * GamePieces_COLS)})",
            pieces,
        )

        purchases = src.execute(
            f"SELECT * FROM ProductPurchaseDates WHERE gameReleaseKey IN ({placeholders})",
            release_keys,
        ).fetchall()
        dst.executemany(
            "INSERT INTO ProductPurchaseDates VALUES "
            f"({','.join('?' * ProductPurchaseDates_COLS)})",
            purchases,
        )
        dst.commit()
    finally:
        dst.close()


def validate(out_path: Path, expected_user_id: str, expected_games: int) -> None:
    parser = GogDBParser(str(out_path))
    try:
        parsed = parser.parse()
    finally:
        parser.close()
    if parsed.user_id != expected_user_id:
        raise SystemExit(
            f"{out_path.name}: parsed user_id {parsed.user_id!r} "
            f"!= expected {expected_user_id!r}"
        )
    if len(parsed.games) != expected_games:
        raise SystemExit(
            f"{out_path.name}: parsed {len(parsed.games)} games, "
            f"expected {expected_games}"
        )
    log.info(
        "  validated %s: user %s, %d games",
        out_path.name,
        parsed.user_id,
        len(parsed.games),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        default="/data/dereks-galaxy-2.0.db",
        help="Path to the full GOG Galaxy SQLite DB to derive fixtures from.",
    )
    args = ap.parse_args()

    needed = sum(GROUP_SIZES.values())
    log.info("Selecting %d games from %s", needed, args.source)
    keys = select_release_keys(args.source, needed)
    groups = partition(keys)

    src = sqlite3.connect(args.source)
    try:
        for user in USERS:
            user_id = user["user_id"]
            release_keys = keys_for_user(user_id, groups)
            out_path = OUT_DIR / user["fixture"]
            log.info(
                "Building %s for user %s (%d games)",
                out_path.name,
                user_id,
                len(release_keys),
            )
            build_fixture(src, user_id, release_keys, out_path)
            validate(out_path, user_id, len(release_keys))
    finally:
        src.close()

    manifest = [
        {k: u[k] for k in ("user_id", "email", "username", "admin", "fixture")}
        for u in USERS
    ]
    (OUT_DIR / "seed_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    log.info("Wrote seed_manifest.json and %d fixtures.", len(USERS))


if __name__ == "__main__":
    main()
