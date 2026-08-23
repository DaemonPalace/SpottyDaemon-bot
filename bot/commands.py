"""Command logic shared between the gateway CommandTree (main.py, only fires
when no Interactions Endpoint URL is configured -- useful for local testing)
and the SQS-relayed path (interaction_relay.py, what actually runs in
production once the Lambda endpoint is set)."""

import logging

import discord

from librespot_manager import LibrespotManager

log = logging.getLogger("commands")


async def do_connect(
    guild: discord.Guild, member: discord.Member, slot_name: str, librespot: LibrespotManager
) -> tuple[str, bool]:
    """Returns (content, ephemeral)."""
    if member.voice is None or member.voice.channel is None:
        return "Join a voice channel first.", True

    channel = member.voice.channel
    try:
        voice_client = guild.voice_client
        if voice_client is None:
            voice_client = await channel.connect()
        elif voice_client.channel.id != channel.id:
            await voice_client.move_to(channel)

        if voice_client.is_playing():
            voice_client.stop()

        proc = librespot.get(slot_name)
        source = discord.FFmpegPCMAudio(
            source=proc.slot.pipe_path,
            before_options="-f s16le -ar 44100 -ac 2",
            options="-vn",
        )
        voice_client.play(source)
    except Exception:
        log.exception("connect command failed")
        return "Something went wrong connecting/starting playback — check the bot logs.", True

    return f"Connected. Control playback from Spotify Connect on device **{slot_name}**.", False


async def do_disconnect(guild: discord.Guild) -> tuple[str, bool]:
    """Returns (content, ephemeral)."""
    if guild.voice_client is not None:
        await guild.voice_client.disconnect(force=True)
        return "Disconnected.", False
    return "Not connected.", True
