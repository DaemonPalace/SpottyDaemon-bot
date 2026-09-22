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

import asyncio
import logging
import secrets
import time

from aiohttp import web

import spotify_player_api
from commands import active_sessions_snapshot, do_delete_slot
from config import API_HOST, API_PORT, API_TOKEN, MAX_SLOTS
from librespot_manager import LibrespotManager
from slot_store import STATE_CLAIMED, SlotStore
from spotify_link import LinkManager
from spotify_web_api import WebApiLinkManager

log = logging.getLogger("api")

_START_MONOTONIC = time.monotonic()


class DiagnosticsApi:
    """Also the Discord-independent slot-lifecycle API the supervisor drives
    (link/select/delete/web-api-link/player/queue) -- see the module
    docstring's scope note above for why this stays one file: it already
    owns the slot_store/librespot wiring these new routes need, and a
    second bot-side HTTP module would just duplicate that."""

    def __init__(
        self,
        client,
        librespot: LibrespotManager,
        slot_store: SlotStore,
        link_manager: LinkManager,
        web_api_link_manager: WebApiLinkManager,
    ):
        self.client = client
        self.librespot = librespot
        self.slot_store = slot_store
        self.link_manager = link_manager
        self.web_api_link_manager = web_api_link_manager
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
        app.router.add_post("/api/slots/link/start", self._link_start)
        app.router.add_post("/api/slots/link/finish", self._link_finish)
        app.router.add_post("/api/slots/{name}/select", self._slot_select)
        app.router.add_delete("/api/slots/{name}", self._slot_delete)
        app.router.add_post("/api/slots/{name}/web-api-link/start", self._web_api_link_start)
        app.router.add_post("/api/slots/{name}/web-api-link/finish", self._web_api_link_finish)
        app.router.add_get("/api/slots/{name}/player-state", self._player_state)
        app.router.add_post("/api/slots/{name}/queue", self._player_queue_add)
        app.router.add_get("/api/slots/{name}/search", self._player_search)
        app.router.add_get("/api/slots/{name}/library/albums", self._library_albums)
        app.router.add_get("/api/slots/{name}/albums/{album_id}", self._album_detail)
        app.router.add_post("/api/slots/{name}/settings", self._slot_settings)
        app.router.add_get("/api/slots/{name}/recently-played", self._recently_played)
        app.router.add_get("/api/slots/{name}/playlists", self._playlists)
        app.router.add_get("/api/slots/{name}/playlists/{playlist_id}", self._playlist_detail)
        app.router.add_get("/api/slots/{name}/playlists/{playlist_id}/track-count", self._playlist_track_count)
        app.router.add_post("/api/slots/{name}/playlists/{playlist_id}/play", self._playlist_play)
        app.router.add_post("/api/slots/{name}/playlists/{playlist_id}/queue-all", self._playlist_queue_all)
        app.router.add_post("/api/slots/{name}/player/play-track", self._player_play_track)
        app.router.add_post("/api/slots/{name}/player/play", self._player_play)
        app.router.add_post("/api/slots/{name}/player/pause", self._player_pause)
        app.router.add_post("/api/slots/{name}/player/next", self._player_next)
        app.router.add_post("/api/slots/{name}/player/previous", self._player_previous)
        app.router.add_post("/api/slots/{name}/player/seek", self._player_seek)
        app.router.add_post("/api/slots/{name}/player/volume", self._player_volume)
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
            "avatar_url": meta.avatar_url,
            "display_name": meta.spotify_display_name,
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

    async def _link_start(self, request: web.Request) -> web.Response:
        """Web-originated equivalent of /link. There's no Discord user here,
        so the supervisor mints a synthetic user_id per link attempt --
        LinkManager only uses it as an opaque key to track one pending link
        at a time, same as a real Discord user id would be."""
        body = await request.json()
        slot_name = body.get("slot_name", "")
        password = body.get("password", "")
        if not slot_name or not password:
            raise web.HTTPBadRequest(text="slot_name and password are required")
        user_id = "web:" + secrets.token_hex(8)
        content, success = await self.link_manager.start_link(user_id, slot_name, password)
        authorize_url = self.link_manager.authorize_url(user_id)
        return web.json_response(
            {"message": content, "success": success, "user_id": user_id, "authorize_url": authorize_url}
        )

    async def _link_finish(self, request: web.Request) -> web.Response:
        body = await request.json()
        user_id = body.get("user_id", "")
        pasted_url = body.get("pasted_url")
        if not user_id:
            raise web.HTTPBadRequest(text="user_id is required")
        content, success = await self.link_manager.finish_link(user_id, pasted_url)
        return web.json_response({"message": content, "success": success})

    async def _slot_select(self, request: web.Request) -> web.Response:
        """Verifies a slot's password without joining voice -- the web
        equivalent of the password check /connect does on Discord's side,
        used for the UI's slot-profile screen."""
        name = request.match_info["name"]
        body = await request.json()
        password = body.get("password", "")
        if not self.slot_store.verify_password(name, password):
            raise web.HTTPUnauthorized(text="wrong password")
        meta = self.slot_store.get_by_name(name)
        assert meta is not None
        if meta.avatar_url is None and meta.web_api_refresh_token is not None:
            # Backfills a slot that was web-api-linked before profile
            # seeding existed. Fire-and-forget: the dashboard just shows an
            # initial-letter avatar until the next select() if this fails.
            asyncio.create_task(self._seed_profile_info(meta.index))
        return web.json_response(
            {
                "name": meta.friendly_name,
                "state": meta.state,
                "web_api_linked": meta.web_api_refresh_token is not None,
                "avatar_url": meta.avatar_url,
                "display_name": meta.spotify_display_name,
            }
        )

    async def _slot_delete(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        content, _ephemeral = await do_delete_slot(name, self.slot_store, self.librespot)
        return web.json_response({"message": content})

    async def _web_api_link_start(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        meta = self.slot_store.get_by_name(name)
        if meta is None:
            raise web.HTTPNotFound(text=f"no slot named {name!r}")
        if meta.state != STATE_CLAIMED:
            raise web.HTTPBadRequest(text=f"slot {name!r} isn't claimed yet -- link it first")
        user_id = "web:" + secrets.token_hex(8)
        content, success = self.web_api_link_manager.start_link(user_id, meta.index)
        authorize_url = self.web_api_link_manager.authorize_url(user_id)
        return web.json_response(
            {"message": content, "success": success, "user_id": user_id, "authorize_url": authorize_url}
        )

    async def _web_api_link_finish(self, request: web.Request) -> web.Response:
        body = await request.json()
        user_id = body.get("user_id", "")
        pasted_url = body.get("pasted_url", "")
        if not user_id or not pasted_url:
            raise web.HTTPBadRequest(text="user_id and pasted_url are required")
        slot_index = self.web_api_link_manager.pending_slot_index(user_id)
        content, success = await self.web_api_link_manager.finish_link(user_id, pasted_url)
        if success and slot_index is not None:
            await self._seed_profile_info(slot_index)
        return web.json_response({"message": content, "success": success})

    async def _seed_profile_info(self, slot_index: int) -> None:
        """Best-effort: populate the profile-selector avatar/name from the
        Spotify account that was just linked. Never fails the link itself --
        the dashboard falls back to an initial-letter avatar if this is
        unavailable (e.g. the account has no profile picture)."""
        try:
            token = await self.web_api_link_manager.get_access_token(slot_index)
            profile = await spotify_player_api.get_current_user_profile(token) if token else None
            if profile:
                images = profile.get("images") or []
                avatar_url = images[0]["url"] if images else None
                await self.slot_store.set_profile_info(slot_index, avatar_url, profile.get("display_name"))
        except Exception:
            log.exception("failed to seed profile info for slot %s", slot_index)

    async def _get_slot_access_token(self, name: str) -> str:
        """Small helper shared by the player/queue routes. Raises an
        aiohttp HTTP exception directly if the slot doesn't exist or hasn't
        completed Spotify Web API linking."""
        meta = self.slot_store.get_by_name(name)
        if meta is None:
            raise web.HTTPNotFound(text=f"no slot named {name!r}")
        if meta.web_api_refresh_token is None:
            raise web.HTTPBadRequest(text=f"slot {name!r} hasn't completed Spotify Web API linking")
        token = await self.web_api_link_manager.get_access_token(meta.index)
        if token is None:
            raise web.HTTPBadRequest(text=f"slot {name!r} hasn't completed Spotify Web API linking")
        return token

    async def _player_state(self, request: web.Request) -> web.Response:
        """Now-playing + queue in one round trip -- the dashboard and jam
        view poll this every few seconds, and now-playing/queue used to be
        two separate Spotify API calls per poll for no reason (they're
        always needed together)."""
        token = await self._get_slot_access_token(request.match_info["name"])
        now_playing, queue = await asyncio.gather(
            spotify_player_api.get_now_playing(token),
            spotify_player_api.get_queue(token),
        )
        return web.json_response({"now_playing": now_playing, "queue": queue})

    async def _player_queue_add(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        body = await request.json()
        uri = body.get("uri", "")
        if not uri:
            raise web.HTTPBadRequest(text="uri is required")
        await spotify_player_api.add_to_queue(token, uri)
        return web.json_response({"queued": uri})

    async def _player_search(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        query = request.query.get("q", "").strip()
        if not query:
            return web.json_response({"tracks": []})
        tracks = await spotify_player_api.search_tracks(token, query)
        return web.json_response({"tracks": tracks})

    async def _library_albums(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        albums = await spotify_player_api.get_saved_albums(token)
        if albums is None:
            return web.json_response({"albums": None, "needs_reauth": True})
        return web.json_response({"albums": albums})

    async def _album_detail(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        album = await spotify_player_api.get_album(token, request.match_info["album_id"])
        return web.json_response(album)


    async def _slot_settings(self, request: web.Request) -> web.Response:
        """Settings screen: re-verifies the current password (same
        confirm-before-destructive-change pattern as slot delete) before
        applying a rename, password change, and/or avatar override."""
        name = request.match_info["name"]
        body = await request.json()
        current_password = body.get("current_password", "")
        if not self.slot_store.verify_password(name, current_password):
            raise web.HTTPUnauthorized(text="wrong password")
        meta = self.slot_store.get_by_name(name)
        assert meta is not None
        error = await self.slot_store.update_settings(
            meta.index,
            new_name=body.get("new_name") or None,
            new_password=body.get("new_password") or None,
            avatar_url=body.get("avatar_url"),
        )
        if error:
            raise web.HTTPBadRequest(text=error)
        updated = self.slot_store.get_by_index(meta.index)
        return web.json_response({"name": updated.friendly_name, "avatar_url": updated.avatar_url})

    async def _recently_played(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        tracks = await spotify_player_api.get_recently_played(token)
        if tracks is None:
            return web.json_response({"tracks": None, "needs_reauth": True})
        return web.json_response({"tracks": tracks})

    async def _playlists(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        playlists = await spotify_player_api.get_playlists(token)
        if playlists is None:
            return web.json_response({"playlists": None, "needs_reauth": True})
        return web.json_response({"playlists": playlists})

    async def _playlist_detail(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        playlist = await spotify_player_api.get_playlist(token, request.match_info["playlist_id"])
        return web.json_response(playlist)

    async def _playlist_track_count(self, request: web.Request) -> web.Response:
        """/me/playlists (the _playlists route above) always reports 0 for
        tracks.total -- a Spotify API bug -- so the grid fetches the real
        count per tile from here instead, off the single-playlist endpoint."""
        token = await self._get_slot_access_token(request.match_info["name"])
        total = await spotify_player_api.get_playlist_track_count(token, request.match_info["playlist_id"])
        return web.json_response({"total": total})

    async def _playlist_play(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        await spotify_player_api.play_context(token, f"spotify:playlist:{request.match_info['playlist_id']}")
        return web.json_response({"ok": True})

    async def _playlist_queue_all(self, request: web.Request) -> web.Response:
        """Spotify's queue endpoint only takes one track at a time -- no
        batch/context form -- so a whole-playlist queue is just this loop."""
        token = await self._get_slot_access_token(request.match_info["name"])
        playlist = await spotify_player_api.get_playlist(token, request.match_info["playlist_id"])
        tracks = [item["track"] for item in playlist.get("tracks", {}).get("items", []) if item.get("track")]
        for track in tracks:
            await spotify_player_api.add_to_queue(token, track["uri"])
        return web.json_response({"queued": len(tracks)})

    async def _player_play_track(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        body = await request.json()
        uri = body.get("uri", "")
        if not uri:
            raise web.HTTPBadRequest(text="uri is required")
        await spotify_player_api.play_uri(token, uri)
        return web.json_response({"ok": True})

    async def _player_play(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        await spotify_player_api.play(token)
        return web.json_response({"ok": True})

    async def _player_pause(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        await spotify_player_api.pause(token)
        return web.json_response({"ok": True})

    async def _player_next(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        await spotify_player_api.next_track(token)
        return web.json_response({"ok": True})

    async def _player_previous(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        await spotify_player_api.previous_track(token)
        return web.json_response({"ok": True})

    async def _player_seek(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        body = await request.json()
        position_ms = body.get("position_ms")
        if position_ms is None:
            raise web.HTTPBadRequest(text="position_ms is required")
        await spotify_player_api.seek(token, int(position_ms))
        return web.json_response({"ok": True})

    async def _player_volume(self, request: web.Request) -> web.Response:
        token = await self._get_slot_access_token(request.match_info["name"])
        body = await request.json()
        percent = body.get("percent")
        if percent is None:
            raise web.HTTPBadRequest(text="percent is required")
        await spotify_player_api.set_volume(token, int(percent))
        return web.json_response({"ok": True})
