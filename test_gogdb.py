"""Tests for GOG db related functions."""

import pathlib
from unittest.mock import patch, mock_open

from helpers import gogdb_helper


class MockedPath:
    def exists(self):
        return True

    def is_file(self):
        return True

    def absolute(self):
        return ""


def mocked_path(path):
    return MockedPath()


@patch("builtins.open", new_callable=mock_open, read_data=b"not long enough")
def test_is_sqlite3_not_enough_data(mocked_file, monkeypatch):

    monkeypatch.setattr(pathlib, "Path", mocked_path)
    result = gogdb_helper.is_sqlite3("/not/a/real/path")
    assert result == False


long_enough_but_wrong_header_values = b"""0123456789
01234567890123456789012345678901234567890123456789
01234567890123456789012345678901234567890123456789
"""


@patch(
    "builtins.open",
    new_callable=mock_open,
    read_data=long_enough_but_wrong_header_values,
)
def test_is_sqlite3_wrong_header_data(mocked_file, monkeypatch):
    monkeypatch.setattr(pathlib, "Path", mocked_path)
    result = gogdb_helper.is_sqlite3("/not/a/real/path")
    assert result == False


good_data = b"""SQLite format 3\000
0123456789
0123456789
0123456789
0123456789
0123456789
0123456789
0123456789
0123456789
0123456789
0123456789
"""


@patch("builtins.open", new_callable=mock_open, read_data=good_data)
def test_is_sqlite3_good_header_data(mocked_file, monkeypatch):
    monkeypatch.setattr(pathlib, "Path", mocked_path)
    result = gogdb_helper.is_sqlite3("/not/a/real/path")
    assert result == True
