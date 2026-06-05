"""User preference defaults and merge logic."""

from __future__ import annotations

from typing import Any

DEFAULT_PREFERENCES: dict[str, Any] = {
    "include_single_player": False,
    "installed_only": False,
    "exclude_platforms": [],
    "default_view": "list",
    "selected_users": "all",
    "exclusive": False,
}


def merge_preferences(stored: dict | None) -> dict:
    """Overlay stored preferences on the defaults."""
    merged = dict(DEFAULT_PREFERENCES)
    if stored:
        merged.update({k: v for k, v in stored.items() if k in DEFAULT_PREFERENCES})
    return merged
