"""Command logic shared between the gateway CommandTree (main.py -- the
default and only path for a self-hosted/standalone install) and the
SQS-relayed path (interaction_relay.py, used only by the legacy AWS
deployment when a Discord Interactions Endpoint URL is configured)."""

import asyncio
import itertools
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field

import discord

import spotify_player_api
from config import SpotifySlot
from librespot_manager import LibrespotManager
from slot_store import STATE_CLAIMED, SlotStore
from spotify_link import LinkManager
from spotify_web_api import WebApiLinkManager
from up_next import UpNextManager

log = logging.getLogger("commands")


@dataclass
class _Session:
    """What slot each guild is *supposed* to be connected to right now, plus
    enough state to make self-heal reattaches safe and boundable.

    generation distinguishes this connect/reconnect from any earlier one to
    the *same* slot_name -- without it, an in-flight _reattach timer from a
    session that was superseded by /reconnect (same slot name, fresh
    process) could still match on slot_name alone and race the new attach,
    both calling voice_client.play() around the same time. Every do_connect
    and do_reconnect mints a new generation, so any stale timer's check
    fails immediately regardless of slot name.

    reattach_failures / last_attach_started bound the self-heal loop: if
    ffmpeg keeps dying within _FLAP_WINDOW_SECONDS of each reattach (e.g.
    librespot has no valid Spotify Connect context to actually stream --
    restarting librespot doesn't fix that), we stop retrying after
    _MAX_REATTACH_ATTEMPTS and post one notice instead of hammering
    librespot/Discord forever."""

    slot_name: str
    generation: int
    notify_channel_id: int | None
    reattach_failures: int = 0
    last_attach_started: float = field(default_factory=time.monotonic)


_active_sessions: dict[int, _Session] = {}
_session_counter = itertools.count(1)

_REATTACH_DELAY_SECONDS = 2
# A reattach that dies again within this long of starting counts as part of
# the same flap storm; longer than that (e.g. a normal Spotify pause) resets
# the counter instead of counting against the cap.
_FLAP_WINDOW_SECONDS = 10
_MAX_REATTACH_ATTEMPTS = 3
# How long /reconnect waits after stopping the old librespot process before
# starting a fresh one -- see do_reconnect for why.
_RECONNECT_SETTLE_SECONDS = 3


def _lookup_verified_slot(
    slot_name: str, password: str, store: SlotStore, librespot: LibrespotManager
) -> tuple[SpotifySlot, None] | tuple[None, tuple[str, bool]]:
    """Shared slot-name + password check used by both /connect and
    /reconnect. Returns (spotify_slot, None) on success or (None, error)
    where error is the (content, ephemeral) tuple to return to the user."""
    slot_meta = store.get_by_name(slot_name)
    if slot_meta is None:
        return None, (f"No slot named '{slot_name}'. Ask your friend to /link one first.", True)
    if not store.verify_password(slot_name, password):
        return None, ("Wrong password for that slot.", True)
    if slot_meta.state != STATE_CLAIMED:
        # Invites set the password at sign-up, before Spotify is linked.
        return None, ("That profile isn't linked to Spotify yet -- finish linking in the dashboard first.", True)
    spotify_slot = librespot.slot_by_index(slot_meta.index)
    assert spotify_slot is not None
    return spotify_slot, None


