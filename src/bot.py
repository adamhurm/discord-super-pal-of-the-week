#!/usr/bin/env python3
"""Discord Super Pal of the Week Bot.

This bot manages weekly "Super Pal of the Week" promotions in a Discord server,
featuring AI-powered image generation, automated role management, and fun commands.
"""

import asyncio
import datetime
import json
import secrets
from typing import cast

import aiosqlite
import discord
import uvicorn
from discord import app_commands
from discord.ext import commands, tasks

import superpal.env as superpal_env
import superpal.palymarket.service as palymarket_svc
import superpal.static as superpal_static
from superpal.cards.db import DB_PATH, init_db
from superpal.cards.embeds import build_card_embed
from superpal.cards.fight_service import (
    FIGHT_TOKEN_EXPIRY_MINUTES,
    accept_fight,
    create_fight,
    create_fight_token,
    expire_pending_challenges,
    get_fight_leaderboard,
)
from superpal.cards.models import RARITY_LABELS
from superpal.cards.pringle_service import (
    ITEM_COSTS,
    ITEM_DESCRIPTIONS,
    ITEM_NAMES,
    buy_item,
    get_balance,
    get_player_items,
    reset_heal_potions_for_empty_players,
)
from superpal.cards.service import (
    TRADE_EXPIRY_MINUTES,
    accept_offer,
    decline_offer,
    decline_trade,
    draw_card,
    execute_trade,
    expire_offer,
    generate_magic_link,
    get_card_quantity,
    get_collection,
    get_leaderboard,
    get_offer_by_id,
    gift_card,
    set_offer_discord_message_id,
    sync_members,
    trade_in,
    upgrade,
)
from superpal.env import WEBAPP_BASE_URL
from superpal.economy import boin_service, exchange_service, game_service
from superpal.economy.boin_service import award_daily_to_all
from superpal.schedule import next_noon_utc, next_sunday_noon_utc

FIGHT_CHALLENGE_TIMEOUT = FIGHT_TOKEN_EXPIRY_MINUTES * 60
TRADE_OFFER_EXPIRY_HOURS = 24

# Get logger
log = superpal_env.log

#############
# Bot setup #
#############
intents = discord.Intents.default()
intents.members = True  # Required to list all users in a guild
intents.message_content = True  # Required to use spin-the-wheel and grab winner
bot = commands.Bot(command_prefix="!", intents=intents)

CLIPPY_ROLE_ID = 1085646770006151259


def _is_clippy(interaction: discord.Interaction) -> bool:
    role_ids = [r.id for r in getattr(interaction.user, "roles", [])]
    return CLIPPY_ROLE_ID in role_ids


# Shared guild member cache — set in on_ready, read by the webapp admin sync route
_guild_members_cache: list[dict] | None = None


def _parse_stats(raw: str | None) -> list[tuple[str, str]]:
    if not raw:
        return []
    try:
        return list(json.loads(raw).items())
    except (json.JSONDecodeError, AttributeError):
        return []


def _resolve_avatar_url(avatar_url: str | None) -> str | None:
    """Return an absolute URL for Discord embeds.

    Synthetic members store avatars as relative paths (/static/avatars/...).
    Discord's embed API requires HTTP(S) URLs, so prefix with WEBAPP_BASE_URL.
    """
    if not avatar_url or avatar_url.startswith("http"):
        return avatar_url
    return f"{WEBAPP_BASE_URL.rstrip('/')}{avatar_url}"


##################
# Helper Functions
##################
def get_non_bot_members(guild: discord.Guild) -> list[discord.Member]:
    """Get list of non-bot members from a guild.

    Args:
        guild: Discord guild to get members from

    Returns:
        List of non-bot members
    """
    return [m for m in guild.members if not m.bot]


def get_super_pal_role(guild: discord.Guild) -> discord.Role | None:
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


##################
# Trade UI Views #
##################
class TradeView(discord.ui.View):
    def __init__(self, trade_id: int, proposer_id: str, recipient_id: str):
        super().__init__(timeout=TRADE_EXPIRY_MINUTES * 60)
        self.trade_id = trade_id
        self.proposer_id = proposer_id
        self.recipient_id = recipient_id
        self.message: discord.Message | None = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
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
    async def decline_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
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


