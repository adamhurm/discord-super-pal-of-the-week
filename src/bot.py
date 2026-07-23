#!/usr/bin/env python3
"""Discord Super Pal of the Week Bot.

This bot manages weekly "Super Pal of the Week" promotions in a Discord server,
featuring AI-powered image generation, automated role management, and fun commands.
"""

import asyncio
import datetime
import secrets
from typing import cast

import discord
import uvicorn
from discord import app_commands
from discord.ext import commands, tasks

import superpal.env as superpal_env
import superpal.notify as notify
import superpal.palymarket.service as palymarket_svc
import superpal.static as superpal_static
from superpal.cards.db import init_db
from superpal.cards.service import (
    generate_magic_link,
    sync_members,
)
from superpal.cogs import EXTENSIONS
from superpal.cogs.helpers import (
    _is_clippy,
    get_non_bot_members,
    get_super_pal_role,
)
from superpal.env import WEBAPP_BASE_URL
from superpal.schedule import next_sunday_noon_utc

# Get logger
log = superpal_env.log

#############
# Bot setup #
#############
intents = discord.Intents.default()
intents.members = True  # Required to list all users in a guild
intents.message_content = True  # Required to use spin-the-wheel and grab winner


class SuperPalBot(commands.Bot):
    async def setup_hook(self) -> None:
        for ext in EXTENSIONS:
            await self.load_extension(ext)


bot = SuperPalBot(command_prefix="!", intents=intents)
notify.set_bot(bot)


##################
# Slash commands #
##################
@bot.tree.command(name="superpal")
@app_commands.checks.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def add_super_pal(interaction: discord.Interaction, new_super_pal: discord.Member) -> None:
    """Promote a user to Super Pal of the Week role.

    Args:
        new_super_pal: choose the member you want to promote to super pal
    """
    try:
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
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

        # Check if new super pal already has the role
        if role in new_super_pal.roles:
            await interaction.response.send_message(
                f"{new_super_pal.mention} is already super pal of the week.", ephemeral=True
            )
            return

        # Promote new super pal and remove current super pal
        await new_super_pal.add_roles(role)
        await interaction.user.remove_roles(role)

        log.info(f"{new_super_pal.name} promoted by {interaction.user.name}")

        await interaction.response.send_message(
            f"You have promoted {new_super_pal.mention} to super pal of the week!", ephemeral=True
        )

        await channel.send(
            f"Congratulations {new_super_pal.mention}! "
            f"You have been promoted to super pal of the week by {interaction.user.name}. "
            f"{superpal_static.WELCOME_MSG}"
        )

    except Exception as e:
        log.error(f"Error in add_super_pal command: {e}")
        await interaction.response.send_message(
            "Sorry, there was an error processing your request.", ephemeral=True
        )


@bot.tree.command(
    name="admin-link",
    description="Get a private admin dashboard link (The Clippy only)",
)
async def admin_link_command(interaction: discord.Interaction) -> None:
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


@bot.tree.command(
    name="announce",
    description="Post a message to the Super Pal channel (The Clippy only)",
)
@discord.app_commands.describe(message="The message to post to the channel")
async def announce_command(interaction: discord.Interaction, message: str) -> None:
    if not _is_clippy(interaction):
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return
    channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
    if channel is None:
        await interaction.response.send_message(
            "Could not find the Super Pal channel.", ephemeral=True
        )
        return
    await channel.send(message)
    await interaction.response.send_message("Announcement posted!", ephemeral=True)


