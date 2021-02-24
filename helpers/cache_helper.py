import json
import logging
import os


class Cache(dict):
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.data = {}
        self.dirty = False
        self.log = logging.getLogger(__name__)

        if os.path.exists(self.cache_file):
            self.log.debug(f"Reading cache from file {self.cache_file}")
            with open(self.cache_file, "r") as f:
                self.data = json.load(f)
        else:
            self.log.warning(
                f"Cache file {self.cache_file} not found, making new cache"
            )
        super().__init__()

    def __setitem__(self, k, v):
        self.dirty = True
        super().__setitem__(k, v)

    def __delitem__(self, k):
        self.dirty = True
        super().__delitem__(k)

    def save(self):
        if self.dirty:
            self.log.info("DEBUG: cache is dirty, saving")
            with open(self.cache_file, "w") as f:
                json.dump(self.data, f)

            self.dirty = False
        else:
            self.log.info("DEBUG: cache is clean, not saving")


"""

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


class Cache(dict):
    def __init__(self):
        self.dd = False
        super().__init__()

    def __setitem__(self, k, v):
        self.dd = True
        super().__setitem__(k, v)

    def __delitem__(self, key):
        self.dd = True
        super().__delitem__(key)

    def is_dirty(self):
        return self.dd

    def self_save(self):
        if self.dd:
            print(self)
        else:
            print("NAH")
        if self.dd:
            self.dd = False
"""
