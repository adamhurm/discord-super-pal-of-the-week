import logging
import random

import aiosqlite

from superpal.cards.db import DB_PATH

log = logging.getLogger(__name__)


async def get_balance(player_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT boin_balance FROM members WHERE discord_id = ?", (player_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] is not None else 0


async def add_boins(player_id: str, amount: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE members SET boin_balance = boin_balance + ? WHERE discord_id = ?",
            (amount, player_id),
        )
        await db.commit()


async def deduct_boins(player_id: str, amount: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT boin_balance FROM members WHERE discord_id = ?", (player_id,)
        ) as cur:
            row = await cur.fetchone()
        balance = row[0] if row and row[0] is not None else 0
        if balance < amount:
            return False
        await db.execute(
            "UPDATE members SET boin_balance = boin_balance - ? WHERE discord_id = ?",
            (amount, player_id),
        )
        await db.commit()
    return True


async def award_daily_to_all(member_ids: list[str]) -> dict[str, int]:
    """Award a random daily boin grant to all members. Returns {discord_id: amount} map."""
    results: dict[str, int] = {}
    async with aiosqlite.connect(DB_PATH) as db:
        for member_id in member_ids:
            amount = int(random.triangular(50, 200, 75))
            await db.execute(
                "UPDATE members SET boin_balance = boin_balance + ? WHERE discord_id = ?",
                (amount, member_id),
            )
            results[member_id] = amount
        await db.commit()
    return results


async def import_initial_balances(data: dict[str, int]) -> None:
    """Seed boin balances from a display_name → amount map. Logs unmatched names."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT discord_id, display_name FROM members") as cur:
            rows = await cur.fetchall()
        name_to_id = {row[1].lower(): row[0] for row in rows}

        for display_name, amount in data.items():
            discord_id = name_to_id.get(display_name.lower())
            if discord_id is None:
                log.warning("import_initial_balances: no match for %r", display_name)
                continue
            await db.execute(
                "UPDATE members SET boin_balance = ? WHERE discord_id = ?",
                (amount, discord_id),
            )
            log.info("import_initial_balances: set %d boins for %s (%s)", amount, display_name, discord_id)
        await db.commit()
