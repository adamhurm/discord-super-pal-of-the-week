import aiosqlite
import pytest

_NOW = "2024-01-01 00:00:00"


async def _insert_member(
    db_path: str,
    discord_id: str,
    palycoin_balance: int = 0,
    pringle_balance: int = 0,
) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO members "
            "(discord_id, display_name, avatar_url, is_excluded, synced_at, "
            "palycoin_balance, pringle_balance) "
            "VALUES (?, ?, NULL, 0, ?, ?, ?)",
            (discord_id, discord_id, _NOW, palycoin_balance, pringle_balance),
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Palycoin economy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_starting_grant_new_player(db):
    """New player with 0 balance and no bets receives the 100-Palycoin starting grant."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1")
    balance = await svc.get_palycoin_balance("player1")
    assert balance == 100


@pytest.mark.asyncio
async def test_no_second_grant_after_bets(db):
    """Player who has placed bets gets their actual balance (0), not the starting grant."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1")
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        cur = await conn.execute(
            "INSERT INTO markets (title, created_by, status, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("Bet market", "admin", "open", _NOW),
        )
        market_id = cur.lastrowid
        await conn.execute(
            "INSERT INTO market_bets (market_id, player_id, side, amount, placed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (market_id, "player1", "yes", 50, _NOW),
        )
        await conn.commit()
    balance = await svc.get_palycoin_balance("player1")
    assert balance == 0


@pytest.mark.asyncio
async def test_exchange_pringles_success(db):
    """200 Pringles converts to 100 Palycoins; both balances update correctly."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1", pringle_balance=200)
    ok, reason = await svc.exchange_pringles("player1", 200)
    assert ok is True
    assert reason == ""
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT pringle_balance, palycoin_balance FROM members WHERE discord_id = 'player1'"
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 0    # 200 - 200
    assert row[1] == 100  # 0 + 100


@pytest.mark.asyncio
async def test_exchange_pringles_insufficient(db):
    """Exchange fails when the player has fewer Pringles than requested."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1", pringle_balance=100)
    ok, reason = await svc.exchange_pringles("player1", 200)
    assert ok is False
    assert reason == "not_enough_pringles"


@pytest.mark.asyncio
async def test_exchange_pringles_below_minimum(db):
    """Exchange fails when the requested amount is under the 200-Pringle minimum."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1", pringle_balance=200)
    ok, reason = await svc.exchange_pringles("player1", 100)
    assert ok is False
    assert reason == "minimum_not_met"


# ---------------------------------------------------------------------------
# Market lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_market_returns_market(db):
    """propose_market inserts a market in pending_approval status and returns it."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1")
    market = await svc.propose_market("Will it rain?", "Daily weather bet.", "player1")
    assert market.title == "Will it rain?"
    assert market.status == "pending_approval"
    assert market.created_by == "player1"


@pytest.mark.asyncio
async def test_approve_market_sets_open(db):
    """approve_market transitions a pending market to open."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1")
    market = await svc.propose_market("Test", None, "player1")
    ok, reason = await svc.approve_market(market.id, "admin")
    assert ok is True
    assert reason == ""
    updated = await svc.get_market(market.id)
    assert updated.status == "open"


@pytest.mark.asyncio
async def test_approve_wrong_status_returns_error(db):
    """approve_market returns an error when the market is not in pending_approval."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1")
    market = await svc.propose_market("Test", None, "player1")
    await svc.approve_market(market.id, "admin")  # now open
    ok, reason = await svc.approve_market(market.id, "admin")  # try again
    assert ok is False
    assert reason == "not_pending"


@pytest.mark.asyncio
async def test_reject_market_sets_rejected(db):
    """reject_market transitions a pending market to rejected."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1")
    market = await svc.propose_market("Test", None, "player1")
    ok, reason = await svc.reject_market(market.id, "admin")
    assert ok is True
    assert reason == ""
    updated = await svc.get_market(market.id)
    assert updated.status == "rejected"


@pytest.mark.asyncio
async def test_close_market_sets_closed(db):
    """close_market transitions an open market to closed."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1")
    market = await svc.propose_market("Test", None, "player1")
    await svc.approve_market(market.id, "admin")
    ok, reason = await svc.close_market(market.id, "admin")
    assert ok is True
    assert reason == ""
    updated = await svc.get_market(market.id)
    assert updated.status == "closed"


