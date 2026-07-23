"""Discord notification seam between the webapp and the bot.

The webapp imports this module instead of bot.py, avoiding a circular
import; bot.py registers itself here at startup. Every function no-ops
when no bot is registered (e.g. webapp running standalone or in tests).
"""

import discord
from discord.ext import commands

import superpal.env as superpal_env
from superpal.cards.fight_service import fight_ended_by_escape, get_fight
from superpal.cards.models import RARITY_LABELS
from superpal.cards.service import (
    get_member_display_name,
    get_offer_by_id,
    get_offer_discord_message_id,
    set_offer_discord_message_id,
)
from superpal.env import WEBAPP_BASE_URL

log = superpal_env.log

_bot: commands.Bot | None = None
_guild_members_cache: list[dict] | None = None


def set_bot(bot: commands.Bot) -> None:
    global _bot
    _bot = bot


def set_guild_members_cache(members: list[dict]) -> None:
    global _guild_members_cache
    _guild_members_cache = members


def get_guild_members_cache() -> list[dict] | None:
    return _guild_members_cache


async def notify_trade_offer(offer_id: int) -> None:
    """DM the listing owner about a new marketplace offer."""
    if _bot is None:
        return
    offer = await get_offer_by_id(offer_id)
    if offer is None:
        return
    guild = _bot.get_guild(int(superpal_env.GUILD_ID))
    if guild is None:
        return
    member = guild.get_member(int(offer.listing.owner_id))
    if member is None:
        return
    offer_names = [
        f"{RARITY_LABELS[item.rarity]} "
        f"{await get_member_display_name(item.member_id) or item.member_id}"
        for item in offer.items
    ]
    listing_names = [
        f"{RARITY_LABELS[item.rarity]} "
        f"{await get_member_display_name(item.member_id) or item.member_id}"
        for item in offer.listing.items
    ]
    from superpal.cogs.cards import TradeOfferView

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


async def announce_fight_result(fight_id: int) -> None:
    """Post a completed fight's result to the Discord channel it was started from."""
    if _bot is None:
        return
    fight = await get_fight(fight_id)
    if (
        fight is None
        or fight.status != "completed"
        or not fight.channel_id
        or not fight.winner_id
    ):
        return
    channel = _bot.get_channel(int(fight.channel_id))
    if not isinstance(channel, discord.abc.Messageable):
        return

    winner_name = await get_member_display_name(fight.winner_id) or fight.winner_id
    loser_id = (
        fight.opponent_id if fight.winner_id == fight.challenger_id else fight.challenger_id
    )
    loser_name = await get_member_display_name(loser_id) or loser_id
    escaped = await fight_ended_by_escape(fight_id)

    if escaped:
        headline = (
            f"🏃 **{loser_name}** fled the battle — **{winner_name}** wins by default!"
        )
    else:
        headline = f"🏆 **{winner_name}** defeated **{loser_name}**!"

    stakes = "50 Pringles transferred to the winner"
    if fight.mode == "extended":
        stakes += " · +25 participation bonus for both players"
    if escaped:
        stakes += " · 25 Pringle escape penalty"

    embed = discord.Embed(
        title=f"{fight.mode.capitalize()} Battle Complete",
        description=f"{headline}\n\n🥫 {stakes}",
        color=0x3BA55C,
    )
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as e:
        log.error("Failed to announce fight %d result: %s", fight_id, e)


async def edit_offer_dm(offer_id: int, message: str) -> None:
    """Edit the DM notification for an offer after web-UI accept/decline."""
    if _bot is None:
        return
    offer = await get_offer_by_id(offer_id)
    if offer is None:
        return
    discord_message_id = await get_offer_discord_message_id(offer_id)
    if not discord_message_id:
        return
    guild = _bot.get_guild(int(superpal_env.GUILD_ID))
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
