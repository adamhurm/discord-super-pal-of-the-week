#!/usr/bin/env python3
"""Discord Super Pal of the Week Bot.

This bot manages weekly "Super Pal of the Week" promotions in a Discord server,
featuring AI-powered image generation, automated role management, and fun commands.
"""

import asyncio
import datetime
import json
import secrets
import uvicorn
from typing import List, Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

import superpal.static as superpal_static
import superpal.env as superpal_env
from superpal.cards.db import init_db, DB_PATH
from superpal.cards.service import (
    draw_card, sync_members, generate_magic_link, trade_in, upgrade,
    create_trade_offer, execute_trade, decline_trade, TRADE_EXPIRY_MINUTES,
    gift_card, get_card_quantity,
)
from superpal.cards.models import RARITY_LABELS
from superpal.env import WEBAPP_BASE_URL
from superpal.cards.embeds import build_card_embed

# Get logger
log = superpal_env.log

#############
# Bot setup #
#############
intents = discord.Intents.default()
intents.members = True  # Required to list all users in a guild
intents.message_content = True  # Required to use spin-the-wheel and grab winner
bot = commands.Bot(command_prefix='!', intents=intents)

_guild_members_cache: list[dict] = []

CLIPPY_ROLE_ID = 1085646770006151259


def _parse_stats(raw: str | None) -> list[tuple[str, str]]:
    if not raw:
        return []
    try:
        return list(json.loads(raw).items())
    except (json.JSONDecodeError, AttributeError):
        return []


##################
# Helper Functions
##################
def get_non_bot_members(guild: discord.Guild) -> List[discord.Member]:
    """Get list of non-bot members from a guild.

    Args:
        guild: Discord guild to get members from

    Returns:
        List of non-bot members
    """
    return [m for m in guild.members if not m.bot]


def get_super_pal_role(guild: discord.Guild) -> Optional[discord.Role]:
    """Get the Super Pal of the Week role from a guild.

    Args:
        guild: Discord guild to get role from

    Returns:
        Super Pal role or None if not found
    """
    role = discord.utils.get(guild.roles, name=superpal_static.SUPER_PAL_ROLE_NAME)
    if not role:
        log.error(f"Super Pal role '{superpal_static.SUPER_PAL_ROLE_NAME}' not found in guild")
    return role


async def promote_super_pal(
    new_super_pal: discord.Member,
    old_super_pal: Optional[discord.Member],
    role: discord.Role,
    channel: discord.TextChannel,
    promoted_by: str
) -> None:
    """Promote a new super pal and demote the old one.

    Args:
        new_super_pal: Member to promote
        old_super_pal: Current super pal to demote (if any)
        role: Super Pal role
        channel: Channel to send announcement
        promoted_by: Name of person/process that triggered promotion
    """
    try:
        # Remove role from old super pal if exists
        if old_super_pal and role in old_super_pal.roles:
            await old_super_pal.remove_roles(role)
            log.info(f"{old_super_pal.name} removed from Super Pal role")

        # Add role to new super pal
        if role not in new_super_pal.roles:
            await new_super_pal.add_roles(role)
            log.info(f"{new_super_pal.name} promoted to Super Pal by {promoted_by}")

            await channel.send(
                f'Congratulations {new_super_pal.mention}! '
                f'You have been promoted to super pal of the week by {promoted_by}. '
                f'{superpal_static.WELCOME_MSG}'
            )
        else:
            log.info(f"{new_super_pal.name} already has Super Pal role")

    except Exception as e:
        log.error(f"Error promoting super pal: {e}")
        await channel.send(f"Sorry, there was an error promoting {new_super_pal.mention}.")


