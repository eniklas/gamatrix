"""Tests for IGDB helpers and the improved matching fallbacks."""

from __future__ import annotations

from gamatrix.igdb.client import (
    IGDBClient,
    _is_multiplayer,
    _max_players,
    _strip_edition,
)


def test_max_players_takes_highest_across_platforms():
    modes = [
        {"onlinemax": 4, "offlinemax": 2},
        {"onlinecoopmax": 8},
        {"offlinecoopmax": 1},
    ]
    assert _max_players(modes) == 8


def test_max_players_empty():
    assert _max_players([]) == 0


def test_is_multiplayer_by_max_players():
    assert _is_multiplayer(4, [])
    assert not _is_multiplayer(1, [])


def test_is_multiplayer_by_game_mode():
    # 5 == mmo, which is in IGDB_MULTIPLAYER_GAME_MODES
    assert _is_multiplayer(0, [5])
    # 1 == singleplayer only
    assert not _is_multiplayer(0, [1])


def test_strip_edition():
    assert _strip_edition("Foo: Bar Edition") == "Foo"
    assert _strip_edition("Foo - Deluxe") == "Foo"
    assert _strip_edition("Plain Title") == "Plain Title"


class _FakeClient(IGDBClient):
    """IGDBClient with _query stubbed to return canned responses by endpoint."""

    def __init__(self, responses):
        self._responses = responses
        self.access_token = "fake"
        self.calls = []

    async def _query(self, endpoint, body):  # type: ignore[override]
        self.calls.append((endpoint, body))
        return self._responses.get(endpoint, [])


async def test_resolve_by_external_uid():
    client = _FakeClient({"external_games": [{"game": 42}]})
    assert await client.resolve_igdb_id("steam_1", "Alpha") == 42


async def test_resolve_falls_back_to_slug():
    client = _FakeClient({"external_games": [], "games": [{"id": 99}]})
    # slug query returns id 99
    assert await client.resolve_igdb_id("gog_2", "Beta") == 99


async def test_resolve_fuzzy_search_match():
    # No external match, no exact slug; the /search fallback returns a close name.
    class FuzzyClient(_FakeClient):
        async def _query(self, endpoint, body):
            self.calls.append((endpoint, body))
            if endpoint == "external_games":
                return []
            if "where slug" in body:
                return []
            if "search" in body:
                return [{"id": 7, "name": "The Witcher 3 Wild Hunt"}]
            return []

    client = FuzzyClient({})
    result = await client.resolve_igdb_id("gog_3", "The Witcher 3: Wild Hunt")
    assert result == 7


async def test_resolve_returns_zero_when_nothing_matches():
    client = _FakeClient({"external_games": [], "games": []})
    assert await client.resolve_igdb_id("gog_4", "Totally Unknown Game XYZ") == 0
