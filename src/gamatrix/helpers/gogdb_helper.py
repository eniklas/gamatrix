import copy
import json
import logging
import os
import sqlite3
from functools import cmp_to_key

from gamatrix.helpers.misc_helper import get_slug_from_title

from gamatrix.helpers.constants import PLATFORMS


def is_sqlite3(stream: bytearray) -> bool:
    """Returns True if stream contains an SQLite3 DB header"""
    # https://www.sqlite.org/fileformat.html
    return len(stream) >= 16 and stream[:16] == b"SQLite format 3\000"


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

        self.log = logging.getLogger(__name__)
        self.log.debug("db_list = {}".format(self.config["db_list"]))

    def use_db(self, db):
        if not os.path.exists(db):
            raise FileNotFoundError(f"DB {db} doesn't exist")

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

    def get_gamepiecetype_id(self, name):
        """Returns the numeric ID for the specified type"""
        return self.cursor.execute(
            'SELECT id FROM GamePieceTypes WHERE type="{}"'.format(name)
        ).fetchone()[0]

    # Taken from https://github.com/AB1908/GOG-Galaxy-Export-Script/blob/master/galaxy_library_export.py
    def get_owned_games(self):
        """Returns a list of release keys owned per the current DB"""

        owned_game_database = """CREATE TEMP VIEW MasterList AS
            SELECT GamePieces.releaseKey, GamePieces.gamePieceTypeId, GamePieces.value FROM ProductPurchaseDates
            JOIN GamePieces ON ProductPurchaseDates.gameReleaseKey = GamePieces.releaseKey;"""
        og_fields = [
            """CREATE TEMP VIEW MasterDB AS SELECT DISTINCT(MasterList.releaseKey) AS releaseKey, MasterList.value AS title, PLATFORMS.value AS platformList"""
        ]
        og_references = [""" FROM MasterList, MasterList AS PLATFORMS"""]
        og_conditions = [
            """ WHERE ((MasterList.gamePieceTypeId={}) OR (MasterList.gamePieceTypeId={})) AND ((PLATFORMS.releaseKey=MasterList.releaseKey) AND (PLATFORMS.gamePieceTypeId={}))""".format(
                self.get_gamepiecetype_id("originalTitle"),
                self.get_gamepiecetype_id("title"),
                self.get_gamepiecetype_id("allGameReleases"),
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

    def get_igdb_release_key(self, gamepiecetype_id, release_key):
        """
        Returns the release key to look up in IGDB. Steam keys are the
        most reliable to look up; GOG keys are about 50% reliable;
        other platforms will never work. So, our order of preference is:
          - Steam
          - GOG
          - release_key
        """
        query = f'SELECT * FROM GamePieces WHERE releaseKey="{release_key}" and gamePieceTypeId = {gamepiecetype_id}'
        self.log.debug("Running query: {}".format(query))
        self.cursor.execute(query)

        raw_result = self.cursor.fetchall()
        self.log.debug(f"raw_result = {raw_result}")
        result = json.loads(raw_result[0][3])
        self.log.debug(f"{release_key}: all release keys: {result}")
        if "releases" not in result:
            self.log.debug(
                f'{release_key}: "releases" not found in result for release keys'
            )
            return release_key

        for k in result["releases"]:
            # Sometimes there's a steam_1234 and steam_steam_1234, but always in that order
            if k.startswith("steam_") and not k.startswith("steam_steam_"):
                return k

        # If we found no Steam key, look for a GOG key
        for k in result["releases"]:
            if k.startswith("gog_"):
                return k

        # If we found neither Steam nor GOG keys, just return the key we were given
        return release_key

    def get_installed_games(self):
        """Returns a list of release keys installed per the current DB"""

        # https://www.reddit.com/r/gog/comments/ek3vtz/dev_gog_galaxy_20_get_list_of_gameid_of_installed/
        query = """SELECT trim(GamePieces.releaseKey) FROM GamePieces
            JOIN GamePieceTypes ON GamePieces.gamePieceTypeId = GamePieceTypes.id
            WHERE releaseKey IN
            (SELECT platforms.name || '_' || InstalledExternalProducts.productId
            FROM InstalledExternalProducts
            JOIN Platforms ON InstalledExternalProducts.platformId = Platforms.id
            UNION
            SELECT 'gog_' || productId FROM InstalledProducts)
            AND GamePieceTypes.type = 'originalTitle'"""

        self.log.debug(f"Running query: {query}")
        self.cursor.execute(query)
        installed_games = []
        # Release keys are each in their own list. Should only be one element per
        # list, but let's not assume that. Put all results into a single list
        for result in self.cursor.fetchall():
            for r in result:
                installed_games.append(r)

        return installed_games

    def get_common_games(self):
        game_list = {}
        self.owners_to_match = []

        # Loop through all the DBs and get info on all owned titles
        for db_file in self.config["db_list"]:
            self.log.debug("Using DB {}".format(db_file))
            self.use_db(db_file)
            userid = self.get_user()[0]
            self.owners_to_match.append(userid)
            self.gamepiecetype_id = self.get_gamepiecetype_id("allGameReleases")
            owned_games = self.get_owned_games()
            installed_games = self.get_installed_games()
            self.log.debug("owned games = {}".format(owned_games))
            # A row looks like (release_keys {"title": "Title Name"})
            for release_keys, title_json in owned_games:
                # If a game is owned on multiple platforms, the release keys will be comma-separated
                for release_key in release_keys.split(","):
                    # Release keys start with the platform
                    platform = release_key.split("_")[0]

                    if platform in self.config["exclude_platforms"]:
                        self.log.debug(
                            f"{release_key}: skipping as {platform} is excluded"
                        )
                        continue

                    if release_key not in game_list:
                        # This is the first we've seen this title, so add it
                        title = json.loads(title_json)["title"]
                        # epic_daac7fe46e3647cb80530411d7ec1dc5 (The Fall) has no data
                        if title is None:
                            self.log.debug(
                                f"{release_key}: skipping as it has a null title"
                            )
                            continue
                        slug = get_slug_from_title(title)
                        if slug in self.config["hidden"]:
                            self.log.debug(
                                f"{release_key} ({title}): skipping as it's hidden"
                            )
                            continue

                        game_list[release_key] = {
                            "title": title,
                            "slug": slug,
                            "owners": [],
                            "installed": [],
                        }

                        # Get the best key to use for IGDB
                        if platform == "steam":
                            game_list[release_key]["igdb_key"] = release_key
                        else:
                            game_list[release_key]["igdb_key"] = (
                                self.get_igdb_release_key(
                                    self.gamepiecetype_id, release_key
                                )
                            )

                        self.log.debug(
                            f'{release_key}: using {game_list[release_key]["igdb_key"]} for IGDB'
                        )

                        # Add metadata from the config file if we have any
                        if slug in self.config["metadata"]:
                            for k in self.config["metadata"][slug]:
                                self.log.debug(
                                    "Adding metadata {} to title {}".format(k, title)
                                )
                                game_list[release_key][k] = self.config["metadata"][
                                    slug
                                ][k]

                    self.log.debug("User {} owns {}".format(userid, release_key))
                    game_list[release_key]["owners"].append(userid)
                    game_list[release_key]["platforms"] = [platform]
                    if release_key in installed_games:
                        game_list[release_key]["installed"].append(userid)

            self.close_connection()

        # Sort by slug to avoid headaches in the templates;
        # dicts maintain insertion order as of Python 3.7
        ordered_game_list = {
            k: v for k, v in sorted(game_list.items(), key=cmp_to_key(self._sort))
        }

        # Sort the owner lists so we can compare them easily
        for k in ordered_game_list:
            ordered_game_list[k]["owners"].sort()

        self.owners_to_match.sort()
        self.log.debug("owners_to_match: {}".format(self.owners_to_match))

        return ordered_game_list

    def merge_duplicate_titles(self, game_list):
        working_game_list = copy.deepcopy(game_list)
        # Merge entries that have the same title and platforms
        keys = list(game_list)
        for k in keys:
            # Skip if we deleted this earlier, or we're at the end of the dict
            if k not in working_game_list or keys.index(k) >= len(keys) - 2:
                continue

            slug = game_list[k]["slug"]
            owners = game_list[k]["owners"]
            platforms = game_list[k]["platforms"]

            # Go through any subsequent keys with the same slug
            next_key = keys[keys.index(k) + 1]
            while game_list[next_key]["slug"] == slug:
                self.log.debug(
                    "Found duplicate title {} (slug: {}), keys {}, {}".format(
                        game_list[k]["title"], slug, k, next_key
                    )
                )
                if game_list[next_key]["max_players"] > game_list[k]["max_players"]:
                    self.log.debug(
                        "{}: has higher max players {}, {} will inherit".format(
                            next_key, game_list[next_key]["max_players"], k
                        )
                    )
                    game_list[k]["max_players"] = game_list[next_key]["max_players"]

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

    def filter_games(self, game_list, all_games=False):
        """
        Removes games that don't fit the search criteria. Note that
        we will not filter a game we have no multiplayer info on
        """
        working_game_list = copy.deepcopy(game_list)

        for k in game_list:
            # Remove single-player games if we didn't ask for them
            if (
                not self.config["include_single_player"]
                and not game_list[k]["multiplayer"]
            ):
                self.log.debug(f"{k}: Removing as it is single player")
                del working_game_list[k]
                continue

            # If all games was chosen, we don't want to filter anything else
            if all_games:
                continue

            # Delete any entries that aren't owned by all users we want
            for owner in self.config["user_ids_to_compare"]:
                if owner not in game_list[k]["owners"]:
                    self.log.debug(
                        f'Deleting {game_list[k]["title"]} as owners {game_list[k]["owners"]} does not include {owner}'
                    )
                    del working_game_list[k]
                    break
                elif (
                    self.config["installed_only"]
                    and owner not in game_list[k]["installed"]
                ):
                    self.log.debug(
                        f'Deleting {game_list[k]["title"]} as it\'s not installed by {owner}'
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

    def get_caption(self, num_games, random=False):
        """Returns the caption string"""

        if random:
            caption_start = f"Random game selected from {num_games}"
        else:
            caption_start = num_games

        if self.config["all_games"]:
            caption_middle = "total games owned by"
        elif len(self.config["user_ids_to_compare"]) == 1:
            caption_middle = "games owned by"
        else:
            caption_middle = "games in common between"

        usernames_excluded = ""
        if self.config["user_ids_to_exclude"] and not self.config["all_games"]:
            usernames = [
                self.config["users"][userid]["username"]
                for userid in self.config["user_ids_to_exclude"]
            ]
            usernames_excluded = f' and not owned by {", ".join(usernames)}'

        platforms_excluded = ""
        if self.config["exclude_platforms"]:
            platforms_excluded = " ({} excluded)".format(
                ", ".join(self.config["exclude_platforms"]).title()
            )

        self.log.debug("platforms_excluded = {}".format(platforms_excluded))

        installed = ""
        if self.config["installed_only"] and not self.config["all_games"]:
            installed = " (installed only)"

        usernames = []
        for userid in self.config["user_ids_to_compare"]:
            usernames.append(self.config["users"][userid]["username"])

        return "{} {} {}{}{}{}".format(
            caption_start,
            caption_middle,
            ", ".join(usernames),
            usernames_excluded,
            platforms_excluded,
            installed,
        )

    # Props to nradoicic!
    def _sort(self, a, b):
        """Does a primary sort by slug, and secondary sort by platforms
        so that steam and gog are first; we prefer those when removing
        dups, as we can currently only get IGDB data for them
        """
        platforms = PLATFORMS
        title_a = a[1]["slug"]
        title_b = b[1]["slug"]
        if title_a == title_b:
            platform_a = a[1]["platforms"][0]
            platform_b = b[1]["platforms"][0]
            for platform in [platform_a, platform_b]:
                if platform not in platforms:
                    self.log.warning(f"Unknown platform {platform}, not sorting")
                    return 0
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
