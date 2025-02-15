#!/usr/bin/env python3
import asyncio, datetime, random

# 3rd-party library
import discord
from discord import app_commands
from discord.ext import commands, tasks

# super pal library
import superpal.static as superpal_static
import superpal.env as superpal_env
import superpal.ai as superpal_ai

#############
# Bot setup #
#############
intents = discord.Intents.default()
intents.members = True         # Required to list all users in a guild.
intents.message_content = True # Required to use spin-the-wheel and grab winner.
bot = commands.Bot(command_prefix='!', intents=intents)

##################
# Slash commands #
##################
# Command: Promote users to "Super Pal of the Week"
@bot.tree.command(name='superpal')
@app_commands.checks.has_role('Super Pal of the Week')
async def add_super_pal(interaction: discord.Interaction, new_super_pal: discord.Member) -> None:
    """Promote a user to Super Pal of the Week role.
    
    Args:
        new_super_pal (discord.Member): choose the member you want to promote to super pal
    """
    channel = bot.get_channel(superpal_env.CHANNEL_ID)
    role = discord.utils.get(interaction.guild.roles, name='Super Pal of the Week')
    # Promote new user and remove current super pal.
    # NOTE: I have to check for user role because commands.has_role() does not seem to work with app_commands
    if  role not in new_super_pal.roles:
        await new_super_pal.add_roles(role)
        await interaction.user.remove_roles(role)
        print(f'{new_super_pal.name} promoted by {interaction.user.name}.')
        await interaction.response.send_message(f'You have promoted {new_super_pal.mention} to super pal of the week!',
            ephemeral=True)
        await channel.send(f'Congratulations {new_super_pal.mention}! '
            f'You have been promoted to super pal of the week by {interaction.user.name}. {superpal_static.WELCOME_MSG}')
    else:
        await interaction.response.send_message(f'{new_super_pal.mention} is already super pal of the week.',
            ephemeral=True)

# Command: Spin the wheel for a random "Super Pal of the Week"
@bot.tree.command(name='spinthewheel')
@commands.has_role('Super Pal of the Week')
async def spinthewheel():
    """Spin the wheel to randomly choose a new super pal of the week!
    
    """
    guild = bot.get_guild(superpal_env.GUILD_ID)
    channel = bot.get_channel(superpal_env.CHANNEL_ID)

    # Get list of members and filter out bots.
    true_member_list = [m for m in guild.members if not m.bot]
    true_name_list = [member.name for member in true_member_list]
    true_name_str = ", ".join(true_name_list)
    # Send Spin the Wheel command.
    await channel.send(f'?pick {true_name_str}')
    print(f'\nSpinning the wheel for new super pal of the week.')

# Command: Surprise images (AI)
@bot.tree.command(name='surprise')
@app_commands.describe(description='describe the image you want to generate')
async def surprise(interaction: discord.Interaction, description: str) -> None:
    """Generate a surprise image! (backed by OpenAI DALL-E)
    
    Args:
        description (str): describe the image
    """
    print(f'{interaction.user.name} used surprise command:\n\t{description}')
    channel = bot.get_channel(superpal_env.ART_CHANNEL_ID)
    await superpal_ai.generate_surprise_image_and_send(description, channel)
 
###############
# Looped task #
###############
# Weekly Task: Choose "Super Pal of the Week"
@tasks.loop(hours=24*7)
async def super_pal_of_the_week():
    guild = bot.get_guild(superpal_env.GUILD_ID)
    channel = bot.get_channel(superpal_env.CHANNEL_ID)
    role = discord.utils.get(guild.roles, name='Super Pal of the Week')

    # Get list of members and filter out bots. Pick random member.
    true_member_list = [m for m in guild.members if not m.bot]
    spotw = random.choice(true_member_list)
    print(f'\nPicking new super pal of the week.')
    # Add super pal, remove current super pal, avoid duplicates.
    for member in true_member_list:
        if role in member.roles and member == spotw:
            print(f'{member.name} is already super pal of the week. Re-rolling.')
            return await super_pal_of_the_week()
        elif role in member.roles:
            print(f'{member.name} has been removed from super pal of the week role.')
            await member.remove_roles(role)
        elif member == spotw:
            print(f'{member.name} has been added to super pal of the week role.')
            await spotw.add_roles(role)
            await channel.send(f'Congratulations to {spotw.mention}, '
                f'the super pal of the week! {superpal_static.WELCOME_MSG}')

