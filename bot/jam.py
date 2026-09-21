"""Discord-native Jam: a live-updating control panel embed (now playing +
next 5 queued tracks) with Rewind/Play/Pause/Skip buttons, posted to
whichever channel /jam was run in. Replaces the old web Jam link.

One session per guild, mirroring commands.py's _active_sessions model.
Editing the message every REFRESH_INTERVAL_SECONDS is well under Discord's
per-channel edit rate limit even with several guilds running Jam at once.
"""

import asyncio
import logging

import discord

import spotify_player_api
from slot_store import SlotStore
from spotify_web_api import WebApiLinkManager

log = logging.getLogger("jam")

REFRESH_INTERVAL_SECONDS = 5
QUEUE_PREVIEW_SIZE = 5


class JamSession:
    def __init__(self, message: discord.Message, slot_index: int, task: asyncio.Task):
        self.message = message
        self.slot_index = slot_index
        self.task = task


class JamManager:
    def __init__(self, slot_store: SlotStore, web_api_link_manager: WebApiLinkManager):
        self.slot_store = slot_store
        self.web_api_link_manager = web_api_link_manager
        self._sessions: dict[int, JamSession] = {}

    async def _build_embed(self, slot_index: int) -> discord.Embed:
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

    async def start_jam(self, interaction: discord.Interaction, slot_index: int) -> None:
        guild_id = interaction.guild_id
        assert guild_id is not None
        await self.stop_jam(guild_id)  # replace any existing panel for this guild, don't stack them

        embed = await self._build_embed(slot_index)
        await interaction.response.send_message(embed=embed, view=JamView(self))
        message = await interaction.original_response()
        task = asyncio.create_task(self._refresh_loop(guild_id))
        self._sessions[guild_id] = JamSession(message, slot_index, task)

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
            embed = await self._build_embed(session.slot_index)
            await session.message.edit(embed=embed)
        except discord.NotFound:
            self._sessions.pop(guild_id, None)  # panel message was deleted -- stop trying
        except Exception:
            log.exception("failed to refresh jam panel for guild %s", guild_id)

    async def handle_button(self, interaction: discord.Interaction, action: str) -> None:
        guild_id = interaction.guild_id
        session = self._sessions.get(guild_id) if guild_id is not None else None
        if session is None:
            await interaction.response.send_message("This Jam session has ended.", ephemeral=True)
            return

        token = await self.web_api_link_manager.get_access_token(session.slot_index)
        if token is None:
            await interaction.response.send_message("Spotify Web API isn't linked for this slot.", ephemeral=True)
            return

        try:
            if action == "rewind":
                await spotify_player_api.previous_track(token)
            elif action == "play":
                await spotify_player_api.play(token)
            elif action == "pause":
                await spotify_player_api.pause(token)
            elif action == "skip":
                await spotify_player_api.next_track(token)
        except Exception:
            await interaction.response.send_message(
                "That didn't work -- is Spotify Connect active on this slot?", ephemeral=True
            )
            return

        embed = await self._build_embed(session.slot_index)
        await interaction.response.edit_message(embed=embed)


class JamView(discord.ui.View):
    def __init__(self, manager: JamManager):
        super().__init__(timeout=None)
        self.manager = manager

    @discord.ui.button(label="Rewind", style=discord.ButtonStyle.secondary, emoji="⏮")
    async def rewind(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.manager.handle_button(interaction, "rewind")

    @discord.ui.button(label="Play", style=discord.ButtonStyle.success, emoji="▶")
    async def play(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.manager.handle_button(interaction, "play")

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, emoji="⏸")
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.manager.handle_button(interaction, "pause")

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.manager.handle_button(interaction, "skip")
