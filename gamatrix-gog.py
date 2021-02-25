#!/usr/bin/env python3
import argparse
import logging
import os
import sys
from typing import Any, List

from flask import Flask, render_template, request
from ipaddress import IPv4Network
from ruamel.yaml import YAML

from helpers.cache_helper import Cache
from helpers.constants import (
    ALPHANUM_PATTERN,
    IGDB_GAME_MODE,
    IGDB_MULTIPLAYER_GAME_MODES,
)
from helpers.gogdb_helper import gogDB
from helpers.igdb_helper import IGDBHelper
from helpers.network_helper import check_ip_is_authorized
from version import VERSION

app = Flask(__name__)


@app.route("/")
def root():
    check_ip_is_authorized(request.remote_addr, config["allowed_cidrs"])

    return render_template(
        "index.html",
        users=config["users"],
        platforms=["epic", "gog", "origin", "steam", "uplay", "xboxone"],
        version=VERSION,
    )


@app.route("/compare", methods=["GET", "POST"])
def compare_libraries():
    check_ip_is_authorized(request.remote_addr, config["allowed_cidrs"])
    opts = init_opts()

    # Check boxes get passed in as "on" if checked, or not at all if unchecked
    for k in request.args.keys():
        # Only user IDs are ints
        try:
            i = int(k)
            opts["user_ids_to_compare"][i] = config["users"][i]
        except ValueError:
            if k.startswith("exclude_platform_"):
                opts["exclude_platforms"].append(k.split("_")[-1])
            else:
                opts[k] = True

    # If no users were selected, just refresh the page
    if not opts["user_ids_to_compare"]:
        return root()

    gog = gogDB(config, opts)

    if request.args["option"] == "grid":
        gog.config["all_games"] = True
        template = "game_grid.html"
    else:
        template = "game_list.html"

    common_games = gog.get_common_games()

    for release_key in list(common_games.keys()):
        igdb.get_igdb_id(release_key)
        igdb.get_game_info(release_key)
        igdb.get_multiplayer_info(release_key)

    cache.save()
    set_multiplayer_status(common_games, cache.data)
    common_games = gog.merge_duplicate_titles(common_games)

    if not gog.config["all_games"]:
        common_games = gog.filter_games(common_games)

    log.debug(f'user_ids_to_compare = {opts["user_ids_to_compare"]}')

    debug_str = ""
    return render_template(
        template,
        debug_str=debug_str,
        games=common_games,
        users=opts["user_ids_to_compare"],
        caption=gog.get_caption(len(common_games)),
        show_keys=opts["show_keys"],
    )


def init_opts():
    """Initializes the options to pass to the gogDB class. Since the
    config is only read once, we need to be able to reinit any options
    that can be passed from the web UI
    """

    return {
        "include_single_player": False,
        "exclusive": False,
        "show_keys": False,
        "user_ids_to_compare": {},
        "exclude_platforms": [],
    }


