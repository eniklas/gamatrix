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
    "argv",
    [
        [
            "./gamatrix-gog.py",  # standard, just left here to simulate actual command line argv list...
            "--config-file",  # use long switch names, more descriptive this way
            "./config-sample.yaml",  # use the sample yaml as a test data source
        ],
        [
            "./gamatrix-gog.py",  # standard, just left here to simulate actual command line argv list...
            "--config-file",  # use long switch names, more descriptive this way
            "./config-sample.yaml",  # use the sample yaml as a test data source
            "--include-single-player",
            "--server",
            "--port=666",
            "--interface=1.22.3.44",
            "--all-games",
        ],
    ],
)
def test_old_vs_new(argv):
    print("=========================================")
    print(argv)
    print("=========================================")

    args = gog.OLD_parse_cmdline(argv[1:])
    print("OLD DONE")
    opts = gog.parse_cmdline(argv[1:])
    print("NEW DONE")

    print("+OLD COMMANDLINE+++++++++++++++++++++++++")
    print(args)

    print("/NEW COMMANDLINE/////////////////////////")
    print(opts)

    og_config = gog.OLD_build_config(args)
    config = gog.build_config(opts)

    fields = ["mode", "port", "interface", "include_single_player", "all_games"]
    for field in fields:
        assert og_config[field] == config[field]

    # with open("old_config.json", "w") as of_:
    #     of_.write(json.dumps(OLD_config))

    # with open("new_config.json", "w") as nf_:
    #     nf_.write(json.dumps(config))


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
    ],
)
def test_cmdline_handling(
    description: str,
    commandline: List[str],
    config_fields: List[str],
    expected_values: List[Any],
):
    """Parse the command line and build the config file, checking for problems."""
    args = gog.OLD_parse_cmdline(commandline)
    config = gog.OLD_build_config(args)
    for i in range(len(config_fields)):
        assert (
            config[config_fields[i]] == expected_values[i]
        ), f"Failure for pass: '{description}'"


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
