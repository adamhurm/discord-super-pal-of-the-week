"""Admin commands, gated on the Clippy role."""

from typing import cast

import discord
from discord import app_commands
from discord.ext import commands

import superpal.env as superpal_env
from superpal.cards.service import generate_magic_link
from superpal.cogs.helpers import _is_clippy
from superpal.env import WEBAPP_BASE_URL

log = superpal_env.log


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="admin-link",
        description="Get a private admin dashboard link (The Clippy only)",
    )
    async def admin_link_command(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not _is_clippy(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return
        url = await generate_magic_link(
            user_id=str(member.id),
            link_type="admin",
            base_url=WEBAPP_BASE_URL,
        )
        try:
            await member.send(
                "Here's your private admin dashboard link "
                f"(valid for 24 hours after first click):\n{url}"
            )
            await interaction.response.send_message(
                "Check your DMs for your admin link!", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I couldn't send you a DM. Please enable DMs from server members and try again.",
                ephemeral=True,
            )

    @app_commands.command(
        name="announce",
        description="Post a message to the Super Pal channel (The Clippy only)",
    )
    @app_commands.describe(message="The message to post to the channel")
    async def announce_command(self, interaction: discord.Interaction, message: str) -> None:
        if not _is_clippy(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return
        channel = cast(
            discord.TextChannel | None, self.bot.get_channel(superpal_env.CHANNEL_ID or 0)
        )
        if channel is None:
            await interaction.response.send_message(
                "Could not find the Super Pal channel.", ephemeral=True
            )
            return
        await channel.send(message)
        await interaction.response.send_message("Announcement posted!", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
