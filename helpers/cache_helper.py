import json
import os


class Cache:
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.data = {}

        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r") as f:
                self.data = json.load(f)

    def save(self):
        with open(self.cache_file, "w") as f:
            json.dump(self.data, f)
