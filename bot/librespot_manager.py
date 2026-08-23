"""Launches and supervises one librespot process per claimed Spotify slot.

Each librespot instance authenticates as one Spotify Premium account and
registers itself as a Spotify Connect device. Audio it decodes is written as
raw PCM to a named pipe, which the Discord bot reads via ffmpeg and plays
into a voice channel. Playback (play/pause/skip/volume) stays controlled
directly from the Spotify app/Connect UI -- the bot only relays audio.

Slots are no longer fixed at startup: a slot only gets a running process once
it's been claimed via /link (bot/spotify_link.py), and /delete-slot stops and
removes one at runtime. See bot/slot_store.py for the claimed/free bookkeeping.
"""

import asyncio
import logging
import os

from config import LIBRESPOT_BIN, PIPE_DIR, SpotifySlot

log = logging.getLogger("librespot")


class LibrespotProcess:
    def __init__(self, slot: SpotifySlot):
        self.slot = slot
        self._proc: asyncio.subprocess.Process | None = None

    def ensure_pipe(self) -> None:
        os.makedirs(PIPE_DIR, exist_ok=True)
        if not os.path.exists(self.slot.pipe_path):
            os.mkfifo(self.slot.pipe_path)

    async def start(self) -> None:
        self.ensure_pipe()
        # librespot uses Rust's env_logger, which only prints ERROR by
        # default -- without RUST_LOG, auth failures etc. are silent.
        env = {**os.environ, "RUST_LOG": "info"}
        self._proc = await asyncio.create_subprocess_exec(
            LIBRESPOT_BIN,
            "--name", self.slot.name,
            "--backend", "pipe",
            "--device", self.slot.pipe_path,
            "--system-cache", self.slot.cache_dir,
            "--bitrate", "320",
            "--disable-audio-cache",
            "--initial-volume", "100",
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        log.info("started librespot for slot %s (pid=%s)", self.slot.name, self._proc.pid)
        asyncio.create_task(self._log_stderr())

    async def _log_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        async for line in self._proc.stderr:
            log.info("[%s] %s", self.slot.name, line.decode(errors="replace").rstrip())

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None


class LibrespotManager:
    def __init__(self, slots: list[SpotifySlot]):
        self._all_slots = {slot.index: slot for slot in slots}
        self.processes: dict[str, LibrespotProcess] = {}

    def slot_by_index(self, index: int) -> SpotifySlot | None:
        return self._all_slots.get(index)

    async def start_claimed(self, claimed_indexes: set[int]) -> None:
        for index in claimed_indexes:
            slot = self._all_slots.get(index)
            if slot is None:
                continue
            if not os.path.exists(os.path.join(slot.cache_dir, "credentials.json")):
                log.warning(
                    "slot %s is marked claimed but has no credentials.json, skipping", slot.name
                )
                continue
            await self.start_one(slot)

    async def start_one(self, slot: SpotifySlot) -> None:
        existing = self.processes.get(slot.name)
        if existing and existing.is_running():
            return
        proc = LibrespotProcess(slot)
        await proc.start()
        self.processes[slot.name] = proc

    async def stop_one(self, slot_name: str) -> None:
        proc = self.processes.pop(slot_name, None)
        if proc is not None:
            await proc.stop()

    async def stop_all(self) -> None:
        for proc in list(self.processes.values()):
            await proc.stop()
        self.processes.clear()

    def get(self, slot_name: str) -> LibrespotProcess:
        return self.processes[slot_name]

    async def restart_if_dead(self) -> None:
        for proc in list(self.processes.values()):
            if not proc.is_running():
                log.warning("librespot for %s died, restarting", proc.slot.name)
                await proc.start()
