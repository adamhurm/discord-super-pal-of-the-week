import aiosqlite

from superpal.cards.db import DB_PATH

BOINS = "boins"
PRINGLES = "pringles"
PALYCOINS = "palycoins"

# (from, to) → (multiply_by, divide_by); received = floor(amount * mul / div)
_RATES: dict[tuple[str, str], tuple[int, int]] = {
    (BOINS, PRINGLES): (4, 1),
    (BOINS, PALYCOINS): (2, 1),
    (PRINGLES, BOINS): (1, 4),
    (PRINGLES, PALYCOINS): (1, 2),
    (PALYCOINS, BOINS): (1, 2),
    (PALYCOINS, PRINGLES): (2, 1),
}

_BALANCE_COL: dict[str, str] = {
    BOINS: "boin_balance",
    PRINGLES: "pringle_balance",
    PALYCOINS: "palycoin_balance",
}


async def exchange(
    player_id: str, from_currency: str, to_currency: str, amount: int
) -> tuple[bool, str, int]:
    """
    Convert `amount` of from_currency to to_currency for player_id.
    Returns (success, reason, received_amount).
    """
    if from_currency == to_currency:
        return False, "same_currency", 0
    if from_currency not in _BALANCE_COL or to_currency not in _BALANCE_COL:
        return False, "unknown_currency", 0
    rate = _RATES.get((from_currency, to_currency))
    if rate is None:
        return False, "unknown_pair", 0
    if amount <= 0:
        return False, "invalid_amount", 0

    mul, div = rate
    received = (amount * mul) // div
    if received <= 0:
        return False, "amount_too_small", 0

    from_col = _BALANCE_COL[from_currency]
    to_col = _BALANCE_COL[to_currency]

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            f"SELECT {from_col} FROM members WHERE discord_id = ?", (player_id,)
        ) as cur:
            row = await cur.fetchone()
        balance = row[0] if row and row[0] is not None else 0
        if balance < amount:
            return False, "insufficient_balance", 0

        await db.execute(
            f"UPDATE members SET {from_col} = {from_col} - ?, {to_col} = {to_col} + ? "
            "WHERE discord_id = ?",
            (amount, received, player_id),
        )
        await db.commit()

    return True, "", received
