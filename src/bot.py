#!/usr/bin/env python3
"""Discord Super Pal of the Week Bot.

This bot manages weekly "Super Pal of the Week" promotions in a Discord server,
featuring AI-powered image generation, automated role management, and fun commands.
"""

import asyncio
import datetime
import random
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import superpal.static as superpal_static
import superpal.env as superpal_env

# Get logger
log = superpal_env.log

#############
# Bot setup #
#############
intents = discord.Intents.default()
intents.members = True  # Required to list all users in a guild
intents.message_content = True  # Required to use spin-the-wheel and grab winner
bot = commands.Bot(command_prefix='!', intents=intents)


##################
# Helper Functions
##################
def get_non_bot_members(guild: discord.Guild) -> List[discord.Member]:
    """Get list of non-bot members from a guild.

    Args:
        guild: Discord guild to get members from

    Returns:
        List of non-bot members
    """
    return [m for m in guild.members if not m.bot]


def get_super_pal_role(guild: discord.Guild) -> Optional[discord.Role]:
    """Get the Super Pal of the Week role from a guild.

    Args:
        guild: Discord guild to get role from

    Returns:
        Super Pal role or None if not found
    """
    role = discord.utils.get(guild.roles, name=superpal_static.SUPER_PAL_ROLE_NAME)
    if not role:
        log.error(f"Super Pal role '{superpal_static.SUPER_PAL_ROLE_NAME}' not found in guild")
    return role


async def promote_super_pal(
    new_super_pal: discord.Member,
    old_super_pal: Optional[discord.Member],
    role: discord.Role,
    channel: discord.TextChannel,
    promoted_by: str
) -> None:
    """Promote a new super pal and demote the old one.

    Args:
        new_super_pal: Member to promote
        old_super_pal: Current super pal to demote (if any)
        role: Super Pal role
        channel: Channel to send announcement
        promoted_by: Name of person/process that triggered promotion
    """
    try:
        # Remove role from old super pal if exists
        if old_super_pal and role in old_super_pal.roles:
            await old_super_pal.remove_roles(role)
            log.info(f"{old_super_pal.name} removed from Super Pal role")

        # Add role to new super pal
        if role not in new_super_pal.roles:
            await new_super_pal.add_roles(role)
            log.info(f"{new_super_pal.name} promoted to Super Pal by {promoted_by}")

            await channel.send(
                f'Congratulations {new_super_pal.mention}! '
                f'You have been promoted to super pal of the week by {promoted_by}. '
                f'{superpal_static.WELCOME_MSG}'
            )
        else:
            log.info(f"{new_super_pal.name} already has Super Pal role")

    except Exception as e:
        log.error(f"Error promoting super pal: {e}")
        await channel.send(f"Sorry, there was an error promoting {new_super_pal.mention}.")


##################
# Slash commands #
##################
@bot.tree.command(name='superpal')
@app_commands.checks.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def add_super_pal(interaction: discord.Interaction, new_super_pal: discord.Member) -> None:
    """Promote a user to Super Pal of the Week role.

    Args:
        new_super_pal: choose the member you want to promote to super pal
    """
    try:
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        if not channel:
            await interaction.response.send_message(
                'Error: Could not find configured channel.',
                ephemeral=True
            )
            return

        role = get_super_pal_role(interaction.guild)
        if not role:
            await interaction.response.send_message(
                'Error: Super Pal role not found.',
                ephemeral=True
            )
            return

        # Check if new super pal already has the role
        if role in new_super_pal.roles:
            await interaction.response.send_message(
                f'{new_super_pal.mention} is already super pal of the week.',
                ephemeral=True
            )
            return

        # Promote new super pal and remove current super pal
        await new_super_pal.add_roles(role)
        await interaction.user.remove_roles(role)

        log.info(f'{new_super_pal.name} promoted by {interaction.user.name}')

        await interaction.response.send_message(
            f'You have promoted {new_super_pal.mention} to super pal of the week!',
            ephemeral=True
        )

        await channel.send(
            f'Congratulations {new_super_pal.mention}! '
            f'You have been promoted to super pal of the week by {interaction.user.name}. '
            f'{superpal_static.WELCOME_MSG}'
        )

    except Exception as e:
        log.error(f"Error in add_super_pal command: {e}")
        await interaction.response.send_message(
            'Sorry, there was an error processing your request.',
            ephemeral=True
        )


