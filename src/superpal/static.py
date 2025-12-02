"""Static content and constant messages for Super Pal Bot.

This module contains all static messages, prompts, and configuration
used throughout the Discord bot.
"""

###################
# Message strings #
###################
COMMANDS_MSG = (
    '**!spotw @name**\n\tPromote another user to super pal of the week. Be sure to @mention the user.\n'
    '**!spinthewheel**\n\tSpin the wheel to choose a new super pal of the week.'
    '**!cacaw**\n\tSpam the channel with party parrots.\n'
    '**!meow**\n\tSpam the channel with party cats.\n'
    '**!karatechop**\n\tMove a random user to AFK voice channel.'
)

GAMBLE_MSG = (
    'Respond to the two polly polls to participate in Super Pal of the Week Gambling™.\n'
    '- Choose your challenger\n'
    '- Make your wager\n\n'
    'You will be given 100 points weekly so feel free to go all-in.\n\n'
    '*The National Problem Gambling Helpline (1-800-522-4700) is available 24/7 and is 100% confidential.*'
)

WELCOME_MSG = (
    'Welcome to the super pal channel.\n\n'
    'Use super pal commands by posting commands in chat. Examples:\n'
    '( !commands (for full list) | !karatechop | !spotw @name | !meow )'
)

RUNTIME_WARN_MSG = 'WARN: Super Pal will still run but you are very likely to encounter run-time errors.'

######################
# GPT static content #
######################
GPT_PROMPT_MSG = (
    'You are a helpful assistant named Super Pal Bot. '
    'You help the members of a small Discord community called Bringus. '
    'Each week a new super pal is chosen at random from the list of Bringus members.'
)

GPT_ASSISTANT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "is_member_super_pal",
            "description": "Check if the given member is currently super pal",
            "parameters": {
                "type": "object",
                "properties": {
                    "member": {
                        "type": "string",
                        "description": "The member name, e.g. clippy",
                    },
                },
                "required": ["member"],
            }
        }
    }
]

# Role and channel names
SUPER_PAL_ROLE_NAME = 'Super Pal of the Week'
SPIN_THE_WHEEL_ROLE_NAME = 'Spin The Wheel'
AFK_CHANNEL_KEYWORD = 'AFK'

# Bot configuration
EMOJI_SPAM_COUNT = 50
