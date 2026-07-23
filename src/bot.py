#!/usr/bin/env python3
"""Discord Super Pal of the Week Bot.

Entrypoint for the Discord bot and the card-game webapp, which run in one
process via asyncio.gather. All commands live in superpal/cogs/.
"""

import asyncio

import discord
import uvicorn
from discord import app_commands
from discord.ext import commands

import superpal.env as superpal_env
import superpal.notify as notify
from superpal.cards.db import init_db
from superpal.cards.service import sync_members
from superpal.cogs import EXTENSIONS

log = superpal_env.log

intents = discord.Intents.default()
intents.members = True  # Required to list all users in a guild
intents.message_content = True  # Required to use spin-the-wheel and grab winner


class SuperPalBot(commands.Bot):
    async def setup_hook(self) -> None:
        for ext in EXTENSIONS:
            await self.load_extension(ext)


bot = SuperPalBot(command_prefix="!", intents=intents)
notify.set_bot(bot)


@bot.event
async def on_command_error(ctx, error):
    """Suppress error messages for commands that aren't related to Super Pal Bot."""
    if isinstance(error, commands.errors.CommandNotFound):
        return
    if isinstance(error, commands.errors.MissingRole):
        await ctx.send("You don't have permission to use this command.")
        return
    log.error(f"Command error: {error}")
    raise error


@bot.event
async def on_ready():
    """Initialize bot when ready."""
    log.info(f"Bot logged in as {bot.user}")
    log.info(f"Connected to {len(bot.guilds)} guilds")

    try:
        await bot.tree.sync()
        log.info("Slash commands synced")
    except Exception as e:
        log.error(f"Error syncing slash commands: {e}")

    await init_db()
    guild = bot.get_guild(superpal_env.GUILD_ID or 0)
    if guild:
        members_data = [
            {
                "discord_id": str(m.id),
                "display_name": m.display_name,
                "avatar_url": str(m.display_avatar.url) if m.display_avatar else None,
            }
            for m in guild.members
            if not m.bot
        ]
        notify.set_guild_members_cache(members_data)
        await sync_members(members_data)
        log.info("Synced %d members to card DB", len(members_data))


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingRole):
        if interaction.response.is_done():
            await interaction.followup.send(
                "You don't have permission to use this command.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
    else:
        log.exception("Unhandled app command error", exc_info=error)
        raise error


async def _main() -> None:
    from superpal.env import WEBAPP_HOST, WEBAPP_PORT
    from superpal.webapp.app import create_app

    webapp = create_app()
    config = uvicorn.Config(webapp, host=WEBAPP_HOST, port=WEBAPP_PORT, log_level="info")
    server = uvicorn.Server(config)
    assert superpal_env.TOKEN is not None, "SUPERPAL_TOKEN is required to start the bot"
    async with bot:
        await asyncio.gather(
            bot.start(superpal_env.TOKEN),
            server.serve(),
        )


if __name__ == "__main__":
    asyncio.run(_main())