class TradeOfferView(discord.ui.View):
    """Discord DM view sent when a marketplace offer arrives."""

    def __init__(self, offer_id: int, listing_owner_id: str):
        super().__init__(timeout=TRADE_OFFER_EXPIRY_HOURS * 3600)
        self.offer_id = offer_id
        self.listing_owner_id = listing_owner_id
        self.message: discord.Message | None = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.listing_owner_id:
            await interaction.response.send_message(
                "Only the listing owner can accept.", ephemeral=True
            )
            return
        success, reason = await accept_offer(self.offer_id, self.listing_owner_id)
        self.stop()
        if success:
            await interaction.response.edit_message(
                content="Trade accepted! Cards have been exchanged.", view=None
            )
        else:
            msg = {
                "not_found": "This offer no longer exists.",
                "not_owner": "You are not the listing owner.",
                "listing_no_card": "Trade failed — you no longer have those listing cards.",
                "offer_no_card": "Trade failed — the proposer no longer has their offered cards.",
            }.get(reason or "", "Trade failed.")
            await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.listing_owner_id:
            await interaction.response.send_message(
                "Only the listing owner can decline.", ephemeral=True
            )
            return
        await decline_offer(self.offer_id, self.listing_owner_id)
        self.stop()
        await interaction.response.edit_message(content="Offer declined.", view=None)

    async def on_timeout(self) -> None:
        await expire_offer(self.offer_id)
        if self.message:
            try:
                await self.message.edit(content="Offer expired.", view=None)
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
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
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
        avatar_url = _resolve_avatar_url(row[1] if row else None)
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
        if isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.channel.send(
                content=(
                    f"{self.recipient.mention} just received "
                    f"a gift from {interaction.user.mention}!"
                ),
                embed=embed,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.gifter_id:
            await interaction.response.send_message(
                "Only the gifter can cancel this.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.edit_message(content="Gift cancelled.", view=None)


#######################
# Fight UI View       #
#######################
class FightChallengeView(discord.ui.View):
    def __init__(
        self,
        fight_id: int,
        challenger_id: str,
        opponent_id: str,
        challenger_name: str,
        mode: str,
    ):
        super().__init__(timeout=FIGHT_CHALLENGE_TIMEOUT)
        self.fight_id = fight_id
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.challenger_name = challenger_name
        self.mode = mode
        self.message: discord.Message | None = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.opponent_id:
            await interaction.response.send_message(
                "Only the challenged player can accept.", ephemeral=True
            )
            return
        self.stop()
        fight = await accept_fight(self.fight_id)
        if fight is None:
            await interaction.response.edit_message(
                content="This challenge has already expired or been resolved.", view=None
            )
            return

        await interaction.response.edit_message(
            content="Challenge accepted! DMs are on their way.", view=None
        )

        # DM both players their lobby magic links
        challenger_url = await create_fight_token(
            self.fight_id, self.challenger_id, WEBAPP_BASE_URL
        )
        opponent_url = await create_fight_token(self.fight_id, self.opponent_id, WEBAPP_BASE_URL)

        for uid, url in ((self.challenger_id, challenger_url), (self.opponent_id, opponent_url)):
            user = interaction.client.get_user(int(uid))
            if user:
                try:
                    other_name = (
                        self.challenger_name
                        if uid == self.opponent_id
                        else interaction.user.display_name
                    )
                    await user.send(
                        f"Your **{self.mode}** battle vs. **{other_name}** "
                        f"is ready!\n\nOpen the fight lobby: <{url}>",
                        suppress_embeds=True,
                    )
                except discord.Forbidden:
                    pass

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.opponent_id:
            await interaction.response.send_message(
                "Only the challenged player can decline.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.edit_message(content="Challenge declined.", view=None)

    async def on_timeout(self) -> None:
        await expire_pending_challenges()
        if self.message:
            try:
                await self.message.edit(content="Fight challenge expired.", view=None)
            except discord.NotFound:
                pass


async def notify_trade_offer(offer_id: int) -> None:
    """DM the listing owner about a new marketplace offer."""
    offer = await get_offer_by_id(offer_id)
    if offer is None:
        return
    guild = bot.get_guild(int(superpal_env.GUILD_ID))
    if guild is None:
        return
    member = guild.get_member(int(offer.listing.owner_id))
    if member is None:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        offer_names = []
        for item in offer.items:
            async with db.execute(
                "SELECT display_name FROM members WHERE discord_id = ?", (item.member_id,)
            ) as cur:
                row = await cur.fetchone()
            offer_names.append(
                f"{RARITY_LABELS[item.rarity]} {row[0] if row else item.member_id}"
            )
        listing_names = []
        for item in offer.listing.items:
            async with db.execute(
                "SELECT display_name FROM members WHERE discord_id = ?", (item.member_id,)
            ) as cur:
                row = await cur.fetchone()
            listing_names.append(
                f"{RARITY_LABELS[item.rarity]} {row[0] if row else item.member_id}"
            )
    view = TradeOfferView(offer_id=offer_id, listing_owner_id=offer.listing.owner_id)
    content = (
        f"**{offer.proposer_display_name}** made an offer on your listing!\n\n"
        f"Your listing: {', '.join(listing_names)}\n"
        f"Their offer: {', '.join(offer_names)}\n\n"
        f"View in marketplace: {WEBAPP_BASE_URL}/marketplace"
    )
    try:
        dm = await member.send(content=content, view=view)
        view.message = dm
        await set_offer_discord_message_id(offer_id, str(dm.id))
    except discord.Forbidden:
        pass


async def edit_offer_dm(offer_id: int, message: str) -> None:
    """Edit the DM notification for an offer after web-UI accept/decline."""
    offer = await get_offer_by_id(offer_id)
    if offer is None:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_message_id FROM trade_offers WHERE id = ?", (offer_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return
    discord_message_id = row[0]
    guild = bot.get_guild(int(superpal_env.GUILD_ID))
    if guild is None:
        return
    owner_member = guild.get_member(int(offer.listing.owner_id))
    if owner_member is None:
        return
    try:
        dm_channel = await owner_member.create_dm()
        msg = await dm_channel.fetch_message(int(discord_message_id))
        await msg.edit(content=message, view=None)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


##################
# Slash commands #
##################
@bot.tree.command(name="superpal")
@app_commands.checks.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def add_super_pal(interaction: discord.Interaction, new_super_pal: discord.Member) -> None:
    """Promote a user to Super Pal of the Week role.

    Args:
        new_super_pal: choose the member you want to promote to super pal
    """
    try:
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
        if not channel:
            await interaction.response.send_message(
                "Error: Could not find configured channel.", ephemeral=True
            )
            return

        assert interaction.guild is not None
        role = get_super_pal_role(interaction.guild)
        if not role:
            await interaction.response.send_message(
                "Error: Super Pal role not found.", ephemeral=True
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command must be used in a server.", ephemeral=True
            )
            return

        # Check if new super pal already has the role
        if role in new_super_pal.roles:
            await interaction.response.send_message(
                f"{new_super_pal.mention} is already super pal of the week.", ephemeral=True
            )
            return

        # Promote new super pal and remove current super pal
        await new_super_pal.add_roles(role)
        await interaction.user.remove_roles(role)

        log.info(f"{new_super_pal.name} promoted by {interaction.user.name}")

        await interaction.response.send_message(
            f"You have promoted {new_super_pal.mention} to super pal of the week!", ephemeral=True
        )

        await channel.send(
            f"Congratulations {new_super_pal.mention}! "
            f"You have been promoted to super pal of the week by {interaction.user.name}. "
            f"{superpal_static.WELCOME_MSG}"
        )

    except Exception as e:
        log.error(f"Error in add_super_pal command: {e}")
        await interaction.response.send_message(
            "Sorry, there was an error processing your request.", ephemeral=True
        )


@bot.tree.command(
    name="card-draw",
    description="Draw a card from the Bringus deck (up to 5 per week)",
)
async def draw_card_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    member = interaction.user
    is_super_pal = any(
        r.name == superpal_static.SUPER_PAL_ROLE_NAME for r in getattr(member, "roles", [])
    )
    max_draws = 10 if is_super_pal else 5

    card = await draw_card(
        owner_id=str(member.id), max_draws=max_draws, drawn_by_name=member.display_name
    )
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
    avatar_url = _resolve_avatar_url(row[1] if row else None)

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
@discord.app_commands.choices(
    rarity=[
        discord.app_commands.Choice(name="Common", value="common"),
        discord.app_commands.Choice(name="Uncommon", value="uncommon"),
        discord.app_commands.Choice(name="Rare", value="rare"),
        discord.app_commands.Choice(name="Legendary", value="legendary"),
    ]
)
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
        avatar_url=_resolve_avatar_url(avatar_url),
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


@bot.tree.command(
    name="card-trade-in",
    description="Trade 3 duplicate cards for a random card of the same rarity",
)
@discord.app_commands.describe(
    member="The member whose card you want to trade in",
    rarity="The rarity of the card to trade",
)
@discord.app_commands.choices(
    rarity=[
        discord.app_commands.Choice(name="Common", value="common"),
        discord.app_commands.Choice(name="Uncommon", value="uncommon"),
        discord.app_commands.Choice(name="Rare", value="rare"),
        discord.app_commands.Choice(name="Legendary", value="legendary"),
    ]
)
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
            f"You need at least 3× {rarity.upper()} {member.display_name} to trade in.",  # noqa: RUF001
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
    avatar_url = _resolve_avatar_url(row[1] if row else None)
    embed = build_card_embed(
        display_name=display_name,
        avatar_url=avatar_url,
        rarity=card.rarity,
        card_number=card.id,
        drawn_by=card.drawn_by_name or interaction.user.display_name,
        bio=row[2] if row else None,
        stats_pairs=_parse_stats(row[3] if row else None),
    )
    await interaction.followup.send("Trade complete! You received:", embed=embed, ephemeral=True)


@bot.tree.command(
    name="card-upgrade",
    description="Spend 5 duplicate cards to upgrade a member's card rarity",
)
@discord.app_commands.describe(
    member="The member whose card you want to upgrade",
    rarity="The current rarity of the card",
)
@discord.app_commands.choices(
    rarity=[
        discord.app_commands.Choice(name="Common", value="common"),
        discord.app_commands.Choice(name="Uncommon", value="uncommon"),
        discord.app_commands.Choice(name="Rare", value="rare"),
    ]
)
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
            f"You need at least 5× {rarity.upper()} {member.display_name} to upgrade.",  # noqa: RUF001
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
    avatar_url = _resolve_avatar_url(row[1] if row else None)
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


@bot.tree.command(
    name="card-trade",
    description="Open the trade marketplace to list cards and make offers",
)
async def propose_trade_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    url = await generate_magic_link(
        user_id=str(interaction.user.id),
        link_type="collection",
        base_url=WEBAPP_BASE_URL,
    )
    try:
        await interaction.user.send(
            f"Open this link to access the trade marketplace "
            f"(valid 24 hours after first click):\n{url}\n\n"
            "Once open, click **Marketplace** in the top nav to browse listings and make offers. "
            "Right-click any card in your collection to list it for trade."
        )
        await interaction.followup.send("Check your DMs for your marketplace link!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            f"Here's your marketplace link (enable DMs to receive these privately):\n{url}",
            ephemeral=True,
        )


@bot.tree.command(name="card-gift", description="Give one of your cards to another player")
@discord.app_commands.describe(
    recipient="The server member to receive the gift",
    member="The member card you want to gift",
    rarity="The rarity of the card to gift",
)
@discord.app_commands.choices(
    rarity=[
        discord.app_commands.Choice(name="Common", value="common"),
        discord.app_commands.Choice(name="Uncommon", value="uncommon"),
        discord.app_commands.Choice(name="Rare", value="rare"),
        discord.app_commands.Choice(name="Legendary", value="legendary"),
    ]
)
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
        (
            f"You're about to gift a **{RARITY_LABELS[rarity]} {member.display_name}**"
            f" to {recipient.mention} — confirm?"
        ),
        view=view,
        ephemeral=True,
    )


@bot.tree.command(name="card-collection-leaderboard", description="Show the top 10 card collectors")
@discord.app_commands.describe(sort_by="What to rank players by")
@discord.app_commands.choices(
    sort_by=[
        discord.app_commands.Choice(name="Total Cards", value="total"),
        discord.app_commands.Choice(name="Legendary Cards", value="legendary"),
        discord.app_commands.Choice(name="Unique Members", value="unique"),
    ]
)
async def card_collection_leaderboard_command(
    interaction: discord.Interaction,
    sort_by: str = "total",
) -> None:
    await interaction.response.defer()
    rows = await get_leaderboard(sort_by)

    title_map = {"total": "Total Cards", "legendary": "Legendary Cards", "unique": "Unique Members"}
    unit_map = {"total": "cards", "legendary": "legendary cards", "unique": "unique members"}
    title = f"Top 10 — {title_map.get(sort_by, 'Total Cards')}"
    unit = unit_map.get(sort_by, "cards")

    if not rows:
        embed = discord.Embed(
            title=title,
            description="No cards have been collected yet!",
            color=discord.Color(0x5865F2),
        )
    else:
        lines = [
            f"{rank}. {row['display_name']} — {row['total']} {unit}"
            for rank, row in enumerate(rows, start=1)
        ]
        embed = discord.Embed(
            title=title, description="\n".join(lines), color=discord.Color(0x5865F2)
        )

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="card-progress", description="Check your card collection progress")
async def card_progress_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    data = await get_collection(str(interaction.user.id))
    owned: list[dict] = data["owned"]
    undiscovered: list[dict] = data["undiscovered"]

    unique_members_collected = len({c["member_id"] for c in owned})
    total_eligible = unique_members_collected + len(undiscovered)
    completion_pct = (
        round(unique_members_collected / total_eligible * 100) if total_eligible > 0 else 0
    )

    rarity_members: dict[str, set[str]] = {
        "common": set(),
        "uncommon": set(),
        "rare": set(),
        "legendary": set(),
    }
    member_rarities: dict[str, set[str]] = {}
    member_names: dict[str, str] = {}
    for card in owned:
        rarity_members[card["rarity"]].add(card["member_id"])
        member_rarities.setdefault(card["member_id"], set()).add(card["rarity"])
        member_names[card["member_id"]] = card["display_name"]

    per_rarity = {r: len(s) for r, s in rarity_members.items()}
    all_rarities = {"common", "uncommon", "rare", "legendary"}
    complete_sets = sorted(
        member_names[mid] for mid, r in member_rarities.items() if r >= all_rarities
    )

    embed = discord.Embed(title="Your Card Progress", color=discord.Color.blurple())
    embed.add_field(
        name="Collection",
        value=f"{unique_members_collected}/{total_eligible} members ({completion_pct}%)",
        inline=False,
    )
    embed.add_field(
        name="Members by Rarity",
        value=(
            f"Common: {per_rarity['common']} · "
            f"Uncommon: {per_rarity['uncommon']} · "
            f"Rare: {per_rarity['rare']} · "
            f"Legendary: {per_rarity['legendary']}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Complete Sets",
        value=", ".join(complete_sets) if complete_sets else "None yet",
        inline=False,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="card-fight", description="Challenge another player to a card battle")
@discord.app_commands.describe(
    opponent="The player to challenge",
    mode="Battle mode: quick (1v1) or extended (3v3)",
)
@discord.app_commands.choices(
    mode=[
        discord.app_commands.Choice(name="Quick (1v1)", value="quick"),
        discord.app_commands.Choice(name="Extended (3v3)", value="extended"),
    ]
)
async def card_fight_command(
    interaction: discord.Interaction,
    opponent: discord.Member,
    mode: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    challenger_id = str(interaction.user.id)
    opponent_id = str(opponent.id)

    if interaction.user.id == opponent.id:
        await interaction.followup.send("You can't fight yourself.", ephemeral=True)
        return
    if opponent.bot:
        await interaction.followup.send("You can't challenge a bot.", ephemeral=True)
        return
    if not interaction.channel:
        await interaction.followup.send(
            "This command must be used in a server channel.", ephemeral=True
        )
        return

    fight = await create_fight(
        challenger_id=challenger_id,
        opponent_id=opponent_id,
        mode=mode,
        channel_id=str(interaction.channel_id),
    )

    view = FightChallengeView(
        fight_id=fight.id,
        challenger_id=challenger_id,
        opponent_id=opponent_id,
        challenger_name=interaction.user.display_name,
        mode=mode,
    )
    if not isinstance(interaction.channel, discord.abc.Messageable):
        await interaction.followup.send(
            "This command must be used in a server channel.", ephemeral=True
        )
        return
    channel_msg = await interaction.channel.send(
        content=(
            f"{opponent.mention}, **{interaction.user.display_name}** challenges you to a "
            f"**{mode.upper()} Battle**!\n\n"
            f"You have {FIGHT_TOKEN_EXPIRY_MINUTES} minutes to respond."
        ),
        view=view,
    )
    view.message = channel_msg
    await interaction.followup.send("Challenge sent!", ephemeral=True)


@bot.tree.command(name="card-fight-leaderboard", description="Show the top 10 fight stats")
@discord.app_commands.describe(sort_by="What to rank players by")
@discord.app_commands.choices(
    sort_by=[
        discord.app_commands.Choice(name="Most Wins", value="wins"),
        discord.app_commands.Choice(name="Best Win Rate", value="win_rate"),
        discord.app_commands.Choice(name="Most Fights Played", value="fights_played"),
        discord.app_commands.Choice(name="Pringle Balance", value="pringle_balance"),
        discord.app_commands.Choice(name="Most Escapes", value="escapes"),
    ]
)
async def card_fight_leaderboard_command(
    interaction: discord.Interaction,
    sort_by: str = "wins",
) -> None:
    await interaction.response.defer()
    rows = await get_fight_leaderboard(sort_by)

    title_map = {
        "wins": "Most Wins",
        "win_rate": "Best Win Rate",
        "fights_played": "Most Fights Played",
        "pringle_balance": "Pringle Balance",
        "escapes": "Most Escapes",
    }
    unit_map = {
        "wins": "wins",
        "fights_played": "fights played",
        "pringle_balance": "Pringles",
        "escapes": "escapes",
    }
    title = f"Fight Leaderboard — {title_map.get(sort_by, 'Most Wins')}"

    if not rows:
        embed = discord.Embed(
            title=title,
            description="No data yet!",
            color=discord.Color(0x5865F2),
        )
    elif sort_by == "win_rate":
        lines = [
            f"{rank}. {row['display_name']} — "
            f"{round(row['total'] * 100)}% ({row['total_fights']} fights)"
            for rank, row in enumerate(rows, start=1)
        ]
        embed = discord.Embed(
            title=title, description="\n".join(lines), color=discord.Color(0x5865F2)
        )
    else:
        unit = unit_map.get(sort_by, "")
        lines = [
            f"{rank}. {row['display_name']} — {row['total']} {unit}"
            for rank, row in enumerate(rows, start=1)
        ]
        embed = discord.Embed(
            title=title, description="\n".join(lines), color=discord.Color(0x5865F2)
        )

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="card-shop", description="Browse or buy items from the Pringle shop")
@discord.app_commands.describe(action="list: show items, buy: purchase an item")
@discord.app_commands.choices(
    action=[
        discord.app_commands.Choice(name="list", value="list"),
    ]
)
async def card_shop_command(interaction: discord.Interaction, action: str = "list") -> None:
    await interaction.response.defer(ephemeral=True)
    player_id = str(interaction.user.id)
    balance = await get_balance(player_id)
    items_owned = await get_player_items(player_id)

    lines = [f"**Pringle Balance:** {balance} 🟣\n", "**Item Shop:**"]
    for item_type, cost in ITEM_COSTS.items():
        owned_qty = items_owned.get(item_type, 0)
        lines.append(
            f"• **{ITEM_NAMES[item_type]}** — {cost} Pringles — {ITEM_DESCRIPTIONS[item_type]}"
            f"  *(you have {owned_qty})*"
        )
    lines.append("\nUse `/card-shop-buy <item>` to purchase.")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


@bot.tree.command(name="card-shop-buy", description="Buy an item from the Pringle shop")
@discord.app_commands.describe(item="Item to purchase")
@discord.app_commands.choices(
    item=[
        discord.app_commands.Choice(name="Heal Potion (50 🟣)", value="heal_potion"),
        discord.app_commands.Choice(name="Super Potion (100 🟣)", value="super_potion"),
        discord.app_commands.Choice(name="Bringus Boost (75 🟣)", value="bringus_boost"),
        discord.app_commands.Choice(name="Smoke Screen (60 🟣)", value="smoke_screen"),
    ]
)
async def card_shop_buy_command(interaction: discord.Interaction, item: str) -> None:
    await interaction.response.defer(ephemeral=True)
    player_id = str(interaction.user.id)
    success, reason = await buy_item(player_id, item)
    if success:
        balance = await get_balance(player_id)
        await interaction.followup.send(
            f"Purchased **{ITEM_NAMES[item]}**! Remaining balance: {balance} Pringles 🟣",
            ephemeral=True,
        )
    else:
        msg = {
            "unknown_item": "Unknown item.",
            "insufficient_pringles": "Not enough Pringles.",
        }.get(reason, "Purchase failed.")
        await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(
    name="card-pringles",
    description="Check your Pringle balance or trade in for a card draw",
)
@discord.app_commands.describe(
    action="balance: show balance, trade-in: spend 100 Pringles for a card draw",
)
@discord.app_commands.choices(
    action=[
        discord.app_commands.Choice(name="balance", value="balance"),
        discord.app_commands.Choice(name="trade-in (100 Pringles → 1 draw)", value="trade-in"),
    ]
)
async def card_pringles_command(interaction: discord.Interaction, action: str = "balance") -> None:
    await interaction.response.defer(ephemeral=True)
    player_id = str(interaction.user.id)

    if action == "balance":
        balance = await get_balance(player_id)
        items = await get_player_items(player_id)
        item_lines = [f"• {ITEM_NAMES[k]}: {v}" for k, v in items.items()] or ["• No items"]
        await interaction.followup.send(
            f"**Pringle Balance:** {balance} 🟣\n\n**Items:**\n" + "\n".join(item_lines),
            ephemeral=True,
        )
    elif action == "trade-in":
        balance = await get_balance(player_id)
        if balance < 100:
            await interaction.followup.send(
                f"You need 100 Pringles to trade in for a card draw. You have {balance}.",
                ephemeral=True,
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE members SET pringle_balance = pringle_balance - 100 WHERE discord_id = ?",
                (player_id,),
            )
            await db.commit()

        is_super_pal = any(
            r.name == superpal_static.SUPER_PAL_ROLE_NAME
            for r in getattr(interaction.user, "roles", [])
        )
        max_draws = 10 if is_super_pal else 5
        card = await draw_card(
            owner_id=player_id, max_draws=max_draws + 1, drawn_by_name=interaction.user.display_name
        )
        if card is None:
            # Refund if draw fails (shouldn't happen but be safe)
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE members SET pringle_balance = pringle_balance + 100 "
                    "WHERE discord_id = ?",
                    (player_id,),
                )
                await db.commit()
            await interaction.followup.send(
                "Could not draw a card (something went wrong). Pringles refunded.", ephemeral=True
            )
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT display_name, avatar_url, bio, stats FROM members WHERE discord_id = ?",
                (card.card_member_id,),
            ) as cur:
                row = await cur.fetchone()

        embed = build_card_embed(
            display_name=row[0] if row else "Unknown",
            avatar_url=_resolve_avatar_url(row[1] if row else None),
            rarity=card.rarity,
            card_number=card.id,
            drawn_by=card.drawn_by_name or interaction.user.display_name,
            bio=row[2] if row else None,
            stats_pairs=_parse_stats(row[3] if row else None),
        )
        new_balance = await get_balance(player_id)
        await interaction.followup.send(
            f"Spent 100 Pringles for a card draw! Remaining: {new_balance} 🟣",
            embed=embed,
            ephemeral=True,
        )


