import pytest
import aiosqlite
import importlib
from datetime import datetime, timezone


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
async def test_magic_link_consumed_once(db):
    db_mod, svc = db
    url = await svc.generate_magic_link("111", "collection", "http://localhost:8080")
    token = url.split("/")[-1]
    link1 = await svc.consume_magic_link(token)
    link2 = await svc.consume_magic_link(token)
    assert link1 is not None
    assert link1.session_token is not None
    assert link2 is None  # already consumed


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
