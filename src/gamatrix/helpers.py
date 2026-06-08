"""Small utility helpers shared across modules."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone


def get_slug_from_title(title: str) -> str:
    """Predict IGDB's slug for a title; also used as our internal slug.

    IGDB doesn't document the algorithm, but observed slugs follow a standard
    "slugify": lowercase, transliterate accents, drop apostrophes entirely
    (so ``Avallac'h`` -> ``avallach``, not ``avallac-h``), and collapse every
    other run of non-alphanumerics into a single hyphen.

        "Ground Branch"     -> "ground-branch"
        "BioShock Infinite" -> "bioshock-infinite"
        "Pokémon"           -> "pokemon"
    """
    # Strip accents: decompose then drop combining marks (é -> e).
    normalized = unicodedata.normalize("NFKD", title)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_title.lower()
    # Apostrophes vanish rather than becoming separators.
    lowered = lowered.replace("'", "").replace("’", "")
    # Any remaining run of non-alphanumerics becomes a single hyphen.
    slug = re.sub(r"[^a-z0-9]+", "-", lowered)
    return slug.strip("-")


def pic_url(user: dict) -> str | None:
    """Resolve a user's profile-picture URL, or None if they have no pic.

    A user-uploaded pic lives in S3 and is served by the /profile_img route
    (keyed by user_id, with a cache-busting ?v= from the last update). A seeded
    pic is a static file committed under static/profile_img/.
    """
    if user.get("pic_key") and user.get("user_id"):
        return f"/profile_img/{user['user_id']}?v={user.get('pic_updated', '')}"
    if user.get("pic"):
        return f"/static/profile_img/{user['pic']}"
    return None


def now_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string, tolerating a trailing Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
