import pytest
import aiosqlite
import importlib
from datetime import datetime, timedelta, timezone


@pytest.fixture
async def db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("CARDS_DB_PATH", db_file)
    import superpal.cards.db as db_mod
    import superpal.cards.service as svc_mod
    importlib.reload(db_mod)
    importlib.reload(svc_mod)
    await db_mod.init_db()
    return db_mod, svc_mod


@pytest.mark.asyncio
async def test_sync_members_upserts(db):
    db_mod, svc = db
    members = [
        {"discord_id": "111", "display_name": "Alice", "avatar_url": "http://a.com/a.png"},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
    ]
    await svc.sync_members(members)
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute("SELECT discord_id FROM members ORDER BY discord_id") as cur:
            rows = await cur.fetchall()
    assert [r[0] for r in rows] == ["111", "222"]


@pytest.mark.asyncio
async def test_draw_card_returns_card(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
    ])
    card = await svc.draw_card(owner_id="111", max_draws=1)
    assert card is not None
    assert card.rarity in ("common", "uncommon", "rare", "legendary")
    assert card.owner_id == "111"
    assert card.quantity == 1


@pytest.mark.asyncio
async def test_draw_card_respects_weekly_limit(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    await svc.draw_card(owner_id="111", max_draws=1)
    second = await svc.draw_card(owner_id="111", max_draws=1)
    assert second is None


@pytest.mark.asyncio
async def test_draw_card_super_pal_gets_two(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    first = await svc.draw_card(owner_id="111", max_draws=2)
    second = await svc.draw_card(owner_id="111", max_draws=2)
    third = await svc.draw_card(owner_id="111", max_draws=2)
    assert first is not None
    assert second is not None
    assert third is None


@pytest.mark.asyncio
async def test_draw_card_excluded_member_not_in_pool(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Excluded", "avatar_url": None},
    ])
    await svc.set_excluded("222", excluded=True)
    # Draw many times; excluded member should never appear
    results = set()
    for _ in range(20):
        card = await svc.draw_card(owner_id="111", max_draws=99)
        if card:
            results.add(card.card_member_id)
    assert "222" not in results


@pytest.mark.asyncio
async def test_draw_card_increments_quantity_on_duplicate(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    # Force two draws of the same member+rarity by patching random
    import unittest.mock as mock
    import superpal.cards.service as svc_mod
    with mock.patch.object(svc_mod, "_roll_rarity", return_value="common"), \
         mock.patch("random.choice", return_value="111"):
        await svc.draw_card(owner_id="111", max_draws=2)
        card = await svc.draw_card(owner_id="111", max_draws=2)
    assert card is not None
    assert card.quantity == 2


@pytest.mark.asyncio
async def test_trade_in_requires_three(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
    ])
    # Give owner 2 copies of Bob's common card
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('111', '222', 'common', 2, ?)",
            (datetime.now(timezone.utc).isoformat(),)
        )
        await conn.commit()
    result = await svc.trade_in("111", "222", "common")
    assert result is None  # not enough


@pytest.mark.asyncio
async def test_trade_in_succeeds_with_three(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
    ])
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('111', '222', 'common', 3, ?)",
            (datetime.now(timezone.utc).isoformat(),)
        )
        await conn.commit()
    import unittest.mock as mock
    with mock.patch("random.choice", return_value="111"):
        result = await svc.trade_in("111", "222", "common")
    assert result is not None
    assert result.rarity == "common"
    assert result.owner_id == "111"
    # Source cards fully deducted (trade gave back "111", not "222")
    remaining = await svc.get_card_quantity("111", "222", "common")
    assert remaining == 0


