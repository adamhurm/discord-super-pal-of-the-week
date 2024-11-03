# standard library
import base64, io, json, time

# 3rd-party library
import discord, openai
from discord.ext import commands
from openai import AsyncOpenAI

# super pal library
from . import static as superpal_static
from . import env as superpal_env

# get logger from super pal library
log = superpal_env.log

################
# OpenAI setup #
################
async def is_member_super_pal(bot: commands.Bot, member: str):
    guild = bot.get_guild(superpal_env.GUILD_ID)
    member = discord.utils.get(guild.members, name=member)
    super_pal_role = discord.utils.get(guild.roles, name='Super Pal of the Week')
    if super_pal_role in member.roles:
        return f"Yes, {member} is the super pal."
    else:
        return f"No, {member} is not the super pal."

async def respond_to_user(user_message: discord.Message):
    log.info(f"{user_message.author.name} said \"{user_message.content}\"")
    # Create OpenAI client and assistant.
    client = AsyncOpenAI(api_key=superpal_env.OPENAI_API_KEY)
    try: # Try to get existing assistant.
        assistant = await client.beta.assistants.retrieve(
            assistant_id=superpal_env.GPT_ASSISTANT_ID
        )
    except openai.NotFoundError as e: # Assistant not found. We will create thread.
        log.warn(f"Assistant ID not found. Creating new Assistant.\nError: {e}")
        assistant = await client.beta.assistants.create(
            name="Super Pal Bot",
            instructions=superpal_static.GPT_PROMPT_MSG,
            tools=superpal_static.GPT_ASSISTANT_TOOLS,
            model="gpt-3.5-turbo-1106"
        )
    try: # Try to get existing thread.
        thread = await client.beta.threads.retrieve(
            thread_id=superpal_env.GPT_ASSISTANT_THREAD_ID
        )
    except openai.NotFoundError as e: # Thread not found. We will create thread.
        log.warn(f"Thread ID not found. Creating new Thread.\nError: {e}")
        thread = await client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant.id
        )
    # Create a thread message.
    await client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=user_message.content
    )
    # Create a thread run.
    run = await client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant.id
    )
    # Check if assistant requires action.
    run = await client.beta.threads.runs.retrieve(
        thread_id=thread.id,
        run_id=run.id
    )
    if run.status == 'requires_action':
        # array of available tools for this assistant
        avail_tools = { "is_member_super_pal": is_member_super_pal }

        # retrieve tool function name, arguments, and call id
        tool_fn = avail_tools[run.required_action.
                            submit_tool_outputs.tool_calls[0].
                            function.name]
        tool_args = json.loads(run.required_action.
                            submit_tool_outputs.tool_calls[0].
                            function.arguments)
        tool_call_id = run.required_action.submit_tool_outputs.tool_calls[0].id

        # call tool function and save output
        tool_output = tool_fn(member=dict(tool_args).get('member'))

        # submit tools output
        run = await client.beta.threads.runs.submit_tool_outputs(
                thread_id=thread.id,
                run_id=run.id,
                tool_outputs=[
                    {
                        "tool_call_id": tool_call_id,
                        "output": tool_output,
                    }
                ]
        )
        # Give 1 second for assistant to complete before first attempt.
        time.sleep(1)
        # check if assistant requires action again
        run = await client.beta.threads.runs.retrieve(
            thread_id=thread.id,
            run_id=run.id
        )
    # Retry every second.
    while run.status == 'in_progress' or run.status == 'queued':
        time.sleep(1)
        run = await client.beta.threads.runs.retrieve(
            thread_id=thread.id,
            run_id=run.id
        )
    if run.status == 'completed':
        # get most recent message from thread and post to discord channel
        messages = await client.beta.threads.messages.list(
            thread_id=thread.id
        )
        gpt_assistant_response = messages.data[0].content[0].text.value
        log.info(f"Super Pal Bot said \"{gpt_assistant_response}\"")
        return gpt_assistant_response
    else:
        log.info(f"Run status: {run.status}")

async def generate_surprise_image_and_send(your_text_here: str, channel: discord.TextChannel):
    # Talk to DALL-E 2 AI (beta) for surprise images.
    client = await AsyncOpenAI(api_key=superpal_env.OPENAI_API_KEY)
    try:
        response = await client.images.generate(
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
    except openai.APIError as err:
        log.warn(err)
        if str(err) == 'Your request was rejected as a result of our safety system.':
            await channel.send('Woah there nasty nelly, you asked for something too silly. OpenAI rejected your request due to "Safety". Please try again and be more polite next time.')
        elif str(err) == 'Billing hard limit has been reached':
            await channel.send('Adam is broke and can\'t afford this request.')