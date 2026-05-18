"""Static content and constant messages for Super Pal Bot.

This module contains all static messages, prompts, and configuration
used throughout the Discord bot.
"""

###################
# Message strings #
###################
COMMANDS_MSG = (
    '**!spotw @name**\n\tPromote another user to super pal of the week. Be sure to @mention the user.\n'
    '**!spinthewheel**\n\tSpin the wheel to choose a new super pal of the week.\n'
    '**!cacaw**\n\tSpam the channel with party parrots.\n'
    '**!meow**\n\tSpam the channel with party cats.\n'
    '**!karatechop**\n\tMove a random user to AFK voice channel.\n\n'
    '**Bringus Card Game**\n'
    '**/card-draw**\n\tDraw a random Bringus card (up to 5 per week; Super Pal gets 10). Resets every Sunday.\n'
    '**/card-trade-in**\n\tTrade 3 duplicate cards of the same rarity for a new random card of that rarity.\n'
    '**/card-upgrade**\n\tSpend 5 duplicate cards to upgrade a member\'s card to the next rarity tier.\n'
    '**/card-collection**\n\tGet a private link to view your full card collection.'
)

WELCOME_MSG = (
    'Welcome to the super pal channel.\n\n'
    'Use super pal commands by posting commands in chat. Examples:\n'
    '( !commands (for full list) | !karatechop | !spotw @name | !meow )\n\n'
    'As Super Pal you get **10 card draws per week** — use `/card-draw` to collect Bringus cards, '
    '`/card-trade-in` to swap duplicates, `/card-upgrade` to level up cards, and `/card-collection` to see your collection.'
)

RUNTIME_WARN_MSG = 'WARN: Super Pal will still run but you are very likely to encounter run-time errors.'

# Role and channel names
SUPER_PAL_ROLE_NAME = 'Super Pal of the Week'
SPIN_THE_WHEEL_ROLE_NAME = 'Spin The Wheel'
AFK_CHANNEL_KEYWORD = 'AFK'

# Bot configuration
EMOJI_SPAM_COUNT = 50