# ---------------------------------------------------------------------------
# Betting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_place_bet_deducts_palycoins(db):
    """Placing a bet immediately deducts the bet amount from palycoin_balance."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1", palycoin_balance=100)
    market = await svc.propose_market("Test", None, "player1")
    await svc.approve_market(market.id, "admin")
    ok, reason = await svc.place_or_update_bet(market.id, "player1", "yes", 50)
    assert ok is True
    assert reason == ""
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT palycoin_balance FROM members WHERE discord_id = 'player1'"
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 50  # 100 - 50


@pytest.mark.asyncio
async def test_update_bet_refunds_and_recharges(db):
    """Switching a bet refunds the old amount and charges the new one."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1", palycoin_balance=100)
    market = await svc.propose_market("Test", None, "player1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "player1", "yes", 50)  # balance → 50
    ok, reason = await svc.place_or_update_bet(market.id, "player1", "no", 30)
    assert ok is True
    assert reason == ""
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT palycoin_balance FROM members WHERE discord_id = 'player1'"
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == 70  # refund 50, charge 30 -> 100 - 30


@pytest.mark.asyncio
async def test_bet_on_closed_market_fails(db):
    """Placing a bet on a closed market returns market_not_open."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1", palycoin_balance=100)
    market = await svc.propose_market("Test", None, "player1")
    await svc.approve_market(market.id, "admin")
    await svc.close_market(market.id, "admin")
    ok, reason = await svc.place_or_update_bet(market.id, "player1", "yes", 50)
    assert ok is False
    assert reason == "market_not_open"


@pytest.mark.asyncio
async def test_bet_insufficient_palycoins_fails(db):
    """Placing a bet larger than the player's balance returns insufficient_palycoins."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1", palycoin_balance=10)
    market = await svc.propose_market("Test", None, "player1")
    await svc.approve_market(market.id, "admin")
    ok, reason = await svc.place_or_update_bet(market.id, "player1", "yes", 50)
    assert ok is False
    assert reason == "insufficient_palycoins"


@pytest.mark.asyncio
async def test_bet_nonpositive_amount_fails(db):
    """Non-positive bet amounts are rejected before touching DB."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "u1", pringle_balance=200)
    # Trigger starting grant (100 Palycoins)
    bal = await svc.get_palycoin_balance("u1")
    assert bal == 100
    market = await svc.propose_market("Test", "Desc", "u1")
    await svc.approve_market(market.id, "admin1")

    ok, reason = await svc.place_or_update_bet(market.id, "u1", "yes", 0)
    assert not ok
    assert reason == "invalid_amount"

    ok, reason = await svc.place_or_update_bet(market.id, "u1", "yes", -50)
    assert not ok
    assert reason == "invalid_amount"

    # Balance unchanged
    bal_after = await svc.get_palycoin_balance("u1")
    assert bal_after == 100


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_market_pays_winners(db):
    """YES winner receives the full pool; NO loser receives nothing."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1", palycoin_balance=100)
    await _insert_member(db_mod.DB_PATH, "player2", palycoin_balance=100)
    market = await svc.propose_market("Test", None, "player1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "player1", "yes", 50)
    await svc.place_or_update_bet(market.id, "player2", "no", 50)
    await svc.close_market(market.id, "admin")
    result = await svc.resolve_market(market.id, "yes", "admin")
    assert result["outcome"] == "yes"
    assert result["total_pool"] == 100
    assert result["winner_count"] == 1
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT palycoin_balance FROM members WHERE discord_id = 'player1'"
        ) as cur:
            row1 = await cur.fetchone()
        async with conn.execute(
            "SELECT palycoin_balance FROM members WHERE discord_id = 'player2'"
        ) as cur:
            row2 = await cur.fetchone()
    assert row1[0] == 150  # 50 remaining after bet + 100 payout
    assert row2[0] == 50   # 50 remaining after bet, no payout


@pytest.mark.asyncio
async def test_resolve_market_no_winners(db):
    """When all bets are on the losing side, no payouts are issued."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "player1", palycoin_balance=100)
    await _insert_member(db_mod.DB_PATH, "player2", palycoin_balance=100)
    market = await svc.propose_market("Test", None, "player1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "player1", "yes", 50)
    await svc.place_or_update_bet(market.id, "player2", "yes", 50)
    await svc.close_market(market.id, "admin")
    result = await svc.resolve_market(market.id, "no", "admin")
    assert result["winner_count"] == 0
    assert result["total_pool"] == 100
    assert result["payouts"] == []
