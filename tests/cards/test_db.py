import os
import pytest
import aiosqlite
from superpal.cards.db import init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_cards.db")
    monkeypatch.setenv("CARDS_DB_PATH", db_file)
    # Re-import to pick up patched env var
    import importlib
    import superpal.cards.db as db_mod
    importlib.reload(db_mod)
    return db_mod


@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_db):
    await tmp_db.init_db()
    async with aiosqlite.connect(tmp_db.DB_PATH) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
    assert {"members", "user_cards", "draw_log", "magic_links"}.issubset(tables)


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_db):
    await tmp_db.init_db()
    await tmp_db.init_db()  # second call must not raise
