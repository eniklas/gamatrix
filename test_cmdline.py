"""Test for command line switches affecting the config.

Currently, these are the values you can affect via the command line:

mode: client | server # default is client, use -s
interface: (valid interface address) # default is 0.0.0.0, use -i
port: (valid port) # default is 8080, use -p
include_single_player: True | False # use -I
all_games: True | False # use -a
"""

from typing import Any, List

import pytest

gog = __import__("gamatrix-gog")


@pytest.mark.parametrize(
    "description,commandline,config_fields,expected_values",
    [
        [
            "No switches",  # Description, should this test pass fail.
            [
                "./gamatrix-gog.py",  # standard, just left here to simulate actual command line argv list...
                "--config-file",  # use long switch names, more descriptive this way
                "./config-sample.yaml",  # use the sample yaml as a test data source
            ],
            [
                "mode",  # names of the top-level field in the config file, in this case mode
                "interface",
                "port",
                "include_single_player",
                "all_games",
            ],
            [
                "server",  # values that are expected, this list is arranged to coincide with fields in the same order as the list above
                "0.0.0.0",
                8080,
                False,
                False,
            ],
        ],
        [
            "Assorted values all in one",
            [
                "./gamatrix-gog.py",  # just here to simulate actual command line argv list...
                "--config-file",
                "./config-sample.yaml",
                "--server",
                "--interface",
                "1.2.3.4",
                "--port",
                "62500",
                "--include-single-player",
                "--all-games",
            ],
            ["mode", "interface", "port", "include_single_player", "all_games"],
            [
                "server",
                "1.2.3.4",
                62500,
                True,
                True,
            ],
        ],
        [
            "Only set the mode to server",
            [
                "./gamatrix-gog.py",
                "--config-file",
                "./config-sample.yaml",
                "--server",
            ],
            ["mode"],
            ["server"],
        ],
        [
            "Allow the cache to update missing items.",
            [
                "./gamatrix-gog.py",
                "--config-file",
                "./config-sample.yaml",
                "--update-cache",
            ],
            ["mode", "update_cache"],
            ["server", True],
        ],
    ],
)
def test_new_cmdline_handling(
    description: str,
    commandline: List[str],
    config_fields: List[str],
    expected_values: List[Any],
):
    """Parse the command line and build the config file, checking for problems."""
    args = gog.parse_cmdline(commandline[1:])
    config = gog.build_config(args)
    for i in range(len(config_fields)):
        assert (
            config[config_fields[i]] == expected_values[i]
        ), f"Failure for pass: '{description}'"
