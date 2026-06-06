#!/usr/bin/env python3
"""Import a v1 .cache.json into the DynamoDB games table.

The v1 cache is keyed by IGDB release key and holds per-game IGDB data. We map
each entry into a v2 games-table row so existing data carries over at cutover
and the first run doesn't have to re-fetch everything from IGDB.

    python scripts/migrate_cache.py /path/to/.cache.json
"""

from __future__ import annotations

import argparse
import json
import logging

from gamatrix.constants import (
    ENRICHMENT_DONE,
    ENRICHMENT_NOT_FOUND,
    IGDB_GAME_MODE,
    IGDB_MAX_PLAYER_KEYS,
    IGDB_MULTIPLAYER_GAME_MODES,
)
from gamatrix.helpers import get_slug_from_title, now_iso
from gamatrix.storage.dynamo import get_repository

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("migrate_cache")


def _max_players(multiplayer_info: list) -> int:
    best = 0
    for platform in multiplayer_info or []:
        for key in IGDB_MAX_PLAYER_KEYS:
            if key in platform and platform[key] > best:
                best = platform[key]
    return best


def migrate(cache_path: str) -> None:
    with open(cache_path) as f:
        cache = json.load(f)

    games = cache.get("igdb", {}).get("games", {})
    repo = get_repository()
    migrated = 0
    for release_key, entry in games.items():
        igdb_id = entry.get("igdb_id", 0)
        info = entry.get("info") or [{}]
        info0 = info[0] if info else {}
        game_modes = info0.get("game_modes", [])
        name = info0.get("name")
        max_players = entry.get("max_players", _max_players(entry.get("multiplayer")))
        multiplayer = max_players > 1 or any(
            m in IGDB_MULTIPLAYER_GAME_MODES for m in game_modes
        )
        if max_players == 0 and game_modes == [IGDB_GAME_MODE["singleplayer"]]:
            max_players = 1

        repo.put_game(
            {
                "release_key": release_key,
                "title": name or release_key,
                "slug": get_slug_from_title(name) if name else "",
                "igdb_key": release_key,
                "igdb_id": igdb_id,
                "game_modes": game_modes,
                "max_players": max_players,
                "multiplayer": multiplayer,
                "rating": round(info0.get("rating", 0)),
                "rating_count": info0.get("rating_count", 0),
                "enrichment_status": (
                    ENRICHMENT_DONE if igdb_id else ENRICHMENT_NOT_FOUND
                ),
                "enriched_at": now_iso(),
            }
        )
        migrated += 1

    log.info("Migrated %d cached games into the games table", migrated)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cache_path", help="Path to v1 .cache.json")
    args = parser.parse_args()
    migrate(args.cache_path)


if __name__ == "__main__":
    main()
