"""Long-polls the SQS queue lambda/wake_sleep.py relays interactions to.

Discord delivers interactions to either the gateway or the Interactions
Endpoint URL, never both. Once that endpoint URL is set (required so /wake
works while this process isn't even running), the gateway stops receiving
INTERACTION_CREATE entirely -- so /connect and /disconnect never reach the
CommandTree in main.py. Lambda relays those here as raw interaction JSON;
this replies on Discord's behalf via the webhook-followup API using the
interaction token, since there's no gateway Interaction object to respond
through.
"""

import asyncio
import json
import logging

import aiohttp
import boto3
import discord

from commands import do_connect, do_disconnect
from config import INTERACTIONS_QUEUE_URL
from librespot_manager import LibrespotManager

log = logging.getLogger("interaction_relay")

DISCORD_API = "https://discord.com/api/v10"
EPHEMERAL = 64


class InteractionRelay:
    def __init__(self, client: discord.Client, librespot: LibrespotManager):
        self.client = client
        self.librespot = librespot
        self._sqs = boto3.client("sqs")
        self._task: asyncio.Task | None = None
        self._http: aiohttp.ClientSession | None = None

    def start(self) -> None:
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
        command_name = interaction["data"]["name"]
        guild_id = int(interaction["guild_id"])
        guild = self.client.get_guild(guild_id)
        if guild is None:
            return "Bot isn't in that server (anymore?).", True

        member_id = int(interaction["member"]["user"]["id"])
        member = guild.get_member(member_id)
        if member is None:
            return "Couldn't find you in this server's member cache — try again in a moment.", True

        if command_name == "connect":
            options = {opt["name"]: opt["value"] for opt in interaction["data"].get("options", [])}
            return await do_connect(guild, member, options["slot"], self.librespot)
        if command_name == "disconnect":
            return await do_disconnect(guild)
        return f"Unknown command: {command_name}", True

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
