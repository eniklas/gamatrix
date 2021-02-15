import json
import os


class Cache:
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.cache = {}

        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r") as f:
                self.cache = json.load(f)

    def save(self):
        with open(self.cache_file, "w") as f:
            json.dump(self.cache, f)
