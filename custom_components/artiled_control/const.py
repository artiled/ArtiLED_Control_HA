# Copyright (C) 2026 ArtiLED B.V. All Rights Reserved.
"""Constants for the ArtiCONTROL integration."""

# ⚠ Settled 2026-08-17, never rename: Home Assistant cannot move a config entry
# across domains, so a rename later costs every entity its id, area, aliases
# and voice exposure. Shown to the user as "ArtiCONTROL" (manifest.json).
DOMAIN = "artiled_control"

CONF_TOKEN = "token"
# The setup tick "Let Assist control all rooms" (Michel 2026-10-25, Q1). Applied
# ONCE, at the first setup; a later un-expose by the user stands.
CONF_ASSIST_ALL = "assist_all"
# The setup tick "Create the ArtiCONTROL dashboard" (S2), default on. Created
# once; Configure offers it again (dashboard.py).
CONF_CREATE_DASHBOARD = "create_dashboard"

STORAGE_VERSION = 1

DEFAULT_PORT = 80

# State cycle: /rooms + /zones. Cheap - the webapp asks the same every second.
UPDATE_INTERVAL_SECONDS = 5
# Inventory cycle: /alive + /version + /activities.
INVENTORY_INTERVAL_SECONDS = 60
# Confirmation after every command. A start answers before the Activity
# Service has run it, so nothing is set optimistically: we look again.
CONFIRM_DELAYS = (1, 3)

# Timeouts. A poll must never queue behind a /button, which can take up to 15 s
# (the webapp waits that long too).
POLL_TIMEOUT = 10
COMMAND_TIMEOUT = 15

# The API version that answers G1-G3 (muted, volume null when unknown, one
# /activities call for every unit) and the 2.00 error table. Read from
# /version "api"; a Control without it is older and is served the old way.
API_ALL_ACTIVITIES = (2, 0)

# Every assistant a voice exposure can be set for - the same three as the Hub.
ASSISTANTS = ("conversation", "cloud.alexa", "cloud.google_assistant")

# The Control's own discovery broadcast (artiled_discovery.py):
# "fna349fn|<ident>|<type>|<ip>|<name>" to its own subnet, every 30 s.
DISCOVERY_PORT = 50081
DISCOVERY_MAGIC = "fna349fn"
# Two different addresses claiming one ident within this window: refused.
FOLLOW_AMBIGUITY_SECONDS = 300

PLATFORMS = ["button", "media_player", "remote", "scene", "sensor"]

# The frontend module: the unit tile, the player card, the voice card and the
# dashboard strategy, served from the integration itself (the Hub's way).
# ⚠ NEVER THE HUB'S NAMES. The ArtiLED Hub integration owns /artiled_hub_frontend,
# /artiled_hub_icons and the `artiled` iconset; a second registration of a path
# raises, and a second define() of a tag fails silently (plan 3.2).
# ⚠ The whole directory is served, so everything in frontend/ must be intended
# to be publicly readable. Font Awesome paths are NOT in there - see icons.py.
FRONTEND_BASE = "/artiled_control_frontend"
FRONTEND_DIR = "frontend"
FRONTEND_URL = f"{FRONTEND_BASE}/articontrol.js"

# Font Awesome paths read from the customer's own Control at runtime, cached in
# the CONFIG directory so a HACS upgrade does not discard them. ⚠ Never inside
# the integration directory: the Pro licence does not permit shipping them.
ICON_BASE = "/artiled_control_icons"
ICON_CACHE_DIR = "artiled_control_icons"
ICON_CACHE_FILE = "fa-icons.json"
ICON_URL = f"{ICON_BASE}/{ICON_CACHE_FILE}"

# The glyphs the dashboard's own controls draw (the webapp remote's keys: power,
# transport, volume, mute - app/remote/index.php), fetched with the rooms' and
# activities' icons so the player card looks like the webapp's remote.
CONTROL_GLYPHS = (
    "fa-solid fa-power-off",
    "fa-solid fa-backward-step",
    "fa-solid fa-stop",
    "fa-solid fa-play",
    "fa-solid fa-pause",
    "fa-solid fa-play-pause",
    "fa-solid fa-forward-step",
    "fa-solid fa-volume-low",
    "fa-solid fa-volume-high",
    "fa-solid fa-volume-xmark",
    # S3c: shuffle and repeat between the progress times (app/remote/index.php).
    "fa-solid fa-shuffle",
    "fa-solid fa-repeat",
    # The shortcut row (app/includes/remote.php APP_REMOTE_SHORTCUTS, the
    # Touch's too): Browse and Search, in the light style.
    "fa-light fa-folder-open",
    "fa-light fa-magnifying-glass",
)
