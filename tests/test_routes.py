"""Tests for request-option parsing in the games routes.

Focus on how `_parse_options` resolves boolean filter checkboxes. An unchecked
HTML checkbox submits no value, so the filter form sends a hidden
`filters_active` marker; when present, an absent flag means "off" rather than
falling back to the user's saved preference.
"""

from __future__ import annotations

import types

from starlette.datastructures import QueryParams

from gamatrix.games import routes


def _opts(query_string: str, preferences: dict):
    request = types.SimpleNamespace(query_params=QueryParams(query_string))
    return routes._parse_options(request, {"preferences": preferences})


def test_unchecked_box_overrides_saved_on_preference():
    """Submitting the form with the box unchecked turns the option off even when
    the saved preference is on (the reported single-player bug)."""
    prefs = {"include_single_player": True, "selected_users": ["1"]}
    opts = _opts("user=1&filters_active=1", prefs)
    assert opts.include_single_player is False


def test_checked_box_turns_option_on():
    prefs = {"include_single_player": False, "selected_users": ["1"]}
    opts = _opts("user=1&filters_active=1&single_player=true", prefs)
    assert opts.include_single_player is True


def test_bare_page_load_uses_saved_preference():
    """A bare /games load (no filter form submitted) still honors saved prefs."""
    prefs = {"include_single_player": True, "selected_users": ["1"]}
    opts = _opts("user=1", prefs)
    assert opts.include_single_player is True


def test_marker_applies_to_all_boolean_flags():
    """All boolean filter checkboxes share the same resolution, so an absent one
    on a submitted form is off regardless of saved prefs."""
    prefs = {
        "include_single_player": True,
        "installed_only": True,
        "exclusive": True,
        "selected_users": ["1"],
    }
    opts = _opts("user=1&filters_active=1", prefs)
    assert opts.include_single_player is False
    assert opts.installed_only is False
    assert opts.exclusive is False


def test_emptied_exclude_list_clears_saved_pref():
    """Submitting with every platform-exclude box unchecked clears the saved
    exclude list instead of falling back to it."""
    prefs = {"exclude_platforms": ["gog", "epic"], "selected_users": ["1"]}
    opts = _opts("user=1&filters_active=1", prefs)
    assert opts.exclude_platforms == []


def test_partial_exclude_list_wins_over_saved_pref():
    prefs = {"exclude_platforms": ["gog", "epic"], "selected_users": ["1"]}
    opts = _opts("user=1&filters_active=1&exclude=gog", prefs)
    assert opts.exclude_platforms == ["gog"]


def test_bare_load_uses_saved_exclude_list():
    prefs = {"exclude_platforms": ["gog", "epic"], "selected_users": ["1"]}
    opts = _opts("user=1", prefs)
    assert opts.exclude_platforms == ["gog", "epic"]


def test_deselecting_all_users_shows_none_on_submit():
    """Submitting with no user boxes checked yields an empty selection rather
    than falling back to the saved users (and without hitting the repo)."""
    prefs = {"selected_users": ["1", "2"], "exclude_platforms": []}
    opts = _opts("filters_active=1", prefs)
    assert opts.selected_user_ids == []


def test_bare_load_uses_saved_user_selection():
    prefs = {"selected_users": ["1", "2"], "exclude_platforms": []}
    opts = _opts("", prefs)
    assert opts.selected_user_ids == ["1", "2"]
