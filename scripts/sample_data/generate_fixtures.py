#!/usr/bin/env python3
"""Generate slim GOG Galaxy fixtures from a real DB for local dev seeding.

Reads a full GOG Galaxy SQLite DB, selects games using the real ``GogDBParser``
(so only parseable, IGDB-resolvable releases are kept), partitions them into a
configurable overlapping ownership matrix, and writes one slim fixture per test
user plus ``seed_manifest.json``.

Each developer runs this against their **own** local GOG Galaxy DB; the outputs
are git-ignored, not committed. A GOG Galaxy DB is therefore a hard prerequisite
for sample data (it is the product's whole input). On Windows the DB lives at
``C:\\ProgramData\\GOG.com\\Galaxy\\storage\\galaxy-2.0.db``.

    docker compose run --rm -v "/abs/path/galaxy-2.0.db:/data/source.db:ro" app \\
        python scripts/sample_data/generate_fixtures.py --source /data/source.db

Ownership matrix (configurable via flags). With N users, G games each, C games
common to all, and P games shared by every unique pair, each user owns:

    G = C + P*(N-1) + uniques        (uniques = G - C - P*(N-1), must be >= 0)

so the total number of distinct games drawn from the source is

    total = C + P*comb(N, 2) + N*uniques

The defaults (N=3, G=20, C=5, P=5) yield 5 uniques each and 35 distinct games,
with a 5-game overlap on every pair of users.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from itertools import combinations
from pathlib import Path

from gamatrix.gogdb.parser import GogDBParser

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("generate_fixtures")

OUT_DIR = Path(__file__).resolve().parent

# Defaults for the ownership matrix; each is overridable on the CLI.
DEFAULT_NUM_USERS = 3
DEFAULT_GAMES_PER_USER = 20
DEFAULT_COMMON = 5
DEFAULT_PAIR_OVERLAP = 5

# user_ids are arbitrary but stable; they become the GOG user id in the fixture's
# Users table and the account's user_id at seed time. User index u -> 1001 + u.
BASE_USER_ID = 1001

# The parser's _installed_games() query (parser.py) joins these tables with no
# graceful fallback, so they must exist. We create them empty: nothing shows as
# installed, which is fine for sample data. (The LibraryReleases/LicensedReleases
# Game-Pass tables ARE optional — that query is wrapped in try/except — so we
# leave them out.)
EMPTY_TABLES = ("InstalledExternalProducts", "Platforms", "InstalledProducts")


def parse_usernames(raw: str, num_users: int | None) -> list[str]:
    """Resolve the list of user emails from --usernames / --num-users.

    If ``raw`` is given it determines the user count (and must agree with
    ``--num-users`` when both are supplied). Otherwise emails are generated as
    ``user1@example.com`` … for ``num_users`` (defaulting to DEFAULT_NUM_USERS).
    """
    if raw.strip():
        names = [n for n in re.split(r"[,\s]+", raw.strip()) if n]
        if not names:
            raise SystemExit("No usernames parsed from --usernames.")
        if num_users is not None and num_users != len(names):
            raise SystemExit(
                f"--num-users ({num_users}) does not match the "
                f"{len(names)} names in --usernames."
            )
        return names
    n = num_users if num_users is not None else DEFAULT_NUM_USERS
    if n < 1:
        raise SystemExit("--num-users must be >= 1.")
    return [f"user{i + 1}@example.com" for i in range(n)]


def compute_layout(
    num_users: int, games_per_user: int, common: int, pair_overlap: int
) -> tuple[list[tuple[int, int]], int, int]:
    """Return (pairs, uniques_per_user, total_distinct) or exit with guidance."""
    for name, val in (
        ("--games-per-user", games_per_user),
        ("--common", common),
        ("--pair-overlap", pair_overlap),
    ):
        if val < 0:
            raise SystemExit(f"{name} must be >= 0.")
    pairs = list(combinations(range(num_users), 2))
    floor = common + pair_overlap * (num_users - 1)
    uniques = games_per_user - floor
    if uniques < 0:
        raise SystemExit(
            f"--games-per-user ({games_per_user}) is too small: each user needs "
            f"--common ({common}) + --pair-overlap*(N-1) "
            f"({pair_overlap}*{num_users - 1}={pair_overlap * (num_users - 1)}) = "
            f"{floor} games before any uniques. Raise --games-per-user or lower "
            "--common / --pair-overlap."
        )
    total = common + pair_overlap * len(pairs) + num_users * uniques
    return pairs, uniques, total


def assign_keys(
    keys: list[str],
    num_users: int,
    common: int,
    pair_overlap: int,
    pairs: list[tuple[int, int]],
    uniques: int,
) -> dict[int, list[str]]:
    """Slice the flat key list into each user's library by the ownership matrix."""
    i = 0
    all_group = keys[i : i + common]
    i += common

    user_keys: dict[int, list[str]] = {u: list(all_group) for u in range(num_users)}

    for a, b in pairs:
        group = keys[i : i + pair_overlap]
        i += pair_overlap
        user_keys[a].extend(group)
        user_keys[b].extend(group)

    for u in range(num_users):
        user_keys[u].extend(keys[i : i + uniques])
        i += uniques

    return user_keys


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
            f"Source has only {len(games)} parseable games; need {needed}. "
            "Lower --num-users / --games-per-user / overlaps, or use a fuller DB."
        )
    chosen = games[:needed]
    log.info("Selected %d games from %d available.", len(chosen), len(games))
    return [g["release_key"] for g in chosen]


