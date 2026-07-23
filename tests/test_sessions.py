"""Tests for the unified web session service."""

import importlib
from datetime import timedelta

import pytest
from freezegun import freeze_time


@pytest.fixture
async def sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("CARDS_DB_PATH", str(tmp_path / "test.db"))

    import superpal.cards.db as db_mod
    import superpal.sessions as sessions_mod

    importlib.reload(db_mod)
    importlib.reload(sessions_mod)
    await db_mod.init_db()
    return sessions_mod


@pytest.mark.asyncio
async def test_create_and_get_roundtrip(sessions):
    created = await sessions.create_session("111", "collection")
    fetched = await sessions.get_session(created.token)
    assert fetched is not None
    assert fetched.user_id == "111"
    assert fetched.scope == "collection"
    assert fetched.is_admin is False
    assert fetched.fight_id is None


@pytest.mark.asyncio
async def test_get_unknown_token_returns_none(sessions):
    assert await sessions.get_session("nope") is None


@pytest.mark.asyncio
async def test_admin_scope(sessions):
    created = await sessions.create_session("111", "admin")
    fetched = await sessions.get_session(created.token)
    assert fetched is not None
    assert fetched.is_admin is True
    assert fetched.fight_id is None


@pytest.mark.asyncio
async def test_fight_scope_parsing(sessions):
    created = await sessions.create_session("111", "fight:42")
    fetched = await sessions.get_session(created.token)
    assert fetched is not None
    assert fetched.fight_id == 42
    assert fetched.is_admin is False


@pytest.mark.asyncio
async def test_session_expires(sessions):
    with freeze_time("2026-01-01 12:00:00") as frozen:
        created = await sessions.create_session("111", "collection")
        frozen.tick(timedelta(hours=25))
        assert await sessions.get_session(created.token) is None


@pytest.mark.asyncio
async def test_rolling_expiry_extends_on_use(sessions):
    with freeze_time("2026-01-01 12:00:00") as frozen:
        created = await sessions.create_session("111", "collection")
        frozen.tick(timedelta(hours=23))
        assert await sessions.get_session(created.token) is not None
        # 46h after creation — dead without the rolling extension
        frozen.tick(timedelta(hours=23))
        assert await sessions.get_session(created.token) is not None
        frozen.tick(timedelta(hours=25))
        assert await sessions.get_session(created.token) is None


@pytest.mark.asyncio
async def test_delete_expired_sessions(sessions):
    with freeze_time("2026-01-01 12:00:00") as frozen:
        stale = await sessions.create_session("111", "collection")
        frozen.tick(timedelta(hours=25))
        fresh = await sessions.create_session("222", "collection")
        removed = await sessions.delete_expired_sessions()
        assert removed == 1
        assert await sessions.get_session(fresh.token) is not None
        assert await sessions.get_session(stale.token) is None
