"""Discord-native Jam: a live-updating control panel embed (now playing +
next 5 queued tracks) with Rewind/Play/Pause/Skip buttons, posted to
whichever channel /jam was run in. Reposted (deleted + resent, see
repost()) after a play-track action so it stays near the bottom of the
channel instead of getting buried under other activity.

If PUBLIC_DASHBOARD_URL is set, the panel also carries an "Open dashboard"
link to <url>/jam/<token>: a no-password dashboard for the jammed slot. The
token lives only as long as the panel does (stop_jam, a new /jam for a
different slot, or a bot restart all kill it) -- see bot/api.py's
_jam_slot for the lookup.

One session per guild, mirroring commands.py's _active_sessions model.
Editing the message every REFRESH_INTERVAL_SECONDS is well under Discord's
per-channel edit rate limit even with several guilds running Jam at once.

Posting/editing the panel only ever uses plain bot-token REST calls
(channel.send / message.edit) rather than an Interaction response, so the
same code works whether /jam was invoked over the gateway or relayed
through Lambda+SQS (see interaction_relay.py) -- start_jam_in_channel takes
a channel, not an Interaction. The one place gateway vs. relay differ is
how a button click's own response gets sent back (handle_button uses a
live Interaction; interaction_relay.py's _run_component calls apply_action
+ build_embed directly and edits the original message itself)."""

import asyncio
import logging
import secrets

import discord

import spotify_player_api
from commands import flush_slot_pipe
from config import PUBLIC_DASHBOARD_URL
from librespot_manager import LibrespotManager
from slot_store import SlotStore
from spotify_web_api import WebApiLinkManager

log = logging.getLogger("jam")

REFRESH_INTERVAL_SECONDS = 5
QUEUE_PREVIEW_SIZE = 5


class JamSession:
    def __init__(self, message: discord.Message, slot_index: int, task: asyncio.Task, token: str):
        self.message = message
        self.slot_index = slot_index
        self.task = task
        self.token = token


