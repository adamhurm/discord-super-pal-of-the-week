import aiosqlite
import pytest


@pytest.fixture
async def tmp_db(db_mods):
    db_mod, *_ = db_mods
    return db_mod


@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_db):
    await tmp_db.init_db()
    async with aiosqlite.connect(tmp_db.DB_PATH) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            tables = {row[0] for row in await cur.fetchall()}
    assert {"members", "user_cards", "draw_log", "magic_links", "pending_trades"}.issubset(tables)


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_db):
    await tmp_db.init_db()
    await tmp_db.init_db()  # second call must not raise
