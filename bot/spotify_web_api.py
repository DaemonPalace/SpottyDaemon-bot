"""Spotify Web API access token management, per slot.

Independent of librespot's own OAuth (bot/spotify_link.py) -- that flow only
yields librespot's internal Connect-session credentials.json, with scopes
controlled entirely by librespot's own registered OAuth client. There is no
guarantee (and today, no evidence) that it covers Web API scopes like
user-modify-playback-state, so this runs a standard Authorization Code +
PKCE flow against the bot's OWN registered Spotify app instead.

With a public domain configured (config.SPOTIFY_DIRECT_CALLBACK), Spotify
redirects straight to bot/api.py's _spotify_callback, which hands the url to
finish_link itself. Without one, UX mirrors /link's "paste the failed
redirect back" shape (see spotify_link.py's module docstring), but the
mechanics are simpler here -- there's no local subprocess to replay the
request to. The bot itself calls Spotify's token endpoint directly over
aiohttp either way.
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


def generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def app_for_slot(slot_index: int) -> tuple[str, str | None] | None:
    """(client_id, client_secret) of the Spotify app serving this slot, or
    None if there aren't enough apps configured to reach it."""
    app = (slot_index - 1) // config.SPOTIFY_USERS_PER_APP
    return config.SPOTIFY_APPS[app] if app < len(config.SPOTIFY_APPS) else None


def _client_fields(slot_index: int) -> dict:
    app = app_for_slot(slot_index)
    if app is None:
        raise RuntimeError(f"no Spotify app configured for slot {slot_index}")
    client_id, client_secret = app
    return {"client_id": client_id, **({"client_secret": client_secret} if client_secret else {})}


def build_authorize_url(state: str, code_challenge: str, slot_index: int, scopes: str = SCOPES) -> str:
    params = {
        "client_id": _client_fields(slot_index)["client_id"],
        "response_type": "code",
        "redirect_uri": config.SPOTIFY_WEB_API_REDIRECT_URI,
        "state": state,
        "scope": scopes,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


async def exchange_code(code: str, code_verifier: str, slot_index: int) -> dict:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.SPOTIFY_WEB_API_REDIRECT_URI,
        "code_verifier": code_verifier,
        **_client_fields(slot_index),
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, data=data, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            body = await resp.json()
            if resp.status >= 300:
                raise RuntimeError(f"token exchange failed ({resp.status}): {body}")
            return body


async def _refresh(refresh_token: str, slot_index: int) -> dict:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        **_client_fields(slot_index),
    }
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
    url: str


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

    def authorize_url(self, user_id: str) -> str | None:
        """The Spotify login url for this user's pending link, if any --
        lets callers (Discord's button+modal flow, the web dashboard's
        link-in-new-tab button) surface it directly instead of scraping it
        out of the human-readable message."""
        pending = self._pending.get(user_id)
        return pending.url if pending is not None else None

    def user_for_state(self, state: str) -> str | None:
        """Which pending link a direct callback (bot/api.py's
        _spotify_callback) belongs to -- the OAuth state is its only key."""
        return next((u for u, p in self._pending.items() if p.state == state), None)

    def start_link(self, user_id: str, slot_index: int) -> tuple[str, bool]:
        if not config.SPOTIFY_CLIENT_ID:
            return (
                "Spotify Web API isn't configured on this bot -- SPOTIFY_CLIENT_ID is unset. "
                "Ask the bot owner to register an app at developer.spotify.com.",
                False,
            )
        if app_for_slot(slot_index) is None:
            return (
                "No Spotify app is configured for this slot -- ask the bot owner to add another "
                "app's client id to SPOTIFY_CLIENT_ID.",
                False,
            )
        verifier, challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(16)
        url = build_authorize_url(state, challenge, slot_index)
        self._pending[user_id] = _PendingWebApiLink(
            slot_index=slot_index, code_verifier=verifier, state=state, started_at=time.monotonic(), url=url
        )
        if config.SPOTIFY_DIRECT_CALLBACK:
            return (
                "Log in with Spotify using the button below -- it finishes on its own once you "
                f"approve, within {PENDING_TIMEOUT_SECONDS // 60} minutes.",
                True,
            )
        return (
            "Log in with Spotify using the button below. Your browser will fail to load the page "
            "it redirects to next -- that's expected. Copy the FULL url from your browser's "
            f"address bar and paste it back, within {PENDING_TIMEOUT_SECONDS // 60} minutes.",
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
            token_response = await exchange_code(code, pending.code_verifier, pending.slot_index)
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

        token_response = await _refresh(meta.web_api_refresh_token, slot_index)
        self._cache[slot_index] = _CachedAccessToken(
            access_token=token_response["access_token"],
            expires_at=time.monotonic() + token_response.get("expires_in", 3600),
        )
        # Spotify may rotate the refresh token on refresh; persist if so.
        new_refresh_token = token_response.get("refresh_token")
        if new_refresh_token and new_refresh_token != meta.web_api_refresh_token:
            await self.store.set_web_api_token(slot_index, new_refresh_token)
        return self._cache[slot_index].access_token
