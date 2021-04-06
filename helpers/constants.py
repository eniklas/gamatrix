# Allowed extensions for uploaded files
UPLOAD_ALLOWED_EXTENSIONS = ["db"]
UPLOAD_MAX_SIZE = 100 * 1024 * 1024

# Full mapping is at https://api-docs.igdb.com/#external-game-enums,
# but only Steam and GOG actually work; the other platforms' IDs
# don't match what's in IGDB
IGDB_PLATFORM_ID = {
    "steam": 1,
    "gog": 5,
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

# Order matters; when deduping, the release key
# retained will be the first one in the list
PLATFORMS = (
    "steam",
    "gog",
    "battlenet",
    "bethesda",
    "epic",
    "origin",
    "uplay",
    "xboxone",
)
