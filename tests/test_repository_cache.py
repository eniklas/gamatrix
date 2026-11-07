"""Tests for the Repository read-model cache.

The comparison read-model (users, games, libraries, metadata overrides) is
cached in-process so repeated filter/sort requests reuse one set of reads. The
cache returns the same object instance on a hit, so identity (`is`) is a precise
probe for "served from cache" vs "freshly read", and writes must invalidate the
affected entry so the cache never serves data this process just changed.
"""

from __future__ import annotations


def test_games_map_caches_until_a_game_is_written(repo):
    repo.put_game({"release_key": "steam_1", "slug": "one", "title": "One"})

    first = repo.get_all_games_map()
    assert repo.get_all_games_map() is first  # cache hit -> same object

    repo.put_game({"release_key": "steam_2", "slug": "two", "title": "Two"})

    second = repo.get_all_games_map()
    assert second is not first  # write invalidated the cache
    assert set(second) == {"steam_1", "steam_2"}


def test_scan_users_caches_until_a_user_is_written(repo):
    repo.put_user({"email": "a@x.com", "username": "A", "user_id": "1"})

    first = repo.scan_users()
    assert repo.scan_users() is first

    repo.put_user({"email": "b@x.com", "username": "B", "user_id": "2"})
    assert repo.scan_users() is not first
    assert {u["email"] for u in repo.scan_users()} == {"a@x.com", "b@x.com"}


def test_user_library_caches_per_user_and_invalidates_on_replace(repo):
    repo.replace_user_library("1", [{"release_key": "steam_1", "installed": False}])

    first = repo.get_user_library("1")
    assert repo.get_user_library("1") is first  # cache hit

    # A different user has its own cache slot and is unaffected.
    repo.replace_user_library("2", [{"release_key": "steam_9", "installed": True}])
    assert repo.get_user_library("1") is first

    repo.replace_user_library(
        "1",
        [
            {"release_key": "steam_1", "installed": False},
            {"release_key": "steam_2", "installed": True},
        ],
    )
    refreshed = repo.get_user_library("1")
    assert refreshed is not first
    assert {row["release_key"] for row in refreshed} == {"steam_1", "steam_2"}


def test_user_library_invalidates_on_clear(repo):
    repo.replace_user_library("1", [{"release_key": "steam_1", "installed": False}])
    assert repo.get_user_library("1")  # populate cache

    repo.clear_user_library("1")
    assert repo.get_user_library("1") == []


def test_metadata_caches_until_an_override_is_written(repo):
    repo.put_metadata({"slug": "one", "max_players": 4})

    first = repo.get_all_metadata()
    assert repo.get_all_metadata() is first

    repo.put_metadata({"slug": "two", "max_players": 2})
    assert repo.get_all_metadata() is not first
    assert set(repo.get_all_metadata()) == {"one", "two"}


def test_metadata_comment_updates_preserve_other_overrides_and_reject_stale_edits(
    repo,
):
    repo.put_metadata(
        {"slug": "one", "max_players": 4, "url": "https://example.com/game"}
    )
    cached = repo.get_all_metadata()

    assert repo.update_metadata_comment("one", "Use the LAN mod", "Alice", 0)
    updated = repo.get_metadata("one")
    assert updated["comment"] == "Use the LAN mod"
    assert updated["comment_updated_by"] == "Alice"
    assert updated["comment_updated_at"]
    assert updated["comment_revision"] == 1
    assert updated["max_players"] == 4
    assert updated["url"] == "https://example.com/game"
    assert repo.get_all_metadata() is not cached

    assert not repo.update_metadata_comment("one", "Stale edit", "Bob", 0)
    assert repo.get_metadata("one")["comment"] == "Use the LAN mod"


def test_metadata_comment_clear_preserves_other_overrides(repo):
    repo.put_metadata(
        {
            "slug": "one",
            "max_players": 4,
            "comment": "Use the LAN mod",
            "comment_updated_at": "2026-01-01T00:00:00+00:00",
            "comment_updated_by": "Alice",
            "comment_revision": 2,
        }
    )

    assert repo.update_metadata_comment("one", None, "Bob", 2)
    updated = repo.get_metadata("one")
    assert "comment" not in updated
    assert "comment_updated_at" not in updated
    assert "comment_updated_by" not in updated
    assert updated["comment_revision"] == 3
    assert updated["max_players"] == 4


def test_ttl_zero_disables_caching(repo):
    repo.settings = repo.settings.model_copy(update={"read_cache_ttl_seconds": 0})
    repo.put_game({"release_key": "steam_1", "slug": "one", "title": "One"})
    # With caching off every read re-scans, so no two calls share identity.
    assert repo.get_all_games_map() is not repo.get_all_games_map()
