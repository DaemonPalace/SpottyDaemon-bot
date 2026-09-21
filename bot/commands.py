"""Command logic shared between the gateway CommandTree (main.py -- the
default and only path for a self-hosted/standalone install) and the
SQS-relayed path (interaction_relay.py, used only by the legacy AWS
deployment when a Discord Interactions Endpoint URL is configured)."""

import asyncio
import itertools
import logging
import os
import time
from dataclasses import dataclass, field

import discord

from config import SpotifySlot
from librespot_manager import LibrespotManager
from slot_store import STATE_CLAIMED, SlotStore
from spotify_link import LinkManager
from spotify_web_api import WebApiLinkManager

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

    return f"Connected. Control playback from Spotify Connect on slot **{slot_name}**.", False


async def do_reconnect(
    guild: discord.Guild,
    member: discord.Member,
    slot_name: str,
    password: str,
    librespot: LibrespotManager,
    store: SlotStore,
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

    return f"Reconnected. Control playback from Spotify Connect on slot **{slot_name}**.", False


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

    source = discord.FFmpegPCMAudio(
        source=spotify_slot.pipe_path,
        before_options="-f s16le -ar 44100 -ac 2",
        options="-vn",
    )
    voice_client.play(source, after=_after_playback)


async def _reattach(
    guild: discord.Guild,
    slot_name: str,
    generation: int,
    spotify_slot: SpotifySlot,
    librespot: LibrespotManager,
) -> None:
    """ffmpeg can exit on its own well before the user runs /disconnect --
    observed in production when Spotify is paused long enough that librespot
    appears to close its pipe write-end, which looks like a clean EOF to
    ffmpeg. There's no reason to make the user run /connect again just
    because Spotify was paused for a bit, so if this guild is still supposed
    to be connected to this slot, transparently reattach a fresh source.

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
    to whoever ran the command, never posted to the channel."""
    content, _success = await link_manager.start_link(user_id, slot_name, password)
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
    return f"Slot '{slot_name}' deleted and freed up for /link.", False


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