###############
# Looped task #
###############
async def pick_super_pal_for_guild(guild: discord.Guild):
    """Pick a new Super Pal of the Week for a specific guild.

    Args:
        guild: Discord guild to pick super pal for
    """
    try:
        log.info(f"Processing super pal selection for guild: {guild.name} (ID: {guild.id})")

        # Get the designated channel for this guild
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        if not channel or channel.guild.id != guild.id:
            # If the configured channel doesn't belong to this guild,
            # try to find a suitable channel in the guild
            channel = discord.utils.find(
                lambda c: isinstance(c, discord.TextChannel) and c.permissions_for(guild.me).send_messages,
                guild.channels
            )

        if not channel:
            log.error(f"Could not find a suitable channel in guild {guild.name}")
            return

        role = get_super_pal_role(guild)
        if not role:
            log.warning(f"Super Pal role not found in guild {guild.name}")
            return

        # Get list of non-bot members and pick random member
        true_member_list = get_non_bot_members(guild)
        if not true_member_list:
            log.error(f"No non-bot members found in guild {guild.name}")
            return

        new_super_pal = random.choice(true_member_list)
        log.info(f'Picking new super pal of the week in {guild.name}: {new_super_pal.name}')

        # Check if chosen member already has role (avoid duplicates)
        if role in new_super_pal.roles:
            log.info(f'{new_super_pal.name} is already super pal in {guild.name}. Re-rolling.')
            # Try again with a different member
            remaining_members = [m for m in true_member_list if role not in m.roles]
            if remaining_members:
                new_super_pal = random.choice(remaining_members)
            else:
                log.info(f"All members in {guild.name} have been super pal. Keeping current.")
                return

        # Remove role from all current super pals
        for member in true_member_list:
            if role in member.roles:
                await member.remove_roles(role)
                log.info(f'{member.name} removed from super pal role in {guild.name}')

        # Add role to new super pal
        await new_super_pal.add_roles(role)
        log.info(f'{new_super_pal.name} promoted to super pal in {guild.name}')

        await channel.send(
            f'Congratulations to {new_super_pal.mention}, '
            f'the super pal of the week! {superpal_static.WELCOME_MSG}'
        )

    except Exception as e:
        log.error(f"Error picking super pal for guild {guild.name}: {e}")


@tasks.loop(hours=24*7)
async def super_pal_of_the_week():
    """Weekly task to choose a new Super Pal of the Week for all guilds."""
    try:
        log.info(f"Running weekly super pal selection across {len(bot.guilds)} guilds")

        # If GUILD_ID is configured, only process that specific guild (backward compatibility)
        if superpal_env.GUILD_ID:
            guild = bot.get_guild(superpal_env.GUILD_ID)
            if guild:
                await pick_super_pal_for_guild(guild)
            else:
                log.error(f"Could not find configured guild with ID {superpal_env.GUILD_ID}")
        else:
            # Process all guilds the bot is in
            for guild in bot.guilds:
                await pick_super_pal_for_guild(guild)

    except Exception as e:
        log.error(f"Error in super_pal_of_the_week task: {e}")


@super_pal_of_the_week.before_loop
async def before_super_pal_of_the_week():
    """Wait until Sunday at noon before starting the weekly task."""
    await bot.wait_until_ready()

    try:
        # Find amount of time until Sunday at noon
        now = datetime.datetime.now()
        current_day = datetime.date.today().isoweekday()
        days_until_sunday = 7 - current_day

        # If it's past noon on Sunday, add 7 days to timer
        if current_day == 7 and now.hour > 12:
            days_until_sunday = 7

        time_diff = now + datetime.timedelta(days=days_until_sunday)
        future = datetime.datetime(time_diff.year, time_diff.month, time_diff.day, 12, 0)

        # Sleep task until Sunday at noon
        sleep_duration = (future - now).total_seconds()
        log.info(f'Sleeping for {future - now}. Will wake up Sunday at 12PM.')
        await asyncio.sleep(sleep_duration)

    except Exception as e:
        log.error(f"Error in before_super_pal_of_the_week: {e}")


