"""Tests for the games comparison service."""

from __future__ import annotations

import pytest

from gamatrix.constants import JOB_RUNNING
from gamatrix.games.service import (
    ComparisonQuery,
    SortSpec,
    compare,
    ensure_enrichment_job,
)
from gamatrix.helpers import now_iso
from gamatrix.storage.queue import EnrichmentQueue


@pytest.fixture
def populated(repo):
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})
    repo.put_user({"email": "b@x.com", "username": "B", "user_id": "2"})

    def game(rk, title, slug, mp, maxp, rating=0):
        repo.put_game(
            {
                "release_key": rk,
                "title": title,
                "slug": slug,
                "igdb_key": rk,
                "platform": rk.split("_")[0],
                "multiplayer": mp,
                "max_players": maxp,
                "rating": rating,
                "game_modes": [],
                "enrichment_status": "done",
                "enriched_at": now_iso(),
            }
        )

    game("steam_10", "Coop Game", "coopgame", True, 4, rating=90)
    game("steam_11", "Solo Game", "sologame", False, 1)
    game("gog_12", "Shared MP", "sharedmp", True, 8, rating=75)

    repo.replace_user_library(
        "1",
        [
            {"release_key": "steam_10", "platform": "steam", "installed": True},
            {"release_key": "steam_11", "platform": "steam", "installed": False},
            {"release_key": "gog_12", "platform": "gog", "installed": False},
        ],
    )
    repo.replace_user_library(
        "2",
        [
            {"release_key": "steam_10", "platform": "steam", "installed": True},
            {"release_key": "gog_12", "platform": "gog", "installed": True},
        ],
    )
    return repo


def test_common_multiplayer_games(populated):
    result = compare(populated, ComparisonQuery(selected_user_ids=["1", "2"]))
    titles = {g.title for g in result.items}
    assert titles == {"Coop Game", "Shared MP"}


def test_include_single_player_single_user(populated):
    result = compare(
        populated,
        ComparisonQuery(selected_user_ids=["1"], include_single_player=True),
    )
    titles = {g.title for g in result.items}
    assert titles == {"Coop Game", "Solo Game", "Shared MP"}


def test_installed_only(populated):
    result = compare(
        populated,
        ComparisonQuery(selected_user_ids=["1"], installed_only=True),
    )
    # Only steam_10 is installed by user 1 (and it's multiplayer).
    titles = {g.title for g in result.items}
    assert titles == {"Coop Game"}


def test_exclude_platform(populated):
    result = compare(
        populated,
        ComparisonQuery(selected_user_ids=["1", "2"], exclude_platforms=["gog"]),
    )
    titles = {g.title for g in result.items}
    assert titles == {"Coop Game"}


def test_sort_by_rating_desc(populated):
    result = compare(
        populated,
        ComparisonQuery(
            selected_user_ids=["1", "2"],
            sort=SortSpec(field="rating", direction="desc"),
        ),
    )
    ratings = [g.rating for g in result.items]
    assert ratings == sorted(ratings, reverse=True)


def test_metadata_override_applied(populated):
    populated.put_metadata(
        {"slug": "coopgame", "max_players": 2, "comment": "Use Hamachi"}
    )
    result = compare(populated, ComparisonQuery(selected_user_ids=["1", "2"]))
    coop = next(g for g in result.items if g.slug == "coopgame")
    assert coop.max_players == 2
    assert coop.comment == "Use Hamachi"


def test_merge_cross_platform_duplicates(repo):
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})
    for rk, platform in [("steam_30", "steam"), ("gog_31", "gog")]:
        repo.put_game(
            {
                "release_key": rk,
                "title": "Dup Game",
                "slug": "dupgame",
                "igdb_key": rk,
                "platform": platform,
                "multiplayer": True,
                "max_players": 4,
                "rating": 0,
                "game_modes": [],
                "enrichment_status": "done",
            }
        )
    repo.replace_user_library(
        "1",
        [
            {"release_key": "steam_30", "platform": "steam", "installed": False},
            {"release_key": "gog_31", "platform": "gog", "installed": False},
        ],
    )
    result = compare(repo, ComparisonQuery(selected_user_ids=["1"]))
    assert len(result.items) == 1
    assert result.items[0].platforms == ["steam", "gog"]


def test_merge_cross_platform_duplicates_union_installed_and_keep_best_metadata(repo):
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})
    repo.put_user({"email": "b@x.com", "username": "B", "user_id": "2"})
    for rk, platform, multiplayer, max_players, rating in [
        ("epic_50", "epic", False, 1, 40),
        ("steam_51", "steam", True, 4, 90),
        ("gog_52", "gog", True, 8, 75),
    ]:
        repo.put_game(
            {
                "release_key": rk,
                "title": "Merged Game",
                "slug": "mergedgame",
                "igdb_key": rk,
                "platform": platform,
                "multiplayer": multiplayer,
                "max_players": max_players,
                "rating": rating,
                "game_modes": [],
                "enrichment_status": "done",
            }
        )
    repo.replace_user_library(
        "1",
        [
            {"release_key": "epic_50", "platform": "epic", "installed": False},
            {"release_key": "steam_51", "platform": "steam", "installed": True},
            {"release_key": "gog_52", "platform": "gog", "installed": False},
        ],
    )
    repo.replace_user_library(
        "2",
        [
            {"release_key": "epic_50", "platform": "epic", "installed": False},
            {"release_key": "steam_51", "platform": "steam", "installed": False},
            {"release_key": "gog_52", "platform": "gog", "installed": True},
        ],
    )

    result = compare(repo, ComparisonQuery(selected_user_ids=["1", "2"]))

    assert len(result.items) == 1
    merged = result.items[0]
    assert merged.platforms == ["steam", "gog", "epic"]
    assert merged.installed == ["1", "2"]
    assert merged.max_players == 8
    assert merged.multiplayer is True
    assert merged.rating == 90