async def do_connect(
    guild: discord.Guild,
    member: discord.Member,
    slot_name: str,
    password: str,
    librespot: LibrespotManager,
    store: SlotStore,
    web_api_link_manager: WebApiLinkManager,
    channel_id: int | None = None,
) -> tuple[str, bool]:
    """Returns (content, ephemeral). channel_id is the text channel the
    command was run from, if any -- kept around so a bounded-out self-heal
    loop (see _reattach) has somewhere to post a "still broken" notice."""
    if member.voice is None or member.voice.channel is None:
        return "Join a voice channel first.", True

    spotify_slot, error = _lookup_verified_slot(slot_name, password, store, librespot)
    if error is not None:
        return error

    proc = librespot.processes.get(spotify_slot.name)
    if proc is None or not proc.is_running():
        return "That slot isn't connected to Spotify right now -- check the bot logs.", True

    channel = member.voice.channel
    try:
        voice_client = guild.voice_client
        if voice_client is None:
            voice_client = await channel.connect()
        elif voice_client.channel.id != channel.id:
            await voice_client.move_to(channel)

        if voice_client.is_playing():
            # Triggers the previous source's after-callback, which resumes
            # draining on whatever proc was playing before -- and, since we
            # overwrite _active_sessions below first, that old callback's
            # reattach check will see it's no longer the active session.
            voice_client.stop()

        generation = next(_session_counter)
        _active_sessions[guild.id] = _Session(slot_name, generation, channel_id)
        _attach_source(guild, voice_client, slot_name, generation, spotify_slot, librespot)
    except Exception:
        stale_proc = librespot.processes.get(spotify_slot.name)
        if stale_proc is not None:
            stale_proc.resume_draining()
        log.exception("connect command failed")
        return "Something went wrong connecting/starting playback — check the bot logs.", True

    await _activate_on_connect(spotify_slot, store, web_api_link_manager)
    return f"Connected. Control playback from Spotify Connect on slot **{slot_name}**.", False


async def do_reconnect(
    guild: discord.Guild,
    member: discord.Member,
    slot_name: str,
    password: str,
    librespot: LibrespotManager,
    store: SlotStore,
    web_api_link_manager: WebApiLinkManager,
    channel_id: int | None = None,
) -> tuple[str, bool]:
    """Forces a full disconnect+reconnect for one command: restarts the
    slot's librespot process from scratch and rejoins voice. No Spotify
    login prompt -- librespot reuses the slot's persisted credentials.json,
    the same as a normal restart. Returns (content, ephemeral)."""
    if member.voice is None or member.voice.channel is None:
        return "Join a voice channel first.", True

    spotify_slot, error = _lookup_verified_slot(slot_name, password, store, librespot)
    if error is not None:
        return error

    channel = member.voice.channel
    try:
        # Drop any in-flight self-heal reattach for whatever was running
        # before, and any existing voice connection, before restarting
        # librespot out from under it.
        forget_session(guild.id)
        if guild.voice_client is not None:
            await guild.voice_client.disconnect(force=True)

        await librespot.stop_one(spotify_slot.name)
        # Give Spotify's backend a moment to release the old Connect session
        # for this device before a new one claims the same device identity.
        # Observed in production: skipping this straight into start_one can
        # leave the fresh session churning (device active/inactive, context
        # lost, same track reloading on repeat) for a couple minutes before
        # it settles on its own.
        await asyncio.sleep(_RECONNECT_SETTLE_SECONDS)
        await librespot.start_one(spotify_slot)

        voice_client = await channel.connect()
        generation = next(_session_counter)
        _active_sessions[guild.id] = _Session(slot_name, generation, channel_id)
        _attach_source(guild, voice_client, slot_name, generation, spotify_slot, librespot)
    except Exception:
        stale_proc = librespot.processes.get(spotify_slot.name)
        if stale_proc is not None:
            stale_proc.resume_draining()
        log.exception("reconnect command failed")
        return "Something went wrong reconnecting — check the bot logs.", True

    await _activate_on_connect(spotify_slot, store, web_api_link_manager)
    return f"Reconnected. Control playback from Spotify Connect on slot **{slot_name}**.", False


# Frames (20ms each) buffered between ffmpeg and discord.py's player thread.
# ponytail: stale audio up to this long can play after an API skip, since
# flush_slot_pipe() only clears the FIFO -- clear this too if that's audible.
_SOURCE_BUFFER_FRAMES = 3


