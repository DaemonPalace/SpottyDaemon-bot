"""Supervisor: the single browser-facing process for Phase 2. Serves the
built React app, owns first-run setup + admin login, and starts/stops/
restarts bot/main.py as a subprocess. See the Phase 2 plan doc for the
full architecture rationale.
"""

import asyncio
import hmac
import logging
import os
import re
from hashlib import sha256

from aiohttp import web

import admin_store
import auth
import env_file
import proxy
from config import (
    AUTOSTART_BOT,
    AWS_REGION,
    ECS_CLUSTER,
    ECS_SERVICE,
    FRONTEND_DIST_DIR,
    INTERACTIONS_ENDPOINT_URL,
    LOCKED_SETTINGS,
    SETUP_TOKEN_SHA256,
    SUPERVISOR_HOST,
    SUPERVISOR_PORT,
    TENANT_ID,
    TENANTS_TABLE,
)
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
# Minus whatever the hosted platform manages itself (config.LOCKED_SETTINGS).
EDITABLE_SETTINGS = [(key, secret) for key, secret in SETTINGS_FIELDS if key not in LOCKED_SETTINGS]
# Changing these needs the bot process restarted to take effect (env vars
# are only read at bot/main.py's startup import time).
RESTART_ON_CHANGE = {
    "DISCORD_TOKEN",
    "MAX_SLOTS",
    "PUBLIC_DASHBOARD_URL",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
    "SPOTIFY_WEB_API_REDIRECT_URI",
}


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
    # Hosted only: the URL the setup page and admin settings show for the
    # Discord developer portal. Not a secret: Discord checks every request's
    # signature against the app's public key.
    return web.json_response({"state": state, "interactions_endpoint_url": INTERACTIONS_ENDPOINT_URL or None})


async def _save_discord_public_key(key: str) -> None:
    """Hosted only: the interactions Lambda checks Discord's signatures
    against this (the app's General Information page). The bot also saves it
    itself on startup (bot/interaction_relay.py's register_endpoint)."""
    import boto3

    table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TENANTS_TABLE)
    await asyncio.to_thread(
        table.update_item,
        Key={"tenant": TENANT_ID},
        UpdateExpression="SET discord_public_key = :k",
        ConditionExpression="attribute_exists(tenant)",
        ExpressionAttributeValues={":k": key},
    )


async def _setup(request: web.Request) -> web.Response:
    if admin_store.is_configured():
        raise web.HTTPBadRequest(text="already set up -- use the login screen")
    body = await request.json()
    if SETUP_TOKEN_SHA256:
        given = sha256(str(body.get("setup_token", "")).encode()).hexdigest()
        if not hmac.compare_digest(given, SETUP_TOKEN_SHA256):
            raise web.HTTPForbidden(text="Open this page from the setup link in your welcome email.")
    discord_token = body.get("discord_token", "").strip()
    admin_password = body.get("admin_password", "")
    if not discord_token:
        raise web.HTTPBadRequest(text="discord_token is required")
    if len(admin_password) < 8:
        raise web.HTTPBadRequest(text="admin_password must be at least 8 characters")
    if INTERACTIONS_ENDPOINT_URL and TENANTS_TABLE and TENANT_ID:
        public_key = str(body.get("discord_public_key", "")).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", public_key):
            raise web.HTTPBadRequest(
                text="The public key is 64 letters and numbers, from your app's General Information page."
            )
        await _save_discord_public_key(public_key)

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
    if admin_store.is_locked():
        raise web.HTTPTooManyRequests(text="Too many wrong passwords -- try again in 15 minutes.")
    if not admin_store.verify_password(password):
        raise web.HTTPUnauthorized(text="wrong password")
    response = web.json_response({"logged_in": True})
    auth.issue_cookie(request, response)
    return response


async def _logout(request: web.Request) -> web.Response:
    response = web.json_response({"logged_in": False})
    auth.clear_cookie(response)
    return response


