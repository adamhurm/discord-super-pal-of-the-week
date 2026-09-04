from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from superpal.cards.models import CardRef


@pytest.fixture
async def db(db_mods):
    db_mod, svc_mod, _fs, _ps, trade_mod = db_mods
    await db_mod.init_db()
    return db_mod, svc_mod, trade_mod


async def _seed_two_players(svc):
    """Insert Alice (111) and Bob (222) as members."""
    await svc.sync_members(
        [
            {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
            {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
        ]
    )


async def _give_card(db_mod, owner_id: str, member_id: str, rarity: str, qty: int = 1):
    """Directly insert a user_card row."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_cards "
            "(owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_id, card_member_id, rarity) DO UPDATE SET quantity = ?",
            (owner_id, member_id, rarity, qty, now, qty),
        )
        await db.commit()


async def _quantity(db_mod, owner_id: str, member_id: str, rarity: str) -> int:
    async with aiosqlite.connect(db_mod.DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM user_cards "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, member_id, rarity),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def _offer_status(db_mod, offer_id: int) -> str:
    async with aiosqlite.connect(db_mod.DB_PATH) as db:
        async with db.execute("SELECT status FROM trade_offers WHERE id = ?", (offer_id,)) as cur:
            row = await cur.fetchone()
    assert row is not None
    return row[0]


# ─── Listings ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_listing_rejects_empty_items(db):
    _db_mod, svc, trade = db
    await _seed_two_players(svc)
    assert await trade.create_listing("111", [], None) == "empty_items"


@pytest.mark.asyncio
async def test_create_listing_rejects_unowned_card(db):
    _db_mod, svc, trade = db
    await _seed_two_players(svc)
    assert await trade.create_listing("111", [CardRef("222", "common")], None) == "no_card"


@pytest.mark.asyncio
async def test_create_listing_rejects_more_copies_than_owned(db):
    """Listing the same card twice needs two copies, not one seen twice."""
    db_mod, svc, trade = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common", qty=1)
    items = [CardRef("222", "common"), CardRef("222", "common")]
    assert await trade.create_listing("111", items, None) == "no_card"


@pytest.mark.asyncio
async def test_create_listing_success(db):
    db_mod, svc, trade = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    result = await trade.create_listing("111", [CardRef("222", "common")], "want a rare")
    assert not isinstance(result, str)
    assert result.owner_id == "111"
    assert result.ask_note == "want a rare"
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_listing_items_carry_card_identity(db):
    """Listing items must name the card, not just its rarity — the marketplace UI shows it."""
    db_mod, svc, trade = db
    await svc.sync_members(
        [
            {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
            {"discord_id": "222", "display_name": "Bob", "avatar_url": "/static/bob.png"},
        ]
    )
    await _give_card(db_mod, "111", "222", "common")
    listing = await trade.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    assert listing.items[0].display_name == "Bob"
    assert listing.items[0].avatar_url == "/static/bob.png"


@pytest.mark.asyncio
async def test_listing_items_survive_unknown_card_member(db):
    """A card whose member row is gone still loads, with a null display name."""
    db_mod, svc, trade = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "999", "common")
    listing = await trade.create_listing("111", [CardRef("999", "common")], None)
    assert not isinstance(listing, str)
    assert listing.items[0].member_id == "999"
    assert listing.items[0].display_name is None


@pytest.mark.asyncio
async def test_cancel_listing_owner_only(db):
    db_mod, svc, trade = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await trade.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    assert not await trade.cancel_listing(listing.id, "222")
    assert await trade.cancel_listing(listing.id, "111")


@pytest.mark.asyncio
async def test_get_active_listings_excludes_own(db):
    db_mod, svc, trade = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    await trade.create_listing("111", [CardRef("222", "common")], None)
    assert await trade.get_active_listings(exclude_owner_id="111") == []
    assert len(await trade.get_active_listings()) == 1


# ─── Listing-originated offers ───────────────────────────────────────────────


async def _listing_with_offer(db_mod, svc, trade):
    """Alice lists a common Bob; Bob offers an uncommon Alice back."""
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await trade.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    await _give_card(db_mod, "222", "111", "uncommon")
    offer = await trade.create_listing_offer(listing.id, "222", [CardRef("111", "uncommon")])
    assert not isinstance(offer, str)
    return listing, offer


@pytest.mark.asyncio
async def test_listing_offer_records_both_sides(db):
    db_mod, svc, trade = db
    _listing, offer = await _listing_with_offer(db_mod, svc, trade)
    assert offer.proposer_id == "222"
    assert offer.recipient_id == "111"
    assert [(i.member_id, i.rarity) for i in offer.give_items] == [("111", "uncommon")]
    assert [(i.member_id, i.rarity) for i in offer.get_items] == [("222", "common")]


@pytest.mark.asyncio
async def test_offer_items_carry_card_identity(db):
    db_mod, svc, trade = db
    _listing, offer = await _listing_with_offer(db_mod, svc, trade)
    assert offer.give_items[0].display_name == "Alice"
    assert offer.get_items[0].display_name == "Bob"


@pytest.mark.asyncio
async def test_create_listing_offer_rejects_self_offer(db):
    db_mod, svc, trade = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await trade.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    await _give_card(db_mod, "111", "222", "uncommon")
    result = await trade.create_listing_offer(listing.id, "111", [CardRef("222", "uncommon")])
    assert result == "self_offer"


@pytest.mark.asyncio
async def test_create_listing_offer_rejects_unowned_card(db):
    db_mod, svc, trade = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await trade.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    result = await trade.create_listing_offer(listing.id, "222", [CardRef("111", "rare")])
    assert result == "no_card"


@pytest.mark.asyncio
async def test_create_listing_offer_rejects_duplicate(db):
    db_mod, svc, trade = db
    _listing, offer = await _listing_with_offer(db_mod, svc, trade)
    await _give_card(db_mod, "222", "111", "uncommon", qty=2)
    result = await trade.create_listing_offer(offer.listing.id, "222", [CardRef("111", "uncommon")])
    assert result == "duplicate_offer"


@pytest.mark.asyncio
async def test_create_listing_offer_rejects_nonexistent_listing(db):
    db_mod, svc, trade = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "222", "111", "uncommon")
    assert (
        await trade.create_listing_offer(9999, "222", [CardRef("111", "uncommon")]) == "not_found"
    )


@pytest.mark.asyncio
async def test_accept_listing_offer_swaps_cards_and_declines_siblings(db):
    db_mod, svc, trade = db
    listing, offer = await _listing_with_offer(db_mod, svc, trade)
    await svc.sync_members([{"discord_id": "333", "display_name": "Carol", "avatar_url": None}])
    await _give_card(db_mod, "333", "111", "rare")
    offer2 = await trade.create_listing_offer(listing.id, "333", [CardRef("111", "rare")])
    assert not isinstance(offer2, str)

    ok, err = await trade.accept_offer(offer.id, "111")

    assert (ok, err) == (True, None)
    assert await _quantity(db_mod, "222", "222", "common") == 1
    assert await _quantity(db_mod, "111", "111", "uncommon") == 1
    assert await _offer_status(db_mod, offer2.id) == "declined"


# ─── Direct trades ───────────────────────────────────────────────────────────


async def _seed_direct_pair(db_mod, svc):
    """Alice holds a rare Bob, Bob holds a legendary Alice."""
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "rare")
    await _give_card(db_mod, "222", "111", "legendary")


@pytest.mark.asyncio
async def test_direct_offer_needs_no_listing(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    offer = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(offer, str)
    assert offer.listing is None
    assert (offer.proposer_id, offer.recipient_id) == ("111", "222")


@pytest.mark.asyncio
async def test_accept_direct_offer_swaps_both_sides(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    offer = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(offer, str)

    ok, err = await trade.accept_offer(offer.id, "222")

    assert (ok, err) == (True, None)
    assert await _quantity(db_mod, "222", "222", "rare") == 1
    assert await _quantity(db_mod, "111", "222", "rare") == 0
    assert await _quantity(db_mod, "111", "111", "legendary") == 1
    assert await _quantity(db_mod, "222", "111", "legendary") == 0


@pytest.mark.asyncio
async def test_direct_offer_rejects_self_trade(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    result = await trade.create_direct_offer(
        "111", "111", [CardRef("222", "rare")], [CardRef("222", "rare")]
    )
    assert result == "self_offer"


@pytest.mark.asyncio
async def test_direct_offer_rejects_card_the_proposer_lacks(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    result = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "legendary")], [CardRef("111", "legendary")]
    )
    assert result == "no_card"


@pytest.mark.asyncio
async def test_direct_offer_rejects_card_the_recipient_lacks(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    result = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "common")]
    )
    assert result == "recipient_no_card"


@pytest.mark.asyncio
async def test_direct_offer_rejects_more_copies_than_held(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    result = await trade.create_direct_offer(
        "111",
        "222",
        [CardRef("222", "rare"), CardRef("222", "rare")],
        [CardRef("111", "legendary")],
    )
    assert result == "no_card"


@pytest.mark.asyncio
async def test_direct_offer_rejects_empty_side(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    assert (
        await trade.create_direct_offer("111", "222", [], [CardRef("111", "legendary")])
        == "empty_items"
    )
    assert (
        await trade.create_direct_offer("111", "222", [CardRef("222", "rare")], []) == "empty_items"
    )


@pytest.mark.asyncio
async def test_direct_offer_caps_items_per_side(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    await _give_card(db_mod, "111", "222", "rare", qty=20)
    give = [CardRef("222", "rare")] * (trade.MAX_ITEMS_PER_SIDE + 1)
    result = await trade.create_direct_offer("111", "222", give, [CardRef("111", "legendary")])
    assert result == "too_many_items"


@pytest.mark.asyncio
async def test_accept_fails_when_proposer_no_longer_holds_card(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    offer = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(offer, str)
    await _give_card(db_mod, "111", "222", "rare", qty=0)

    ok, err = await trade.accept_offer(offer.id, "222")

    assert (ok, err) == (False, "give_no_card")
    assert await _quantity(db_mod, "111", "111", "legendary") == 0


@pytest.mark.asyncio
async def test_accept_fails_when_recipient_no_longer_holds_card(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    offer = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(offer, str)
    await _give_card(db_mod, "222", "111", "legendary", qty=0)

    ok, err = await trade.accept_offer(offer.id, "222")

    assert (ok, err) == (False, "get_no_card")


@pytest.mark.asyncio
async def test_accept_rejects_non_recipient(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    offer = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(offer, str)

    ok, err = await trade.accept_offer(offer.id, "111")

    assert (ok, err) == (False, "not_recipient")


@pytest.mark.asyncio
async def test_accept_rejects_expired_offer(db):
    """A stale offer must not settle — the Discord view timeout is not a reliable clock."""
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    offer = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(offer, str)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute("UPDATE trade_offers SET expires_at = ? WHERE id = ?", (past, offer.id))
        await conn.commit()

    ok, err = await trade.accept_offer(offer.id, "222")

    assert (ok, err) == (False, "expired")
    assert await _quantity(db_mod, "222", "222", "rare") == 0


@pytest.mark.asyncio
async def test_decline_offer_is_recipient_only(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    offer = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(offer, str)
    assert not await trade.decline_offer(offer.id, "111")
    assert await trade.decline_offer(offer.id, "222")
    assert await _offer_status(db_mod, offer.id) == "declined"


@pytest.mark.asyncio
async def test_cancel_offer_is_proposer_only(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    offer = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(offer, str)
    assert not await trade.cancel_offer(offer.id, "222")
    assert await trade.cancel_offer(offer.id, "111")
    assert await _offer_status(db_mod, offer.id) == "cancelled"


# ─── Counters ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_counter_closes_parent_and_reverses_the_sides(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    original = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(original, str)

    counter = await trade.create_direct_offer(
        "222",
        "111",
        [CardRef("111", "legendary")],
        [CardRef("222", "rare")],
        counter_of_id=original.id,
    )

    assert not isinstance(counter, str)
    assert counter.counter_of_id == original.id
    assert (counter.proposer_id, counter.recipient_id) == ("222", "111")
    assert await _offer_status(db_mod, original.id) == "countered"


@pytest.mark.asyncio
async def test_counter_rejected_from_someone_who_is_not_the_recipient(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    await svc.sync_members([{"discord_id": "333", "display_name": "Carol", "avatar_url": None}])
    await _give_card(db_mod, "333", "222", "rare")
    original = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(original, str)

    result = await trade.create_direct_offer(
        "333",
        "111",
        [CardRef("222", "rare")],
        [CardRef("222", "rare")],
        counter_of_id=original.id,
    )

    assert result == "not_found"
    assert await _offer_status(db_mod, original.id) == "pending"


@pytest.mark.asyncio
async def test_counter_rejected_when_parent_already_settled(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    original = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(original, str)
    await trade.decline_offer(original.id, "222")

    result = await trade.create_direct_offer(
        "222",
        "111",
        [CardRef("111", "legendary")],
        [CardRef("222", "rare")],
        counter_of_id=original.id,
    )

    assert result == "not_found"


# ─── Queries ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_incoming_and_outgoing_offers_are_separate_views(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    offer = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(offer, str)

    assert [o.id for o in await trade.get_outgoing_offers("111")] == [offer.id]
    assert await trade.get_incoming_offers("111") == []
    assert [o.id for o in await trade.get_incoming_offers("222")] == [offer.id]
    assert await trade.get_outgoing_offers("222") == []


@pytest.mark.asyncio
async def test_settled_offers_leave_the_pending_views(db):
    db_mod, svc, trade = db
    await _seed_direct_pair(db_mod, svc)
    offer = await trade.create_direct_offer(
        "111", "222", [CardRef("222", "rare")], [CardRef("111", "legendary")]
    )
    assert not isinstance(offer, str)
    await trade.decline_offer(offer.id, "222")

    assert await trade.get_outgoing_offers("111") == []
    assert await trade.get_incoming_offers("222") == []