# Before Loop: Wait until Sunday at noon.
@super_pal_of_the_week.before_loop
async def before_super_pal_of_the_week():
    await bot.wait_until_ready()
    # Find amount of time until Sunday at noon.
    now = datetime.datetime.now()
    days_until_sunday = 7 - datetime.date.today().isoweekday()
    # If it's past noon on Sunday, add 7 days to timer.
    if datetime.date.today().isoweekday() == 7 and now.hour > 12:
        days_until_sunday = 7
    time_diff = now + datetime.timedelta(days = days_until_sunday)
    future = datetime.datetime(time_diff.year, time_diff.month, time_diff.day, 12, 0)
    # Sleep task until Sunday at noon.
    print(f'Sleeping for {(future-now)}. Will wake up Sunday at 12PM Eastern Time.')
    await asyncio.sleep((future-now).total_seconds())

##############
# Bot events #
##############
# Event: Suppress error messages for commands that aren't related to Super Pal Bot.
@bot.event
async def on_command_error(error):
    if isinstance(error, commands.errors.CommandNotFound):
        return
    raise error

# Event: Start loop once bot is ready
@bot.event
async def on_ready():
    await bot.tree.sync()
    if not super_pal_of_the_week.is_running():
        super_pal_of_the_week.start()

# Event: Check Spin The Wheel rich message
@bot.event
async def on_message(message: discord.Message):
    guild = bot.get_guild(superpal_env.GUILD_ID)
    spin_the_wheel_role = discord.utils.get(guild.roles, name='Spin The Wheel')
    member = guild.get_member(message.author.id)
    # Reply to messages in Super Pal channel if they aren't commands and they aren't from a bot.
    #if message.channel.id == superpal_env.CHANNEL_ID and message.content[0] != '!' and message.author.bot is False:
    #    gpt_response_msg = await superpal_ai.respond_to_user(message)
    #    await message.channel.send(gpt_response_msg)
    # Only check embedded messages from Spin The Wheel Bot.
    if member is not None and spin_the_wheel_role in member.roles:
        embeds = message.embeds
        for embed in embeds:
            # Wait until message contains Spin the Wheel winner.
            if embed.description is None: continue
            elif embed.description[0] == '🏆':
                super_pal_role = discord.utils.get(guild.roles, name='Super Pal of the Week')
                # Grab winner name from Spin the Wheel message.
                new_super_pal_name = embed.description[12:-2]
                new_super_pal = discord.utils.get(guild.members, name=new_super_pal_name)
                print(f'{new_super_pal.name} was chosen by the wheel spin.')
                # Remove existing Super Pal of the Week
                true_member_list = [m for m in guild.members if not m.bot]
                for member in true_member_list:
                    if super_pal_role in member.roles:
                        await member.remove_roles(super_pal_role)
                # Add new winner to Super Pal of the Week.
                await new_super_pal.add_roles(super_pal_role)
                await message.channel.send(f'Congratulations {new_super_pal.mention}! '
                    f'You have been promoted to super pal of the week by wheel spin. {superpal_static.WELCOME_MSG}')
    # Handle commands if the message was not from Spin the Wheel.
    await bot.process_commands(message)

