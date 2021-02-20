import copy
import json
import logging
import os
import sqlite3

from .constants import ALPHANUM_PATTERN
from functools import cmp_to_key


class gogDB:
    def __init__(
        self,
        config,
        opts,
    ):
        # Server mode only reads the config once, so we don't want to modify it
        self.config = copy.deepcopy(config)
        for k in opts:
            self.config[k] = opts[k]
        self.config["user_ids_to_exclude"] = []

        # All DBs defined in the config file will be in db_list. Remove the DBs for
        # users that we don't want to compare, unless exclusive was specified, in
        # which case we need to look at all DBs
        for user in list(self.config["users"]):
            if user not in self.config["user_ids_to_compare"]:
                if self.config["exclusive"]:
                    self.config["user_ids_to_exclude"].append(user)
                elif (
                    "db" in self.config["users"][user]
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

        self.log = logging.getLogger("gogDB")
        self.log.debug("db_list = {}".format(self.config["db_list"]))

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
            self.log.warning(
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

        for query in [owned_game_database, og_query, unique_game_data]:
            self.log.debug("Running query: {}".format(query))
            self.cursor.execute(query)

        return self.cursor.fetchall()

    def get_common_games(self):
        game_list = {}
        self.owners_to_match = []

        # Loop through all the DBs and get info on all owned titles
        for db_file in self.config["db_list"]:
            self.log.debug("Using DB {}".format(db_file))
            self.use_db(db_file)
            userid = self.get_user()[0]
            self.owners_to_match.append(userid)
            owned_games = self.get_owned_games(userid)
            self.log.debug("owned games = {}".format(owned_games))
            # A row looks like (release_keys {"title": "Title Name"})
            for release_keys, title_json in owned_games:
                # If a game is owned on multiple platforms, the release keys will be comma-separated
                for release_key in release_keys.split(","):
                    # Release keys start with the platform
                    platform = release_key.split("_")[0]
                    if (
                        release_key not in game_list
                        and platform not in self.config["exclude_platforms"]
                    ):
                        # This is the first we've seen this title, so add it
                        title = json.loads(title_json)["title"]
                        sanitized_title = ALPHANUM_PATTERN.sub("", title).lower()
                        if sanitized_title in self.config["hidden"]:
                            self.log.debug(
                                f"Skipping {release_key} ({title}) as it's hidden"
                            )
                            continue

                        game_list[release_key] = {
                            "title": title,
                            "sanitized_title": sanitized_title,
                            "owners": [],
                        }

                        # Add metadata from the config file if we have any
                        if sanitized_title in self.config["metadata"]:
                            for k in self.config["metadata"][sanitized_title]:
                                self.log.debug(
                                    "Adding metadata {} to title {}".format(k, title)
                                )
                                game_list[release_key][k] = self.config["metadata"][
                                    sanitized_title
                                ][k]

                    self.log.debug("User {} owns {}".format(userid, release_key))
                    game_list[release_key]["owners"].append(userid)
                    game_list[release_key]["platforms"] = [platform]

            self.close_connection()

        # Sort by sanitized title to avoid headaches in the templates;
        # dicts maintain insertion order as of Python 3.7
        ordered_game_list = {
            k: v for k, v in sorted(game_list.items(), key=cmp_to_key(self._sort))
        }

        # Sort the owner lists so we can compare them easily
        for k in ordered_game_list:
            ordered_game_list[k]["owners"].sort()

        self.owners_to_match.sort()
        self.log.debug("owners_to_match: {}".format(self.owners_to_match))

        deduped_game_list = self.merge_duplicate_titles(ordered_game_list)
        self.log.debug(
            f"ordered_game_list (before dedup) = {ordered_game_list}, size = {len(ordered_game_list)}\n"
        )
        self.log.debug(
            f"deduped_game_list = {deduped_game_list}, size = {len(deduped_game_list)}"
        )

        return deduped_game_list

    def merge_duplicate_titles(self, game_list):
        working_game_list = copy.deepcopy(game_list)
        # Merge entries that have the same title and platforms
        keys = list(game_list)
        for k in keys:
            # Skip if we deleted this earlier, or we're at the end of the dict
            if k not in working_game_list or keys.index(k) >= len(keys) - 2:
                continue

            sanitized_title = game_list[k]["sanitized_title"]
            owners = game_list[k]["owners"]
            platforms = game_list[k]["platforms"]

            # Go through any subsequent keys with the same (sanitized) title
            next_key = keys[keys.index(k) + 1]
            while game_list[next_key]["sanitized_title"] == sanitized_title:
                self.log.debug(
                    "Found duplicate title {} (sanitized: {}), keys {}, {}".format(
                        game_list[k]["title"], sanitized_title, k, next_key
                    )
                )
                if game_list[next_key]["owners"] == owners:
                    self.log.debug(
                        "{}: owners are the same: {}, {}".format(
                            next_key, owners, game_list[next_key]["owners"]
                        )
                    )
                    platform = game_list[next_key]["platforms"][0]
                    if platform not in platforms:
                        self.log.debug(
                            "{}: adding new platform {} to {}".format(
                                next_key, platform, platforms
                            )
                        )
                        platforms.append(platform)
                    else:
                        self.log.debug(
                            "{}: platform {} already in {}".format(
                                next_key, platform, platforms
                            )
                        )

                    self.log.debug(
                        "{}: deleting duplicate {} as it has been merged into {}".format(
                            next_key, game_list[next_key], game_list[k]
                        )
                    )
                    del working_game_list[next_key]
                    working_game_list[k]["platforms"] = sorted(platforms)
                else:
                    self.log.debug(
                        "{}: owners are different: {} {}".format(
                            next_key, owners, game_list[next_key]["owners"]
                        )
                    )

                if keys.index(next_key) >= len(keys) - 2:
                    break

                next_key = keys[keys.index(next_key) + 1]

        return working_game_list

    def filter_games(self, game_list):
        """Removes games that don't fit the search criteria"""
        working_game_list = copy.deepcopy(game_list)

        for k in game_list:
            # Remove single-player games if we didn't ask for them
            if (
                not self.config["include_single_player"]
                and "max_players" in game_list[k]
                and game_list[k]["max_players"] == 1
            ):
                self.log.debug(f"{k}: Removing as it is single player")
                del working_game_list[k]
                continue

            # Remove games with zero (unknown) max players
            if (
                not self.config["include_zero_players"]
                and "max_players" in game_list[k]
                and game_list[k]["max_players"] == 0
            ):
                self.log.debug(f"{k}: Removing as it has zero max players")
                del working_game_list[k]
                continue

            # Delete any entries that aren't owned by all users we want
            for owner in self.config["user_ids_to_compare"]:
                if owner not in game_list[k]["owners"]:
                    self.log.debug(
                        "Deleting {} as owners {} does not include {}".format(
                            game_list[k]["title"],
                            game_list[k]["owners"],
                            owner,
                        )
                    )
                    del working_game_list[k]
                    break
            # This only executes if the for loop didn't break
            else:
                for owner in self.config["user_ids_to_exclude"]:
                    if owner in game_list[k]["owners"]:
                        self.log.debug(
                            "Deleting {} as owners {} includes {} and exclusive is true".format(
                                game_list[k]["title"],
                                game_list[k]["owners"],
                                owner,
                            )
                        )
                        del working_game_list[k]
                        break

        return working_game_list

    def get_caption(self, num_games):
        """Returns the caption string"""
        usernames = self.get_usernames_from_ids(self.config["user_ids_to_compare"])

        if self.config["all_games"]:
            caption_middle = "total games owned by"
        elif len(usernames) == 1:
            caption_middle = "games owned by"
        else:
            caption_middle = "games in common between"

        userids_excluded = ""
        if self.config["user_ids_to_exclude"]:
            usernames_to_exclude = self.get_usernames_from_ids(
                self.config["user_ids_to_exclude"]
            )
            userids_excluded = " and not owned by {}".format(
                ", ".join(usernames_to_exclude.values())
            )

        platforms_excluded = ""
        if self.config["exclude_platforms"]:
            platforms_excluded = " ({} excluded)".format(
                ", ".join(self.config["exclude_platforms"]).title()
            )

        self.log.debug("platforms_excluded = {}".format(platforms_excluded))

        return "{} {} {}{}{}".format(
            num_games,
            caption_middle,
            ", ".join(usernames.values()),
            userids_excluded,
            platforms_excluded,
        )

    def get_usernames_from_ids(self, userids):
        """Returns a dict of usernames mapped by user ID"""
        usernames = {}
        sorted_usernames = {}

        for userid in userids:
            if "username" in self.config["users"][userid]:
                usernames[userid] = self.config["users"][userid]["username"]
            else:
                usernames[userid] = str(userid)

        # Order by value (username) to avoid having to do it in the templates
        sorted_usernames = {
            k: v for k, v in sorted(usernames.items(), key=lambda item: item[1].lower())
        }

        return sorted_usernames

    # Props to nradoicic!
    def _sort(self, a, b):
        """Does a primary sort by sanitized title, and secondary sort by
        platforms so that steam is first; we prefer the steam key when
        removing dups, as we can currently only get IGDB data for steam
        """
        platforms = ("steam", "gog", "epic", "origin", "uplay", "xboxone")
        title_a = a[1]["sanitized_title"]
        title_b = b[1]["sanitized_title"]
        if title_a == title_b:
            platform_a = a[1]["platforms"][0]
            platform_b = b[1]["platforms"][0]
            index_a = platforms.index(platform_a)
            index_b = platforms.index(platform_b)
            if index_a < index_b:
                return -1
            if index_a > index_b:
                return 1
            return 0
        if title_a < title_b:
            return -1
        if title_a > title_b:
            return 1
