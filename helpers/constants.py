import re

ALPHANUM_PATTERN = re.compile("[^\s\w]+")

# Full mapping is at https://api-docs.igdb.com/#external-game-enums,
# but only steam actually works; the other platforms' IDs don't match
# what's in IGDB
IGDB_PLATFORM_ID = {
    "steam": 1,
}
IGDB_GAME_MODE = {
    "singleplayer": 1,
    "multiplayer": 2,
    "coop": 3,
    "splitscreen": 4,
    "mmo": 5,
    "battleroyale": 6,
}
IGDB_MAX_PLAYER_KEYS = [
    "offlinecoopmax",
    "offlinemax",
    "onlinecoopmax",
    "onlinemax",
]
# The API has a rate limit of 4 requests/sec
IGDB_API_CALL_DELAY = 0.25
