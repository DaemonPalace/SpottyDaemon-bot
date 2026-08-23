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
# where each slot's OAuth-derived credentials.json lives after the one-time
# interactive `--enable-oauth` bootstrap (see infra/bootstrap-spotify-oauth.sh).
LIBRESPOT_CACHE_DIR = os.environ.get("LIBRESPOT_CACHE_DIR", "/opt/discord-bot/librespot-cache")


@dataclass(frozen=True)
class SpotifySlot:
    name: str
    cache_dir: str
    pipe_path: str


def _slot(index: int) -> SpotifySlot | None:
    prefix = f"SPOTIFY_{index}_"
    cache_dir = os.path.join(LIBRESPOT_CACHE_DIR, f"slot{index}")
    if not os.path.exists(os.path.join(cache_dir, "credentials.json")):
        return None
    return SpotifySlot(
        name=os.environ.get(f"{prefix}DEVICE_NAME", f"discord-bot-{index}"),
        cache_dir=cache_dir,
        pipe_path=os.path.join(PIPE_DIR, f"slot{index}.pcm"),
    )


# Up to 2 slots -> matches the "max 2 simultaneous streams" requirement, but
# only slots that have already completed the OAuth bootstrap get built. Runs
# fine with 1 -- add slot 2's credentials.json whenever it's bootstrapped.
SLOTS = [slot for slot in (_slot(1), _slot(2)) if slot is not None]
if not SLOTS:
    raise RuntimeError(
        f"No Spotify accounts bootstrapped. Run "
        f"infra/bootstrap-spotify-oauth.sh for at least slot 1 so "
        f"{LIBRESPOT_CACHE_DIR}/slot1/credentials.json exists."
    )