class _GaplessSource(discord.AudioSource):
    """Wraps FFmpegPCMAudio so an empty pipe means silence, not end of
    stream. discord.py ends a source on the first short read, and ffmpeg's
    stdout goes quiet every time Spotify pauses (librespot stops writing;
    the FIFO never EOFs because LibrespotProcess holds a keepalive write
    fd). A pump thread reads ffmpeg into a small bounded queue -- blocking
    on a full queue keeps backpressure flowing to librespot, so the player's
    20ms clock stays the only clock -- and read() never blocks: it hands
    back a queued frame or silence. The stream ends only when ffmpeg itself
    does (crash, or the FIFO's last writer closing on slot stop)."""

    _SILENCE = b"\x00" * discord.opus.Encoder.FRAME_SIZE

    def __init__(self, inner: discord.AudioSource):
        self._inner = inner
        self._frames: queue.Queue[bytes] = queue.Queue(maxsize=_SOURCE_BUFFER_FRAMES)
        self._stopped = threading.Event()
        threading.Thread(target=self._pump, name="gapless-pump", daemon=True).start()

    def _pump(self) -> None:
        while True:
            try:
                frame = self._inner.read()
            except Exception:
                if not self._stopped.is_set():
                    log.exception("ffmpeg source failed")
                frame = b""
            while not self._stopped.is_set():
                try:
                    self._frames.put(frame, timeout=0.1)
                    break
                except queue.Full:
                    pass
            if not frame or self._stopped.is_set():
                return

    def read(self) -> bytes:
        try:
            return self._frames.get_nowait()
        except queue.Empty:
            return self._SILENCE

    def cleanup(self) -> None:
        self._stopped.set()
        self._inner.cleanup()


def _attach_source(
    guild: discord.Guild,
    voice_client: discord.VoiceClient,
    slot_name: str,
    generation: int,
    spotify_slot: SpotifySlot,
    librespot: LibrespotManager,
) -> None:
    """Starts (or restarts) playback of a slot's pipe into an already-connected
    voice_client, wiring up the after-callback that both resumes pipe
    draining and, if this guild is still supposed to be connected to this
    slot, self-heals by reattaching a fresh source. Shared by do_connect,
    do_reconnect and _reattach so retries behave identically to the initial
    connect. Fetches the current LibrespotProcess by name (rather than
    taking one as an argument) so this stays correct even if the process
    behind spotify_slot.name got restarted (e.g. by /reconnect or a
    self-heal watchdog) between calls.

    generation must match the caller's _Session.generation for the
    after-callback to trigger a reattach -- see _Session's docstring for why
    slot_name alone isn't enough to tell a live session from a superseded
    one."""
    proc = librespot.get(spotify_slot.name)
    loop = asyncio.get_running_loop()

    # Reset the flap-window clock on every (re)attach, not just the first --
    # _reattach measures elapsed time since *this* attach to tell a flap
    # apart from a stop after a long, healthy playback stretch.
    session = _active_sessions.get(guild.id)
    if session is not None and session.slot_name == slot_name and session.generation == generation:
        session.last_attach_started = time.monotonic()

    # Stop competing with ffmpeg for bytes on this slot's pipe -- see
    # librespot_manager.py's module docstring for why this exists.
    proc.pause_draining()
    # Discard whatever's queued as of right now -- ffmpeg hasn't spawned
    # yet (that happens below), so without this, whatever built up before
    # attach (or during the idle-drain-paused/ffmpeg-not-listening-yet gap
    # that just opened) plays out first once ffmpeg's read loop starts,
    # which is exactly the "distorted, sped-up first few seconds" symptom.
    # Same mechanism as flush_slot_pipe()'s use around API-driven track
    # changes, just for the attach-a-new-reader case instead of the
    # swap-which-track-is-playing case.
    proc.flush()

    def _after_playback(error: Exception | None) -> None:
        if error:
            log.error("playback error for slot %s: %s", spotify_slot.name, error)
        # Called from the audio player thread, not the event loop.
        current = librespot.processes.get(spotify_slot.name)
        if current is not None:
            loop.call_soon_threadsafe(current.resume_draining)
        session = _active_sessions.get(guild.id)
        if session is not None and session.slot_name == slot_name and session.generation == generation:
            asyncio.run_coroutine_threadsafe(
                _reattach(guild, slot_name, generation, spotify_slot, librespot), loop
            )

    source = _GaplessSource(discord.FFmpegPCMAudio(
        source=spotify_slot.pipe_path,
        before_options="-f s16le -ar 44100 -ac 2",
        # soxr: better 44.1k->48k resampling than swr's default. Needs an
        # ffmpeg built with --enable-libsoxr; without it ffmpeg exits at
        # startup and _reattach's flap cap posts the "stuck" notice.
        options="-vn -af aresample=resampler=soxr",
    ))
    voice_client.play(source, after=_after_playback)


