"""Tests for small shared helpers."""

import pytest

from gamatrix.helpers import get_slug_from_title


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Ground Branch", "ground-branch"),
        ("BioShock Infinite", "bioshock-infinite"),
        ("Alan Wake 2", "alan-wake-2"),
        # Apostrophes are dropped, not turned into separators.
        ("Avallac'h", "avallach"),
        ("Tom Clancy's Rainbow Six® Siege", "tom-clancys-rainbow-six-siege"),
        # Accents are transliterated.
        ("Pokémon", "pokemon"),
        # Leading/trailing/repeated separators collapse.
        ("  Spaced  Out  ", "spaced-out"),
    ],
)
def test_get_slug_from_title(title, expected):
    assert get_slug_from_title(title) == expected