###############
# Looped task #
###############
@tasks.loop(hours=24 * 7)
async def super_pal_of_the_week():
    """Weekly task to choose a new Super Pal of the Week."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID or 0)
        if not guild:
            log.error(f"Could not find guild with ID {superpal_env.GUILD_ID}")
            return

        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
        if not channel:
            log.error(f"Could not find channel with ID {superpal_env.CHANNEL_ID}")
            return

        role = get_super_pal_role(guild)
        if not role:
            return

        # Get list of non-bot members
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
            log.error("No eligible members for super pal selection (all members already have role)")
            return

        new_super_pal = secrets.choice(eligible_members)
        log.info(f"Selected new super pal of the week: {new_super_pal.name}")

        # Remove role from all current super pals
        for member in true_member_list:
            if role in member.roles:
                await member.remove_roles(role)
                log.info(f"{member.name} removed from super pal role")

        # Add role to new super pal
        await new_super_pal.add_roles(role)
        log.info(f"{new_super_pal.name} promoted to super pal")

        await channel.send(
            f"Congratulations to {new_super_pal.mention}, "
            f"the super pal of the week! {superpal_static.WELCOME_MSG}"
        )

    except Exception as e:
        log.error(f"Error in super_pal_of_the_week task: {e}")


@super_pal_of_the_week.before_loop
async def before_super_pal_of_the_week():
    await bot.wait_until_ready()
    try:
        target = next_sunday_noon_utc()
        delta = target - datetime.datetime.now(datetime.timezone.utc)
        log.info("Super pal task: sleeping for %s. Will wake up Sunday at 12PM UTC.", delta)
        await asyncio.sleep(delta.total_seconds())
    except Exception as e:
        log.error("Error in before_super_pal_of_the_week: %s", e)


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

    if not super_pal_of_the_week.is_running():
        super_pal_of_the_week.start()
        log.info("Weekly task started")


@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages, including Spin The Wheel integration."""
    try:
        # Skip bot messages
        if message.author.bot:
            # Check if this is from Spin The Wheel bot
            guild = bot.get_guild(superpal_env.GUILD_ID or 0)
            if not guild:
                await bot.process_commands(message)
                return

            spin_the_wheel_role = discord.utils.get(
                guild.roles, name=superpal_static.SPIN_THE_WHEEL_ROLE_NAME
            )
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

            if len(embed.description) > 0 and embed.description[0] == "🏆":
                super_pal_role = get_super_pal_role(guild)
                if not super_pal_role:
                    return

                # Grab winner name from Spin the Wheel message
                new_super_pal_name = embed.description[12:-2]
                new_super_pal = discord.utils.get(guild.members, name=new_super_pal_name)

                if not new_super_pal:
                    log.error(f"Could not find member: {new_super_pal_name}")
                    return

                log.info(f"{new_super_pal.name} was chosen by wheel spin")

                # Remove existing Super Pal of the Week
                true_member_list = get_non_bot_members(guild)
                for member in true_member_list:
                    if super_pal_role in member.roles:
                        await member.remove_roles(super_pal_role)

                # Add new winner to Super Pal of the Week
                await new_super_pal.add_roles(super_pal_role)

                await message.channel.send(
                    f"Congratulations {new_super_pal.mention}! "
                    f"You have been promoted to super pal of the week by wheel spin. "
                    f"{superpal_static.WELCOME_MSG}"
                )

    except Exception as e:
        log.error(f"Error handling spin the wheel message: {e}")


################
# Bot commands #
################
@bot.command(name="spotw", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def spotw_command(ctx, new_super_pal: discord.Member):
    """Promote users to Super Pal of the Week (legacy command)."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID or 0)
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))

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
            log.info(f"{new_super_pal.name} promoted by {current_super_pal.name}")
            await new_super_pal.add_roles(role)
            await current_super_pal.remove_roles(role)
            await channel.send(
                f"Congratulations {new_super_pal.mention}! "
                f"You have been promoted to super pal of the week by {current_super_pal.name}. "
                f"{superpal_static.WELCOME_MSG}"
            )
        else:
            await ctx.send(f"{new_super_pal.mention} is already super pal of the week.")

    except Exception as e:
        log.error(f"Error in spotw command: {e}")
        await ctx.send("Sorry, there was an error processing your request.")


@bot.command(name="spinthewheel", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def spinthewheel(ctx):
    """Spin the wheel for a random Super Pal of the Week."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID or 0)
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))

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
        await channel.send(f"?pick {true_name_str}")
        log.info("Spinning the wheel for new super pal")

    except Exception as e:
        log.error(f"Error in spinthewheel command: {e}")
        await ctx.send("Sorry, there was an error spinning the wheel.")


