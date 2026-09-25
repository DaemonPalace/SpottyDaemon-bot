"""Self-check for direct-mode Spotify linking (bot/api.py's
_spotify_callback + finish-link polling). Run from the repo root:
`DISCORD_TOKEN=x python bot/test_spotify_callback.py` (no Discord or Spotify
needed -- the token exchange and stores are stubbed)."""

import asyncio
import os
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISCORD_TOKEN", "x")
os.environ.setdefault("LIBRESPOT_CACHE_DIR", tempfile.mkdtemp())

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import api  # noqa: E402
import config  # noqa: E402
import spotify_web_api  # noqa: E402
from slot_store import SlotMetadata, hash_password  # noqa: E402

api.API_TOKEN = None
api.SPOTIFY_DIRECT_CALLBACK = config.SPOTIFY_DIRECT_CALLBACK = True
config.SPOTIFY_CLIENT_ID = "client"


async def fake_exchange(code, verifier):
    assert code == "good-code"
    return {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}


spotify_web_api.exchange_code = fake_exchange


class StubStore:
    def __init__(self):
        self.slot = SlotMetadata(index=1, state="claimed", friendly_name="alice", password_hash=hash_password("pw"))
        self.saved_token = None

    def get_by_name(self, name):
        return self.slot if name == "alice" else None

    async def set_web_api_token(self, index, token):
        self.saved_token = token


class StubLinks:
    def user_for_state(self, state):
        return None


async def main():
    store = StubStore()
    web_api = spotify_web_api.WebApiLinkManager(store)
    diag = api.DiagnosticsApi(None, None, store, StubLinks(), web_api, None, None)

    async def no_profile(slot_index):
        pass

    diag._seed_profile_info = no_profile
    token = api.issue_slot_token(store.slot.password_hash)

    async with TestClient(TestServer(diag._build_app())) as client:

        async def poll(user_id):
            async with client.post(
                "/api/slots/alice/web-api-link/finish", json={"user_id": user_id}, headers={"X-Slot-Token": token}
            ) as resp:
                return await resp.json()

        async with client.post("/api/slots/alice/web-api-link/start", headers={"X-Slot-Token": token}) as resp:
            started = await resp.json()
        assert started["direct"] is True
        user_id = started["user_id"]
        state = urllib.parse.parse_qs(urllib.parse.urlparse(started["authorize_url"]).query)["state"][0]

        assert (await poll(user_id))["pending"] is True

        async with client.get("/api/spotify/callback", params={"state": "nope", "code": "good-code"}) as resp:
            assert "expired" in await resp.text()

        async with client.get("/api/spotify/callback", params={"state": state, "code": "good-code"}) as resp:
            assert resp.status == 200 and "Spotify linked" in await resp.text()
        assert store.saved_token == "rt"

        result = await poll(user_id)
        assert result["success"] is True and "pending" not in result
        # Consumed -- a second poll finds nothing left to report.
        assert "expired" in (await poll(user_id))["message"]

    print("ok")


asyncio.run(main())
