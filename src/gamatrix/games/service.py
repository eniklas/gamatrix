"""Compute the displayed game list from stored libraries and metadata.

This is the v2 equivalent of v1's gogDB.get_common_games / merge_duplicate_titles
/ filter_games, but it reads from DynamoDB instead of opening SQLite DBs, and it
returns a sorted list of plain dicts that the templates (and column sorting) can
consume directly.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from gamatrix.constants import IGDB_MULTIPLAYER_GAME_MODES, PLATFORMS
from gamatrix.storage.dynamo import Repository


@dataclass
class CompareOptions:
    selected_user_ids: list[str] = field(default_factory=list)
    include_single_player: bool = False
    installed_only: bool = False
    exclude_platforms: list[str] = field(default_factory=list)
    exclusive: bool = False
    all_games: bool = False  # grid view: list everything owned, no intersection
    randomize: bool = False
    sort: str = "title"
    direction: str = "asc"


# Sortable columns -> key function over a game dict.
SORT_KEYS = {
    "title": lambda g: g["slug"],
    "players": lambda g: g.get("max_players", 0),
    "rating": lambda g: g.get("rating", 0),
    "installed": lambda g: len(g.get("installed", [])),
}


@dataclass
class CompareResult:
    games: list[dict]
    excluded_user_ids: list[str]
    total: int


def compare(repo: Repository, opts: CompareOptions) -> CompareResult:
    users = {str(u["user_id"]): u for u in repo.scan_users() if u.get("user_id")}
    selected = [str(u) for u in opts.selected_user_ids if str(u) in users]

    # For exclusive mode we need to know who else owns each game.
    excluded_ids: list[str] = []
    libraries_needed = set(selected)
    if opts.exclusive:
        excluded_ids = [u for u in users if u not in selected]
        libraries_needed.update(excluded_ids)

    # Aggregate ownership/installed/platforms per release key.
    agg: dict[str, dict] = {}
    for user_id in libraries_needed:
        for entry in repo.get_user_library(user_id):
            rk = entry["release_key"]
            platform = entry.get("platform", rk.split("_")[0])
            if platform in opts.exclude_platforms:
                continue
            slot = agg.setdefault(
                rk, {"owners": set(), "installed": set(), "platform": platform}
            )
            slot["owners"].add(user_id)
            if entry.get("installed"):
                slot["installed"].add(user_id)

    metadata = repo.batch_get_games(agg.keys())
    overrides = repo.get_all_metadata()

    games: list[dict] = []
    for rk, slot in agg.items():
        meta = metadata.get(rk)
        if meta is None:
            continue  # not yet ingested into the games table
        game = _build_game(rk, slot, meta, overrides)
        games.append(game)

    games = _merge_duplicates(games)
    games = _filter(games, opts, selected, excluded_ids)
    games.sort(
        key=SORT_KEYS.get(opts.sort, SORT_KEYS["title"]),
        reverse=opts.direction == "desc",
    )

    total = len(games)
    if opts.randomize and games:
        games = [random.choice(games)]

    return CompareResult(games=games, excluded_user_ids=excluded_ids, total=total)


def _build_game(rk: str, slot: dict, meta: dict, overrides: dict) -> dict:
    game = {
        "release_key": rk,
        "title": meta.get("title", rk),
        "slug": meta.get("slug", ""),
        "igdb_key": meta.get("igdb_key", rk),
        "platforms": [slot["platform"]],
        "owners": sorted(slot["owners"]),
        "installed": sorted(slot["installed"]),
        "max_players": meta.get("max_players", 0),
        "multiplayer": meta.get("multiplayer", False),
        "rating": meta.get("rating", 0),
        "rating_count": meta.get("rating_count", 0),
        "enrichment_status": meta.get("enrichment_status"),
        "comment": "",
        "url": None,
    }

    # Apply manual overrides (config metadata in v1) by slug; these win over IGDB.
    override = overrides.get(game["slug"])
    if override:
        if "max_players" in override:
            game["max_players"] = override["max_players"]
            game["multiplayer"] = _multiplayer(
                override["max_players"], meta.get("game_modes", [])
            )
        if override.get("comment"):
            game["comment"] = override["comment"]
        if override.get("url"):
            game["url"] = override["url"]
    return game


def _multiplayer(max_players: int, game_modes: list[int]) -> bool:
    if max_players > 1:
        return True
    return any(m in IGDB_MULTIPLAYER_GAME_MODES for m in game_modes)


def _merge_duplicates(games: list[dict]) -> list[dict]:
    """Merge same-title entries that have identical owners (cross-platform copies).

    Mirrors v1: copies of the same game on different stores held by the same set
    of owners collapse into one row listing all platforms; copies owned by
    different people stay separate.
    """
    grouped: dict[tuple[str, frozenset], dict] = {}
    for game in games:
        key = (game["slug"], frozenset(game["owners"]))
        if key not in grouped:
            grouped[key] = {**game, "platforms": list(game["platforms"])}
            continue
        existing = grouped[key]
        for platform in game["platforms"]:
            if platform not in existing["platforms"]:
                existing["platforms"].append(platform)
        existing["installed"] = sorted(
            set(existing["installed"]) | set(game["installed"])
        )
        existing["max_players"] = max(existing["max_players"], game["max_players"])
        existing["multiplayer"] = existing["multiplayer"] or game["multiplayer"]
        existing["rating"] = max(existing["rating"], game["rating"])

    for game in grouped.values():
        game["platforms"] = _sort_platforms(game["platforms"])
    return list(grouped.values())


def _sort_platforms(platforms: list[str]) -> list[str]:
    return sorted(
        platforms,
        key=lambda p: PLATFORMS.index(p) if p in PLATFORMS else len(PLATFORMS),
    )


def _filter(
    games: list[dict],
    opts: CompareOptions,
    selected: list[str],
    excluded_ids: list[str],
) -> list[dict]:
    selected_set = set(selected)
    excluded_set = set(excluded_ids)
    result = []
    for game in games:
        if not opts.include_single_player and not game["multiplayer"]:
            continue
        if not opts.all_games:
            owners = set(game["owners"])
            if not selected_set.issubset(owners):
                continue
            if opts.installed_only and not selected_set.issubset(
                set(game["installed"])
            ):
                continue
            if opts.exclusive and owners & excluded_set:
                continue
        result.append(game)
    return result


def build_caption(repo: Repository, opts: CompareOptions, result: CompareResult) -> str:
    users = {str(u["user_id"]): u for u in repo.scan_users() if u.get("user_id")}
    names = [users[u]["username"] for u in opts.selected_user_ids if u in users]

    if opts.randomize:
        start = f"Random game selected from {result.total}"
    else:
        start = str(result.total)

    if opts.all_games:
        middle = "total games owned by"
    elif len(opts.selected_user_ids) == 1:
        middle = "games owned by"
    else:
        middle = "games in common between"

    caption = f"{start} {middle} {', '.join(names)}"

    if opts.exclusive and result.excluded_user_ids and not opts.all_games:
        excluded_names = [
            users[u]["username"] for u in result.excluded_user_ids if u in users
        ]
        caption += f" and not owned by {', '.join(excluded_names)}"
    if opts.exclude_platforms:
        caption += f" ({', '.join(opts.exclude_platforms).title()} excluded)"
    if opts.installed_only and not opts.all_games:
        caption += " (installed only)"
    return caption
