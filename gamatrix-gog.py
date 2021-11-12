#!/usr/bin/env python3
"""
gamatrix-gog
Show and compare between games owned by multiple users.

Usage:
    gamatrix-gog.py --help
    gamatrix-gog.py --version
    gamatrix-gog.py [--config-file=CFG] [--debug] [--all-games] [--interface=IFC] [--installed-only] [--include-single-player] [--port=PORT] [--server] [--update-cache] [--userid=UID ...] [<db> ... ]

Options:
  -h, --help                   Show this help message and exit.
  -v, --version                Print version and exit.
  -c CFG, --config-file=CFG    The config file to use.
  -d, --debug                  Print out verbose debug output.
  -a, --all-games              List all games owned by the selected users (doesn't include single player unless -S is used).
  -i IFC, --interface=IFC      The network interface to use if running in server mode; default is 0.0.0.0.
  -I, --installed-only         Only show games installed by all users.
  -p PORT, --port=PORT         The network port to use if running in server mode; default is 8080.
  -s, --server                 Run in server mode.
  -S, --include-single-player  Include single player games.
  -U, --update-cache           Update cache entries that have incomplete info.
  -u USERID, --userid=USERID   The GOG user IDs to compare, there can be multiples of this switch.

Positional Arguments:
  <db>                         The GOG DB for a user, multiple can be listed.
"""

import docopt
import logging
import os
import sys
import time

from flask import Flask, render_template, request
from ipaddress import IPv4Address, IPv4Network
from ruamel.yaml import YAML
from typing import Any, Dict, List
from werkzeug.utils import secure_filename

from helpers import constants
from helpers.cache_helper import Cache
from helpers.gogdb_helper import gogDB, is_sqlite3
from helpers.igdb_helper import IGDBHelper
from helpers.misc_helper import get_slug_from_title
from helpers.network_helper import check_ip_is_authorized
from version import VERSION

app = Flask(__name__)


@app.route("/")
def root():
    check_ip_is_authorized(request.remote_addr, config["allowed_cidrs"])

    return render_template(
        "index.html",
        users=config["users"],
        uploads_enabled=config["uploads_enabled"],
        platforms=constants.PLATFORMS,
        version=VERSION,
    )


# https://flask.palletsprojects.com/en/1.1.x/patterns/fileuploads/
@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    check_ip_is_authorized(request.remote_addr, config["allowed_cidrs"])

    if request.method == "POST":
        message = "Upload failed: "

        # Check if the post request has the file part
        if "file" not in request.files:
            message += "no file part in post request"
        else:
            # Until we use a prod server, files that are too large will just hang :-(
            # See the flask site above for deets
            file = request.files["file"]

            # If user does not select file, the browser submits an empty part without filename
            if file.filename == "":
                message += "no file selected"
            elif not allowed_file(file.filename):
                message += "unsupported file extension"
            else:
                # Name the file according to who uploaded it
                user, target_filename = get_db_name_from_ip(request.remote_addr)
                if target_filename is None:
                    message += "failed to determine target filename from your IP; is it in the config file?"
                elif not is_sqlite3(file.read(16)):
                    message += "file is not an SQLite database"
                else:
                    log.info(f"Uploading {target_filename} from {request.remote_addr}")
                    filename = secure_filename(target_filename)

                    # Back up the previous file
                    full_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                    backup_filename = f"{filename}.bak"
                    full_backup_path = os.path.join(
                        app.config["UPLOAD_FOLDER"], backup_filename
                    )

                    if os.path.exists(full_path):
                        os.replace(full_path, full_backup_path)

                    # Put the cursor back to the start after the above file.read()
                    file.seek(0)
                    file.save(full_path)
                    # Could call get_db_mtime() here but this is less expensive
                    config["users"][user]["db_mtime"] = time.strftime(
                        constants.TIME_FORMAT, time.localtime()
                    )
                    message = f"Great success! File uploaded as {filename}"

        return render_template("upload_status.html", message=message)
    else:
        return """
        <!doctype html>
        <title>Upload DB</title>
        <h1>Upload DB</h1>
        GOG DBs are usually in C:\ProgramData\GOG.com\Galaxy\storage\galaxy-2.0.db
        <br><br>
        <form method=post enctype=multipart/form-data>
        <input type=file name=file>
        <input type=submit value=Upload>
        </form>
        """


@app.route("/api/users", methods=["GET"])
def get_users():
    return """{
        [
            {
                "steam_username": "d3r3kk",
                "name": "Derek"
                "image": "static/d3r3kk.png"
            },
            {
                "steam_username": "elsinore84",
                "name": "elsinore84",
                "image": "static/Blade Runner.png"
            },
            {
                "steam_username": "Chief_Wahoo",
                "name": "Chief Wahoo",
                "image": "static/Chief Wahoo.png"
            },
            {
                "steam_username": "Kane",
                "name": "Kane",
                "image": "static/Kane.png"
            },
            {
                "steam_username": "MonkeyPox",
                "name": "MonkeyPox",
                "image": "static/MonkeyPox.png"
            }
        ]
    }
    """