##################
# Trade UI Views #
##################
class TradeView(discord.ui.View):
    def __init__(self, trade_id: int, proposer_id: str, recipient_id: str):
        super().__init__(timeout=TRADE_EXPIRY_MINUTES * 60)
        self.trade_id = trade_id
        self.proposer_id = proposer_id
        self.recipient_id = recipient_id
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if str(interaction.user.id) != self.recipient_id:
            await interaction.response.send_message(
                "Only the trade recipient can accept this offer.", ephemeral=True
            )
            return
        success, reason = await execute_trade(self.trade_id)
        self.stop()
        if success:
            await interaction.response.edit_message(
                content="Trade accepted! Cards have been exchanged.", view=None
            )
        else:
            msg = {
                "already_resolved": "This trade has already been resolved.",
                "expired": "This trade has expired.",
                "proposer_missing_card": "Trade failed — the proposer no longer has that card.",
                "recipient_missing_card": "Trade failed — you no longer have that card.",
            }.get(reason or "", "Trade failed.")
            await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if str(interaction.user.id) != self.recipient_id:
            await interaction.response.send_message(
                "Only the trade recipient can decline this offer.", ephemeral=True
            )
            return
        await decline_trade(self.trade_id)
        self.stop()
        await interaction.response.edit_message(content="Trade declined.", view=None)

    async def on_timeout(self) -> None:
        await decline_trade(self.trade_id)
        if self.message:
            try:
                await self.message.edit(content="Trade offer expired.", view=None)
            except discord.NotFound:
                pass


