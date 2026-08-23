"""Launches and supervises one librespot process per claimed Spotify slot.

Each librespot instance authenticates as one Spotify Premium account and
registers itself as a Spotify Connect device. Audio it decodes is written as
raw PCM to a named pipe, which the Discord bot reads via ffmpeg and plays
into a voice channel. Playback (play/pause/skip/volume) stays controlled
directly from the Spotify app/Connect UI -- the bot only relays audio.

Slots are no longer fixed at startup: a slot only gets a running process once
it's been claimed via /link (bot/spotify_link.py), and /delete-slot stops and
removes one at runtime. See bot/slot_store.py for the claimed/free bookkeeping.

LibrespotProcess also owns a permanent background reader on the pipe (see
"pipe draining" below) -- without it, pausing in Spotify (or seeking, or
simply nobody being /connect'd yet) leaves nothing draining the FIFO. Once
the kernel pipe buffer (~64KB) fills, librespot's write() call blocks
indefinitely and its whole playback thread hangs -- the only fix at that
point is restarting librespot (or the instance) entirely. And because the
FIFO is long-lived, whatever stale/backed-up bytes were sitting in it play
out first the next time ffmpeg attaches, which is the "audio speeds up and
distorts at the start" symptom. Keeping a reader on the pipe at all times
(discarding data whenever no real playback is attached, pausing only while
ffmpeg is actually consuming it for Discord) fixes both: librespot's writer
never blocks, and there's never a backlog left over to play out.
"""

import asyncio
import logging
import os

from config import LIBRESPOT_BIN, PIPE_DIR, SpotifySlot

log = logging.getLogger("librespot")

_DRAIN_CHUNK_SIZE = 65536


class LibrespotProcess:
    def __init__(self, slot: SpotifySlot):
        self.slot = slot
        self._proc: asyncio.subprocess.Process | None = None
        self._drain_fd: int | None = None
        self._draining = True

    def ensure_pipe(self) -> None:
        os.makedirs(PIPE_DIR, exist_ok=True)
        if not os.path.exists(self.slot.pipe_path):
            os.mkfifo(self.slot.pipe_path)

    async def start(self) -> None:
        self.ensure_pipe()
        self._open_drain_reader()

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

    def _open_drain_reader(self) -> None:
        """Opens our own permanent read end on the pipe, non-blocking, and
        registers it with the event loop. This must happen BEFORE librespot
        starts -- a FIFO opened for writing blocks until a reader exists, so
        without this librespot's own open() would block until the first
        /connect ever happened. A no-op if we already have one open (e.g.
        restart_if_dead() restarting a crashed librespot -- the pipe reader
        itself doesn't need to restart, only the writer)."""
        if self._drain_fd is not None:
            return
        self._drain_fd = os.open(self.slot.pipe_path, os.O_RDONLY | os.O_NONBLOCK)
        asyncio.get_running_loop().add_reader(self._drain_fd, self._on_pipe_readable)

    def _on_pipe_readable(self) -> None:
        # Only actually consume bytes while nothing else (ffmpeg, via
        # discord.py) is attached -- pause_draining() stops this without
        # closing the fd, so librespot never sees a gap in readers even
        # while ffmpeg is the one actually consuming the audio.
        if not self._draining or self._drain_fd is None:
            return
        try:
            os.read(self._drain_fd, _DRAIN_CHUNK_SIZE)
        except BlockingIOError:
            pass
        except OSError:
            log.exception("error draining pipe for slot %s", self.slot.name)

    def pause_draining(self) -> None:
        """Call right before attaching a real (ffmpeg) reader for playback,
        so we stop competing with it for bytes."""
        self._draining = False

    def resume_draining(self) -> None:
        """Call once playback stops for any reason (disconnect, error, the
        track/source ending) so librespot's writer never blocks again."""
        self._draining = True

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
        if self._drain_fd is not None:
            asyncio.get_running_loop().remove_reader(self._drain_fd)
            os.close(self._drain_fd)
            self._drain_fd = None

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
