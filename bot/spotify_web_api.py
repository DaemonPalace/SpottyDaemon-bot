"""Spotify Web API access token management, per slot.

Independent of librespot's own OAuth (bot/spotify_link.py) -- that flow only
yields librespot's internal Connect-session credentials.json, with scopes
controlled entirely by librespot's own registered OAuth client. There is no
guarantee (and today, no evidence) that it covers Web API scopes like
user-modify-playback-state, so this runs a standard Authorization Code +
PKCE flow against the bot's OWN registered Spotify app instead.

UX mirrors /link's "paste the failed redirect back" shape (see
spotify_link.py's module docstring for why: no public HTTPS endpoint is
assumed), but the mechanics are simpler here -- there's no local subprocess
to replay the request to. The bot itself calls Spotify's token endpoint
directly over aiohttp.

A real listen-and-catch-the-redirect flow (no paste-back step) is the
natural upgrade once there's a real local HTTP server to receive it (see the
Phase 4 standalone app) -- out of scope while this is still Discord-command
driven.
"""

import base64
import hashlib
import logging
import secrets
import time
import urllib.parse
from dataclasses import dataclass

import aiohttp

import config
from slot_store import SlotStore

log = logging.getLogger("spotify_web_api")

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

# user-modify-playback-state covers /me/player/queue add plus play/pause/skip/
# seek/volume; user-read-currently-playing + user-read-playback-state cover
# now-playing; user-library-read covers the dashboard's "your library"
# saved-albums browser; user-read-recently-played and playlist-read-private
# cover the dashboard's playlists/recently-played sections; user-read-private
# covers the profile picture shown in the profile selector. A slot linked
# before one of these was added needs to re-run /link-web-api to pick it up --
# see spotify_player_api.get_saved_albums's None return for how that's surfaced.
SCOPES = (
    "user-read-playback-state user-modify-playback-state user-read-currently-playing "
    "user-library-read user-read-recently-played playlist-read-private user-read-private"
)

PENDING_TIMEOUT_SECONDS = 600
# Refresh a bit early so a token in active use doesn't expire mid-request.
_EXPIRY_SAFETY_MARGIN_SECONDS = 60


def _generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _build_authorize_url(state: str, code_challenge: str) -> str:
    params = {
        "client_id": config.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.SPOTIFY_WEB_API_REDIRECT_URI,
        "state": state,
        "scope": SCOPES,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


async def _exchange_code(code: str, code_verifier: str) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.SPOTIFY_WEB_API_REDIRECT_URI,
        "client_id": config.SPOTIFY_CLIENT_ID,
        "code_verifier": code_verifier,
    }
    if config.SPOTIFY_CLIENT_SECRET:
        data["client_secret"] = config.SPOTIFY_CLIENT_SECRET
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, data=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            body = await resp.json()
            if resp.status >= 300:
                raise RuntimeError(f"token exchange failed ({resp.status}): {body}")
            return body


async def _refresh(refresh_token: str) -> dict:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": config.SPOTIFY_CLIENT_ID,
    }
    if config.SPOTIFY_CLIENT_SECRET:
        data["client_secret"] = config.SPOTIFY_CLIENT_SECRET
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, data=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            body = await resp.json()
            if resp.status >= 300:
                raise RuntimeError(f"token refresh failed ({resp.status}): {body}")
            return body


@dataclass
class _PendingWebApiLink:
    slot_index: int
    code_verifier: str
    state: str
    started_at: float


@dataclass
class _CachedAccessToken:
    access_token: str
    expires_at: float


