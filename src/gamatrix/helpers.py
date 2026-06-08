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


def now_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string, tolerating a trailing Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