class GiftConfirmView(discord.ui.View):
    def __init__(
        self,
        interaction: discord.Interaction,
        gifter_id: str,
        recipient: discord.Member,
        card_member_id: str,
        rarity: str,
    ):
        super().__init__(timeout=60)
        self.interaction = interaction
        self.gifter_id = gifter_id
        self.recipient = recipient
        self.card_member_id = card_member_id
        self.rarity = rarity

    async def on_timeout(self) -> None:
        try:
            await self.interaction.edit_original_response(
                content="Gift confirmation expired.", view=None
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if str(interaction.user.id) != self.gifter_id:
            await interaction.response.send_message(
                "Only the gifter can confirm this.", ephemeral=True
            )
            return

        self.stop()
        card, err = await gift_card(
            gifter_id=self.gifter_id,
            recipient_id=str(self.recipient.id),
            card_member_id=self.card_member_id,
            rarity=self.rarity,
            drawn_by_name=interaction.user.display_name,
        )

        if card is None:
            msg = {
                "no_card": "You no longer have that card.",
                "self_gift": "You can't gift a card to yourself.",
            }.get(err or "", "Gift failed.")
            await interaction.response.edit_message(content=msg, view=None)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT display_name, avatar_url, bio, stats FROM members WHERE discord_id = ?",
                (self.card_member_id,),
            ) as cur:
                row = await cur.fetchone()

        display_name = row[0] if row else "Unknown"
        avatar_url = row[1] if row else None
        embed = build_card_embed(
            display_name=display_name,
            avatar_url=avatar_url,
            rarity=self.rarity,
            card_number=card.id,
            drawn_by=self.recipient.display_name,
            bio=row[2] if row else None,
            stats_pairs=_parse_stats(row[3] if row else None),
            action_label=f"gifted by {interaction.user.display_name} to",
        )
        await interaction.response.edit_message(content="Gift sent!", view=None)
        if interaction.channel:
            await interaction.channel.send(
                content=f"{self.recipient.mention} just received a gift from {interaction.user.mention}!",
                embed=embed,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if str(interaction.user.id) != self.gifter_id:
            await interaction.response.send_message(
                "Only the gifter can cancel this.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.edit_message(content="Gift cancelled.", view=None)


##################
# Slash commands #
##################
@bot.tree.command(name='superpal')
@app_commands.checks.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def add_super_pal(interaction: discord.Interaction, new_super_pal: discord.Member) -> None:
    """Promote a user to Super Pal of the Week role.

    Args:
        new_super_pal: choose the member you want to promote to super pal
    """
    try:
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        if not channel:
            await interaction.response.send_message(
                'Error: Could not find configured channel.',
                ephemeral=True
            )
            return

        role = get_super_pal_role(interaction.guild)
        if not role:
            await interaction.response.send_message(
                'Error: Super Pal role not found.',
                ephemeral=True
            )
            return

        # Check if new super pal already has the role
        if role in new_super_pal.roles:
            await interaction.response.send_message(
                f'{new_super_pal.mention} is already super pal of the week.',
                ephemeral=True
            )
            return

        # Promote new super pal and remove current super pal
        await new_super_pal.add_roles(role)
        await interaction.user.remove_roles(role)

        log.info(f'{new_super_pal.name} promoted by {interaction.user.name}')

        await interaction.response.send_message(
            f'You have promoted {new_super_pal.mention} to super pal of the week!',
            ephemeral=True
        )

        await channel.send(
            f'Congratulations {new_super_pal.mention}! '
            f'You have been promoted to super pal of the week by {interaction.user.name}. '
            f'{superpal_static.WELCOME_MSG}'
        )

    except Exception as e:
        log.error(f"Error in add_super_pal command: {e}")
        await interaction.response.send_message(
            'Sorry, there was an error processing your request.',
            ephemeral=True
        )


@bot.tree.command(name="card-draw", description="Draw a card from the Bringus deck (up to 5 per week)")
async def draw_card_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    member = interaction.user
    is_super_pal = any(
        r.name == superpal_static.SUPER_PAL_ROLE_NAME for r in getattr(member, "roles", [])
    )
    max_draws = 10 if is_super_pal else 5

    card = await draw_card(owner_id=str(member.id), max_draws=max_draws, drawn_by_name=member.display_name)
    if card is None:
        limit_label = "10 draws" if is_super_pal else "5 draws"
        await interaction.followup.send(
            f"You've used your {limit_label} for this week. Come back Sunday!",
            ephemeral=True,
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url, bio, stats FROM members WHERE discord_id = ?",
            (card.card_member_id,),
        ) as cur:
            row = await cur.fetchone()

    display_name = row[0] if row else "Unknown"
    avatar_url = row[1] if row else None

    embed = build_card_embed(
        display_name=display_name,
        avatar_url=avatar_url,
        rarity=card.rarity,
        card_number=card.id,
        drawn_by=card.drawn_by_name or member.display_name,
        bio=row[2] if row else None,
        stats_pairs=_parse_stats(row[3] if row else None),
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="card-display", description="Show a card you own in the channel")
@discord.app_commands.describe(
    member="The member whose card you want to display",
    rarity="The rarity of the card to display",
)
@discord.app_commands.choices(rarity=[
    discord.app_commands.Choice(name="Common", value="common"),
    discord.app_commands.Choice(name="Uncommon", value="uncommon"),
    discord.app_commands.Choice(name="Rare", value="rare"),
    discord.app_commands.Choice(name="Legendary", value="legendary"),
])
async def display_card_command(
    interaction: discord.Interaction,
    member: discord.Member,
    rarity: str,
) -> None:
    await interaction.response.defer()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT uc.id, m.display_name, m.avatar_url, m.bio, m.stats, uc.drawn_by_name "
            "FROM user_cards uc JOIN members m ON uc.card_member_id = m.discord_id "
            "WHERE uc.owner_id = ? AND uc.card_member_id = ? AND uc.rarity = ? AND uc.quantity > 0",
            (str(interaction.user.id), str(member.id), rarity),
        ) as cur:
            row = await cur.fetchone()

    if row is None:
        await interaction.followup.send(
            f"You don't own a {rarity.upper()} {member.display_name} card.",
            ephemeral=True,
        )
        return

    card_id, display_name, avatar_url, bio, stats_raw, drawn_by_name = row
    embed = build_card_embed(
        display_name=display_name,
        avatar_url=avatar_url,
        rarity=rarity,
        card_number=card_id,
        drawn_by=drawn_by_name or interaction.user.display_name,
        bio=bio,
        stats_pairs=_parse_stats(stats_raw),
    )
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="card-collection", description="Get a private link to your card collection")
async def my_collection_command(interaction: discord.Interaction) -> None:
    url = await generate_magic_link(
        user_id=str(interaction.user.id),
        link_type="collection",
        base_url=WEBAPP_BASE_URL,
    )
    try:
        await interaction.user.send(
            f"Here's your private collection link (valid for 24 hours after first click):\n{url}"
        )
        await interaction.response.send_message(
            "Check your DMs for your collection link!", ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "I couldn't send you a DM. Please enable DMs from server members and try again.",
            ephemeral=True,
        )


