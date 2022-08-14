#!/usr/bin/env python3
import asyncio
import discord
import os
from datetime import date, datetime, timedelta
from discord.ext import commands, tasks
from dotenv import load_dotenv
from http.client import ResponseNotReady
from random import randrange

# Load environmental variables.
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = int(os.getenv('GUILD_ID'))
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID'))

# Required to list all users in a guild.
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Weekly Task: Choose "Super Pal of the Week"
@tasks.loop(hours=24*7)
async def super_pal_of_the_week():
    await bot.wait_until_ready()
    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(CHANNEL_ID)
    announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    role = discord.utils.get(guild.roles, name='super pal of the week')
    # Get list of members and filter out bots.
    true_member_list = [m for m in guild.members if not m.bot]
    # Choose random "Super Pal of the Week" from list.
    spotw = true_member_list[randrange(len(true_member_list))]
    print(f'\nPicking new super pal of the week.')

    for member in true_member_list:
        if role in member.roles and member == spotw:
            print(f'{member.name} is already super pal of the week. Re-rolling.')
            return await super_pal_of_the_week()
        elif role in member.roles:
            print(f'{member.name} has been removed from super pal of the week role.')
            await member.remove_roles(role)
        elif member == spotw:
            await spotw.add_roles(role)
            print(f'{member.name} has been added to super pal of the week role.')
            await announcements_channel.send(f'Congratulations to {spotw.mention}, the super pal of the week!')
            await channel.send(f'Congratulations {spotw.mention}! Welcome to the super pal channel.\n\n'
                                f'You can now try out the following super pal commands:\n'
                                f'!spotw @name | !cacaw | !meow | !commands (for full list)')

# Before Loop: Wait until Sunday at noon.
@super_pal_of_the_week.before_loop
async def before_super_pal_of_the_week():
    # Find amount of time until Sunday at noon. 
    now = datetime.now()
    days_until_sunday = 7 - date.today().isoweekday()
    # If it's past noon on Sunday, add 7 days to timer.
    if date.today().isoweekday() == 7 and now.hour > 12:
        days_until_sunday = 7
    future = datetime(now.year, now.month, now.day+days_until_sunday, 12, 0)
    # Sleep task until Sunday at noon.
    print(f'Sleeping for {(future-now)}. Will wake up Sunday at 12PM Eastern Time.')
    await asyncio.sleep((future-now).total_seconds())

# Event: Avoid printing errors message for commands that aren't related to Super Pal Bot.
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.errors.CommandNotFound):
        return
    raise error

# Event: Start loop once bot is ready
@bot.event
async def on_ready():
    if not super_pal_of_the_week.is_running():
        super_pal_of_the_week.start()

# Command: Promote users to "Super Pal of the Week"
@bot.command(name='spotw', pass_context=True)
@commands.has_role('super pal of the week')
async def add_super_pal(ctx, new_super_pal: discord.Member):
    channel = bot.get_channel(CHANNEL_ID)
    announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    role = discord.utils.get(ctx.guild.roles, name='super pal of the week')
    current_super_pal = ctx.message.author
    if role not in new_super_pal.roles:
        # Promote new user and remove current user.
        await new_super_pal.add_roles(role)
        await current_super_pal.remove_roles(role)
        print(f'{new_super_pal.name} promoted by {current_super_pal.name}')
        await announcements_channel.send(f'Congratulations {spotw.mention},'
                            f'You have been promoted to super pal of the week by {current_super_pal.name}.')
        await channel.send(f'Congratulations {spotw.mention}! Welcome to the super pal channel.\n\n'
                            f'You can now try out the following super pal commands:\n'
                            f'!spotw @name | !cacaw | !meow | !commands (for full list)')

# Command: Display more information about commands.
@bot.command(name='commands', pass_context=True)
@commands.has_role('super pal of the week')
async def list_commands(ctx):
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    current_super_pal = ctx.message.author
    print(f'{current_super_pal.name} used help command.')
    msg = f"""**!spotw @name**\n\tPromote another user to super pal of the week. Be sure to @mention the user.
**!cacaw**\n\tSpam the channel with party parrots.
**!meow**\n\tSpam the channel with party cats."""
    await channel.send(msg)

# Command: Send party parrot discord emoji
@bot.command(name='cacaw', pass_context=True)
@commands.has_role('super pal of the week')
async def cacaw(ctx):
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    current_super_pal = ctx.message.author
    print(f'{current_super_pal.name} used cacaw command.')
    await channel.send(':partyparrot:'*100)

# Command: Send party cat discord emoji
@bot.command(name='meow', pass_context=True)
@commands.has_role('super pal of the week')
async def meow(ctx):
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    current_super_pal = ctx.message.author
    print(f'{current_super_pal.name} used meow command.')
    await channel.send(':partymeow:'*100)

bot.run(TOKEN)
