import json
import logging
import os


class Cache:
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.data: CacheDict = CacheDict()
        self.data.dirty: bool = False
        self.log = logging.getLogger(__name__)
        self.log.info(f"Debug: self.data.dirty = {self.data.dirty}")

        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r") as f:
                self.data = json.load(f)

    def save(self):
        if self.data.dirty == True:
            self.log.info("DEBUG: cache is dirty, saving")
            with open(self.cache_file, "w") as f:
                json.dump(self.data, f)

            self.data.dirty = False


# Use this for the cache so we can mark it dirty on any update
class CacheDict(dict):
    def __init__(self):
        self.dirty = False

    def __setitem__(self, k, v) -> None:
        self.dirty = True
        super(CacheDict, self).__setitem__(k, v)

    def __delitem__(self, v) -> None:
        self.dirty = True
        return super().__delitem__(v)
