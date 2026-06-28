import aiosqlite
import importlib
import pytest


@pytest.fixture
async def db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("CARDS_DB_PATH", db_file)

    import superpal.cards.db as db_mod
    import superpal.palymarket.service as svc_mod

    importlib.reload(db_mod)
    importlib.reload(svc_mod)

    await db_mod.init_db()
    return db_mod, svc_mod


@pytest.mark.asyncio
async def test_probability_history_table_created(db):
    """init_db creates the market_probability_history table."""
    db_mod, _ = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='market_probability_history'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


_NOW = "2024-01-01 00:00:00"


async def _insert_member(db_path, discord_id, palycoin_balance=0):
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO members "
            "(discord_id, display_name, avatar_url, is_excluded, synced_at, palycoin_balance) "
            "VALUES (?, ?, NULL, 0, ?, ?)",
            (discord_id, discord_id, _NOW, palycoin_balance),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_place_bet_records_snapshot(db):
    """place_or_update_bet inserts a probability snapshot after the bet commits."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    await _insert_member(db_mod.DB_PATH, "p2", 100)
    market = await svc.propose_market("Test", None, "p1")
    await svc.approve_market(market.id, "admin")

    await svc.place_or_update_bet(market.id, "p1", "yes", 30)
    await svc.place_or_update_bet(market.id, "p2", "no", 70)

    history = await svc.get_probability_history(market.id)
    assert len(history) == 2
    pct1, _ = history[0]
    pct2, _ = history[1]
    # After first bet: 30 YES / 30 total = 1.0
    assert abs(pct1 - 1.0) < 0.001
    # After second bet: 30 YES / 100 total = 0.30
    assert abs(pct2 - 0.30) < 0.001


@pytest.mark.asyncio
async def test_get_probability_history_empty(db):
    """get_probability_history returns [] for a market with no bets yet."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    market = await svc.propose_market("Test", None, "p1")
    await svc.approve_market(market.id, "admin")
    history = await svc.get_probability_history(market.id)
    assert history == []
