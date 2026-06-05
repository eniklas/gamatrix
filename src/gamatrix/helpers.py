"""Small utility helpers shared across modules."""

from __future__ import annotations

import re
from datetime import datetime, timezone


def get_slug_from_title(title: str) -> str:
    """Normalize a title to lowercase alphanumeric for fuzzy matching.

    Mirrors v1 behavior so existing config/metadata slugs keep matching.
    """
    return re.sub(r"[^a-z0-9]", "", title.lower())


def now_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string, tolerating a trailing Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
