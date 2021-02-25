import json
import logging
import os


class Cache:
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.data = {}
        self.log = logging.getLogger(__name__)

        if os.path.exists(self.cache_file):
            self.log.debug(f"Reading cache file {self.cache_file}")
            with open(self.cache_file, "r") as f:
                self.data = json.load(f)

        else:
            self.log.warning(
                f"Cache file {self.cache_file} not found, making new cache"
            )

        self.data["dirty"] = False

    def save(self):
        if self.data["dirty"]:
            self.log.debug("Cache is dirty, saving")
            with open(self.cache_file, "w") as f:
                json.dump(self.data, f)
        else:
            self.log.debug("Cache is clean, not saving")
