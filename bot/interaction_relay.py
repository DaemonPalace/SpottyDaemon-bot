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
payload doesn't carry the original command's other arguments.

Message component interactions (type == MESSAGE_COMPONENT, i.e. a button
click -- currently just Jam's Rewind/Play/Pause/Skip, custom_id
"jam:<action>") are deferred by Lambda as DEFERRED_UPDATE_MESSAGE, so the
real response has to EDIT the original message via the webhook API's
@original endpoint instead of posting a new followup message.

Autocomplete (e.g. /play's live search-as-you-type) never reaches here at
all -- Discord doesn't support deferring an autocomplete response, so
Lambda answers those directly with an empty choice list (it has no access
to a slot's cached Spotify token). /play still fully works when relayed;
it just has no live suggestions while typing, same as if you'd hit enter
without picking one.
"""

import asyncio
import json
import logging

import aiohttp
import boto3
import discord

from commands import (
    do_connect,
    do_delete_slot,
    do_disconnect,
    do_link,
    do_link_finish,
    do_link_web_api_finish,
    do_play,
    do_reconnect,
    resolve_jam_slot,
)
from config import AWS_REGION, INTERACTIONS_QUEUE_URL
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
        # Built here, not __init__, so importing/constructing this class
        # never requires boto3/AWS credentials -- only actually starting the
        # relay (main.py only does so when INTERACTIONS_QUEUE_URL is set).
        # region_name explicit for the same reason bot/config.py's Secrets
        # Manager client needs it -- botocore doesn't reliably auto-resolve
        # a region under this systemd service. An unhandled exception here
        # would abort the rest of on_ready() silently (discord.py's default
        # on_error just logs it), taking link_manager/diagnostics_api's
        # own .start() calls down with it since they run after this one.
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
                    content, ephemeral = "Couldn't find you in this server's member cache — try again in a moment.", True
                else:
                    content, ephemeral = await self._run_command(interaction, guild, member)
                await self._followup(interaction, content, ephemeral)
        except Exception:
            log.exception("failed to process relayed interaction")
        finally:
            await asyncio.to_thread(
                self._sqs.delete_message,
                QueueUrl=INTERACTIONS_QUEUE_URL,
                ReceiptHandle=receipt_handle,
            )

    async def _run_command(self, interaction: dict, guild: discord.Guild, member: discord.Member) -> tuple[str, bool]:
        if interaction.get("type") == TYPE_MODAL_SUBMIT:
            return await self._run_modal_submit(interaction, guild, member)

        command_name = interaction["data"]["name"]
        options = {opt["name"]: opt["value"] for opt in interaction["data"].get("options", [])}
        member_id = member.id

        if command_name == "disconnect":
            return await do_disconnect(guild)
        if command_name == "link-finish":
            return await do_link_finish(str(member_id), options.get("url"), self.link_manager)
        if command_name == "link-web-api-finish":
            return await do_link_web_api_finish(str(member_id), options.get("url", ""), self.web_api_link_manager)
        if command_name == "delete-slot":
            permissions = int(interaction["member"].get("permissions", "0"))
            if not permissions & MANAGE_GUILD_PERMISSION:
                return "You need the Manage Server permission to do that.", True
            return await do_delete_slot(options["name"], self.store, self.librespot)
        if command_name == "jam":
            return await self._run_jam(interaction, guild)
        if command_name == "play":
            return await do_play(guild.id, options.get("track", ""), options.get("mode", "queue"), self.store, self.web_api_link_manager)
        return f"Unknown command: {command_name}", True

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
    ) -> tuple[str, bool]:
        custom_id = interaction["data"]["custom_id"]
        command_name, _, argument = custom_id.partition(":")

        fields = {}
        for row in interaction["data"].get("components", []):
            for component in row.get("components", []):
                fields[component["custom_id"]] = component["value"]
        password = fields.get("password", "")
        channel_id = int(interaction["channel_id"]) if interaction.get("channel_id") else None

        if command_name == "connect":
            return await do_connect(
                guild, member, argument, password, self.librespot, self.store, channel_id
            )
        if command_name == "reconnect":
            return await do_reconnect(
                guild, member, argument, password, self.librespot, self.store, channel_id
            )
        if command_name == "link":
            return await do_link(str(member.id), argument, password, self.link_manager)
        return f"Unknown modal submission: {custom_id}", True

    async def _run_component(self, interaction: dict, guild: discord.Guild) -> None:
        """Handles a relayed button click. Unlike _run_command, this sends
        its own response (an edit to the original message) rather than
        returning (content, ephemeral) for _followup to post as a new
        message -- Lambda deferred this as DEFERRED_UPDATE_MESSAGE, which
        Discord expects to be resolved by editing the message in place."""
        custom_id = interaction["data"]["custom_id"]
        prefix, _, action = custom_id.partition(":")
        if prefix != "jam":
            log.warning("unknown component custom_id: %s", custom_id)
            return
        slot_index = self.jam_manager.session_slot_index(guild.id)
        if slot_index is None:
            return  # panel's session already ended -- nothing to update
        await self.jam_manager.apply_action(slot_index, action)
        embed = await self.jam_manager.build_embed(slot_index)
        await self._edit_original(interaction, embed)

    async def _followup(self, interaction: dict, content: str, ephemeral: bool) -> None:
        application_id = interaction["application_id"]
        token = interaction["token"]
        payload = {"content": content}
        if ephemeral:
            payload["flags"] = EPHEMERAL
        url = f"{DISCORD_API}/webhooks/{application_id}/{token}"
        async with self._http.post(url, json=payload) as resp:
            if resp.status >= 300:
                log.error("followup send failed (%s): %s", resp.status, await resp.text())

    async def _edit_original(self, interaction: dict, embed: discord.Embed) -> None:
        application_id = interaction["application_id"]
        token = interaction["token"]
        url = f"{DISCORD_API}/webhooks/{application_id}/{token}/messages/@original"
        async with self._http.patch(url, json={"embeds": [embed.to_dict()]}) as resp:
            if resp.status >= 300:
                log.error("edit-original failed (%s): %s", resp.status, await resp.text())