def build_config(args):
    """Returns a config dict created from the config file and
    command-line arguments, with the latter taking precedence
    """
    if args.version:
        print("{} version {}".format(os.path.basename(__file__), VERSION))
        sys.exit(0)

    if args.config_file:
        yaml = YAML(typ="safe")
        with open(args.config_file) as config_file:
            config = yaml.load(config_file)
    else:
        # We didn't get a config file, so populate from args
        config = {}

    # TODO: allow using both IDs and DBs (use one arg and detect if it's an int)
    # TODO: should be able to use unambiguous partial names
    if not args.db and "users" not in config:
        raise ValueError("You must use -u, have users in the config file, or list DBs")

    # Command-line args override values in the config file
    # TODO: maybe we can do this directly in argparse, or otherwise better

    # This can't be given as an argument as it wouldn't make much sense;
    #  provide a sane default if it's missing from the config file
    if "db_path" not in config:
        config["db_path"] = "."

    config["all_games"] = False
    if args.all_games:
        config["all_games"] = True

    config["include_single_player"] = False
    if args.include_single_player:
        config["include_single_player"] = True

    if args.server:
        config["mode"] = "server"

    if args.interface:
        config["interface"] = args.interface
    if "interface" not in config:
        config["interface"] = "0.0.0.0"

    if args.port:
        config["port"] = args.port
    if "port" not in config:
        config["port"] = 8080

    # Convert allowed CIDRs into IPv4Network objects
    cidrs = []
    if "allowed_cidrs" in config:
        for cidr in config["allowed_cidrs"]:
            cidrs.append(IPv4Network(cidr))
    config["allowed_cidrs"] = cidrs

    # DBs and user IDs can be in the config file and/or passed in as args
    config["db_list"] = []
    if "users" not in config:
        config["users"] = {}

    for userid in config["users"]:
        config["db_list"].append(
            "{}/{}".format(config["db_path"], config["users"][userid]["db"])
        )

    for db in args.db:
        if os.path.abspath(db) not in config["db_list"]:
            config["db_list"].append(os.path.abspath(db))

    if args.userid:
        for userid in args.userid:
            if userid not in config["users"]:
                raise ValueError(
                    "User ID {} isn't defined in the config file".format(userid)
                )
            elif "db" not in config["users"][userid]:
                raise ValueError(
                    "User ID {} is missing the db key in the config file".format(userid)
                )
            elif (
                "{}/{}".format(config["db_path"], config["users"][userid]["db"])
                not in config["db_list"]
            ):
                config["db_list"].append(
                    "{}/{}".format(config["db_path"], config["users"][userid]["db"])
                )

    # Order users by username to avoid having to do it in the templates
    config["users"] = {
        k: v
        for k, v in sorted(
            config["users"].items(), key=lambda item: item[1]["username"].lower()
        )
    }

    if "hidden" not in config:
        config["hidden"] = []

    # Lowercase and remove non-alphanumeric characters for better matching
    for i in range(len(config["hidden"])):
        config["hidden"][i] = ALPHANUM_PATTERN.sub("", config["hidden"][i]).lower()

    sanitized_metadata = {}
    for title in config["metadata"]:
        sanitized_title = ALPHANUM_PATTERN.sub("", title).lower()
        sanitized_metadata[sanitized_title] = config["metadata"][title]

    config["metadata"] = sanitized_metadata

    return config


def set_multiplayer_status(game_list, cache):
    """
    Sets the max_players for each release key; precedence is:
      - max_players in the config yaml
      - max_players from IGDB
      - 1 if the above aren't available and the only game mode from IGDB is single player
      - 0 (unknown) otherwise
    Also sets multiplayer to True if any of the of the following are true:
      - max_players > 1
      - IGDB game modes includes a multiplayer mode
    """
    for k in game_list:
        max_players = 0
        multiplayer = False
        reason = "as we have no max player info and can't infer from game modes"

        if "max_players" in game_list[k]:
            max_players = game_list[k]["max_players"]
            reason = "from config file"
            multiplayer = max_players > 1

        if k not in cache["igdb"]["games"]:
            reason = "no IGDB info in cache, did you call get_igdb_id()?"
            log.warning(f"{k}: {reason}")

        elif "max_players" not in cache["igdb"]["games"][k]:
            reason = "IGDB max_players not found, did you call get_multiplayer_info()?"
            log.warning(f"{k}: {reason}")

        elif cache["igdb"]["games"][k]["max_players"] > 0:
            max_players = cache["igdb"]["games"][k]["max_players"]
            reason = "from IGDB cache"
            multiplayer = True

        # We don't have max player info, so try to infer it from game modes
        elif (
            "info" in cache["igdb"]["games"][k]
            and cache["igdb"]["games"][k]["info"]
            and "game_modes" in cache["igdb"]["games"][k]["info"][0]
        ):
            if cache["igdb"]["games"][k]["info"][0]["game_modes"] == [
                IGDB_GAME_MODE["singleplayer"]
            ]:
                max_players = 1
                reason = "as IGDB has single player as the only game mode"
            else:
                for mode in cache["igdb"]["games"][k]["info"][0]["game_modes"]:
                    if mode in IGDB_MULTIPLAYER_GAME_MODES:
                        multiplayer = True
                        reason = f"as game modes includes {mode}"
                        break

        log.debug(f"{k}: multiplayer {multiplayer}, max players {max_players} {reason}")
        game_list[k]["multiplayer"] = multiplayer
        game_list[k]["max_players"] = max_players


