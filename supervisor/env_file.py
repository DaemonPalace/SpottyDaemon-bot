"""Targeted read/write of the shared .env file. Preserves existing lines
(comments, ordering, commented-out defaults from .env.example) -- only the
specific keys being set are touched, everything else passes through
untouched. This is the only thing that lets the supervisor and the bot
subprocess (bot/config.py's own load_dotenv()) agree on config without a
second config file or an RPC call between them.
"""

import os
import re

from config import ENV_PATH


def _key_pattern(key: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)


def read_env() -> dict[str, str]:
    """Best-effort parse of KEY=VALUE lines, ignoring comments/blank lines
    -- good enough for the supervisor's own status checks (does DISCORD_TOKEN
    exist yet), not meant to replace dotenv's own parsing inside the bot."""
    if not os.path.exists(ENV_PATH):
        return {}
    values: dict[str, str] = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _with_spotify_callback(values: dict[str, str]) -> dict[str, str]:
    """Entering the domain also points SPOTIFY_WEB_API_REDIRECT_URI at its
    public callback (bot/api.py's _spotify_callback), which switches the bot
    to direct Spotify linking -- see bot/config.py's SPOTIFY_DIRECT_CALLBACK.
    Normalized the same way bot/config.py reads it back. An explicit
    redirect URI in the same call wins."""
    url = values.get("PUBLIC_DASHBOARD_URL", "").strip().rstrip("/")
    if not url or "SPOTIFY_WEB_API_REDIRECT_URI" in values:
        return values
    if "://" not in url:
        url = f"https://{url}"
    if not url.startswith("https://"):
        # Spotify rejects plain-HTTP redirect URIs except on loopback, so an
        # http://<lan-ip> dashboard keeps the paste-back default.
        return {**values, "PUBLIC_DASHBOARD_URL": url}
    return {**values, "PUBLIC_DASHBOARD_URL": url, "SPOTIFY_WEB_API_REDIRECT_URI": f"{url}/api/spotify/callback"}


def set_env_values(values: dict[str, str]) -> None:
    """Sets each key=value, uncommenting/replacing an existing (possibly
    commented-out) line for that key if one exists, appending a fresh line
    otherwise. Also updates os.environ in-process so this supervisor process
    itself sees the new values immediately (dotenv's own load_dotenv() only
    reads the file once at import time)."""
    values = _with_spotify_callback(values)
    text = ""
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            text = f.read()

    for key, value in values.items():
        line = f"{key}={value}"
        pattern = _key_pattern(key)
        commented_pattern = re.compile(rf"^#\s*{re.escape(key)}=.*$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(line, text, count=1)
        elif commented_pattern.search(text):
            text = commented_pattern.sub(line, text, count=1)
        else:
            if text and not text.endswith("\n"):
                text += "\n"
            text += line + "\n"
        os.environ[key] = value

    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    tmp_path = f"{ENV_PATH}.tmp"
    with open(tmp_path, "w") as f:
        f.write(text)
    os.replace(tmp_path, ENV_PATH)