@bot.tree.command(name="card-trade-in", description="Trade 3 duplicate cards for a random card of the same rarity")
@discord.app_commands.describe(
    member="The member whose card you want to trade in",
    rarity="The rarity of the card to trade",
)
@discord.app_commands.choices(rarity=[
    discord.app_commands.Choice(name="Common", value="common"),
    discord.app_commands.Choice(name="Uncommon", value="uncommon"),
    discord.app_commands.Choice(name="Rare", value="rare"),
    discord.app_commands.Choice(name="Legendary", value="legendary"),
])
async def trade_in_command(
    interaction: discord.Interaction,
    member: discord.Member,
    rarity: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    card = await trade_in(
        owner_id=str(interaction.user.id),
        card_member_id=str(member.id),
        rarity=rarity,
        drawn_by_name=interaction.user.display_name,
    )
    if card is None:
        await interaction.followup.send(
            f"You need at least 3× {rarity.upper()} {member.display_name} to trade in.",
            ephemeral=True,
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url, bio, stats FROM members WHERE discord_id = ?",
            (card.card_member_id,),
        ) as cur:
            row = await cur.fetchone()

    display_name = row[0] if row else "Unknown"
    avatar_url = row[1] if row else None
    embed = build_card_embed(
        display_name=display_name,
        avatar_url=avatar_url,
        rarity=card.rarity,
        card_number=card.id,
        drawn_by=card.drawn_by_name or interaction.user.display_name,
        bio=row[2] if row else None,
        stats_pairs=_parse_stats(row[3] if row else None),
    )
    await interaction.followup.send(
        "Trade complete! You received:", embed=embed, ephemeral=True
    )


@bot.tree.command(name="card-upgrade", description="Spend 5 duplicate cards to upgrade a member's card rarity")
@discord.app_commands.describe(
    member="The member whose card you want to upgrade",
    rarity="The current rarity of the card",
)
@discord.app_commands.choices(rarity=[
    discord.app_commands.Choice(name="Common", value="common"),
    discord.app_commands.Choice(name="Uncommon", value="uncommon"),
    discord.app_commands.Choice(name="Rare", value="rare"),
])
async def upgrade_command(
    interaction: discord.Interaction,
    member: discord.Member,
    rarity: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    card = await upgrade(
        owner_id=str(interaction.user.id),
        card_member_id=str(member.id),
        rarity=rarity,
        drawn_by_name=interaction.user.display_name,
    )
    if card is None:
        await interaction.followup.send(
            f"You need at least 5× {rarity.upper()} {member.display_name} to upgrade.",
            ephemeral=True,
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url, bio, stats FROM members WHERE discord_id = ?",
            (card.card_member_id,),
        ) as cur:
            row = await cur.fetchone()

    display_name = row[0] if row else "Unknown"
    avatar_url = row[1] if row else None
    embed = build_card_embed(
        display_name=display_name,
        avatar_url=avatar_url,
        rarity=card.rarity,
        card_number=card.id,
        drawn_by=card.drawn_by_name or interaction.user.display_name,
        bio=row[2] if row else None,
        stats_pairs=_parse_stats(row[3] if row else None),
    )
    await interaction.followup.send(
        f"Upgrade complete! {member.display_name} is now {card.rarity.upper()}:",
        embed=embed,
        ephemeral=True,
    )


@bot.tree.command(name="card-trade", description="Offer one of your cards in exchange for another player's card")
@discord.app_commands.describe(
    recipient="The server member you want to trade with",
    offer_member="The member card you're offering",
    offer_rarity="The rarity of the card you're offering",
    request_member="The member card you want in return",
    request_rarity="The rarity of the card you want",
)
@discord.app_commands.choices(
    offer_rarity=[
        discord.app_commands.Choice(name="Common", value="common"),
        discord.app_commands.Choice(name="Uncommon", value="uncommon"),
        discord.app_commands.Choice(name="Rare", value="rare"),
        discord.app_commands.Choice(name="Legendary", value="legendary"),
    ],
    request_rarity=[
        discord.app_commands.Choice(name="Common", value="common"),
        discord.app_commands.Choice(name="Uncommon", value="uncommon"),
        discord.app_commands.Choice(name="Rare", value="rare"),
        discord.app_commands.Choice(name="Legendary", value="legendary"),
    ],
)
async def propose_trade_command(
    interaction: discord.Interaction,
    recipient: discord.Member,
    offer_member: discord.Member,
    offer_rarity: str,
    request_member: discord.Member,
    request_rarity: str,
) -> None:
    await interaction.response.defer(ephemeral=True)

    if interaction.user.id == recipient.id:
        await interaction.followup.send("You can't trade with yourself.", ephemeral=True)
        return
    if not interaction.channel:
        await interaction.followup.send(
            "This command must be used in a server channel.", ephemeral=True
        )
        return

    trade, err = await create_trade_offer(
        str(interaction.user.id), str(recipient.id),
        str(offer_member.id), offer_rarity,
        str(request_member.id), request_rarity,
    )
    if trade is None:
        msg = {
            "invalid_rarity": "Invalid rarity specified.",
            "self_trade": "You can't trade with yourself.",
            "no_offer_card": (
                f"You don't have a {offer_rarity.upper()} {offer_member.display_name} card to offer."
            ),
            "pending_exists": "You already have a pending trade offer. Wait for it to resolve first.",
        }.get(err or "", "Could not create trade offer.")
        await interaction.followup.send(msg, ephemeral=True)
        return

    view = TradeView(trade.id, str(interaction.user.id), str(recipient.id))
    channel_msg = await interaction.channel.send(
        content=(
            f"{recipient.mention}, **{interaction.user.display_name}** wants to trade:\n\n"
            f"Their offer: **{RARITY_LABELS[offer_rarity]} {offer_member.display_name}**\n"
            f"They want:   **{RARITY_LABELS[request_rarity]} {request_member.display_name}**\n\n"
            f"You have {TRADE_EXPIRY_MINUTES} minutes to respond."
        ),
        view=view,
    )
    view.message = channel_msg
    await interaction.followup.send("Trade offer sent!", ephemeral=True)


@bot.tree.command(name="card-gift", description="Give one of your cards to another player")
@discord.app_commands.describe(
    recipient="The server member to receive the gift",
    member="The member card you want to gift",
    rarity="The rarity of the card to gift",
)
@discord.app_commands.choices(rarity=[
    discord.app_commands.Choice(name="Common", value="common"),
    discord.app_commands.Choice(name="Uncommon", value="uncommon"),
    discord.app_commands.Choice(name="Rare", value="rare"),
    discord.app_commands.Choice(name="Legendary", value="legendary"),
])
async def gift_card_command(
    interaction: discord.Interaction,
    recipient: discord.Member,
    member: discord.Member,
    rarity: str,
) -> None:
    gifter_id = str(interaction.user.id)

    if interaction.user.id == recipient.id:
        await interaction.response.send_message(
            "You can't gift a card to yourself.", ephemeral=True
        )
        return

    qty = await get_card_quantity(gifter_id, str(member.id), rarity)
    if qty < 1:
        await interaction.response.send_message(
            f"You don't own a {RARITY_LABELS[rarity]} {member.display_name} card.",
            ephemeral=True,
        )
        return

    view = GiftConfirmView(
        interaction=interaction,
        gifter_id=gifter_id,
        recipient=recipient,
        card_member_id=str(member.id),
        rarity=rarity,
    )
    await interaction.response.send_message(
        f"You're about to gift a **{RARITY_LABELS[rarity]} {member.display_name}** to {recipient.mention} — confirm?",
        view=view,
        ephemeral=True,
    )


@bot.tree.command(name="admin-link", description="Get a private admin dashboard link (The Clippy only)")
async def admin_link_command(interaction: discord.Interaction) -> None:
    member = interaction.user
    role_ids = [r.id for r in getattr(member, "roles", [])]
    if CLIPPY_ROLE_ID not in role_ids:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return
    url = await generate_magic_link(
        user_id=str(member.id),
        link_type="admin",
        base_url=WEBAPP_BASE_URL,
    )
    try:
        await member.send(
            f"Here's your private admin dashboard link (valid for 24 hours after first click):\n{url}"
        )
        await interaction.response.send_message(
            "Check your DMs for your admin link!", ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "I couldn't send you a DM. Please enable DMs from server members and try again.",
            ephemeral=True,
        )


@bot.tree.command(name="announce", description="Post a message to the Super Pal channel (The Clippy only)")
@discord.app_commands.describe(message="The message to post to the channel")
async def announce_command(interaction: discord.Interaction, message: str) -> None:
    member = interaction.user
    role_ids = [r.id for r in getattr(member, "roles", [])]
    if CLIPPY_ROLE_ID not in role_ids:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return
    channel = bot.get_channel(superpal_env.CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message(
            "Could not find the Super Pal channel.", ephemeral=True
        )
        return
    await channel.send(message)
    await interaction.response.send_message("Announcement posted!", ephemeral=True)


###############
# Looped task #
###############
@tasks.loop(hours=24*7)
async def super_pal_of_the_week():
    """Weekly task to choose a new Super Pal of the Week."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID)
        if not guild:
            log.error(f"Could not find guild with ID {superpal_env.GUILD_ID}")
            return

        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        if not channel:
            log.error(f"Could not find channel with ID {superpal_env.CHANNEL_ID}")
            return

        role = get_super_pal_role(guild)
        if not role:
            return

        # Get list of non-bot members
        true_member_list = get_non_bot_members(guild)
        if not true_member_list:
            log.error("No non-bot members found in guild")
            return

        log.info(f"Total guild members: {guild.member_count}")
        log.info(f"Cached members: {len(guild.members)}")
        log.info(f"Non-bot members: {len(true_member_list)}")
        if len(guild.members) < guild.member_count:
            log.warning("Member cache may be incomplete! Some users may be excluded from selection.")

        # Exclude current super pal so they can't be re-selected
        eligible_members = [m for m in true_member_list if role not in m.roles]
        if not eligible_members:
            log.error("No eligible members for super pal selection (all members already have role)")
            return

        new_super_pal = secrets.choice(eligible_members)
        log.info(f'Selected new super pal of the week: {new_super_pal.name}')

        # Remove role from all current super pals
        for member in true_member_list:
            if role in member.roles:
                await member.remove_roles(role)
                log.info(f'{member.name} removed from super pal role')

        # Add role to new super pal
        await new_super_pal.add_roles(role)
        log.info(f'{new_super_pal.name} promoted to super pal')

        await channel.send(
            f'Congratulations to {new_super_pal.mention}, '
            f'the super pal of the week! {superpal_static.WELCOME_MSG}'
        )

    except Exception as e:
        log.error(f"Error in super_pal_of_the_week task: {e}")


@super_pal_of_the_week.before_loop
async def before_super_pal_of_the_week():
    """Wait until Sunday at noon before starting the weekly task."""
    await bot.wait_until_ready()

    try:
        # Find amount of time until Sunday at noon
        now = datetime.datetime.now()
        current_day = datetime.date.today().isoweekday()
        days_until_sunday = 7 - current_day

        # If it's past noon on Sunday, add 7 days to timer
        if current_day == 7 and now.hour > 12:
            days_until_sunday = 7

        time_diff = now + datetime.timedelta(days=days_until_sunday)
        future = datetime.datetime(time_diff.year, time_diff.month, time_diff.day, 12, 0)

        # Sleep task until Sunday at noon
        sleep_duration = (future - now).total_seconds()
        log.info(f'Sleeping for {future - now}. Will wake up Sunday at 12PM.')
        await asyncio.sleep(sleep_duration)

    except Exception as e:
        log.error(f"Error in before_super_pal_of_the_week: {e}")


##############
# Bot events #
##############
@bot.event
async def on_command_error(ctx, error):
    """Suppress error messages for commands that aren't related to Super Pal Bot."""
    if isinstance(error, commands.errors.CommandNotFound):
        return
    if isinstance(error, commands.errors.MissingRole):
        await ctx.send("You don't have permission to use this command.")
        return
    log.error(f"Command error: {error}")
    raise error


@bot.event
async def on_ready():
    """Initialize bot when ready."""
    log.info(f'Bot logged in as {bot.user}')
    log.info(f'Connected to {len(bot.guilds)} guilds')

    try:
        await bot.tree.sync()
        log.info('Slash commands synced')
    except Exception as e:
        log.error(f'Error syncing slash commands: {e}')

    await init_db()
    guild = bot.get_guild(superpal_env.GUILD_ID)
    if guild:
        members_data = [
            {
                "discord_id": str(m.id),
                "display_name": m.display_name,
                "avatar_url": str(m.display_avatar.url) if m.display_avatar else None,
            }
            for m in guild.members
            if not m.bot
        ]
        await sync_members(members_data)
        _guild_members_cache.clear()
        _guild_members_cache.extend(members_data)
        log.info("Synced %d members to card DB", len(members_data))

    if not super_pal_of_the_week.is_running():
        super_pal_of_the_week.start()
        log.info('Weekly task started')


@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages, including Spin The Wheel integration."""
    try:
        # Skip bot messages
        if message.author.bot:
            # Check if this is from Spin The Wheel bot
            guild = bot.get_guild(superpal_env.GUILD_ID)
            if not guild:
                await bot.process_commands(message)
                return

            spin_the_wheel_role = discord.utils.get(guild.roles, name=superpal_static.SPIN_THE_WHEEL_ROLE_NAME)
            member = guild.get_member(message.author.id)

            # Only check embedded messages from Spin The Wheel Bot
            if member and spin_the_wheel_role and spin_the_wheel_role in member.roles:
                await handle_spin_the_wheel_message(message, guild)

            await bot.process_commands(message)
            return

        # Process commands for non-bot messages
        await bot.process_commands(message)

    except Exception as e:
        log.error(f"Error in on_message: {e}")


async def handle_spin_the_wheel_message(message: discord.Message, guild: discord.Guild):
    """Handle messages from Spin The Wheel bot to detect winners.

    Args:
        message: Message from Spin The Wheel bot
        guild: Discord guild
    """
    try:
        for embed in message.embeds:
            # Wait until message contains Spin the Wheel winner
            if embed.description is None:
                continue

            if len(embed.description) > 0 and embed.description[0] == '🏆':
                super_pal_role = get_super_pal_role(guild)
                if not super_pal_role:
                    return

                # Grab winner name from Spin the Wheel message
                new_super_pal_name = embed.description[12:-2]
                new_super_pal = discord.utils.get(guild.members, name=new_super_pal_name)

                if not new_super_pal:
                    log.error(f"Could not find member: {new_super_pal_name}")
                    return

                log.info(f'{new_super_pal.name} was chosen by wheel spin')

                # Remove existing Super Pal of the Week
                true_member_list = get_non_bot_members(guild)
                for member in true_member_list:
                    if super_pal_role in member.roles:
                        await member.remove_roles(super_pal_role)

                # Add new winner to Super Pal of the Week
                await new_super_pal.add_roles(super_pal_role)

                await message.channel.send(
                    f'Congratulations {new_super_pal.mention}! '
                    f'You have been promoted to super pal of the week by wheel spin. '
                    f'{superpal_static.WELCOME_MSG}'
                )

    except Exception as e:
        log.error(f"Error handling spin the wheel message: {e}")


################
# Bot commands #
################
@bot.command(name='spotw', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def spotw_command(ctx, new_super_pal: discord.Member):
    """Promote users to Super Pal of the Week (legacy command)."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID)
        channel = bot.get_channel(superpal_env.CHANNEL_ID)

        if not guild or not channel:
            await ctx.send("Error: Could not find guild or channel.")
            return

        role = get_super_pal_role(guild)
        if not role:
            await ctx.send("Error: Super Pal role not found.")
            return

        current_super_pal = ctx.message.author

        # Promote new user and remove current super pal
        if role not in new_super_pal.roles:
            log.info(f'{new_super_pal.name} promoted by {current_super_pal.name}')
            await new_super_pal.add_roles(role)
            await current_super_pal.remove_roles(role)
            await channel.send(
                f'Congratulations {new_super_pal.mention}! '
                f'You have been promoted to super pal of the week by {current_super_pal.name}. '
                f'{superpal_static.WELCOME_MSG}'
            )
        else:
            await ctx.send(f'{new_super_pal.mention} is already super pal of the week.')

    except Exception as e:
        log.error(f"Error in spotw command: {e}")
        await ctx.send("Sorry, there was an error processing your request.")


@bot.command(name='spinthewheel', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def spinthewheel(ctx):
    """Spin the wheel for a random Super Pal of the Week."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID)
        channel = bot.get_channel(superpal_env.CHANNEL_ID)

        if not guild or not channel:
            await ctx.send("Error: Could not find guild or channel.")
            return

        # Get list of non-bot members
        true_member_list = get_non_bot_members(guild)
        if not true_member_list:
            await ctx.send("Error: No members found.")
            return

        true_name_list = [member.name for member in true_member_list]
        true_name_str = ", ".join(true_name_list)

        # Send Spin the Wheel command
        await channel.send(f'?pick {true_name_str}')
        log.info('Spinning the wheel for new super pal')

    except Exception as e:
        log.error(f"Error in spinthewheel command: {e}")
        await ctx.send("Sorry, there was an error spinning the wheel.")


@bot.command(name='commands', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def list_commands(ctx):
    """Display information about available commands."""
    try:
        log.info(f'{ctx.message.author.name} used help command')
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        if channel:
            await channel.send(superpal_static.COMMANDS_MSG)
        else:
            await ctx.send(superpal_static.COMMANDS_MSG)

    except Exception as e:
        log.error(f"Error in commands command: {e}")
        await ctx.send("Sorry, there was an error displaying commands.")


@bot.command(name='cacaw', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def cacaw(ctx):
    """Send party parrot discord emoji."""
    try:
        log.info(f'{ctx.message.author.name} used cacaw command')
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        emoji_guild = bot.get_guild(superpal_env.EMOJI_GUILD_ID)

        if not emoji_guild:
            await ctx.send("Error: Emoji guild not found.")
            return

        partyparrot_emoji = discord.utils.get(emoji_guild.emojis, name='partyparrot')

        if partyparrot_emoji:
            await channel.send(str(partyparrot_emoji) * superpal_static.EMOJI_SPAM_COUNT)
        else:
            await ctx.send("Partyparrot emoji not found!")

    except Exception as e:
        log.error(f"Error in cacaw command: {e}")
        await ctx.send("Sorry, there was an error.")


@bot.command(name='meow', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def meow(ctx):
    """Send party cat discord emoji."""
    try:
        log.info(f'{ctx.message.author.name} used meow command')
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        emoji_guild = bot.get_guild(superpal_env.EMOJI_GUILD_ID)

        if not emoji_guild:
            await ctx.send("Error: Emoji guild not found.")
            return

        partymeow_emoji = discord.utils.get(emoji_guild.emojis, name='partymeow')

        if partymeow_emoji:
            await channel.send(str(partymeow_emoji) * superpal_static.EMOJI_SPAM_COUNT)
        else:
            await ctx.send("Partymeow emoji not found!")

    except Exception as e:
        log.error(f"Error in meow command: {e}")
        await ctx.send("Sorry, there was an error.")


@bot.command(name='karatechop', pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def karate_chop(ctx):
    """Randomly remove one user from voice chat."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID)
        channel = bot.get_channel(superpal_env.CHANNEL_ID)
        current_super_pal = ctx.message.author

        if not guild or not channel:
            await ctx.send("Error: Could not find guild or channel.")
            return

        active_members = [voice_channel.members for voice_channel in guild.voice_channels]

        # Check if anyone is in voice channels
        if not any(active_members):
            log.info(f'{current_super_pal.name} used karate chop, but no one is in voice channels')
            await channel.send(f'There is no one to karate chop, {current_super_pal.mention}!')
            return

        # Flatten user list, filter out bots, and choose random user
        def flatten(nested):
            return [x for y in nested for x in y]
        true_member_list = [m for m in flatten(active_members) if not m.bot]

        if not true_member_list:
            await channel.send('No users found in voice channels!')
            return

        chopped_member = secrets.choice(true_member_list)
        log.info(f'{chopped_member.name} karate chopped')

        # Check that an 'AFK' channel exists
        afk_channels = [c for c in guild.voice_channels if superpal_static.AFK_CHANNEL_KEYWORD in c.name]

        if afk_channels:
            await chopped_member.move_to(afk_channels[0])
            await channel.send(f'karate chopped {chopped_member.mention}!')
        else:
            await channel.send(
                f'{chopped_member.mention} would have been chopped, but an AFK channel was not found.\n'
                'Please complain to the server owner.'
            )

    except Exception as e:
        log.error(f"Error in karate_chop command: {e}")
        await ctx.send("Sorry, there was an error processing karate chop.")


################
# Start the bot
################
async def _main() -> None:
    from superpal.webapp.app import create_app
    from superpal.env import WEBAPP_HOST, WEBAPP_PORT
    webapp = create_app()
    config = uvicorn.Config(webapp, host=WEBAPP_HOST, port=WEBAPP_PORT, log_level="info")
    server = uvicorn.Server(config)
    async with bot:
        await asyncio.gather(
            bot.start(superpal_env.TOKEN),
            server.serve(),
        )


if __name__ == "__main__":
    asyncio.run(_main())