def parse_cmdline(argv: List[str]) -> Any:
    parser = argparse.ArgumentParser(description="Show games owned by multiple users.")
    parser.add_argument(
        "db", type=str, nargs="*", help="the GOG DB for a user; multiple can be listed"
    )
    parser.add_argument(
        "-a",
        "--all-games",
        action="store_true",
        help="list all games owned by the selected users (doesn't include single player unless -I is used)",
    )
    parser.add_argument("-c", "--config-file", type=str, help="the config file to use")
    parser.add_argument("-d", "--debug", action="store_true", help="debug output")
    parser.add_argument(
        "-i",
        "--interface",
        type=str,
        help="the network interface to use if running in server mode; defaults to 0.0.0.0",
    )
    parser.add_argument(
        "-I",
        "--include-single-player",
        action="store_true",
        help="Include single player games",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        help="the network port to use if running in server mode; defaults to 8080",
    )
    parser.add_argument(
        "-s", "--server", action="store_true", help="run in server mode"
    )
    parser.add_argument(
        "-u", "--userid", type=int, nargs="*", help="the GOG user IDs to compare"
    )
    parser.add_argument(
        "-v", "--version", action="store_true", help="print version and exit"
    )

    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger()

    args = parse_cmdline(sys.argv[1:])

    if args.debug:
        log.setLevel(logging.DEBUG)

    # in case we want to see our command line shenanigans in a vebose session:
    log.debug(f"Command line arguments: {sys.argv}")
    log.debug(f"Arguments after parsing: {args}")

    try:
        config = build_config(args)
        log.debug(f"config = {config}")
    except ValueError as e:
        print(e)
        sys.exit(1)

    cache = Cache(config["cache"])
    # Get multiplayer info from IGDB and save it to the cache
    igdb = IGDBHelper(
        config["igdb_client_id"], config["igdb_client_secret"], cache.data
    )

    if "mode" in config and config["mode"] == "server":
        # Start Flask to run in server mode until killed
        app.run(host=config["interface"], port=config["port"])
        sys.exit(0)

    if args.userid is None:
        user_ids_to_compare = [u for u in config["users"].keys()]
    else:
        user_ids_to_compare = args.userid

    # init_opts() is meant for server mode; any CLI options that are also
    # web UI options need to be overridden
    opts = init_opts()
    opts["include_single_player"] = args.include_single_player
    for userid in user_ids_to_compare:
        opts["user_ids_to_compare"][userid] = config["users"][userid]

    log.debug(f'user_ids_to_compare = {opts["user_ids_to_compare"]}')
    gog = gogDB(config, opts)
    common_games = gog.get_common_games()

    # TODO: handle not getting an access token
    for release_key in list(common_games.keys()):
        igdb.get_igdb_id(release_key)
        igdb.get_game_info(release_key)
        igdb.get_multiplayer_info(release_key)

    cache.save()
    set_multiplayer_status(common_games, cache.data)
    common_games = gog.merge_duplicate_titles(common_games)

    if not config["all_games"]:
        common_games = gog.filter_games(common_games)

    for key in common_games:
        usernames_with_game_installed = [
            config["users"][userid]["username"]
            for userid in common_games[key]["installed"]
        ]

        print(
            "{} ({})".format(
                common_games[key]["title"],
                ", ".join(common_games[key]["platforms"]),
            ),
            end="",
        )
        if "max_players" in common_games[key]:
            print(f' Players: {common_games[key]["max_players"]}', end="")
        if "comment" in common_games[key]:
            print(f' Comment: {common_games[key]["comment"]}', end="")
        if not usernames_with_game_installed:
            print(" Installed: (none)")
        else:
            print(f' Installed: {", ".join(usernames_with_game_installed)}')

    print(gog.get_caption(len(common_games)))
