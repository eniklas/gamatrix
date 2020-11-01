#!/usr/bin/env python3
import argparse
import copy
import json
import logging
import os
import sqlite3
import sys
from flask import Flask, request, render_template
from ruamel.yaml import YAML
from version import VERSION

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

app = Flask(__name__)


@app.route("/")
def root():
    return render_template("index.html", users=config["users"])


@app.route("/compare", methods=["GET", "POST"])
def compare_libraries():
    if request.method != "GET":
        return

    include_single_player = False
    user_ids_to_compare = []

    # Check boxes get passed in as "on" if checked, or not at all if unchecked
    for k in request.args.keys():
        if k == "include_single_player":
            include_single_player = True
        else:
            user_ids_to_compare.append(int(k))

    # If no users were selected, just refresh the page
    if not user_ids_to_compare:
        return root()

    gog = gogDB(config, user_ids_to_compare, include_single_player)
    common_games = gog.get_common_games()
    debug_str = ""
    return render_template(
        "game_list.html",
        debug_str=debug_str,
        games=common_games,
        caption=gog.get_caption(len(common_games)),
    )


class gogDB:
    def __init__(self, config, user_ids_to_compare=[], include_single_player=False):
        self.config = copy.deepcopy(config)
        self.config["user_ids_to_compare"] = user_ids_to_compare
        self.config["include_single_player"] = include_single_player

        # All DBs defined in the config file will be in db_list;
        #  remove the DBs for users that we don't want to compare
        for user in list(self.config["users"]):
            if (
                user not in self.config["user_ids_to_compare"]
                and "db" in self.config["users"][user]
                and "{}/{}".format(
                    self.config["db_path"], self.config["users"][user]["db"]
                )
                in self.config["db_list"]
            ):
                self.config["db_list"].remove(
                    "{}/{}".format(
                        self.config["db_path"], self.config["users"][user]["db"]
                    )
                )

        if config["log_level"].lower() == "debug":
            level = logging.DEBUG
        else:
            level = logging.INFO

        self.logger = logging.getLogger("gogDB")
        self.logger.setLevel(level)
        self.logger.debug("db_list = {}".format(self.config["db_list"]))

    def use_db(self, db):
        if not os.path.exists(db):
            raise FileNotFoundError("DB {} doesn't exist".format(db))

        self.db = db
        self.conn = sqlite3.connect(db)
        self.cursor = self.conn.cursor()

    def close_connection(self):
        self.conn.close()

    def get_user(self):
        user_query = self.cursor.execute("select * from Users")
        if user_query.rowcount == 0:
            raise ValueError("No users found in the Users table in the DB")

        user = self.cursor.fetchall()[0]

        if user_query.rowcount > 1:
            self.logger.warning(
                "Found multiple users in the DB; using the first one ({})".format(user)
            )

        return user

    def id(self, name):
        """ Returns the numeric ID for the specified type """
        return self.cursor.execute(
            'SELECT id FROM GamePieceTypes WHERE type="{}"'.format(name)
        ).fetchone()[0]

    # Taken from https://github.com/AB1908/GOG-Galaxy-Export-Script/blob/master/galaxy_library_export.py
    def get_owned_games(self, userid):
        owned_game_database = """CREATE TEMP VIEW MasterList AS
            SELECT GamePieces.releaseKey, GamePieces.gamePieceTypeId, GamePieces.value FROM GameLinks
            JOIN GamePieces ON GameLinks.releaseKey = GamePieces.releaseKey;"""
        og_fields = [
            """CREATE TEMP VIEW MasterDB AS SELECT DISTINCT(MasterList.releaseKey) AS releaseKey, MasterList.value AS title, PLATFORMS.value AS platformList"""
        ]
        og_references = [""" FROM MasterList, MasterList AS PLATFORMS"""]
        og_conditions = [
            """ WHERE ((MasterList.gamePieceTypeId={}) OR (MasterList.gamePieceTypeId={})) AND ((PLATFORMS.releaseKey=MasterList.releaseKey) AND (PLATFORMS.gamePieceTypeId={}))""".format(
                self.id("originalTitle"), self.id("title"), self.id("allGameReleases")
            )
        ]
        og_order = """ ORDER BY title;"""
        og_resultFields = [
            "GROUP_CONCAT(DISTINCT MasterDB.releaseKey)",
            "MasterDB.title",
        ]
        og_resultGroupBy = ["MasterDB.platformList"]
        og_query = "".join(og_fields + og_references + og_conditions) + og_order

        # Display each game and its details along with corresponding release key grouped by releasesList
        unique_game_data = (
            """SELECT {} FROM MasterDB GROUP BY {} ORDER BY MasterDB.title;""".format(
                ", ".join(og_resultFields), ", ".join(og_resultGroupBy)
            )
        )

        self.logger.debug("Running query: {}".format(owned_game_database))
        self.cursor.execute(owned_game_database)
        self.logger.debug("Running query: {}".format(og_query))
        self.cursor.execute(og_query)
        self.logger.debug("Running query: {}".format(unique_game_data))
        self.cursor.execute(unique_game_data)

        return self.cursor.fetchall()

    def get_common_games(self):
        game_list = {}
        owners_to_match = []

        for db_file in self.config["db_list"]:
            self.logger.debug("Using DB {}".format(db_file))
            self.use_db(db_file)
            userid = self.get_user()[0]
            owners_to_match.append(userid)
            owned_games = self.get_owned_games(userid)
            self.logger.debug("owned games = {}".format(owned_games))
            # A row looks like (release_keys {"title": "Title Name"})
            for release_keys, title_json in owned_games:
                # If a game is owned on multiple platforms, the release keys will be comma-separated
                for release_key in release_keys.split(","):
                    if release_key not in game_list:
                        # This is the first we've seen this title, so add it
                        title = json.loads(title_json)["title"]
                        # Skip this title if it's single player and we didn't ask for them
                        if (
                            not self.config["all_games"]
                            and not self.config["include_single_player"]
                            and title in self.config["single_player"]
                        ):
                            continue

                        game_list[release_key] = {
                            "title": title,
                            "owners": [],
                        }

                        # Add metadata from the config file if we have any
                        if title in self.config["metadata"]:
                            for k in self.config["metadata"][title]:
                                self.logger.debug(
                                    "Adding metadata {} to title {}".format(k, title)
                                )
                                game_list[release_key][k] = self.config["metadata"][
                                    title
                                ][k]

                    self.logger.debug("User {} owns {}".format(userid, release_key))
                    game_list[release_key]["owners"].append(userid)
                    # Release keys start with the platform
                    game_list[release_key]["platform"] = release_key.split("_")[0]

            self.close_connection()

        # Sort the owner lists so we can compare them easily
        for k in game_list:
            game_list[k]["owners"].sort()

        games_in_common = []
        owners_to_match.sort()
        self.logger.debug("owners_to_match: {}".format(owners_to_match))
        final_game_list = copy.deepcopy(game_list)
        for k in game_list:
            if game_list[k]["owners"] != owners_to_match:
                del final_game_list[k]

        # If -a was used, list all games (no filtering)
        if self.config["all_games"]:
            return game_list
        else:
            return final_game_list

    def get_caption(self, num_games):
        """Returns the caption string"""

        # List the username if possible, otherwise the user ID
        usernames = []
        for userid in self.config["user_ids_to_compare"]:
            if "username" in self.config["users"][userid]:
                usernames.append(self.config["users"][userid]["username"])
            else:
                usernames.append(str(userid))

        if self.config["all_games"]:
            caption_middle = "total games owned by"
        else:
            caption_middle = "games in common between"

        return "{} {} {}".format(
            num_games, caption_middle, ", ".join(sorted(usernames, key=str.lower))
        )


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

    if args.all_games:
        config["all_games"] = True
    else:
        config["all_games"] = False

    if args.debug:
        config["log_level"] = "debug"
    elif "log_level" not in config:
        config["log_level"] = "info"

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

    if "single_player" not in config:
        config["single_player"] = []

    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show games owned by multiple users.")
    parser.add_argument(
        "db", type=str, nargs="*", help="the GOG DB for a user; multiple can be listed"
    )
    parser.add_argument(
        "-a",
        "--all-games",
        action="store_true",
        help="list all games owned by the selected users",
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
    try:
        config = build_config(args)
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

    gog = gogDB(config, user_ids_to_compare)
    games_in_common = gog.get_common_games()
    for title in sorted([games_in_common[t]["title"] for t in games_in_common.keys()]):
        print(title)
    print(gog.get_caption(len(games_in_common)))
