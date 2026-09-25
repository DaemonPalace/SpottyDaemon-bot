import logging

import discord
from discord import app_commands

from api import DiagnosticsApi
from commands import (
    do_connect,
    do_delete_slot,
    do_disconnect,
    do_link,
    do_link_finish,
    do_link_web_api,
    do_link_web_api_finish,
    do_play_track,
    do_reconnect,
    do_search_tracks,
    forget_session,
    resolve_jam_slot,
)
from config import DEV_GUILD_ID, DISCORD_TOKEN, HOST_CONTROLLER, INTERACTIONS_QUEUE_URL, SLOTS, SPOTIFY_DIRECT_CALLBACK
from host_control import build_host_controller
from idle_monitor import IdleMonitor
from interaction_relay import InteractionRelay
from jam import JamManager
from librespot_manager import LibrespotManager
from slot_store import SlotStore
from spotify_link import LinkManager
from spotify_web_api import WebApiLinkManager
from up_next import UpNextManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

intents = discord.Intents.default()
intents.voice_states = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

slot_store = SlotStore()
librespot = LibrespotManager(SLOTS)
link_manager = LinkManager(slot_store, librespot)
web_api_link_manager = WebApiLinkManager(slot_store)
host_controller = build_host_controller(HOST_CONTROLLER)
idle_monitor = IdleMonitor(client, slot_store, host_controller)
jam_manager = JamManager(slot_store, web_api_link_manager, librespot)
up_next = UpNextManager(slot_store, web_api_link_manager)
diagnostics_api = DiagnosticsApi(
    client, librespot, slot_store, link_manager, web_api_link_manager, jam_manager, up_next
)
# None when no Lambda/SQS relay is configured -- the gateway CommandTree
# below is then the one and only command path (self-host/standalone default).
interaction_relay = (
    InteractionRelay(client, librespot, slot_store, link_manager, web_api_link_manager, jam_manager, up_next)
    if INTERACTIONS_QUEUE_URL
    else None
)


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


class PasteUrlModal(discord.ui.Modal):
    """Popup for pasting the failed-redirect url back -- the clean
    alternative to typing `/link-finish url:...` by hand. Field is optional:
    leaving it blank means the page loaded fine (bot and browser on the same
    machine)."""

    pasted_url = discord.ui.TextInput(
        label="Redirect URL (blank if the page loaded)",
        style=discord.TextStyle.short,
        required=False,
        max_length=500,
    )

    def __init__(self, title: str, on_submit_callback):
        super().__init__(title=title)
        self._on_submit_callback = on_submit_callback

    async def on_submit(self, interaction: discord.Interaction):
        value = str(self.pasted_url.value).strip() or None
        await self._on_submit_callback(interaction, value)