async def _reattach(
    guild: discord.Guild,
    slot_name: str,
    generation: int,
    spotify_slot: SpotifySlot,
    librespot: LibrespotManager,
) -> None:
    """Safety net for ffmpeg exiting on its own before the user runs
    /disconnect. Spotify pausing used to do this (librespot closes its FIFO
    write end on pause, a clean EOF to ffmpeg) -- LibrespotProcess's
    keepalive write fd plus _GaplessSource now keep that from ending the
    stream, so this only fires on real failures (ffmpeg crashing, a voice
    error). If this guild is still supposed to be connected to this slot,
    transparently reattach a fresh source.

    Bounded: if reattaches keep dying within _FLAP_WINDOW_SECONDS of each
    other (observed in production when librespot has no valid Spotify
    Connect context to stream -- restarting librespot, e.g. via /reconnect,
    doesn't fix that, so retrying forever just hammers librespot/Discord),
    give up after _MAX_REATTACH_ATTEMPTS and post one notice instead."""
    await asyncio.sleep(_REATTACH_DELAY_SECONDS)
    session = _active_sessions.get(guild.id)
    if session is None or session.slot_name != slot_name or session.generation != generation:
        return  # superseded by a newer /connect, /reconnect, or /disconnect meanwhile
    voice_client = guild.voice_client
    if voice_client is None or not voice_client.is_connected() or voice_client.is_playing():
        return
    proc = librespot.processes.get(spotify_slot.name)
    if proc is None or not proc.is_running():
        log.warning(
            "slot %s for guild %s has no running librespot process, skipping reattach",
            slot_name, guild.id,
        )
        return

    since_last_attach = time.monotonic() - session.last_attach_started
    if since_last_attach >= _FLAP_WINDOW_SECONDS:
        session.reattach_failures = 0  # that attach ran fine for a while -- not a flap
    session.reattach_failures += 1

    if session.reattach_failures > _MAX_REATTACH_ATTEMPTS:
        log.warning(
            "giving up auto-reattach for slot %s guild %s after %d attempts within %.0fs",
            slot_name, guild.id, session.reattach_failures - 1, _FLAP_WINDOW_SECONDS,
        )
        await _notify_stuck(guild, session)
        return  # stop retrying -- a fresh /connect or /reconnect starts a new session

    log.info(
        "reattaching to slot %s for guild %s after unexpected stop (attempt %d/%d)",
        slot_name, guild.id, session.reattach_failures, _MAX_REATTACH_ATTEMPTS,
    )
    try:
        _attach_source(guild, voice_client, slot_name, generation, spotify_slot, librespot)
    except Exception:
        proc.resume_draining()
        log.exception("failed to reattach slot %s for guild %s", slot_name, guild.id)


async def _notify_stuck(guild: discord.Guild, session: _Session) -> None:
    """Best-effort: tell whoever can see the channel that auto-recovery gave
    up, since the alternative is silence that looks identical to "still
    working on it"."""
    channel = guild.get_channel(session.notify_channel_id) if session.notify_channel_id else None
    if channel is None:
        log.warning(
            "no channel to notify for guild %s slot %s -- auto-reattach gave up silently",
            guild.id, session.slot_name,
        )
        return
    try:
        await channel.send(
            f"Lost audio on slot **{session.slot_name}** and couldn't recover automatically. "
            "Make sure Spotify is actually playing something on this device, then run /reconnect."
        )
    except Exception:
        log.exception("failed to post stuck-playback notice for guild %s", guild.id)


