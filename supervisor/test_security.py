"""Self-check for the supervisor's hosted-mode protections: the one-time
setup token, platform-locked settings, the admin login throttle, session
rotation on password change, and admin-only bot stop/restart. Run from the
repo root: `python supervisor/test_security.py` (no Discord needed -- the
bot process is stubbed)."""

import asyncio
import os
import tempfile
from hashlib import sha256

_tmp = tempfile.mkdtemp()
os.environ.update(
    {
        "DISCORD_TOKEN": "x",
        "ENV_PATH": os.path.join(_tmp, ".env"),
        "LIBRESPOT_CACHE_DIR": _tmp,
        "SETUP_TOKEN_SHA256": sha256(b"setup-secret").hexdigest(),
        "LOCKED_SETTINGS": "MAX_SLOTS,ENABLE_AUTO_SHUTDOWN",
        "INTERACTIONS_ENDPOINT_URL": "https://lambda.example/t/alpha",
        "TENANTS_TABLE": "tenants",
        "TENANT_ID": "alpha",
    }
)

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import main  # noqa: E402


class StubBot:
    def start(self):
        pass

    async def restart(self):
        pass

    async def stop(self):
        pass

    async def status(self):
        return "running"


main.bot_process = StubBot()
saved_keys = []


async def _save_key(key):
    saved_keys.append(key)


main._save_discord_public_key = _save_key


async def run():
    async with TestClient(TestServer(main.build_app())) as client:

        async def post(path, body=None, **headers):
            async with client.post(path, json=body or {}, headers=headers) as resp:
                return resp.status

        setup = {"discord_token": "tok", "admin_password": "password1"}
        assert await post("/api/supervisor/setup", setup) == 403
        assert await post("/api/supervisor/setup", {**setup, "setup_token": "guess"}) == 403
        # Hosted setup also needs the app's public key, saved for the Lambda.
        assert await post("/api/supervisor/setup", {**setup, "setup_token": "setup-secret"}) == 400
        key = "AB" * 32
        setup["discord_public_key"] = key
        assert await post("/api/supervisor/setup", {**setup, "setup_token": "setup-secret"}) == 200
        assert saved_keys == [key.lower()]

        assert await post("/api/supervisor/bot/start") == 200
        assert await post("/api/supervisor/bot/stop") == 401
        assert await post("/api/supervisor/bot/restart") == 401
        assert await post("/api/supervisor/update") == 401

        assert await post("/api/supervisor/login", {"password": "password1"}) == 200
        old_cookie = client.session.cookie_jar.filter_cookies(client.make_url("/"))["admin_session"].value

        async with client.get("/api/supervisor/settings") as resp:
            fields = await resp.json()
        assert "MAX_SLOTS" not in fields and "ENABLE_AUTO_SHUTDOWN" not in fields and "IDLE_SHUTDOWN_MINUTES" in fields
        async with client.post("/api/supervisor/settings", json={"MAX_SLOTS": "50", "ENABLE_AUTO_SHUTDOWN": "false"}) as resp:
            assert (await resp.json())["updated"] == []
        assert "MAX_SLOTS" not in main.env_file.read_env()
        assert await post("/api/supervisor/bot/stop") == 200

        # Changing the password keeps this session but kills older cookies.
        assert await post("/api/supervisor/admin-password", {"new_password": "password2"}) == 200
        assert await post("/api/supervisor/bot/stop") == 200
        client.session.cookie_jar.clear()
        assert await post("/api/supervisor/bot/stop", Cookie=f"admin_session={old_cookie}") == 401

        # Login throttle: 10 misses lock out even the right password.
        for _ in range(10):
            assert await post("/api/supervisor/login", {"password": "nope"}) == 401
        assert await post("/api/supervisor/login", {"password": "password2"}) == 429
    print("supervisor security checks passed")


asyncio.run(run())
