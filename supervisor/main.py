"""Supervisor: the single browser-facing process for Phase 2. Serves the
built React app, owns first-run setup + admin login, and starts/stops/
restarts bot/main.py as a subprocess. See the Phase 2 plan doc for the
full architecture rationale.
"""

import asyncio
import logging
import os

from aiohttp import web

import admin_store
import auth
import env_file
import proxy
from config import FRONTEND_DIST_DIR, SUPERVISOR_HOST, SUPERVISOR_PORT
from process_manager import STATUS_NOT_CONFIGURED, BotProcessManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("supervisor")

bot_process = BotProcessManager()

# (key, secret) -- shown in the admin settings screen (AdminSettingsModal.jsx).
# secret fields never echo their current value back to the client, only
# whether one is set; DEV_GUILD_ID/API_TOKEN/etc. are internal-only and
# stay out of this list on purpose.
SETTINGS_FIELDS = [
    ("DISCORD_TOKEN", True),
    ("SPOTIFY_CLIENT_ID", False),
    ("SPOTIFY_CLIENT_SECRET", True),
    ("SPOTIFY_WEB_API_REDIRECT_URI", False),
    ("PUBLIC_DASHBOARD_URL", False),
    ("MAX_SLOTS", False),
    ("IDLE_SHUTDOWN_MINUTES", False),
    ("ENABLE_AUTO_SHUTDOWN", False),
]
# Changing these needs the bot process restarted to take effect (env vars
# are only read at bot/main.py's startup import time).
RESTART_ON_CHANGE = {"DISCORD_TOKEN", "MAX_SLOTS", "PUBLIC_DASHBOARD_URL"}


async def _status(request: web.Request) -> web.Response:
    # not_configured means "first run, setup wizard hasn't happened yet" --
    # gated on the admin password alone, since that's the one thing only the
    # wizard can create. Gating on DISCORD_TOKEN too used to send an
    # already-set-up install back to the wizard whenever .env's token looked
    # missing (blank on purpose in Secrets-Manager mode, or blanked by an
    # unrelated bug) -- except _setup() itself refuses to run a second time
    # once admin.json exists, so that was a dead end, not a fix path. A
    # missing/bad token now surfaces as crash_looping via bot_process.status()
    # instead (the bot fails to start, same as any other bad credential).
    if not admin_store.is_configured():
        state = STATUS_NOT_CONFIGURED
    else:
        state = await bot_process.status()
    return web.json_response({"state": state})


async def _setup(request: web.Request) -> web.Response:
    if admin_store.is_configured():
        raise web.HTTPBadRequest(text="already set up -- use the login screen")
    body = await request.json()
    discord_token = body.get("discord_token", "").strip()
    admin_password = body.get("admin_password", "")
    if not discord_token:
        raise web.HTTPBadRequest(text="discord_token is required")
    if len(admin_password) < 8:
        raise web.HTTPBadRequest(text="admin_password must be at least 8 characters")

    values = {"DISCORD_TOKEN": discord_token}
    existing = env_file.read_env()
    if not existing.get("API_TOKEN"):
        import secrets

        values["API_TOKEN"] = secrets.token_hex(32)
    env_file.set_env_values(values)

    admin_store.set_password(admin_password)
    bot_process.start()
    return web.json_response({"status": "starting"})


async def _login(request: web.Request) -> web.Response:
    body = await request.json()
    password = body.get("password", "")
    if not admin_store.verify_password(password):
        raise web.HTTPUnauthorized(text="wrong password")
    response = web.json_response({"logged_in": True})
    auth.issue_cookie(response)
    return response


async def _logout(request: web.Request) -> web.Response:
    response = web.json_response({"logged_in": False})
    auth.clear_cookie(response)
    return response


async def _get_settings(request: web.Request) -> web.Response:
    current = env_file.read_env()
    fields = {
        key: {"value": "" if secret else current.get(key, ""), "isSet": bool(current.get(key))}
        for key, secret in SETTINGS_FIELDS
    }
    return web.json_response(fields)


