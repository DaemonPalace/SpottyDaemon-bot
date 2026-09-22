"""Manages bot/main.py as a subprocess: start/stop/restart, a bounded log
buffer for the UI's log view, and crash-loop detection so a bad token
doesn't spin forever. A pidfile lets a restarted supervisor re-attach to a
bot process that's still running rather than losing track of it or
double-spawning -- .env alone doesn't record a PID.
"""

import asyncio
import collections
import logging
import os
import signal
import sys
import time

import aiohttp

from config import BOT_API_HOST, BOT_API_PORT, BOT_DIR, BOT_PIDFILE_PATH

log = logging.getLogger("process_manager")

LOG_BUFFER_LINES = 2000
# N crashes within this window -> stop auto-managing, surface crash_looping.
CRASH_LOOP_WINDOW_SECONDS = 30
CRASH_LOOP_MAX_EXITS = 3

STATUS_NOT_CONFIGURED = "not_configured"
STATUS_STOPPED = "stopped"
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_CRASH_LOOPING = "crash_looping"


class BotProcessManager:
    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._log_task: asyncio.Task | None = None
        self.logs: collections.deque[str] = collections.deque(maxlen=LOG_BUFFER_LINES)
        self._recent_exits: list[float] = []
        self._crash_looping = False

    async def attach_if_running(self) -> None:
        """Called once at supervisor startup: if a pidfile exists and that
        PID is alive, assume it's our bot (started by a previous supervisor
        run) and just watch it rather than spawning a duplicate."""
        pid = self._read_pidfile()
        if pid is None:
            return
        try:
            os.kill(pid, 0)
        except OSError:
            self._remove_pidfile()
            return
        log.info("re-attaching to already-running bot process (pid=%d)", pid)
        # We can't recover the original asyncio.subprocess.Process handle
        # for a process we didn't spawn -- track just enough to know it's
        # alive and let status checks fall back to the /healthz probe.
        self._external_pid = pid

    def start(self) -> None:
        if self.is_alive():
            return
        self._crash_looping = False
        asyncio.create_task(self._spawn())

    async def _spawn(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "main.py",
            cwd=BOT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._write_pidfile(self._proc.pid)
        self._log_task = asyncio.create_task(self._read_logs())
        asyncio.create_task(self._watch_exit())

    async def _read_logs(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        async for line in self._proc.stdout:
            self.logs.append(line.decode(errors="replace").rstrip())

    async def _watch_exit(self) -> None:
        assert self._proc is not None
        await self._proc.wait()
        self._remove_pidfile()
        now = time.monotonic()
        self._recent_exits = [t for t in self._recent_exits if now - t < CRASH_LOOP_WINDOW_SECONDS]
        self._recent_exits.append(now)
        if len(self._recent_exits) >= CRASH_LOOP_MAX_EXITS:
            log.warning("bot process crash-looping (%d exits within %ds), not auto-restarting",
                        len(self._recent_exits), CRASH_LOOP_WINDOW_SECONDS)
            self._crash_looping = True

    async def stop(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._remove_pidfile()
        self._proc = None

    async def restart(self) -> None:
        await self.stop()
        self._crash_looping = False
        self._recent_exits.clear()
        self.start()

    def is_alive(self) -> bool:
        if self._proc is not None and self._proc.returncode is None:
            return True
        pid = getattr(self, "_external_pid", None)
        if pid is not None:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                self._external_pid = None
        return False

    async def status(self) -> str:
        """Checks the bot's actual /healthz first, before anything this
        process knows about its own subprocess/pidfile -- a hosted install
        commonly runs bot/main.py as its own systemd unit (discord-music-bot
        .service) entirely independent of this supervisor, so is_alive()
        (which only knows about a process *this* supervisor spawned or
        re-attached to via the pidfile) would otherwise report "stopped"
        forever even though the real, systemd-managed bot is healthy."""
        if self._crash_looping:
            return STATUS_CRASH_LOOPING
        if await self._healthz_ok():
            return STATUS_RUNNING
        if self.is_alive():
            return STATUS_STARTING
        return STATUS_STOPPED

    async def _healthz_ok(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://{BOT_API_HOST}:{BOT_API_PORT}/healthz",
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    if resp.status != 200:
                        return False
                    body = await resp.json()
                    return bool(body.get("ready"))
        except Exception:
            return False

    def _write_pidfile(self, pid: int) -> None:
        os.makedirs(os.path.dirname(BOT_PIDFILE_PATH), exist_ok=True)
        with open(BOT_PIDFILE_PATH, "w") as f:
            f.write(str(pid))

    def _read_pidfile(self) -> int | None:
        if not os.path.exists(BOT_PIDFILE_PATH):
            return None
        try:
            with open(BOT_PIDFILE_PATH) as f:
                return int(f.read().strip())
        except (ValueError, OSError):
            return None

    def _remove_pidfile(self) -> None:
        if os.path.exists(BOT_PIDFILE_PATH):
            os.remove(BOT_PIDFILE_PATH)