def _copy_schema(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> None:
    row = src.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None:
        raise SystemExit(f"Source DB has no table {table!r}")
    dst.execute(row[0])


def _quote_ident(identifier: str) -> str:
    """Return a SQLite-safe quoted identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def _copy_table_rows(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
    where_clause: str = "",
    params: tuple | list = (),
) -> None:
    """Copy rows from one SQLite table to another using runtime column metadata.

    The destination table is assumed to already exist with a compatible schema.
    Column names and placeholder counts come from the source query itself, so
    upstream tables can add columns without requiring this script to be updated.
    """
    query = f"SELECT * FROM {_quote_ident(table)}"
    if where_clause:
        query += f" WHERE {where_clause}"

    cursor = src.execute(query, params)
    rows = cursor.fetchall()
    columns = [column[0] for column in cursor.description or ()]
    if not rows or not columns:
        return

    column_sql = ", ".join(_quote_ident(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    dst.executemany(
        f"INSERT INTO {_quote_ident(table)} ({column_sql}) VALUES ({placeholders})",
        rows,
    )


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
        _copy_table_rows(src, dst, "GamePieceTypes")

        placeholders = ",".join("?" * len(release_keys))
        _copy_table_rows(
            src,
            dst,
            "GamePieces",
            f"releaseKey IN ({placeholders})",
            release_keys,
        )
        _copy_table_rows(
            src,
            dst,
            "ProductPurchaseDates",
            f"gameReleaseKey IN ({placeholders})",
            release_keys,
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


def build_users(usernames: list[str]) -> list[dict]:
    """Build the manifest user records; the first user is the admin."""
    users = []
    for u, email in enumerate(usernames):
        users.append(
            {
                "user_id": str(BASE_USER_ID + u),
                "email": email,
                "username": email.split("@")[0],
                "admin": u == 0,
                "fixture": f"sample_user{u + 1}.db",
            }
        )
    return users


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--source",
        required=True,
        help="Path to the full GOG Galaxy SQLite DB to derive fixtures from.",
    )
    ap.add_argument(
        "--output",
        default=str(OUT_DIR),
        help="Directory to write the fixtures and seed_manifest.json into.",
    )
    ap.add_argument(
        "--num-users",
        type=int,
        default=None,
        help=f"Number of test users (default {DEFAULT_NUM_USERS}; "
        "ignored if --usernames is given).",
    )
    ap.add_argument(
        "--games-per-user",
        type=int,
        default=DEFAULT_GAMES_PER_USER,
        help=f"Games in each user's library (default {DEFAULT_GAMES_PER_USER}).",
    )
    ap.add_argument(
        "--common",
        type=int,
        default=DEFAULT_COMMON,
        help=f"Games owned by all users (default {DEFAULT_COMMON}).",
    )
    ap.add_argument(
        "--pair-overlap",
        type=int,
        default=DEFAULT_PAIR_OVERLAP,
        help=(
            "Games shared by each unique pair of users "
            f"(default {DEFAULT_PAIR_OVERLAP})."
        ),
    )
    ap.add_argument(
        "--usernames",
        default="",
        help="Optional comma/space separated emails (first is the admin). "
        "Defaults to user1@example.com, user2@example.com, ...",
    )
    args = ap.parse_args()

    usernames = parse_usernames(args.usernames, args.num_users)
    num_users = len(usernames)
    users = build_users(usernames)

    pairs, uniques, total = compute_layout(
        num_users, args.games_per_user, args.common, args.pair_overlap
    )
    log.info(
        "Layout: %d users, %d games each, %d common, %d per pair, %d unique each "
        "(%d distinct games).",
        num_users,
        args.games_per_user,
        args.common,
        args.pair_overlap,
        uniques,
        total,
    )

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    keys = select_release_keys(args.source, total)
    user_keys = assign_keys(
        keys, num_users, args.common, args.pair_overlap, pairs, uniques
    )

    src = sqlite3.connect(args.source)
    try:
        for u, user in enumerate(users):
            release_keys = user_keys[u]
            assert (
                len(release_keys) == args.games_per_user
            ), f"user {u}: {len(release_keys)} keys != {args.games_per_user}"
            out_path = out_dir / user["fixture"]
            log.info(
                "Building %s for %s (user %s, %d games)",
                out_path.name,
                user["email"],
                user["user_id"],
                len(release_keys),
            )
            build_fixture(src, user["user_id"], release_keys, out_path)
            validate(out_path, user["user_id"], args.games_per_user)
    finally:
        src.close()

    manifest = [
        {k: u[k] for k in ("user_id", "email", "username", "admin", "fixture")}
        for u in users
    ]
    (out_dir / "seed_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    log.info("Wrote seed_manifest.json and %d fixtures to %s.", num_users, out_dir)


if __name__ == "__main__":
    main()
