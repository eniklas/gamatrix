"""Tests for the GOG Galaxy DB parser, including the issue #120 fix."""

from __future__ import annotations

import json
import sqlite3

import pytest

from gamatrix.gogdb.ingest import ingest_db_file
from gamatrix.gogdb.parser import GogDBParser, is_sqlite3
from gamatrix.storage.queue import EnrichmentQueue


def _build_gog_db(path: str) -> None:
    """Create a minimal GOG Galaxy schema with the tables the parser reads."""
    conn = sqlite3.connect(path)
    c = conn.cursor()

    c.execute("CREATE TABLE Users (id INTEGER, name TEXT)")
    c.execute("INSERT INTO Users VALUES (12345, 'tester')")

    c.execute("CREATE TABLE GamePieceTypes (id INTEGER, type TEXT)")
    c.executemany(
        "INSERT INTO GamePieceTypes VALUES (?, ?)",
        [(1, "originalTitle"), (2, "title"), (3, "allGameReleases")],
    )

    # Real GOG schema has value at column index 3 (id, releaseKey, type, value);
    # the parser reads raw[0][3], so mirror that column order here.
    c.execute(
        "CREATE TABLE GamePieces (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "releaseKey TEXT, gamePieceTypeId INTEGER, value TEXT)"
    )
    c.execute("CREATE TABLE ProductPurchaseDates (gameReleaseKey TEXT)")

    # owned title, owned-flag (isOwned), installed?, releases-list
    games = [
        ("steam_1", "Alpha", 1, True, ["steam_1"]),
        ("gog_2", "Beta", 1, False, ["gog_2"]),
        # Expired Game Pass title: still in ProductPurchaseDates but isOwned = 0.
        ("xboxone_100", "GamePassGone", 0, False, ["xboxone_100"]),
        # Genuinely purchased Xbox title: isOwned = 1, must be kept.
        ("xboxone_200", "RealXbox", 1, False, ["xboxone_200"]),
        # Current Game Pass title GOG wrongly flags isOwned = 1, but it is still
        # listed in SubscriptionReleases, so the #120 fix must drop it too.
        ("xboxone_300", "GamePassOwnedFlag", 1, False, ["xboxone_300"]),
    ]

    for rk, title, _owned, _installed, releases in games:
        for type_id in (1, 2):
            c.execute(
                "INSERT INTO GamePieces (releaseKey, gamePieceTypeId, value) "
                "VALUES (?, ?, ?)",
                (rk, type_id, json.dumps({"title": title})),
            )
        c.execute(
            "INSERT INTO GamePieces (releaseKey, gamePieceTypeId, value) "
            "VALUES (?, ?, ?)",
            (rk, 3, json.dumps({"releases": releases})),
        )
        c.execute("INSERT INTO ProductPurchaseDates VALUES (?)", (rk,))

    # LibraryReleases + LicensedReleases drive the #120 filter.
    c.execute(
        "CREATE TABLE LibraryReleases "
        "(id INTEGER PRIMARY KEY, userId INTEGER, releaseKey TEXT)"
    )
    c.execute("CREATE TABLE LicensedReleases (libraryId INTEGER, isOwned BOOLEAN)")
    library_ids = {}
    for i, (rk, _t, owned, _inst, _r) in enumerate(games, start=1):
        library_ids[rk] = i
        c.execute("INSERT INTO LibraryReleases VALUES (?, ?, ?)", (i, 12345, rk))
        c.execute("INSERT INTO LicensedReleases VALUES (?, ?)", (i, 1 if owned else 0))

    # SubscriptionReleases lists titles available through a subscription, keyed
    # by libraryId. xboxone_300 sits here despite isOwned = 1 (see #120 fix).
    c.execute(
        "CREATE TABLE SubscriptionReleases "
        "(id INTEGER PRIMARY KEY, subscriptionId INTEGER, licenseId INTEGER)"
    )
    c.execute(
        "INSERT INTO SubscriptionReleases (subscriptionId, licenseId) VALUES (?, ?)",
        (1, library_ids["xboxone_300"]),
    )

    # PlatformConnections drives the #186 fix: steam and xboxone are connected,
    # epic is not (its titles are already gone from ProductPurchaseDates).
    c.execute(
        "CREATE TABLE PlatformConnections "
        "(userId INTEGER, platform TEXT, connectionState TEXT)"
    )
    c.executemany(
        "INSERT INTO PlatformConnections VALUES (12345, ?, ?)",
        [("steam", "Connected"), ("xboxone", "Connected"), ("epic", "Disconnected")],
    )

    # Installed-games query plumbing: steam_1 is installed.
    c.execute("CREATE TABLE Platforms (id INTEGER, name TEXT)")
    c.execute("INSERT INTO Platforms VALUES (1, 'steam')")
    c.execute(
        "CREATE TABLE InstalledExternalProducts (platformId INTEGER, productId TEXT)"
    )
    c.execute("INSERT INTO InstalledExternalProducts VALUES (1, '1')")
    c.execute("CREATE TABLE InstalledProducts (productId TEXT)")

    conn.commit()
    conn.close()


