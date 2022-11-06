#!/usr/bin/env python3
import asyncio, base64, discord, io, os, random, requests
from datetime import date, datetime, timedelta
from discord.ext import commands, tasks
from dotenv import load_dotenv
from http.client import ResponseNotReady

# Load environmental variables.
load_dotenv()
TOKEN = os.getenv('SUPERPAL_TOKEN')
GUILD_ID = int(os.getenv('GUILD_ID'))
EMOJI_GUILD_ID = int(os.getenv('EMOJI_GUILD_ID'))
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv('ANNOUNCEMENTS_CHANNEL_ID'))

# Define text strings for re-use.
WELCOME_MSG = ( f'Welcome to the super pal channel.\n\n'
                f'Use super pal commands by posting commands in chat. Examples:\n'
                f'( !commands (for full list) | !spotw @name | !karatechop | !meow )' )

# Required to list all users in a guild.
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Weekly Task: Choose "Super Pal of the Week"
@tasks.loop(hours=24*7)
async def super_pal_of_the_week():
    # Get IDs
    await bot.wait_until_ready()
    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(CHANNEL_ID)
    announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    role = discord.utils.get(guild.roles, name='super pal of the week')
    # Get list of members and filter out bots.
    true_member_list = [m for m in guild.members if not m.bot]
    # Choose random "Super Pal of the Week" from list.
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
            await spotw.add_roles(role)
            print(f'{member.name} has been added to super pal of the week role.')
            await announcements_channel.send(f'Congratulations to {spotw.mention}, the super pal of the week!')
            await channel.send(f'Congratulations {spotw.mention}! {WELCOME_MSG}')

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

# Event: Check Spin The Wheel rich message
@bot.event
async def on_message(message):
    embeds = message.embeds
    for embed in embeds:
        # Wait until message contains Spin the Wheel winner.
        if embed.description[0] == '🏆':
            # Get IDs.
            await bot.wait_until_ready()
            guild = bot.get_guild(GUILD_ID)
            channel = bot.get_channel(CHANNEL_ID)
            announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
            role = discord.utils.get(guild.roles, name='super pal of the week')
            # Grab winner name from Spin the Wheel message.
            winner = embed.description[12:-2]
            new_super_pal = discord.utils.get(guild.members, name=winner)
            # Add new winner to Super Pal of the Week.
            print(f'{new_super_pal.name} was chosen by the wheel spin.')
            await new_super_pal.add_roles(role)
            await announcements_channel.send(f'Congratulations {new_super_pal.mention}, '
                            f'you have been promoted to super pal of the week by wheel spin.')
            await channel.send(f'Congratulations {spotw.mention}! {WELCOME_MSG}')
    # Handle commands if the message was not from Spin the Wheel.
    await bot.process_commands(message)

# Command: Spin the whell for a random "Super Pal of the Week"
@bot.command(name='spinthewheel', pass_context=True)
@commands.has_role('super pal of the week')
async def spinthewheel(ctx):
    # Get IDs.
    await bot.wait_until_ready()
    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(CHANNEL_ID)
    role = discord.utils.get(guild.roles, name='super pal of the week')
    current_super_pal = ctx.message.author
    # Get list of members and filter out bots.
    true_member_list = [m for m in guild.members if not m.bot]
    true_name_list = [member.name for member in true_member_list]
    true_name_str = ", ".join(true_name_list)
    # Send Spin the Wheel command.
    await channel.send(f'!pick {true_name_str}')
    print(f'\nSpinning the wheel for new super pal of the week.')
    # Remove current super pal.
    await current_super_pal.remove_roles(role)

# Command: Promote users to "Super Pal of the Week"
@bot.command(name='spotw', pass_context=True)
@commands.has_role('super pal of the week')
async def add_super_pal(ctx, new_super_pal: discord.Member):
    # Get IDs.
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    announcements_channel = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    role = discord.utils.get(ctx.guild.roles, name='super pal of the week')
    current_super_pal = ctx.message.author
    # Promote new user and remove current super pal.
    if role not in new_super_pal.roles:
        await new_super_pal.add_roles(role)
        await current_super_pal.remove_roles(role)
        print(f'{new_super_pal.name} promoted by {current_super_pal.name}')
        await announcements_channel.send(f'Congratulations {new_super_pal.mention}, '
                            f'you have been promoted to super pal of the week by {current_super_pal.name}.')
        await channel.send(f'Congratulations {spotw.mention}! {WELCOME_MSG}')

