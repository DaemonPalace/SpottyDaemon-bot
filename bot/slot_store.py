"""Persists which of the fixed slot indices (bot/config.py's SLOTS) are
claimed, by whom, under what friendly name, and behind what password.

A flat JSON file, not a database -- this repo has no DB anywhere, there are
at most MAX_SLOTS (5) rows, and the bot process is the only writer. See the
plan doc for the reasoning; a JSON file matches the existing
credentials.json/.env-file conventions rather than introducing SQLite or a
network dependency for five rows of state.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass

from config import LIBRESPOT_CACHE_DIR, MAX_SLOTS

log = logging.getLogger("slot_store")

SLOT_METADATA_PATH = os.environ.get(
    "SLOT_METADATA_PATH", os.path.join(LIBRESPOT_CACHE_DIR, "slots.json")
)

# Discord-facing slot name: also used as part of a Lambda modal custom_id and
# as a Spotify Connect-adjacent identifier, so keep it boring and short.
SLOT_NAME_RE = re.compile(r"^[a-z0-9-]{1,32}$")

STATE_FREE = "free"
STATE_LINKING = "linking"
STATE_CLAIMED = "claimed"

_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password_hash(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    expected = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return hmac.compare_digest(expected.hex(), digest_hex)


@dataclass
class SlotMetadata:
    index: int
    state: str = STATE_FREE
    friendly_name: str | None = None
    password_hash: str | None = None
    claimed_by_user_id: str | None = None
    claimed_at: float | None = None
    # Spotify Web API OAuth (bot/spotify_web_api.py) -- independent of the
    # librespot Connect-session credentials.json for this slot. None until
    # /link-web-api completes; wiped by reset() same as everything else.
    web_api_refresh_token: str | None = None
    web_api_linked_at: float | None = None
    # Which Spotify client issued the refresh token -- refreshing needs the
    # same one. None means our own app for this slot (spotify_web_api.
    # app_for_slot); /link stores librespot's client id here.
    web_api_client_id: str | None = None
    # Profile-selector display info. avatar_url defaults to the linked
    # Spotify account's own picture (set once by the web-api-link/finish
    # route) and can be overridden by the slot's own settings screen.
    avatar_url: str | None = None
    spotify_display_name: str | None = None


class SlotStore:
    def __init__(self, max_slots: int = MAX_SLOTS, path: str = SLOT_METADATA_PATH):
        self.max_slots = max_slots
        self.path = path
        self._lock = asyncio.Lock()
        self._slots: dict[int, SlotMetadata] = self._load()

    def _load(self) -> dict[int, SlotMetadata]:
        slots = {i: SlotMetadata(index=i) for i in range(1, self.max_slots + 1)}
        if os.path.exists(self.path):
            with open(self.path) as f:
                raw = json.load(f)
            for index_str, entry in raw.get("slots", {}).items():
                index = int(index_str)
                if index not in slots:
                    continue
                slots[index] = SlotMetadata(
                    index=index,
                    state=entry.get("state", STATE_FREE),
                    friendly_name=entry.get("friendly_name"),
                    password_hash=entry.get("password_hash"),
                    claimed_by_user_id=entry.get("claimed_by_user_id"),
                    claimed_at=entry.get("claimed_at"),
                    web_api_refresh_token=entry.get("web_api_refresh_token"),
                    web_api_linked_at=entry.get("web_api_linked_at"),
                    web_api_client_id=entry.get("web_api_client_id"),
                    avatar_url=entry.get("avatar_url"),
                    spotify_display_name=entry.get("spotify_display_name"),
                )
        # A slot stuck "linking" from a previous process is stale -- the
        # transient OAuth subprocess that would have finished it is gone
        # now that the bot has restarted.
        for slot in slots.values():
            if slot.state == STATE_LINKING:
                log.warning("slot %s was mid-link at startup, resetting to free", slot.index)
                slot.state = STATE_FREE
        return slots

    def _save_locked(self) -> None:
        payload = {
            "slots": {
                str(slot.index): {
                    "state": slot.state,
                    "friendly_name": slot.friendly_name,
                    "password_hash": slot.password_hash,
                    "claimed_by_user_id": slot.claimed_by_user_id,
                    "claimed_at": slot.claimed_at,
                    "web_api_refresh_token": slot.web_api_refresh_token,
                    "web_api_linked_at": slot.web_api_linked_at,
                    "web_api_client_id": slot.web_api_client_id,
                    "avatar_url": slot.avatar_url,
                    "spotify_display_name": slot.spotify_display_name,
                }
                for slot in self._slots.values()
            }
        }
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, self.path)

    def get_by_name(self, name: str) -> SlotMetadata | None:
        for slot in self._slots.values():
            if slot.friendly_name == name:
                return slot
        return None

    def get_by_index(self, index: int) -> SlotMetadata | None:
        return self._slots.get(index)

    def find_free_slot(self) -> SlotMetadata | None:
        for slot in self._slots.values():
            if slot.state == STATE_FREE:
                return slot
        return None

    def claimed_indexes(self) -> set[int]:
        return {s.index for s in self._slots.values() if s.state == STATE_CLAIMED}

    def verify_password(self, name: str, password: str) -> bool:
        slot = self.get_by_name(name)
        if slot is None or slot.password_hash is None:
            return False
        return verify_password_hash(password, slot.password_hash)

    async def set_linking(self, index: int) -> None:
        async with self._lock:
            self._slots[index].state = STATE_LINKING
            self._save_locked()

    async def claim(self, index: int, name: str, password: str, user_id: str) -> None:
        async with self._lock:
            slot = self._slots[index]
            slot.state = STATE_CLAIMED
            slot.friendly_name = name
            slot.password_hash = hash_password(password)
            slot.claimed_by_user_id = user_id
            slot.claimed_at = time.time()
            self._save_locked()

    async def set_web_api_token(self, index: int, refresh_token: str, client_id: str | None = None) -> None:
        async with self._lock:
            slot = self._slots[index]
            slot.web_api_refresh_token = refresh_token
            slot.web_api_client_id = client_id
            slot.web_api_linked_at = time.time()
            self._save_locked()

    async def set_profile_info(self, index: int, avatar_url: str | None, display_name: str | None) -> None:
        """Called once right after web-api-link finishes to seed the
        profile-selector picture/name from the linked Spotify account.
        Never overwrites an avatar the slot owner has since set manually in
        settings -- see update_settings."""
        async with self._lock:
            slot = self._slots[index]
            slot.spotify_display_name = display_name
            if slot.avatar_url is None:
                slot.avatar_url = avatar_url
            self._save_locked()

    async def update_settings(
        self,
        index: int,
        new_name: str | None = None,
        new_password: str | None = None,
        avatar_url: str | None = None,
    ) -> str | None:
        """Applies the slot settings screen's edits. Returns an error
        message on failure (bad name / name taken), or None on success."""
        async with self._lock:
            slot = self._slots[index]
            if new_name is not None and new_name != slot.friendly_name:
                if not SLOT_NAME_RE.match(new_name):
                    return "Name must be 1-32 characters: lowercase letters, numbers, hyphens."
                if any(s.friendly_name == new_name for s in self._slots.values() if s.index != index):
                    return "That name is already taken."
                slot.friendly_name = new_name
            if new_password:
                slot.password_hash = hash_password(new_password)
            if avatar_url is not None:
                slot.avatar_url = avatar_url
            self._save_locked()
            return None

    async def reset(self, index: int) -> None:
        async with self._lock:
            self._slots[index] = SlotMetadata(index=index)
            self._save_locked()