@pytest.mark.asyncio
async def test_upgrade_legendary_rejected(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    result = await svc.upgrade("111", "111", "legendary")
    assert result is None


@pytest.mark.asyncio
async def test_upgrade_requires_five(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('111', '111', 'common', 4, ?)",
            (datetime.now(timezone.utc).isoformat(),)
        )
        await conn.commit()
    result = await svc.upgrade("111", "111", "common")
    assert result is None


@pytest.mark.asyncio
async def test_upgrade_succeeds(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('111', '111', 'common', 5, ?)",
            (datetime.now(timezone.utc).isoformat(),)
        )
        await conn.commit()
    result = await svc.upgrade("111", "111", "common")
    assert result is not None
    assert result.rarity == "uncommon"
    assert result.card_member_id == "111"
    remaining = await svc.get_card_quantity("111", "111", "common")
    assert remaining == 0


@pytest.mark.asyncio
async def test_magic_link_reusable_within_24h(db):
    db_mod, svc = db
    url = await svc.generate_magic_link("111", "collection", "http://localhost:8080")
    token = url.split("/")[-1]
    link1 = await svc.use_magic_link(token)
    link2 = await svc.use_magic_link(token)
    assert link1 is not None
    assert link1.session_token is not None
    assert link2 is not None
    assert link2.session_token is not None
    assert link1.session_token != link2.session_token  # fresh session each time


@pytest.mark.asyncio
async def test_add_member_inserts(db):
    db_mod, svc = db
    await svc.add_member("test_dingus", "Dingus Supreme")
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT display_name FROM members WHERE discord_id = ?", ("test_dingus",)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == "Dingus Supreme"


@pytest.mark.asyncio
async def test_add_member_upserts_name(db):
    db_mod, svc = db
    await svc.add_member("test_dingus", "Dingus Supreme")
    await svc.add_member("test_dingus", "Dingus Supreme Revised")
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT display_name FROM members WHERE discord_id = ?", ("test_dingus",)
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == "Dingus Supreme Revised"


@pytest.mark.asyncio
async def test_set_member_avatar_updates_url(db):
    db_mod, svc = db
    await svc.add_member("test_dingus", "Dingus Supreme")
    await svc.set_member_avatar("test_dingus", "/static/avatars/test_dingus.png")
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT avatar_url FROM members WHERE discord_id = ?", ("test_dingus",)
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == "/static/avatars/test_dingus.png"


@pytest.mark.asyncio
async def test_award_card_creates_entry(db):
    db_mod, svc = db
    await svc.add_member("owner1", "Owner")
    await svc.add_member("card1", "Card Member")
    card = await svc.award_card("owner1", "card1", "rare", 2)
    assert card is not None
    assert card.owner_id == "owner1"
    assert card.card_member_id == "card1"
    assert card.rarity == "rare"
    assert card.quantity == 2


@pytest.mark.asyncio
async def test_award_card_increments_existing(db):
    db_mod, svc = db
    await svc.add_member("owner1", "Owner")
    await svc.add_member("card1", "Card Member")
    await svc.award_card("owner1", "card1", "common", 1)
    card = await svc.award_card("owner1", "card1", "common", 3)
    assert card.quantity == 4


@pytest.mark.asyncio
async def test_award_card_rejects_invalid_rarity(db):
    db_mod, svc = db
    await svc.add_member("owner1", "Owner")
    await svc.add_member("card1", "Card Member")
    result = await svc.award_card("owner1", "card1", "mythic", 1)
    assert result is None


# ─── Peer trade tests ────────────────────────────────────────────────────────

async def _setup_trade_members(svc, db_mod):
    """Insert four members and give proposer a common card of card_a."""
    await svc.sync_members([
        {"discord_id": "proposer", "display_name": "Proposer", "avatar_url": None},
        {"discord_id": "recipient", "display_name": "Recipient", "avatar_url": None},
        {"discord_id": "card_a", "display_name": "Card A", "avatar_url": None},
        {"discord_id": "card_b", "display_name": "Card B", "avatar_url": None},
    ])
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('proposer', 'card_a', 'common', 1, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_create_trade_offer_returns_trade(db):
    db_mod, svc = db
    await _setup_trade_members(svc, db_mod)
    trade, err = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "common", "card_b", "rare"
    )
    assert err is None
    assert trade is not None
    assert trade.status == "pending"
    assert trade.proposer_id == "proposer"
    assert trade.offer_rarity == "common"


@pytest.mark.asyncio
async def test_create_trade_offer_rejects_missing_card(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "proposer", "display_name": "Proposer", "avatar_url": None},
        {"discord_id": "recipient", "display_name": "Recipient", "avatar_url": None},
        {"discord_id": "card_a", "display_name": "Card A", "avatar_url": None},
        {"discord_id": "card_b", "display_name": "Card B", "avatar_url": None},
    ])
    trade, err = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "common", "card_b", "rare"
    )
    assert trade is None
    assert err == "no_offer_card"


