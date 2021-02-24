import json
import os


class Cache:
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.data = CacheDict()

        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r") as f:
                self.data = json.load(f)

    def save(self):
        with open(self.cache_file, "w") as f:
            json.dump(self.data, f)
        self.data.dirty = False


# Use this for the cache so we can mark it dirty on any update
class CacheDict(dict):
    def __init__(self):
        self.dirty = False

    def __setitem__(self, k, v):
        self.dirty = True
        super(CacheDict, self).__setitem__(k, v)