def test_same_slug_merges_only_within_matching_owner_groups(repo):
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})
    repo.put_user({"email": "b@x.com", "username": "B", "user_id": "2"})
    for rk, platform in [
        ("steam_60", "steam"),
        ("gog_61", "gog"),
        ("epic_62", "epic"),
    ]:
        repo.put_game(
            {
                "release_key": rk,
                "title": "Grouped Game",
                "slug": "groupedgame",
                "igdb_key": rk,
                "platform": platform,
                "multiplayer": True,
                "max_players": 4,
                "rating": 0,
                "game_modes": [],
                "enrichment_status": "done",
            }
        )
    repo.replace_user_library(
        "1",
        [
            {"release_key": "steam_60", "platform": "steam", "installed": False},
            {"release_key": "gog_61", "platform": "gog", "installed": False},
        ],
    )
    repo.replace_user_library(
        "2", [{"release_key": "epic_62", "platform": "epic", "installed": False}]
    )

    result = compare(
        repo,
        ComparisonQuery(selected_user_ids=["1", "2"], scope="owned"),
    )

    assert result.total == 1
    rows = {(tuple(game.owners), tuple(game.platforms)) for game in result.items}
    assert rows == {
        (("1",), ("steam", "gog")),
        (("2",), ("epic",)),
    }


def test_count_is_unique_games_not_rows(repo):
    """The header count is unique games, not table rows.

    In the grid view the same title can occupy two rows when platform copies
    have different owners (so they don't merge); that's still one game.
    """
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})
    repo.put_user({"email": "b@x.com", "username": "B", "user_id": "2"})
    for rk, platform in [("steam_40", "steam"), ("gog_41", "gog")]:
        repo.put_game(
            {
                "release_key": rk,
                "title": "Split Game",
                "slug": "splitgame",
                "igdb_key": rk,
                "platform": platform,
                "multiplayer": True,
                "max_players": 4,
                "rating": 0,
                "game_modes": [],
                "enrichment_status": "done",
            }
        )
    # User 1 owns it on Steam, user 2 on GOG: different owner sets keep the two
    # rows separate rather than merging into one.
    repo.replace_user_library(
        "1", [{"release_key": "steam_40", "platform": "steam", "installed": False}]
    )
    repo.replace_user_library(
        "2", [{"release_key": "gog_41", "platform": "gog", "installed": False}]
    )
    result = compare(
        repo,
        ComparisonQuery(selected_user_ids=["1", "2"], scope="owned"),
    )
    assert len(result.items) == 2  # two rows
    assert result.total == 1  # but one unique game


def test_owned_scope_lists_all_selected_users_games(populated):
    result = compare(
        populated,
        ComparisonQuery(
            selected_user_ids=["1", "2"],
            scope="owned",
            include_single_player=True,
        ),
    )
    titles = {g.title for g in result.items}
    assert titles == {"Coop Game", "Solo Game", "Shared MP"}


def test_ensure_enrichment_job_reuses_active_job(repo, settings):
    repo.put_job(
        {
            "job_id": "live-job",
            "status": JOB_RUNNING,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "completed_at": None,
            "release_keys": ["steam_10"],
            "total": 1,
            "completed_count": 0,
        }
    )

    job_id = ensure_enrichment_job(
        repo,
        EnrichmentQueue(settings=settings),
        ComparisonQuery(selected_user_ids=["1"]),
        settings=settings,
    )

    assert job_id == "live-job"


def test_ensure_enrichment_job_creates_job_for_stale_selected_games(repo, settings):
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})
    repo.put_game(
        {
            "release_key": "steam_10",
            "title": "Needs Refresh",
            "slug": "needs-refresh",
            "igdb_key": "steam_10",
            "platform": "steam",
            "multiplayer": True,
            "max_players": 4,
            "rating": 0,
            "game_modes": [],
            "enrichment_status": "pending",
            "enriched_at": None,
        }
    )
    repo.replace_user_library(
        "1", [{"release_key": "steam_10", "platform": "steam", "installed": False}]
    )

    job_id = ensure_enrichment_job(
        repo,
        EnrichmentQueue(settings=settings),
        ComparisonQuery(selected_user_ids=["1"]),
        settings=settings,
    )

    assert job_id is not None
    job = repo.get_job(job_id)
    assert job is not None
    assert job["release_keys"] == ["steam_10"]
    assert job["total"] == 1


def test_ensure_enrichment_job_returns_none_when_selected_games_are_fresh(
    repo, settings
):
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})
    repo.put_game(
        {
            "release_key": "steam_10",
            "title": "Fresh Game",
            "slug": "fresh-game",
            "igdb_key": "steam_10",
            "platform": "steam",
            "multiplayer": True,
            "max_players": 4,
            "rating": 0,
            "game_modes": [],
            "enrichment_status": "done",
            "enriched_at": now_iso(),
        }
    )
    repo.replace_user_library(
        "1", [{"release_key": "steam_10", "platform": "steam", "installed": False}]
    )

    job_id = ensure_enrichment_job(
        repo,
        EnrichmentQueue(settings=settings),
        ComparisonQuery(selected_user_ids=["1"]),
        settings=settings,
    )

    assert job_id is None
    assert repo.get_active_job() is None
