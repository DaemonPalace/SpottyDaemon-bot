"""Long-polls the SQS queue lambda/wake_sleep.py relays interactions to.

Discord delivers interactions to either the gateway or the Interactions
Endpoint URL, never both. Once that endpoint URL is set (required so /wake
works while this process isn't even running), the gateway stops receiving
INTERACTION_CREATE entirely -- so most commands never reach the CommandTree
in main.py. Lambda relays those here as raw interaction JSON; this replies
on Discord's behalf via the webhook-followup API using the interaction
token, since there's no gateway Interaction object to respond through.

/connect and /link are answered by the Lambda directly with a Discord MODAL
(to collect a password without it appearing in the channel's visible
command-usage line) instead of being relayed as a plain APPLICATION_COMMAND
-- what shows up here for those is the modal's *submission*
(type == MODAL_SUBMIT), with the original slot name folded into the modal's
custom_id ("connect:<slot>" / "link:<slotname>") since the submission
payload doesn't carry the original command's other arguments. The
"Paste redirect URL" button on /link and /link-web-api's replies works the
same way: Lambda answers that button click directly with a MODAL
(custom_id "paste-finish:link" / "paste-finish:link-web-api"), and its
submission comes back here as another MODAL_SUBMIT.

Message component interactions (type == MESSAGE_COMPONENT, i.e. a button
click) are all deferred by Lambda as DEFERRED_UPDATE_MESSAGE, so the real
response always edits the message the button lives on via the webhook
API's @original endpoint: Jam's Rewind/Play/Pause/Skip ("jam:<action>")
update just the embed; /play's per-track Play/Queue buttons
("play-track:<mode>:<uri>") replace the content and clear the buttons
entirely, collapsing the picker to a one-line confirmation once a track's
been chosen.

Autocomplete never reaches here at all -- Discord doesn't support
deferring an autocomplete response, so Lambda always answers those with an
empty choice list directly. Not that it matters: /play no longer uses
autocomplete at all, see main.py's TrackResultsView.
"""

import asyncio
import json
import logging

import aiohttp
import discord

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
    resolve_jam_slot,
)
from config import AWS_REGION, INTERACTIONS_QUEUE_URL, SPOTIFY_DIRECT_CALLBACK
from jam import JamManager
from librespot_manager import LibrespotManager
from slot_store import SlotStore
from spotify_link import LinkManager
from spotify_web_api import WebApiLinkManager

log = logging.getLogger("interaction_relay")

DISCORD_API = "https://discord.com/api/v10"
EPHEMERAL = 64

TYPE_APPLICATION_COMMAND = 2
TYPE_MESSAGE_COMPONENT = 3
TYPE_MODAL_SUBMIT = 5

# Discord permission bit for "Manage Server", used as a defense-in-depth
# check on /delete-slot -- default_permissions on the command (main.py)
# already gates this Discord-side, but production traffic never touches
# that gateway-registered command object (it comes straight from Lambda via
# SQS), so this is checked again here against the raw interaction payload.
MANAGE_GUILD_PERMISSION = 0x20


def _link_start_components(authorize_url: str, paste_flow: str) -> list[dict]:
    """One row: a pure Link-style button (Discord opens it client-side, no
    interaction generated -- safe to send with no live Interaction behind
    it) plus a "Paste redirect URL" button whose click Lambda answers with
    a MODAL directly (custom_id "paste-finish:<paste_flow>" once
    submitted, see this module's docstring). Direct mode drops the paste
    button -- Spotify redirects to the bot's own callback instead."""
    buttons = [{"type": 2, "style": 5, "label": "Log in with Spotify", "url": authorize_url}]
    if not SPOTIFY_DIRECT_CALLBACK:
        buttons.append({"type": 2, "style": 1, "label": "Paste redirect URL", "custom_id": f"paste:{paste_flow}"})
    return [{"type": 1, "components": buttons}]