@bot.command(name="commands", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def list_commands(ctx):
    """Display information about available commands."""
    try:
        log.info(f"{ctx.message.author.name} used help command")
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
        if channel:
            await channel.send(superpal_static.COMMANDS_MSG)
        else:
            await ctx.send(superpal_static.COMMANDS_MSG)

    except Exception as e:
        log.error(f"Error in commands command: {e}")
        await ctx.send("Sorry, there was an error displaying commands.")


@bot.command(name="cacaw", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def cacaw(ctx):
    """Send party parrot discord emoji."""
    try:
        log.info(f"{ctx.message.author.name} used cacaw command")
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
        emoji_guild = bot.get_guild(superpal_env.EMOJI_GUILD_ID or 0)

        if not emoji_guild:
            await ctx.send("Error: Emoji guild not found.")
            return

        partyparrot_emoji = discord.utils.get(emoji_guild.emojis, name="partyparrot")

        if partyparrot_emoji and channel:
            await channel.send(str(partyparrot_emoji) * superpal_static.EMOJI_SPAM_COUNT)
        else:
            await ctx.send("Partyparrot emoji not found!")

    except Exception as e:
        log.error(f"Error in cacaw command: {e}")
        await ctx.send("Sorry, there was an error.")


@bot.command(name="meow", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def meow(ctx):
    """Send party cat discord emoji."""
    try:
        log.info(f"{ctx.message.author.name} used meow command")
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
        emoji_guild = bot.get_guild(superpal_env.EMOJI_GUILD_ID or 0)

        if not emoji_guild:
            await ctx.send("Error: Emoji guild not found.")
            return

        partymeow_emoji = discord.utils.get(emoji_guild.emojis, name="partymeow")

        if partymeow_emoji and channel:
            await channel.send(str(partymeow_emoji) * superpal_static.EMOJI_SPAM_COUNT)
        else:
            await ctx.send("Partymeow emoji not found!")

    except Exception as e:
        log.error(f"Error in meow command: {e}")
        await ctx.send("Sorry, there was an error.")


@bot.command(name="karatechop", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def karate_chop(ctx):
    """Randomly remove one user from voice chat."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID or 0)
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
        current_super_pal = ctx.message.author

        if not guild or not channel:
            await ctx.send("Error: Could not find guild or channel.")
            return

        active_members = [voice_channel.members for voice_channel in guild.voice_channels]

        # Check if anyone is in voice channels
        if not any(active_members):
            log.info(f"{current_super_pal.name} used karate chop, but no one is in voice channels")
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


#####################
# Palymarket commands
#####################
@bot.tree.command(name="palymarket-propose", description="Propose a new prediction market")
@app_commands.describe(title="Short title for the market", description="Full description")
async def palymarket_propose(
    interaction: discord.Interaction, title: str, description: str
) -> None:
    await interaction.response.defer(ephemeral=True)
    market = await palymarket_svc.propose_market(title, description, str(interaction.user.id))
    await interaction.followup.send(
        f"Market proposed! Admins will review it shortly. ID: {market.id}", ephemeral=True
    )
    if isinstance(interaction.channel, discord.abc.Messageable):
        await interaction.channel.send(
            f"📊 New market proposed by {interaction.user.mention}: "
            f"**{market.title}** (ID: {market.id}) — awaiting admin approval"
        )


@bot.tree.command(name="palymarket-bet", description="Place or update a bet on a market")
@app_commands.describe(market_id="Market ID to bet on", amount="Amount of Palycoins to bet")
@app_commands.choices(
    side=[
        app_commands.Choice(name="Yes", value="yes"),
        app_commands.Choice(name="No", value="no"),
    ]
)
async def palymarket_bet(
    interaction: discord.Interaction,
    market_id: int,
    side: app_commands.Choice[str],
    amount: app_commands.Range[int, 1],
) -> None:
    await interaction.response.defer(ephemeral=True)
    await palymarket_svc.get_palycoin_balance(str(interaction.user.id))
    success, reason = await palymarket_svc.place_or_update_bet(
        market_id, str(interaction.user.id), side.value, amount
    )
    if success:
        await interaction.followup.send(
            f"Bet placed! {amount} Palycoins on {side.value.upper()} for market #{market_id}",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(reason, ephemeral=True)


@bot.tree.command(name="palymarket-list", description="List all open prediction markets")
async def palymarket_list(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    markets = await palymarket_svc.list_markets(status="open")
    if not markets:
        await interaction.followup.send("No open markets right now.", ephemeral=True)
        return
    embed = discord.Embed(title="📊 Open Palymarkets")
    for m in markets:
        embed.add_field(
            name=f"#{m.id}: {m.title}",
            value=f"YES: {m.yes_pool} | NO: {m.no_pool}",
            inline=False,
        )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="palymarket-balance", description="Check your Palycoin balance and bets")
async def palymarket_balance(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    balance = await palymarket_svc.get_palycoin_balance(str(interaction.user.id))
    active_bets = await palymarket_svc.get_player_active_bets(str(interaction.user.id))
    embed = discord.Embed(title="📊 Palycoin Balance")
    embed.add_field(name="Balance", value=f"{balance} Palycoins", inline=False)
    if active_bets:
        bet_lines = [
            f"#{market.id} **{market.title}**: {bet.amount} on {bet.side.upper()}"
            for market, bet in active_bets
        ]
        embed.add_field(name="Active Bets", value="\n".join(bet_lines), inline=False)
    else:
        embed.add_field(name="Active Bets", value="None", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)



@bot.tree.command(name="palymarket-approve", description="[Admin] Approve a pending market")
@app_commands.describe(market_id="Market ID to approve")
@app_commands.check(_is_clippy)
async def palymarket_approve(interaction: discord.Interaction, market_id: int) -> None:
    await interaction.response.defer(ephemeral=True)
    success, reason = await palymarket_svc.approve_market(market_id, str(interaction.user.id))
    if success:
        await interaction.followup.send(f"Market #{market_id} approved.", ephemeral=True)
        if isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.channel.send(
                f"📊 Market #{market_id} is now OPEN for betting!"
            )
    else:
        await interaction.followup.send(
            f"Could not approve market #{market_id}: {reason}", ephemeral=True
        )


@bot.tree.command(name="palymarket-reject", description="[Admin] Reject a pending market")
@app_commands.describe(market_id="Market ID to reject", reason="Reason for rejection")
@app_commands.check(_is_clippy)
async def palymarket_reject(
    interaction: discord.Interaction, market_id: int, reason: str
) -> None:
    await interaction.response.defer(ephemeral=True)
    success, _ = await palymarket_svc.reject_market(market_id, str(interaction.user.id))
    if success:
        await interaction.followup.send(f"Market #{market_id} rejected.", ephemeral=True)
    else:
        await interaction.followup.send(
            f"Could not reject market #{market_id}.", ephemeral=True
        )


@bot.tree.command(name="palymarket-close", description="[Admin] Close a market to new bets")
@app_commands.describe(market_id="Market ID to close")
@app_commands.check(_is_clippy)
async def palymarket_close(interaction: discord.Interaction, market_id: int) -> None:
    await interaction.response.defer(ephemeral=True)
    success, reason = await palymarket_svc.close_market(market_id, str(interaction.user.id))
    if success:
        await interaction.followup.send(f"Market #{market_id} closed.", ephemeral=True)
        if isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.channel.send(
                f"📊 Market #{market_id} is now CLOSED. No more bets accepted."
            )
    else:
        await interaction.followup.send(
            f"Could not close market #{market_id}: {reason}", ephemeral=True
        )


@bot.tree.command(name="palymarket-resolve", description="[Admin] Resolve a market and pay winners")
@app_commands.describe(market_id="Market ID to resolve")
@app_commands.choices(
    outcome=[
        app_commands.Choice(name="Yes", value="yes"),
        app_commands.Choice(name="No", value="no"),
    ]
)
@app_commands.check(_is_clippy)
async def palymarket_resolve(
    interaction: discord.Interaction,
    market_id: int,
    outcome: app_commands.Choice[str],
) -> None:
    await interaction.response.defer(ephemeral=True)
    result = await palymarket_svc.resolve_market(market_id, outcome.value, str(interaction.user.id))
    if "error" in result:
        await interaction.followup.send(
            f"Could not resolve market #{market_id}: {result['error']}", ephemeral=True
        )
        return
    await interaction.followup.send(f"Market #{market_id} resolved.", ephemeral=True)
    if isinstance(interaction.channel, discord.abc.Messageable):
        embed = discord.Embed(title=f"📊 Market #{market_id} Resolved")
        embed.add_field(name="Outcome", value=result["outcome"].upper(), inline=True)
        embed.add_field(name="Total Pool", value=str(result["total_pool"]), inline=True)
        embed.add_field(name="Winners", value=str(result["winner_count"]), inline=True)
        top_payouts = sorted(result["payouts"], key=lambda p: p["payout"], reverse=True)[:5]
        if top_payouts:
            payout_lines = [
                f"<@{p['player_id']}> → {p['payout']} Palycoins" for p in top_payouts
            ]
            embed.add_field(name="Top Payouts", value="\n".join(payout_lines), inline=False)
        await interaction.channel.send(embed=embed)


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


################
# Start the bot
################
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
