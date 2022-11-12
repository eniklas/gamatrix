"""Tests for GOG db related functions."""

from gamatrix_gog.helpers import gogdb_helper


def test_is_sqlite3_not_enough_data():
    assert not gogdb_helper.is_sqlite3(b"not long enough")


long_enough_but_wrong_header_values = b"""0123456789
01234567890123456789012345678901234567890123456789
01234567890123456789012345678901234567890123456789
"""


def test_is_sqlite3_wrong_header_data():
    assert not gogdb_helper.is_sqlite3(long_enough_but_wrong_header_values)


good_data = b"""SQLite format 3\000
0123456789
0123456789
0123456789
0123456789
0123456789
0123456789
0123456789
"""


def test_is_sqlite3_good_header_data():
    assert gogdb_helper.is_sqlite3(good_data)
