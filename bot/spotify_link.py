"""Self-serve Spotify account linking for a free slot.

Logs in through librespot's own OAuth client (spotify_web_api.
LIBRESPOT_CLIENT_ID) rather than our own Spotify app: a development-mode app
rejects every account not on its allowlist, librespot's client doesn't. The
bot runs the PKCE flow itself instead of `librespot --enable-oauth`, so it
keeps the tokens: the access token signs librespot in once
(`--access-token`, writing credentials.json) and the refresh token backs the
dashboard's Web API features -- one login links both.

librespot's client only accepts a loopback redirect (127.0.0.1), so there's
no way to send a friend's browser back to the bot. Spotify redirects them to
127.0.0.1 on their own machine, the page fails to load, and they paste that
url back via /link-finish (or the dashboard) for the code in it.
"""

import asyncio
import logging
import os
import secrets
import time
import urllib.parse
from dataclasses import dataclass

import config
import spotify_web_api
from librespot_manager import LibrespotManager
from slot_store import SLOT_NAME_RE, SlotStore

log = logging.getLogger("spotify_link")

CREDENTIALS_WAIT_SECONDS = 60
# The redirect librespot's own OAuth client accepts (any loopback port).
LIBRESPOT_REDIRECT_URI = f"http://127.0.0.1:{config.LINK_OAUTH_PORT}/login"
CREDENTIALS_POLL_INTERVAL_SECONDS = 2


@dataclass
class PendingLink:
    slot_index: int
    slot_name: str
    password: str
    started_at: float
    url: str
    state: str
    code_verifier: str


