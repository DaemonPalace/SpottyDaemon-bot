import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# If set, credentials (currently just DISCORD_TOKEN) are pulled from this AWS
# Secrets Manager secret (a single JSON blob) instead of the environment/.env
# file. .env still supplies the non-secret settings below.
SECRETS_MANAGER_SECRET_ID = os.environ.get("SECRETS_MANAGER_SECRET_ID")
# botocore doesn't reliably auto-resolve a region under systemd (no
# ~/.aws/config for the service user, and EC2 instance metadata isn't
# always consulted for region the way credentials are) -- pass it
# explicitly rather than depend on ambient SDK config. Falls back to
# wherever this project's own AWS resources actually live.
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-2"))


def _load_secrets() -> dict:
    if not SECRETS_MANAGER_SECRET_ID:
        return {}
    import boto3

    client = boto3.client("secretsmanager", region_name=AWS_REGION)
    value = client.get_secret_value(SecretId=SECRETS_MANAGER_SECRET_ID)
    return json.loads(value["SecretString"])


_secrets = _load_secrets()


def _get(key: str) -> str:
    if key in _secrets:
        return _secrets[key]
    return os.environ[key]


DISCORD_TOKEN = _get("DISCORD_TOKEN")
# SQS queue lambda/wake_sleep.py relays interactions to (see interaction_relay.py).
# Optional: unset means "no Lambda/SQS relay -- run the gateway CommandTree
# directly," the default and only path for a self-hosted/standalone install.
INTERACTIONS_QUEUE_URL = os.environ.get("INTERACTIONS_QUEUE_URL")
IDLE_SHUTDOWN_MINUTES = int(os.environ.get("IDLE_SHUTDOWN_MINUTES", "20"))
IDLE_CHECK_INTERVAL_SECONDS = int(os.environ.get("IDLE_CHECK_INTERVAL_SECONDS", "30"))
# Set to "false" to disable self-stopping the host on idle (e.g. local testing).
ENABLE_AUTO_SHUTDOWN = os.environ.get("ENABLE_AUTO_SHUTDOWN", "true").lower() == "true"
# Which HostController implements "stop the host" (idle shutdown, /sleep):
# "noop" (default, self-host/standalone -- the user turns the app off
# themselves) or "ec2" (legacy AWS deployment, see bot/host_control.py).
HOST_CONTROLLER = os.environ.get("HOST_CONTROLLER", "noop")

# Read-only diagnostics REST API (bot/api.py). Loopback-only by default --
# not exposed off-box unless deliberately rebound. If API_TOKEN is unset the
# API is unauthenticated; fine for local diagnostics/UI use, not for
# exposing beyond localhost.
API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("API_PORT", "8787"))
API_TOKEN = os.environ.get("API_TOKEN")

# Public base URL of the dashboard (e.g. https://music.example.com). When
# set, the /jam panel links guests to a no-password jam dashboard on it
# (bot/jam.py). Unset: no link button, since there's nothing public to
# point at. A bare domain gets https:// prepended.
PUBLIC_DASHBOARD_URL = os.environ.get("PUBLIC_DASHBOARD_URL", "").strip().rstrip("/")
if PUBLIC_DASHBOARD_URL and "://" not in PUBLIC_DASHBOARD_URL:
    PUBLIC_DASHBOARD_URL = f"https://{PUBLIC_DASHBOARD_URL}"

# Optional: a test server's guild ID. When set, slash commands sync to just
# that guild instead of globally -- guild-scoped commands update instantly,
# global ones can take up to an hour to propagate (plus Discord client-side
# caching on top of that). Handy for local dev iteration; leave unset for a
# normal deployment.
DEV_GUILD_ID = os.environ.get("DEV_GUILD_ID")

