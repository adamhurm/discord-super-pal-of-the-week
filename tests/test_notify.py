from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import superpal.notify as notify


@pytest.fixture(autouse=True)
def _isolate_notify_state(monkeypatch):
    monkeypatch.setattr(notify, "_bot", None)
    monkeypatch.setattr(notify, "_guild_members_cache", None)


def test_guild_members_cache_roundtrip():
    assert notify.get_guild_members_cache() is None
    members = [{"discord_id": "1", "display_name": "Alice", "avatar_url": None}]
    notify.set_guild_members_cache(members)
    assert notify.get_guild_members_cache() == members


@pytest.mark.asyncio
async def test_notify_trade_offer_noop_without_bot():
    with patch("superpal.notify.get_offer_by_id", new=AsyncMock()) as get_offer:
        await notify.notify_trade_offer(1)
    get_offer.assert_not_called()


@pytest.mark.asyncio
async def test_edit_offer_dm_noop_without_bot():
    with patch("superpal.notify.get_offer_by_id", new=AsyncMock()) as get_offer:
        await notify.edit_offer_dm(1, "message")
    get_offer.assert_not_called()


def _fake_offer():
    offer = MagicMock()
    offer.items = []
    offer.proposer_display_name = "Bob"
    offer.listing.owner_id = "42"
    offer.listing.items = []
    return offer


@pytest.mark.asyncio
async def test_notify_trade_offer_sends_dm(monkeypatch):
    monkeypatch.setattr(notify.superpal_env, "GUILD_ID", 999)
    dm = MagicMock(id=555)
    member = MagicMock()
    member.send = AsyncMock(return_value=dm)
    guild = MagicMock()
    guild.get_member.return_value = member
    bot = MagicMock()
    bot.get_guild.return_value = guild
    notify.set_bot(bot)

    with (
        patch("superpal.notify.get_offer_by_id", new=AsyncMock(return_value=_fake_offer())),
        patch("superpal.notify.set_offer_discord_message_id", new=AsyncMock()) as set_msg_id,
    ):
        await notify.notify_trade_offer(7)

    member.send.assert_awaited_once()
    assert "made an offer on your listing" in member.send.call_args.kwargs["content"]
    set_msg_id.assert_awaited_once_with(7, "555")


@pytest.mark.asyncio
async def test_edit_offer_dm_edits_message(monkeypatch):
    monkeypatch.setattr(notify.superpal_env, "GUILD_ID", 999)
    msg = MagicMock()
    msg.edit = AsyncMock()
    dm_channel = MagicMock()
    dm_channel.fetch_message = AsyncMock(return_value=msg)
    owner = MagicMock()
    owner.create_dm = AsyncMock(return_value=dm_channel)
    guild = MagicMock()
    guild.get_member.return_value = owner
    bot = MagicMock()
    bot.get_guild.return_value = guild
    notify.set_bot(bot)

    with (
        patch("superpal.notify.get_offer_by_id", new=AsyncMock(return_value=_fake_offer())),
        patch(
            "superpal.notify.get_offer_discord_message_id",
            new=AsyncMock(return_value="321"),
        ),
    ):
        await notify.edit_offer_dm(7, "Offer declined.")

    dm_channel.fetch_message.assert_awaited_once_with(321)
    msg.edit.assert_awaited_once_with(content="Offer declined.", view=None)


def _fake_fight(status="completed", channel_id="555", winner_id="p1", mode="quick"):
    fight = MagicMock()
    fight.status = status
    fight.channel_id = channel_id
    fight.winner_id = winner_id
    fight.mode = mode
    fight.challenger_id = "p1"
    fight.opponent_id = "p2"
    return fight


@pytest.mark.asyncio
async def test_announce_fight_result_noop_without_bot():
    with patch("superpal.notify.get_fight", new=AsyncMock()) as get_fight:
        await notify.announce_fight_result(1)
    get_fight.assert_not_called()


@pytest.mark.asyncio
async def test_announce_fight_result_sends_embed():
    import discord

    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel
    notify.set_bot(bot)

    with (
        patch("superpal.notify.get_fight", new=AsyncMock(return_value=_fake_fight())),
        patch("superpal.notify.fight_ended_by_escape", new=AsyncMock(return_value=False)),
        patch(
            "superpal.notify.get_member_display_name",
            new=AsyncMock(side_effect=lambda pid: {"p1": "Alice", "p2": "Bob"}.get(pid)),
        ),
    ):
        await notify.announce_fight_result(1)

    bot.get_channel.assert_called_once_with(555)
    channel.send.assert_awaited_once()
    embed = channel.send.call_args.kwargs["embed"]
    assert "Alice" in embed.description
    assert "Bob" in embed.description
    assert "50 Pringles" in embed.description


@pytest.mark.asyncio
async def test_announce_fight_result_escape_flavor():
    import discord

    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel
    notify.set_bot(bot)

    with (
        patch(
            "superpal.notify.get_fight",
            new=AsyncMock(return_value=_fake_fight(mode="extended")),
        ),
        patch("superpal.notify.fight_ended_by_escape", new=AsyncMock(return_value=True)),
        patch("superpal.notify.get_member_display_name", new=AsyncMock(return_value="Pal")),
    ):
        await notify.announce_fight_result(1)

    embed = channel.send.call_args.kwargs["embed"]
    assert "fled" in embed.description
    assert "escape penalty" in embed.description
    assert "participation bonus" in embed.description


@pytest.mark.asyncio
async def test_announce_fight_result_skips_incomplete_or_channelless():
    bot = MagicMock()
    notify.set_bot(bot)

    for fight in (_fake_fight(status="active"), _fake_fight(channel_id=None), None):
        with patch("superpal.notify.get_fight", new=AsyncMock(return_value=fight)):
            await notify.announce_fight_result(1)
    bot.get_channel.assert_not_called()
