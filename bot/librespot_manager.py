"""Launches and supervises one librespot process per Spotify slot.

Each librespot instance authenticates as one Spotify Premium account and
registers itself as a Spotify Connect device. Audio it decodes is written as
raw PCM to a named pipe, which the Discord bot reads via ffmpeg and plays
into a voice channel. Playback (play/pause/skip/volume) stays controlled
directly from the Spotify app/Connect UI — the bot only relays audio.
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
        self._proc = await asyncio.create_subprocess_exec(
            LIBRESPOT_BIN,
            "--name", self.slot.name,
            "--backend", "pipe",
            "--device", self.slot.pipe_path,
            "--username", self.slot.username,
            "--password", self.slot.password,
            "--bitrate", "320",
            "--disable-audio-cache",
            "--initial-volume", "100",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        log.info("started librespot for slot %s (pid=%s)", self.slot.name, self._proc.pid)
        asyncio.create_task(self._log_stderr())

    async def _log_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        async for line in self._proc.stderr:
            log.debug("[%s] %s", self.slot.name, line.decode(errors="replace").rstrip())

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
        self.processes = {slot.name: LibrespotProcess(slot) for slot in slots}

    async def start_all(self) -> None:
        for proc in self.processes.values():
            await proc.start()

    async def stop_all(self) -> None:
        for proc in self.processes.values():
            await proc.stop()

    def get(self, slot_name: str) -> LibrespotProcess:
        return self.processes[slot_name]

    async def restart_if_dead(self) -> None:
        for proc in self.processes.values():
            if not proc.is_running():
                log.warning("librespot for %s died, restarting", proc.slot.name)
                await proc.start()
