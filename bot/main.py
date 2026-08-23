import logging

import discord
from discord import app_commands

from config import DISCORD_TOKEN, SLOTS
from idle_monitor import IdleMonitor
from librespot_manager import LibrespotManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

intents = discord.Intents.default()
intents.voice_states = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

librespot = LibrespotManager(SLOTS)
idle_monitor = IdleMonitor(client)

SLOT_NAMES = [slot.name for slot in SLOTS]


@tree.command(name="connect", description="Join your voice channel and stream a Spotify Connect slot")
@app_commands.describe(slot="Which Spotify account slot to stream (1 or 2)")
@app_commands.choices(slot=[app_commands.Choice(name=name, value=name) for name in SLOT_NAMES])
async def connect(interaction: discord.Interaction, slot: app_commands.Choice[str]):
    member = interaction.user
    if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel is None:
        await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
        return

    channel = member.voice.channel
    guild = interaction.guild
    assert guild is not None

    voice_client = guild.voice_client
    if voice_client is None:
        voice_client = await channel.connect()
    elif voice_client.channel.id != channel.id:
        await voice_client.move_to(channel)

    if voice_client.is_playing():
        voice_client.stop()

    proc = librespot.get(slot.value)
    pipe_path = proc.slot.pipe_path
    source = discord.FFmpegPCMAudio(
        source=pipe_path,
        before_options="-f s16le -ar 44100 -ac 2",
        options="-vn",
    )
    voice_client.play(source)

    await interaction.response.send_message(
        f"Connected. Control playback from Spotify Connect on device **{slot.value}**."
    )


@tree.command(name="disconnect", description="Leave the voice channel")
async def disconnect(interaction: discord.Interaction):
    guild = interaction.guild
    assert guild is not None
    if guild.voice_client is not None:
        await guild.voice_client.disconnect(force=True)
        await interaction.response.send_message("Disconnected.")
    else:
        await interaction.response.send_message("Not connected.", ephemeral=True)


@client.event
async def on_ready():
    log.info("logged in as %s", client.user)
    await librespot.start_all()
    await tree.sync()
    idle_monitor.start()


@client.event
async def on_voice_state_update(member, before, after):
    # If everyone leaves the channel the bot is in, drop the connection so
    # the idle monitor's "no active voice connection" check fires promptly.
    guild = member.guild
    voice_client = guild.voice_client
    if voice_client is None:
        return
    channel = voice_client.channel
    if len([m for m in channel.members if not m.bot]) == 0:
        await voice_client.disconnect(force=True)


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
