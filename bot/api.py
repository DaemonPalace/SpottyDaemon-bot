"""Read-only diagnostics REST API: healthcheck, latency, per-slot audio
state, active sessions. Built on aiohttp.web (aiohttp is already a
dependency for outbound calls elsewhere in this repo, so this adds no new
dependency). Follows the same .start()/.stop() async-task convention as
IdleMonitor/InteractionRelay/LinkManager, wired into main.py's on_ready.

Binds to API_HOST:API_PORT (127.0.0.1 by default -- not exposed off-box
unless the self-hoster opts in). If API_TOKEN is set, every route except
/healthz requires "Authorization: Bearer <token>"; if unset, a one-time
startup warning is logged and all routes are open -- fine for diagnostics
tooling and a local UI, not a substitute for real auth if this is ever
exposed beyond localhost.
"""

import logging
import time

from aiohttp import web

from commands import active_sessions_snapshot
from config import API_HOST, API_PORT, API_TOKEN, MAX_SLOTS
from librespot_manager import LibrespotManager
from slot_store import STATE_CLAIMED, SlotStore

log = logging.getLogger("api")

_START_MONOTONIC = time.monotonic()


class DiagnosticsApi:
    def __init__(self, client, librespot: LibrespotManager, slot_store: SlotStore):
        self.client = client
        self.librespot = librespot
        self.slot_store = slot_store
        self._runner: web.AppRunner | None = None

        if not API_TOKEN:
            log.warning("API_TOKEN not set -- diagnostics API is unauthenticated; do not expose beyond localhost")

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        if API_TOKEN and request.path != "/healthz":
            header = request.headers.get("Authorization", "")
            if header != f"Bearer {API_TOKEN}":
                raise web.HTTPUnauthorized(text="missing or invalid bearer token")
        return await handler(request)

    def _build_app(self) -> web.Application:
        app = web.Application(middlewares=[self._auth_middleware])
        app.router.add_get("/healthz", self._healthz)
        app.router.add_get("/api/latency", self._latency)
        app.router.add_get("/api/slots", self._slots)
        app.router.add_get("/api/slots/{name}/audio", self._slot_audio)
        app.router.add_get("/api/sessions", self._sessions)
        return app

    def start(self) -> None:
        import asyncio

        asyncio.create_task(self._run())

    async def _run(self) -> None:
        self._runner = web.AppRunner(self._build_app())
        await self._runner.setup()
        site = web.TCPSite(self._runner, API_HOST, API_PORT)
        await site.start()
        log.info("diagnostics API listening on http://%s:%d", API_HOST, API_PORT)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _healthz(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ready": self.client.is_ready(),
                "uptime_seconds": time.monotonic() - _START_MONOTONIC,
            }
        )

    async def _latency(self, request: web.Request) -> web.Response:
        return web.json_response({"gateway_latency_seconds": self.client.latency})

    def _slot_summary(self, index: int) -> dict | None:
        meta = self.slot_store.get_by_index(index)
        if meta is None:
            return None
        proc = self.librespot.processes.get(meta.friendly_name) if meta.friendly_name else None
        entry = {
            "index": meta.index,
            "name": meta.friendly_name,
            "state": meta.state,
            "running": proc.is_running() if proc else False,
        }
        if meta.state == STATE_CLAIMED and proc is not None:
            entry["audio"] = {
                "uptime_seconds": proc.uptime_seconds(),
                "backlog_seconds": proc.pipe_backlog_seconds(),
                "draining": proc.is_draining(),
                "restart_count": self.librespot.restart_count(meta.friendly_name),
            }
        return entry

    async def _slots(self, request: web.Request) -> web.Response:
        slots = [s for i in range(1, MAX_SLOTS + 1) if (s := self._slot_summary(i)) is not None]
        return web.json_response({"slots": slots})

    async def _slot_audio(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        proc = self.librespot.processes.get(name)
        if proc is None:
            raise web.HTTPNotFound(text=f"no running librespot process for slot {name!r}")
        return web.json_response(
            {
                "name": name,
                "running": proc.is_running(),
                "uptime_seconds": proc.uptime_seconds(),
                "backlog_seconds": proc.pipe_backlog_seconds(),
                "draining": proc.is_draining(),
                "restart_count": self.librespot.restart_count(name),
            }
        )

    async def _sessions(self, request: web.Request) -> web.Response:
        return web.json_response({"sessions": active_sessions_snapshot()})