class WebApiLinkManager:
    """Drives /link-web-api and /link-web-api-finish. Holds the transient
    PKCE state between the two commands; persistence of the resulting
    refresh token happens through SlotStore, same as everything else about a
    slot."""

    def __init__(self, store: SlotStore):
        self.store = store
        self._pending: dict[str, _PendingWebApiLink] = {}
        self._cache: dict[int, _CachedAccessToken] = {}

    def pending_slot_index(self, user_id: str) -> int | None:
        """For callers (bot/api.py) that need the slot a pending link will
        claim before calling finish_link, which pops the pending entry."""
        pending = self._pending.get(user_id)
        return pending.slot_index if pending is not None else None

    def start_link(self, user_id: str, slot_index: int) -> tuple[str, bool]:
        if not config.SPOTIFY_CLIENT_ID:
            return (
                "Spotify Web API isn't configured on this bot -- SPOTIFY_CLIENT_ID is unset. "
                "Ask the bot owner to register an app at developer.spotify.com.",
                False,
            )
        verifier, challenge = _generate_pkce_pair()
        state = secrets.token_urlsafe(16)
        self._pending[user_id] = _PendingWebApiLink(
            slot_index=slot_index, code_verifier=verifier, state=state, started_at=time.monotonic()
        )
        url = _build_authorize_url(state, challenge)
        return (
            f"**Step 1:** open this link and log in with Spotify:\n{url}\n\n"
            "**Step 2:** your browser will fail to load the page it redirects to -- "
            "that's expected. Copy the FULL url from your browser's address bar.\n\n"
            f"**Step 3:** run `/link-web-api-finish` and paste that url in, within "
            f"{PENDING_TIMEOUT_SECONDS // 60} minutes.",
            True,
        )

    async def finish_link(self, user_id: str, pasted_url: str) -> tuple[str, bool]:
        pending = self._pending.get(user_id)
        if pending is None:
            return "No Web API link in progress. Run /link-web-api first.", False

        if time.monotonic() - pending.started_at > PENDING_TIMEOUT_SECONDS:
            self._pending.pop(user_id, None)
            return "That login session expired -- run /link-web-api again.", False

        query = urllib.parse.urlparse(pasted_url.strip()).query
        params = urllib.parse.parse_qs(query)
        code = params.get("code", [None])[0]
        returned_state = params.get("state", [None])[0]

        if not code or not returned_state:
            return (
                "That doesn't look like the right url -- make sure you copied the FULL address "
                "from your browser's address bar, including the `?code=...` part.",
                False,
            )
        if returned_state != pending.state:
            self._pending.pop(user_id, None)
            return "That link doesn't match your pending request -- run /link-web-api again.", False

        try:
            token_response = await _exchange_code(code, pending.code_verifier)
        except Exception:
            log.exception("web api token exchange failed")
            self._pending.pop(user_id, None)
            return "Something went wrong finishing the login -- run /link-web-api again.", False

        self._pending.pop(user_id, None)
        refresh_token = token_response.get("refresh_token")
        if not refresh_token:
            return "Spotify didn't return a refresh token -- try again.", False

        await self.store.set_web_api_token(pending.slot_index, refresh_token)
        self._cache[pending.slot_index] = _CachedAccessToken(
            access_token=token_response["access_token"],
            expires_at=time.monotonic() + token_response.get("expires_in", 3600),
        )
        return "Spotify Web API linked for this slot.", True

    async def get_access_token(self, slot_index: int) -> str | None:
        """Lazily refreshes as needed. Returns None if the slot has never
        completed /link-web-api."""
        cached = self._cache.get(slot_index)
        if cached is not None and cached.expires_at - _EXPIRY_SAFETY_MARGIN_SECONDS > time.monotonic():
            return cached.access_token

        meta = self.store.get_by_index(slot_index)
        if meta is None or not meta.web_api_refresh_token:
            return None

        token_response = await _refresh(meta.web_api_refresh_token)
        self._cache[slot_index] = _CachedAccessToken(
            access_token=token_response["access_token"],
            expires_at=time.monotonic() + token_response.get("expires_in", 3600),
        )
        # Spotify may rotate the refresh token on refresh; persist if so.
        new_refresh_token = token_response.get("refresh_token")
        if new_refresh_token and new_refresh_token != meta.web_api_refresh_token:
            await self.store.set_web_api_token(slot_index, new_refresh_token)
        return self._cache[slot_index].access_token
