import logging

import discord
from discord import app_commands

from commands import do_connect, do_disconnect
from config import DISCORD_TOKEN, SLOTS
from idle_monitor import IdleMonitor
from interaction_relay import InteractionRelay
from librespot_manager import LibrespotManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

intents = discord.Intents.default()
intents.voice_states = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

librespot = LibrespotManager(SLOTS)
idle_monitor = IdleMonitor(client)
interaction_relay = InteractionRelay(client, librespot)

SLOT_NAMES = [slot.name for slot in SLOTS]


# These only fire over the gateway, which only happens when the Discord app
# has no Interactions Endpoint URL configured (i.e. local testing without
# the Lambda wired up). In production, Discord routes /connect and
# /disconnect to interaction_relay.py instead -- see its module docstring.
@tree.command(name="connect", description="Join your voice channel and stream a Spotify Connect slot")
@app_commands.describe(slot="Which Spotify account slot to stream (1 or 2)")
@app_commands.choices(slot=[app_commands.Choice(name=name, value=name) for name in SLOT_NAMES])
async def connect(interaction: discord.Interaction, slot: app_commands.Choice[str]):
    assert isinstance(interaction.user, discord.Member) and interaction.guild is not None
    await interaction.response.defer()
    content, ephemeral = await do_connect(interaction.guild, interaction.user, slot.value, librespot)
    await interaction.followup.send(content, ephemeral=ephemeral)


@tree.command(name="disconnect", description="Leave the voice channel")
async def disconnect(interaction: discord.Interaction):
    assert interaction.guild is not None
    content, ephemeral = await do_disconnect(interaction.guild)
    await interaction.response.send_message(content, ephemeral=ephemeral)


@client.event
async def on_ready():
    log.info("logged in as %s", client.user)
    await librespot.start_all()
    await tree.sync()
    idle_monitor.start()
    interaction_relay.start()


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
