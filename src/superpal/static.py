###################
# Message strings #
###################
COMMANDS_MSG = (f'**!spotw @name**\n\tPromote another user to super pal of the week. Be sure to @mention the user.\n'
    f'**!spinthewheel**\n\tSpin the wheel to choose a new super pal of the week.'
    f'**!cacaw**\n\tSpam the channel with party parrots.\n'
    f'**!meow**\n\tSpam the channel with party cats.\n'
    f'**!surprise** your text here\n\tReceive an AI-generated image in the channel based on the text prompt you provide.\n'
    f'**!karatechop**\n\tMove a random user to AFK voice channel.' )
GAMBLE_MSG = ( f'Respond to the two polly polls to participate in Super Pal of the Week Gambling™.\n'
    f'- Choose your challenger\n'
    f'- Make your wager\n\n'
    f'You will be given 100 points weekly so feel free to go all-in.\n\n'
    f'*The National Problem Gambling Helpline (1-800-522-4700) is available 24/7 and is 100% confidential.*' )
WELCOME_MSG = ( f'Welcome to the super pal channel.\n\n'
                f'Use super pal commands by posting commands in chat. Examples:\n'
                f'( !commands (for full list) | !surprise your text here | !karatechop | !spotw @name | !meow )' )
RUNTIME_WARN_MSG = 'WARN: Super Pal will still run but you are very likely to encounter run-time errors.'

######################
# GPT static content #
######################
GPT_PROMPT_MSG = ( f'You are a helpful assistant named Super Pal Bot. '
                    f'You help the members of a small Discord community called Bringus. '
                    f'Each week a new super pal is chosen at random from the list of Bringus members.' )
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