@app.route("/compare", methods=["GET", "POST"])
def compare_libraries():
    check_ip_is_authorized(request.remote_addr, config["allowed_cidrs"])

    if request.args["option"] == "upload":
        return upload_file()

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
    elif request.args["option"] == "list":
        template = "game_list.html"
    else:
        return root()

    common_games = gog.get_common_games()

    if not igdb.access_token:
        igdb.get_access_token()

    for k in list(common_games.keys()):
        log.debug(f'{k}: using igdb_key {common_games[k]["igdb_key"]}')
        # Get the IGDB ID by release key if possible, otherwise try by title
        igdb.get_igdb_id(common_games[k]["igdb_key"]) or igdb.get_igdb_id_by_slug(
            common_games[k]["igdb_key"],
            common_games[k]["slug"],
            config["update_cache"],
        )
        igdb.get_game_info(common_games[k]["igdb_key"])
        igdb.get_multiplayer_info(common_games[k]["igdb_key"])

    cache.save()
    set_multiplayer_status(common_games, cache.data)
    common_games = gog.merge_duplicate_titles(common_games)

    common_games = gog.filter_games(common_games, gog.config["all_games"])

    log.debug(f'user_ids_to_compare = {opts["user_ids_to_compare"]}')

    debug_str = ""
    return render_template(
        template,
        debug_str=debug_str,
        games=common_games,
        users=opts["user_ids_to_compare"],
        caption=gog.get_caption(len(common_games)),
        show_keys=opts["show_keys"],
        platforms=constants.PLATFORMS,
    )


def get_db_name_from_ip(ip):
    """Returns the userid and DB filename based on the IP of the user"""
    ip = IPv4Address(ip)

    for user in config["users"]:
        if "cidrs" in config["users"][user]:
            for cidr in config["users"][user]["cidrs"]:
                if ip in cidr:
                    return user, config["users"][user]["db"]

    return None, None


def allowed_file(filename):
    """Returns True if filename has an allowed extension"""
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in constants.UPLOAD_ALLOWED_EXTENSIONS
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


def get_db_mtime(db):
    """Returns the modification time of DB in local time"""
    try:
        mtime = time.strftime(
            constants.TIME_FORMAT, time.localtime(os.path.getmtime(db))
        )
    except Exception:
        mtime = "unavailable"
    return mtime