@bot.tree.command(
    name="admin-link",
    description="Get a private admin dashboard link (The Clippy only)",
)
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
            "Here's your private admin dashboard link "
            f"(valid for 24 hours after first click):\n{url}"
        )
        await interaction.response.send_message(
            "Check your DMs for your admin link!", ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "I couldn't send you a DM. Please enable DMs from server members and try again.",
            ephemeral=True,
        )


@bot.tree.command(
    name="announce",
    description="Post a message to the Super Pal channel (The Clippy only)",
)
@discord.app_commands.describe(message="The message to post to the channel")
async def announce_command(interaction: discord.Interaction, message: str) -> None:
    member = interaction.user
    role_ids = [r.id for r in getattr(member, "roles", [])]
    if CLIPPY_ROLE_ID not in role_ids:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return
    channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
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
@tasks.loop(hours=24 * 7)
async def super_pal_of_the_week():
    """Weekly task to choose a new Super Pal of the Week."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID or 0)
        if not guild:
            log.error(f"Could not find guild with ID {superpal_env.GUILD_ID}")
            return

        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
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
        if len(guild.members) < (guild.member_count or 0):
            log.warning(
                "Member cache may be incomplete! Some users may be excluded from selection."
            )

        # Exclude current super pal so they can't be re-selected
        eligible_members = [m for m in true_member_list if role not in m.roles]
        if not eligible_members:
            log.error("No eligible members for super pal selection (all members already have role)")
            return

        new_super_pal = secrets.choice(eligible_members)
        log.info(f"Selected new super pal of the week: {new_super_pal.name}")

        # Remove role from all current super pals
        for member in true_member_list:
            if role in member.roles:
                await member.remove_roles(role)
                log.info(f"{member.name} removed from super pal role")

        # Add role to new super pal
        await new_super_pal.add_roles(role)
        log.info(f"{new_super_pal.name} promoted to super pal")

        await channel.send(
            f"Congratulations to {new_super_pal.mention}, "
            f"the super pal of the week! {superpal_static.WELCOME_MSG}"
        )

    except Exception as e:
        log.error(f"Error in super_pal_of_the_week task: {e}")


@tasks.loop(hours=24 * 7)
async def heal_potion_reset():
    """Reset Heal Potions to 2 for players with 0 every Sunday at noon UTC."""
    try:
        count = await reset_heal_potions_for_empty_players()
        log.info("Heal potion reset: %d players topped up", count)
    except Exception as e:
        log.error("Error in heal_potion_reset: %s", e)


@heal_potion_reset.before_loop
async def before_heal_potion_reset():
    await bot.wait_until_ready()
    try:
        target = next_sunday_noon_utc()
        delta = target - datetime.datetime.now(datetime.timezone.utc)
        log.info("Heal potion reset: sleeping for %s. Will wake up Sunday at 12PM UTC.", delta)
        await asyncio.sleep(delta.total_seconds())
    except Exception as e:
        log.error("Error in before_heal_potion_reset: %s", e)


@tasks.loop(hours=24)
async def daily_boin_grant():
    """Award daily boins to all guild members at noon UTC."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID or 0)
        if not guild:
            log.error("daily_boin_grant: could not find guild")
            return
        member_ids = [str(m.id) for m in get_non_bot_members(guild)]
        results = await award_daily_to_all(member_ids)
        log.info("Daily boin grant: awarded to %d members", len(results))
    except Exception as e:
        log.error("Error in daily_boin_grant: %s", e)