# Spotify Web API OAuth (bot/spotify_web_api.py) -- a separate Authorization
# Code + PKCE flow against the bot's OWN registered Spotify app, independent
# of librespot's own OAuth client (which only yields a Connect-session
# credentials.json, not a Web-API-usable token). Register an app at
# developer.spotify.com and add SPOTIFY_WEB_API_REDIRECT_URI there.
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
# Optional -- only needed if the app is registered as a confidential client;
# a PKCE public client doesn't require one.
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
# Development-mode Spotify apps only allow a few allowlisted users each, so
# both of the above may be comma-separated lists of several apps (secrets
# positionally matched, blank for a public client: "secret1,,secret3").
# Slot N uses app (N-1) // SPOTIFY_USERS_PER_APP, so slots 1-5 stay on the
# first app. Every app needs SPOTIFY_WEB_API_REDIRECT_URI registered on it,
# and MAX_SLOTS raised to cover the extra slots.
SPOTIFY_USERS_PER_APP = int(os.environ.get("SPOTIFY_USERS_PER_APP", "5"))
SPOTIFY_APPS = [
    (client_id.strip(), secret.strip() or None)
    for client_id, secret in zip(
        (SPOTIFY_CLIENT_ID or "").split(","),
        (SPOTIFY_CLIENT_SECRET or "").split(",") + [""] * (SPOTIFY_CLIENT_ID or "").count(","),
    )
    if client_id.strip()
]
SPOTIFY_WEB_API_REDIRECT_URI = os.environ.get("SPOTIFY_WEB_API_REDIRECT_URI") or (
    # Spotify only allows plain HTTP on loopback, so an http:// LAN dashboard
    # can't be the callback -- keep the paste-back default for it.
    f"{PUBLIC_DASHBOARD_URL}/api/spotify/callback"
    if PUBLIC_DASHBOARD_URL.startswith("https://")
    else "http://127.0.0.1:5589/callback"
)
# Direct linking: Spotify redirects the browser straight back to this bot's
# public callback (bot/api.py's _spotify_callback), so neither /link nor
# /link-web-api needs the copy-the-failed-url-back step. Needs our own
# Spotify app (SPOTIFY_CLIENT_ID) with the callback registered on it -- the
# supervisor/install.sh set SPOTIFY_WEB_API_REDIRECT_URI to it whenever the
# domain is entered. Anything else keeps the paste-back flow.
SPOTIFY_DIRECT_CALLBACK = bool(
    SPOTIFY_CLIENT_ID
    and PUBLIC_DASHBOARD_URL
    and SPOTIFY_WEB_API_REDIRECT_URI == f"{PUBLIC_DASHBOARD_URL}/api/spotify/callback"
)

LIBRESPOT_BIN = os.environ.get("LIBRESPOT_BIN", "librespot")
PIPE_DIR = os.environ.get("PIPE_DIR", "/tmp/librespot")
# Must be persistent storage, NOT /tmp (tmpfs, wiped on reboot) -- this is
# where each slot's OAuth-derived credentials.json lives after linking (see
# bot/spotify_link.py), and where slots.json (bot/slot_store.py) is kept.
LIBRESPOT_CACHE_DIR = os.environ.get("LIBRESPOT_CACHE_DIR", "/opt/discord-bot/librespot-cache")

# Up to this many self-serve Spotify slots. Slots start unclaimed -- /link
# claims one, /delete-slot frees it back up. See bot/slot_store.py.
MAX_SLOTS = int(os.environ.get("MAX_SLOTS", "5"))

# Local OAuth redirect port librespot's --enable-oauth binds while a /link is
# in progress (bot/spotify_link.py). Only ever one transient login process at
# a time per slot, and librespot's OAuth client only accepts loopback
# redirect URIs anyway (see bootstrap-spotify-oauth.sh / spotify_link.py
# module docstring) so this never needs to be reachable from outside the box.
LINK_OAUTH_PORT = int(os.environ.get("LINK_OAUTH_PORT", "5588"))
# How long a friend has between /link and finishing with /link-finish before
# the slot reverts to free and they have to start over. Generous on purpose
# -- this covers actually reading the instructions, opening Spotify, logging
# in (possibly through 2FA), and coming back to paste the url.
LINK_TIMEOUT_SECONDS = int(os.environ.get("LINK_TIMEOUT_SECONDS", "900"))


@dataclass(frozen=True)
class SpotifySlot:
    index: int
    name: str
    cache_dir: str
    pipe_path: str


def _slot(index: int) -> SpotifySlot:
    prefix = f"SPOTIFY_{index}_"
    return SpotifySlot(
        index=index,
        name=os.environ.get(f"{prefix}DEVICE_NAME", f"discord-bot-{index}"),
        cache_dir=os.path.join(LIBRESPOT_CACHE_DIR, f"slot{index}"),
        pipe_path=os.path.join(PIPE_DIR, f"slot{index}.pcm"),
    )


# All possible slots, regardless of whether anyone has linked an account to
# them yet -- bot/slot_store.py tracks which ones are actually claimed.
SLOTS = [_slot(index) for index in range(1, MAX_SLOTS + 1)]
SLOTS_BY_INDEX = {slot.index: slot for slot in SLOTS}
