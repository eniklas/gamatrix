import re

ALPHANUM_PATTERN = re.compile(r"[^\s\w]+")

# Full mapping is at https://api-docs.igdb.com/#external-game-enums,
# but only steam actually works; the other platforms' IDs don't match
# what's in IGDB
IGDB_PLATFORM_ID = {
    "steam": 1,
}
# The mapping of game modes from https://api-docs.igdb.com/#game-mode
IGDB_GAME_MODE = {
    "singleplayer": 1,
    "multiplayer": 2,
    "coop": 3,
    "splitscreen": 4,
    "mmo": 5,
    "battleroyale": 6,
}
# The modes from IGDB_GAME_MODE that we consider multiplayer
IGDB_MULTIPLAYER_GAME_MODES = [
    IGDB_GAME_MODE["multiplayer"],
    IGDB_GAME_MODE["mmo"],
    IGDB_GAME_MODE["battleroyale"],
]
IGDB_MAX_PLAYER_KEYS = [
    "offlinecoopmax",
    "offlinemax",
    "onlinecoopmax",
    "onlinemax",
]
# The API has a rate limit of 4 requests/sec
IGDB_API_CALL_DELAY = 0.25