class LinkManager:
    def __init__(self, store: SlotStore, librespot: LibrespotManager):
        self.store = store
        self.librespot = librespot
        self._pending: dict[str, PendingLink] = {}
        self._sweep_task: asyncio.Task | None = None

    def start(self) -> None:
        self._sweep_task = asyncio.create_task(self._sweep_loop())

    def stop(self) -> None:
        if self._sweep_task:
            self._sweep_task.cancel()

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            now = time.monotonic()
            expired = [
                user_id
                for user_id, pending in self._pending.items()
                if now - pending.started_at > config.LINK_TIMEOUT_SECONDS
            ]
            for user_id in expired:
                log.warning("link for user %s timed out, freeing slot", user_id)
                await self._abort(user_id)

    async def _abort(self, user_id: str) -> None:
        pending = self._pending.pop(user_id, None)
        if pending is None:
            return
        await self.store.reset(pending.slot_index)

    @staticmethod
    async def _kill(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()

    def has_pending(self, user_id: str) -> bool:
        return user_id in self._pending

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

    async def start_link(self, user_id: str, slot_name: str, password: str) -> tuple[str, bool]:
        """Returns (message, success)."""
        slot_name = slot_name.strip().lower()

        if user_id in self._pending:
            return (
                "You already have a link in progress. Finish it with "
                "/link-finish, or wait 5 minutes for it to expire and try again.",
                False,
            )
        if not SLOT_NAME_RE.match(slot_name):
            return "Slot name must be lowercase letters, numbers, and hyphens only (max 32 chars).", False
        if self.store.get_by_name(slot_name) is not None:
            return f"Slot name '{slot_name}' is already taken.", False

        slot_meta = self.store.find_free_slot()
        if slot_meta is None:
            return "All slots are in use -- ask the bot owner to free one up with /delete-slot.", False

        index = slot_meta.index
        await self.store.set_linking(index)

        spotify_slot = self.librespot.slot_by_index(index)
        assert spotify_slot is not None
        os.makedirs(spotify_slot.cache_dir, exist_ok=True)
        # A free slot shouldn't have leftover credentials, but can if a
        # previous link attempt crashed/errored after librespot wrote
        # credentials.json but before this flow finished (or completed) --
        # librespot silently reuses valid cached credentials instead of
        # doing the OAuth browser flow, which would authenticate the new
        # /link as the STALE account with no login prompt at all.
        credentials_path = os.path.join(spotify_slot.cache_dir, "credentials.json")
        if os.path.exists(credentials_path):
            log.warning("slot %s had leftover credentials.json, removing before fresh link", spotify_slot.name)
            os.remove(credentials_path)

        verifier, challenge = spotify_web_api.generate_pkce_pair()
        state = secrets.token_urlsafe(16)
        self._pending[user_id] = PendingLink(
            slot_index=index,
            slot_name=slot_name,
            password=password,
            started_at=time.monotonic(),
            url=spotify_web_api.build_authorize_url(
                state,
                challenge,
                index,
                spotify_web_api.SCOPES + " streaming",
                client_id=spotify_web_api.LIBRESPOT_CLIENT_ID,
                redirect_uri=LIBRESPOT_REDIRECT_URI,
            ),
            state=state,
            code_verifier=verifier,
        )
        return (
            f"Log in with the Spotify account for **{slot_name}** using the button below. "
            "Your browser will fail to load the page it redirects to next -- that's expected. "
            "Copy the FULL url from your browser's address bar and paste it back. "
            f"You have {config.LINK_TIMEOUT_SECONDS // 60} minutes.",
            True,
        )

    async def finish_link(self, user_id: str, pasted_url: str | None = None) -> tuple[str, bool]:
        """pasted_url is the failed 127.0.0.1 redirect the browser landed on
        after the Spotify login -- its query carries the code."""
        pending = self._pending.get(user_id)
        if pending is None:
            return "No link in progress. Run /link first.", False

        params = urllib.parse.parse_qs(urllib.parse.urlparse((pasted_url or "").strip()).query)
        if "error" in params:
            await self._abort(user_id)
            return "Spotify login was cancelled -- run /link again.", False
        code = params.get("code", [None])[0]
        if not code:
            return (
                "Paste the FULL url from your browser's address bar after logging in, including "
                "the `?code=...` part.",
                False,
            )
        if params.get("state", [None])[0] != pending.state:
            return "That url doesn't match your pending login -- make sure it's from the latest link.", False

        # Off the pending list first so the timeout sweep can't reset the
        # slot mid-exchange; every failure path below resets it itself.
        self._pending.pop(user_id, None)
        index = pending.slot_index
        spotify_slot = self.librespot.slot_by_index(index)
        assert spotify_slot is not None
        credentials_path = os.path.join(spotify_slot.cache_dir, "credentials.json")

        try:
            tokens = await spotify_web_api.exchange_code(
                code,
                pending.code_verifier,
                index,
                client_id=spotify_web_api.LIBRESPOT_CLIENT_ID,
                redirect_uri=LIBRESPOT_REDIRECT_URI,
            )
            # ponytail: the access token is visible in `ps` for the few
            # seconds librespot runs -- it's short-lived (1h) and local-only.
            process = await asyncio.create_subprocess_exec(
                config.LIBRESPOT_BIN,
                "--name", spotify_slot.name,
                "--access-token", tokens["access_token"],
                "--system-cache", spotify_slot.cache_dir,
                "--backend", "pipe",
                "--device", os.path.join(config.PIPE_DIR, f"slot{index}-linking.pcm"),
                env={**os.environ, "RUST_LOG": "info"},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception:
            log.exception("link failed before librespot sign-in")
            await self.store.reset(index)
            return "Something went wrong finishing the login -- run /link again.", False

        success = await self._wait_for_credentials(credentials_path)
        await self._kill(process)
        if not success:
            assert process.stdout is not None
            output = (await process.stdout.read()).decode(errors="replace")
            log.error("librespot didn't sign in with the OAuth token for slot %s:\n%s", spotify_slot.name, output)
            await self.store.reset(index)
            return "Spotify login worked, but the player couldn't sign in with it -- check the bot logs.", False

        await self.store.claim(index, pending.slot_name, pending.password, user_id)
        if tokens.get("refresh_token"):
            await self.store.set_web_api_token(index, tokens["refresh_token"], spotify_web_api.LIBRESPOT_CLIENT_ID)
        await self.librespot.start_one(spotify_slot)
        return (
            f"Linked! Slot **{pending.slot_name}** is ready -- "
            f"use `/connect {pending.slot_name}` with the password you set.",
            True,
        )

    @staticmethod
    async def _wait_for_credentials(credentials_path: str) -> bool:
        deadline = time.monotonic() + CREDENTIALS_WAIT_SECONDS
        while time.monotonic() < deadline:
            if os.path.exists(credentials_path):
                return True
            await asyncio.sleep(CREDENTIALS_POLL_INTERVAL_SECONDS)
        return os.path.exists(credentials_path)
