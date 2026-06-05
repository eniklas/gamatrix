"""Parse a single uploaded GOG Galaxy SQLite database.

Where v1 read every user's DB at request time and computed the intersection on
the spot, v2 parses one DB per upload and writes that user's library to
DynamoDB. The common-games computation now happens in the games service from
the stored libraries. The SQLite queries themselves are carried over from v1's
gogdb_helper, with one addition: the issue #120 fix that drops expired Xbox
Game Pass titles via LicensedReleases.isOwned.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field

from gamatrix.helpers import get_slug_from_title

log = logging.getLogger(__name__)


def is_sqlite3(stream: bytes) -> bool:
    """Return True if the stream begins with the SQLite3 file header."""
    # https://www.sqlite.org/fileformat.html
    return len(stream) >= 16 and stream[:16] == b"SQLite format 3\000"


@dataclass
class ParsedLibrary:
    user_id: str
    # One entry per owned release key: ownership + install status for this user.
    entries: list[dict] = field(default_factory=list)
    # One stub per release key: the GOG-derived fields the games table needs.
    games: list[dict] = field(default_factory=list)


class GogDBParser:
    """Reads a single GOG Galaxy DB file. Not thread-safe; use per file."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()

    def close(self) -> None:
        self.conn.close()

    def get_user_id(self) -> str:
        self.cursor.execute("SELECT * FROM Users")
        rows = self.cursor.fetchall()
        if not rows:
            raise ValueError("No users found in the Users table in the DB")
        if len(rows) > 1:
            log.warning("Multiple users in DB; using the first (%s)", rows[0])
        return str(rows[0][0])

    def _gamepiecetype_id(self, name: str) -> int:
        return self.cursor.execute(
            'SELECT id FROM GamePieceTypes WHERE type="{}"'.format(name)
        ).fetchone()[0]

    def get_subscription_release_keys(self) -> set[str]:
        """Release keys present only via a subscription (issue #120).

        Xbox Game Pass titles stay in ProductPurchaseDates after they leave the
        subscription, so v1 reported them as owned forever. LicensedReleases
        flags genuine ownership: isOwned = 0 means the title is only available
        through a subscription (verified: all such rows are Xbox Game Pass).
        """
        try:
            self.cursor.execute("""SELECT lr.releaseKey
                   FROM LibraryReleases lr
                   JOIN LicensedReleases lic ON lr.id = lic.libraryId
                   WHERE lic.isOwned = 0""")
        except sqlite3.OperationalError:
            # Older GOG Galaxy schemas may lack these tables; degrade gracefully.
            log.warning("LicensedReleases/LibraryReleases not found; skipping #120 fix")
            return set()
        return {row[0] for row in self.cursor.fetchall()}

    # Carried over from v1 (originally from AB1908/GOG-Galaxy-Export-Script).
    def _owned_games(self) -> list[tuple]:
        owned_game_database = """CREATE TEMP VIEW MasterList AS
            SELECT GamePieces.releaseKey, GamePieces.gamePieceTypeId, GamePieces.value
            FROM ProductPurchaseDates
            JOIN GamePieces
            ON ProductPurchaseDates.gameReleaseKey = GamePieces.releaseKey;"""
        og_fields = [
            "CREATE TEMP VIEW MasterDB AS SELECT DISTINCT(MasterList.releaseKey) "
            "AS releaseKey, "
            "MasterList.value AS title, PLATFORMS.value AS platformList"
        ]
        og_references = [" FROM MasterList, MasterList AS PLATFORMS"]
        og_conditions = [
            " WHERE ((MasterList.gamePieceTypeId={}) OR "
            "(MasterList.gamePieceTypeId={})) "
            "AND ((PLATFORMS.releaseKey=MasterList.releaseKey) AND "
            "(PLATFORMS.gamePieceTypeId={}))".format(
                self._gamepiecetype_id("originalTitle"),
                self._gamepiecetype_id("title"),
                self._gamepiecetype_id("allGameReleases"),
            )
        ]
        og_order = " ORDER BY title;"
        og_result_fields = [
            "GROUP_CONCAT(DISTINCT MasterDB.releaseKey)",
            "MasterDB.title",
        ]
        og_query = "".join(og_fields + og_references + og_conditions) + og_order
        unique_game_data = (
            "SELECT {} FROM MasterDB GROUP BY MasterDB.platformList "
            "ORDER BY MasterDB.title;".format(", ".join(og_result_fields))
        )
        for query in [owned_game_database, og_query, unique_game_data]:
            self.cursor.execute(query)
        return self.cursor.fetchall()

    def _igdb_release_key(self, gamepiecetype_id: int, release_key: str) -> str:
        """Best release key to look up in IGDB: Steam > GOG > the key itself."""
        self.cursor.execute(
            (
                'SELECT * FROM GamePieces WHERE releaseKey="{}" '
                "and gamePieceTypeId = {}"
            ).format(release_key, gamepiecetype_id)
        )
        raw = self.cursor.fetchall()
        result = json.loads(raw[0][3])
        if "releases" not in result:
            return release_key
        for k in result["releases"]:
            # Sometimes there's steam_1234 and steam_steam_1234, always in that order.
            if k.startswith("steam_") and not k.startswith("steam_steam_"):
                return k
        for k in result["releases"]:
            if k.startswith("gog_"):
                return k
        return release_key

    def _installed_games(self) -> set[str]:
        query = """SELECT trim(GamePieces.releaseKey) FROM GamePieces
            JOIN GamePieceTypes ON GamePieces.gamePieceTypeId = GamePieceTypes.id
            WHERE releaseKey IN
            (SELECT platforms.name || '_' || InstalledExternalProducts.productId
            FROM InstalledExternalProducts
            JOIN Platforms ON InstalledExternalProducts.platformId = Platforms.id
            UNION
            SELECT 'gog_' || productId FROM InstalledProducts)
            AND GamePieceTypes.type = 'originalTitle'"""
        self.cursor.execute(query)
        installed: set[str] = set()
        for result in self.cursor.fetchall():
            for r in result:
                installed.add(r)
        return installed

    def parse(self) -> ParsedLibrary:
        user_id = self.get_user_id()
        all_releases_type = self._gamepiecetype_id("allGameReleases")
        owned = self._owned_games()
        installed = self._installed_games()
        excluded = self.get_subscription_release_keys()
        log.info(
            "Parsed DB for user %s: %d owned rows, %d installed, "
            "%d excluded (Game Pass)",
            user_id,
            len(owned),
            len(installed),
            len(excluded),
        )

        parsed = ParsedLibrary(user_id=user_id)
        seen: set[str] = set()
        for release_keys, title_json in owned:
            for release_key in release_keys.split(","):
                if release_key in excluded:
                    log.debug("%s: skipping expired Game Pass title", release_key)
                    continue

                platform = release_key.split("_")[0]
                title = json.loads(title_json).get("title")
                if title is None:
                    # e.g. epic_daac... (The Fall) has no data in some DBs.
                    log.debug("%s: skipping null title", release_key)
                    continue

                parsed.entries.append(
                    {
                        "release_key": release_key,
                        "platform": platform,
                        "installed": release_key in installed,
                    }
                )

                if release_key in seen:
                    continue
                seen.add(release_key)

                if platform == "steam":
                    igdb_key = release_key
                else:
                    igdb_key = self._igdb_release_key(all_releases_type, release_key)

                parsed.games.append(
                    {
                        "release_key": release_key,
                        "title": title,
                        "slug": get_slug_from_title(title),
                        "igdb_key": igdb_key,
                        "platform": platform,
                    }
                )

        return parsed
