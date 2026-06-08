"""Tests for display-name validation and the profile-save endpoint."""

from __future__ import annotations

import pytest

from gamatrix import preferences
from gamatrix.constants import DISPLAY_NAME_MAX_LENGTH
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
