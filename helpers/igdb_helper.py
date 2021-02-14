import json
import logging
import os
import requests
import time


class IGDBHelper:
    def __init__(self, client_id, client_secret, cache_file):
        self.client_id = client_id
        self.client_secret = client_secret
        self.cache_file = cache_file
        self.log = logging.getLogger(__name__)
        self.api_failures = 0
        # https://api-docs.igdb.com/#external-game-enums
        # TODO: does "windows" == "xboxone"?
        # Only steam keys match up
        self.platform_id = {
            "steam": 1,
        }
        self.game_mode = {
            "singleplayer": 1,
            "multiplayer": 2,
            "coop": 3,
            "splitscreen": 4,
            "mmo": 5,
            "battleroyale": 6,
        }
        self.max_player_keys = [
            "offlinecoopmax",
            "offlinemax",
            "onlinecoopmax",
            "onlinemax",
        ]
        self.access_token = {}
        self._init_cache()
        self.log.debug(f"cache = {self.cache}")
        if not self.access_token and not self.get_access_token():
            raise ValueError("Failed to get IGDB access token")

    def _init_cache(self):
        """Initialize the cache"""
        self.cache = {}

        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r") as f:
                self.cache = json.load(f)

        if not "igdb" in self.cache:
            self.cache["igdb"] = {}
        if "games" not in self.cache["igdb"]:
            self.cache["igdb"]["games"] = {}
        if "access_token" in self.cache["igdb"]:
            self.access_token = self.cache["igdb"]["access_token"]

    def get_access_token(self):
        """Gets a new access token. Returns True on success, False on failure"""
        url = "https://id.twitch.tv/oauth2/token"
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }
        r = requests.post(url, params=payload)

        if r.status_code != 200:
            self.log.error(
                "Failed to get access token: {} (status code {})".format(
                    r.text, r.status_code
                )
            )
        elif "access_token" not in r.json():
            self.log.error(
                "Request succeded, but access_token not found in response: {}".format(
                    r.text
                )
            )
        else:
            self.log.debug(f"Access token request succeeded, response: {r.text}")
            self.access_token = r.json()["access_token"]
            self.cache["igdb"]["access_token"] = self.access_token
            self.api_failures = 0
            return True

        self.api_failures += 1
        return False

    def api_request(self, url, body):
        """Makes an API request, retrying if the rate limit is hit"""
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

        # Back off when we have failed requests
        if self.api_failures > 0:
            sleep_secs = time.sleep(5) * self.api_failures
            log.info(f"{self.api_failures} API failures, sleeping {sleep_secs}")

        while True:
            self.log.debug(f"Sending API request to {url}, body = '{body}'")
            r = requests.post(url, headers=headers, data=body)
            self.log.debug(f"Response = {r.text} (status code {r.status_code})")

            if r.status_code == 200:
                self.api_failures = 0
                return r.json()
            elif r.status_code == 401:
                self.log.info("Got 401 Unauthorized, getting new access token")
                # If this fails, avoid spamming the API
                if not self.get_access_token():
                    self.api_failures += 1
                    return {}
            elif r.status_code == 429:
                self.log.info("Rate limit exceeded, sleeping 1s")
                time.sleep(1)
            else:
                self.log.error(
                    f"Request failed, response: {r.text} (status code {r.status_code})"
                )
                self.api_failures += 1
                return {}

    def save_cache(self):
        with open(self.cache_file, "w") as f:
            json.dump(self.cache, f)

    def get_multiplayer_info(self, release_key):
        """Gets the multiplayer info for release_key.
        Returns True on success, False on failure
        """
        if (
            release_key in self.cache["igdb"]["games"]
            and "max_players" in self.cache["igdb"]["games"][release_key]
        ):
            self.log.debug(
                "Found max players {} for release key {} in cache".format(
                    self.cache["igdb"]["games"][release_key]["max_players"],
                    release_key,
                )
            )
            return True

        self.cache["igdb"]["games"][release_key] = {}

        # We don't have it in the cache, so get it from IGDB
        # release_key is e.g. steam_379720
        platform, platform_key = release_key.split("_")

        body = f'fields game; where uid = "{platform_key}"'
        # If we have a platform ID, specify it
        if platform in self.platform_id:
            body += f" & category = {self.platform_id[platform]}"

        body += ";"
        url = "https://api.igdb.com/v4/external_games"

        response = self.api_request(url, body)

        # Whether we find something or not, set max_players
        # so we know we've looked this game up before
        if not response:
            self.log.info(f"{release_key} not found in IGDB, setting max_players to 0")
            self.cache["igdb"]["games"][release_key]["max_players"] = 0
            return False

        # That gives [{"id": 8104, "game": 7351}]; game is the igdb id
        # The response is a list of all external IDs; they'll all have the same
        # value for "game" so just get the first one
        igdb_id = response[0]["game"]

        # Now we can get the multiplayer info
        url = "https://api.igdb.com/v4/multiplayer_modes"
        body = f"fields *; where game = {igdb_id};"
        response = self.api_request(url, body)

        self.cache["igdb"]["games"][release_key]["multiplayer"] = response
        self.cache["igdb"]["games"][release_key]["max_players"] = self._get_max_players(
            response
        )

    def _get_max_players(self, multiplayer_info):
        """Returns the max_players value for release_key, which is the
        highest value of any of the various max player keys from IGDB
        """
        max_players = 0

        # multiplayer_info is a list of all platforms it has data for;
        # e.g., "Xbox One" and "All platforms". Each of these can have some or all
        # of the keys we're looking for, and the data can be inconsistent between
        # platforms. So, loop through all platforms and grab the max value we see
        for platform in multiplayer_info:
            for mp_key in self.max_player_keys:
                if mp_key in platform and platform[mp_key] > max_players:
                    max_players = platform[mp_key]
                    self.log.debug(f"Found new max_players {max_players}, key {mp_key}")

        return max_players


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    log = logging.getLogger()
    client_id = ""
    secret = ""

    igdb = IGDBHelper(client_id, secret, ".cache.json")

    # print(json.dumps(igdb.cache))
    # Show all info on Spelunky
    url = "https://api.igdb.com/v4/games"
    body = 'fields *; where name = "Spelunky";'

    # Get game IDs on other services
    url = "https://api.igdb.com/v4/external_games"
    body = "fields *; where game = 3029;"

    # Get multiplayer info
    url = "https://api.igdb.com/v4/multiplayer_modes"
    body = "fields *; where game = 3029;"

    # Get external IDs for any game matching "the division"
    url = "https://api.igdb.com/v4/external_games"
    body = 'fields category,game,name,platform,uid; where name ~ *"the division"*;'

    url = "https://api.igdb.com/v4/games"
    body = 'fields *; where name ~ *"hot pursuit"*;'

    url = "https://api.igdb.com/v4/multiplayer_modes"
    body = "fields *; where game = 83341;"
    url = "https://api.igdb.com/v4/games"
    body = "fields *; where id = 83341;"

    url = "https://api.igdb.com/v4/games"
    body = "fields *; where id = 11749;"

    # Look up airborne kingdom, an epic exclusive game
    url = "https://api.igdb.com/v4/games"
    body = 'fields *; where name ~ *"airborne kingdom"*;'
    # That gives "external_games": [1710294, 1746471, 1913099]
    url = "https://api.igdb.com/v4/external_games"
    body = "fields *; where id = 1710294 | id = 1746471 | id = 1913099;"
    # Better way would be to take the id of the first call and do "where game = <game_id>"

    url = "https://api.igdb.com/v4/game_modes"
    body = "fields *;"

    # Show games matching aragami (insensitive case)
    # url = "https://api.igdb.com/v4/external_games"
    # body = 'fields *; where name ~ *"aragami"*;'
    # body = 'fields *; where uid = "1037569" | uid = "1305282" | uid = "1615335" | uid = "1931183";'

    url = "https://api.igdb.com/v4/games"
    body = 'fields *; where name = "198X";'

    # "id": 100562, "external_games": [ 1724812, 1725307, 1775673, 1914329 ],
    url = "https://api.igdb.com/v4/external_games"
    body = "fields *; where game = 100562;"

    # with open("tmp", "w") as f:
    #    f.write(json.dumps(igdb.api_request(url, body)))

    # igdb.api_request(url, body)

    for release_key in [
        "steam_251570",
        "steam_945360",
        "xboxone_244952910",
        "epic_aafc587fbf654758802c8e41e4fb3255",
        "steam_346110",
        "xboxone_983730484",
        "gog_2033991040",
        "steam_271670",
    ]:
        time.sleep(0.5)
        igdb.get_multiplayer_info(release_key)

    igdb.save_cache()