async def _update_settings(request: web.Request) -> web.Response:
    body = await request.json()
    allowed = {key for key, _ in SETTINGS_FIELDS}
    # Blank means "leave as-is" (how a masked secret field round-trips
    # without ever having its real value sent back to the browser) --
    # never used to clear a value.
    values = {k: str(v) for k, v in body.items() if k in allowed and str(v).strip() != ""}
    if not values:
        return web.json_response({"updated": [], "restarted": False})

    env_file.set_env_values(values)

    restarted = False
    if RESTART_ON_CHANGE & values.keys() and admin_store.is_configured():
        await bot_process.restart()
        restarted = True
    return web.json_response({"updated": list(values), "restarted": restarted})


async def _change_admin_password(request: web.Request) -> web.Response:
    body = await request.json()
    new_password = body.get("new_password", "")
    if len(new_password) < 8:
        raise web.HTTPBadRequest(text="new password must be at least 8 characters")
    # No separate "current password" check -- reaching this endpoint at all
    # already required the admin session cookie (auth.py's
    # _is_admin_gated), which only exists because /login already verified
    # it moments ago. Same trust level SlotList.jsx's delete-confirm uses.
    admin_store.set_password(new_password)
    return web.json_response({"changed": True})


async def _logs(request: web.Request) -> web.Response:
    return web.json_response({"lines": list(bot_process.logs)})


async def _bot_start(request: web.Request) -> web.Response:
    bot_process.start()
    return web.json_response({"status": "starting"})


async def _bot_stop(request: web.Request) -> web.Response:
    await bot_process.stop()
    return web.json_response({"status": "stopped"})


async def _bot_restart(request: web.Request) -> web.Response:
    await bot_process.restart()
    return web.json_response({"status": "starting"})


async def _spa_fallback(request: web.Request) -> web.Response:
    index_path = os.path.join(FRONTEND_DIST_DIR, "index.html")
    if not os.path.exists(index_path):
        raise web.HTTPNotFound(text="frontend not built -- run `npm run build` in frontend/")
    # Serve real top-level build files (favicon.svg etc.) as themselves;
    # only client-side routes (/setup, /slots/foo, ...) fall back to the
    # SPA shell.
    requested_path = os.path.normpath(os.path.join(FRONTEND_DIST_DIR, request.match_info["tail"]))
    if requested_path.startswith(FRONTEND_DIST_DIR) and os.path.isfile(requested_path):
        return web.FileResponse(requested_path)
    return web.FileResponse(index_path)


def build_app() -> web.Application:
    app = web.Application(middlewares=[auth.session_middleware])

    app.router.add_get("/api/supervisor/status", _status)
    app.router.add_post("/api/supervisor/setup", _setup)
    app.router.add_post("/api/supervisor/login", _login)
    app.router.add_post("/api/supervisor/logout", _logout)
    app.router.add_get("/api/supervisor/logs", _logs)
    app.router.add_get("/api/supervisor/settings", _get_settings)
    app.router.add_post("/api/supervisor/settings", _update_settings)
    app.router.add_post("/api/supervisor/admin-password", _change_admin_password)
    app.router.add_post("/api/supervisor/bot/start", _bot_start)
    app.router.add_post("/api/supervisor/bot/stop", _bot_stop)
    app.router.add_post("/api/supervisor/bot/restart", _bot_restart)

    # Catch-all: everything else under /api/* is proxied straight through to
    # bot/api.py. auth.session_middleware gates the admin-only routes; the
    # bot itself checks per-slot access (bot/api.py's _slot_access_middleware).
    app.router.add_route("*", "/api/{tail:.*}", proxy.proxy_api)

    if os.path.isdir(FRONTEND_DIST_DIR):
        app.router.add_static("/assets", os.path.join(FRONTEND_DIST_DIR, "assets"))
    app.router.add_get("/{tail:.*}", _spa_fallback)

    return app


async def _main() -> None:
    await bot_process.attach_if_running()
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SUPERVISOR_HOST, SUPERVISOR_PORT)
    await site.start()
    log.info("supervisor listening on http://%s:%d", SUPERVISOR_HOST, SUPERVISOR_PORT)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main())
