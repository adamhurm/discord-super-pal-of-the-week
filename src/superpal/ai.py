"""AI integration module for Super Pal Bot.

This module handles all OpenAI API integrations including:
- GPT Assistant for conversational responses
- DALL-E image generation
"""

import base64
import io
import json
import time
from typing import Optional

import discord
import openai
from discord.ext import commands
from openai import AsyncOpenAI

from . import static as superpal_static
from . import env as superpal_env

# Get logger from super pal library
log = superpal_env.log


async def is_member_super_pal(bot: commands.Bot, member: str) -> str:
    """Check if a member currently has the Super Pal of the Week role.

    Args:
        bot: The Discord bot instance
        member: Member name to check

    Returns:
        String indicating whether member is super pal
    """
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID)
        if not guild:
            log.error(f"Could not find guild with ID {superpal_env.GUILD_ID}")
            return "Error: Could not find guild"

        member_obj = discord.utils.get(guild.members, name=member)
        if not member_obj:
            return f"Error: Member '{member}' not found"

        super_pal_role = discord.utils.get(guild.roles, name=superpal_static.SUPER_PAL_ROLE_NAME)
        if not super_pal_role:
            log.error("Super Pal role not found in guild")
            return "Error: Super Pal role not configured"

        if super_pal_role in member_obj.roles:
            return f"Yes, {member} is the super pal."
        else:
            return f"No, {member} is not the super pal."
    except Exception as e:
        log.error(f"Error checking super pal status: {e}")
        return "Error checking super pal status"

async def respond_to_user(user_message: discord.Message) -> Optional[str]:
    """Generate a response to a user message using OpenAI GPT Assistant.

    Args:
        user_message: The Discord message to respond to

    Returns:
        GPT assistant response text, or None if error occurs
    """
    log.info(f"{user_message.author.name} said \"{user_message.content}\"")

    if not superpal_env.OPENAI_API_KEY:
        log.error("OpenAI API key not configured")
        return "Sorry, AI features are not configured."

    try:
        client = AsyncOpenAI(api_key=superpal_env.OPENAI_API_KEY)

        # Try to get existing assistant or create new one
        try:
            assistant = await client.beta.assistants.retrieve(
                assistant_id=superpal_env.GPT_ASSISTANT_ID
            )
        except openai.NotFoundError as e:
            log.warning(f"Assistant ID not found. Creating new Assistant.\nError: {e}")
            assistant = await client.beta.assistants.create(
                name="Super Pal Bot",
                instructions=superpal_static.GPT_PROMPT_MSG,
                tools=superpal_static.GPT_ASSISTANT_TOOLS,
                model="gpt-3.5-turbo-1106"
            )

        # Try to get existing thread or create new one
        try:
            thread = await client.beta.threads.retrieve(
                thread_id=superpal_env.GPT_ASSISTANT_THREAD_ID
            )
        except openai.NotFoundError as e:
            log.warning(f"Thread ID not found. Creating new Thread.\nError: {e}")
            thread = await client.beta.threads.create()

        # Create a thread message
        await client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=user_message.content
        )

        # Create a thread run
        run = await client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant.id
        )

        # Wait for run to complete and handle tool calls
        max_retries = 60  # 60 second timeout
        retry_count = 0

        while retry_count < max_retries:
            run = await client.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run.id
            )

            if run.status == 'completed':
                break
            elif run.status == 'requires_action':
                # Handle tool calls
                tool_outputs = []
                for tool_call in run.required_action.submit_tool_outputs.tool_calls:
                    if tool_call.function.name == 'is_member_super_pal':
                        args = json.loads(tool_call.function.arguments)
                        member_name = args.get('member')
                        output = await is_member_super_pal(user_message.channel.guild, member_name)
                        tool_outputs.append({
                            "tool_call_id": tool_call.id,
                            "output": output
                        })

                # Submit tool outputs
                run = await client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread.id,
                    run_id=run.id,
                    tool_outputs=tool_outputs
                )
            elif run.status in ['failed', 'cancelled', 'expired']:
                log.error(f"Run failed with status: {run.status}")
                return None

            time.sleep(1)
            retry_count += 1

        if run.status == 'completed':
            # Get most recent message from thread
            messages = await client.beta.threads.messages.list(thread_id=thread.id)
            gpt_assistant_response = messages.data[0].content[0].text.value
            log.info(f"Super Pal Bot said \"{gpt_assistant_response}\"")
            return gpt_assistant_response
        else:
            log.error(f"Run timed out with status: {run.status}")
            return None

    except Exception as e:
        log.error(f"Error in respond_to_user: {e}")
        return None

async def generate_surprise_image_and_send(your_text_here: str, channel: discord.TextChannel) -> None:
    """Generate surprise images using DALL-E and send to channel.

    Args:
        your_text_here: Text prompt for image generation
        channel: Discord channel to send images to
    """
    if not superpal_env.OPENAI_API_KEY:
        await channel.send('Sorry, AI image generation is not configured.')
        return

    if not your_text_here or your_text_here.strip() == '':
        await channel.send('Please provide a description for the image!')
        return

    log.info(f"Generating surprise image with prompt: {your_text_here}")

    try:
        client = AsyncOpenAI(api_key=superpal_env.OPENAI_API_KEY)

        response = await client.images.generate(
            prompt=your_text_here,
            n=superpal_static.IMAGE_GENERATION_COUNT,
            response_format="b64_json",
            size=superpal_static.IMAGE_SIZE
        )

        if response.data:
            files = []
            for idx, img in enumerate(response.data):
                image_data = base64.b64decode(img.b64_json)
                files.append(
                    discord.File(io.BytesIO(image_data), filename=f'surprise_{idx}.jpg')
                )
            await channel.send(files=files)
            log.info(f"Successfully sent {len(files)} generated images")
        else:
            await channel.send('Failed to create surprise image. Everyone boo Adam.')
            log.error("No image data returned from OpenAI")

    except openai.APIError as err:
        log.warning(f"OpenAI API Error: {err}")
        error_msg = str(err)

        if 'safety system' in error_msg.lower():
            await channel.send(
                'Woah there nasty nelly, you asked for something too silly. '
                'OpenAI rejected your request due to "Safety". '
                'Please try again and be more polite next time.'
            )
        elif 'billing hard limit' in error_msg.lower():
            await channel.send("Adam is broke and can't afford this request.")
        else:
            await channel.send(f'Sorry, there was an error generating the image: {error_msg}')

    except Exception as e:
        log.error(f"Unexpected error in generate_surprise_image_and_send: {e}")
        await channel.send('An unexpected error occurred. Please try again later.')