################
# Bot commands #
################
# Command: Spin the wheel for a random "Super Pal of the Week"
@bot.command(name='spinthewheel', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def spinthewheel(ctx):
    guild = bot.get_guild(superpal_env.GUILD_ID)
    channel = bot.get_channel(superpal_env.CHANNEL_ID)

    role = discord.utils.get(guild.roles, name='Super Pal of the Week')
    # Get list of members and filter out bots.
    true_member_list = [m for m in guild.members if not m.bot]
    true_name_list = [member.name for member in true_member_list]
    true_name_str = ", ".join(true_name_list)
    # Send Spin the Wheel command.
    await channel.send(f'?pick {true_name_str}')
    print(f'\nSpinning the wheel for new super pal of the week.')

# Command: Promote users to "Super Pal of the Week"
@bot.command(name='spotw', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def add_super_pal(ctx, new_super_pal: discord.Member):
    guild = bot.get_guild(superpal_env.GUILD_ID)
    channel = bot.get_channel(superpal_env.CHANNEL_ID)
    role = discord.utils.get(guild.roles, name='Super Pal of the Week')
    current_super_pal = ctx.message.author

    # Promote new user and remove current super pal.
    if role not in new_super_pal.roles:
        print(f'{new_super_pal.name} promoted by {current_super_pal.name}.')
        await new_super_pal.add_roles(role)
        await current_super_pal.remove_roles(role)
        await channel.send(f'Congratulations {new_super_pal.mention}! '
            f'You have been promoted to super pal of the week by {current_super_pal.name}. {superpal_static.WELCOME_MSG}')

# Command: Display more information about commands.
@bot.command(name='commands', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def list_commands(ctx):
    print(f'{ctx.message.author.name} used help command.')
    channel = bot.get_channel(superpal_env.CHANNEL_ID)
    await channel.send(superpal_static.COMMANDS_MSG)

# Command: Send party parrot discord emoji.
@bot.command(name='cacaw', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def cacaw(ctx):
    print(f'{ctx.message.author.name} used cacaw command.')
    channel = bot.get_channel(superpal_env.CHANNEL_ID)
    emoji_guild = bot.get_guild(superpal_env.EMOJI_GUILD_ID)
    partyparrot_emoji = discord.utils.get(emoji_guild.emojis, name='partyparrot')
    await channel.send(str(partyparrot_emoji)*50)

# Command: Randomly remove one user from voice chat
@bot.command(name='karatechop', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def karate_chop(ctx):
    guild = bot.get_guild(superpal_env.GUILD_ID)
    channel = bot.get_channel(superpal_env.CHANNEL_ID)
    current_super_pal = ctx.message.author 

    active_members = [voice_channel.members for voice_channel in guild.voice_channels]

    # Kick random user from voice channel.
    if not any(active_members):
        print(f'{current_super_pal.name} used karate chop, but no one is in the voice channels.')
        await channel.send(f'There is no one to karate chop, {current_super_pal.mention}!')
    else:
        print(f'{chopped_member.name} karate chopped')
        # Flatten user list, filter out bots, and choose random user
        flatten = lambda l: [x for y in l for x in y]
        true_member_list = [m for m in flatten(active_members) if not m.bot]
        chopped_member = random.choice(true_member_list)

        # Check that an 'AFK' channel exists and choose the first one we see
        afk_channels = [c.name for c in guild.voice_channels if 'AFK' in c.name]
        if any(afk_channels):
            await chopped_member.move_to(guild.voice_channels[afk_channels[0]])
            await channel.send(f'karate chopped {chopped_member.mention}!')
        else:
            await channel.send(f'{chopped_member.mention} would have been chopped, but an AFK channel was not found.\n'
                               f'Please complain to the server owner.')

# Command: Send party cat discord emoji
@bot.command(name='meow', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def meow(ctx):
    print(f'{ctx.message.author.name} used meow command.')
    channel = bot.get_channel(superpal_env.CHANNEL_ID)
    emoji_guild = bot.get_guild(superpal_env.EMOJI_GUILD_ID)
    partymeow_emoji = discord.utils.get(emoji_guild.emojis, name='partymeow')
    await channel.send(str(partymeow_emoji)*50)

# Command: Surprise images (AI)
@bot.command(name='surprise', pass_context=True)
#@commands.has_role('Super Pal of the Week')
async def surprise(ctx):
    print(f'{ctx.message.author.name} used surprise command:\n\t{ctx.message.content}')
    channel = bot.get_channel(superpal_env.ART_CHANNEL_ID)
    your_text_here = ctx.message.content.removeprefix('!surprise ')
    await superpal_ai.generate_surprise_image_and_send(your_text_here, channel)

bot.run(superpal_env.TOKEN)
