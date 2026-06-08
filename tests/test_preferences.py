"""Tests for display-name validation and the profile-save endpoint."""

from __future__ import annotations

import pytest

from gamatrix import preferences
from gamatrix.constants import DISPLAY_NAME_MAX_LENGTH


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
