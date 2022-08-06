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
            await channel.send(f'Congratulations to {spotw.name}, the super pal of the week!')

# Before Loop : Wait until Sunday at noon.
@super_pal_of_the_week.before_loop
async def before_super_pal_of_the_week():
    # Find amount of time until Sunday at noon. 
    now = datetime.now()
    days_until_sunday = 7 - date.today().isoweekday()
    future = datetime(now.year, now.month, now.day+days_until_sunday, 12, 0)
    # Sleep task until Sunday at noon.
    await asyncio.sleep((future-now).seconds)

# Event: Start loop once bot is ready
@bot.event
async def on_ready():
    super_pal_of_the_week.start()

# Command: Promote users to "Super Pal of the Week"
@bot.command(name='spotw', pass_context=True)
@commands.has_role('super pal of the week')
async def add_super_pal(ctx, member: discord.Member):
    role = discord.utils.get(ctx.guild.roles, name='super pal of the week')
    former_super_pal = ctx.message.author
    if role not in member.roles:
        await member.add_roles(role)
        await former_super_pal.remove_roles(role)
        await channel.send(f'Congratulations {member.name}! You have been promoted to super pal of the week by {former_super_pal.name}.')
        print(f'{member.name}promoted {former_super_pal.name}')

bot.run(TOKEN)
