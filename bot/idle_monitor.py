import asyncio
import logging
import time

import discord

import ec2_control
from config import ENABLE_AUTO_SHUTDOWN, IDLE_CHECK_INTERVAL_SECONDS, IDLE_SHUTDOWN_MINUTES
from slot_store import STATE_LINKING, SlotStore

log = logging.getLogger("idle_monitor")


class IdleMonitor:
    """Stops the EC2 instance after IDLE_SHUTDOWN_MINUTES with zero active
    voice connections and no /link in progress (a link involves no voice
    connection at all, but shouldn't get killed mid-flow)."""

    def __init__(self, bot: discord.Client, store: SlotStore):
        self.bot = bot
        self.store = store
        self._last_active = time.monotonic()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        threshold_seconds = IDLE_SHUTDOWN_MINUTES * 60
        while True:
            await asyncio.sleep(IDLE_CHECK_INTERVAL_SECONDS)

            linking = any(
                slot.state == STATE_LINKING
                for i in range(1, self.store.max_slots + 1)
                if (slot := self.store.get_by_index(i)) is not None
            )
            if self.bot.voice_clients or linking:
                self._last_active = time.monotonic()
                continue

            idle_for = time.monotonic() - self._last_active
            remaining = threshold_seconds - idle_for
            if remaining > 0:
                log.debug("idle for %.0fs, shutdown in %.0fs", idle_for, remaining)
                continue

            log.warning("no active voice connections for %s minutes", IDLE_SHUTDOWN_MINUTES)
            if ENABLE_AUTO_SHUTDOWN:
                try:
                    ec2_control.stop_this_instance()
                except Exception:
                    log.exception("failed to stop instance; will retry next check")
                    continue
            # Stop trying once we've fired (instance is shutting down anyway).
            return
