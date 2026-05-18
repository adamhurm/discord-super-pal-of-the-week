import aiosqlite
import pytest


@pytest.fixture
async def db(db_mods):
    db_mod, svc_mod, _, ps_mod = db_mods
    await db_mod.init_db()
    # Seed two members
    await svc_mod.sync_members(
        [
            {"discord_id": "player1", "display_name": "Alice", "avatar_url": None},
            {"discord_id": "player2", "display_name": "Bob", "avatar_url": None},
        ]
    )
    return db_mod, svc_mod, ps_mod


@pytest.mark.asyncio
async def test_get_balance_defaults_zero(db):
    _, _, ps = db
    assert await ps.get_balance("player1") == 0


@pytest.mark.asyncio
async def test_buy_item_success(db):
    db_mod, _, ps = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute("UPDATE members SET pringle_balance = 200 WHERE discord_id = 'player1'")
        await conn.commit()
    ok, reason = await ps.buy_item("player1", "heal_potion")
    assert ok is True
    assert reason == ""
    balance = await ps.get_balance("player1")
    assert balance == 150
    items = await ps.get_player_items("player1")
    assert items.get("heal_potion") == 1


@pytest.mark.asyncio
async def test_buy_item_insufficient_pringles(db):
    _, _, ps = db
    ok, reason = await ps.buy_item("player1", "heal_potion")
    assert ok is False
    assert reason == "insufficient_pringles"


@pytest.mark.asyncio
async def test_buy_item_unknown_type(db):
    _, _, ps = db
    ok, reason = await ps.buy_item("player1", "not_real")
    assert ok is False
    assert reason == "unknown_item"


@pytest.mark.asyncio
async def test_award_fight_pringles_full_pay_quick(db):
    db_mod, _, ps = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute("UPDATE members SET pringle_balance = 100 WHERE discord_id = 'player1'")
        await conn.execute("UPDATE members SET pringle_balance = 100 WHERE discord_id = 'player2'")
        await conn.commit()
    result = await ps.award_fight_pringles("player1", "player2", "quick")
    assert result["loser_paid"] == 50
    assert result["shortfall"] == 0
    assert result["bank_covered"] == 0
    # winner gets 50 (no extended bonus in quick)
    assert await ps.get_balance("player1") == 150
    # loser pays 50 (no extended bonus)
    assert await ps.get_balance("player2") == 50


@pytest.mark.asyncio
async def test_award_fight_pringles_extended_bonus(db):
    db_mod, _, ps = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute("UPDATE members SET pringle_balance = 100 WHERE discord_id = 'player1'")
        await conn.execute("UPDATE members SET pringle_balance = 100 WHERE discord_id = 'player2'")
        await conn.commit()
    await ps.award_fight_pringles("player1", "player2", "extended")
    # winner: 100 + 50 (win) + 25 (extended) = 175
    assert await ps.get_balance("player1") == 175
    # loser: 100 - 50 (loss) + 25 (extended) = 75
    assert await ps.get_balance("player2") == 75


@pytest.mark.asyncio
async def test_bank_of_bringus_partial_pay(db):
    db_mod, _, ps = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute("UPDATE members SET pringle_balance = 100 WHERE discord_id = 'player1'")
        await conn.execute("UPDATE members SET pringle_balance = 30 WHERE discord_id = 'player2'")
        await conn.commit()
    result = await ps.award_fight_pringles("player1", "player2", "quick")
    assert result["loser_paid"] == 30
    assert result["shortfall"] == 20
    assert result["bank_covered"] == 10
    # winner: 100 + 30 (loser paid) + 10 (bank) = 140
    assert await ps.get_balance("player1") == 140
    # loser: 30 - 30 = 0, floor at 0
    assert await ps.get_balance("player2") == 0
    # bank_debt should be 20
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT bank_debt FROM members WHERE discord_id = 'player2'"
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 20


@pytest.mark.asyncio
async def test_bank_of_bringus_zero_pay(db):
    db_mod, _, ps = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute("UPDATE members SET pringle_balance = 100 WHERE discord_id = 'player1'")
        # player2 has 0 Pringles
        await conn.commit()
    result = await ps.award_fight_pringles("player1", "player2", "quick")
    assert result["loser_paid"] == 0
    assert result["shortfall"] == 50
    assert result["bank_covered"] == 25
    assert await ps.get_balance("player1") == 125
    assert await ps.get_balance("player2") == 0


@pytest.mark.asyncio
async def test_award_fight_pringles_escape_penalty(db):
    db_mod, _, ps = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute("UPDATE members SET pringle_balance = 100 WHERE discord_id = 'player1'")
        await conn.execute("UPDATE members SET pringle_balance = 100 WHERE discord_id = 'player2'")
        await conn.commit()
    # player2 escaped (11-15 roll) — they lose + escape penalty
    result = await ps.award_fight_pringles("player1", "player2", "extended", escape_penalty=True)
    # player2: 100 - 50 (loss) + 25 (extended) - 25 (escape) = 50
    assert await ps.get_balance("player2") == 50
    assert result["escape_paid"] == 25


@pytest.mark.asyncio
async def test_reset_heal_potions_empty_players(db):
    _db_mod, _, ps = db
    count = await ps.reset_heal_potions_for_empty_players()
    assert count == 2  # both players have 0 heal potions
    items1 = await ps.get_player_items("player1")
    items2 = await ps.get_player_items("player2")
    assert items1.get("heal_potion") == 2
    assert items2.get("heal_potion") == 2


@pytest.mark.asyncio
async def test_reset_heal_potions_skips_players_with_heals(db):
    db_mod, _, ps = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO player_items (player_id, item_type, quantity) "
            "VALUES ('player1', 'heal_potion', 1)"
        )
        await conn.commit()
    count = await ps.reset_heal_potions_for_empty_players()
    assert count == 1  # only player2 is reset
    assert (await ps.get_player_items("player1")).get("heal_potion") == 1  # unchanged
    assert (await ps.get_player_items("player2")).get("heal_potion") == 2
