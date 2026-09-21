"""Self-serve Spotify account linking for a free slot.

librespot's built-in OAuth (`--enable-oauth`) uses Spotify's own client
registration, which only accepts a loopback (127.0.0.1) redirect URI -- there
is no way to make Spotify redirect a friend's browser straight back to a
public EC2 address, so we can't just open a port and hand out a link.

Instead this uses librespot's own documented fallback for exactly this
headless/remote situation: start `librespot --enable-oauth` on the bot's box,
hand the friend the printed Spotify authorize URL, let them log in in their
own browser. The final redirect (to http://127.0.0.1:<port>/login?code=...)
fails to load in their browser since 127.0.0.1 means their own machine, not
the bot's -- but librespot itself is running a real HTTP server on that
loopback port, actively waiting for that exact request (confirmed by hand:
running librespot --enable-oauth and curling 127.0.0.1:<port>/login?code=...
gets back "Go back to your terminal :)" and completes the exchange -- it does
NOT read the callback from stdin, despite what some docs suggest). The friend
copies that failed url out of their address bar and pastes it back via
/link-finish; since the bot process runs on the same box as librespot, it
just re-issues that same GET request to 127.0.0.1 itself, which librespot's
server receives exactly as if the friend's own browser had reached it.

This means no security-group/EC2-network changes are needed at all.
"""

import asyncio
import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass

import aiohttp

import config
from librespot_manager import LibrespotManager
from slot_store import SLOT_NAME_RE, SlotStore

log = logging.getLogger("spotify_link")

AUTHORIZE_URL_RE = re.compile(r"https://accounts\.spotify\.com/\S+")
AUTHORIZE_URL_WAIT_SECONDS = 15
CREDENTIALS_WAIT_SECONDS = 60
CREDENTIALS_POLL_INTERVAL_SECONDS = 2


@dataclass
class PendingLink:
    slot_index: int
    slot_name: str
    password: str
    started_at: float
    process: asyncio.subprocess.Process
    url: str


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
        await self._kill(pending.process)
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

        env = {**os.environ, "RUST_LOG": "info"}
        try:
            process = await asyncio.create_subprocess_exec(
                config.LIBRESPOT_BIN,
                "--name", spotify_slot.name,
                "--enable-oauth",
                "--oauth-port", str(config.LINK_OAUTH_PORT),
                "--system-cache", spotify_slot.cache_dir,
                "--backend", "pipe",
                "--device", os.path.join(config.PIPE_DIR, f"slot{index}-linking.pcm"),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception:
            log.exception("failed to start librespot for linking")
            await self.store.reset(index)
            return "Couldn't start the Spotify login flow -- check the bot logs.", False

        url = await self._read_authorize_url(process)
        if url is None:
            await self._kill(process)
            await self.store.reset(index)
            return "Couldn't start the Spotify login flow -- check the bot logs.", False

        self._pending[user_id] = PendingLink(
            slot_index=index,
            slot_name=slot_name,
            password=password,
            started_at=time.monotonic(),
            process=process,
            url=url,
        )
        return (
            f"Log in with the Spotify account for **{slot_name}** using the button below. "
            "Your browser will most likely fail to load the page it redirects to next (expected, "
            "unless the bot happens to be running on this same machine) -- if so, copy the FULL "
            "url from your browser's address bar and paste it back, or leave it blank if the page "
            f"loaded fine. You have {config.LINK_TIMEOUT_SECONDS // 60} minutes.",
            True,
        )

    async def _read_authorize_url(self, process: asyncio.subprocess.Process) -> str | None:
        assert process.stdout is not None
        try:
            async with asyncio.timeout(AUTHORIZE_URL_WAIT_SECONDS):
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        return None
                    match = AUTHORIZE_URL_RE.search(line.decode(errors="replace"))
                    if match:
                        return match.group(0)
        except TimeoutError:
            return None

    async def finish_link(self, user_id: str, pasted_url: str | None = None) -> tuple[str, bool]:
        """pasted_url is only needed when the bot and the browser doing the
        Spotify login are on different machines (the normal remote/friend
        case) -- the redirect to 127.0.0.1:<port> fails to load there, so the
        bot has to replay it itself from the copied url. When the bot and
        browser share a machine (e.g. testing locally), that redirect
        actually reaches librespot's own server on its own; credentials.json
        just appears with no failed page and nothing to copy, so pasted_url
        can be omitted and this only needs to wait for that to happen."""
        pending = self._pending.get(user_id)
        if pending is None:
            return "No link in progress. Run /link first.", False

        process = pending.process
        if process.returncode is not None:
            self._pending.pop(user_id, None)
            await self.store.reset(pending.slot_index)
            return "That login session expired -- run /link again.", False

        if pasted_url:
            query = urllib.parse.urlparse(pasted_url.strip()).query
            if not query:
                return (
                    "That doesn't look like the right url -- make sure you copied the FULL address "
                    "from your browser's address bar, including the `?code=...` part.",
                    False,
                )

            callback_url = f"http://127.0.0.1:{config.LINK_OAUTH_PORT}/login?{query}"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(callback_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        await resp.read()
            except Exception:
                log.exception("failed to replay oauth callback to librespot")
                self._pending.pop(user_id, None)
                await self._kill(process)
                await self.store.reset(pending.slot_index)
                return "Something went wrong finishing the login -- run /link again.", False

        spotify_slot = self.librespot.slot_by_index(pending.slot_index)
        assert spotify_slot is not None
        credentials_path = os.path.join(spotify_slot.cache_dir, "credentials.json")

        success = await self._wait_for_credentials(credentials_path)
        self._pending.pop(user_id, None)
        await self._kill(process)

        if not success:
            await self.store.reset(pending.slot_index)
            if pasted_url:
                return (
                    "Login didn't complete -- the pasted url may have been wrong, expired, or "
                    "already used. Run /link again to retry.",
                    False,
                )
            return (
                "Login hasn't completed yet -- finish logging in in your browser, then run "
                "/link-finish again (no url needed if the bot and your browser are on the same "
                "machine). If your browser showed a failed-to-load page instead, paste that url "
                "into /link-finish.",
                False,
            )

        await self.store.claim(pending.slot_index, pending.slot_name, pending.password, user_id)
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
