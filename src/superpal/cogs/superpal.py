"""Super Pal of the Week promotion: weekly rotation, manual promotion, wheel integration."""

import asyncio
import datetime
import secrets
from typing import cast

import discord
from discord import app_commands
from discord.ext import commands, tasks

import superpal.env as superpal_env
import superpal.static as superpal_static
from superpal.cogs.helpers import get_non_bot_members, get_super_pal_role
from superpal.schedule import next_sunday_noon_utc

log = superpal_env.log


async def promote_super_pal(
    guild: discord.Guild,
    channel: discord.abc.Messageable,
    new_super_pal: discord.Member,
    *,
    promoted_by: str | None = None,
) -> bool:
    """Swap the Super Pal role to new_super_pal and announce it.

    Removes the role from every current holder, adds it to the winner, and
    posts the congratulations message. promoted_by=None means the weekly
    rotation; otherwise it names the promoter (a member or "wheel spin").
    Returns False if the role doesn't exist.
    """
    role = get_super_pal_role(guild)
    if not role:
        return False

    for member in get_non_bot_members(guild):
        if role in member.roles:
            await member.remove_roles(role)
            log.info(f"{member.name} removed from super pal role")

    await new_super_pal.add_roles(role)
    log.info(f"{new_super_pal.name} promoted to super pal")

    if promoted_by is None:
        message = (
            f"Congratulations to {new_super_pal.mention}, "
            f"the super pal of the week! {superpal_static.WELCOME_MSG}"
        )
    else:
        message = (
            f"Congratulations {new_super_pal.mention}! "
            f"You have been promoted to super pal of the week by {promoted_by}. "
            f"{superpal_static.WELCOME_MSG}"
        )
    await channel.send(message)
    return True


class SuperPalCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        if not self.super_pal_of_the_week.is_running():
            self.super_pal_of_the_week.start()

    async def cog_unload(self) -> None:
        self.super_pal_of_the_week.cancel()

    @tasks.loop(hours=24 * 7)
    async def super_pal_of_the_week(self) -> None:
        """Weekly task to choose a new Super Pal of the Week."""
        try:
            guild = self.bot.get_guild(superpal_env.GUILD_ID or 0)
            if not guild:
                log.error(f"Could not find guild with ID {superpal_env.GUILD_ID}")
                return

            channel = cast(
                discord.TextChannel | None, self.bot.get_channel(superpal_env.CHANNEL_ID or 0)
            )
            if not channel:
                log.error(f"Could not find channel with ID {superpal_env.CHANNEL_ID}")
                return

            role = get_super_pal_role(guild)
            if not role:
                return

            true_member_list = get_non_bot_members(guild)
            if not true_member_list:
                log.error("No non-bot members found in guild")
                return

            log.info(f"Total guild members: {guild.member_count}")
            log.info(f"Cached members: {len(guild.members)}")
            log.info(f"Non-bot members: {len(true_member_list)}")
            if len(guild.members) < (guild.member_count or 0):
                log.warning(
                    "Member cache may be incomplete! Some users may be excluded from selection."
                )

            # Exclude current super pal so they can't be re-selected
            eligible_members = [m for m in true_member_list if role not in m.roles]
            if not eligible_members:
                log.error(
                    "No eligible members for super pal selection "
                    "(all members already have role)"
                )
                return

            new_super_pal = secrets.choice(eligible_members)
            log.info(f"Selected new super pal of the week: {new_super_pal.name}")

            await promote_super_pal(guild, channel, new_super_pal)

        except Exception as e:
            log.error(f"Error in super_pal_of_the_week task: {e}")

    @super_pal_of_the_week.before_loop
    async def before_super_pal_of_the_week(self) -> None:
        await self.bot.wait_until_ready()
        try:
            target = next_sunday_noon_utc()
            delta = target - datetime.datetime.now(datetime.timezone.utc)
            log.info("Super pal task: sleeping for %s. Will wake up Sunday at 12PM UTC.", delta)
            await asyncio.sleep(delta.total_seconds())
        except Exception as e:
            log.error("Error in before_super_pal_of_the_week: %s", e)

    @app_commands.command(name="superpal")
    @app_commands.checks.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
    async def add_super_pal(
        self, interaction: discord.Interaction, new_super_pal: discord.Member
    ) -> None:
        """Promote a user to Super Pal of the Week role.

        Args:
            new_super_pal: choose the member you want to promote to super pal
        """
        try:
            channel = cast(
                discord.TextChannel | None, self.bot.get_channel(superpal_env.CHANNEL_ID or 0)
            )
            if not channel:
                await interaction.response.send_message(
                    "Error: Could not find configured channel.", ephemeral=True
                )
                return

            assert interaction.guild is not None
            role = get_super_pal_role(interaction.guild)
            if not role:
                await interaction.response.send_message(
                    "Error: Super Pal role not found.", ephemeral=True
                )
                return

            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message(
                    "This command must be used in a server.", ephemeral=True
                )
                return

            if role in new_super_pal.roles:
                await interaction.response.send_message(
                    f"{new_super_pal.mention} is already super pal of the week.", ephemeral=True
                )
                return

            log.info(f"{new_super_pal.name} promoted by {interaction.user.name}")
            await interaction.response.send_message(
                f"You have promoted {new_super_pal.mention} to super pal of the week!",
                ephemeral=True,
            )
            await promote_super_pal(
                interaction.guild, channel, new_super_pal, promoted_by=interaction.user.name
            )

        except Exception as e:
            log.error(f"Error in add_super_pal command: {e}")
            await interaction.response.send_message(
                "Sorry, there was an error processing your request.", ephemeral=True
            )

    @commands.command(name="spotw", pass_context=True)
    @commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
    async def spotw_command(self, ctx, new_super_pal: discord.Member):
        """Promote users to Super Pal of the Week (legacy command)."""
        try:
            guild = self.bot.get_guild(superpal_env.GUILD_ID or 0)
            channel = cast(
                discord.TextChannel | None, self.bot.get_channel(superpal_env.CHANNEL_ID or 0)
            )

            if not guild or not channel:
                await ctx.send("Error: Could not find guild or channel.")
                return

            role = get_super_pal_role(guild)
            if not role:
                await ctx.send("Error: Super Pal role not found.")
                return

            if role in new_super_pal.roles:
                await ctx.send(f"{new_super_pal.mention} is already super pal of the week.")
                return

            log.info(f"{new_super_pal.name} promoted by {ctx.message.author.name}")
            await promote_super_pal(
                guild, channel, new_super_pal, promoted_by=ctx.message.author.name
            )

        except Exception as e:
            log.error(f"Error in spotw command: {e}")
            await ctx.send("Sorry, there was an error processing your request.")

    @commands.command(name="spinthewheel", pass_context=True)
    @commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
    async def spinthewheel(self, ctx):
        """Spin the wheel for a random Super Pal of the Week."""
        try:
            guild = self.bot.get_guild(superpal_env.GUILD_ID or 0)
            channel = cast(
                discord.TextChannel | None, self.bot.get_channel(superpal_env.CHANNEL_ID or 0)
            )

            if not guild or not channel:
                await ctx.send("Error: Could not find guild or channel.")
                return

            true_member_list = get_non_bot_members(guild)
            if not true_member_list:
                await ctx.send("Error: No members found.")
                return

            true_name_list = [member.name for member in true_member_list]
            true_name_str = ", ".join(true_name_list)

            # Send Spin the Wheel command
            await channel.send(f"?pick {true_name_str}")
            log.info("Spinning the wheel for new super pal")

        except Exception as e:
            log.error(f"Error in spinthewheel command: {e}")
            await ctx.send("Sorry, there was an error spinning the wheel.")

    @commands.Cog.listener("on_message")
    async def wheel_winner_listener(self, message: discord.Message) -> None:
        """Watch for Spin The Wheel bot winner embeds and promote the winner."""
        try:
            if not message.author.bot:
                return
            guild = self.bot.get_guild(superpal_env.GUILD_ID or 0)
            if not guild:
                return

            spin_the_wheel_role = discord.utils.get(
                guild.roles, name=superpal_static.SPIN_THE_WHEEL_ROLE_NAME
            )
            member = guild.get_member(message.author.id)

            # Only check embedded messages from Spin The Wheel Bot
            if member and spin_the_wheel_role and spin_the_wheel_role in member.roles:
                await self._handle_spin_the_wheel_message(message, guild)

        except Exception as e:
            log.error(f"Error in wheel_winner_listener: {e}")

    async def _handle_spin_the_wheel_message(
        self, message: discord.Message, guild: discord.Guild
    ) -> None:
        """Parse a wheel-winner embed and promote the named member."""
        try:
            for embed in message.embeds:
                if embed.description is None:
                    continue

                if len(embed.description) > 0 and embed.description[0] == "🏆":
                    # Grab winner name from Spin the Wheel message
                    new_super_pal_name = embed.description[12:-2]
                    new_super_pal = discord.utils.get(guild.members, name=new_super_pal_name)

                    if not new_super_pal:
                        log.error(f"Could not find member: {new_super_pal_name}")
                        return

                    log.info(f"{new_super_pal.name} was chosen by wheel spin")
                    await promote_super_pal(
                        guild, message.channel, new_super_pal, promoted_by="wheel spin"
                    )

        except Exception as e:
            log.error(f"Error handling spin the wheel message: {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SuperPalCog(bot))
