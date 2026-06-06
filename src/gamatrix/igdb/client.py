"""Async IGDB API client.

Replaces v1's synchronous requests-based helper. Authenticates against the
Twitch OAuth endpoint, resolves release keys to IGDB ids, and fetches game
info, ratings, and multiplayer details. A shared async rate limiter keeps us
under IGDB's 4 requests/second limit.

Matching improvements over v1 (design COULD item):
  1. external_games lookup by uid (Steam/GOG), as before
  2. exact slug match, as before
  3. NEW: fuzzy search fallback using IGDB's /search endpoint + rapidfuzz, and
     a retry with edition/subtitle stripped from the title
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx
from rapidfuzz import fuzz

from gamatrix.constants import (
    IGDB_API_CALL_DELAY,
    IGDB_GAME_MODE,
    IGDB_MAX_PLAYER_KEYS,
    IGDB_MULTIPLAYER_GAME_MODES,
    IGDB_PLATFORM_ID,
)
from gamatrix.helpers import get_slug_from_title

log = logging.getLogger(__name__)

# Minimum rapidfuzz token-sort ratio for a search hit to be accepted.
FUZZY_MATCH_THRESHOLD = 85.0


@dataclass
class GameMetadata:
    """IGDB-derived metadata for one release key."""

    igdb_id: int = 0
    name: str | None = None
    game_modes: list[int] = field(default_factory=list)
    max_players: int = 0
    multiplayer: bool = False
    rating: int = 0
    rating_count: int = 0
    found: bool = False


class _RateLimiter:
    """Serializes calls and enforces a minimum delay between them."""

    def __init__(self, delay: float):
        self._delay = delay
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < self._delay:
                await asyncio.sleep(self._delay - elapsed)
            self._last = time.monotonic()

    def push_out(self, extra: float) -> None:
        """Defer the next permitted call by `extra` seconds beyond now."""
        self._last = max(self._last, time.monotonic()) + extra


class IGDBClient:
    TOKEN_URL = "https://id.twitch.tv/oauth2/token"
    API_BASE = "https://api.igdb.com/v4"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token: str | None = None
        self._http = httpx.AsyncClient(timeout=30.0)
        self._limiter = _RateLimiter(IGDB_API_CALL_DELAY)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "IGDBClient":
        await self.authenticate()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def authenticate(self) -> None:
        resp = await self._http.post(
            self.TOKEN_URL,
            params={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        self.access_token = resp.json()["access_token"]
        log.info("Obtained IGDB access token")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

    async def _query(self, endpoint: str, body: str) -> list[dict]:
        """POST an Apicalypse query, refreshing the token once on 401."""
        if self.access_token is None:
            await self.authenticate()
        for attempt in range(6):
            await self._limiter.wait()
            resp = await self._http.post(
                f"{self.API_BASE}/{endpoint}", headers=self._headers, content=body
            )
            if resp.status_code == 401 and attempt == 0:
                log.info("IGDB 401; refreshing token")
                await self.authenticate()
                continue
            if resp.status_code == 429:
                backoff = min(10 * (2 ** attempt), 120)
                log.warning("IGDB rate limit hit; backing off %ds (attempt %d)", backoff, attempt)
                self._limiter.push_out(backoff)
                continue
            resp.raise_for_status()
            return resp.json()
        log.error("IGDB: exhausted retries for %s", endpoint)
        return []

    # ------------------------------------------------------------------
    # ID resolution
    # ------------------------------------------------------------------
    async def resolve_igdb_id(self, igdb_key: str, title: str) -> int:
        """Resolve a release key to an IGDB game id, 0 if not found."""
        igdb_id = await self._id_by_external_uid(igdb_key)
        if igdb_id:
            return igdb_id

        slug = get_slug_from_title(title)
        igdb_id = await self._id_by_slug(slug)
        if igdb_id:
            return igdb_id

        # NEW fuzzy fallbacks.
        igdb_id = await self._id_by_search(title)
        if igdb_id:
            return igdb_id

        stripped = _strip_edition(title)
        if stripped != title:
            igdb_id = await self._id_by_search(stripped)
            if igdb_id:
                return igdb_id

        log.debug("%s (%s): no IGDB match", igdb_key, title)
        return 0

    async def _id_by_external_uid(self, igdb_key: str) -> int:
        platform, _, platform_key = igdb_key.partition("_")
        body = f'fields game; where uid = "{platform_key}"'
        if platform in IGDB_PLATFORM_ID:
            body += f" & category = {IGDB_PLATFORM_ID[platform]}"
        body += ";"
        result = await self._query("external_games", body)
        return result[0]["game"] if result else 0

    async def _id_by_slug(self, slug: str) -> int:
        result = await self._query("games", f'fields id; where slug = "{slug}";')
        return result[0]["id"] if result else 0

    async def _id_by_search(self, title: str) -> int:
        safe = title.replace('"', "")
        result = await self._query(
            "games", f'search "{safe}"; fields id,name; limit 5;'
        )
        best_id, best_score = 0, 0.0
        for candidate in result:
            score = fuzz.token_sort_ratio(title.lower(), candidate["name"].lower())
            if score > best_score:
                best_id, best_score = candidate["id"], score
        if best_score >= FUZZY_MATCH_THRESHOLD:
            log.debug("Fuzzy matched '%s' -> id %s (%.0f)", title, best_id, best_score)
            return best_id
        return 0

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    async def fetch_metadata(self, igdb_key: str, title: str) -> GameMetadata:
        meta = GameMetadata()
        igdb_id = await self.resolve_igdb_id(igdb_key, title)
        if not igdb_id:
            return meta

        meta.igdb_id = igdb_id
        meta.found = True

        info = await self._query(
            "games",
            "fields game_modes,name,slug,rating,rating_count; "
            f"where id = {igdb_id};",
        )
        if info:
            game = info[0]
            meta.name = game.get("name")
            meta.game_modes = game.get("game_modes", [])
            if "rating" in game:
                meta.rating = round(game["rating"])
            meta.rating_count = game.get("rating_count", 0)

        modes = await self._query(
            "multiplayer_modes", f"fields *; where game = {igdb_id};"
        )
        meta.max_players = _max_players(modes)
        meta.multiplayer = _is_multiplayer(meta.max_players, meta.game_modes)
        # If IGDB only knows single player, encode that as a max of 1.
        if meta.max_players == 0 and meta.game_modes == [
            IGDB_GAME_MODE["singleplayer"]
        ]:
            meta.max_players = 1
        return meta


def _max_players(multiplayer_modes: list[dict]) -> int:
    """Highest of the various max-player keys across all platform entries."""
    best = 0
    for platform in multiplayer_modes:
        for key in IGDB_MAX_PLAYER_KEYS:
            if key in platform and platform[key] > best:
                best = platform[key]
    return best


def _is_multiplayer(max_players: int, game_modes: list[int]) -> bool:
    if max_players > 1:
        return True
    return any(mode in IGDB_MULTIPLAYER_GAME_MODES for mode in game_modes)


def _strip_edition(title: str) -> str:
    """Drop a trailing edition/subtitle so 'Foo: Bar Edition' retries as 'Foo'."""
    for sep in (":", " - ", " – "):
        if sep in title:
            return title.split(sep)[0].strip()
    return title