# Command: Display more information about commands.
@bot.command(name='commands', pass_context=True)
@commands.has_role('super pal of the week')
async def list_commands(ctx):
    # Get IDs.
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    current_super_pal = ctx.message.author
    # Print help message.
    print(f'{current_super_pal.name} used help command.')
    msg = f"""**!spotw @name**\n\tPromote another user to super pal of the week. Be sure to @mention the user.
**!spinthewheel**\n\tSpin the wheel to choose a new super pal of the week.
**!cacaw**\n\tSpam the channel with party parrots.
**!meow**\n\tSpam the channel with party cats.
**!surprise** your text here\n\tReceive a surprise image in the channel based on the text you provide.
**!unsurprise**\n\tReceive a surprise image in the channel.
**!karatechop**\n\tMove a random user to AFK voice channel.
"""
    await channel.send(msg)

# Command: Send party parrot discord emoji
@bot.command(name='cacaw', pass_context=True)
@commands.has_role('super pal of the week')
async def cacaw(ctx):
    # Get IDs.
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    emoji_guild = bot.get_guild(EMOJI_GUILD_ID)
    partyparrot = discord.utils.get(emoji_guild.emojis, name='partyparrot')
    current_super_pal = ctx.message.author
    # Send message.
    print(f'{current_super_pal.name} used cacaw command.')
    await channel.send(str(partyparrot)*50)

# Command: Randomly remove one user from voice chat
@bot.command(name='karatechop', pass_context=True)
@commands.has_role('super pal of the week')
async def karate_chop(ctx):
    # Get IDs.
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    current_super_pal = ctx.message.author
    # Assume "General" voice channel exists.
    voice_channels = [
        discord.utils.get(ctx.message.guild.voice_channels, name="\U0001F50A | General", type=discord.ChannelType.voice),
        discord.utils.get(ctx.message.guild.voice_channels, name="Classified", type=discord.ChannelType.voice),
        discord.utils.get(ctx.message.guild.voice_channels, name="\U0001F3AE | Games", type=discord.ChannelType.voice),
        discord.utils.get(ctx.message.guild.voice_channels, name="\U0001F464 | AFK", type=discord.ChannelType.voice)
    ]
    # Kick random user from voice channel.
    if not any(voice_channels[x].members for x in voice_channels):
        print(f'{current_super_pal.name} used karate chop, but no one is in the voice channels')
        await channel.send(f'There is no one to karate chop, {current_super_pal.mention}!')
    else:
        # Prioritize Classified channel for karate chop
        voice_channel = None
        for channel in voice_channels:
            if channel.members:
                voice_channel = channel
                break

        true_member_list = [m for m in voice_channel.members if not m.bot]
        chopped_member = random.choice(true_member_list)
        await chopped_member.move_to(voice_channels[3])
        print(f'{chopped_member.name} karate chopped by {current_super_pal.name}')
        await channel.send(f'{current_super_pal.mention} karate chopped {chopped_member.mention}!')

# Command: Send party cat discord emoji
@bot.command(name='meow', pass_context=True)
@commands.has_role('super pal of the week')
async def meow(ctx):
    # Get IDs.
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    emoji_guild = bot.get_guild(EMOJI_GUILD_ID)
    partymeow = discord.utils.get(emoji_guild.emojis, name='partymeow')
    current_super_pal = ctx.message.author
    # Send message.
    print(f'{current_super_pal.name} used meow command.')
    await channel.send(str(partymeow)*50)

# Command : Surprise images (AI)
@bot.command(name='surprise', pass_context=True)
async def surprise(ctx):
    # Get images from DALLE backend
    def getDALLE(message):
        r = requests.post('http://localhost:8080/dalle',
                         json={'text':message,'num_images':4})
        if r.status_code != 200:
            return None
        images = r.json().get('generatedImgs')
        return images

    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    current_super_pal = ctx.message.author
    print(f'{current_super_pal.name} used surprise command.')
    print(ctx.message.content)
    # Talk to local DALL-E AI for surprise images
    your_text_here = ctx.message.content.removeprefix('!surprise ')
    files = getDALLE(your_text_here)
    if files:
        await channel.send(files=[discord.File(io.BytesIO(base64.b64decode(f)),
                            filename='{random.randrange(1000)}.jpg') for f in files])
    else:
        await channel.send('Failed to create surprise image. Everyone boo Adam.')

# Command: Unsurprise images
@bot.command(name='unsurprise', pass_context=True)
@commands.has_role('super pal of the week')
async def unsurprise(ctx):
    # Get IDs.
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    current_super_pal = ctx.message.author
    # Grab random image from assets folder and send message.
    print(f'{current_super_pal.name} used unsurprise command.')
    image_types = ["bucket", "nails", "mantis"]
    random_image_type = image_types[random.randrange(0,3)]
    random_path = "/home/discord-super-pal-of-the-week/assets/surprise_images/" \
                      + random_image_type + str(random.randrange(0,10)) + ".jpg"
    await channel.send(file=discord.File(random_path))

bot.run(TOKEN)
