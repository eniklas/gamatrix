"""Static constants shared across the application."""

# Allowed extensions for uploaded GOG Galaxy DBs.
UPLOAD_ALLOWED_EXTENSIONS = ["db"]
UPLOAD_MAX_SIZE = 300 * 1024 * 1024

# Bounds for a user-chosen display name (the `username` field).
DISPLAY_NAME_MAX_LENGTH = 32

# Full mapping is at https://api-docs.igdb.com/#external-game-enums, but only
# Steam and GOG actually work; the other platforms' IDs don't match IGDB.
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

# The modes from IGDB_GAME_MODE that we consider multiplayer.
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

# The IGDB API has a rate limit of 4 requests/sec.
IGDB_API_CALL_DELAY = 0.25

# Order matters; when deduping, the release key retained is the first one in
# the list (we prefer Steam and GOG since IGDB data is most reliable for them).
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

# Enrichment job and per-game enrichment status values.
ENRICHMENT_PENDING = "pending"
ENRICHMENT_RUNNING = "running"
ENRICHMENT_DONE = "done"
ENRICHMENT_NOT_FOUND = "not_found"

JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"

# A pending/running job with no progress for this long is presumed dead (the
# enricher Lambda crashed or hit its hard timeout without recording a terminal
# status). Past this it stops driving the UI. Kept above the enricher's 15-min
# Lambda timeout so a slow-but-live job isn't reaped mid-run.
JOB_TIMEOUT_MINUTES = 20