@pytest.fixture
def gog_db(tmp_path):
    path = str(tmp_path / "galaxy-2.0.db")
    _build_gog_db(path)
    return path


def test_is_sqlite3(gog_db):
    with open(gog_db, "rb") as f:
        assert is_sqlite3(f.read(16))
    assert not is_sqlite3(b"not a db")


def test_parse_excludes_expired_game_pass(gog_db):
    parser = GogDBParser(gog_db)
    try:
        parsed = parser.parse()
    finally:
        parser.close()

    assert parsed.user_id == "12345"
    keys = {e["release_key"] for e in parsed.entries}
    # Game Pass titles dropped (isOwned = 0 and the isOwned = 1 / in-subscription
    # case); purchased Xbox title kept.
    assert "xboxone_100" not in keys
    assert "xboxone_300" not in keys
    assert "xboxone_200" in keys
    assert {"steam_1", "gog_2", "xboxone_200"} == keys


def test_subscription_release_keys(gog_db):
    parser = GogDBParser(gog_db)
    try:
        excluded = parser.get_subscription_release_keys()
    finally:
        parser.close()
    assert excluded == {"xboxone_100", "xboxone_300"}


def test_installed_and_igdb_key(gog_db):
    parser = GogDBParser(gog_db)
    try:
        parsed = parser.parse()
    finally:
        parser.close()

    by_key = {e["release_key"]: e for e in parsed.entries}
    assert by_key["steam_1"]["installed"] is True
    assert by_key["gog_2"]["installed"] is False

    games = {g["release_key"]: g for g in parsed.games}
    assert games["steam_1"]["igdb_key"] == "steam_1"
    assert games["gog_2"]["igdb_key"] == "gog_2"
    assert games["steam_1"]["slug"] == "alpha"


def _add_duplicate_platform_list_row(path: str, release_key: str) -> None:
    """Add a second allGameReleases row for one title.

    This mirrors a real production failure mode: the parser's owned-games query
    groups by platform-list rows, so duplicate allGameReleases rows for the same
    release can cause the same release_key to appear multiple times in
    ParsedLibrary.entries, which then breaks DynamoDB batch writes.
    """
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute(
        "INSERT INTO GamePieces (releaseKey, gamePieceTypeId, value) VALUES (?, ?, ?)",
        (release_key, 3, json.dumps({"releases": [release_key, "gog_2"]})),
    )
    conn.commit()
    conn.close()


def test_parse_dedupes_duplicate_release_keys(gog_db):
    _add_duplicate_platform_list_row(gog_db, "steam_1")

    parser = GogDBParser(gog_db)
    try:
        parsed = parser.parse()
    finally:
        parser.close()

    steam_entries = [e for e in parsed.entries if e["release_key"] == "steam_1"]
    assert steam_entries == [
        {"release_key": "steam_1", "platform": "steam", "installed": True}
    ]


def test_ingest_same_db_twice_with_duplicate_parser_rows_is_idempotent(
    gog_db, repo, settings
):
    _add_duplicate_platform_list_row(gog_db, "steam_1")

    queue = EnrichmentQueue(settings=settings)
    first_user_id, first_job_id = ingest_db_file(gog_db, repo, queue)
    second_user_id, second_job_id = ingest_db_file(gog_db, repo, queue)

    assert first_user_id == "12345"
    assert second_user_id == "12345"
    assert first_job_id is not None
    assert second_job_id is not None

    library = repo.get_user_library("12345")
    assert {row["release_key"] for row in library} == {
        "steam_1",
        "gog_2",
        "xboxone_200",
    }
    assert len(library) == 3


def _set_connection_state(path: str, platform: str, state: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO PlatformConnections VALUES (12345, ?, ?) ", (platform, state)
    )
    conn.execute(
        "DELETE FROM PlatformConnections WHERE platform = ? AND connectionState != ?",
        (platform, state),
    )
    conn.commit()
    conn.close()


def _parse(path: str):
    parser = GogDBParser(path)
    try:
        return parser.parse()
    finally:
        parser.close()


