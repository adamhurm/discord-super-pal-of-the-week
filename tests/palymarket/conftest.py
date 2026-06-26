import importlib

import pytest


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Patch CARDS_DB_PATH, reload modules, init DB."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("CARDS_DB_PATH", db_file)

    import superpal.cards.db as db_mod
    import superpal.palymarket.service as svc_mod

    importlib.reload(db_mod)
    importlib.reload(svc_mod)

    await db_mod.init_db()
    return db_mod, svc_mod
