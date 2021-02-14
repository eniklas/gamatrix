#!/usr/bin/env python3
import argparse
import copy
import json
import logging
import os
import re
import sqlite3
import sys
import time
from flask import Flask, request, render_template
from helpers.constants import ALPHANUM_PATTERN
from helpers.gogdb_helper import gogDB
from helpers.igdb_helper import IGDBHelper
from ruamel.yaml import YAML
from version import VERSION

app = Flask(__name__)


@app.route("/")
def root():
    log.info("Request from {}".format(request.remote_addr))
    return render_template(
        "index.html",
        users=config["users"],
        platforms=["epic", "gog", "origin", "steam", "uplay", "xboxone"],
    )


@app.route("/compare", methods=["GET", "POST"])
def compare_libraries():
    log.info("Request from {}".format(request.remote_addr))

    opts = init_opts()

    # Check boxes get passed in as "on" if checked, or not at all if unchecked
    for k in request.args.keys():
        # Only user IDs are ints
        try:
            int(k)
            opts["user_ids_to_compare"].append(int(k))
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

    users = gog.get_usernames_from_ids(gog.config["user_ids_to_compare"])
    common_games = gog.get_common_games()
    debug_str = ""
    return render_template(
        template,
        debug_str=debug_str,
        games=common_games,
        users=users,
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
        "user_ids_to_compare": [],
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

    if "multiplayer" not in config:
        config["multiplayer"] = []
    if "hidden" not in config:
        config["hidden"] = []

    # Lowercase and remove non-alphanumeric characters for better matching
    for k in ["multiplayer", "hidden"]:
        for i in range(len(config[k])):
            config[k][i] = ALPHANUM_PATTERN.sub("", config[k][i]).lower()

    sanitized_metadata = {}
    for title in config["metadata"]:
        sanitized_title = ALPHANUM_PATTERN.sub("", title).lower()
        sanitized_metadata[sanitized_title] = config["metadata"][title]

    config["metadata"] = sanitized_metadata

    return config


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger()

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

    args = parser.parse_args()
    if args.debug:
        log.setLevel(logging.DEBUG)

    try:
        config = build_config(args)
        log.debug(f"config = {config}")
    except ValueError as e:
        print(e)
        sys.exit(1)

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
    opts["user_ids_to_compare"] = user_ids_to_compare
    gog = gogDB(config, opts)
    games_in_common = gog.get_common_games()

    # Get multiplayer info from IGDB
    igdb = IGDBHelper(
        config["igdb_client_id"], config["igdb_client_secret"], config["cache"]
    )
    # TODO: handle not getting an access token
    for release_key in list(games_in_common.keys()):
        igdb.get_multiplayer_info(release_key)

    igdb.save_cache()

    for key in games_in_common:
        print(
            "{} ({})".format(
                games_in_common[key]["title"],
                ", ".join(games_in_common[key]["platforms"]),
            ),
            end="",
        )
        if "max_players" in games_in_common[key]:
            print(" Players: {}".format(games_in_common[key]["max_players"]), end="")
        if "comment" in games_in_common[key]:
            print(" Comment: {}".format(games_in_common[key]["comment"]), end="")
        print("")

    print(gog.get_caption(len(games_in_common)))