@daily_boin_grant.before_loop
async def before_daily_boin_grant():
    await bot.wait_until_ready()
    try:
        delta = next_noon_utc() - datetime.datetime.now(datetime.timezone.utc)
        log.info("Daily boin grant: sleeping %s until noon UTC", delta)
        await asyncio.sleep(delta.total_seconds())
    except Exception as e:
        log.error("Error in before_daily_boin_grant: %s", e)


@super_pal_of_the_week.before_loop
async def before_super_pal_of_the_week():
    await bot.wait_until_ready()
    try:
        target = next_sunday_noon_utc()
        delta = target - datetime.datetime.now(datetime.timezone.utc)
        log.info("Super pal task: sleeping for %s. Will wake up Sunday at 12PM UTC.", delta)
        await asyncio.sleep(delta.total_seconds())
    except Exception as e:
        log.error("Error in before_super_pal_of_the_week: %s", e)


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
    log.info(f"Bot logged in as {bot.user}")
    log.info(f"Connected to {len(bot.guilds)} guilds")

    try:
        await bot.tree.sync()
        log.info("Slash commands synced")
    except Exception as e:
        log.error(f"Error syncing slash commands: {e}")

    await init_db()
    guild = bot.get_guild(superpal_env.GUILD_ID or 0)
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
        global _guild_members_cache
        _guild_members_cache = members_data
        await sync_members(members_data)
        log.info("Synced %d members to card DB", len(members_data))

    if not super_pal_of_the_week.is_running():
        super_pal_of_the_week.start()
        log.info("Weekly task started")

    if not heal_potion_reset.is_running():
        heal_potion_reset.start()
        log.info("Heal potion reset task started")

    if not daily_boin_grant.is_running():
        daily_boin_grant.start()
        log.info("Daily boin grant task started")


@bot.event
async def on_message(message: discord.Message):
    """Handle incoming messages, including Spin The Wheel integration."""
    try:
        # Skip bot messages
        if message.author.bot:
            # Check if this is from Spin The Wheel bot
            guild = bot.get_guild(superpal_env.GUILD_ID or 0)
            if not guild:
                await bot.process_commands(message)
                return

            spin_the_wheel_role = discord.utils.get(
                guild.roles, name=superpal_static.SPIN_THE_WHEEL_ROLE_NAME
            )
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

            if len(embed.description) > 0 and embed.description[0] == "🏆":
                super_pal_role = get_super_pal_role(guild)
                if not super_pal_role:
                    return

                # Grab winner name from Spin the Wheel message
                new_super_pal_name = embed.description[12:-2]
                new_super_pal = discord.utils.get(guild.members, name=new_super_pal_name)

                if not new_super_pal:
                    log.error(f"Could not find member: {new_super_pal_name}")
                    return

                log.info(f"{new_super_pal.name} was chosen by wheel spin")

                # Remove existing Super Pal of the Week
                true_member_list = get_non_bot_members(guild)
                for member in true_member_list:
                    if super_pal_role in member.roles:
                        await member.remove_roles(super_pal_role)

                # Add new winner to Super Pal of the Week
                await new_super_pal.add_roles(super_pal_role)

                await message.channel.send(
                    f"Congratulations {new_super_pal.mention}! "
                    f"You have been promoted to super pal of the week by wheel spin. "
                    f"{superpal_static.WELCOME_MSG}"
                )

    except Exception as e:
        log.error(f"Error handling spin the wheel message: {e}")