##############
# Bot events #
##############
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
    log.info(f'Bot logged in as {bot.user}')
    log.info(f'Connected to {len(bot.guilds)} guilds')

    try:
        await bot.tree.sync()
        log.info('Slash commands synced')
    except Exception as e:
        log.error(f'Error syncing slash commands: {e}')

    if not super_pal_of_the_week.is_running():
        super_pal_of_the_week.start()
        log.info('Weekly task started')


@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages, including Spin The Wheel integration."""
    try:
        # Skip bot messages
        if message.author.bot:
            # Check if this is from Spin The Wheel bot
            guild = message.guild
            if not guild:
                await bot.process_commands(message)
                return

            spin_the_wheel_role = discord.utils.get(guild.roles, name=superpal_static.SPIN_THE_WHEEL_ROLE_NAME)
            member = guild.get_member(message.author.id)

            # Only check embedded messages from Spin The Wheel Bot
            if member and spin_the_wheel_role and spin_the_wheel_role in member.roles:
                await handle_spin_the_wheel_message(message, guild)

            await bot.process_commands(message)
            return

        # Process commands for non-bot messages
        await bot.process_commands(message)

    except Exception as e:
        log.error(f"Error in on_message: {e}")


async def handle_spin_the_wheel_message(message: discord.Message, guild: discord.Guild):
    """Handle messages from Spin The Wheel bot to detect winners.

    Args:
        message: Message from Spin The Wheel bot
        guild: Discord guild
    """
    try:
        for embed in message.embeds:
            # Wait until message contains Spin the Wheel winner
            if embed.description is None:
                continue

            if len(embed.description) > 0 and embed.description[0] == '🏆':
                super_pal_role = get_super_pal_role(guild)
                if not super_pal_role:
                    return

                # Grab winner name from Spin the Wheel message
                new_super_pal_name = embed.description[12:-2]
                new_super_pal = discord.utils.get(guild.members, name=new_super_pal_name)

                if not new_super_pal:
                    log.error(f"Could not find member: {new_super_pal_name}")
                    return

                log.info(f'{new_super_pal.name} was chosen by wheel spin')

                # Remove existing Super Pal of the Week
                true_member_list = get_non_bot_members(guild)
                for member in true_member_list:
                    if super_pal_role in member.roles:
                        await member.remove_roles(super_pal_role)

                # Add new winner to Super Pal of the Week
                await new_super_pal.add_roles(super_pal_role)

                await message.channel.send(
                    f'Congratulations {new_super_pal.mention}! '
                    f'You have been promoted to super pal of the week by wheel spin. '
                    f'{superpal_static.WELCOME_MSG}'
                )

    except Exception as e:
        log.error(f"Error handling spin the wheel message: {e}")


################
# Bot commands #
################
@bot.command(name='spotw', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def spotw_command(ctx, new_super_pal: discord.Member):
    """Promote users to Super Pal of the Week (legacy command)."""
    try:
        guild = ctx.guild
        channel = bot.get_channel(superpal_env.CHANNEL_ID)

        if not guild or not channel:
            await ctx.send("Error: Could not find guild or channel.")
            return

        role = get_super_pal_role(guild)
        if not role:
            await ctx.send("Error: Super Pal role not found.")
            return

        current_super_pal = ctx.message.author

        # Promote new user and remove current super pal
        if role not in new_super_pal.roles:
            log.info(f'{new_super_pal.name} promoted by {current_super_pal.name}')
            await new_super_pal.add_roles(role)
            await current_super_pal.remove_roles(role)
            await channel.send(
                f'Congratulations {new_super_pal.mention}! '
                f'You have been promoted to super pal of the week by {current_super_pal.name}. '
                f'{superpal_static.WELCOME_MSG}'
            )
        else:
            await ctx.send(f'{new_super_pal.mention} is already super pal of the week.')

    except Exception as e:
        log.error(f"Error in spotw command: {e}")
        await ctx.send("Sorry, there was an error processing your request.")


@bot.command(name='spinthewheel', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def spinthewheel(ctx):
    """Spin the wheel for a random Super Pal of the Week."""
    try:
        guild = ctx.guild
        channel = bot.get_channel(superpal_env.CHANNEL_ID)

        if not guild or not channel:
            await ctx.send("Error: Could not find guild or channel.")
            return

        # Get list of non-bot members
        true_member_list = get_non_bot_members(guild)
        if not true_member_list:
            await ctx.send("Error: No members found.")
            return

        true_name_list = [member.name for member in true_member_list]
        true_name_str = ", ".join(true_name_list)

        # Send Spin the Wheel command
        await channel.send(f'?pick {true_name_str}')
        log.info('Spinning the wheel for new super pal')

    except Exception as e:
        log.error(f"Error in spinthewheel command: {e}")
        await ctx.send("Sorry, there was an error spinning the wheel.")


@bot.command(name='commands', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def list_commands(ctx):
    """Display information about available commands."""
    try:
        log.info(f'{ctx.message.author.name} used help command')
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        if channel:
            await channel.send(superpal_static.COMMANDS_MSG)
        else:
            await ctx.send(superpal_static.COMMANDS_MSG)

    except Exception as e:
        log.error(f"Error in commands command: {e}")
        await ctx.send("Sorry, there was an error displaying commands.")


@bot.command(name='cacaw', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def cacaw(ctx):
    """Send party parrot discord emoji."""
    try:
        log.info(f'{ctx.message.author.name} used cacaw command')
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        emoji_guild = bot.get_guild(superpal_env.EMOJI_GUILD_ID)

        if not emoji_guild:
            await ctx.send("Error: Emoji guild not found.")
            return

        partyparrot_emoji = discord.utils.get(emoji_guild.emojis, name='partyparrot')

        if partyparrot_emoji:
            await channel.send(str(partyparrot_emoji) * superpal_static.EMOJI_SPAM_COUNT)
        else:
            await ctx.send("Partyparrot emoji not found!")

    except Exception as e:
        log.error(f"Error in cacaw command: {e}")
        await ctx.send("Sorry, there was an error.")


@bot.command(name='meow', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def meow(ctx):
    """Send party cat discord emoji."""
    try:
        log.info(f'{ctx.message.author.name} used meow command')
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        emoji_guild = bot.get_guild(superpal_env.EMOJI_GUILD_ID)

        if not emoji_guild:
            await ctx.send("Error: Emoji guild not found.")
            return

        partymeow_emoji = discord.utils.get(emoji_guild.emojis, name='partymeow')

        if partymeow_emoji:
            await channel.send(str(partymeow_emoji) * superpal_static.EMOJI_SPAM_COUNT)
        else:
            await ctx.send("Partymeow emoji not found!")

    except Exception as e:
        log.error(f"Error in meow command: {e}")
        await ctx.send("Sorry, there was an error.")


@bot.command(name='karatechop', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def karate_chop(ctx):
    """Randomly remove one user from voice chat."""
    try:
        guild = ctx.guild
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        current_super_pal = ctx.message.author

        if not guild or not channel:
            await ctx.send("Error: Could not find guild or channel.")
            return

        active_members = [voice_channel.members for voice_channel in guild.voice_channels]

        # Check if anyone is in voice channels
        if not any(active_members):
            log.info(f'{current_super_pal.name} used karate chop, but no one is in voice channels')
            await channel.send(f'There is no one to karate chop, {current_super_pal.mention}!')
            return

        # Flatten user list, filter out bots, and choose random user
        flatten = lambda l: [x for y in l for x in y]
        true_member_list = [m for m in flatten(active_members) if not m.bot]

        if not true_member_list:
            await channel.send('No users found in voice channels!')
            return

        chopped_member = random.choice(true_member_list)
        log.info(f'{chopped_member.name} karate chopped')

        # Check that an 'AFK' channel exists
        afk_channels = [c for c in guild.voice_channels if superpal_static.AFK_CHANNEL_KEYWORD in c.name]

        if afk_channels:
            await chopped_member.move_to(afk_channels[0])
            await channel.send(f'karate chopped {chopped_member.mention}!')
        else:
            await channel.send(
                f'{chopped_member.mention} would have been chopped, but an AFK channel was not found.\n'
                'Please complain to the server owner.'
            )

    except Exception as e:
        log.error(f"Error in karate_chop command: {e}")
        await ctx.send("Sorry, there was an error processing karate chop.")


################
# Start the bot
################
if __name__ == '__main__':
    if not superpal_env.TOKEN:
        log.error("Bot token not configured. Cannot start bot.")
    else:
        try:
            bot.run(superpal_env.TOKEN)
        except Exception as e:
            log.error(f"Fatal error running bot: {e}")