async def do_disconnect(guild: discord.Guild) -> tuple[str, bool]:
    """Returns (content, ephemeral)."""
    forget_session(guild.id)
    if guild.voice_client is not None:
        await guild.voice_client.disconnect(force=True)
        return "Disconnected.", False
    return "Not connected.", True


def forget_session(guild_id: int) -> None:
    """Call before any voice_client.disconnect()/stop() that ISN'T /connect
    switching slots -- e.g. main.py's on_voice_state_update auto-leaving an
    empty channel -- so the outgoing source's after-callback doesn't
    self-heal-reattach a session nobody wants anymore."""
    _active_sessions.pop(guild_id, None)


def get_active_slot_name(guild_id: int) -> str | None:
    """Which slot a guild is currently /connect-ed to, if any -- used by
    /jam and /play to find the Spotify account they should act on."""
    session = _active_sessions.get(guild_id)
    return session.slot_name if session is not None else None


def resolve_jam_slot(guild_id: int, store: SlotStore) -> tuple[int, None] | tuple[None, tuple[str, bool]]:
    """Shared precondition check for /jam and /play: the guild needs an
    active /connect session, and that slot needs to be Web-API-linked.
    Returns (slot_index, None) on success or (None, error) where error is
    the (content, ephemeral) tuple to return to the user."""
    slot_name = get_active_slot_name(guild_id)
    if slot_name is None:
        return None, ("Connect a slot first with /connect.", True)
    slot_meta = store.get_by_name(slot_name)
    if slot_meta is None or slot_meta.web_api_refresh_token is None:
        return None, (f"Slot '{slot_name}' isn't linked for Spotify Web API yet -- run /link-web-api first.", True)
    return slot_meta.index, None


async def resolve_play_token(
    guild_id: int, store: SlotStore, web_api_link_manager: WebApiLinkManager
) -> tuple[str, int, None] | tuple[None, None, tuple[str, bool]]:
    """Shared by do_search_tracks/do_play_track: resolves the guild's
    active slot down to a usable Spotify access token (and its index,
    needed by do_play_track to flush the right pipe), or the error to
    show the user."""
    slot_index, error = resolve_jam_slot(guild_id, store)
    if error is not None:
        return None, None, error
    token = await web_api_link_manager.get_access_token(slot_index)
    if token is None:
        return None, None, ("Spotify Web API isn't linked for this slot.", True)
    return token, slot_index, None


def flush_slot_pipe(slot_index: int, store: SlotStore, librespot: LibrespotManager) -> None:
    """Discards whatever's currently queued in the slot's audio pipe --
    call this right before issuing an API-driven track change (play_uri,
    skip, rewind) so the old track's leftover tail never reaches ffmpeg
    mixed in with the new track's head. See librespot_manager.py's
    LibrespotProcess.flush() for why this matters. A no-op if the slot
    isn't actually running (nothing to flush)."""
    slot_meta = store.get_by_index(slot_index)
    if slot_meta is None or slot_meta.friendly_name is None:
        return
    proc = librespot.processes.get(slot_meta.friendly_name)
    if proc is not None:
        proc.flush()


async def do_search_tracks(
    guild_id: int, query: str, store: SlotStore, web_api_link_manager: WebApiLinkManager
) -> tuple[list[dict], None] | tuple[None, tuple[str, bool]]:
    """Returns (results, None) -- results may be an empty list if Spotify
    genuinely has no matches -- or (None, error). Used by /play to show up
    to 5 tracks as a Play/Queue button picker instead of blindly guessing
    the top hit (autocomplete can't be relied on to have run first, see
    interaction_relay.py)."""
    token, _slot_index, error = await resolve_play_token(guild_id, store, web_api_link_manager)
    if error is not None:
        return None, error
    results = await spotify_player_api.search_tracks(token, query)
    return results[:5], None


