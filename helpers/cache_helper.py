import json
import logging
import os


class Cache:
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.data = {}
        self.data["dirty"] = False
        self.log = logging.getLogger(__name__)

        if os.path.exists(self.cache_file):
            self.log.debug(f"Reading cache file {self.cache_file}")
            with open(self.cache_file, "r") as f:
                self.data = json.load(f)
        else:
            self.log.debug(f"Cache file {self.cache_file} not found, making new cache")

    def save(self):
        if self.data["dirty"]:
            self.log.debug(f"Cache is dirty, saving")
            # The dirty status is itself part of the cache,
            # so we need to mark it clean before saving it
            self.data["dirty"] = False
            with open(self.cache_file, "w") as f:
                json.dump(self.data, f)
        else:
            self.log.debug(f"Cache is clean, not saving")
