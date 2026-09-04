"""Discord notification seam between the webapp and the bot.

The webapp imports this module instead of bot.py, avoiding a circular
import; bot.py registers itself here at startup. Every function no-ops
when no bot is registered (e.g. webapp running standalone or in tests).
"""

import discord
from discord.ext import commands

import superpal.env as superpal_env
from superpal.cards.fight_service import (
    FIGHT_TOKEN_EXPIRY_MINUTES,
    create_fight_token,
    fight_ended_by_escape,
    fight_ended_by_forfeit,
    get_fight,
)
from superpal.cards.models import RARITY_LABELS, CardRef
from superpal.cards.service import get_member_display_name
from superpal.cards.trade_service import (
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


async def _card_names(items: list[CardRef]) -> str:
    labels = [
        f"{RARITY_LABELS[item.rarity]} "
        f"{await get_member_display_name(item.member_id) or item.member_id}"
        for item in items
    ]
    return ", ".join(labels)


async def notify_trade_offer(offer_id: int) -> None:
    """DM the recipient of a new trade offer with both sides of the swap."""
    if _bot is None:
        return
    offer = await get_offer_by_id(offer_id)
    if offer is None:
        return
    guild = _bot.get_guild(superpal_env.GUILD_ID or 0)
    if guild is None:
        return
    member = guild.get_member(int(offer.recipient_id))
    if member is None:
        return

    from superpal.cogs.cards import TradeOfferView

    view = TradeOfferView(
        offer_id=offer_id,
        recipient_id=offer.recipient_id,
        trade_url=f"{WEBAPP_BASE_URL}/trade/{offer_id}",
    )
    content = (
        f"**{offer.proposer_display_name}** wants to trade with you!\n\n"
        f"You get: {await _card_names(offer.give_items)}\n"
        f"You give: {await _card_names(offer.get_items)}\n\n"
        f"Open the trade window to accept, decline or counter: "
        f"{WEBAPP_BASE_URL}/trade/{offer_id}"
    )
    try:
        dm = await member.send(content=content, view=view)
        view.message = dm
        await set_offer_discord_message_id(offer_id, str(dm.id))
    except discord.Forbidden:
        pass


async def send_fight_lobby_dms(
    fight_id: int, challenger_id: str, opponent_id: str, mode: str
) -> None:
    """DM both players their fight lobby magic links after a challenge is accepted."""
    if _bot is None:
        return
    guild = _bot.get_guild(superpal_env.GUILD_ID or 0)
    if guild is None:
        return
    names = {uid: await get_member_display_name(uid) or uid for uid in (challenger_id, opponent_id)}
    for uid, other_uid in ((challenger_id, opponent_id), (opponent_id, challenger_id)):
        member = guild.get_member(int(uid))
        if member is None:
            continue
        url = await create_fight_token(fight_id, uid, WEBAPP_BASE_URL)
        try:
            await member.send(
                f"Your **{mode}** battle vs. **{names[other_uid]}** "
                f"is ready!\n\nOpen the fight lobby: <{url}>",
                suppress_embeds=True,
            )
        except discord.Forbidden:
            pass


async def notify_fight_challenge(fight_id: int) -> None:
    """DM the opponent about a fight challenge created with no Discord channel (i.e. via web)."""
    if _bot is None:
        return
    fight = await get_fight(fight_id)
    if fight is None:
        return
    guild = _bot.get_guild(superpal_env.GUILD_ID or 0)
    if guild is None:
        return
    opponent = guild.get_member(int(fight.opponent_id))
    if opponent is None:
        return
    challenger_name = await get_member_display_name(fight.challenger_id) or fight.challenger_id

    from superpal.cogs.fights import FightChallengeView

    view = FightChallengeView(
        fight_id=fight.id,
        challenger_id=fight.challenger_id,
        opponent_id=fight.opponent_id,
        mode=fight.mode,
    )
    try:
        dm = await opponent.send(
            content=(
                f"**{challenger_name}** challenges you to a **{fight.mode.upper()} Battle**!\n\n"
                f"You have {FIGHT_TOKEN_EXPIRY_MINUTES} minutes to respond."
            ),
            view=view,
        )
        view.message = dm
    except discord.Forbidden:
        pass


async def announce_fight_result(fight_id: int) -> None:
    """Post a completed fight's result to the Discord channel it was started from."""
    if _bot is None:
        return
    fight = await get_fight(fight_id)
    if fight is None or fight.status != "completed" or not fight.channel_id or not fight.winner_id:
        return
    channel = _bot.get_channel(int(fight.channel_id))
    if not isinstance(channel, discord.abc.Messageable):
        return

    winner_name = await get_member_display_name(fight.winner_id) or fight.winner_id
    loser_id = fight.opponent_id if fight.winner_id == fight.challenger_id else fight.challenger_id
    loser_name = await get_member_display_name(loser_id) or loser_id
    escaped = await fight_ended_by_escape(fight_id)
    forfeited = await fight_ended_by_forfeit(fight_id)

    if forfeited:
        headline = f"⏳ **{loser_name}** never made their move — **{winner_name}** wins by forfeit!"
    elif escaped:
        headline = f"🏃 **{loser_name}** fled the battle — **{winner_name}** wins by default!"
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
    guild = _bot.get_guild(superpal_env.GUILD_ID or 0)
    if guild is None:
        return
    owner_member = guild.get_member(int(offer.recipient_id))
    if owner_member is None:
        return
    try:
        dm_channel = await owner_member.create_dm()
        msg = await dm_channel.fetch_message(int(discord_message_id))
        await msg.edit(content=message, view=None)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass
