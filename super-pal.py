#!/usr/bin/env python3
import asyncio, base64, io, logging, os, random
import discord, openai
from datetime import date, datetime, timedelta
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

################
# Env. variables
################

load_dotenv()
TOKEN = os.getenv('SUPERPAL_TOKEN')
GUILD_ID = int(os.getenv('GUILD_ID'))
EMOJI_GUILD_ID = int(os.getenv('EMOJI_GUILD_ID'))
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
ART_CHANNEL_ID = int(os.getenv('ART_CHANNEL_ID'))
openai.api_key = os.getenv("OPENAI_API_KEY")
VOICE_CHANNELS = (os.getenv("VOICE_CHANNELS")).encode('utf-8').decode('unicode-escape')


#########
# Logging
#########

log = logging.getLogger('super-pal')
log.setLevel(logging.INFO)
log_handler = logging.FileHandler(filename='discord-super-pal.log', encoding='utf-8', mode='w')
dt_fmt = '%Y-%m-%d %H:%M:%S'
formatter = logging.Formatter('[{asctime}] [{levelname:<8}] {name}: {message}', dt_fmt, style='{')
log_handler.setFormatter(formatter)
log.addHandler(log_handler)


#################
# Message strings
#################

COMMANDS_MSG = (f'**!spotw @name**\n\tPromote another user to super pal of the week. Be sure to @mention the user.\n'
    f'**!spinthewheel**\n\tSpin the wheel to choose a new super pal of the week.'
    f'**!cacaw**\n\tSpam the channel with party parrots.\n'
    f'**!meow**\n\tSpam the channel with party cats.\n'
    f'**!surprise** your text here\n\tReceive an AI-generated image in the channel based on the text prompt you provide.\n'
    f'**!unsurprise**\n\tReceive a surprise image in the channel.\n'
    f'**!karatechop**\n\tMove a random user to AFK voice channel.' )

GAMBLE_MSG = ( f'Respond to the two polly polls to participate in Super Pal of the Week Gambling™.\n'
    f'- Choose your challenger\n'
    f'- Make your wager\n\n'
    f'You will be given 100 points weekly so feel free to go all-in.\n\n'
    f'*The National Problem Gambling Helpline (1-800-522-4700) is available 24/7 and is 100% confidential.*' )

WELCOME_MSG = ( f'Welcome to the super pal channel.\n\n'
    f'Use super pal commands by posting commands in chat. Examples:\n'
    f'( !commands (for full list) | !surprise your text here | !karatechop | !spotw @name | !meow )' )


###########
# Bot setup
###########

intents = discord.Intents.default()
# Required to list all users in a guild.
intents.members = True
# Required to use spin-the-wheel and grab winner.
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)


################
# Slash commands
################

'''
# Command: Bet on who will be the next "Super Pal of the Week"
@bot.tree.command(name='bet')
@commands.describe(pal='the pal you want to bet on', amount='the amount of points you want to bet')
async def bet_on_super_pal(interaction: discord.Interaction, pal: discord.Member, amount: int) -> None:
    user_already_bet = 0 #fetch this dynamically from local file
    if user_already_bet:
        return await interaction.response.send_message(f'Hi {interaction.user.mention}, you have already placed your bet for this week.',
                                                ephemeral=True)
    await interaction.response.send_message(f'Hi {interaction.user.mention}, you have bet {amount} points that {pal.name} will be Super Pal.', 
                                            ephemeral=True)
'''

# Command: Promote users to "Super Pal of the Week"
@bot.tree.command(name='superpal')
@app_commands.describe(new_super_pal='the member you want to promote to super pal')
@app_commands.checks.has_role('Super Pal of the Week')
async def add_super_pal(interaction: discord.Interaction, new_super_pal: discord.Member) -> None:
    channel = bot.get_channel(CHANNEL_ID)
    role = discord.utils.get(interaction.guild.roles, name='Super Pal of the Week')
    # Promote new user and remove current super pal.
    # NOTE: I have to check for user role because commands.has_role() does not seem to work with app_commands
    if  role not in new_super_pal.roles:
        await new_super_pal.add_roles(role)
        await interaction.user.remove_roles(role)
        log.info(f'{new_super_pal.name} promoted by {interaction.user.name}.')
        await interaction.response.send_message(f'You have promoted {new_super_pal.mention} to super pal of the week!',
            ephemeral=True)
        await channel.send(f'Congratulations {new_super_pal.mention}! '
            f'You have been promoted to super pal of the week by {interaction.user.name}. {WELCOME_MSG}')
    else:
        await interaction.response.send_message(f'{new_super_pal.mention} is already super pal of the week.',
            ephemeral=True)
            

