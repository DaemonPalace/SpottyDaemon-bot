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
"""

import asyncio
import json
import logging

import aiohttp
import boto3
import discord

from commands import do_connect, do_delete_slot, do_disconnect, do_link, do_link_finish, do_reconnect
from config import INTERACTIONS_QUEUE_URL
from librespot_manager import LibrespotManager
from slot_store import SlotStore
from spotify_link import LinkManager

log = logging.getLogger("interaction_relay")

DISCORD_API = "https://discord.com/api/v10"
EPHEMERAL = 64

TYPE_APPLICATION_COMMAND = 2
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
    ):
        self.client = client
        self.librespot = librespot
        self.store = store
        self.link_manager = link_manager
        self._sqs = None
        self._task: asyncio.Task | None = None
        self._http: aiohttp.ClientSession | None = None

    def start(self) -> None:
        # Built here, not __init__, so importing/constructing this class
        # never requires boto3/AWS credentials -- only actually starting the
        # relay (main.py only does so when INTERACTIONS_QUEUE_URL is set).
        self._sqs = boto3.client("sqs")
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

    async def _handle(self, message: dict) -> None:
        receipt_handle = message["ReceiptHandle"]
        try:
            interaction = json.loads(message["Body"])
            content, ephemeral = await self._run_command(interaction)
            await self._followup(interaction, content, ephemeral)
        except Exception:
            log.exception("failed to process relayed interaction")
        finally:
            await asyncio.to_thread(
                self._sqs.delete_message,
                QueueUrl=INTERACTIONS_QUEUE_URL,
                ReceiptHandle=receipt_handle,
            )

    async def _run_command(self, interaction: dict) -> tuple[str, bool]:
        guild_id = int(interaction["guild_id"])
        guild = self.client.get_guild(guild_id)
        if guild is None:
            return "Bot isn't in that server (anymore?).", True

        member_id = int(interaction["member"]["user"]["id"])
        member = guild.get_member(member_id)
        if member is None:
            return "Couldn't find you in this server's member cache — try again in a moment.", True

        if interaction.get("type") == TYPE_MODAL_SUBMIT:
            return await self._run_modal_submit(interaction, guild, member)

        command_name = interaction["data"]["name"]
        options = {opt["name"]: opt["value"] for opt in interaction["data"].get("options", [])}

        if command_name == "disconnect":
            return await do_disconnect(guild)
        if command_name == "link-finish":
            return await do_link_finish(str(member_id), options.get("url"), self.link_manager)
        if command_name == "delete-slot":
            permissions = int(interaction["member"].get("permissions", "0"))
            if not permissions & MANAGE_GUILD_PERMISSION:
                return "You need the Manage Server permission to do that.", True
            return await do_delete_slot(options["name"], self.store, self.librespot)
        return f"Unknown command: {command_name}", True

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
