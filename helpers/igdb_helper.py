import logging
import requests
import time

from .constants import (
    IGDB_API_CALL_DELAY,
    IGDB_GAME_MODE,
    IGDB_MAX_PLAYER_KEYS,
    IGDB_PLATFORM_ID,
)


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
        if not self.access_token:
            self.log.info("No access token in cache, getting new one")
            self.get_access_token()

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

        request_succeeded = True

        try:
            r = requests.post(url, params=payload)
        except Exception as e:
            self.log.error(f"Request to IGDB failed trying to get access token: {e}")
            request_succeeded = False

        if not request_succeeded:
            pass
        elif r.status_code != 200:
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
            self.log.info("Access token request succeeded")
            self.access_token = r.json()["access_token"]
            self.cache["igdb"]["access_token"] = self.access_token
            self.api_failures = 0
            return True

        self.access_token = {}
        self.api_failures += 1
        return False

    def api_request(self, url, body):
        """Makes an API request with retries, honoring the rate
        limit and backing off when we have failed calls
        """
        if not self.access_token:
            self.log.error("We have no access token, skipping IGDB request")
            return {}

        self.cache["dirty"] = True
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

        # Back off when we have failed requests
        if self.api_failures > 0:
            sleep_secs = 2 * self.api_failures
            self.log.info(f"{self.api_failures} API failures, sleeping {sleep_secs}")
            time.sleep(sleep_secs)

        while True:
            # Respect the API rate limit
            secs_since_last_call = time.time() - self.last_api_call_time

            if secs_since_last_call < self.api_call_delay:
                secs_to_wait = self.api_call_delay - secs_since_last_call
                self.log.debug(
                    f"{secs_since_last_call:.3f}s since last API call, waiting {secs_to_wait:.3f}s"
                )
                time.sleep(secs_to_wait)

            self.last_api_call_time = time.time()
            self.log.debug(f"Sending API request to {url}, body = '{body}'")
            try:
                r = requests.post(url, headers=headers, data=body)
            except Exception as e:
                self.log.error(f"Request to IGDB failed: {e}")
                self.api_failures += 1
                return {}

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
                self.log.info(f"Rate limit exceeded, sleeping {sleep_secs}s")
                time.sleep(sleep_secs)
            else:
                self.log.error(
                    f"Request failed, response: {r.text} (status code {r.status_code})"
                )
                self.api_failures += 1
                return {}

    def get_game_info(self, release_key, update=False):
        """Gets some game info for release_key"""
        if release_key not in self.cache["igdb"]["games"]:
            self.log.error(f"{release_key}: not in cache; use get_igdb_id() first")
            return
        elif "info" in self.cache["igdb"]["games"][release_key] and not (
            update and not self.cache["igdb"]["games"][release_key]["info"]
        ):
            self.log.debug(f"{release_key}: found game info in cache")
            return
        elif "igdb_id" not in self.cache["igdb"]["games"][release_key]:
            self.log.error(f"{release_key}: IGDB ID not found, can't get game info")
            return
        elif self.cache["igdb"]["games"][release_key]["igdb_id"] == 0:
            self.log.debug(
                f"{release_key}: IGDB ID is 0, not looking up game info and setting empty response in cache"
            )
            # Save an empty response so we know we tried to look this up before
            response = []
            self.cache["dirty"] = True
        else:
            self.log.info(f"{release_key}: getting game info from IGDB")
            url = "https://api.igdb.com/v4/games"
            body = "fields game_modes,name,parent_game,slug; where id = {};".format(
                self.cache["igdb"]["games"][release_key]["igdb_id"]
            )

            response = self.api_request(url, body)

        self.cache["igdb"]["games"][release_key]["info"] = response
        return

    def get_igdb_id(self, release_key, update=False):
        """Gets the IDGB ID for release_key. Returns
        True if an ID was found, False if not
        """
        if release_key not in self.cache["igdb"]["games"]:
            self.cache["igdb"]["games"][release_key] = {}
        elif self._igdb_id_in_cache(release_key, update):
            return True

        self.log.info(f"{release_key}: getting ID from IGDB")

        # Using maxsplit prevents keys like battlenet_hs_beta from hosing us
        platform, platform_key = release_key.split("_", 1)

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
            self.log.debug(f'{release_key}: got IGDB ID {response[0]["game"]}')
        else:
            self.log.debug(f"{release_key}: not found in IGDB")
            return False

        return True

    def get_igdb_id_by_slug(self, release_key, slug, update=False):
        """Gets the IDGB ID for release_key by title. Returns
        True if an ID was found, False if not
        """
        if release_key not in self.cache["igdb"]["games"]:
            self.cache["igdb"]["games"][release_key] = {}
        elif self._igdb_id_in_cache(release_key, update):
            return True

        body = f'fields id,name; where slug = "{slug}";'
        url = "https://api.igdb.com/v4/games"

        response = self.api_request(url, body)

        if response:
            # That gives [{"id": 8104, "name": "blah"}]; id is the igdb id
            self.cache["igdb"]["games"][release_key]["igdb_id"] = response[0]["id"]
            self.log.debug(
                f'{release_key}: got IGDB ID {response[0]["id"]} with slug lookup {slug}'
            )
        else:
            # If we don't get an ID, set it to 0 so we know we've looked this game up before
            self.log.debug(
                f"{release_key}: not found in IGDB with slug lookup {slug}, setting ID to 0"
            )
            self.cache["igdb"]["games"][release_key]["igdb_id"] = 0
            return False

        return True

    def get_multiplayer_info(self, release_key, update=False):
        """Gets the multiplayer info for release_key"""
        if release_key not in self.cache["igdb"]["games"]:
            self.log.error(f"{release_key} not in cache; use get_igdb_id() first")
            return

        elif "max_players" in self.cache["igdb"]["games"][release_key] and not (
            update and self.cache["igdb"]["games"][release_key]["max_players"] == 0
        ):
            self.log.debug(
                "Found max players {} for release key {} in cache".format(
                    self.cache["igdb"]["games"][release_key]["max_players"],
                    release_key,
                )
            )
            return

        elif "igdb_id" not in self.cache["igdb"]["games"][release_key]:
            self.log.error(f"{release_key}: IGDB ID not found, can't get max players")
            return

        elif self.cache["igdb"]["games"][release_key]["igdb_id"] == 0:
            self.log.debug(
                f"{release_key}: IGDB ID is 0, not looking up multiplayer info and setting empty response in cache"
            )
            # Set an empty response so we know we tried to look this up before
            response = []
            self.cache["dirty"] = True

        else:
            self.log.info(f"{release_key}: getting multiplayer info from IGDB")
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

    def _igdb_id_in_cache(self, release_key, update=False):
        """Returns True if the IGDB ID is in the cache"""
        if "igdb_id" not in self.cache["igdb"]["games"][release_key]:
            self.log.debug(f"{release_key}: no IGDB ID in cache")
            return False

        if self.cache["igdb"]["games"][release_key]["igdb_id"] == 0:
            if update:
                self.log.debug(
                    f"{release_key}: IGDB ID is 0 and update is {update}, cache miss"
                )
                return False
            else:
                self.log.debug(
                    f"{release_key}: IGDB ID is 0 and update is {update}, cache hit"
                )
        else:
            self.log.debug(
                f'{release_key}: found IGDB ID {self.cache["igdb"]["games"][release_key]["igdb_id"]} in cache'
            )

        return True
