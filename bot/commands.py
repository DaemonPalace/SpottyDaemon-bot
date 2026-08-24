"""Command logic shared between the gateway CommandTree (main.py, only fires
when no Interactions Endpoint URL is configured -- useful for local testing)
and the SQS-relayed path (interaction_relay.py, what actually runs in
production once the Lambda endpoint is set)."""

import asyncio
import logging
import os

import discord

from config import SpotifySlot
from librespot_manager import LibrespotManager, LibrespotProcess
from slot_store import SlotStore
from spotify_link import LinkManager

log = logging.getLogger("commands")

# Which slot each guild is *supposed* to be connected to right now, keyed by
# guild id. Used to tell "ffmpeg exited because the user /disconnect'd" apart
# from "ffmpeg exited on its own" (e.g. librespot appears to close its pipe
# write-end while Spotify is paused, which looks like a clean EOF to ffmpeg)
# -- only the latter should trigger an automatic reattach. Cleared by
# do_disconnect and overwritten by a fresh do_connect, both of which make any
# in-flight reattach for the old session a no-op once it checks this map.
_active_sessions: dict[int, str] = {}

_REATTACH_DELAY_SECONDS = 2


async def do_connect(
    guild: discord.Guild,
    member: discord.Member,
    slot_name: str,
    password: str,
    librespot: LibrespotManager,
    store: SlotStore,
) -> tuple[str, bool]:
    """Returns (content, ephemeral)."""
    if member.voice is None or member.voice.channel is None:
        return "Join a voice channel first.", True

    slot_meta = store.get_by_name(slot_name)
    if slot_meta is None:
        return f"No slot named '{slot_name}'. Ask your friend to /link one first.", True
    if not store.verify_password(slot_name, password):
        return "Wrong password for that slot.", True

    spotify_slot = librespot.slot_by_index(slot_meta.index)
    assert spotify_slot is not None
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

        _active_sessions[guild.id] = slot_name
        _attach_source(guild, voice_client, slot_name, spotify_slot, proc)
    except Exception:
        proc.resume_draining()
        log.exception("connect command failed")
        return "Something went wrong connecting/starting playback — check the bot logs.", True

    return f"Connected. Control playback from Spotify Connect on slot **{slot_name}**.", False


def _attach_source(
    guild: discord.Guild,
    voice_client: discord.VoiceClient,
    slot_name: str,
    spotify_slot: SpotifySlot,
    proc: LibrespotProcess,
) -> None:
    """Starts (or restarts) playback of a slot's pipe into an already-connected
    voice_client, wiring up the after-callback that both resumes pipe
    draining and, if this guild is still supposed to be connected to this
    slot, self-heals by reattaching a fresh source. Shared by do_connect and
    _reattach so retries behave identically to the initial connect."""
    loop = asyncio.get_running_loop()

    # Stop competing with ffmpeg for bytes on this slot's pipe -- see
    # librespot_manager.py's module docstring for why this exists.
    proc.pause_draining()

    def _after_playback(error: Exception | None) -> None:
        if error:
            log.error("playback error for slot %s: %s", spotify_slot.name, error)
        # Called from the audio player thread, not the event loop.
        loop.call_soon_threadsafe(proc.resume_draining)
        if _active_sessions.get(guild.id) == slot_name:
            asyncio.run_coroutine_threadsafe(
                _reattach(guild, slot_name, spotify_slot, proc), loop
            )

    source = discord.FFmpegPCMAudio(
        source=proc.slot.pipe_path,
        before_options="-f s16le -ar 44100 -ac 2",
        options="-vn",
    )
    voice_client.play(source, after=_after_playback)


async def _reattach(
    guild: discord.Guild, slot_name: str, spotify_slot: SpotifySlot, proc: LibrespotProcess
) -> None:
    """ffmpeg can exit on its own well before the user runs /disconnect --
    observed in production when Spotify is paused long enough that librespot
    appears to close its pipe write-end, which looks like a clean EOF to
    ffmpeg. There's no reason to make the user run /connect again just
    because Spotify was paused for a bit, so if this guild is still supposed
    to be connected to this slot, transparently reattach a fresh source."""
    await asyncio.sleep(_REATTACH_DELAY_SECONDS)
    if _active_sessions.get(guild.id) != slot_name:
        return  # someone /connect'd or /disconnect'd elsewhere meanwhile
    voice_client = guild.voice_client
    if voice_client is None or not voice_client.is_connected() or voice_client.is_playing():
        return
    log.info("reattaching to slot %s for guild %s after unexpected stop", slot_name, guild.id)
    try:
        _attach_source(guild, voice_client, slot_name, spotify_slot, proc)
    except Exception:
        proc.resume_draining()
        log.exception("failed to reattach slot %s for guild %s", slot_name, guild.id)


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


async def do_link(
    user_id: str, slot_name: str, password: str, link_manager: LinkManager
) -> tuple[str, bool]:
    """Returns (content, ephemeral). Always ephemeral -- this is always sent
    to whoever ran the command, never posted to the channel."""
    content, _success = await link_manager.start_link(user_id, slot_name, password)
    return content, True


async def do_link_finish(
    user_id: str, pasted_url: str, link_manager: LinkManager
) -> tuple[str, bool]:
    """Returns (content, ephemeral)."""
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
