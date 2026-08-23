import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# If set, credentials (currently just DISCORD_TOKEN) are pulled from this AWS
# Secrets Manager secret (a single JSON blob) instead of the environment/.env
# file. .env still supplies the non-secret settings below.
SECRETS_MANAGER_SECRET_ID = os.environ.get("SECRETS_MANAGER_SECRET_ID")


def _load_secrets() -> dict:
    if not SECRETS_MANAGER_SECRET_ID:
        return {}
    import boto3

    client = boto3.client("secretsmanager")
    value = client.get_secret_value(SecretId=SECRETS_MANAGER_SECRET_ID)
    return json.loads(value["SecretString"])


_secrets = _load_secrets()


def _get(key: str) -> str:
    if key in _secrets:
        return _secrets[key]
    return os.environ[key]


DISCORD_TOKEN = _get("DISCORD_TOKEN")
# SQS queue lambda/wake_sleep.py relays interactions to (see interaction_relay.py).
INTERACTIONS_QUEUE_URL = os.environ["INTERACTIONS_QUEUE_URL"]
IDLE_SHUTDOWN_MINUTES = int(os.environ.get("IDLE_SHUTDOWN_MINUTES", "20"))
IDLE_CHECK_INTERVAL_SECONDS = int(os.environ.get("IDLE_CHECK_INTERVAL_SECONDS", "30"))
# Set to "false" to disable self-stopping the EC2 instance (e.g. local testing).
ENABLE_AUTO_SHUTDOWN = os.environ.get("ENABLE_AUTO_SHUTDOWN", "true").lower() == "true"

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
# the slot reverts to free and they have to start over.
LINK_TIMEOUT_SECONDS = int(os.environ.get("LINK_TIMEOUT_SECONDS", "300"))


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