@pytest.mark.asyncio
async def test_create_trade_offer_rejects_self_trade(db):
    db_mod, svc = db
    await _setup_trade_members(svc, db_mod)
    trade, err = await svc.create_trade_offer(
        "proposer", "proposer", "card_a", "common", "card_b", "rare"
    )
    assert trade is None
    assert err == "self_trade"


@pytest.mark.asyncio
async def test_create_trade_offer_rejects_duplicate_pending(db):
    db_mod, svc = db
    await _setup_trade_members(svc, db_mod)
    # Give proposer a second card so the first offer succeeds
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('proposer', 'card_a', 'rare', 1, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        await conn.commit()
    trade1, err1 = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "common", "card_b", "rare"
    )
    assert err1 is None
    trade2, err2 = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "rare", "card_b", "uncommon"
    )
    assert trade2 is None
    assert err2 == "pending_exists"


@pytest.mark.asyncio
async def test_create_trade_offer_allows_after_first_resolved(db):
    db_mod, svc = db
    await _setup_trade_members(svc, db_mod)
    trade1, _ = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "common", "card_b", "rare"
    )
    assert trade1 is not None
    await svc.decline_trade(trade1.id)
    # Give proposer the card again for the second offer
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('proposer', 'card_a', 'common', 1, ?) "
            "ON CONFLICT(owner_id, card_member_id, rarity) DO UPDATE SET quantity = quantity + 1",
            (datetime.now(timezone.utc).isoformat(),),
        )
        await conn.commit()
    trade2, err2 = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "common", "card_b", "rare"
    )
    assert err2 is None
    assert trade2 is not None


@pytest.mark.asyncio
async def test_execute_trade_swaps_cards(db):
    db_mod, svc = db
    await _setup_trade_members(svc, db_mod)
    # Give recipient card_b/rare
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('recipient', 'card_b', 'rare', 1, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        await conn.commit()
    trade, _ = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "common", "card_b", "rare"
    )
    assert trade is not None
    success, reason = await svc.execute_trade(trade.id)
    assert success is True
    assert reason is None
    assert await svc.get_card_quantity("proposer", "card_b", "rare") == 1
    assert await svc.get_card_quantity("recipient", "card_a", "common") == 1
    assert await svc.get_card_quantity("proposer", "card_a", "common") == 0
    assert await svc.get_card_quantity("recipient", "card_b", "rare") == 0


@pytest.mark.asyncio
async def test_execute_trade_fails_if_proposer_card_gone(db):
    db_mod, svc = db
    await _setup_trade_members(svc, db_mod)
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('recipient', 'card_b', 'rare', 1, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        await conn.commit()
    trade, _ = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "common", "card_b", "rare"
    )
    # Remove proposer's card after creating the offer
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "DELETE FROM user_cards WHERE owner_id = 'proposer' AND card_member_id = 'card_a'"
        )
        await conn.commit()
    success, reason = await svc.execute_trade(trade.id)
    assert success is False
    assert reason == "proposer_missing_card"


@pytest.mark.asyncio
async def test_execute_trade_fails_if_recipient_card_gone(db):
    db_mod, svc = db
    await _setup_trade_members(svc, db_mod)
    trade, _ = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "common", "card_b", "rare"
    )
    # Recipient never had card_b/rare
    success, reason = await svc.execute_trade(trade.id)
    assert success is False
    assert reason == "recipient_missing_card"


