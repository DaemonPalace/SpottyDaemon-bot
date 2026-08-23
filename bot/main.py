import logging

import discord
from discord import app_commands

import ec2_control
from commands import do_connect, do_delete_slot, do_disconnect, do_link, do_link_finish
from config import DISCORD_TOKEN, SLOTS
from idle_monitor import IdleMonitor
from interaction_relay import InteractionRelay
from librespot_manager import LibrespotManager
from slot_store import SlotStore
from spotify_link import LinkManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

intents = discord.Intents.default()
intents.voice_states = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

slot_store = SlotStore()
librespot = LibrespotManager(SLOTS)
link_manager = LinkManager(slot_store, librespot)
idle_monitor = IdleMonitor(client, slot_store)
interaction_relay = InteractionRelay(client, librespot, slot_store, link_manager)


class PasswordModal(discord.ui.Modal):
    """Collects a password via a Discord popup instead of a plain slash
    command argument -- string command args show up in the channel's visible
    "/connect slot: x password: ****" usage line, which would leak it to
    anyone watching. Modal submissions aren't posted as visible channel text."""

    password = discord.ui.TextInput(label="Password", style=discord.TextStyle.short, required=True, max_length=100)

    def __init__(self, title: str, on_submit_callback):
        super().__init__(title=title)
        self._on_submit_callback = on_submit_callback

    async def on_submit(self, interaction: discord.Interaction):
        await self._on_submit_callback(interaction, str(self.password.value))


# These only fire over the gateway, which only happens when the Discord app
# has no Interactions Endpoint URL configured (i.e. local testing without
# the Lambda wired up). In production, Discord routes everything except
# /wake and /sleep to interaction_relay.py instead -- see its module
# docstring.
@tree.command(name="connect", description="Join your voice channel and stream a Spotify Connect slot")
@app_commands.describe(slot="Which slot to stream (see whoever set it up with /link)")
async def connect(interaction: discord.Interaction, slot: str):
    assert isinstance(interaction.user, discord.Member) and interaction.guild is not None
    guild = interaction.guild
    member = interaction.user

    async def handle_submit(modal_interaction: discord.Interaction, password: str):
        await modal_interaction.response.defer(ephemeral=True)
        content, ephemeral = await do_connect(guild, member, slot, password, librespot, slot_store)
        await modal_interaction.followup.send(content, ephemeral=ephemeral)

    await interaction.response.send_modal(PasswordModal(f"Password for '{slot}'", handle_submit))


@tree.command(name="disconnect", description="Leave the voice channel")
async def disconnect(interaction: discord.Interaction):
    assert interaction.guild is not None
    content, ephemeral = await do_disconnect(interaction.guild)
    await interaction.response.send_message(content, ephemeral=ephemeral)


@tree.command(name="link", description="Claim a free Spotify slot and link your own Spotify account")
@app_commands.describe(slotname="Name for this slot, e.g. your username (lowercase, no spaces)")
async def link(interaction: discord.Interaction, slotname: str):
    user_id = str(interaction.user.id)

    async def handle_submit(modal_interaction: discord.Interaction, password: str):
        await modal_interaction.response.defer(ephemeral=True)
        content, ephemeral = await do_link(user_id, slotname, password, link_manager)
        await modal_interaction.followup.send(content, ephemeral=ephemeral)

    await interaction.response.send_modal(PasswordModal(f"Set a password for '{slotname}'", handle_submit))


@tree.command(name="link-finish", description="Finish /link by pasting the url your browser failed to load")
@app_commands.describe(url="The http://127.0.0.1:.../login?code=... url from your browser's address bar")
async def link_finish(interaction: discord.Interaction, url: str):
    await interaction.response.defer(ephemeral=True)
    content, ephemeral = await do_link_finish(str(interaction.user.id), url, link_manager)
    await interaction.followup.send(content, ephemeral=ephemeral)


@tree.command(name="delete-slot", description="Free up a claimed Spotify slot (admin only)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(name="Slot name to delete")
async def delete_slot(interaction: discord.Interaction, name: str):
    content, ephemeral = await do_delete_slot(name, slot_store, librespot)
    await interaction.response.send_message(content, ephemeral=ephemeral)


# Registered here too (not just via infra/register-discord-commands.sh) so a
# single tree.sync() defines all commands together. Discord's bulk
# command-overwrite endpoint replaces the *entire* command set on every call
# -- registering wake/sleep separately silently deleted connect/disconnect,
# and the next tree.sync() would have deleted wake/sleep right back. If this
# handler ever actually fires (only possible via the gateway, i.e. no Lambda
# Interactions Endpoint URL configured -- see interaction_relay.py's
# docstring), the bot process is already up, so both are trivial.
@tree.command(name="wake", description="Start the music bot instance (takes ~30s)")
async def wake(interaction: discord.Interaction):
    await interaction.response.send_message("Already running.", ephemeral=True)


@tree.command(name="sleep", description="Stop the music bot instance")
async def sleep(interaction: discord.Interaction):
    await interaction.response.send_message("Stopping the music bot instance.")
    ec2_control.stop_this_instance()


@client.event
async def on_ready():
    log.info("logged in as %s", client.user)
    await librespot.start_claimed(slot_store.claimed_indexes())
    await tree.sync()
    idle_monitor.start()
    interaction_relay.start()
    link_manager.start()


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
