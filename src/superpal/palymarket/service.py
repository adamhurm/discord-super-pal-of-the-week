from __future__ import annotations

import math
from datetime import datetime, timezone

import aiosqlite

from superpal.cards.db import DB_PATH
from superpal.palymarket.models import Bet, Market


def _parse_market(row: aiosqlite.Row) -> Market:
    return Market(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        created_by=row["created_by"],
        status=row["status"],
        outcome=row["outcome"],
        yes_pool=row["yes_pool"],
        no_pool=row["no_pool"],
        created_at=datetime.fromisoformat(row["created_at"]),
        resolved_at=(
            datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None
        ),
        resolved_by=row["resolved_by"],
    )


def _parse_bet(row: aiosqlite.Row) -> Bet:
    return Bet(
        id=row["id"],
        market_id=row["market_id"],
        player_id=row["player_id"],
        side=row["side"],
        amount=row["amount"],
        placed_at=datetime.fromisoformat(row["placed_at"]),
    )


async def get_palycoin_balance(player_id: str) -> int:
    """Return balance. Issue 100 Palycoin starting grant if balance==0 and player has no bets."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT palycoin_balance FROM members WHERE discord_id = ?",
            (player_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return 0
        balance = row["palycoin_balance"] if row["palycoin_balance"] is not None else 0
        if balance != 0:
            return balance
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT palycoin_balance FROM members WHERE discord_id = ?",
            (player_id,),
        ) as cur:
            row = await cur.fetchone()
        balance = row["palycoin_balance"] if row["palycoin_balance"] is not None else 0
        if balance != 0:
            await db.commit()
            return balance
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM market_bets WHERE player_id = ?",
            (player_id,),
        ) as cur:
            cnt_row = await cur.fetchone()
        if cnt_row["cnt"] > 0:
            await db.commit()
            return 0
        await db.execute(
            "UPDATE members SET palycoin_balance = palycoin_balance + 100 "
            "WHERE discord_id = ?",
            (player_id,),
        )
        await db.commit()
        return 100


async def exchange_pringles(player_id: str, pringle_amount: int) -> tuple[bool, str]:
    """Convert Pringles to Palycoins. Rate: 200 Pringles → 100 Palycoins."""
    if pringle_amount < 200:
        return False, "minimum_not_met"
    palycoin_gain = (pringle_amount // 200) * 100
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT pringle_balance FROM members WHERE discord_id = ?",
            (player_id,),
        ) as cur:
            row = await cur.fetchone()
        pringle_bal = (
            row["pringle_balance"] if row and row["pringle_balance"] is not None else 0
        )
        if pringle_bal < pringle_amount:
            return False, "not_enough_pringles"
        await db.execute(
            "UPDATE members "
            "SET pringle_balance = pringle_balance - ?, "
            "    palycoin_balance = palycoin_balance + ? "
            "WHERE discord_id = ?",
            (pringle_amount, palycoin_gain, player_id),
        )
        await db.commit()
    return True, ""


async def propose_market(title: str, description: str, created_by: str) -> Market:
    """Insert market with status='pending_approval'. Return the new Market."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "INSERT INTO markets (title, description, created_by, created_at) "
            "VALUES (?, ?, ?, ?)",
            (title, description, created_by, now),
        )
        market_id = cur.lastrowid
        await db.commit()
        async with db.execute(
            "SELECT * FROM markets WHERE id = ?",
            (market_id,),
        ) as cur2:
            row = await cur2.fetchone()
    return _parse_market(row)


async def approve_market(market_id: int, admin_id: str) -> tuple[bool, str]:
    """Set status='open'. Return (True, '') or (False, reason)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT status FROM markets WHERE id = ?",
            (market_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["status"] != "pending_approval":
            return False, "not_pending"
        await db.execute(
            "UPDATE markets SET status = 'open' WHERE id = ?",
            (market_id,),
        )
        await db.commit()
    return True, ""


async def reject_market(market_id: int, admin_id: str) -> tuple[bool, str]:
    """Set status='rejected'. Return (True, '') or (False, reason)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT status FROM markets WHERE id = ?",
            (market_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["status"] != "pending_approval":
            return False, "not_pending"
        await db.execute(
            "UPDATE markets SET status = 'rejected' WHERE id = ?",
            (market_id,),
        )
        await db.commit()
    return True, ""


async def close_market(market_id: int, admin_id: str) -> tuple[bool, str]:
    """Set status='closed'. Return (True, '') or (False, reason)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT status FROM markets WHERE id = ?",
            (market_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None or row["status"] != "open":
            return False, "not_open"
        await db.execute(
            "UPDATE markets SET status = 'closed' WHERE id = ?",
            (market_id,),
        )
        await db.commit()
    return True, ""


async def resolve_market(market_id: int, outcome: str, admin_id: str) -> dict:
    """Resolve market, compute parimutuel payouts, credit winners."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT * FROM markets WHERE id = ?",
            (market_id,),
        ) as cur:
            market_row = await cur.fetchone()
        if market_row is None or market_row["status"] != "closed":
            return {"error": "not_closed"}
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE markets SET status = 'resolved', outcome = ?, "
            "resolved_at = ?, resolved_by = ? WHERE id = ?",
            (outcome, now, admin_id, market_id),
        )
        async with db.execute(
            "SELECT * FROM market_bets WHERE market_id = ?",
            (market_id,),
        ) as cur:
            bet_rows = await cur.fetchall()
        total_pool = market_row["yes_pool"] + market_row["no_pool"]
        winning_bets = [r for r in bet_rows if r["side"] == outcome]
        winning_pool = sum(r["amount"] for r in winning_bets)
        payouts: list[dict] = []
        if winning_pool > 0:
            for bet_row in winning_bets:
                payout = math.floor(bet_row["amount"] / winning_pool * total_pool)
                await db.execute(
                    "UPDATE members SET palycoin_balance = palycoin_balance + ? "
                    "WHERE discord_id = ?",
                    (payout, bet_row["player_id"]),
                )
                payouts.append({"player_id": bet_row["player_id"], "payout": payout})
        await db.commit()
    return {
        "outcome": outcome,
        "total_pool": total_pool,
        "winner_count": len(winning_bets),
        "payouts": payouts,
    }


