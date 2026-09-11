"""Admin password + session-signing secret, stored in admin.json beside
slots.json. Password hashing reuses bot/slot_store.py's PBKDF2 functions
directly -- no separate hashing scheme for "the same kind of secret,"
stored in a different file for a different actor (there's exactly one
admin, vs. up to MAX_SLOTS slot passwords)."""

import json
import os
import secrets
import sys

from config import ADMIN_STORE_PATH

# bot/ isn't a package (no __init__.py, matches how bot/main.py imports its
# siblings) -- add it to sys.path so hash_password/verify_password_hash can
# be imported directly rather than reimplemented. bot/slot_store.py does its
# own `from config import ...`, and this process ALSO has a top-level
# "config" module (this file's own supervisor/config.py, already imported)
# -- sys.modules caches by bare name, so without swapping it out here,
# slot_store would silently resolve "config" to supervisor's config.py
# (missing MAX_SLOTS/etc.) instead of bot/config.py, and fail to import.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot"))
_saved_supervisor_config = sys.modules.pop("config", None)
try:
    from slot_store import hash_password, verify_password_hash  # noqa: E402
finally:
    if _saved_supervisor_config is not None:
        sys.modules["config"] = _saved_supervisor_config


def is_configured() -> bool:
    return os.path.exists(ADMIN_STORE_PATH)


def _load() -> dict:
    with open(ADMIN_STORE_PATH) as f:
        return json.load(f)


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(ADMIN_STORE_PATH), exist_ok=True)
    tmp_path = f"{ADMIN_STORE_PATH}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, ADMIN_STORE_PATH)


def set_password(password: str) -> None:
    """Called once during first-run setup. Generates the session secret at
    the same time if this is the very first setup."""
    data = _load() if is_configured() else {}
    data["password_hash"] = hash_password(password)
    if "session_secret" not in data:
        data["session_secret"] = secrets.token_hex(32)
    _save(data)


def verify_password(password: str) -> bool:
    if not is_configured():
        return False
    return verify_password_hash(password, _load()["password_hash"])


def get_session_secret() -> str:
    return _load()["session_secret"]
