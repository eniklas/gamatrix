"""Tests for schema-driven fixture generation."""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path


def _load_generate_fixtures(monkeypatch) -> object:
    """Import the fixture generator as a top-level script module."""
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts" / "sample_data"
    monkeypatch.syspath_prepend(str(scripts_dir))
    sys.modules.pop("generate_fixtures", None)
    return importlib.import_module("generate_fixtures")


def _build_source_db_with_extended_tables(path: Path) -> None:
    """Create a minimal GOG DB whose copied tables include extra columns.

    The parser still expects certain key columns and the GamePieces.value field
    to remain at column index 3, so these schema extensions append columns
    rather than reordering the existing parser-relevant ones.
    """
    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    cursor.execute("CREATE TABLE Users (id INTEGER, name TEXT)")
    cursor.execute("INSERT INTO Users VALUES (12345, 'source-user')")

    cursor.execute(
        "CREATE TABLE GamePieceTypes (id INTEGER, type TEXT, source_tag TEXT)"
    )
    cursor.executemany(
        "INSERT INTO GamePieceTypes VALUES (?, ?, ?)",
        [
            (1, "originalTitle", "core"),
            (2, "title", "core"),
            (3, "allGameReleases", "core"),
        ],
    )

    cursor.execute(
        "CREATE TABLE GamePieces ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "releaseKey TEXT, "
        "gamePieceTypeId INTEGER, "
        "value TEXT, "
        "source_tag TEXT)"
    )
    cursor.execute(
        "CREATE TABLE ProductPurchaseDates ("
        "gameReleaseKey TEXT, "
        "purchased_at TEXT, "
        "source_tag TEXT, "
        "ordinal INTEGER)"
    )

    games = [
        ("steam_1", "Alpha", ["steam_1"]),
        ("gog_2", "Beta", ["gog_2"]),
        ("epic_3", "Gamma", ["epic_3"]),
    ]
    for release_key, title, releases in games:
        for type_id in (1, 2):
            cursor.execute(
                "INSERT INTO GamePieces "
                "(releaseKey, gamePieceTypeId, value, source_tag) "
                "VALUES (?, ?, ?, ?)",
                (release_key, type_id, json.dumps({"title": title}), "piece"),
            )
        cursor.execute(
            "INSERT INTO GamePieces "
            "(releaseKey, gamePieceTypeId, value, source_tag) "
            "VALUES (?, ?, ?, ?)",
            (release_key, 3, json.dumps({"releases": releases}), "piece"),
        )
        cursor.execute(
            "INSERT INTO ProductPurchaseDates VALUES (?, ?, ?, ?)",
            (release_key, "2026-01-01", "purchase", len(releases)),
        )

    cursor.execute("CREATE TABLE Platforms (id INTEGER, name TEXT)")
    cursor.execute(
        "CREATE TABLE InstalledExternalProducts (platformId INTEGER, productId TEXT)"
    )
    cursor.execute("CREATE TABLE InstalledProducts (productId TEXT)")

    conn.commit()
    conn.close()


def _column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return table column names in declaration order."""
    return [row[1] for row in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]


def test_copy_table_rows_handles_schema_drift_for_future_tables(monkeypatch, tmp_path):
    generate_fixtures = _load_generate_fixtures(monkeypatch)
    src = sqlite3.connect(":memory:")
    dst = sqlite3.connect(":memory:")
    try:
        src.execute(
            'CREATE TABLE "FutureTable" (id INTEGER, name TEXT, extra_json TEXT)'
        )
        src.executemany(
            'INSERT INTO "FutureTable" VALUES (?, ?, ?)',
            [(1, "alpha", '{"x":1}'), (2, "beta", '{"x":2}')],
        )
        dst.execute(
            'CREATE TABLE "FutureTable" (id INTEGER, name TEXT, extra_json TEXT)'
        )

        generate_fixtures._copy_table_rows(
            src, dst, "FutureTable", "id IN (?, ?)", (1, 2)
        )

        copied = dst.execute(
            'SELECT id, name, extra_json FROM "FutureTable" ORDER BY id'
        ).fetchall()
    finally:
        src.close()
        dst.close()

    assert copied == [(1, "alpha", '{"x":1}'), (2, "beta", '{"x":2}')]


def test_build_fixture_preserves_extended_table_schemas(monkeypatch, tmp_path):
    generate_fixtures = _load_generate_fixtures(monkeypatch)
    source_path = tmp_path / "source.db"
    fixture_path = tmp_path / "fixture.db"
    _build_source_db_with_extended_tables(source_path)

    src = sqlite3.connect(source_path)
    try:
        generate_fixtures.build_fixture(
            src,
            user_id="2001",
            release_keys=["steam_1", "gog_2"],
            out_path=fixture_path,
        )
    finally:
        src.close()

    generate_fixtures.validate(fixture_path, "2001", 2)

    src_check = sqlite3.connect(source_path)
    dst_check = sqlite3.connect(fixture_path)
    try:
        for table in ("GamePieceTypes", "GamePieces", "ProductPurchaseDates"):
            assert _column_names(dst_check, table) == _column_names(src_check, table)

        assert (
            dst_check.execute("SELECT COUNT(*) FROM GamePieceTypes").fetchone()[0] == 3
        )
        assert dst_check.execute("SELECT COUNT(*) FROM GamePieces").fetchone()[0] == 6
        assert (
            dst_check.execute("SELECT COUNT(*) FROM ProductPurchaseDates").fetchone()[0]
            == 2
        )
    finally:
        src_check.close()
        dst_check.close()