async def do_play_track(
    guild_id: int,
    track_uri: str,
    mode: str,
    store: SlotStore,
    web_api_link_manager: WebApiLinkManager,
    librespot: LibrespotManager,
    up_next: UpNextManager,
) -> tuple[str, bool]:
    """Returns (content, ephemeral). `mode` is "play_now" or "queue" --
    called when a specific track's Play/Queue button (from
    do_search_tracks' results) is clicked."""
    token, slot_index, error = await resolve_play_token(guild_id, store, web_api_link_manager)
    if error is not None:
        return error
    try:
        if mode == "play_now":
            # Flush right before the command that actually swaps which
            # track is playing -- add_to_queue doesn't cause an immediate
            # transition, so it's left alone.
            flush_slot_pipe(slot_index, store, librespot)
            await _activate_then_play(token, slot_index, track_uri, librespot)
            return "Playing now.", True

        # Spotify's queue-add endpoint can only append to an *existing*
        # active session -- it can't start playback from nothing. If the
        # slot's idle, queueing a track would silently do nothing useful,
        # so play it directly instead.
        now_playing = await spotify_player_api.get_now_playing(token)
        if not now_playing or not now_playing.get("is_playing"):
            flush_slot_pipe(slot_index, store, librespot)
            await _activate_then_play(token, slot_index, track_uri, librespot)
            return "Nothing was playing, so playing this now instead.", True

        track = await spotify_player_api.get_track(token, track_uri)
        await up_next.add(slot_index, [track])
        return "Added to Up next.", True
    except Exception:
        # Most commonly Spotify's 404 NO_ACTIVE_DEVICE -- librespot hasn't
        # been picked up as the active Connect device yet. An uncaught
        # exception here would otherwise leave a relayed button click
        # stuck on "thinking..." forever (interaction_relay.py's outer
        # handler logs and swallows it with no followup sent at all).
        return "That didn't work -- is Spotify Connect active on this slot? Try /connect first.", True


async def _activate_then_play(token: str, slot_index: int, track_uri: str, librespot: LibrespotManager) -> None:
    """Activates the slot's device on its own first if it isn't already
    the active one, THEN plays the track as a separate call -- see
    spotify_player_api.transfer_playback for why combining "activate" and
    "load this contextless track" into a single /me/player/play call
    caused a reload-and-fail loop. A lookup/transfer failure here isn't
    fatal: play_uri still works fine on its own once some device is
    already active, which is the common (non-cold) case."""
    slot = librespot.slot_by_index(slot_index)
    if slot is not None:
        try:
            await _activate_device(token, slot)
        except Exception:
            log.exception("failed to activate device for slot index %s", slot_index)
    await spotify_player_api.play_uri(token, track_uri)


async def _activate_device(token: str, slot: SpotifySlot) -> bool:
    """Makes the slot's librespot the active Spotify Connect device if it
    isn't already. Returns False if Spotify doesn't list the device (yet)."""
    device = await spotify_player_api.find_device(token, slot.name)
    if device is None:
        return False
    if not device.get("is_active") and device.get("id"):
        await spotify_player_api.transfer_playback(token, device["id"])
    return True


# A freshly (re)started librespot takes a moment to show up in Spotify's
# device list -- /reconnect hits this every time.
_ACTIVATE_ATTEMPTS = 5
_ACTIVATE_RETRY_SECONDS = 1


