import logging
import requests
import time

from .constants import (
    IGDB_API_CALL_DELAY,
    IGDB_GAME_MODE,
    IGDB_MAX_PLAYER_KEYS,
    IGDB_PLATFORM_ID,
)
from helpers.cache_helper import Cache


class IGDBHelper:
    def __init__(self, client_id, client_secret, cache):
        self.cache = cache
        self.client_id = client_id
        self.client_secret = client_secret
        self.log = logging.getLogger(__name__)
        self.api_failures = 0
        self.last_api_call_time = time.time()
        self.platform_id = IGDB_PLATFORM_ID
        self.game_mode = IGDB_GAME_MODE
        self.max_player_keys = IGDB_MAX_PLAYER_KEYS
        self.api_call_delay = IGDB_API_CALL_DELAY
        self.access_token = {}
        self._init_cache()
        self.log.debug(f"cache = {self.cache}")
        if not self.access_token and not self.get_access_token():
            raise ValueError("Failed to get IGDB access token")

    def _init_cache(self):
        if "igdb" not in self.cache:
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
        """Makes an API request with retries, honoring the rate
        limit and backing off when we have failed calls
        """
        self.cache["dirty"] = True
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

        # Back off when we have failed requests
        if self.api_failures > 0:
            sleep_secs = time.sleep(5) * self.api_failures
            self.log.info(f"{self.api_failures} API failures, sleeping {sleep_secs}")

        while True:
            # Respect the API rate limit
            secs_since_last_call = time.time() - self.last_api_call_time

            if secs_since_last_call < self.api_call_delay:
                secs_to_wait = self.api_call_delay - secs_since_last_call
                self.log.debug(
                    f"{secs_since_last_call}s since last API call, waiting {secs_to_wait}s"
                )
                time.sleep(secs_to_wait)

            self.last_api_call_time = time.time()
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
            # This shouldn't happen, but just in case
            elif r.status_code == 429:
                sleep_secs = self.api_call_delay * 2
                self.log.info(f"Rate limit exceeded, sleeping {self.api_call_delay}s")
                time.sleep(self.api_call_delay)
            else:
                self.log.error(
                    f"Request failed, response: {r.text} (status code {r.status_code})"
                )
                self.api_failures += 1
                return {}

    def get_game_info(self, release_key):
        """Gets some game info for release_key.
        Returns True on success, False on failure
        """
        if release_key not in self.cache["igdb"]["games"]:
            self.log.error(f"{release_key} not in cache; use get_igdb_id() first")
            return False
        elif "info" in self.cache["igdb"]["games"][release_key]:
            self.log.debug(f"Found game info for {release_key} in cache")
            return True
        elif "igdb_id" not in self.cache["igdb"]["games"][release_key]:
            self.log.error("IGDB ID not found, can't get game info")
            return False

        # Get the game info from IGDB
        url = "https://api.igdb.com/v4/games"
        body = "fields game_modes,name,url; where id = {};".format(
            self.cache["igdb"]["games"][release_key]["igdb_id"]
        )

        response = self.api_request(url, body)
        self.cache["igdb"]["games"][release_key]["info"] = response
        return True

    def get_igdb_id(self, release_key):
        """Gets the IDGB ID for release_key"""
        if release_key not in self.cache["igdb"]["games"]:
            self.cache["igdb"]["games"][release_key] = {}
        elif "igdb_id" in self.cache["igdb"]["games"][release_key]:
            self.log.debug(
                "Found IGDB ID {} for {} in cache".format(
                    self.cache["igdb"]["games"][release_key]["igdb_id"], release_key
                )
            )
            return

        # release_key is e.g. steam_379720
        platform, platform_key = release_key.split("_")

        body = f'fields game; where uid = "{platform_key}"'
        # If we have a platform ID, specify it
        if platform in self.platform_id:
            body += f" & category = {self.platform_id[platform]}"

        body += ";"
        url = "https://api.igdb.com/v4/external_games"

        response = self.api_request(url, body)

        if response:
            # That gives [{"id": 8104, "game": 7351}]; game is the igdb id
            # The response is a list of all external IDs; they'll all have the same
            # value for "game" so just get the first one
            self.cache["igdb"]["games"][release_key]["igdb_id"] = response[0]["game"]
        else:
            # If we don't get an ID, set it to 0 so we know we've looked this game up before
            self.log.info(f"{release_key} not found in IGDB, setting ID to 0")
            self.cache["igdb"]["games"][release_key]["igdb_id"] = 0

    def get_multiplayer_info(self, release_key):
        """Gets the multiplayer info for release_key.
        Returns True on success, False on failure
        """
        if release_key not in self.cache["igdb"]["games"]:
            self.log.error(f"{release_key} not in cache; use get_igdb_id() first")
            return False

        elif "max_players" in self.cache["igdb"]["games"][release_key]:
            self.log.debug(
                "Found max players {} for release key {} in cache".format(
                    self.cache["igdb"]["games"][release_key]["max_players"],
                    release_key,
                )
            )
            return True

        if "igdb_id" not in self.cache["igdb"]["games"][release_key]:
            self.log.error("IGDB ID not found, can't get max players")
            return False

        # Get the multiplayer info
        url = "https://api.igdb.com/v4/multiplayer_modes"
        body = f'fields *; where game = {self.cache["igdb"]["games"][release_key]["igdb_id"]};'
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

    cache = Cache(".cache.json")
    igdb = IGDBHelper(client_id, secret, cache)

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
        # igdb.get_multiplayer_info(release_key)

    # cache.save()
