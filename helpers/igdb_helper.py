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
        self.logger = logging.getLogger(__name__)
        self.access_token = {}
        # https://api-docs.igdb.com/#external-game-enums
        self.platform_id = {
            "steam": 1,
            "gog": 5,
            "xboxone": 11,
        }
        self.cache = {}
        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r") as f:
                self.cache = json.load(f)

    def get_access_token(self):
        url = "https://id.twitch.tv/oauth2/token"
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }
        r = requests.post(url, params=payload)

        if r.status_code != 200:
            self.logger.error(
                "Failed to get access token: {} (status code {})".format(
                    r.text, r.status_code
                )
            )
        elif "access_token" not in r.json():
            self.logger.error(
                "Request succeded, but access_token not found in response: {}".format(
                    r.text
                )
            )
        else:
            self.logger.debug(f"Access token request succeeded, response: {r.text}")
            self.access_token = r.json()["access_token"]

    def api_request(self, url, body):
        """Makes an API request, retrying if the rate limit is hit"""
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

        while True:
            r = requests.post(url, headers=headers, data=body)

            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                self.logger.info("Rate limit exceeded, sleeping 1s")
                time.sleep(1)
                continue
            else:
                self.logger.error(
                    f"Request failed: {r.text} (status code {r.status_code})"
                )
                return {}

    def save_cache(self):
        with open(self.cache_file, "w") as f:
            json.dump(self.cache, f)

    def get_max_players(self, release_key):
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger()
    client_id = ""
    secret = ""

    igdb = IGDBHelper(client_id, secret, ".igdb_cache")
    igdb.access_token = ""
    if not igdb.access_token:
        igdb.get_access_token()

    print(json.dumps(igdb.cache))
    # Show all info on Spelunky
    url = "https://api.igdb.com/v4/games"
    body = 'fields *; where name = "Spelunky";'

    # Get game IDs on other services
    url = "https://api.igdb.com/v4/external_games"
    body = "fields *; where game = 3029;"

    # Get multiplayer info
    url = "https://api.igdb.com/v4/multiplayer_modes"
    body = "fields *; where game = 3029;"

    # Show games matching division (insensitive case)
    url = "https://api.igdb.com/v4/games"
    body = 'fields *; where name ~ *"the division"*;'

    # Get external IDs for any game matching "the division"
    url = "https://api.igdb.com/v4/external_games"
    body = 'fields category,game,name,platform,uid; where name ~ *"the division"*;'

    url = "https://api.igdb.com/v4/games"
    body = 'fields *; where name ~ *"hot pursuit"*;'

    # Get multiplayer info for NFS: Hot Pursuit (2010)
    url = "https://api.igdb.com/v4/multiplayer_modes"
    # body = "fields *; where onlinecoopmax = 32;"
    body = "fields *;"

    url = "https://api.igdb.com/v4/multiplayer_modes"
    body = "fields *; where game = 83341;"
    url = "https://api.igdb.com/v4/games"
    body = "fields *; where id = 83341;"

    url = "https://api.igdb.com/v4/games"
    body = "fields *; where id = 11749;"
    """

    # Getting Doom 2016 max players from steam ID
    release_key = "steam_379720"
    # Get igdb ID from the steam ID; note that uid is a string
    url = "https://api.igdb.com/v4/external_games"
    body = 'fields game; where category = 1 & uid = "379720";'
    # That gives [{"id": 8104, "game": 7351}]; game is the igdb id
    eg_req = igdb.api_request(url, body)
    igdb_id = eg_req[0]["game"]

    url = "https://api.igdb.com/v4/multiplayer_modes"
    # body = "fields *; where game = 7351;"
    body = f"fields *; where game = {igdb_id};"
    mm_req = igdb.api_request(url, body)
    logger.debug(f"mm_req = {mm_req}")
    igdb.cache[release_key] = {
        "igdb_id": igdb_id,
        "max_players": 0,
        "offlinecoopmax": 0,
        "offlinemax": 0,
        "onlinecoopmax": 0,
        "onlinemax": 0,
    }
    for platform in mm_req:
        for k in ["offlinecoopmax", "offlinemax", "onlinecoopmax", "onlinemax"]:
            if k in platform:
                if platform[k] > igdb.cache[release_key]["max_players"]:
                    logger.debug(
                        f"Found new max_players {platform[k]}, platform {platform}, k {k}"
                    )
                    igdb.cache[release_key]["max_players"] = platform[k]
                if platform[k] > igdb.cache[release_key][k]:
                    logger.debug(
                        f"Found new max {platform[k]}, platform {platform}, k {k}"
                    )
                    igdb.cache[release_key][k] = platform[k]
    # That gives the multiplayer data for both xbone one and "all platforms".
    # The all platforms data is junk, loop through all results and take the max you see of:
    # offlinecoopmax
    # offlinemax
    # onlinecoopmax
    # onlinemax
    igdb.save_cache()
    """

    # Look up airborne kingdom, an epic exclusive game
    url = "https://api.igdb.com/v4/games"
    body = 'fields *; where name ~ *"airborne kingdom"*;'
    # That gives "external_games": [1710294, 1746471, 1913099]
    url = "https://api.igdb.com/v4/external_games"
    body = "fields *; where id = 1710294 | id = 1746471 | id = 1913099;"
    # Better way would be to take the id of the first call and do "where game = <game_id>"

    # with open("tmp", "w") as f:
    #     f.write(json.dumps(igdb.api_request(url, body)))
    # body = "fields *; where external_games 379720;"

    """
    alternate names probably not needed
    external games should have the IDs to match (but not origin/uplay?)

    game modes can tell if it's multiplayer, etc.

steam_1234:
    igdb_id: 333
    max_players: 4
    offlinecoopmax
    offlinemax
    onlinecoopmax
    onlinemax

Doom 2016 example:
steam external service category = 1
steam_379720:
    igdb_id:
    """