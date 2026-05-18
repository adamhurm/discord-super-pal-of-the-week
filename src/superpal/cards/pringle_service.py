import aiosqlite

from superpal.cards.db import DB_PATH

ITEM_COSTS: dict[str, int] = {
    "heal_potion": 50,
    "super_potion": 100,
    "bringus_boost": 75,
    "smoke_screen": 60,
}

ITEM_NAMES: dict[str, str] = {
    "heal_potion": "Heal Potion",
    "super_potion": "Super Potion",
    "bringus_boost": "Bringus Boost",
    "smoke_screen": "Smoke Screen",
}

ITEM_DESCRIPTIONS: dict[str, str] = {
    "heal_potion": "Restore 40 HP to your active card",
    "super_potion": "Restore 80 HP to your active card",
    "bringus_boost": "+10 ATK for your next 3 turns",
    "smoke_screen": "Opponent's next attack auto-misses",
}


async def get_balance(player_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT pringle_balance FROM members WHERE discord_id = ?",
            (player_id,),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] is not None else 0


async def get_player_items(player_id: str) -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT item_type, quantity FROM player_items WHERE player_id = ? AND quantity > 0",
            (player_id,),
        ) as cur:
            rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}


async def buy_item(player_id: str, item_type: str) -> tuple[bool, str]:
    """Buy an item from the shop. Returns (success, reason)."""
    if item_type not in ITEM_COSTS:
        return False, "unknown_item"
    cost = ITEM_COSTS[item_type]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT pringle_balance FROM members WHERE discord_id = ?",
            (player_id,),
        ) as cur:
            row = await cur.fetchone()
        balance = row[0] if row and row[0] is not None else 0
        if balance < cost:
            return False, "insufficient_pringles"

        await db.execute(
            "UPDATE members SET pringle_balance = pringle_balance - ? WHERE discord_id = ?",
            (cost, player_id),
        )
        await db.execute(
            """
            INSERT INTO player_items (player_id, item_type, quantity)
            VALUES (?, ?, 1)
            ON CONFLICT(player_id, item_type)
            DO UPDATE SET quantity = quantity + 1
            """,
            (player_id, item_type),
        )
        await db.commit()
    return True, ""


async def award_fight_pringles(
    winner_id: str,
    loser_id: str,
    mode: str,
    escape_penalty: bool = False,
) -> dict:
    """
    Award Pringles after a fight ends. Handles Bank of Bringus for unpayable debts.
    escape_penalty=True applies an extra -25 to the loser (11-15 run roll).
    Returns a summary dict of what was transferred.
    """
    WIN_AWARD = 50
    LOSE_COST = 50
    EXTENDED_BONUS = 25
    ESCAPE_PENALTY = 25

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")

        async with db.execute(
            "SELECT pringle_balance FROM members WHERE discord_id = ?",
            (loser_id,),
        ) as cur:
            row = await cur.fetchone()
        loser_balance = row[0] if row and row[0] is not None else 0

        loser_paid = min(loser_balance, LOSE_COST)
        shortfall = LOSE_COST - loser_paid
        bank_covered = shortfall // 2

        winner_receives = loser_paid + bank_covered
        extended_bonus = EXTENDED_BONUS if mode == "extended" else 0

        # Deduct loss from loser (floor at 0)
        await db.execute(
            "UPDATE members SET pringle_balance = MAX(0, pringle_balance - ?) WHERE discord_id = ?",
            (LOSE_COST, loser_id),
        )

        if shortfall > 0:
            await db.execute(
                "UPDATE members SET bank_debt = bank_debt + ? WHERE discord_id = ?",
                (shortfall, loser_id),
            )

        # Apply escape penalty if applicable
        escape_paid = 0
        if escape_penalty:
            async with db.execute(
                "SELECT pringle_balance FROM members WHERE discord_id = ?",
                (loser_id,),
            ) as cur:
                row2 = await cur.fetchone()
            current = row2[0] if row2 and row2[0] is not None else 0
            escape_paid = min(current, ESCAPE_PENALTY)
            await db.execute(
                "UPDATE members SET pringle_balance = MAX(0, pringle_balance - ?) WHERE discord_id = ?",
                (ESCAPE_PENALTY, loser_id),
            )
            winner_receives += escape_paid

        # Award winner: fight transfer + extended bonus
        await db.execute(
            "UPDATE members SET pringle_balance = pringle_balance + ? WHERE discord_id = ?",
            (winner_receives + extended_bonus, winner_id),
        )

        # Extended participation bonus for loser too
        if mode == "extended":
            await db.execute(
                "UPDATE members SET pringle_balance = pringle_balance + ? WHERE discord_id = ?",
                (extended_bonus, loser_id),
            )

        await db.commit()

    return {
        "loser_paid": loser_paid,
        "shortfall": shortfall,
        "bank_covered": bank_covered,
        "winner_receives": winner_receives + extended_bonus,
        "loser_net": -(loser_paid + escape_paid) + extended_bonus,
        "bank_message": shortfall > 0,
        "escape_paid": escape_paid,
    }


async def reset_heal_potions_for_empty_players() -> int:
    """Reset Heal Potions to 2 for all players with 0 on hand. Returns count reset."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_id FROM members WHERE is_excluded = 0"
        ) as cur:
            all_players = [r[0] for r in await cur.fetchall()]

        async with db.execute(
            "SELECT player_id FROM player_items WHERE item_type = 'heal_potion' AND quantity > 0"
        ) as cur:
            has_heals = {r[0] for r in await cur.fetchall()}

        empty_players = [p for p in all_players if p not in has_heals]
        for player_id in empty_players:
            await db.execute(
                """
                INSERT INTO player_items (player_id, item_type, quantity)
                VALUES (?, 'heal_potion', 2)
                ON CONFLICT(player_id, item_type)
                DO UPDATE SET quantity = 2
                """,
                (player_id,),
            )
        await db.commit()

    return len(empty_players)
