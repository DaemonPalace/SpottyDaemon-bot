"""Signed session cookie for the admin login -- stateless (no server-side
session store), same shape as bot/api.py's _auth_middleware but a cookie
instead of a bearer header, since this is a real browser login flow rather
than a machine-to-machine API token."""

import hmac
import time
from hashlib import sha256

from aiohttp import web

import admin_store

COOKIE_NAME = "admin_session"
SESSION_LIFETIME_SECONDS = 7 * 24 * 3600
# Reissue the cookie if less than this much validity remains, so a session
# in active use never expires mid-use.
REISSUE_THRESHOLD_SECONDS = 24 * 3600

# API paths reachable with no admin session at all: first-run setup, login
# itself, status polling (so the frontend can decide which screen to show
# before anyone's logged in), and jam-mode routes (their own token IS the
# auth, handled separately in proxy.py). Everything that ISN'T under /api/
# is the frontend SPA shell (index.html, JS/CSS assets, client-side routes
# like /setup or /login) -- that must always be servable with no session,
# since the whole point of pages like /setup is to run before a session
# exists. The React app itself decides what to show based on the *data*
# it gets back from these gated API calls, not from whether the HTML
# shell loaded.
_PUBLIC_API_PATH_PREFIXES = (
    "/api/supervisor/setup",
    "/api/supervisor/status",
    "/api/supervisor/login",
    "/api/supervisor/logs",
    "/api/jam/",
)


def _sign(expiry: int) -> str:
    secret = admin_store.get_session_secret()
    mac = hmac.new(secret.encode(), f"admin:{expiry}".encode(), sha256).hexdigest()
    return f"{expiry}.{mac}"


def _verify(cookie_value: str) -> bool:
    try:
        expiry_str, mac = cookie_value.split(".", 1)
        expiry = int(expiry_str)
    except ValueError:
        return False
    if expiry < time.time():
        return False
    secret = admin_store.get_session_secret()
    expected = hmac.new(secret.encode(), f"admin:{expiry}".encode(), sha256).hexdigest()
    return hmac.compare_digest(expected, mac)


def issue_cookie(response: web.StreamResponse) -> None:
    expiry = int(time.time()) + SESSION_LIFETIME_SECONDS
    response.set_cookie(COOKIE_NAME, _sign(expiry), httponly=True, samesite="Lax", max_age=SESSION_LIFETIME_SECONDS)


def clear_cookie(response: web.StreamResponse) -> None:
    response.del_cookie(COOKIE_NAME)


def is_public_path(path: str) -> bool:
    if not path.startswith("/api/"):
        return True  # frontend SPA shell -- always public, see module docstring
    return any(path.startswith(prefix) for prefix in _PUBLIC_API_PATH_PREFIXES)


@web.middleware
async def session_middleware(request: web.Request, handler):
    if is_public_path(request.path):
        return await handler(request)

    cookie_value = request.cookies.get(COOKIE_NAME)
    if not cookie_value or not _verify(cookie_value):
        raise web.HTTPUnauthorized(text="not logged in")

    response = await handler(request)

    # Sliding expiry: reissue if the current cookie is getting close to
    # expiring, so an admin actively using the UI is never logged out.
    expiry = int(cookie_value.split(".", 1)[0])
    if expiry - time.time() < REISSUE_THRESHOLD_SECONDS:
        issue_cookie(response)
    return response
