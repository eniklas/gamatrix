"""UX-neutral comparison and refresh-planning services.

This module owns the application-layer contract for comparing libraries from the
stored read model. It returns typed comparison items and keeps presentation
concerns such as captions, random selection, and template context outside the
core service so the same data contract can drive multiple UX layers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Iterable, Literal, Protocol

from gamatrix.config import Settings, get_settings
from gamatrix.constants import (
    ENRICHMENT_PENDING,
    IGDB_MULTIPLAYER_GAME_MODES,
    PLATFORMS,
)
from gamatrix.helpers import parse_iso
from gamatrix.jobs import create_enrichment_job
from gamatrix.storage.dynamo import Repository
from gamatrix.storage.queue import EnrichmentQueue


@dataclass
class SortSpec:
    field: str = "title"
    direction: Literal["asc", "desc"] = "asc"


@dataclass
class ComparisonQuery:
    selected_user_ids: list[str] = field(default_factory=list)
    include_single_player: bool = False
    installed_only: bool = False
    exclude_platforms: list[str] = field(default_factory=list)
    exclusive: bool = False
    scope: Literal["shared", "owned"] = "shared"
    sort: SortSpec = field(default_factory=SortSpec)


class ComparisonRepository(Protocol):
    """Read-model operations the comparison service needs."""

    def scan_users(self) -> list[dict]: ...

    def get_user_library(self, user_id: str) -> list[dict]: ...

    def batch_get_games(self, release_keys: Iterable[str]) -> dict[str, dict]: ...

    def get_all_metadata(self) -> dict[str, dict]: ...


# Sortable columns -> key function over a comparison item.
SORT_KEYS = {
    "title": lambda g: g.slug,
    "players": lambda g: g.max_players,
    "rating": lambda g: g.rating,
    "installed": lambda g: len(g.installed),
}


@dataclass
class ComparisonItem:
    release_key: str
    title: str
    slug: str
    igdb_key: str
    platforms: list[str]
    owners: list[str]
    installed: list[str]
    max_players: int
    multiplayer: bool
    rating: int | float
    rating_count: int = 0
    enrichment_status: str | None = None
    comment: str = ""
    url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ComparisonDataset:
    items: list[ComparisonItem]
    excluded_user_ids: list[str]
    total: int


@dataclass
class RefreshAdvice:
    job_id: str | None
    stale_release_keys: list[str] = field(default_factory=list)
    reused_active_job: bool = False
    created_job: bool = False


def compare(repo: ComparisonRepository, query: ComparisonQuery) -> ComparisonDataset:
    users = {str(u["user_id"]): u for u in repo.scan_users() if u.get("user_id")}
    selected = [str(u) for u in query.selected_user_ids if str(u) in users]

    # For exclusive mode we need to know who else owns each game.
    excluded_ids: list[str] = []
    libraries_needed = set(selected)
    if query.exclusive:
        excluded_ids = [u for u in users if u not in selected]
        libraries_needed.update(excluded_ids)

    # Aggregate ownership/installed/platforms per release key.
    agg: dict[str, dict] = {}
    for user_id in libraries_needed:
        for entry in repo.get_user_library(user_id):
            rk = entry["release_key"]
            platform = entry.get("platform", rk.split("_")[0])
            if platform in query.exclude_platforms:
                continue
            slot = agg.setdefault(
                rk, {"owners": set(), "installed": set(), "platform": platform}
            )
            slot["owners"].add(user_id)
            if entry.get("installed"):
                slot["installed"].add(user_id)

    metadata = repo.batch_get_games(agg.keys())
    overrides = repo.get_all_metadata()

    games: list[ComparisonItem] = []
    for rk, slot in agg.items():
        meta = metadata.get(rk)
        if meta is None:
            continue  # not yet ingested into the games table
        game = _build_game(rk, slot, meta, overrides)
        games.append(game)

    games = _merge_duplicates(games)
    games = _filter(games, query, selected, excluded_ids)
    games.sort(
        key=SORT_KEYS.get(query.sort.field, SORT_KEYS["title"]),
        reverse=query.sort.direction == "desc",
    )

    # Count unique games, not rows: the grid view can list the same title on
    # more than one row when platform copies have different owners, but those
    # are still one game. Rows are already grouped by slug, so distinct slugs
    # is the unique-game count.
    total = len({g.slug for g in games})

    return ComparisonDataset(items=games, excluded_user_ids=excluded_ids, total=total)


def _build_game(rk: str, slot: dict, meta: dict, overrides: dict) -> ComparisonItem:
    game = ComparisonItem(
        release_key=rk,
        title=meta.get("title", rk),
        slug=meta.get("slug", ""),
        igdb_key=meta.get("igdb_key", rk),
        platforms=[slot["platform"]],
        owners=sorted(slot["owners"]),
        installed=sorted(slot["installed"]),
        max_players=meta.get("max_players", 0),
        multiplayer=meta.get("multiplayer", False),
        rating=meta.get("rating", 0),
        rating_count=meta.get("rating_count", 0),
        enrichment_status=meta.get("enrichment_status"),
    )

    # Apply manual overrides (config metadata in v1) by slug; these win over IGDB.
    override = overrides.get(game.slug)
    if override:
        if "max_players" in override:
            game.max_players = override["max_players"]
            game.multiplayer = _multiplayer(
                override["max_players"],
                meta.get("game_modes", []),
            )
        if override.get("comment"):
            game.comment = override["comment"]
        if override.get("url"):
            game.url = override["url"]
    return game


def _multiplayer(max_players: int, game_modes: list[int]) -> bool:
    if max_players > 1:
        return True
    return any(m in IGDB_MULTIPLAYER_GAME_MODES for m in game_modes)


def _merge_duplicates(games: list[ComparisonItem]) -> list[ComparisonItem]:
    """Merge same-title entries that have identical owners (cross-platform copies).

    Mirrors v1: copies of the same game on different stores held by the same set
    of owners collapse into one row listing all platforms; copies owned by
    different people stay separate.
    """
    grouped: dict[tuple[str, frozenset[str]], ComparisonItem] = {}
    for game in games:
        key = (game.slug, frozenset(game.owners))
        if key not in grouped:
            grouped[key] = replace(game, platforms=list(game.platforms))
            continue
        existing = grouped[key]
        for platform in game.platforms:
            if platform not in existing.platforms:
                existing.platforms.append(platform)
        existing.installed = sorted(set(existing.installed) | set(game.installed))
        existing.max_players = max(existing.max_players, game.max_players)
        existing.multiplayer = existing.multiplayer or game.multiplayer
        existing.rating = max(existing.rating, game.rating)

    for game in grouped.values():
        game.platforms = _sort_platforms(game.platforms)
    return list(grouped.values())


def _sort_platforms(platforms: list[str]) -> list[str]:
    return sorted(
        platforms,
        key=lambda p: PLATFORMS.index(p) if p in PLATFORMS else len(PLATFORMS),
    )


def _filter(
    games: list[ComparisonItem],
    query: ComparisonQuery,
    selected: list[str],
    excluded_ids: list[str],
) -> list[ComparisonItem]:
    selected_set = set(selected)
    excluded_set = set(excluded_ids)
    result = []
    for game in games:
        if not query.include_single_player and not game.multiplayer:
            continue
        if query.scope == "shared":
            owners = set(game.owners)
            if not selected_set.issubset(owners):
                continue
            if query.installed_only and not selected_set.issubset(set(game.installed)):
                continue
            if query.exclusive and owners & excluded_set:
                continue
        result.append(game)
    return result


def ensure_enrichment_job(
    repo: Repository,
    queue: EnrichmentQueue,
    query: ComparisonQuery,
    settings: Settings | None = None,
) -> RefreshAdvice:
    """Ensure the selected libraries have an active refresh job if needed."""
    active = repo.get_active_job()
    if active:
        return RefreshAdvice(
            job_id=active["job_id"],
            reused_active_job=True,
        )

    stale = stale_release_keys(repo, query, settings=settings)
    if not stale:
        return RefreshAdvice(job_id=None, stale_release_keys=[])

    job_id = create_enrichment_job(repo, queue, stale)
    return RefreshAdvice(
        job_id=job_id,
        stale_release_keys=stale,
        created_job=job_id is not None,
    )


def stale_release_keys(
    repo: Repository,
    query: ComparisonQuery,
    settings: Settings | None = None,
) -> list[str]:
    """Return selected-library games whose enrichment is pending or stale."""
    settings = settings or get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.igdb_stale_days)

    release_keys: set[str] = set()
    for user_id in query.selected_user_ids:
        for entry in repo.get_user_library(user_id):
            release_keys.add(entry["release_key"])

    stale: list[str] = []
    for rk, game in repo.batch_get_games(release_keys).items():
        status = game.get("enrichment_status")
        if status in (ENRICHMENT_PENDING, None):
            stale.append(rk)
            continue
        enriched_at = game.get("enriched_at")
        if enriched_at and parse_iso(enriched_at) < cutoff:
            stale.append(rk)
    return stale
