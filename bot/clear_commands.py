"""One-off maintenance utility: wipes ALL registered slash commands (both
the global scope and DEV_GUILD_ID's guild scope, if set) so a normal
restart repopulates Discord with exactly what main.py currently defines --
nothing stale left over from an earlier command shape or from switching
DEV_GUILD_ID on/off (main.py's tree.sync() only ever overwrites the scope
it's told to sync, never the other one, so toggling DEV_GUILD_ID can leave
duplicate/outdated commands sitting in whichever scope was last used).

Run once, from the bot/ directory, with the same venv/environment the bot
itself uses (so it resolves DISCORD_TOKEN/DEV_GUILD_ID the same way):

    cd /opt/discord-bot/bot && ../venv/bin/python clear_commands.py

Then restart the bot normally (systemctl restart discord-music-bot) to
resync the current command set.
"""

import asyncio

import discord
from discord import app_commands

from config import DEV_GUILD_ID, DISCORD_TOKEN


async def main() -> None:
    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)

    @client.event
    async def on_ready():
        tree.clear_commands(guild=None)
        await tree.sync()
        print("Cleared global commands.")

        if DEV_GUILD_ID:
            guild = discord.Object(id=int(DEV_GUILD_ID))
            tree.clear_commands(guild=guild)
            await tree.sync(guild=guild)
            print(f"Cleared commands for guild {DEV_GUILD_ID}.")

        await client.close()

    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
