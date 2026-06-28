import random

import aiosqlite

from superpal.cards.db import DB_PATH

MIN_BET = 10

# Roulette: bet_type → (winning_numbers_set_or_fn, payout_multiplier)
# Numbers 0-36; red = even numbers 2-36, black = odd numbers 1-35, green = 0
_RED = {2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36}
_BLACK = {1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 33, 35}
_FIRST_DOZEN = set(range(1, 13))
_SECOND_DOZEN = set(range(13, 25))
_THIRD_DOZEN = set(range(25, 37))

_ROULETTE_BETS: dict[str, tuple[set[int], int]] = {
    "red": (_RED, 2),
    "black": (_BLACK, 2),
    "green": ({0}, 14),
    "1st dozen": (_FIRST_DOZEN, 3),
    "2nd dozen": (_SECOND_DOZEN, 3),
    "3rd dozen": (_THIRD_DOZEN, 3),
}


async def _check_and_deduct(player_id: str, bet: int) -> tuple[bool, str]:
    if bet < MIN_BET:
        return False, f"minimum_bet_{MIN_BET}"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT boin_balance FROM members WHERE discord_id = ?", (player_id,)
        ) as cur:
            row = await cur.fetchone()
        balance = row[0] if row and row[0] is not None else 0
        if balance < bet:
            return False, "insufficient_boins"
        await db.execute(
            "UPDATE members SET boin_balance = boin_balance - ? WHERE discord_id = ?",
            (bet, player_id),
        )
        await db.commit()
    return True, ""


async def _award(player_id: str, amount: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE members SET boin_balance = boin_balance + ? WHERE discord_id = ?",
            (amount, player_id),
        )
        await db.commit()


async def play_dice(player_id: str, bet: int) -> dict:
    """Roll 2d6 vs the bot. Higher total wins 2× bet; tie refunds; lower loses."""
    ok, reason = await _check_and_deduct(player_id, bet)
    if not ok:
        return {"error": reason}

    player_roll = random.randint(1, 6) + random.randint(1, 6)
    bot_roll = random.randint(1, 6) + random.randint(1, 6)

    if player_roll > bot_roll:
        net = bet
        outcome = "win"
        await _award(player_id, bet * 2)
    elif player_roll == bot_roll:
        net = 0
        outcome = "tie"
        await _award(player_id, bet)
    else:
        net = -bet
        outcome = "lose"

    return {"player_roll": player_roll, "bot_roll": bot_roll, "outcome": outcome, "net": net}


async def play_rps(player_id: str, choice: str, bet: int) -> dict:
    """Rock/paper/scissors vs the bot. Win 2× bet; tie refunds; lose loses."""
    ok, reason = await _check_and_deduct(player_id, bet)
    if not ok:
        return {"error": reason}

    options = ["rock", "paper", "scissors"]
    bot_choice = random.choice(options)
    wins_against = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

    if choice == bot_choice:
        net = 0
        outcome = "tie"
        await _award(player_id, bet)
    elif wins_against[choice] == bot_choice:
        net = bet
        outcome = "win"
        await _award(player_id, bet * 2)
    else:
        net = -bet
        outcome = "lose"

    return {"bot_choice": bot_choice, "outcome": outcome, "net": net}


async def play_roulette(player_id: str, bet_type: str, bet: int) -> dict:
    """Roulette spin 0-36. Payouts: red/black 2×, green 14×, dozen 3×."""
    if bet_type not in _ROULETTE_BETS:
        return {"error": "unknown_bet_type"}
    ok, reason = await _check_and_deduct(player_id, bet)
    if not ok:
        return {"error": reason}

    spin = random.randint(0, 36)
    winning_numbers, multiplier = _ROULETTE_BETS[bet_type]

    if spin in winning_numbers:
        net = bet * (multiplier - 1)
        outcome = "win"
        await _award(player_id, bet * multiplier)
    else:
        net = -bet
        outcome = "lose"

    return {"spin": spin, "bet_type": bet_type, "outcome": outcome, "net": net}


async def play_guess(player_id: str, number: int, bet: int) -> dict:
    """Pick 1-10; match the bot's pick to win 9× bet."""
    ok, reason = await _check_and_deduct(player_id, bet)
    if not ok:
        return {"error": reason}

    bot_number = random.randint(1, 10)

    if number == bot_number:
        net = bet * 8
        outcome = "win"
        await _award(player_id, bet * 9)
    else:
        net = -bet
        outcome = "lose"

    return {"bot_number": bot_number, "outcome": outcome, "net": net}
