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