'''
# Command: Surprise images (AI)
@bot.tree.command(name='surprise')
@app_commands.describe(text_prompt='text prompt for DALL-E AI image generator')
@app_commands.checks.has_role('Super Pal of the Week')
async def surprise(interaction: discord.Interaction, text_prompt: str):
    channel = bot.get_channel(ART_CHANNEL_ID)
    log.info(f'{interaction.user.name} used surprise command.')
    log.info(interaction.message.content)
    # Talk to DALL-E 2 AI (beta) for surprise images.
    try:
        response = openai.Image.create(
            prompt=text_prompt,
            n=4,
            response_format="b64_json",
            size="1024x1024"
        )
        if response['data']:
            await channel.send(files=[discord.File(io.BytesIO(base64.b64decode(img['b64_json'])),
                            filename='{random.randrange(1000)}.jpg') for img in response['data']])
        else:
            await channel.send('Failed to create surprise image. Everyone boo Adam.')
    except openai.error.InvalidRequestError as err:
        if str(err) == 'Your request was rejected as a result of our safety system.':
            await channel.send('Woah there nasty nelly, you asked for something too fucking silly. OpenAI rejected your request due to "Safety". Please try again and be more polite next time.')
        elif str(err) == 'Billing hard limit has been reached':
            await channel.send('Adam is broke and can\'t afford this request.')
'''

            
#############
# Looped task
#############

# Weekly Task: Choose "Super Pal of the Week"
@tasks.loop(hours=24*7)
async def super_pal_of_the_week():
    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(CHANNEL_ID)
    role = discord.utils.get(guild.roles, name='Super Pal of the Week')
    
    # Get list of members and filter out bots. Pick random member.
    true_member_list = [m for m in guild.members if not m.bot]
    spotw = random.choice(true_member_list)
    log.info(f'\nPicking new super pal of the week.')
    # Add super pal, remove current super pal, avoid duplicates.
    for member in true_member_list:
        if role in member.roles and member == spotw:
            log.info(f'{member.name} is already super pal of the week. Re-rolling.')
            return await super_pal_of_the_week()
        elif role in member.roles:
            log.info(f'{member.name} has been removed from super pal of the week role.')
            await member.remove_roles(role)
        elif member == spotw:
            log.info(f'{member.name} has been added to super pal of the week role.')
            await spotw.add_roles(role)
            await channel.send(f'Congratulations to {spotw.mention}, '
                f'the super pal of the week! {WELCOME_MSG}')

# Before Loop: Wait until Sunday at noon.
@super_pal_of_the_week.before_loop
async def before_super_pal_of_the_week():
    await bot.wait_until_ready()
    # Find amount of time until Sunday at noon.
    now = datetime.now()
    days_until_sunday = 7 - date.today().isoweekday()
    # If it's past noon on Sunday, add 7 days to timer.
    if date.today().isoweekday() == 7 and now.hour > 12:
        days_until_sunday = 7
    time_diff = now + timedelta(days = days_until_sunday)
    future = datetime(time_diff.year, time_diff.month, time_diff.day, 12, 0)
    # Sleep task until Sunday at noon.
    log.info(f'Sleeping for {(future-now)}. Will wake up Sunday at 12PM Eastern Time.')
    await asyncio.sleep((future-now).total_seconds())


############
# Bot events
############

# Event: Avoid printing errors message for commands that aren't related to Super Pal Bot.
@bot.event
async def on_command_error(ctx, error):
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
async def on_message(message):
    guild = bot.get_guild(GUILD_ID)
    spin_the_wheel_role = discord.utils.get(guild.roles, name='Spin The Wheel')
    # Only check embedded messages from Spin The Wheel Bot.
    if spin_the_wheel_role in message.author.roles:
        embeds = message.embeds
        for embed in embeds:
            # Wait until message contains Spin the Wheel winner.
            if embed.description is None: continue
            elif embed.description[0] == '🏆':
                super_pal_role = discord.utils.get(guild.roles, name='Super Pal of the Week')
                # Grab winner name from Spin the Wheel message.
                new_super_pal_name = embed.description[12:-2]
                new_super_pal = discord.utils.get(guild.members, name=new_super_pal_name)
                # Add new winner to Super Pal of the Week.
                log.info(f'{new_super_pal.name} was chosen by the wheel spin.')
                await new_super_pal.add_roles(super_pal_role)
                await message.channel.send(f'Congratulations {new_super_pal.mention}! '
                    f'You have been promoted to super pal of the week by wheel spin. {WELCOME_MSG}')
    # Handle commands if the message was not from Spin the Wheel.
    await bot.process_commands(message)


##############
# Bot commands
##############