@pytest.mark.asyncio
async def test_execute_trade_fails_if_expired(db):
    db_mod, svc = db
    await _setup_trade_members(svc, db_mod)
    # Insert an already-expired trade directly
    past = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO pending_trades "
            "(proposer_id, recipient_id, offer_member_id, offer_rarity, "
            "request_member_id, request_rarity, status, created_at, expires_at) "
            "VALUES ('proposer', 'recipient', 'card_a', 'common', 'card_b', 'rare', 'pending', ?, ?)",
            (past, past),
        )
        await conn.commit()
        async with conn.execute("SELECT last_insert_rowid()") as cur:
            trade_id = (await cur.fetchone())[0]
    success, reason = await svc.execute_trade(trade_id)
    assert success is False
    assert reason == "expired"


@pytest.mark.asyncio
async def test_decline_trade_works(db):
    db_mod, svc = db
    await _setup_trade_members(svc, db_mod)
    trade, _ = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "common", "card_b", "rare"
    )
    result = await svc.decline_trade(trade.id)
    assert result is True
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT status FROM pending_trades WHERE id = ?", (trade.id,)
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == "declined"


@pytest.mark.asyncio
async def test_decline_trade_already_resolved_returns_false(db):
    db_mod, svc = db
    await _setup_trade_members(svc, db_mod)
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('recipient', 'card_b', 'rare', 1, ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        await conn.commit()
    trade, _ = await svc.create_trade_offer(
        "proposer", "recipient", "card_a", "common", "card_b", "rare"
    )
    await svc.execute_trade(trade.id)
    result = await svc.decline_trade(trade.id)
    assert result is False


# ─── Gift card tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gift_card_transfers_card(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
        {"discord_id": "333", "display_name": "CardMember", "avatar_url": None},
    ])
    # Give Alice a card to gift
    await svc.award_card("111", "333", "rare", 1)

    card, err = await svc.gift_card(
        gifter_id="111",
        recipient_id="222",
        card_member_id="333",
        rarity="rare",
        drawn_by_name="Alice",
    )

    assert err is None
    assert card is not None
    assert card.owner_id == "222"
    assert card.card_member_id == "333"
    assert card.rarity == "rare"
    # Alice's card should be gone
    assert await svc.get_card_quantity("111", "333", "rare") == 0
    # Bob should have it
    assert await svc.get_card_quantity("222", "333", "rare") == 1


@pytest.mark.asyncio
async def test_gift_card_gifter_keeps_extra_copy(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
        {"discord_id": "333", "display_name": "CardMember", "avatar_url": None},
    ])
    await svc.award_card("111", "333", "common", 3)

    card, err = await svc.gift_card("111", "222", "333", "common", "Alice")

    assert err is None
    assert await svc.get_card_quantity("111", "333", "common") == 2
    assert await svc.get_card_quantity("222", "333", "common") == 1


@pytest.mark.asyncio
async def test_gift_card_fails_when_not_owned(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
        {"discord_id": "333", "display_name": "CardMember", "avatar_url": None},
    ])

    card, err = await svc.gift_card("111", "222", "333", "rare", "Alice")

    assert card is None
    assert err == "no_card"


@pytest.mark.asyncio
async def test_gift_card_fails_self_gift(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "333", "display_name": "CardMember", "avatar_url": None},
    ])
    await svc.award_card("111", "333", "common", 1)

    card, err = await svc.gift_card("111", "111", "333", "common", "Alice")

    assert card is None
    assert err == "self_gift"


@pytest.mark.asyncio
async def test_gift_card_recipient_already_owns_copy(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
        {"discord_id": "333", "display_name": "CardMember", "avatar_url": None},
    ])
    await svc.award_card("111", "333", "rare", 1)
    await svc.award_card("222", "333", "rare", 1)  # Bob already has one

    card, err = await svc.gift_card("111", "222", "333", "rare", "Alice")

    assert err is None
    assert card is not None
    assert card.owner_id == "222"
    assert await svc.get_card_quantity("222", "333", "rare") == 2
    assert await svc.get_card_quantity("111", "333", "rare") == 0


# ─── Leaderboard tests ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_leaderboard_total_empty(db):
    db_mod, svc = db
    assert await svc.get_leaderboard("total") == []