class LinkStartView(discord.ui.View):
    """Posted alongside a /link or /link-web-api reply: a Link-style button
    opens Spotify's login directly in a new tab (no copy/paste for step
    one), and a second button pops a paste-back dialog for step two.

    The paste-back button needs a live Interaction to answer immediately
    with a MODAL -- only possible when this process itself receives
    interactions (no Lambda/SQS relay in the way, see
    interaction_relay.py's module docstring). Under the relay there's no
    way to answer a fresh button click with a modal from here, so it's
    swapped for a disabled hint instead; the plain fallback_command still
    works either way (interaction_relay.py handles it directly).

    In direct mode (SPOTIFY_DIRECT_CALLBACK) there's nothing to paste --
    Spotify redirects to the bot's own callback -- so just the login button."""

    def __init__(self, authorize_url: str, modal_title: str, on_finish_callback, fallback_command: str):
        super().__init__(timeout=900)
        self.add_item(
            discord.ui.Button(label="Log in with Spotify", style=discord.ButtonStyle.link, url=authorize_url)
        )
        if SPOTIFY_DIRECT_CALLBACK:
            return
        if INTERACTIONS_QUEUE_URL:
            self.add_item(
                discord.ui.Button(
                    label=f"Use {fallback_command} to finish", style=discord.ButtonStyle.secondary, disabled=True
                )
            )
        else:
            self._modal_title = modal_title
            self._on_finish_callback = on_finish_callback
            paste_button = discord.ui.Button(label="Paste redirect URL", style=discord.ButtonStyle.primary)
            paste_button.callback = self._on_paste_click
            self.add_item(paste_button)

    async def _on_paste_click(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(PasteUrlModal(self._modal_title, self._on_finish_callback))


# These fire over the gateway, the default and only command path for a
# self-hosted/standalone install (no Lambda/SQS relay configured). The
# legacy AWS deployment instead sets a Discord Interactions Endpoint URL,
# which routes everything except /wake and /sleep to interaction_relay.py
# instead -- see its module docstring.
@tree.command(name="connect", description="Join your voice channel and stream a Spotify Connect slot")
@app_commands.describe(slot="Which slot to stream (see whoever set it up with /link)")
async def connect(interaction: discord.Interaction, slot: str):
    assert isinstance(interaction.user, discord.Member) and interaction.guild is not None
    guild = interaction.guild
    member = interaction.user

    async def handle_submit(modal_interaction: discord.Interaction, password: str):
        await modal_interaction.response.defer(ephemeral=True)
        content, ephemeral = await do_connect(
            guild, member, slot, password, librespot, slot_store, web_api_link_manager, interaction.channel_id
        )
        await modal_interaction.followup.send(content, ephemeral=ephemeral)
        if not ephemeral:
            # Auto-post the Jam panel in the channel /connect was run from,
            # same as running /jam by hand.
            note = await jam_manager.auto_start(interaction.channel, guild.id)
            if note is not None:
                await modal_interaction.followup.send(note, ephemeral=True)

    await interaction.response.send_modal(PasswordModal(f"Password for '{slot}'", handle_submit))


@tree.command(name="reconnect", description="Force a full disconnect+reconnect for a slot (fixes stuck playback)")
@app_commands.describe(slot="Which slot to reconnect")
async def reconnect(interaction: discord.Interaction, slot: str):
    assert isinstance(interaction.user, discord.Member) and interaction.guild is not None
    guild = interaction.guild
    member = interaction.user

    async def handle_submit(modal_interaction: discord.Interaction, password: str):
        await modal_interaction.response.defer(ephemeral=True)
        content, ephemeral = await do_reconnect(
            guild, member, slot, password, librespot, slot_store, web_api_link_manager, interaction.channel_id
        )
        await modal_interaction.followup.send(content, ephemeral=ephemeral)

    await interaction.response.send_modal(PasswordModal(f"Password for '{slot}'", handle_submit))


@tree.command(name="disconnect", description="Leave the voice channel")
async def disconnect(interaction: discord.Interaction):
    assert interaction.guild is not None
    content, ephemeral = await do_disconnect(interaction.guild)
    await jam_manager.stop_jam(interaction.guild.id)
    await interaction.response.send_message(content, ephemeral=ephemeral)


@tree.command(name="jam", description="Post a live now-playing/queue control panel in this channel")
async def jam(interaction: discord.Interaction):
    assert interaction.guild is not None
    slot_index, error = resolve_jam_slot(interaction.guild.id, slot_store)
    if error is not None:
        content, ephemeral = error
        await interaction.response.send_message(content, ephemeral=ephemeral)
        return
    await jam_manager.start_jam_in_channel(interaction.channel, slot_index)
    await interaction.response.send_message("Jam panel posted above.", ephemeral=True)


class TrackResultsView(discord.ui.View):
    """One message, buttons only -- no separate text lines, since the Play
    button's own label IS the track name. One row per track (up to 5,
    Discord's row cap) so each track's Play/Queue pair stays visually
    grouped without needing text alignment. Clicking either button
    collapses the whole message to a one-line confirmation with no
    buttons, rather than leaving a picker with dead/used buttons sitting
    around. Explicit "play-track:<mode>:<uri>" custom_ids so a relayed
    click can be dispatched the same way (interaction_relay.py's
    _run_component)."""

    def __init__(self, guild_id: int, results: list[dict]):
        super().__init__(timeout=120)
        for i, track in enumerate(results[:5]):
            uri = track["uri"]
            artists = ", ".join(a["name"] for a in track.get("artists", []))
            label = f"{track['name']} — {artists}"[:80]
            play_button = discord.ui.Button(
                label=label, style=discord.ButtonStyle.success, custom_id=f"play-track:play_now:{uri}", row=i
            )
            play_button.callback = self._make_callback(guild_id, uri, "play_now")
            queue_button = discord.ui.Button(
                label="➕ Queue", style=discord.ButtonStyle.secondary, custom_id=f"play-track:queue:{uri}", row=i
            )
            queue_button.callback = self._make_callback(guild_id, uri, "queue")
            self.add_item(play_button)
            self.add_item(queue_button)

    def _make_callback(self, guild_id: int, track_uri: str, mode: str):
        async def callback(interaction: discord.Interaction) -> None:
            content, ephemeral = await do_play_track(
                guild_id, track_uri, mode, slot_store, web_api_link_manager, librespot, up_next
            )
            await interaction.response.edit_message(content=content, view=None)
            await jam_manager.repost(guild_id)

        return callback


@tree.command(name="play", description="Search Spotify for a track to play or queue")
@app_commands.describe(query="Song name to search for")
async def play(interaction: discord.Interaction, query: str):
    assert interaction.guild is not None
    await interaction.response.defer(ephemeral=True)
    results, error = await do_search_tracks(interaction.guild.id, query, slot_store, web_api_link_manager)
    if error is not None:
        content, ephemeral = error
        await interaction.followup.send(content, ephemeral=ephemeral)
        return
    if not results:
        await interaction.followup.send(f"No tracks found for '{query}'.", ephemeral=True)
        return
    await interaction.followup.send(view=TrackResultsView(interaction.guild.id, results), ephemeral=True)


@tree.command(name="link", description="Claim a free Spotify slot and link your own Spotify account")
@app_commands.describe(slotname="Name for this slot, e.g. your username (lowercase, no spaces)")
async def link(interaction: discord.Interaction, slotname: str):
    user_id = str(interaction.user.id)

    async def handle_finish(modal_interaction: discord.Interaction, pasted_url: str | None):
        await modal_interaction.response.defer(ephemeral=True)
        content, ephemeral = await do_link_finish(user_id, pasted_url, link_manager)
        await modal_interaction.followup.send(content, ephemeral=ephemeral)

    async def handle_submit(modal_interaction: discord.Interaction, password: str):
        await modal_interaction.response.defer(ephemeral=True)
        content, ephemeral = await do_link(user_id, slotname, password, link_manager)
        authorize_url = link_manager.authorize_url(user_id)
        if authorize_url:
            view = LinkStartView(authorize_url, f"Finish linking '{slotname}'", handle_finish, "/link-finish")
            await modal_interaction.followup.send(content, view=view, ephemeral=ephemeral)
        else:
            await modal_interaction.followup.send(content, ephemeral=ephemeral)

    await interaction.response.send_modal(PasswordModal(f"Set a password for '{slotname}'", handle_submit))


@tree.command(name="link-finish", description="Finish /link (paste the url your browser failed to load, or leave blank if it loaded fine)")
@app_commands.describe(url="The http://127.0.0.1:.../login?code=... url, only if your browser failed to load it")
async def link_finish(interaction: discord.Interaction, url: str | None = None):
    await interaction.response.defer(ephemeral=True)
    content, ephemeral = await do_link_finish(str(interaction.user.id), url, link_manager)
    await interaction.followup.send(content, ephemeral=ephemeral)


@tree.command(name="delete-slot", description="Free up a claimed Spotify slot (admin only)")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(name="Slot name to delete")
async def delete_slot(interaction: discord.Interaction, name: str):
    content, ephemeral = await do_delete_slot(name, slot_store, librespot)
    await interaction.response.send_message(content, ephemeral=ephemeral)


@tree.command(name="link-web-api", description="Grant this bot Spotify Web API access for an already-linked slot")
@app_commands.describe(slotname="Slot name (must already be linked via /link)")
async def link_web_api(interaction: discord.Interaction, slotname: str):
    user_id = str(interaction.user.id)
    content, ephemeral = await do_link_web_api(user_id, slotname, slot_store, web_api_link_manager)
    authorize_url = web_api_link_manager.authorize_url(user_id)
    if authorize_url:

        async def handle_finish(modal_interaction: discord.Interaction, pasted_url: str | None):
            await modal_interaction.response.defer(ephemeral=True)
            content2, ephemeral2 = await do_link_web_api_finish(user_id, pasted_url or "", web_api_link_manager)
            await modal_interaction.followup.send(content2, ephemeral=ephemeral2)

        view = LinkStartView(
            authorize_url, f"Finish Web API link for '{slotname}'", handle_finish, "/link-web-api-finish"
        )
        await interaction.response.send_message(content, view=view, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(content, ephemeral=ephemeral)


@tree.command(name="link-web-api-finish", description="Finish /link-web-api by pasting the url your browser failed to load")
@app_commands.describe(url="The http://127.0.0.1:.../callback?code=... url from your browser's address bar")
async def link_web_api_finish(interaction: discord.Interaction, url: str):
    await interaction.response.defer(ephemeral=True)
    content, ephemeral = await do_link_web_api_finish(str(interaction.user.id), url, web_api_link_manager)
    await interaction.followup.send(content, ephemeral=ephemeral)


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
    if host_controller.describe() == "noop":
        await interaction.response.send_message(
            "Already running -- this install doesn't auto-sleep/wake (no Lambda/EC2 configured, "
            "HOST_CONTROLLER=noop). See README's \"Legacy: AWS deployment\" section for that setup.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message("Already running.", ephemeral=True)


@tree.command(name="sleep", description="Stop the music bot instance")
async def sleep(interaction: discord.Interaction):
    if host_controller.describe() == "noop":
        await interaction.response.send_message(
            "Can't self-stop -- this install has no Lambda/EC2 configured (HOST_CONTROLLER=noop). "
            "Stop it yourself (e.g. `sudo systemctl stop discord-music-bot`), or set up the legacy "
            "AWS auto-sleep/wake deployment -- see README's \"Legacy: AWS deployment\" section.",
            ephemeral=True,
        )
        return
    await interaction.response.send_message("Stopping the music bot instance.")
    await host_controller.stop_host()


@client.event
async def on_ready():
    log.info("logged in as %s", client.user)
    await librespot.start_claimed(slot_store.claimed_indexes())
    if DEV_GUILD_ID:
        guild = discord.Object(id=int(DEV_GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        log.info("synced commands to dev guild %s (instant, not global)", DEV_GUILD_ID)
    else:
        await tree.sync()
    idle_monitor.start()
    if interaction_relay is not None:
        interaction_relay.start()
    link_manager.start()
    up_next.start()
    diagnostics_api.start()


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
        forget_session(guild.id)
        await jam_manager.stop_jam(guild.id)
        await voice_client.disconnect(force=True)


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