def build_config(args: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a config dict created from the config file and
    command-line arguments, with the latter taking precedence
    """
    config_file = args.get("--config-file", None)
    if config_file is not None:
        yaml = YAML(typ="safe")
        with open(config_file, "r") as config_file:
            config = yaml.load(config_file)
    else:
        # We didn't get a config file, so populate from args
        config = {}

    # TODO: allow using both IDs and DBs (use one arg and detect if it's an int)
    # TODO: should be able to use unambiguous partial names
    if not args.get("<db>", []) and "users" not in config:
        raise ValueError("You must use -u, have users in the config file, or list DBs")

    # Command-line args override values in the config file

    # This can't be given as an argument as it wouldn't make much sense;
    #  provide a sane default if it's missing from the config file
    if "db_path" not in config:
        config["db_path"] = "."

    config["all_games"] = args.get("--all-games", False)
    config["include_single_player"] = args.get("--include-single-player", False)
    config["installed_only"] = args.get("--installed-only", False)

    if args.get(
        "--server", False
    ):  # Note that the --server opt is False unless present
        config["mode"] = "server"

    if args.get("--interface"):
        config["interface"] = args["--interface"]
    if "interface" not in config:
        config["interface"] = "0.0.0.0"

    if args.get("--port"):
        config["port"] = int(args["--port"])
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
        full_db_path = f'{config["db_path"]}/{config["users"][userid]["db"]}'
        config["db_list"].append(full_db_path)
        config["users"][userid]["db_mtime"] = get_db_mtime(full_db_path)

        # Convert CIDRs into IPv4Network objects; if there are none, disable uploads
        config["uploads_enabled"] = False
        if "cidrs" in config["users"][userid]:
            for i in range(len(config["users"][userid]["cidrs"])):
                config["users"][userid]["cidrs"][i] = IPv4Network(
                    config["users"][userid]["cidrs"][i]
                )
                config["uploads_enabled"] = True

    for db in args.get("<db>", []):
        if os.path.abspath(db) not in config["db_list"]:
            config["db_list"].append(os.path.abspath(db))

    for userid_str in args.get("--userid", []):
        userid = int(userid_str)
        if userid not in config["users"]:
            raise ValueError(f"User ID {userid} isn't defined in the config file")
        elif "db" not in config["users"][userid]:
            raise ValueError(
                f"User ID {userid} is missing the db key in the config file"
            )
        elif (
            f'{config["db_path"]}/{config["users"][userid]["db"]}'
            not in config["db_list"]
        ):
            config["db_list"].append(
                f'{config["db_path"]}/{config["users"][userid]["db"]}'
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

    config["update_cache"] = args.get("--update-cache", False)

    # Lowercase and remove non-alphanumeric characters for better matching
    for i in range(len(config["hidden"])):
        config["hidden"][i] = get_slug_from_title(config["hidden"][i])

    slug_metadata = {}
    for title in config["metadata"]:
        slug = get_slug_from_title(title)
        slug_metadata[slug] = config["metadata"][title]

    config["metadata"] = slug_metadata

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
        igdb_key = game_list[k]["igdb_key"]
        max_players = 0
        multiplayer = False
        reason = "as we have no max player info and can't infer from game modes"

        if "max_players" in game_list[k]:
            max_players = game_list[k]["max_players"]
            reason = "from config file"
            multiplayer = max_players > 1

        elif igdb_key not in cache["igdb"]["games"]:
            reason = (
                f"no IGDB info in cache for {igdb_key}, did you call get_igdb_id()?"
            )

        elif "max_players" not in cache["igdb"]["games"][igdb_key]:
            reason = f"IGDB {igdb_key} max_players not found, did you call get_multiplayer_info()?"
            log.warning(f"{k}: something seems wrong, see next message")

        elif cache["igdb"]["games"][igdb_key]["max_players"] > 0:
            max_players = cache["igdb"]["games"][igdb_key]["max_players"]
            reason = "from IGDB cache"
            multiplayer = cache["igdb"]["games"][igdb_key]["max_players"] > 1

        # We don't have max player info, so try to infer it from game modes
        elif (
            "info" in cache["igdb"]["games"][igdb_key]
            and cache["igdb"]["games"][igdb_key]["info"]
            and "game_modes" in cache["igdb"]["games"][igdb_key]["info"][0]
        ):
            if cache["igdb"]["games"][igdb_key]["info"][0]["game_modes"] == [
                constants.IGDB_GAME_MODE["singleplayer"]
            ]:
                max_players = 1
                reason = "as IGDB has single player as the only game mode"
            else:
                for mode in cache["igdb"]["games"][igdb_key]["info"][0]["game_modes"]:
                    if mode in constants.IGDB_MULTIPLAYER_GAME_MODES:
                        multiplayer = True
                        reason = f"as game modes includes {mode}"
                        break

        log.debug(
            f"{k} ({game_list[k]['title']}, IGDB key {igdb_key}): "
            f"multiplayer {multiplayer}, max players {max_players} {reason}"
        )
        game_list[k]["multiplayer"] = multiplayer
        game_list[k]["max_players"] = max_players


def parse_cmdline(argv: List[str]) -> Dict[str, Any]:
    return docopt.docopt(
        __doc__, argv=argv, help=True, version=VERSION, options_first=True
    )


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger()

    opts = parse_cmdline(sys.argv[1:])

    if opts.get("--debug", False):
        log.setLevel(logging.DEBUG)

    log.debug(f"Command line arguments: {sys.argv}")
    log.debug(f"Arguments after parsing: {opts}")

    config = build_config(opts)
    log.debug(f"config = {config}")

    cache = Cache(config["cache"])
    # Get multiplayer info from IGDB and save it to the cache
    igdb = IGDBHelper(
        config["igdb_client_id"], config["igdb_client_secret"], cache.data
    )

    if "mode" in config and config["mode"] == "server":
        # Start Flask to run in server mode until killed
        if os.name != "nt":
            time.tzset()  # type: ignore

        app.config["UPLOAD_FOLDER"] = config["db_path"]
        app.config["MAX_CONTENT_LENGTH"] = constants.UPLOAD_MAX_SIZE
        app.run(host=config["interface"], port=config["port"])
        sys.exit(0)

    user_ids_to_compare = opts.get("--userid", [])
    if user_ids_to_compare:
        user_ids_to_compare = [int(u) for u in user_ids_to_compare]
    else:
        user_ids_to_compare = [u for u in config["users"].keys()]

    # init_opts() is meant for server mode; any CLI options that are also
    # web UI options need to be overridden
    web_opts = init_opts()
    web_opts["include_single_player"] = opts.get("--include-single-player", False)

    for userid in user_ids_to_compare:
        web_opts["user_ids_to_compare"][userid] = config["users"][userid]

    log.debug(f'user_ids_to_compare = {web_opts["user_ids_to_compare"]}')

    gog = gogDB(config, web_opts)
    common_games = gog.get_common_games()

    for k in list(common_games.keys()):
        log.debug(f'{k}: using igdb_key {common_games[k]["igdb_key"]}')
        # Get the IGDB ID by release key if possible, otherwise try by title
        igdb.get_igdb_id(
            common_games[k]["igdb_key"], config["update_cache"]
        ) or igdb.get_igdb_id_by_slug(
            common_games[k]["igdb_key"],
            common_games[k]["slug"],
            config["update_cache"],
        )
        igdb.get_game_info(common_games[k]["igdb_key"], config["update_cache"])
        igdb.get_multiplayer_info(common_games[k]["igdb_key"], config["update_cache"])

    cache.save()
    set_multiplayer_status(common_games, cache.data)
    common_games = gog.merge_duplicate_titles(common_games)

    common_games = gog.filter_games(common_games, config["all_games"])

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
