#!/usr/bin/env python3
from http.client import ResponseNotReady
import os
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
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
    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(CHANNEL_ID)
    role = discord.utils.get(guild.roles, name='super pal of the week')
    # Get list of members and filter out bots.
    true_member_list = [m for m in guild.members if not m.bot]
    # Choose random "Super Pal of the Week" from list.
    spotw = true_member_list[randrange(len(true_member_list))]

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

# Event: Wait until bot is ready to fetch members
@bot.event
async def on_ready():
    super_pal_of_the_week.start()

# Command: Promote Users to "Super Pal of the Week"
@bot.command(name='spotw', pass_context=True)
@commands.has_role('super pal of the week')
async def add_super_pal(ctx, member: discord.Member):
    role = discord.utils.get(ctx.guild.roles, name='super pal of the week')
    if role not in member.roles:
        await member.add_roles(role)

bot.run(TOKEN)
