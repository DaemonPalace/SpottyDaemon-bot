import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
IDLE_SHUTDOWN_MINUTES = int(os.environ.get("IDLE_SHUTDOWN_MINUTES", "20"))
IDLE_CHECK_INTERVAL_SECONDS = int(os.environ.get("IDLE_CHECK_INTERVAL_SECONDS", "30"))
# Set to "false" to disable self-stopping the EC2 instance (e.g. local testing).
ENABLE_AUTO_SHUTDOWN = os.environ.get("ENABLE_AUTO_SHUTDOWN", "true").lower() == "true"

LIBRESPOT_BIN = os.environ.get("LIBRESPOT_BIN", "librespot")
PIPE_DIR = os.environ.get("PIPE_DIR", "/tmp/librespot")


@dataclass(frozen=True)
class SpotifySlot:
    name: str
    username: str
    password: str
    pipe_path: str


def _slot(index: int) -> SpotifySlot:
    prefix = f"SPOTIFY_{index}_"
    return SpotifySlot(
        name=os.environ.get(f"{prefix}DEVICE_NAME", f"discord-bot-{index}"),
        username=os.environ[f"{prefix}USERNAME"],
        password=os.environ[f"{prefix}PASSWORD"],
        pipe_path=os.path.join(PIPE_DIR, f"slot{index}.pcm"),
    )


# Exactly 2 slots -> matches the "max 2 simultaneous streams" requirement.
SLOTS = [_slot(1), _slot(2)]
