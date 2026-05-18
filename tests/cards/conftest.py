import importlib

import pytest


@pytest.fixture
async def db_mods(tmp_path, monkeypatch):
    """Patch CARDS_DB_PATH and reload all card modules. Does NOT call init_db()."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("CARDS_DB_PATH", db_file)

    import superpal.cards.db as db_mod
    import superpal.cards.fight_service as fs_mod
    import superpal.cards.pringle_service as ps_mod
    import superpal.cards.service as svc_mod

    importlib.reload(db_mod)
    importlib.reload(svc_mod)
    importlib.reload(fs_mod)
    importlib.reload(ps_mod)
    return db_mod, svc_mod, fs_mod, ps_mod