class JamManager:
    def __init__(self, slot_store: SlotStore, web_api_link_manager: WebApiLinkManager, librespot: LibrespotManager):
        self.slot_store = slot_store
        self.web_api_link_manager = web_api_link_manager
        self.librespot = librespot
        self._sessions: dict[int, JamSession] = {}

    def session_slot_index(self, guild_id: int) -> int | None:
        """For callers (interaction_relay.py's component dispatch) that
        need to know which slot a guild's Jam panel controls without a
        live Interaction to look it up through."""
        session = self._sessions.get(guild_id)
        return session.slot_index if session is not None else None

    def slot_index_for_token(self, token: str) -> int | None:
        for session in self._sessions.values():
            if secrets.compare_digest(session.token, token):
                return session.slot_index
        return None

    async def build_embed(self, slot_index: int) -> discord.Embed:
        token = await self.web_api_link_manager.get_access_token(slot_index)
        if token is None:
            return discord.Embed(description="Spotify Web API isn't linked for this slot.")

        now_playing, queue = await asyncio.gather(
            spotify_player_api.get_now_playing(token),
            spotify_player_api.get_queue(token),
        )
        embed = discord.Embed(title="Now Playing")
        item = now_playing.get("item") if now_playing else None
        if item:
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            embed.description = f"**{item['name']}**\n{artists}"
            images = item.get("album", {}).get("images") or []
            if images:
                embed.set_thumbnail(url=images[-1]["url"])
        else:
            embed.description = "Nothing playing right now."

        upcoming = (queue or {}).get("queue", [])[:QUEUE_PREVIEW_SIZE]
        if upcoming:
            lines = []
            for i, track in enumerate(upcoming, 1):
                artists = ", ".join(a["name"] for a in track.get("artists", []))
                lines.append(f"{i}. {track['name']} — {artists}")
            embed.add_field(name="Up next", value="\n".join(lines), inline=False)
        return embed

    async def start_jam_in_channel(self, channel: discord.abc.Messageable, slot_index: int) -> None:
        guild = getattr(channel, "guild", None)
        assert guild is not None
        # Keep the old link working if this just re-posts the same slot's
        # panel (e.g. /connect's auto-post followed by a manual /jam).
        old = self._sessions.get(guild.id)
        token = old.token if old is not None and old.slot_index == slot_index else secrets.token_urlsafe(16)
        await self.stop_jam(guild.id)  # replace any existing panel for this guild, don't stack them

        embed = await self.build_embed(slot_index)
        message = await channel.send(embed=embed, view=JamView(self, token))
        task = asyncio.create_task(self._refresh_loop(guild.id))
        self._sessions[guild.id] = JamSession(message, slot_index, task, token)

    async def repost(self, guild_id: int) -> None:
        """Deletes the current panel message and resends it as a fresh one
        in the same channel, so it doesn't get buried by other channel
        activity (e.g. the "used /play" line every /play invocation adds,
        even though its own reply is ephemeral) -- called after a
        play-track action completes. Keeps the same refresh task/session,
        just repoints it at the new message."""
        session = self._sessions.get(guild_id)
        if session is None:
            return
        channel = session.message.channel
        embed = await self.build_embed(session.slot_index)
        try:
            await session.message.delete()
        except discord.HTTPException:
            pass  # already gone -- fine, we're replacing it anyway
        try:
            session.message = await channel.send(embed=embed, view=JamView(self, session.token))
        except discord.HTTPException:
            log.exception("failed to repost jam panel for guild %s", guild_id)

    async def stop_jam(self, guild_id: int) -> None:
        session = self._sessions.pop(guild_id, None)
        if session is None:
            return
        session.task.cancel()
        try:
            await session.message.edit(view=None)
        except discord.HTTPException:
            pass  # message already gone -- nothing to clean up

    async def _refresh_loop(self, guild_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
                await self._refresh(guild_id)
        except asyncio.CancelledError:
            pass

    async def _refresh(self, guild_id: int) -> None:
        session = self._sessions.get(guild_id)
        if session is None:
            return
        try:
            embed = await self.build_embed(session.slot_index)
            await session.message.edit(embed=embed)
        except discord.NotFound:
            self._sessions.pop(guild_id, None)  # panel message was deleted -- stop trying
        except Exception:
            log.exception("failed to refresh jam panel for guild %s", guild_id)

    async def apply_action(self, slot_index: int, action: str) -> bool:
        """Performs the transport action against Spotify. Returns False if
        the slot isn't linked or the call failed -- callers decide how to
        surface that (gateway: an ephemeral reply; relay: the edited embed
        already reflects reality either way, see interaction_relay.py)."""
        token = await self.web_api_link_manager.get_access_token(slot_index)
        if token is None:
            return False
        try:
            if action == "rewind":
                # Flush right before the commands that swap which track is
                # playing -- play/pause don't introduce new track audio, so
                # they're left alone. See librespot_manager.py's
                # LibrespotProcess.flush() for why this matters.
                flush_slot_pipe(slot_index, self.slot_store, self.librespot)
                await spotify_player_api.previous_track(token)
            elif action == "play":
                await spotify_player_api.play(token)
            elif action == "pause":
                await spotify_player_api.pause(token)
            elif action == "skip":
                flush_slot_pipe(slot_index, self.slot_store, self.librespot)
                await spotify_player_api.next_track(token)
        except Exception:
            return False
        return True

    async def handle_button(self, interaction: discord.Interaction, action: str) -> None:
        """Gateway-only entry point: JamView's buttons call this directly
        since there's a live Interaction to respond through here. The
        SQS-relay path (interaction_relay.py's _run_component) calls
        apply_action + build_embed itself instead, since a relayed button
        click has no gateway Interaction object -- just the same JSON."""
        guild_id = interaction.guild_id
        session = self._sessions.get(guild_id) if guild_id is not None else None
        if session is None:
            await interaction.response.send_message("This Jam session has ended.", ephemeral=True)
            return

        ok = await self.apply_action(session.slot_index, action)
        if not ok:
            await interaction.response.send_message(
                "That didn't work -- is Spotify Connect active on this slot?", ephemeral=True
            )
            return

        embed = await self.build_embed(session.slot_index)
        await interaction.response.edit_message(embed=embed)


class JamView(discord.ui.View):
    def __init__(self, manager: JamManager, token: str):
        super().__init__(timeout=None)
        self.manager = manager
        if PUBLIC_DASHBOARD_URL:
            url = f"{PUBLIC_DASHBOARD_URL}/jam/{token}"
            self.add_item(discord.ui.Button(label="Open dashboard", style=discord.ButtonStyle.link, url=url))

    @discord.ui.button(label="Rewind", style=discord.ButtonStyle.secondary, emoji="⏮", custom_id="jam:rewind")
    async def rewind(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.manager.handle_button(interaction, "rewind")

    @discord.ui.button(label="Play", style=discord.ButtonStyle.success, emoji="▶", custom_id="jam:play")
    async def play(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.manager.handle_button(interaction, "play")

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, emoji="⏸", custom_id="jam:pause")
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.manager.handle_button(interaction, "pause")

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭", custom_id="jam:skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.manager.handle_button(interaction, "skip")
