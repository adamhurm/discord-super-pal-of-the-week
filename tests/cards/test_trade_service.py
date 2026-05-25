import pytest

from superpal.cards.models import CardRef


@pytest.fixture
async def db(db_mods):
    db_mod, svc_mod, *_ = db_mods
    await db_mod.init_db()
    return db_mod, svc_mod


async def _seed_two_players(svc):
    """Insert Alice (111) and Bob (222) as members."""
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
    ])


async def _give_card(db_mod, owner_id: str, member_id: str, rarity: str, qty: int = 1):
    """Directly insert a user_card row."""
    import aiosqlite
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_id, card_member_id, rarity) DO UPDATE SET quantity = ?",
            (owner_id, member_id, rarity, qty, now, qty),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_create_listing_rejects_empty_items(db):
    _db_mod, svc = db
    await _seed_two_players(svc)
    result = await svc.create_listing("111", [], None)
    assert result == "empty_items"


@pytest.mark.asyncio
async def test_create_listing_rejects_unowned_card(db):
    _db_mod, svc = db
    await _seed_two_players(svc)
    result = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert result == "no_card"


@pytest.mark.asyncio
async def test_create_listing_success(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    result = await svc.create_listing("111", [CardRef("222", "common")], "want a rare")
    assert not isinstance(result, str)
    assert result.owner_id == "111"
    assert result.ask_note == "want a rare"
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_cancel_listing_owner_only(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    # Non-owner cannot cancel
    assert not await svc.cancel_listing(listing.id, "222")
    # Owner can cancel
    assert await svc.cancel_listing(listing.id, "111")


@pytest.mark.asyncio
async def test_get_active_listings_excludes_own(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    await svc.create_listing("111", [CardRef("222", "common")], None)
    listings = await svc.get_active_listings(exclude_owner_id="111")
    assert len(listings) == 0
    listings_all = await svc.get_active_listings()
    assert len(listings_all) == 1


@pytest.mark.asyncio
async def test_create_offer_rejects_self_offer(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    await _give_card(db_mod, "111", "222", "uncommon")
    result = await svc.create_offer(listing.id, "111", [CardRef("222", "uncommon")])
    assert result == "self_offer"


@pytest.mark.asyncio
async def test_create_offer_rejects_unowned_card(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    # Bob has no cards
    result = await svc.create_offer(listing.id, "222", [CardRef("111", "rare")])
    assert result == "no_card"


@pytest.mark.asyncio
async def test_create_offer_rejects_duplicate(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    await _give_card(db_mod, "222", "111", "uncommon", qty=2)
    await svc.create_offer(listing.id, "222", [CardRef("111", "uncommon")])
    result = await svc.create_offer(listing.id, "222", [CardRef("111", "uncommon")])
    assert result == "duplicate_offer"


@pytest.mark.asyncio
async def test_accept_offer_swaps_cards_and_declines_siblings(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    # Alice lists a COMMON of Bob
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    # Bob offers an UNCOMMON of Alice
    await _give_card(db_mod, "222", "111", "uncommon")
    offer = await svc.create_offer(listing.id, "222", [CardRef("111", "uncommon")])
    assert not isinstance(offer, str)
    # A second offer from a third player (use "333")
    await svc.sync_members([{"discord_id": "333", "display_name": "Carol", "avatar_url": None}])
    await _give_card(db_mod, "333", "111", "rare")
    offer2 = await svc.create_offer(listing.id, "333", [CardRef("111", "rare")])
    assert not isinstance(offer2, str)
    # Alice accepts Bob's offer
    ok, err = await svc.accept_offer(offer.id, "111")
    assert ok is True and err is None
    # Bob now has COMMON of Bob; Alice now has UNCOMMON of Alice
    import aiosqlite
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT quantity FROM user_cards WHERE owner_id='222' AND card_member_id='222' AND rarity='common'"
        ) as cur:
            bob_common = (await cur.fetchone())[0]
        async with conn.execute(
            "SELECT quantity FROM user_cards WHERE owner_id='111' AND card_member_id='111' AND rarity='uncommon'"
        ) as cur:
            alice_uncommon = (await cur.fetchone())[0]
        async with conn.execute(
            "SELECT status FROM trade_offers WHERE id=?", (offer2.id,)
        ) as cur:
            sibling_status = (await cur.fetchone())[0]
    assert bob_common == 1
    assert alice_uncommon == 1
    assert sibling_status == "declined"


@pytest.mark.asyncio
async def test_accept_offer_fails_if_card_no_longer_held(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    await _give_card(db_mod, "222", "111", "uncommon")
    offer = await svc.create_offer(listing.id, "222", [CardRef("111", "uncommon")])
    assert not isinstance(offer, str)
    # Remove Alice's listing card before she accepts
    import aiosqlite
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "UPDATE user_cards SET quantity = 0 WHERE owner_id='111' AND card_member_id='222' AND rarity='common'"
        )
        await conn.commit()
    ok, err = await svc.accept_offer(offer.id, "111")
    assert ok is False
    assert err == "listing_no_card"