def _track_result_components(results: list[dict]) -> list[dict]:
    """One row per track (up to 5, Discord's row cap) -- the Play button's
    own label is the track name, so the message needs no separate text
    lines at all. Clicking either button collapses the whole message (see
    InteractionRelay._run_component)."""
    rows = []
    for track in results[:5]:
        uri = track["uri"]
        artists = ", ".join(a["name"] for a in track.get("artists", []))
        label = f"{track['name']} — {artists}"[:80]
        rows.append(
            {
                "type": 1,
                "components": [
                    {"type": 2, "style": 3, "label": label, "custom_id": f"play-track:play_now:{uri}"},
                    {"type": 2, "style": 2, "label": "➕ Queue", "custom_id": f"play-track:queue:{uri}"},
                ],
            }
        )
    return rows


class InteractionRelay:
    def __init__(
        self,
        client: discord.Client,
        librespot: LibrespotManager,
        store: SlotStore,
        link_manager: LinkManager,
        web_api_link_manager: WebApiLinkManager,
        jam_manager: JamManager,
    ):
        self.client = client
        self.librespot = librespot
        self.store = store
        self.link_manager = link_manager
        self.web_api_link_manager = web_api_link_manager
        self.jam_manager = jam_manager
        self._sqs = None
        self._task: asyncio.Task | None = None
        self._http: aiohttp.ClientSession | None = None

    def start(self) -> None:
        # Imported and built here, not at module level / in __init__, so
        # importing/constructing this class never requires boto3 to even be
        # installed -- only actually starting the relay (main.py only does
        # so when INTERACTIONS_QUEUE_URL is set). boto3 is an optional
        # legacy-AWS dependency (requirements-aws.txt), not part of the
        # plain self-host install.
        # region_name explicit for the same reason bot/config.py's Secrets
        # Manager client needs it -- botocore doesn't reliably auto-resolve
        # a region under this systemd service. An unhandled exception here
        # would abort the rest of on_ready() silently (discord.py's default
        # on_error just logs it), taking link_manager/diagnostics_api's
        # own .start() calls down with it since they run after this one.
        import boto3

        self._sqs = boto3.client("sqs", region_name=AWS_REGION)
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        self._http = aiohttp.ClientSession()
        try:
            while True:
                messages = await asyncio.to_thread(
                    self._sqs.receive_message,
                    QueueUrl=INTERACTIONS_QUEUE_URL,
                    MaxNumberOfMessages=5,
                    WaitTimeSeconds=20,
                )
                for message in messages.get("Messages", []):
                    await self._handle(message)
        finally:
            await self._http.close()

    def _resolve_guild_member(self, interaction: dict) -> tuple[discord.Guild, discord.Member] | tuple[None, None]:
        guild = self.client.get_guild(int(interaction["guild_id"]))
        if guild is None:
            return None, None
        member_id = int(interaction["member"]["user"]["id"])
        member = guild.get_member(member_id)
        return guild, member

    async def _handle(self, message: dict) -> None:
        receipt_handle = message["ReceiptHandle"]
        try:
            interaction = json.loads(message["Body"])
            guild, member = self._resolve_guild_member(interaction)
            if guild is None:
                pass  # nothing sane to reply with -- drop it
            elif interaction.get("type") == TYPE_MESSAGE_COMPONENT:
                await self._run_component(interaction, guild)
            else:
                if member is None:
                    await self._followup(
                        interaction, "Couldn't find you in this server's member cache — try again in a moment.", True
                    )
                else:
                    result = await self._run_command(interaction, guild, member)
                    if result is not None:
                        content, ephemeral, components = result
                        await self._followup(interaction, content, ephemeral, components=components)
        except Exception:
            log.exception("failed to process relayed interaction")
        finally:
            await asyncio.to_thread(
                self._sqs.delete_message,
                QueueUrl=INTERACTIONS_QUEUE_URL,
                ReceiptHandle=receipt_handle,
            )

    async def _run_command(
        self, interaction: dict, guild: discord.Guild, member: discord.Member
    ) -> tuple[str, bool, list[dict] | None] | None:
        """Returns (content, ephemeral, components) for _handle to post as
        a single followup -- components is only ever non-None for
        link-web-api/link's modal-submit (the login+paste buttons). Returns
        None if this already sent its own followup(s) (/play posts one
        message per track, so it can't go through the single-followup
        path)."""
        if interaction.get("type") == TYPE_MODAL_SUBMIT:
            return await self._run_modal_submit(interaction, guild, member)

        command_name = interaction["data"]["name"]
        options = {opt["name"]: opt["value"] for opt in interaction["data"].get("options", [])}
        member_id = member.id

        if command_name == "disconnect":
            content, ephemeral = await do_disconnect(guild)
        elif command_name == "link-finish":
            content, ephemeral = await do_link_finish(str(member_id), options.get("url"), self.link_manager)
        elif command_name == "link-web-api":
            content, ephemeral = await do_link_web_api(
                str(member_id), options.get("slotname", ""), self.store, self.web_api_link_manager
            )
            url = self.web_api_link_manager.authorize_url(str(member_id))
            return content, ephemeral, _link_start_components(url, "link-web-api") if url else None
        elif command_name == "link-web-api-finish":
            content, ephemeral = await do_link_web_api_finish(
                str(member_id), options.get("url", ""), self.web_api_link_manager
            )
        elif command_name == "delete-slot":
            permissions = int(interaction["member"].get("permissions", "0"))
            if not permissions & MANAGE_GUILD_PERMISSION:
                content, ephemeral = "You need the Manage Server permission to do that.", True
            else:
                content, ephemeral = await do_delete_slot(options["name"], self.store, self.librespot)
        elif command_name == "jam":
            content, ephemeral = await self._run_jam(interaction, guild)
        elif command_name == "play":
            await self._run_play(interaction, guild, options.get("query", ""))
            return None
        else:
            content, ephemeral = f"Unknown command: {command_name}", True
        return content, ephemeral, None

    async def _run_play(self, interaction: dict, guild: discord.Guild, query: str) -> None:
        """Sends its own followup rather than returning through the
        single-reply path: one message, buttons only, one row per track
        (the Play button's label is the track name -- no separate text
        needed)."""
        results, error = await do_search_tracks(guild.id, query, self.store, self.web_api_link_manager)
        if error is not None:
            content, ephemeral = error
            await self._followup(interaction, content, ephemeral)
            return
        if not results:
            await self._followup(interaction, f"No tracks found for '{query}'.", True)
            return
        await self._followup(interaction, None, True, components=_track_result_components(results))

    async def _run_jam(self, interaction: dict, guild: discord.Guild) -> tuple[str, bool]:
        slot_index, error = resolve_jam_slot(guild.id, self.store)
        if error is not None:
            return error
        channel_id = int(interaction["channel_id"])
        channel = self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)
        await self.jam_manager.start_jam_in_channel(channel, slot_index)
        return "Jam panel posted above.", True

    async def _run_modal_submit(
        self, interaction: dict, guild: discord.Guild, member: discord.Member
    ) -> tuple[str, bool, list[dict] | None]:
        custom_id = interaction["data"]["custom_id"]
        command_name, _, argument = custom_id.partition(":")

        fields = {}
        for row in interaction["data"].get("components", []):
            for component in row.get("components", []):
                fields[component["custom_id"]] = component["value"]
        password = fields.get("password", "")
        channel_id = int(interaction["channel_id"]) if interaction.get("channel_id") else None

        if command_name == "connect":
            content, ephemeral = await do_connect(
                guild, member, argument, password, self.librespot, self.store, channel_id
            )
            if not ephemeral and channel_id is not None:
                channel = self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)
                note = await self.jam_manager.auto_start(channel, guild.id)
                if note is not None:
                    content = f"{content}\n{note}"
        elif command_name == "reconnect":
            content, ephemeral = await do_reconnect(
                guild, member, argument, password, self.librespot, self.store, channel_id
            )
        elif command_name == "link":
            content, ephemeral = await do_link(str(member.id), argument, password, self.link_manager)
            url = self.link_manager.authorize_url(str(member.id))
            return content, ephemeral, _link_start_components(url, "link") if url else None
        elif command_name == "paste-finish":
            pasted_url = fields.get("url", "").strip() or None
            if argument == "link":
                content, ephemeral = await do_link_finish(str(member.id), pasted_url, self.link_manager)
            else:
                content, ephemeral = await do_link_web_api_finish(
                    str(member.id), pasted_url or "", self.web_api_link_manager
                )
        else:
            content, ephemeral = f"Unknown modal submission: {custom_id}", True
        return content, ephemeral, None

    async def _run_component(self, interaction: dict, guild: discord.Guild) -> None:
        """Handles a relayed button click. Unlike _run_command, this sends
        its own response rather than returning (content, ephemeral) for
        _followup to post -- both button families edit the original
        message (Lambda defers every component click as
        DEFERRED_UPDATE_MESSAGE): jam's buttons only touch the embed
        (leaving its own buttons in place); play-track's buttons replace
        the content and clear the buttons entirely, so the picker
        collapses to a one-line confirmation instead of leaving a message
        full of now-stale buttons behind."""
        custom_id = interaction["data"]["custom_id"]
        if custom_id.startswith("jam:"):
            action = custom_id.split(":", 1)[1]
            slot_index = self.jam_manager.session_slot_index(guild.id)
            if slot_index is None:
                return  # panel's session already ended -- nothing to update
            await self.jam_manager.apply_action(slot_index, action)
            embed = await self.jam_manager.build_embed(slot_index)
            await self._edit_original_embed(interaction, embed)
            return
        if custom_id.startswith("play-track:"):
            _, mode, track_uri = custom_id.split(":", 2)
            content, _ephemeral = await do_play_track(
                guild.id, track_uri, mode, self.store, self.web_api_link_manager, self.librespot
            )
            await self._edit_original_clear(interaction, content)
            await self.jam_manager.repost(guild.id)
            return
        log.warning("unknown component custom_id: %s", custom_id)

    async def _followup(
        self, interaction: dict, content: str | None, ephemeral: bool, components: list[dict] | None = None
    ) -> None:
        application_id = interaction["application_id"]
        token = interaction["token"]
        payload = {}
        if content:
            payload["content"] = content
        if ephemeral:
            payload["flags"] = EPHEMERAL
        if components:
            payload["components"] = components
        url = f"{DISCORD_API}/webhooks/{application_id}/{token}"
        async with self._http.post(url, json=payload) as resp:
            if resp.status >= 300:
                log.error("followup send failed (%s): %s", resp.status, await resp.text())

    async def _edit_original_embed(self, interaction: dict, embed: discord.Embed) -> None:
        """Used by jam's buttons: only touches the embed, leaving whatever
        components (the buttons themselves) are already on the message
        untouched -- PATCH .../messages/@original is a partial update, an
        omitted field means "leave as-is"."""
        await self._patch_original(interaction, {"embeds": [embed.to_dict()]})

    async def _edit_original_clear(self, interaction: dict, content: str) -> None:
        """Used by play-track's buttons: replaces the content and removes
        the buttons entirely, collapsing the picker to a plain
        confirmation once a track's been chosen."""
        await self._patch_original(interaction, {"content": content, "components": []})

    async def _patch_original(self, interaction: dict, payload: dict) -> None:
        application_id = interaction["application_id"]
        token = interaction["token"]
        url = f"{DISCORD_API}/webhooks/{application_id}/{token}/messages/@original"
        async with self._http.patch(url, json=payload) as resp:
            if resp.status >= 300:
                log.error("edit-original failed (%s): %s", resp.status, await resp.text())