################
# Bot commands #
################
@bot.command(name="spotw", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def spotw_command(ctx, new_super_pal: discord.Member):
    """Promote users to Super Pal of the Week (legacy command)."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID or 0)
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))

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
            log.info(f"{new_super_pal.name} promoted by {current_super_pal.name}")
            await new_super_pal.add_roles(role)
            await current_super_pal.remove_roles(role)
            await channel.send(
                f"Congratulations {new_super_pal.mention}! "
                f"You have been promoted to super pal of the week by {current_super_pal.name}. "
                f"{superpal_static.WELCOME_MSG}"
            )
        else:
            await ctx.send(f"{new_super_pal.mention} is already super pal of the week.")

    except Exception as e:
        log.error(f"Error in spotw command: {e}")
        await ctx.send("Sorry, there was an error processing your request.")


@bot.command(name="spinthewheel", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def spinthewheel(ctx):
    """Spin the wheel for a random Super Pal of the Week."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID or 0)
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))

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
        await channel.send(f"?pick {true_name_str}")
        log.info("Spinning the wheel for new super pal")

    except Exception as e:
        log.error(f"Error in spinthewheel command: {e}")
        await ctx.send("Sorry, there was an error spinning the wheel.")


@bot.command(name="commands", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def list_commands(ctx):
    """Display information about available commands."""
    try:
        log.info(f"{ctx.message.author.name} used help command")
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
        if channel:
            await channel.send(superpal_static.COMMANDS_MSG)
        else:
            await ctx.send(superpal_static.COMMANDS_MSG)

    except Exception as e:
        log.error(f"Error in commands command: {e}")
        await ctx.send("Sorry, there was an error displaying commands.")


@bot.command(name="cacaw", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def cacaw(ctx):
    """Send party parrot discord emoji."""
    try:
        log.info(f"{ctx.message.author.name} used cacaw command")
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
        emoji_guild = bot.get_guild(superpal_env.EMOJI_GUILD_ID or 0)

        if not emoji_guild:
            await ctx.send("Error: Emoji guild not found.")
            return

        partyparrot_emoji = discord.utils.get(emoji_guild.emojis, name="partyparrot")

        if partyparrot_emoji and channel:
            await channel.send(str(partyparrot_emoji) * superpal_static.EMOJI_SPAM_COUNT)
        else:
            await ctx.send("Partyparrot emoji not found!")

    except Exception as e:
        log.error(f"Error in cacaw command: {e}")
        await ctx.send("Sorry, there was an error.")


@bot.command(name="meow", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def meow(ctx):
    """Send party cat discord emoji."""
    try:
        log.info(f"{ctx.message.author.name} used meow command")
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
        emoji_guild = bot.get_guild(superpal_env.EMOJI_GUILD_ID or 0)

        if not emoji_guild:
            await ctx.send("Error: Emoji guild not found.")
            return

        partymeow_emoji = discord.utils.get(emoji_guild.emojis, name="partymeow")

        if partymeow_emoji and channel:
            await channel.send(str(partymeow_emoji) * superpal_static.EMOJI_SPAM_COUNT)
        else:
            await ctx.send("Partymeow emoji not found!")

    except Exception as e:
        log.error(f"Error in meow command: {e}")
        await ctx.send("Sorry, there was an error.")


@bot.command(name="karatechop", pass_context=True)
@commands.has_role(superpal_static.SUPER_PAL_ROLE_NAME)
async def karate_chop(ctx):
    """Randomly remove one user from voice chat."""
    try:
        guild = bot.get_guild(superpal_env.GUILD_ID or 0)
        channel = cast(discord.TextChannel | None, bot.get_channel(superpal_env.CHANNEL_ID or 0))
        current_super_pal = ctx.message.author

        if not guild or not channel:
            await ctx.send("Error: Could not find guild or channel.")
            return

        active_members = [voice_channel.members for voice_channel in guild.voice_channels]

        # Check if anyone is in voice channels
        if not any(active_members):
            log.info(f"{current_super_pal.name} used karate chop, but no one is in voice channels")
            await channel.send(f"There is no one to karate chop, {current_super_pal.mention}!")
            return

        # Flatten user list, filter out bots, and choose random user
        def flatten(nested):
            return [x for y in nested for x in y]

        true_member_list = [m for m in flatten(active_members) if not m.bot]

        if not true_member_list:
            await channel.send("No users found in voice channels!")
            return

        chopped_member = secrets.choice(true_member_list)
        log.info(f"{chopped_member.name} karate chopped")

        # Check that an 'AFK' channel exists
        afk_channels = [
            c for c in guild.voice_channels if superpal_static.AFK_CHANNEL_KEYWORD in c.name
        ]

        if afk_channels:
            await chopped_member.move_to(afk_channels[0])
            await channel.send(f"karate chopped {chopped_member.mention}!")
        else:
            await channel.send(
                f"{chopped_member.mention} would have been chopped, "
                "but an AFK channel was not found.\n"
                "Please complain to the server owner."
            )

    except Exception as e:
        log.error(f"Error in karate_chop command: {e}")
        await ctx.send("Sorry, there was an error processing karate chop.")


#####################
# Palymarket commands
#####################
@bot.tree.command(name="palymarket-propose", description="Propose a new prediction market")
@app_commands.describe(title="Short title for the market", description="Full description")
async def palymarket_propose(
    interaction: discord.Interaction, title: str, description: str
) -> None:
    await interaction.response.defer(ephemeral=True)
    market = await palymarket_svc.propose_market(title, description, str(interaction.user.id))
    await interaction.followup.send(
        f"Market proposed! Admins will review it shortly. ID: {market.id}", ephemeral=True
    )
    if isinstance(interaction.channel, discord.abc.Messageable):
        await interaction.channel.send(
            f"📊 New market proposed by {interaction.user.mention}: "
            f"**{market.title}** (ID: {market.id}) — awaiting admin approval"
        )


@bot.tree.command(name="palymarket-bet", description="Place or update a bet on a market")
@app_commands.describe(market_id="Market ID to bet on", amount="Amount of Palycoins to bet")
@app_commands.choices(
    side=[
        app_commands.Choice(name="Yes", value="yes"),
        app_commands.Choice(name="No", value="no"),
    ]
)
async def palymarket_bet(
    interaction: discord.Interaction,
    market_id: int,
    side: app_commands.Choice[str],
    amount: app_commands.Range[int, 1],
) -> None:
    await interaction.response.defer(ephemeral=True)
    await palymarket_svc.get_palycoin_balance(str(interaction.user.id))
    success, reason = await palymarket_svc.place_or_update_bet(
        market_id, str(interaction.user.id), side.value, amount
    )
    if success:
        await interaction.followup.send(
            f"Bet placed! {amount} Palycoins on {side.value.upper()} for market #{market_id}",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(reason, ephemeral=True)


@bot.tree.command(name="palymarket-list", description="List all open prediction markets")
async def palymarket_list(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    markets = await palymarket_svc.list_markets(status="open")
    if not markets:
        await interaction.followup.send("No open markets right now.", ephemeral=True)
        return
    embed = discord.Embed(title="📊 Open Palymarkets")
    for m in markets:
        embed.add_field(
            name=f"#{m.id}: {m.title}",
            value=f"YES: {m.yes_pool} | NO: {m.no_pool}",
            inline=False,
        )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="palymarket-balance", description="Check your Palycoin balance and bets")
async def palymarket_balance(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    balance = await palymarket_svc.get_palycoin_balance(str(interaction.user.id))
    active_bets = await palymarket_svc.get_player_active_bets(str(interaction.user.id))
    embed = discord.Embed(title="📊 Palycoin Balance")
    embed.add_field(name="Balance", value=f"{balance} Palycoins", inline=False)
    if active_bets:
        bet_lines = [
            f"#{market.id} **{market.title}**: {bet.amount} on {bet.side.upper()}"
            for market, bet in active_bets
        ]
        embed.add_field(name="Active Bets", value="\n".join(bet_lines), inline=False)
    else:
        embed.add_field(name="Active Bets", value="None", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="palymarket-exchange", description="Exchange Pringles for Palycoins")
@app_commands.describe(amount="Pringles to spend (2 Pringles → 1 Palycoin)")
async def palymarket_exchange(interaction: discord.Interaction, amount: int) -> None:
    await interaction.response.defer(ephemeral=True)
    success, reason, received = await exchange_service.exchange(
        str(interaction.user.id), exchange_service.PRINGLES, exchange_service.PALYCOINS, amount
    )
    if success:
        await interaction.followup.send(
            f"Exchanged {amount} Pringles for {received} Palycoins!", ephemeral=True
        )
    elif reason == "amount_too_small":
        await interaction.followup.send(
            "You need at least 2 Pringles to exchange for 1 Palycoin.", ephemeral=True
        )
    else:
        await interaction.followup.send("Not enough Pringles.", ephemeral=True)


@bot.tree.command(name="palymarket-approve", description="[Admin] Approve a pending market")
@app_commands.describe(market_id="Market ID to approve")
@app_commands.check(_is_clippy)
async def palymarket_approve(interaction: discord.Interaction, market_id: int) -> None:
    await interaction.response.defer(ephemeral=True)
    success, reason = await palymarket_svc.approve_market(market_id, str(interaction.user.id))
    if success:
        await interaction.followup.send(f"Market #{market_id} approved.", ephemeral=True)
        if isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.channel.send(
                f"📊 Market #{market_id} is now OPEN for betting!"
            )
    else:
        await interaction.followup.send(
            f"Could not approve market #{market_id}: {reason}", ephemeral=True
        )


@bot.tree.command(name="palymarket-reject", description="[Admin] Reject a pending market")
@app_commands.describe(market_id="Market ID to reject", reason="Reason for rejection")
@app_commands.check(_is_clippy)
async def palymarket_reject(
    interaction: discord.Interaction, market_id: int, reason: str
) -> None:
    await interaction.response.defer(ephemeral=True)
    success, _ = await palymarket_svc.reject_market(market_id, str(interaction.user.id))
    if success:
        await interaction.followup.send(f"Market #{market_id} rejected.", ephemeral=True)
    else:
        await interaction.followup.send(
            f"Could not reject market #{market_id}.", ephemeral=True
        )


@bot.tree.command(name="palymarket-close", description="[Admin] Close a market to new bets")
@app_commands.describe(market_id="Market ID to close")
@app_commands.check(_is_clippy)
async def palymarket_close(interaction: discord.Interaction, market_id: int) -> None:
    await interaction.response.defer(ephemeral=True)
    success, reason = await palymarket_svc.close_market(market_id, str(interaction.user.id))
    if success:
        await interaction.followup.send(f"Market #{market_id} closed.", ephemeral=True)
        if isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.channel.send(
                f"📊 Market #{market_id} is now CLOSED. No more bets accepted."
            )
    else:
        await interaction.followup.send(
            f"Could not close market #{market_id}: {reason}", ephemeral=True
        )


@bot.tree.command(name="palymarket-resolve", description="[Admin] Resolve a market and pay winners")
@app_commands.describe(market_id="Market ID to resolve")
@app_commands.choices(
    outcome=[
        app_commands.Choice(name="Yes", value="yes"),
        app_commands.Choice(name="No", value="no"),
    ]
)
@app_commands.check(_is_clippy)
async def palymarket_resolve(
    interaction: discord.Interaction,
    market_id: int,
    outcome: app_commands.Choice[str],
) -> None:
    await interaction.response.defer(ephemeral=True)
    result = await palymarket_svc.resolve_market(market_id, outcome.value, str(interaction.user.id))
    if "error" in result:
        await interaction.followup.send(
            f"Could not resolve market #{market_id}: {result['error']}", ephemeral=True
        )
        return
    await interaction.followup.send(f"Market #{market_id} resolved.", ephemeral=True)
    if isinstance(interaction.channel, discord.abc.Messageable):
        embed = discord.Embed(title=f"📊 Market #{market_id} Resolved")
        embed.add_field(name="Outcome", value=result["outcome"].upper(), inline=True)
        embed.add_field(name="Total Pool", value=str(result["total_pool"]), inline=True)
        embed.add_field(name="Winners", value=str(result["winner_count"]), inline=True)
        top_payouts = sorted(result["payouts"], key=lambda p: p["payout"], reverse=True)[:5]
        if top_payouts:
            payout_lines = [
                f"<@{p['player_id']}> → {p['payout']} Palycoins" for p in top_payouts
            ]
            embed.add_field(name="Top Payouts", value="\n".join(payout_lines), inline=False)
        await interaction.channel.send(embed=embed)


#####################
# Economy commands  #
#####################
@bot.tree.command(name="pal-balance", description="Show your Boins, Pringles, and Palycoins")
async def pal_balance(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    player_id = str(interaction.user.id)
    boins = await boin_service.get_balance(player_id)
    pringles = await get_balance(player_id)
    palycoins = await palymarket_svc.get_palycoin_balance(player_id)
    embed = discord.Embed(title="Your Wallet", color=0x5865F2)
    embed.add_field(name="🪙 Boins", value=str(boins), inline=True)
    embed.add_field(name="🥫 Pringles", value=str(pringles), inline=True)
    embed.add_field(name="📈 Palycoins", value=str(palycoins), inline=True)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="pal-exchange", description="Exchange between Boins, Pringles, and Palycoins")
@app_commands.describe(
    from_currency="Currency to spend",
    to_currency="Currency to receive",
    amount="Amount to exchange",
)
@app_commands.choices(
    from_currency=[
        app_commands.Choice(name="Boins", value="boins"),
        app_commands.Choice(name="Pringles", value="pringles"),
        app_commands.Choice(name="Palycoins", value="palycoins"),
    ],
    to_currency=[
        app_commands.Choice(name="Boins", value="boins"),
        app_commands.Choice(name="Pringles", value="pringles"),
        app_commands.Choice(name="Palycoins", value="palycoins"),
    ],
)
async def pal_exchange(
    interaction: discord.Interaction,
    from_currency: str,
    to_currency: str,
    amount: int,
) -> None:
    await interaction.response.defer(ephemeral=True)
    if from_currency == to_currency:
        await interaction.followup.send("Cannot exchange a currency for itself.", ephemeral=True)
        return
    success, reason, received = await exchange_service.exchange(
        str(interaction.user.id), from_currency, to_currency, amount
    )
    if success:
        await interaction.followup.send(
            f"Exchanged {amount} {from_currency.capitalize()} for {received} {to_currency.capitalize()}!",
            ephemeral=True,
        )
    elif reason == "amount_too_small":
        await interaction.followup.send(
            f"Amount too small — you would receive 0 {to_currency}.", ephemeral=True
        )
    elif reason == "insufficient_balance":
        await interaction.followup.send(f"Not enough {from_currency}.", ephemeral=True)
    else:
        await interaction.followup.send(f"Exchange failed: {reason}.", ephemeral=True)


@bot.tree.command(name="pal-dice", description="Roll dice against the bot and bet Boins")
@app_commands.describe(bet="Boins to wager (minimum 10)")
async def pal_dice(interaction: discord.Interaction, bet: int) -> None:
    await interaction.response.defer(ephemeral=True)
    result = await game_service.play_dice(str(interaction.user.id), bet)
    if "error" in result:
        await interaction.followup.send(_game_error(result["error"]), ephemeral=True)
        return
    outcome_str = {"win": "You win!", "tie": "Tie — bet returned.", "lose": "You lose."}[result["outcome"]]
    embed = discord.Embed(title="🎲 Dice Roll", color=_outcome_color(result["outcome"]))
    embed.add_field(name="Your Roll", value=str(result["player_roll"]), inline=True)
    embed.add_field(name="Bot Roll", value=str(result["bot_roll"]), inline=True)
    embed.add_field(name="Result", value=f"{outcome_str} ({_net_str(result['net'])} Boins)", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="pal-rps", description="Rock, paper, scissors against the bot for Boins")
@app_commands.describe(choice="Your pick", bet="Boins to wager (minimum 10)")
@app_commands.choices(
    choice=[
        app_commands.Choice(name="Rock", value="rock"),
        app_commands.Choice(name="Paper", value="paper"),
        app_commands.Choice(name="Scissors", value="scissors"),
    ]
)
async def pal_rps(interaction: discord.Interaction, choice: str, bet: int) -> None:
    await interaction.response.defer(ephemeral=True)
    result = await game_service.play_rps(str(interaction.user.id), choice, bet)
    if "error" in result:
        await interaction.followup.send(_game_error(result["error"]), ephemeral=True)
        return
    outcome_str = {"win": "You win!", "tie": "Tie — bet returned.", "lose": "You lose."}[result["outcome"]]
    embed = discord.Embed(title="✂️ Rock Paper Scissors", color=_outcome_color(result["outcome"]))
    embed.add_field(name="Your Pick", value=choice.capitalize(), inline=True)
    embed.add_field(name="Bot Pick", value=result["bot_choice"].capitalize(), inline=True)
    embed.add_field(name="Result", value=f"{outcome_str} ({_net_str(result['net'])} Boins)", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="pal-roulette", description="Spin the roulette wheel and bet Boins")
@app_commands.describe(bet_type="What to bet on", bet="Boins to wager (minimum 10)")
@app_commands.choices(
    bet_type=[
        app_commands.Choice(name="Red (2×)", value="red"),
        app_commands.Choice(name="Black (2×)", value="black"),
        app_commands.Choice(name="Green / 0 (14×)", value="green"),
        app_commands.Choice(name="1st Dozen 1–12 (3×)", value="1st dozen"),
        app_commands.Choice(name="2nd Dozen 13–24 (3×)", value="2nd dozen"),
        app_commands.Choice(name="3rd Dozen 25–36 (3×)", value="3rd dozen"),
    ]
)
async def pal_roulette(interaction: discord.Interaction, bet_type: str, bet: int) -> None:
    await interaction.response.defer(ephemeral=True)
    result = await game_service.play_roulette(str(interaction.user.id), bet_type, bet)
    if "error" in result:
        await interaction.followup.send(_game_error(result["error"]), ephemeral=True)
        return
    outcome_str = {"win": "You win!", "lose": "You lose."}[result["outcome"]]
    embed = discord.Embed(title="🎰 Roulette", color=_outcome_color(result["outcome"]))
    embed.add_field(name="Spin", value=str(result["spin"]), inline=True)
    embed.add_field(name="Bet", value=bet_type.capitalize(), inline=True)
    embed.add_field(name="Result", value=f"{outcome_str} ({_net_str(result['net'])} Boins)", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="pal-guess", description="Guess a number 1–10 and bet Boins")
@app_commands.describe(number="Your guess (1–10)", bet="Boins to wager (minimum 10)")
async def pal_guess(
    interaction: discord.Interaction,
    number: app_commands.Range[int, 1, 10],
    bet: int,
) -> None:
    await interaction.response.defer(ephemeral=True)
    result = await game_service.play_guess(str(interaction.user.id), number, bet)
    if "error" in result:
        await interaction.followup.send(_game_error(result["error"]), ephemeral=True)
        return
    outcome_str = {"win": "Correct! You win!", "lose": "Wrong number. You lose."}[result["outcome"]]
    embed = discord.Embed(title="🔢 Number Guess", color=_outcome_color(result["outcome"]))
    embed.add_field(name="Your Guess", value=str(number), inline=True)
    embed.add_field(name="Bot's Number", value=str(result["bot_number"]), inline=True)
    embed.add_field(name="Result", value=f"{outcome_str} ({_net_str(result['net'])} Boins)", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


def _outcome_color(outcome: str) -> int:
    return {
        "win": 0x3BA55C,
        "tie": 0xFAA61A,
        "lose": 0xED4245,
    }.get(outcome, 0x5865F2)


def _net_str(net: int) -> str:
    return f"+{net}" if net >= 0 else str(net)


def _game_error(reason: str) -> str:
    if reason.startswith("minimum_bet_"):
        minimum = reason.split("_")[-1]
        return f"Minimum bet is {minimum} Boins."
    if reason == "insufficient_boins":
        return "Not enough Boins."
    return f"Error: {reason}"


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    if isinstance(error, app_commands.MissingRole):
        if interaction.response.is_done():
            await interaction.followup.send(
                "You don't have permission to use this command.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
    else:
        log.exception("Unhandled app command error", exc_info=error)
        raise error


################
# Start the bot
################
async def _main() -> None:
    from superpal.env import WEBAPP_HOST, WEBAPP_PORT
    from superpal.webapp.app import create_app

    webapp = create_app()
    config = uvicorn.Config(webapp, host=WEBAPP_HOST, port=WEBAPP_PORT, log_level="info")
    server = uvicorn.Server(config)
    assert superpal_env.TOKEN is not None, "SUPERPAL_TOKEN is required to start the bot"
    async with bot:
        await asyncio.gather(
            bot.start(superpal_env.TOKEN),
            server.serve(),
        )


if __name__ == "__main__":
    asyncio.run(_main())
