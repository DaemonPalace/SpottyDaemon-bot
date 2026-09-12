"""Forwards browser-facing /api/* requests to bot/api.py, injecting the
shared API_TOKEN bearer server-side so the browser never sees it. Also
resolves Jam mode's /jam/<token>/... routes to the underlying slot name
via bot/api.py's by-jam-token lookup, entirely separate from the admin
session cookie (supervisor/auth.py already treats /jam/ as a public path).
"""

import logging
import os

import aiohttp
from aiohttp import web

from config import BOT_API_HOST, BOT_API_PORT

log = logging.getLogger("proxy")

_BOT_API_TOKEN_ENV = "API_TOKEN"


def _bot_api_token() -> str | None:
    return os.environ.get(_BOT_API_TOKEN_ENV)


def _bot_base_url() -> str:
    return f"http://{BOT_API_HOST}:{BOT_API_PORT}"


async def _forward(request: web.Request, path: str) -> web.Response:
    token = _bot_api_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = await request.read() if request.can_read_body else None

    async with aiohttp.ClientSession() as session:
        async with session.request(
            request.method,
            f"{_bot_base_url()}{path}",
            headers=headers,
            data=body,
            params=request.query,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            response_body = await resp.read()
            return web.Response(
                status=resp.status,
                body=response_body,
                content_type=resp.content_type,
            )


async def proxy_api(request: web.Request) -> web.Response:
    """Admin-session-gated (auth.session_middleware already ran by the time
    this handler fires for any /api/* path other than the public ones)."""
    return await _forward(request, request.path)


async def jam_player_state(request: web.Request) -> web.Response:
    slot_name = await _resolve_jam_token(request.match_info["token"])
    return await _forward(request, f"/api/slots/{slot_name}/player-state")


async def jam_queue_post(request: web.Request) -> web.Response:
    slot_name = await _resolve_jam_token(request.match_info["token"])
    return await _forward(request, f"/api/slots/{slot_name}/queue")


async def _resolve_jam_token(token: str) -> str:
    token_resp = await _forward_raw(f"/api/slots/by-jam-token/{token}")
    if token_resp.status != 200:
        raise web.HTTPNotFound(text="invalid or expired jam link")
    import json

    return json.loads(token_resp.body)["name"]


async def _forward_raw(path: str) -> web.Response:
    token = _bot_api_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_bot_base_url()}{path}", headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            body = await resp.read()
            return web.Response(status=resp.status, body=body)