def test_connected_platforms(gog_db):
    parser = GogDBParser(gog_db)
    try:
        connected = parser.get_connected_platforms()
    finally:
        parser.close()
    # gog has no integration row of its own but is always authoritative.
    assert connected == {"steam", "xboxone", "gog"}
    assert "epic" not in connected


def test_authoritative_platforms_exclude_disconnected_integrations(gog_db):
    """A platform whose titles are still present but is no longer connected
    (the window before GOG finishes purging) must not license deletions."""
    _set_connection_state(gog_db, "xboxone", "Disconnected")

    parsed = _parse(gog_db)

    assert "xboxone_200" in {e["release_key"] for e in parsed.entries}
    assert parsed.authoritative_platforms == {"steam", "gog"}


def test_authoritative_platforms_without_platform_connections_table(gog_db):
    """Older GOG schemas have no PlatformConnections; fall back to "a platform
    that reported at least one title speaks for itself"."""
    conn = sqlite3.connect(gog_db)
    conn.execute("DROP TABLE PlatformConnections")
    conn.commit()
    conn.close()

    parsed = _parse(gog_db)

    assert parsed.authoritative_platforms == {"steam", "gog", "xboxone"}


def test_ingest_keeps_titles_from_a_disconnected_platform(gog_db, repo, settings):
    """Issue #186: disconnecting an integration purges its titles from the GOG
    DB, and that must not empty the platform out of gamatrix."""
    repo.replace_user_library(
        "12345",
        [
            {
                "release_key": "epic_9",
                "platform": "epic",
                "installed": True,
                "db_updated_at": "2020-01-01T00:00:00+00:00",
            }
        ],
    )

    ingest_db_file(gog_db, repo, EnrichmentQueue(settings=settings))

    library = {row["release_key"]: row for row in repo.get_user_library("12345")}
    assert set(library) == {"steam_1", "gog_2", "xboxone_200", "epic_9"}
    # Retained untouched, so its db_updated_at still records when it was last
    # actually seen in an upload.
    assert library["epic_9"]["db_updated_at"] == "2020-01-01T00:00:00+00:00"
    assert library["epic_9"]["installed"] is True


def test_ingest_removes_titles_a_connected_platform_stopped_reporting(
    gog_db, repo, settings
):
    """The legitimate removals — refunds, a cancelled subscription, a title
    leaving Game Pass — all come from a platform that is still connected."""
    repo.replace_user_library(
        "12345",
        [
            {"release_key": "xboxone_500", "platform": "xboxone", "installed": False},
            {"release_key": "steam_99", "platform": "steam", "installed": False},
        ],
    )

    ingest_db_file(gog_db, repo, EnrichmentQueue(settings=settings))

    library = {row["release_key"] for row in repo.get_user_library("12345")}
    assert library == {"steam_1", "gog_2", "xboxone_200"}


def test_ingest_keeps_titles_from_a_connected_platform_reporting_nothing(
    gog_db, repo, settings
):
    """A signed-in but unsynced integration reports zero titles; that is not
    evidence the user sold their library."""
    _set_connection_state(gog_db, "uplay", "Connected")
    repo.replace_user_library(
        "12345",
        [{"release_key": "uplay_7", "platform": "uplay", "installed": False}],
    )

    ingest_db_file(gog_db, repo, EnrichmentQueue(settings=settings))

    library = {row["release_key"] for row in repo.get_user_library("12345")}
    assert library == {"steam_1", "gog_2", "xboxone_200", "uplay_7"}


def test_delete_user_library_platform_is_the_escape_hatch(gog_db, repo, settings):
    repo.replace_user_library(
        "12345",
        [
            {"release_key": "epic_9", "platform": "epic", "installed": False},
            {"release_key": "epic_10", "platform": "epic", "installed": False},
        ],
    )
    ingest_db_file(gog_db, repo, EnrichmentQueue(settings=settings))

    removed = repo.delete_user_library_platform("12345", "epic")

    assert removed == 2
    library = {row["release_key"] for row in repo.get_user_library("12345")}
    assert library == {"steam_1", "gog_2", "xboxone_200"}


def test_ingest_writes_db_updated_at_to_user_record(gog_db, repo, settings):
    repo.put_user(
        {"email": "tester@example.com", "user_id": "12345", "username": "tester"}
    )
    queue = EnrichmentQueue(settings=settings)

    ingest_db_file(gog_db, repo, queue)

    user = repo.get_user("tester@example.com")
    assert user is not None
    assert user.get("db_updated_at") is not None
    assert user["db_updated_at"] != "never"
