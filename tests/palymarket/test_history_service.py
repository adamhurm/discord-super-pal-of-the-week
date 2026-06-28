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