async def _get_settings(request: web.Request) -> web.Response:
    current = env_file.read_env()
    fields = {
        key: {"value": "" if secret else current.get(key, ""), "isSet": bool(current.get(key))}
        for key, secret in EDITABLE_SETTINGS
    }
    fields["SPOTIFY_APPS"] = {"value": [{"clientId": cid, "secretSet": bool(sec)} for cid, sec in _spotify_apps(current)]}
    return web.json_response(fields)


def _spotify_apps(env: dict[str, str]) -> list[tuple[str, str]]:
    """SPOTIFY_CLIENT_ID/SECRET as the (id, secret) pairs bot/config.py's
    SPOTIFY_APPS reads them back as -- comma-separated, secrets positional."""
    ids = env.get("SPOTIFY_CLIENT_ID", "").split(",")
    secret_list = env.get("SPOTIFY_CLIENT_SECRET", "").split(",")
    return [(cid.strip(), (secret_list[i] if i < len(secret_list) else "").strip()) for i, cid in enumerate(ids) if cid.strip()]


def _spotify_app_values(apps: list[dict], env: dict[str, str]) -> dict[str, str]:
    """The settings screen's app list, back to .env values. A blank secret
    keeps whatever secret that client id already had, so masked secrets
    round-trip without ever being sent to the browser."""
    old = dict(_spotify_apps(env))
    pairs = [
        (cid, str(app.get("secret") or "").strip() or old.get(cid, ""))
        for app in apps
        if (cid := str(app.get("clientId") or "").strip())
    ]
    if any("," in cid + sec for cid, sec in pairs):
        raise web.HTTPBadRequest(text="client ids and secrets can't contain commas")
    return {
        "SPOTIFY_CLIENT_ID": ",".join(cid for cid, _ in pairs),
        "SPOTIFY_CLIENT_SECRET": ",".join(sec for _, sec in pairs),
    }


async def _update_settings(request: web.Request) -> web.Response:
    body = await request.json()
    allowed = {key for key, _ in EDITABLE_SETTINGS}
    # Blank means "leave as-is" (how a masked secret field round-trips
    # without ever having its real value sent back to the browser) --
    # never used to clear a value.
    values = {k: str(v) for k, v in body.items() if k in allowed and str(v).strip() != ""}
    if isinstance(body.get("SPOTIFY_APPS"), list):
        values.update(_spotify_app_values(body["SPOTIFY_APPS"], env_file.read_env()))
    if not values:
        return web.json_response({"updated": [], "restarted": False})

    env_file.set_env_values(values)

    restarted = False
    # set_env_values may also have set SPOTIFY_WEB_API_REDIRECT_URI -- but
    # only alongside PUBLIC_DASHBOARD_URL, which restarts anyway.
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
    # set_password rotated the session secret, logging out every other
    # session -- re-sign this one so the admin who just changed it stays in.
    response = web.json_response({"changed": True})
    auth.issue_cookie(request, response)
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


async def _update(request: web.Request) -> web.Response:
    """Hosted only: restart this bot on a new ECS deployment, which pulls
    whatever its image tag (:latest) points at now. The dashboard offers it
    when GET /_platform/update (the platform Lambda) says there's a newer
    image. About a minute of downtime; the task running this request is
    stopped once the new one is up."""
    if not (ECS_CLUSTER and ECS_SERVICE):
        raise web.HTTPNotFound(text="updates are only available on the hosted platform")
    import boto3

    client = boto3.client("ecs", region_name=AWS_REGION)
    await asyncio.to_thread(client.update_service, cluster=ECS_CLUSTER, service=ECS_SERVICE, forceNewDeployment=True)
    return web.json_response({"status": "updating"})


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
    # no-cache: the shell names the current hashed JS bundle. A cached copy
    # keeps running the previous build after an update (old bundles stay on
    # disk -- update.sh's rsync doesn't delete), talking to the new backend.
    return web.FileResponse(index_path, headers={"Cache-Control": "no-cache"})


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
    app.router.add_post("/api/supervisor/update", _update)

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
    if AUTOSTART_BOT and admin_store.is_configured():
        bot_process.start()
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, SUPERVISOR_HOST, SUPERVISOR_PORT)
    await site.start()
    log.info("supervisor listening on http://%s:%d", SUPERVISOR_HOST, SUPERVISOR_PORT)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main())
