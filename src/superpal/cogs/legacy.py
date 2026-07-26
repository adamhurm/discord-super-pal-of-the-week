"""Legacy prefix fun commands, gated on the Super Pal role."""

import secrets
from typing import cast

import discord
from discord.ext import commands

import superpal.env as superpal_env
import superpal.static as superpal_static

log = superpal_env.log


class LegacyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="commands", pass_context=True)
    @commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
    async def list_commands(self, ctx):
        """Display information about available commands."""
        try:
            log.info(f"{ctx.message.author.name} used help command")
            channel = cast(
                discord.TextChannel | None, self.bot.get_channel(superpal_env.CHANNEL_ID or 0)
            )
            if channel:
                await channel.send(superpal_static.COMMANDS_MSG)
            else:
                await ctx.send(superpal_static.COMMANDS_MSG)

        except Exception as e:
            log.error(f"Error in commands command: {e}")
            await ctx.send("Sorry, there was an error displaying commands.")

    @commands.command(name="cacaw", pass_context=True)
    @commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
    async def cacaw(self, ctx):
        """Send party parrot discord emoji."""
        await self._spam_emoji(ctx, "partyparrot", "cacaw")

    @commands.command(name="meow", pass_context=True)
    @commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
    async def meow(self, ctx):
        """Send party cat discord emoji."""
        await self._spam_emoji(ctx, "partymeow", "meow")

    async def _spam_emoji(self, ctx, emoji_name: str, command_name: str) -> None:
        try:
            log.info(f"{ctx.message.author.name} used {command_name} command")
            channel = cast(
                discord.TextChannel | None, self.bot.get_channel(superpal_env.CHANNEL_ID or 0)
            )
            emoji_guild = self.bot.get_guild(superpal_env.EMOJI_GUILD_ID or 0)

            if not emoji_guild:
                await ctx.send("Error: Emoji guild not found.")
                return

            emoji = discord.utils.get(emoji_guild.emojis, name=emoji_name)

            if emoji and channel:
                await channel.send(str(emoji) * superpal_static.EMOJI_SPAM_COUNT)
            else:
                await ctx.send(f"{emoji_name.capitalize()} emoji not found!")

        except Exception as e:
            log.error(f"Error in {command_name} command: {e}")
            await ctx.send("Sorry, there was an error.")

    @commands.command(name="karatechop", pass_context=True)
    @commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
    async def karate_chop(self, ctx):
        """Randomly remove one user from voice chat."""
        try:
            guild = self.bot.get_guild(superpal_env.GUILD_ID or 0)
            channel = cast(
                discord.TextChannel | None, self.bot.get_channel(superpal_env.CHANNEL_ID or 0)
            )
            current_super_pal = ctx.message.author

            if not guild or not channel:
                await ctx.send("Error: Could not find guild or channel.")
                return

            active_members = [voice_channel.members for voice_channel in guild.voice_channels]

            # Check if anyone is in voice channels
            if not any(active_members):
                log.info(
                    f"{current_super_pal.name} used karate chop, "
                    "but no one is in voice channels"
                )
                await channel.send(f"There is no one to karate chop, {current_super_pal.mention}!")
                return

            # Flatten user list, filter out bots, and choose random user
            def flatten(nested):
                return [x for y in nested for x in y]

            true_member_list = [m for m in flatten(active_members) if not m.bot]

            if not true_member_list:
                await channel.send("No users found in voice channels!")
                return

            chopped_member = secrets.choice(true_member_list)
            log.info(f"{chopped_member.name} karate chopped")

            # Check that an 'AFK' channel exists
            afk_channels = [
                c for c in guild.voice_channels if superpal_static.AFK_CHANNEL_KEYWORD in c.name
            ]

            if afk_channels:
                await chopped_member.move_to(afk_channels[0])
                await channel.send(f"karate chopped {chopped_member.mention}!")
            else:
                await channel.send(
                    f"{chopped_member.mention} would have been chopped, "
                    "but an AFK channel was not found.\n"
                    "Please complain to the server owner."
                )

        except Exception as e:
            log.error(f"Error in karate_chop command: {e}")
            await ctx.send("Sorry, there was an error processing karate chop.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LegacyCog(bot))
