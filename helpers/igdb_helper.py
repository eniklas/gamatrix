import json
import logging
import requests
import time


class IGDBHelper:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.logger = logging.getLogger(__name__)
        self.access_token = {}

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
            self.logger.debug("Access token request succeeded, response: f{r.text}")
            self.access_token = r.json()["access_token"]

    def _api_request(self, url, body):
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

        while True:
            r = requests.post(url, headers=headers, data=json.dumps(body))

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


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger()
    client_id = ""
    secret = ""

    igdb = IGDBHelper(client_id, secret)
    # igdb.get_access_token()
    igdb.access_token = ""

    url = "https://api.igdb.com/v4/games"
    # body = {"body": "fields *; where name = Spelunky;"}
    # body = {"body": 'search "Spelunky 2"; fields name,release_date.human;'}
    # body = {"body": "fields *; where id = 70;"}
    body = {"body": "fields *;"}
    print(igdb._api_request(url, body))