async def _activate_on_connect(
    spotify_slot: SpotifySlot, store: SlotStore, web_api_link_manager: WebApiLinkManager
) -> None:
    """Best-effort, once per /connect or /reconnect: activate the slot's
    device so the web UI's play/playlist/queue calls work straight away
    instead of failing with NO_ACTIVE_DEVICE until someone picks the device
    in a Spotify app. A no-op for slots without a Web API link."""
    slot_meta = store.get_by_index(spotify_slot.index)
    if slot_meta is None or slot_meta.web_api_refresh_token is None:
        return
    try:
        token = await web_api_link_manager.get_access_token(slot_meta.index)
        if token is None:
            return
        for _ in range(_ACTIVATE_ATTEMPTS):
            if await _activate_device(token, spotify_slot):
                return
            await asyncio.sleep(_ACTIVATE_RETRY_SECONDS)
        log.warning("slot %s never showed up in Spotify's device list, not activated", spotify_slot.name)
    except Exception:
        log.exception("failed to activate device for slot %s on connect", spotify_slot.name)


def active_sessions_snapshot() -> list[dict]:
    """JSON-safe view of _active_sessions for the diagnostics API -- omits
    the generation counter (internal race-guard, not diagnostic info)."""
    return [
        {
            "guild_id": guild_id,
            "slot_name": session.slot_name,
            "notify_channel_id": session.notify_channel_id,
            "reattach_failures": session.reattach_failures,
        }
        for guild_id, session in _active_sessions.items()
    ]


async def do_link(
    user_id: str, slot_name: str, password: str, link_manager: LinkManager
) -> tuple[str, bool]:
    """Returns (content, ephemeral). Always ephemeral -- this is always sent
    to whoever ran the command, never posted to the channel. Only links a
    profile the admin approved from a dashboard invite (slot_store.py)."""
    if not link_manager.store.verify_password(slot_name.strip().lower(), password):
        return "Wrong profile name or password.", True
    content, _success = await link_manager.start_link(user_id, slot_name)
    return content, True


async def do_link_finish(
    user_id: str, pasted_url: str | None, link_manager: LinkManager
) -> tuple[str, bool]:
    """Returns (content, ephemeral). pasted_url is None/blank when the bot
    and the logging-in browser share a machine -- see LinkManager.finish_link."""
    content, _success = await link_manager.finish_link(user_id, pasted_url)
    return content, True


async def do_delete_slot(
    slot_name: str, store: SlotStore, librespot: LibrespotManager
) -> tuple[str, bool]:
    """Returns (content, ephemeral). Admin-only -- see main.py's
    default_permissions on the /delete-slot command and the belt-and-suspenders
    permission check in interaction_relay.py's production path."""
    slot_meta = store.get_by_name(slot_name)
    if slot_meta is None:
        return f"No slot named '{slot_name}'.", True

    spotify_slot = librespot.slot_by_index(slot_meta.index)
    assert spotify_slot is not None
    await librespot.stop_one(spotify_slot.name)

    credentials_path = os.path.join(spotify_slot.cache_dir, "credentials.json")
    if os.path.exists(credentials_path):
        os.remove(credentials_path)
    if os.path.exists(spotify_slot.pipe_path):
        os.remove(spotify_slot.pipe_path)

    await store.reset(slot_meta.index)
    return f"Slot '{slot_name}' deleted and freed up.", False


async def do_link_web_api(
    user_id: str, slot_name: str, store: SlotStore, web_api_link_manager: WebApiLinkManager
) -> tuple[str, bool]:
    """Returns (content, ephemeral). Requires the slot to already be claimed
    via /link -- Web API scopes are meaningless without a linked account."""
    slot_meta = store.get_by_name(slot_name)
    if slot_meta is None:
        return f"No slot named '{slot_name}'.", True
    if slot_meta.state != STATE_CLAIMED:
        return f"Slot '{slot_name}' isn't claimed yet -- run /link first.", True
    content, _success = web_api_link_manager.start_link(user_id, slot_meta.index)
    return content, True


async def do_link_web_api_finish(
    user_id: str, pasted_url: str, web_api_link_manager: WebApiLinkManager
) -> tuple[str, bool]:
    """Returns (content, ephemeral)."""
    content, _success = await web_api_link_manager.finish_link(user_id, pasted_url)
    return content, True
