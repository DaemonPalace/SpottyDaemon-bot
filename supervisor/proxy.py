"""Forwards browser-facing /api/* requests to bot/api.py, injecting the
shared API_TOKEN bearer server-side so the browser never sees it. Also
forwards the browser's X-Slot-Token and vouches for an admin session with
X-Admin -- see bot/api.py's _slot_access_middleware. Only these headers are
built here, so a browser can't send its own X-Admin through.
"""

import logging
import os

import aiohttp
from aiohttp import web

import auth
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
    if slot_token := request.headers.get("X-Slot-Token"):
        headers["X-Slot-Token"] = slot_token
    if auth.is_admin(request):
        headers["X-Admin"] = "1"
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


