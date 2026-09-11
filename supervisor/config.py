"""Supervisor's own config -- separate from bot/config.py (different
process), but follows the same os.environ.get(...)-with-default pattern.
Reads the same .env file the bot reads (env_file.py writes it; dotenv here
just picks up whatever's already there on supervisor startup)."""

import os

from dotenv import load_dotenv

load_dotenv()

SUPERVISOR_HOST = os.environ.get("SUPERVISOR_HOST", "127.0.0.1")
SUPERVISOR_PORT = int(os.environ.get("SUPERVISOR_PORT", "8080"))

# Where the bot's own diagnostics/control API lives -- must match
# bot/config.py's API_HOST/API_PORT defaults (kept in sync via the shared
# .env file, not hardcoded twice).
BOT_API_HOST = os.environ.get("API_HOST", "127.0.0.1")
BOT_API_PORT = int(os.environ.get("API_PORT", "8787"))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_DIR = os.path.join(REPO_ROOT, "bot")
ENV_PATH = os.path.join(REPO_ROOT, ".env")

# Same directory bot/slot_store.py keeps slots.json in -- admin.json and the
# bot's pidfile live alongside it. Read directly from the env var/default
# rather than importing bot/config.py (keeps the two processes decoupled;
# see supervisor/README-ish module docstrings elsewhere for why).
LIBRESPOT_CACHE_DIR = os.environ.get("LIBRESPOT_CACHE_DIR", "/opt/discord-bot/librespot-cache")
ADMIN_STORE_PATH = os.environ.get("ADMIN_STORE_PATH", os.path.join(LIBRESPOT_CACHE_DIR, "admin.json"))
BOT_PIDFILE_PATH = os.environ.get("BOT_PIDFILE_PATH", os.path.join(LIBRESPOT_CACHE_DIR, "bot.pid"))

FRONTEND_DIST_DIR = os.path.join(REPO_ROOT, "frontend", "dist")
