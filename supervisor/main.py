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


async def _status(request: web.Request) -> web.Response:
    if not admin_store.is_configured() or not env_file.read_env().get("DISCORD_TOKEN"):
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
    app.router.add_post("/api/supervisor/bot/start", _bot_start)
    app.router.add_post("/api/supervisor/bot/stop", _bot_stop)
    app.router.add_post("/api/supervisor/bot/restart", _bot_restart)

    app.router.add_get("/api/jam/{token}/player-state", proxy.jam_player_state)
    app.router.add_post("/api/jam/{token}/queue", proxy.jam_queue_post)
    app.router.add_get("/api/jam/{token}/search", proxy.jam_search)

    # Catch-all: everything else under /api/* is proxied straight through to
    # bot/api.py. auth.session_middleware only gates DELETE /api/slots/*
    # (slot deletion) -- see auth.py's module docstring for why.
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
