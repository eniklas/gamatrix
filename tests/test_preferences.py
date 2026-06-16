"""Tests for display-name validation and the profile-save endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from gamatrix import preferences
from gamatrix.app import app
from gamatrix.auth.dependencies import current_user, current_user_api, get_repo
from gamatrix.constants import DISPLAY_NAME_MAX_LENGTH
from gamatrix.games.preferences import merge_preferences
from gamatrix.helpers import pic_url


def test_clean_display_name_trims_and_collapses_whitespace():
    assert preferences.clean_display_name("  Erik   Niklas  ") == "Erik Niklas"


def test_clean_display_name_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        preferences.clean_display_name("   ")


def test_clean_display_name_rejects_too_long():
    with pytest.raises(ValueError, match="characters or fewer"):
        preferences.clean_display_name("x" * (DISPLAY_NAME_MAX_LENGTH + 1))


def test_save_profile_persists_username(repo):
    repo.put_user({"email": "user@x.com", "username": "Old", "user_id": "1"})
    name = preferences.clean_display_name(" New ")
    repo.update_user("user@x.com", {"username": name})
    assert repo.get_user("user@x.com")["username"] == "New"


def test_pic_url_prefers_uploaded_pic_with_cache_buster():
    user = {"user_id": "7", "pic_updated": 123}
    assert pic_url(user) == "/profile_img/7?v=123"


def test_profile_pic_round_trips_through_dynamo(repo):
    repo.put_profile_pic("7", b"\x89PNG-bytes")
    assert repo.get_profile_pic("7") == b"\x89PNG-bytes"


def test_get_profile_pic_missing_returns_none(repo):
    assert repo.get_profile_pic("nobody") is None


def test_pic_url_falls_back_to_static_seeded_pic():
    user = {"user_id": "7", "pic": "Kane.png"}
    assert pic_url(user) == "/static/profile_img/Kane.png"


def test_pic_url_none_when_no_pic():
    assert pic_url({"user_id": "7"}) is None


def test_invalid_stored_display_mode_falls_back_to_system_preference():
    assert merge_preferences({"display_mode": "<script>"})["display_mode"] is None


def test_save_preferences_persists_mode(repo):
    user = {
        "email": "user@x.com",
        "username": "User",
        "user_id": "1",
        "preferences": {"display_mode": None},
    }
    repo.put_user(user)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user_api] = lambda: user
    try:
        response = TestClient(app).post(
            "/preferences",
            data={"display_mode": "high-contrast", "user": "1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert repo.get_user("user@x.com")["preferences"]["display_mode"] == "high-contrast"


def test_save_preferences_persists_system_mode_as_none(repo):
    user = {
        "email": "user@x.com",
        "username": "User",
        "user_id": "1",
        "preferences": {"display_mode": "dark"},
    }
    repo.put_user(user)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user_api] = lambda: user
    try:
        response = TestClient(app).post(
            "/preferences",
            data={"display_mode": "system", "user": "1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    assert repo.get_user("user@x.com")["preferences"]["display_mode"] is None


def test_filter_auto_save_preserves_display_mode(repo):
    user = {
        "email": "user@x.com",
        "username": "User",
        "user_id": "1",
        "preferences": {"display_mode": "color-blind"},
    }
    repo.put_user(user)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user_api] = lambda: user
    try:
        response = TestClient(app).post("/preferences?user=1&single_player=true")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    stored = repo.get_user("user@x.com")["preferences"]
    assert stored["display_mode"] == "color-blind"


def test_preferences_form_selects_system_setting_for_none(repo):
    user = {
        "email": "user@x.com",
        "username": "User",
        "user_id": "1",
        "preferences": {"display_mode": None},
    }
    repo.put_user(user)
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[current_user] = lambda: user
    try:
        response = TestClient(app).get("/preferences")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert '<select name="display_mode">' in response.text
    assert '<option value="system" selected>System Setting</option>' in response.text
    assert '<button type="button" onclick="applyDisplayMode()">Apply</button>' in response.text
    assert "function applyDisplayMode()" in response.text
    assert "Save preferences to keep change" in response.text
    assert "s.textContent='Saved ✓'" in response.text
