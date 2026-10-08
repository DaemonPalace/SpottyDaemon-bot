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

The idle drain deliberately consumes at roughly real-time PCM pace, not as
fast as the OS will hand us bytes. A first version drained greedily (as
soon as data was available), which removed all backpressure on librespot's
writer whenever the drain was active -- if ffmpeg died mid-pause and
draining resumed, librespot would then write (and our drain would swallow)
an entire track in about a second instead of its real duration, since
nothing was pacing it to real time anymore. Observed in production as a
"skips every track instantly" storm that also triggered Spotify-side
`Service unavailable { audio key error }` responses -- almost certainly
Spotify's own rate limiting reacting to that burst of rapid-fire requests.
Pacing the idle drain to the same ~176400 bytes/sec (44.1kHz stereo s16le)
a real listener would consume keeps librespot's writer from ever blocking
(never fills for more than one tick) while still throttling it to a
realistic rate when nobody's actually listening.
"""

import array
import asyncio
import fcntl
import logging
import os
import termios
import time

from config import LIBRESPOT_BIN, PIPE_DIR, SpotifySlot

log = logging.getLogger("librespot")

# Matches librespot's pipe backend output format (also what commands.py's
# ffmpeg invocation assumes via `-ar 44100 -ac 2`): 16-bit stereo PCM.
_PCM_BYTES_PER_SECOND = 44100 * 2 * 2
# Tied to _PIPE_BUFFER_SIZE_BYTES below: the idle drain loop has to read at
# least once per however long the (now much smaller) pipe can hold, or
# librespot's writer blocks between ticks -- see that constant's comment.
_DRAIN_TICK_SECONDS = 0.01
_DRAIN_CHUNK_SIZE = int(_PCM_BYTES_PER_SECOND * _DRAIN_TICK_SECONDS)

# Linux's default pipe buffer (65536 bytes, ~371ms of this audio format) is
# what Stage A2's diagnostics found sitting as a constant steady-state
# backlog during normal playback -- not a bug, just the pipe staying full at
# capacity. Also the leftover backlog that gets read out (as a burst) after
# an API-driven track change (play_uri/skip/rewind, see
# LibrespotProcess.flush()) if nobody flushes it first -- ffmpeg reads the
# old track's queued tail immediately followed by the new track's head, no
# gap, which is the "distorted, then speeds up" symptom. flush() handles
# the accidental case; this constant caps the worst case for anything that
# doesn't call it (or the brief window between a flush and the next byte
# actually arriving). 4096 (one page) is the practical floor -- F_SETPIPE_SZ
# rounds up to PAGE_SIZE regardless of what's requested below it.
_PIPE_BUFFER_SIZE_BYTES = 4096

# How often to sample the pipe's kernel buffer for diagnosing sync drift
# (bot/commands.py issue: audio lagging/speeding up). FIONREAD works on
# either end of a FIFO and doesn't consume data, so sampling our own
# permanent drain fd tells us how much undelivered audio is backed up in
# the pipe regardless of whether ffmpeg or our drain loop is the one
# actually reading it right now.
_BACKLOG_LOG_INTERVAL_SECONDS = 3.0
# Backlog at or above this is logged at WARNING -- a subtler amount of drift
# than the original 1.0s cutoff (dropped to catch "a little bit out of
# sync," not just severe buildups), since anything under this is audible
# but wouldn't have crossed the old threshold at all.
_BACKLOG_WARN_SECONDS = 0.3
# Also log a routine sample at INFO this often even when nothing crosses
# the warning threshold -- otherwise (root logger is INFO, see main.py) a
# session with no spike leaves zero backlog data behind, which is exactly
# what happened to the first attempt at this instrumentation: normal
# samples were logged at DEBUG and got silently dropped, so a multi-hour
# listening session produced no trend data at all.
_ROUTINE_LOG_INTERVAL_SECONDS = 30.0


class LibrespotProcess:
    def __init__(self, slot: SpotifySlot):
        self.slot = slot
        self._proc: asyncio.subprocess.Process | None = None
        self._drain_fd: int | None = None
        # Write end we never write to -- held only so the FIFO always has a
        # writer. librespot's pipe sink closes its end on every pause, and
        # without another writer that's EOF for ffmpeg, ending playback.
        self._keepalive_fd: int | None = None
        self._drain_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._draining = True
        self._started_monotonic: float | None = None
        # Set when a real (ffmpeg) reader attaches, cleared once the monitor
        # loop observes the first backlog bytes after that -- lets us log
        # how long playback took to actually start producing audio.
        self._attach_monotonic: float | None = None
        self._first_bytes_logged = True

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
            "--volume-ctrl", "linear",
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._started_monotonic = time.monotonic()
        log.info("started librespot for slot %s (pid=%s)", self.slot.name, self._proc.pid)
        asyncio.create_task(self._log_stderr())

    def _open_drain_reader(self) -> None:
        """Opens our own permanent read end on the pipe, non-blocking, and
        starts the paced background task that drains it. This must happen
        BEFORE librespot starts -- a FIFO opened for writing blocks until a
        reader exists, so without this librespot's own open() would block
        until the first /connect ever happened. A no-op if we already have
        one open (e.g. restart_if_dead() restarting a crashed librespot --
        the pipe reader itself doesn't need to restart, only the writer)."""
        if self._drain_fd is not None:
            return
        self._drain_fd = os.open(self.slot.pipe_path, os.O_RDONLY | os.O_NONBLOCK)
        # Pipe buffer size is a property of the pipe itself, not just this
        # fd -- shrinking it here (before librespot's writer ever opens the
        # other end, so it's guaranteed empty) caps how much undelivered
        # audio can ever sit queued, which is exactly the steady-state
        # backlog Stage A2's diagnostics measured: it tracked the kernel's
        # default 65536-byte pipe size (~371ms of s16le stereo 44.1kHz
        # audio) almost exactly. Smaller still leaves slack for read-side
        # jitter (ffmpeg/our drain loop being a tick late) without
        # reintroducing the writer-blocks-forever hang this file's module
        # docstring describes -- both readers still drain in step with
        # production rate, just with less cushion.
        try:
            fcntl.fcntl(self._drain_fd, fcntl.F_SETPIPE_SZ, _PIPE_BUFFER_SIZE_BYTES)
        except OSError:
            log.warning("could not shrink pipe buffer for slot %s, using kernel default", self.slot.name)
        # Needs the read end above to exist first, or O_NONBLOCK open fails with ENXIO.
        self._keepalive_fd = os.open(self.slot.pipe_path, os.O_WRONLY | os.O_NONBLOCK)
        self._drain_task = asyncio.create_task(self._drain_loop())
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _drain_loop(self) -> None:
        assert self._drain_fd is not None
        fd = self._drain_fd
        while True:
            await asyncio.sleep(_DRAIN_TICK_SECONDS)
            # Only actually consume bytes while nothing else (ffmpeg, via
            # discord.py) is attached -- pause_draining() stops this
            # without closing the fd, so librespot never sees a gap in
            # readers even while ffmpeg is the one actually consuming the
            # audio for real playback.
            if not self._draining:
                continue
            try:
                os.read(fd, _DRAIN_CHUNK_SIZE)
            except BlockingIOError:
                pass
            except OSError:
                log.exception("error draining pipe for slot %s", self.slot.name)

    def flush(self) -> None:
        """Discards whatever's sitting in the pipe right now. Call this
        right when issuing an API-driven track change (play_uri, skip,
        rewind) -- Spotify's Connect round-trip for the actual transition
        takes 100ms+, while this completes in microseconds, so there's
        ample margin to clear the old track's leftover tail before the new
        track's audio starts arriving. Doesn't touch _draining's state or
        the fd itself -- safe to call whether ffmpeg is attached or the
        idle drain is active."""
        if self._drain_fd is None:
            return
        try:
            while os.read(self._drain_fd, _DRAIN_CHUNK_SIZE):
                pass
        except BlockingIOError:
            pass  # nothing left queued -- the common case
        except OSError:
            log.exception("error flushing pipe for slot %s", self.slot.name)

    def pause_draining(self) -> None:
        """Call right before attaching a real (ffmpeg) reader for playback,
        so we stop competing with it for bytes."""
        self._draining = False
        self._attach_monotonic = time.monotonic()
        self._first_bytes_logged = False

    def resume_draining(self) -> None:
        """Call once playback stops for any reason (disconnect, error, the
        track/source ending) so librespot's writer never blocks again."""
        self._draining = True

    def uptime_seconds(self) -> float:
        if self._started_monotonic is None:
            return 0.0
        return time.monotonic() - self._started_monotonic

    @staticmethod
    def _pipe_backlog_bytes(fd: int) -> int | None:
        """How many undelivered bytes are sitting in the pipe right now, or
        None if that can't be determined (e.g. fd just closed)."""
        try:
            buf = array.array("i", [0])
            fcntl.ioctl(fd, termios.FIONREAD, buf, True)
            return buf[0]
        except OSError:
            return None

    async def _monitor_loop(self) -> None:
        """Diagnostic-only: periodically logs pipe backlog (source of the
        "lags behind / speeds up" sync drift symptom -- see module
        docstring) and, once per attach, how long it took for librespot to
        actually start producing audio after ffmpeg attached. Always logs at
        INFO or above -- DEBUG is silently dropped under this bot's default
        logging config, so a DEBUG-level routine sample would leave no trail
        at all for a session that never spikes."""
        assert self._drain_fd is not None
        fd = self._drain_fd
        last_routine_log = 0.0
        while True:
            await asyncio.sleep(_BACKLOG_LOG_INTERVAL_SECONDS)
            backlog = self._pipe_backlog_bytes(fd)
            if backlog is None:
                continue

            if not self._first_bytes_logged and backlog > 0 and self._attach_monotonic is not None:
                latency = time.monotonic() - self._attach_monotonic
                log.info("slot %s: first audio bytes %.2fs after attach", self.slot.name, latency)
                self._first_bytes_logged = True

            backlog_seconds = backlog / _PCM_BYTES_PER_SECOND
            now = time.monotonic()
            is_warning = backlog_seconds >= _BACKLOG_WARN_SECONDS
            if is_warning or now - last_routine_log >= _ROUTINE_LOG_INTERVAL_SECONDS:
                last_routine_log = now
                log_fn = log.warning if is_warning else log.info
                log_fn(
                    "slot %s: pipe backlog=%.2fs (%d bytes) draining=%s librespot_uptime=%.0fs",
                    self.slot.name, backlog_seconds, backlog, self._draining, self.uptime_seconds(),
                )

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
        if self._drain_task is not None:
            self._drain_task.cancel()
            self._drain_task = None
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            self._monitor_task = None
        if self._keepalive_fd is not None:
            os.close(self._keepalive_fd)
            self._keepalive_fd = None
        if self._drain_fd is not None:
            os.close(self._drain_fd)
            self._drain_fd = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def is_draining(self) -> bool:
        return self._draining

    def pipe_backlog_seconds(self) -> float | None:
        """Same value _monitor_loop already logs at INFO, exposed for the
        diagnostics API instead of only ending up in the log."""
        if self._drain_fd is None:
            return None
        backlog = self._pipe_backlog_bytes(self._drain_fd)
        if backlog is None:
            return None
        return backlog / _PCM_BYTES_PER_SECOND


class LibrespotManager:
    def __init__(self, slots: list[SpotifySlot]):
        self._all_slots = {slot.index: slot for slot in slots}
        self.processes: dict[str, LibrespotProcess] = {}
        # Diagnostic-only: counts unplanned restarts per slot (librespot
        # dying and getting revived by restart_if_dead()/a self-heal
        # watchdog), keyed by slot name. Deliberate restarts (/reconnect,
        # which stops the slot first) don't go through this path.
        self._restart_counts: dict[str, int] = {}

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
        if existing is not None:
            count = self._restart_counts.get(slot.name, 0) + 1
            self._restart_counts[slot.name] = count
            log.warning("restarting librespot for slot %s (restart #%d)", slot.name, count)
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

    def restart_count(self, slot_name: str) -> int:
        return self._restart_counts.get(slot_name, 0)

    async def restart_if_dead(self) -> None:
        for proc in list(self.processes.values()):
            if not proc.is_running():
                log.warning("librespot for %s died, restarting", proc.slot.name)
                await proc.start()