@pytest.mark.asyncio
async def test_get_leaderboard_total_ranks_by_quantity(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "bob", "display_name": "Bob", "avatar_url": None},
        {"discord_id": "card1", "display_name": "Card1", "avatar_url": None},
    ])
    await svc.award_card("alice", "card1", "common", 5)
    await svc.award_card("bob", "card1", "common", 10)
    result = await svc.get_leaderboard("total")
    assert result[0]["display_name"] == "Bob" and result[0]["total"] == 10
    assert result[1]["display_name"] == "Alice" and result[1]["total"] == 5

@pytest.mark.asyncio
async def test_get_leaderboard_total_sums_across_rarities(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "card1", "display_name": "Card1", "avatar_url": None},
        {"discord_id": "card2", "display_name": "Card2", "avatar_url": None},
    ])
    await svc.award_card("alice", "card1", "common", 3)
    await svc.award_card("alice", "card1", "rare", 2)
    await svc.award_card("alice", "card2", "legendary", 1)
    result = await svc.get_leaderboard("total")
    assert result[0]["total"] == 6

@pytest.mark.asyncio
async def test_get_leaderboard_excludes_excluded_players(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "excluded", "display_name": "Excluded", "avatar_url": None},
        {"discord_id": "card1", "display_name": "Card1", "avatar_url": None},
    ])
    await svc.award_card("alice", "card1", "common", 5)
    await svc.award_card("excluded", "card1", "common", 100)
    await svc.set_excluded("excluded", excluded=True)
    result = await svc.get_leaderboard("total")
    assert all(r["owner_id"] != "excluded" for r in result)

@pytest.mark.asyncio
async def test_get_leaderboard_limits_to_ten(db):
    db_mod, svc = db
    members = [{"discord_id": str(i), "display_name": f"User{i}", "avatar_url": None} for i in range(15)]
    members.append({"discord_id": "card1", "display_name": "Card1", "avatar_url": None})
    await svc.sync_members(members)
    for i in range(15):
        await svc.award_card(str(i), "card1", "common", i + 1)
    result = await svc.get_leaderboard("total")
    assert len(result) == 10 and result[0]["total"] == 15

@pytest.mark.asyncio
async def test_get_leaderboard_legendary_counts_only_legendary(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "bob", "display_name": "Bob", "avatar_url": None},
        {"discord_id": "card1", "display_name": "Card1", "avatar_url": None},
    ])
    await svc.award_card("alice", "card1", "common", 50)
    await svc.award_card("bob", "card1", "legendary", 3)
    result = await svc.get_leaderboard("legendary")
    assert result[0]["owner_id"] == "bob" and result[0]["total"] == 3

@pytest.mark.asyncio
async def test_get_leaderboard_unique_counts_distinct_members(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "bob", "display_name": "Bob", "avatar_url": None},
        {"discord_id": "card1", "display_name": "Card1", "avatar_url": None},
        {"discord_id": "card2", "display_name": "Card2", "avatar_url": None},
    ])
    await svc.award_card("alice", "card1", "common", 5)
    await svc.award_card("alice", "card2", "rare", 1)
    await svc.award_card("bob", "card1", "common", 1)
    await svc.award_card("bob", "card1", "legendary", 1)
    result = await svc.get_leaderboard("unique")
    alice_row = next(r for r in result if r["owner_id"] == "alice")
    bob_row = next(r for r in result if r["owner_id"] == "bob")
    assert alice_row["total"] == 2
    assert bob_row["total"] == 1

@pytest.mark.asyncio
async def test_get_leaderboard_default_is_total(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "card1", "display_name": "Card1", "avatar_url": None},
    ])
    await svc.award_card("alice", "card1", "common", 7)
    result = await svc.get_leaderboard()
    assert result[0]["total"] == 7

@pytest.mark.asyncio
async def test_get_leaderboard_result_keys(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "card1", "display_name": "Card1", "avatar_url": None},
    ])
    await svc.award_card("alice", "card1", "common", 1)
    result = await svc.get_leaderboard("total")
    assert set(result[0].keys()) == {"owner_id", "display_name", "total"}
