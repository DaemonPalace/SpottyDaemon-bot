"""Supervisor's own config -- separate from bot/config.py (different
process), but follows the same os.environ.get(...)-with-default pattern.
Reads the same .env file the bot reads (env_file.py writes it; dotenv here
just picks up whatever's already there on supervisor startup)."""

import os

from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The hosted platform keeps .env on persistent storage (/data on EFS);
# bot/config.py reads the same ENV_PATH.
ENV_PATH = os.environ.get("ENV_PATH", os.path.join(REPO_ROOT, ".env"))
load_dotenv(ENV_PATH)

SUPERVISOR_HOST = os.environ.get("SUPERVISOR_HOST", "127.0.0.1")
SUPERVISOR_PORT = int(os.environ.get("SUPERVISOR_PORT", "8080"))

# Where the bot's own diagnostics/control API lives -- must match
# bot/config.py's API_HOST/API_PORT defaults (kept in sync via the shared
# .env file, not hardcoded twice).
BOT_API_HOST = os.environ.get("API_HOST", "127.0.0.1")
BOT_API_PORT = int(os.environ.get("API_PORT", "8787"))

BOT_DIR = os.path.join(REPO_ROOT, "bot")

# Same directory bot/slot_store.py keeps slots.json in -- admin.json and the
# bot's pidfile live alongside it. Read directly from the env var/default
# rather than importing bot/config.py (keeps the two processes decoupled;
# see supervisor/README-ish module docstrings elsewhere for why).
LIBRESPOT_CACHE_DIR = os.environ.get("LIBRESPOT_CACHE_DIR", "/opt/discord-bot/librespot-cache")
ADMIN_STORE_PATH = os.environ.get("ADMIN_STORE_PATH", os.path.join(LIBRESPOT_CACHE_DIR, "admin.json"))
BOT_PIDFILE_PATH = os.environ.get("BOT_PIDFILE_PATH", os.path.join(LIBRESPOT_CACHE_DIR, "bot.pid"))

FRONTEND_DIST_DIR = os.path.join(REPO_ROOT, "frontend", "dist")

# Hosted platform only -- both unset on a self-host install.
# SETUP_TOKEN_SHA256: sha256 hex of the one-time token in the customer's
# welcome email. When set, first-run setup requires it, so a fresh public
# dashboard can't be claimed by whoever happens to reach it first.
SETUP_TOKEN_SHA256 = os.environ.get("SETUP_TOKEN_SHA256", "").strip().lower()
# LOCKED_SETTINGS: comma-separated .env keys the platform sets itself (as
# task-definition env vars, which .env can't override anyway) -- hidden
# from the admin settings screen and rejected if posted, e.g.
# MAX_SLOTS,IDLE_SHUTDOWN_MINUTES,ENABLE_AUTO_SHUTDOWN,PUBLIC_DASHBOARD_URL,SPOTIFY_WEB_API_REDIRECT_URI
LOCKED_SETTINGS = frozenset(key.strip() for key in os.environ.get("LOCKED_SETTINGS", "").split(",") if key.strip())

# Hosted platform only (tenant.yaml's task env): the bot's own ECS service,
# for the dashboard's admin-only "Update" (POST /api/supervisor/update).
AWS_REGION = os.environ.get("AWS_REGION")
ECS_CLUSTER = os.environ.get("ECS_CLUSTER")
ECS_SERVICE = os.environ.get("ECS_SERVICE")
# Hosted platform only (tenant.yaml's task env): the Interactions Endpoint URL
# the customer pastes into the Discord developer portal, and the tenants row
# where setup saves their app's public key for the interactions Lambda.
INTERACTIONS_ENDPOINT_URL = os.environ.get("INTERACTIONS_ENDPOINT_URL", "")
TENANTS_TABLE = os.environ.get("TENANTS_TABLE")
TENANT_ID = os.environ.get("TENANT_ID")

# Container image only (set in the Dockerfile): start the bot as soon as the
# supervisor comes up, if setup has already happened. Off by default -- a
# self-host install runs the bot as its own systemd unit, and both units
# start at once, so spawning here could race it into a duplicate bot.
AUTOSTART_BOT = os.environ.get("AUTOSTART_BOT", "").lower() in ("1", "true")
