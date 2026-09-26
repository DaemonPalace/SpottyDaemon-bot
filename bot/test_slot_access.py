"""Self-check for bot/api.py's per-slot access rules. Run from the repo
root: `DISCORD_TOKEN=x python bot/test_slot_access.py` (no Discord or
Spotify needed -- stores are stubbed, and every request stops at the
middleware or at a stub handler)."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISCORD_TOKEN", "x")
os.environ.setdefault("LIBRESPOT_CACHE_DIR", tempfile.mkdtemp())

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import api  # noqa: E402
from slot_store import SlotMetadata, hash_password  # noqa: E402

api.API_TOKEN = None  # a local .env may set one; bearer auth isn't under test here


class StubStore:
    def __init__(self):
        self.slot = SlotMetadata(index=1, state="claimed", friendly_name="alice", password_hash=hash_password("pw"))

    def get_by_name(self, name):
        return self.slot if name == "alice" else None

    def verify_password(self, name, password):
        return False


class StubJam:
    def slot_index_for_token(self, token):
        return 1 if token == "jamtok" else None


async def main():
    store = StubStore()
    diag = api.DiagnosticsApi(None, None, store, None, None, StubJam(), None)

    async def ok(request):
        from aiohttp import web

        return web.json_response({"ok": True})

    # Stub every gated handler out -- only the middleware is under test.
    for attr in (
        "_player_state", "_slot_settings", "_web_api_link_start", "_slot_delete", "_sessions", "_artist_detail",
        "_invites", "_invite_check",
    ):
        setattr(diag, attr, ok)

    async def fake_token(name):
        return "t"

    diag._get_slot_access_token = fake_token

    async with TestClient(TestServer(diag._build_app())) as client:

        async def status(method, path, **headers):
            async with client.request(method, path, headers=headers) as resp:
                return resp.status

        good = api.issue_slot_token(store.slot.password_hash)
        state = "/api/slots/alice/player-state"
        assert await status("GET", state) == 401
        assert await status("GET", state, **{"X-Slot-Token": "garbage"}) == 401
        assert await status("GET", state, **{"X-Slot-Token": good}) == 200
        assert await status("GET", state, **{"X-Slot-Token": "jamtok"}) == 200
        assert await status("GET", state, **{"X-Admin": "1"}) == 200
        # Jam guests: playback only.
        assert await status("POST", "/api/slots/alice/settings", **{"X-Slot-Token": "jamtok"}) == 401
        assert await status("POST", "/api/slots/alice/web-api-link/start", **{"X-Slot-Token": "jamtok"}) == 401
        assert await status("DELETE", "/api/slots/alice", **{"X-Slot-Token": "jamtok"}) == 401
        # Jam guests browse artists; bad search types and non-context URIs are rejected before Spotify.
        assert await status("GET", "/api/slots/alice/artists/x", **{"X-Slot-Token": "jamtok"}) == 200
        assert await status("GET", "/api/slots/alice/search?q=a&type=show", **{"X-Slot-Token": "jamtok"}) == 400
        async with client.post(
            "/api/slots/alice/player/play-context", json={"uri": "spotify:track:x"}, headers={"X-Slot-Token": "jamtok"}
        ) as resp:
            assert resp.status == 400
        # Password change invalidates old sessions.
        store.slot.password_hash = hash_password("new")
        assert await status("GET", state, **{"X-Slot-Token": good}) == 401
        # /select stays open (it IS the password check); /api/sessions is admin-only.
        async with client.post("/api/slots/alice/select", json={"password": "x"}) as resp:
            assert await resp.text() == "wrong password"  # reached the handler, not the middleware
        assert await status("GET", "/api/sessions") == 401
        assert await status("GET", "/api/sessions", **{"X-Admin": "1"}) == 200
        # Invite review is admin-only; an invite link itself is public (even one starting with "s").
        assert await status("GET", "/api/invites") == 401
        assert await status("GET", "/api/invites", **{"X-Admin": "1"}) == 200
        assert await status("POST", "/api/invites/1/approve") == 401
        assert await status("GET", "/api/invite/sometoken") == 200

    print("slot access checks passed")


asyncio.run(main())