async def place_or_update_bet(
    market_id: int, player_id: str, side: str, amount: int
) -> tuple[bool, str]:
    """Place or update a bet. One bet per player per market."""
    if amount <= 0:
        return (False, "invalid_amount")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT status FROM markets WHERE id = ?",
            (market_id,),
        ) as cur:
            market_row = await cur.fetchone()
        if market_row is None or market_row["status"] != "open":
            return False, "market_not_open"
        async with db.execute(
            "SELECT id, side, amount FROM market_bets "
            "WHERE market_id = ? AND player_id = ?",
            (market_id, player_id),
        ) as cur:
            existing = await cur.fetchone()
        old_amount = existing["amount"] if existing else 0
        async with db.execute(
            "SELECT palycoin_balance FROM members WHERE discord_id = ?",
            (player_id,),
        ) as cur:
            bal_row = await cur.fetchone()
        balance = (
            bal_row["palycoin_balance"]
            if bal_row and bal_row["palycoin_balance"] is not None
            else 0
        )
        # After refunding the old bet, does the player have enough for the new one?
        if balance + old_amount < amount:
            return False, "insufficient_palycoins"
        # Remove old bet from the relevant pool
        if existing is not None:
            if existing["side"] == "yes":
                await db.execute(
                    "UPDATE markets SET yes_pool = yes_pool - ? WHERE id = ?",
                    (old_amount, market_id),
                )
            else:
                await db.execute(
                    "UPDATE markets SET no_pool = no_pool - ? WHERE id = ?",
                    (old_amount, market_id),
                )
        # Net palycoin change: refund old, charge new
        net_change = old_amount - amount
        if net_change != 0:
            await db.execute(
                "UPDATE members SET palycoin_balance = palycoin_balance + ? "
                "WHERE discord_id = ?",
                (net_change, player_id),
            )
        # Add new bet amount to the chosen pool
        if side == "yes":
            await db.execute(
                "UPDATE markets SET yes_pool = yes_pool + ? WHERE id = ?",
                (amount, market_id),
            )
        else:
            await db.execute(
                "UPDATE markets SET no_pool = no_pool + ? WHERE id = ?",
                (amount, market_id),
            )
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT OR REPLACE INTO market_bets "
            "(market_id, player_id, side, amount, placed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (market_id, player_id, side, amount, now),
        )
        await db.commit()
    return True, ""


async def get_market(market_id: int) -> Market | None:
    """Return market by id, or None if not found."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM markets WHERE id = ?",
            (market_id,),
        ) as cur:
            row = await cur.fetchone()
    return _parse_market(row) if row else None


async def list_markets(status: str | None = None) -> list[Market]:
    """Return all markets, optionally filtered by status."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status is not None:
            async with db.execute(
                "SELECT * FROM markets WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM markets ORDER BY created_at DESC",
            ) as cur:
                rows = await cur.fetchall()
    return [_parse_market(r) for r in rows]


async def get_bets_for_market(market_id: int) -> list[Bet]:
    """Return all bets for a market."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM market_bets WHERE market_id = ? ORDER BY placed_at",
            (market_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [_parse_bet(r) for r in rows]


async def get_player_bet(market_id: int, player_id: str) -> Bet | None:
    """Return the player's bet on a market, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM market_bets WHERE market_id = ? AND player_id = ?",
            (market_id, player_id),
        ) as cur:
            row = await cur.fetchone()
    return _parse_bet(row) if row else None


async def get_player_active_bets(player_id: str) -> list[tuple[Market, Bet]]:
    """Return (Market, Bet) pairs for all bets in non-resolved/rejected markets."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT
                m.id AS m_id, m.title, m.description, m.created_by, m.status,
                m.outcome, m.yes_pool, m.no_pool, m.created_at,
                m.resolved_at, m.resolved_by,
                b.id AS b_id, b.market_id, b.side, b.amount, b.placed_at
            FROM market_bets b
            JOIN markets m ON m.id = b.market_id
            WHERE b.player_id = ?
              AND m.status NOT IN ('resolved', 'rejected')
            ORDER BY b.placed_at DESC
        """
        async with db.execute(query, (player_id,)) as cur:
            rows = await cur.fetchall()
    result = []
    for row in rows:
        market = Market(
            id=row["m_id"],
            title=row["title"],
            description=row["description"],
            created_by=row["created_by"],
            status=row["status"],
            outcome=row["outcome"],
            yes_pool=row["yes_pool"],
            no_pool=row["no_pool"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None
            ),
            resolved_by=row["resolved_by"],
        )
        bet = Bet(
            id=row["b_id"],
            market_id=row["market_id"],
            player_id=player_id,
            side=row["side"],
            amount=row["amount"],
            placed_at=datetime.fromisoformat(row["placed_at"]),
        )
        result.append((market, bet))
    return result


async def list_pending_markets() -> list[Market]:
    """Return all markets with status='pending_approval'."""
    return await list_markets(status="pending_approval")
