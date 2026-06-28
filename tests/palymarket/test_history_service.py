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


@pytest.mark.asyncio
async def test_get_player_portfolio_active(db):
    """Active bet appears in portfolio with correct yes_pct and estimated_payout."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    await _insert_member(db_mod.DB_PATH, "p2", 100)
    market = await svc.propose_market("Test", None, "p1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "p1", "yes", 50)
    await svc.place_or_update_bet(market.id, "p2", "no", 50)

    portfolio = await svc.get_player_portfolio("p1")
    assert len(portfolio["active"]) == 1
    assert portfolio["resolved"] == []
    pos = portfolio["active"][0]
    assert pos["market"].id == market.id
    assert pos["side"] == "yes"
    assert pos["amount"] == 50
    assert pos["yes_pct"] == 50          # 50 YES / 100 total = 50%
    assert pos["estimated_payout"] == 100  # floor(50/50 * 100) = 100


@pytest.mark.asyncio
async def test_get_player_portfolio_resolved(db):
    """Resolved win/loss appears in portfolio["resolved"] with correct amount_returned."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    await _insert_member(db_mod.DB_PATH, "p2", 100)
    market = await svc.propose_market("Test", None, "p1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "p1", "yes", 50)
    await svc.place_or_update_bet(market.id, "p2", "no", 50)
    await svc.close_market(market.id, "admin")
    await svc.resolve_market(market.id, "yes", "admin")

    portfolio_winner = await svc.get_player_portfolio("p1")
    assert portfolio_winner["active"] == []
    assert len(portfolio_winner["resolved"]) == 1
    r = portfolio_winner["resolved"][0]
    assert r["won"] is True
    assert r["amount_returned"] == 100  # floor(50/50 * 100)

    portfolio_loser = await svc.get_player_portfolio("p2")
    r2 = portfolio_loser["resolved"][0]
    assert r2["won"] is False
    assert r2["amount_returned"] == 0


@pytest.mark.asyncio
async def test_get_recent_activity(db):
    """Recent activity returns bets newest-first with display_name and market title."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    market = await svc.propose_market("My Market", None, "p1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "p1", "yes", 40)

    activity = await svc.get_recent_activity(limit=10)
    assert len(activity) >= 1
    row = activity[0]
    assert row["market_title"] == "My Market"
    assert row["side"] == "yes"
    assert row["amount"] == 40
    assert row["display_name"] == "p1"  # display_name equals discord_id in test fixture


@pytest.mark.asyncio
async def test_get_bets_for_market_with_names(db):
    """Returns bets with display_name from members table."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    market = await svc.propose_market("Test", None, "p1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "p1", "no", 25)

    bets = await svc.get_bets_for_market_with_names(market.id)
    assert len(bets) == 1
    assert bets[0]["display_name"] == "p1"
    assert bets[0]["side"] == "no"
    assert bets[0]["amount"] == 25