# Command: Spin the wheel for a random "Super Pal of the Week"
@bot.command(name='spinthewheel', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def spinthewheel(ctx):
    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(CHANNEL_ID)
    role = discord.utils.get(guild.roles, name='Super Pal of the Week')
    current_super_pal = ctx.message.author

    # Get list of members and filter out bots.
    true_member_list = [m for m in guild.members if not m.bot]
    true_name_list = [member.name for member in true_member_list]
    true_name_str = ", ".join(true_name_list)
    # Send Spin the Wheel command.
    await channel.send(f'?pick {true_name_str}')
    log.info(f'\nSpinning the wheel for new super pal of the week.')
    # Remove current super pal.
    await current_super_pal.remove_roles(role)

# Command: Promote users to "Super Pal of the Week"
@bot.command(name='spotw', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def add_super_pal(ctx, new_super_pal: discord.Member):
    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(CHANNEL_ID)
    role = discord.utils.get(guild.roles, name='Super Pal of the Week')
    current_super_pal = ctx.message.author

    # Promote new user and remove current super pal.
    if role not in new_super_pal.roles:
        await new_super_pal.add_roles(role)
        await current_super_pal.remove_roles(role)
        log.info(f'{new_super_pal.name} promoted by {current_super_pal.name}.')
        await channel.send(f'Congratulations {new_super_pal.mention}! '
            f'You have been promoted to super pal of the week by {current_super_pal.name}. {WELCOME_MSG}')

# Command: Display more information about commands.
@bot.command(name='commands', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def list_commands(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    current_super_pal = ctx.message.author

    log.info(f'{current_super_pal.name} used help command.')
    await channel.send(COMMANDS_MSG)

# Command: Send party parrot discord emoji.
@bot.command(name='cacaw', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def cacaw(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    emoji_guild = bot.get_guild(EMOJI_GUILD_ID)
    partyparrot_emoji = discord.utils.get(emoji_guild.emojis, name='partyparrot')
    current_super_pal = ctx.message.author

    log.info(f'{current_super_pal.name} used cacaw command.')
    await channel.send(str(partyparrot_emoji)*50)

# Command: Get more info about gambling.
@bot.command(name="gamble", pass_context=True)
async def gamble(ctx):
    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(CHANNEL_ID)

    await channel.send(GAMBLE_MSG)
    true_member_list = [m for m in guild.members if not m.bot]
    true_name_list = [member.name for member in true_member_list]
    true_name_str = ", ".join(true_name_list)
    await channel.send(f'/poll {true_name_str}')

# Command: Randomly remove one user from voice chat
@bot.command(name='karatechop', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def karate_chop(ctx):
    guild = bot.get_guild(GUILD_ID)
    channel = bot.get_channel(CHANNEL_ID)
    current_super_pal = ctx.message.author

    # Grab voice channels from env file values.
    voice_channels = [
        discord.utils.get(guild.voice_channels, name=voice_channel, type=discord.ChannelType.voice)
        for voice_channel in VOICE_CHANNELS
    ]
    # Kick random user from voice channel.
    if not any(x.members for x in voice_channels):
        log.info(f'{current_super_pal.name} used karate chop, but no one is in the voice channels.')
        await channel.send(f'There is no one to karate chop, {current_super_pal.mention}!')
    else:
        # Grab the first channel with users in it.
        voice_channel = None
        for channel in voice_channels:
            if channel.members:
                voice_channel = channel
                break

        true_member_list = [m for m in voice_channel.members if not m.bot]
        chopped_member = random.choice(true_member_list)
        log.info(f'{chopped_member.name} karate chopped by {current_super_pal.name}.')
        await chopped_member.move_to(voice_channels[3])
        await channel.send(f'{current_super_pal.mention} karate chopped {chopped_member.mention}!')

# Command: Send party cat discord emoji
@bot.command(name='meow', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def meow(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    emoji_guild = bot.get_guild(EMOJI_GUILD_ID)
    partymeow_emoji = discord.utils.get(emoji_guild.emojis, name='partymeow')
    current_super_pal = ctx.message.author

    log.info(f'{current_super_pal.name} used meow command.')
    await channel.send(str(partymeow_emoji)*50)

# Command: Surprise images (AI)
@bot.command(name='surprise', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def surprise(ctx):
    channel = bot.get_channel(ART_CHANNEL_ID)
    current_super_pal = ctx.message.author

    log.info(f'{current_super_pal.name} used surprise command:\n\t{ctx.message.content}')
    # Talk to DALL-E 2 AI (beta) for surprise images.
    your_text_here = ctx.message.content.removeprefix('!surprise ')
    try:
        response = openai.Image.create(
            prompt=your_text_here,
            n=4,
            response_format="b64_json",
            size="1024x1024"
        )
        if response['data']:
            await channel.send(files=[discord.File(io.BytesIO(base64.b64decode(img['b64_json'])),
                            filename='{random.randrange(1000)}.jpg') for img in response['data']])
        else:
            await channel.send('Failed to create surprise image. Everyone boo Adam.')
    except openai.error.InvalidRequestError as err:
        if str(err) == 'Your request was rejected as a result of our safety system.':
            await channel.send('Woah there nasty nelly, you asked for something too fucking silly. OpenAI rejected your request due to "Safety". Please try again and be more polite next time.')
        elif str(err) == 'Billing hard limit has been reached':
            await channel.send('Adam is broke and can\'t afford this request.')

# Command: Old "surprise" images (predetermined)
@bot.command(name='unsurprise', pass_context=True)
@commands.has_role('Super Pal of the Week')
async def unsurprise(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    current_super_pal = ctx.message.author

    # Grab random image from assets folder and send message.
    log.info(f'{current_super_pal.name} used unsurprise command.')
    image_types = ["bucket", "nails", "mantis"]
    random_image_type = image_types[random.randrange(0,3)]
    random_path = "/home/discord-super-pal-of-the-week/assets/surprise_images/" \
                      + random_image_type + str(random.randrange(0,10)) + ".jpg"
    await channel.send(file=discord.File(random_path))


bot.run(TOKEN, log_handler=log_handler)