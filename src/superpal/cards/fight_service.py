import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from math import floor

import aiosqlite

from superpal.cards.db import DB_PATH
from superpal.cards.models import Fight, FightCard, FightLogEntry

FIGHT_TOKEN_EXPIRY_MINUTES = 5
FIGHT_SESSION_HOURS = 24
CHALLENGE_EXPIRY_MINUTES = 5
INACTIVITY_EXPIRE_MINUTES = 10
AUTO_PASS_MINUTES = 3

RARITY_STATS: dict[str, dict] = {
    "common": {"hp": 80, "atk_bonus": 0},
    "uncommon": {"hp": 100, "atk_bonus": 5},
    "rare": {"hp": 130, "atk_bonus": 10},
    "legendary": {"hp": 170, "atk_bonus": 20},
}

ATTACKS: dict[str, dict] = {
    "vibe_check": {"name": "Vibe Check", "base_damage": 15, "min_roll": 1},
    "body_slam": {"name": "Body Slam", "base_damage": 20, "min_roll": 6},
    "hype_strike": {"name": "Hype Strike", "base_damage": 25, "min_roll": 10},
    "super_bringus_beam": {"name": "Super Bringus Beam", "base_damage": 35, "min_roll": 14},
}

ITEM_EFFECTS: dict[str, dict] = {
    "heal_potion": {"hp_restore": 40},
    "super_potion": {"hp_restore": 80},
    "bringus_boost": {"atk_boost_turns": 3},
    "smoke_screen": {"smoke_screen": True},
}


def roll_d20() -> int:
    return random.randint(1, 20)


def calc_damage(attack_key: str, atk_bonus: int, roll: int) -> tuple[int, str]:
    """Calculate damage from an attack roll. Returns (damage, tier_name)."""
    attack = ATTACKS[attack_key]
    base = attack["base_damage"]
    min_roll = attack["min_roll"]

    if roll < min_roll:
        return 0, "miss"
    if roll <= 10:
        return floor((base + atk_bonus) * 0.5), "glancing"
    if roll <= 16:
        return floor((base + atk_bonus) * 1.0), "direct"
    if roll <= 19:
        return floor((base + atk_bonus) * 1.5), "critical"
    return floor((base + atk_bonus) * 2.0), "nat20"


def _row_to_fight(row: aiosqlite.Row) -> Fight:
    return Fight(
        id=row[0],
        mode=row[1],
        challenger_id=row[2],
        opponent_id=row[3],
        status=row[4],
        winner_id=row[5],
        current_turn_player_id=row[6],
        pending_swap_player_id=row[7],
        channel_id=row[8],
        challenger_ready=bool(row[9]),
        opponent_ready=bool(row[10]),
        challenger_atk_boost=row[11],
        opponent_atk_boost=row[12],
        challenger_smoked=bool(row[13]),
        opponent_smoked=bool(row[14]),
        created_at=row[15],
        started_at=row[16],
        completed_at=row[17],
        expires_at=row[18],
        last_activity_at=row[19],
    )


_FIGHT_SELECT = (
    "SELECT id, mode, challenger_id, opponent_id, status, winner_id, "
    "current_turn_player_id, pending_swap_player_id, channel_id, "
    "challenger_ready, opponent_ready, challenger_atk_boost, opponent_atk_boost, "
    "challenger_smoked, opponent_smoked, created_at, started_at, completed_at, "
    "expires_at, last_activity_at FROM fights"
)


async def get_fight(fight_id: int) -> Fight | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"{_FIGHT_SELECT} WHERE id = ?", (fight_id,)) as cur:
            row = await cur.fetchone()
    return _row_to_fight(row) if row else None


async def get_fight_cards(fight_id: int) -> list[FightCard]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, fight_id, player_id, card_member_id, rarity, slot, "
            "hp_current, hp_max, is_active, is_fainted FROM fight_cards WHERE fight_id = ?",
            (fight_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        FightCard(
            id=r[0],
            fight_id=r[1],
            player_id=r[2],
            card_member_id=r[3],
            rarity=r[4],
            slot=r[5],
            hp_current=r[6],
            hp_max=r[7],
            is_active=bool(r[8]),
            is_fainted=bool(r[9]),
        )
        for r in rows
    ]


async def get_fight_log(fight_id: int, limit: int = 20) -> list[FightLogEntry]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, fight_id, actor_id, action_type, action_detail, d20_roll, "
            "damage_dealt, narrative_text, created_at FROM fight_log "
            "WHERE fight_id = ? ORDER BY id DESC LIMIT ?",
            (fight_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [
        FightLogEntry(
            id=r[0],
            fight_id=r[1],
            actor_id=r[2],
            action_type=r[3],
            action_detail=r[4],
            d20_roll=r[5],
            damage_dealt=r[6],
            narrative_text=r[7],
            created_at=r[8],
        )
        for r in reversed(list(rows))
    ]


async def create_fight(
    challenger_id: str,
    opponent_id: str,
    mode: str,
    channel_id: str | None = None,
) -> Fight:
    now = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=CHALLENGE_EXPIRY_MINUTES)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO fights (mode, challenger_id, opponent_id, channel_id, "
            "created_at, expires_at, last_activity_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mode, challenger_id, opponent_id, channel_id, now, expires, now),
        )
        fight_id = cur.lastrowid
        await db.commit()
        async with db.execute(f"{_FIGHT_SELECT} WHERE id = ?", (fight_id,)) as c:
            row = await c.fetchone()
    assert row is not None
    return _row_to_fight(row)


async def accept_fight(fight_id: int) -> Fight | None:
    """Set fight status to 'lobby'. Returns None if fight is not in pending state."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE fights SET status = 'lobby', last_activity_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now, fight_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            return None
        async with db.execute(f"{_FIGHT_SELECT} WHERE id = ?", (fight_id,)) as c:
            row = await c.fetchone()
    return _row_to_fight(row) if row else None


async def decline_fight(fight_id: int) -> Fight | None:
    """Set fight status to 'declined'. Returns None if fight is not in pending state."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE fights SET status = 'declined', last_activity_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (now, fight_id),
        )
        await db.commit()
        if cur.rowcount == 0:
            return None
        async with db.execute(f"{_FIGHT_SELECT} WHERE id = ?", (fight_id,)) as c:
            row = await c.fetchone()
    return _row_to_fight(row) if row else None


async def get_pending_challenges(opponent_id: str) -> list[Fight]:
    """Return pending fights where opponent_id is the challenged player, newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"{_FIGHT_SELECT} WHERE status = 'pending' AND opponent_id = ? "
            "ORDER BY created_at DESC",
            (opponent_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_fight(row) for row in rows]


async def get_active_fight_between(player_a: str, player_b: str) -> Fight | None:
    """Return the most recent unresolved fight (pending/lobby/active) between two players."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"{_FIGHT_SELECT} WHERE status IN ('pending','lobby','active') "
            "AND ((challenger_id = ? AND opponent_id = ?) "
            "OR (challenger_id = ? AND opponent_id = ?)) ORDER BY id DESC LIMIT 1",
            (player_a, player_b, player_b, player_a),
        ) as cur:
            row = await cur.fetchone()
    return _row_to_fight(row) if row else None


async def create_fight_token(fight_id: int, player_id: str, base_url: str) -> str:
    """Create a one-time fight lobby token. Returns the full lobby URL."""
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    expires = (datetime.now(timezone.utc) + timedelta(hours=FIGHT_SESSION_HOURS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO fight_tokens (token, fight_id, player_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, fight_id, player_id, now, expires),
        )
        await db.commit()
    return f"{base_url}/fight/{fight_id}/lobby?ft={token}"


async def use_fight_token(token: str) -> tuple[int, str, str] | None:
    """
    Validate and consume a fight token; create a session.
    Returns (fight_id, player_id, session_token) or None if invalid/expired.
    Idempotent: repeated calls (e.g. from Discord's link preview) return the same session.
    """
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT fight_id, player_id, expires_at, session_token "
            "FROM fight_tokens WHERE token = ?",
            (token,),
        ) as cur:
            row = await cur.fetchone()
        if not row or row[2] < now:
            return None
        fight_id, player_id, _, existing_session = row[0], row[1], row[2], row[3]

        if existing_session:
            return fight_id, player_id, existing_session

        session_token = str(uuid.uuid4())
        expires = (datetime.now(timezone.utc) + timedelta(hours=FIGHT_SESSION_HOURS)).isoformat()
        await db.execute(
            "INSERT INTO fight_sessions (session_token, fight_id, player_id, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (session_token, fight_id, player_id, expires),
        )
        await db.execute(
            "UPDATE fight_tokens SET session_token = ? WHERE token = ?",
            (session_token, token),
        )
        await db.commit()

    return fight_id, player_id, session_token


async def get_fight_session(session_token: str) -> dict | None:
    """Validate a fight session token. Returns {fight_id, player_id} or None."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT fight_id, player_id FROM fight_sessions "
            "WHERE session_token = ? AND expires_at > ?",
            (session_token, now),
        ) as cur:
            row = await cur.fetchone()
    return {"fight_id": row[0], "player_id": row[1]} if row else None


async def set_fight_cards(
    fight_id: int,
    player_id: str,
    card_slots: list[dict],
) -> bool:
    """
    Set a player's fight cards. card_slots is a list of {card_member_id, rarity, slot}.
    Returns False if any card is not owned by the player.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        for slot_info in card_slots:
            async with db.execute(
                "SELECT quantity FROM user_cards WHERE owner_id = ? "
                "AND card_member_id = ? AND rarity = ?",
                (player_id, slot_info["card_member_id"], slot_info["rarity"]),
            ) as cur:
                row = await cur.fetchone()
            if not row or row[0] < 1:
                return False

        await db.execute(
            "DELETE FROM fight_cards WHERE fight_id = ? AND player_id = ?",
            (fight_id, player_id),
        )
        for slot_info in card_slots:
            rarity = slot_info["rarity"]
            hp = RARITY_STATS[rarity]["hp"]
            is_active = 1 if slot_info["slot"] == 1 else 0
            await db.execute(
                "INSERT INTO fight_cards (fight_id, player_id, card_member_id, rarity, "
                "slot, hp_current, hp_max, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fight_id,
                    player_id,
                    slot_info["card_member_id"],
                    rarity,
                    slot_info["slot"],
                    hp,
                    hp,
                    is_active,
                ),
            )
        await db.commit()
    return True


async def mark_player_ready(fight_id: int, player_id: str) -> tuple[bool, str | None]:
    """
    Mark a player as ready. Returns (both_ready, first_turn_player_id).
    If both are ready, starts the fight with a coin toss.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"{_FIGHT_SELECT} WHERE id = ?", (fight_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return False, None
        fight = _row_to_fight(row)

        if fight.status != "lobby":
            return False, None

        is_challenger = player_id == fight.challenger_id
        col = "challenger_ready" if is_challenger else "opponent_ready"
        await db.execute(f"UPDATE fights SET {col} = 1 WHERE id = ?", (fight_id,))

        # Reload to check both ready
        async with db.execute(f"{_FIGHT_SELECT} WHERE id = ?", (fight_id,)) as cur:
            row = await cur.fetchone()
        assert row is not None
        fight = _row_to_fight(row)

        if not (fight.challenger_ready and fight.opponent_ready):
            await db.commit()
            return False, None

        # Coin toss for first turn
        first_turn = random.choice([fight.challenger_id, fight.opponent_id])
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE fights SET status = 'active', current_turn_player_id = ?, "
            "started_at = ?, last_activity_at = ? WHERE id = ?",
            (first_turn, now, now, fight_id),
        )
        await db.execute(
            "INSERT INTO fight_log (fight_id, action_type, narrative_text) VALUES (?, 'system', ?)",
            (fight_id, f"The fight begins! Coin toss: <@{first_turn}> goes first."),
        )
        await db.commit()

    return True, first_turn


async def get_fight_state(fight_id: int) -> dict:
    """Build the full state JSON dict sent to both WS clients."""
    fight = await get_fight(fight_id)
    if not fight:
        return {"error": "fight_not_found"}

    cards = await get_fight_cards(fight_id)
    log_entries = await get_fight_log(fight_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_id, display_name, avatar_url FROM members WHERE discord_id IN (?, ?)",
            (fight.challenger_id, fight.opponent_id),
        ) as cur:
            member_rows = {
                r[0]: {"display_name": r[1], "avatar_url": r[2]} for r in await cur.fetchall()
            }

        # Get card member names and avatars
        card_member_ids = list({c.card_member_id for c in cards})
        if card_member_ids:
            placeholders = ",".join("?" * len(card_member_ids))
            async with db.execute(
                "SELECT discord_id, display_name, avatar_url FROM members "
                f"WHERE discord_id IN ({placeholders})",
                card_member_ids,
            ) as cur:
                card_info = {
                    r[0]: {"display_name": r[1], "avatar_url": r[2]}
                    for r in await cur.fetchall()
                }
        else:
            card_info = {}

        player_ids = [fight.challenger_id, fight.opponent_id]
        id_placeholders = ",".join("?" * len(player_ids))
        async with db.execute(
            f"SELECT player_id, item_type, quantity FROM player_items"
            f" WHERE player_id IN ({id_placeholders}) AND quantity > 0",
            player_ids,
        ) as cur:
            item_rows = await cur.fetchall()
    items_by_player: dict[str, dict[str, int]] = {pid: {} for pid in player_ids}
    for row in item_rows:
        items_by_player[row[0]][row[1]] = row[2]

    def player_state(pid: str) -> dict:
        player_cards = [c for c in cards if c.player_id == pid]
        is_challenger = pid == fight.challenger_id
        return {
            "player_id": pid,
            "display_name": member_rows.get(pid, {}).get("display_name", pid),
            "atk_boost": fight.challenger_atk_boost if is_challenger else fight.opponent_atk_boost,
            "smoked": fight.challenger_smoked if is_challenger else fight.opponent_smoked,
            "items": items_by_player.get(pid, {}),
            "cards": [
                {
                    "id": c.id,
                    "slot": c.slot,
                    "card_member_id": c.card_member_id,
                    "display_name": card_info.get(c.card_member_id, {}).get(
                        "display_name", c.card_member_id
                    ),
                    "avatar_url": card_info.get(c.card_member_id, {}).get("avatar_url"),
                    "rarity": c.rarity,
                    "hp_current": c.hp_current,
                    "hp_max": c.hp_max,
                    "is_active": c.is_active,
                    "is_fainted": c.is_fainted,
                }
                for c in sorted(player_cards, key=lambda x: x.slot)
            ],
        }

    return {
        "fight_id": fight_id,
        "mode": fight.mode,
        "status": fight.status,
        "current_turn_player_id": fight.current_turn_player_id,
        "pending_swap_player_id": fight.pending_swap_player_id,
        "winner_id": fight.winner_id,
        "challenger": player_state(fight.challenger_id),
        "opponent": player_state(fight.opponent_id),
        "log": [
            {
                "id": e.id,
                "actor_id": e.actor_id,
                "action_type": e.action_type,
                "action_detail": e.action_detail,
                "d20_roll": e.d20_roll,
                "damage_dealt": e.damage_dealt,
                "narrative_text": e.narrative_text,
                "created_at": e.created_at,
            }
            for e in log_entries
        ],
    }


def _other_player(fight: Fight, player_id: str) -> str:
    return fight.opponent_id if player_id == fight.challenger_id else fight.challenger_id


def _is_challenger(fight: Fight, player_id: str) -> bool:
    return player_id == fight.challenger_id


async def _log_action(
    db: aiosqlite.Connection,
    fight_id: int,
    actor_id: str | None,
    action_type: str,
    narrative: str,
    d20_roll: int | None = None,
    damage: int | None = None,
    detail: dict | None = None,
) -> None:
    await db.execute(
        "INSERT INTO fight_log (fight_id, actor_id, action_type, action_detail, "
        "d20_roll, damage_dealt, narrative_text) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            fight_id,
            actor_id,
            action_type,
            json.dumps(detail) if detail else None,
            d20_roll,
            damage,
            narrative,
        ),
    )


async def _get_active_card(
    db: aiosqlite.Connection, fight_id: int, player_id: str
) -> aiosqlite.Row | None:
    async with db.execute(
        "SELECT id, rarity, hp_current, hp_max FROM fight_cards "
        "WHERE fight_id = ? AND player_id = ? AND is_active = 1 AND is_fainted = 0",
        (fight_id, player_id),
    ) as cur:
        return await cur.fetchone()


async def _apply_damage(db: aiosqlite.Connection, card_id: int, damage: int) -> tuple[int, bool]:
    """Apply damage to a card. Returns (new_hp, fainted)."""
    async with db.execute("SELECT hp_current FROM fight_cards WHERE id = ?", (card_id,)) as cur:
        row = await cur.fetchone()
    assert row is not None
    new_hp = max(0, row[0] - damage)
    fainted = new_hp == 0
    await db.execute(
        "UPDATE fight_cards SET hp_current = ?, is_fainted = ?, is_active = ? WHERE id = ?",
        (new_hp, 1 if fainted else 0, 0 if fainted else 1, card_id),
    )
    return new_hp, fainted


async def _check_all_fainted(db: aiosqlite.Connection, fight_id: int, player_id: str) -> bool:
    async with db.execute(
        "SELECT COUNT(*) FROM fight_cards WHERE fight_id = ? AND player_id = ? AND is_fainted = 0",
        (fight_id, player_id),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0] == 0


async def _has_non_fainted_cards(db: aiosqlite.Connection, fight_id: int, player_id: str) -> bool:
    async with db.execute(
        "SELECT COUNT(*) FROM fight_cards WHERE fight_id = ? AND player_id = ? "
        "AND is_fainted = 0 AND is_active = 0",
        (fight_id, player_id),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0] > 0


async def _finish_fight(db: aiosqlite.Connection, fight_id: int, winner_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE fights SET status = 'completed', winner_id = ?, completed_at = ? WHERE id = ?",
        (winner_id, now, fight_id),
    )


async def _advance_turn(db: aiosqlite.Connection, fight_id: int, next_player_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE fights SET current_turn_player_id = ?, last_activity_at = ? WHERE id = ?",
        (next_player_id, now, fight_id),
    )


async def _handle_attack(
    db: aiosqlite.Connection,
    fight: Fight,
    player_id: str,
    attack_key: str,
    detail: dict,
) -> tuple[bool, str]:
    """Process an attack action. Returns (fight_ended, narrative)."""
    if attack_key not in ATTACKS:
        raise ValueError(f"Unknown attack: {attack_key}")

    is_ch = _is_challenger(fight, player_id)
    opponent_id = _other_player(fight, player_id)

    # Check smoke screen (player's attack is auto-missed)
    smoked = fight.challenger_smoked if is_ch else fight.opponent_smoked
    smoked_col = "challenger_smoked" if is_ch else "opponent_smoked"
    if smoked:
        await db.execute(f"UPDATE fights SET {smoked_col} = 0 WHERE id = ?", (fight.id,))
        await _log_action(
            db,
            fight.id,
            player_id,
            "attack",
            f"<@{player_id}>'s {ATTACKS[attack_key]['name']} was blocked by Smoke Screen!",
            detail={**detail, "tier": "miss"},
        )
        await _advance_turn(db, fight.id, opponent_id)
        return False, "smoked"

    # ATK bonus from Bringus Boost
    atk_bonus = RARITY_STATS["common"]["atk_bonus"]  # base
    active_card = await _get_active_card(db, fight.id, player_id)
    if not active_card:
        raise ValueError("No active card for attacker")
    atk_bonus = RARITY_STATS[active_card[1]]["atk_bonus"]

    boost = fight.challenger_atk_boost if is_ch else fight.opponent_atk_boost
    if boost > 0:
        atk_bonus += 10
        boost_col = "challenger_atk_boost" if is_ch else "opponent_atk_boost"
        await db.execute(
            f"UPDATE fights SET {boost_col} = {boost_col} - 1 WHERE id = ?", (fight.id,)
        )

    roll = roll_d20()
    damage, tier = calc_damage(attack_key, atk_bonus, roll)

    attack_name = ATTACKS[attack_key]["name"]
    tier_text = {
        "miss": "missed",
        "glancing": "glancing blow",
        "direct": "direct hit",
        "critical": "critical hit",
        "nat20": "NAT 20! LEGENDARY HIT",
    }[tier]

    if damage == 0:
        narrative = f"<@{player_id}> used **{attack_name}** — rolled {roll}, missed!"
        await _log_action(
            db, fight.id, player_id, "attack", narrative, d20_roll=roll, damage=0,
            detail={**detail, "tier": tier},
        )
        await _advance_turn(db, fight.id, opponent_id)
        return False, narrative

    # Damage > 0 — apply to opponent's active card
    opp_card = await _get_active_card(db, fight.id, opponent_id)
    if not opp_card:
        raise ValueError("No active card for defender")
    new_hp, fainted = await _apply_damage(db, opp_card[0], damage)
    narrative = (
        f"<@{player_id}> used **{attack_name}** — rolled {roll} ({tier_text}), "
        f"dealt {damage} damage! ({new_hp}/{opp_card[3]} HP remaining)"
    )
    if tier == "nat20":
        narrative += " ✨ UNBELIEVABLE POWER!"

    if fainted:
        narrative += f"\n<@{opponent_id}>'s card has fainted!"

    await _log_action(
        db, fight.id, player_id, "attack", narrative, d20_roll=roll, damage=damage,
        detail={**detail, "tier": tier},
    )

    if fainted:
        all_fainted = await _check_all_fainted(db, fight.id, opponent_id)
        if all_fainted:
            await _finish_fight(db, fight.id, player_id)
            return True, narrative

        has_reserve = await _has_non_fainted_cards(db, fight.id, opponent_id)
        if has_reserve:
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                "UPDATE fights SET pending_swap_player_id = ?, last_activity_at = ? WHERE id = ?",
                (opponent_id, now, fight.id),
            )
        else:
            await _finish_fight(db, fight.id, player_id)
            return True, narrative
        return False, narrative

    await _advance_turn(db, fight.id, opponent_id)
    return False, narrative


async def _handle_item(
    db: aiosqlite.Connection,
    fight: Fight,
    player_id: str,
    item_type: str,
) -> str:
    """Use an item on your turn. Returns narrative."""
    async with db.execute(
        "SELECT quantity FROM player_items WHERE player_id = ? AND item_type = ?",
        (player_id, item_type),
    ) as cur:
        row = await cur.fetchone()
    if not row or row[0] < 1:
        raise ValueError("no_item")

    await db.execute(
        "UPDATE player_items SET quantity = quantity - 1 WHERE player_id = ? AND item_type = ?",
        (player_id, item_type),
    )

    is_ch = _is_challenger(fight, player_id)
    opponent_id = _other_player(fight, player_id)
    narrative = ""

    if item_type in ("heal_potion", "super_potion"):
        hp_restore = ITEM_EFFECTS[item_type]["hp_restore"]
        active_card = await _get_active_card(db, fight.id, player_id)
        if not active_card:
            raise ValueError("no_active_card")
        card_id, _rarity, hp_cur, hp_max = active_card
        new_hp = min(hp_max, hp_cur + hp_restore)
        await db.execute(
            "UPDATE fight_cards SET hp_current = ? WHERE id = ?",
            (new_hp, card_id),
        )
        item_name = "Heal Potion" if item_type == "heal_potion" else "Super Potion"
        narrative = (
            f"<@{player_id}> used **{item_name}**! "
            f"Restored {new_hp - hp_cur} HP ({new_hp}/{hp_max})."
        )
    elif item_type == "bringus_boost":
        col = "challenger_atk_boost" if is_ch else "opponent_atk_boost"
        await db.execute(f"UPDATE fights SET {col} = 3 WHERE id = ?", (fight.id,))
        narrative = f"<@{player_id}> activated **Bringus Boost**! +10 ATK for the next 3 turns."
    elif item_type == "smoke_screen":
        # Smoke screen makes the OPPONENT's next attack miss
        opp_smoked_col = "opponent_smoked" if is_ch else "challenger_smoked"
        await db.execute(f"UPDATE fights SET {opp_smoked_col} = 1 WHERE id = ?", (fight.id,))
        narrative = (
            f"<@{player_id}> deployed **Smoke Screen**! <@{opponent_id}>'s next attack will miss."
        )

    await _log_action(db, fight.id, player_id, "item", narrative, detail={"item_type": item_type})
    await _advance_turn(db, fight.id, opponent_id)
    return narrative


async def _handle_swap(
    db: aiosqlite.Connection,
    fight: Fight,
    player_id: str,
    slot: int,
    forced: bool = False,
) -> str:
    """Swap to a different card. forced=True means post-faint replacement (no turn cost)."""
    async with db.execute(
        "SELECT id, card_member_id, hp_current, is_fainted FROM fight_cards "
        "WHERE fight_id = ? AND player_id = ? AND slot = ?",
        (fight.id, player_id, slot),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise ValueError("invalid_slot")
    card_id, card_member_id, hp_cur, is_fainted = row
    if is_fainted:
        raise ValueError("card_fainted")

    await db.execute(
        "UPDATE fight_cards SET is_active = 0 WHERE fight_id = ? AND player_id = ?",
        (fight.id, player_id),
    )
    await db.execute("UPDATE fight_cards SET is_active = 1 WHERE id = ?", (card_id,))

    async with db.execute(
        "SELECT display_name FROM members WHERE discord_id = ?", (card_member_id,)
    ) as cur:
        name_row = await cur.fetchone()
    card_name = name_row[0] if name_row else card_member_id

    narrative = f"<@{player_id}> sent out **{card_name}** ({hp_cur} HP)!"
    await _log_action(db, fight.id, player_id, "swap", narrative, detail={"slot": slot})

    if forced:
        # Post-faint swap: clear pending_swap, give turn to the swapping player (defender)
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE fights SET pending_swap_player_id = NULL, "
            "current_turn_player_id = ?, last_activity_at = ? WHERE id = ?",
            (player_id, now, fight.id),
        )
    else:
        # Voluntary swap costs the turn
        await _advance_turn(db, fight.id, _other_player(fight, player_id))

    return narrative


async def _handle_run(
    db: aiosqlite.Connection,
    fight: Fight,
    player_id: str,
) -> tuple[bool, bool, int, str]:
    """
    Attempt to run. Returns (fight_ended, escaped, roll, narrative).
    Escape penalty (escape_paid) is handled by caller via pringle_service.
    """
    if fight.mode != "extended":
        raise ValueError("run_not_allowed")

    roll = roll_d20()
    opponent_id = _other_player(fight, player_id)

    if roll >= 16:
        narrative = (
            f"<@{player_id}> attempted to flee — rolled {roll}! "
            "Free escape! The battle ends with no Pringle cost."
        )
        await _log_action(
            db, fight.id, player_id, "run", narrative, d20_roll=roll, detail={"escaped": True}
        )
        await _finish_fight(db, fight.id, opponent_id)
        return True, True, roll, narrative
    elif roll >= 11:
        narrative = (
            f"<@{player_id}> attempted to flee — rolled {roll}! "
            "Escape successful, but forfeits 25 Pringles."
        )
        await _log_action(
            db, fight.id, player_id, "run", narrative, d20_roll=roll, detail={"escaped": True}
        )
        await _finish_fight(db, fight.id, opponent_id)
        return True, True, roll, narrative
    else:
        narrative = (
            f"<@{player_id}> attempted to flee — rolled {roll}! Failed to escape! Loses their turn."
        )
        await _log_action(
            db, fight.id, player_id, "run", narrative, d20_roll=roll, detail={"escaped": False}
        )
        await _advance_turn(db, fight.id, opponent_id)
        return False, False, roll, narrative


async def process_action(
    fight_id: int,
    player_id: str,
    action: str,
    detail: dict,
) -> tuple[bool, str, dict]:
    """
    Process a player action. Returns (success, error_msg, new_state_dict).
    Pringles for run escape are handled here via pringle_service.
    """
    from superpal.cards.pringle_service import award_fight_pringles

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(f"{_FIGHT_SELECT} WHERE id = ?", (fight_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return False, "fight_not_found", {}
        fight = _row_to_fight(row)

        if fight.status != "active":
            return False, "fight_not_active", {}

        # Forced swap takes priority over normal turn order
        if fight.pending_swap_player_id:
            if player_id != fight.pending_swap_player_id:
                return False, "waiting_for_swap", {}
            if action != "swap":
                return False, "must_swap", {}
            slot = detail.get("slot")
            if not slot:
                return False, "missing_slot", {}
            try:
                await _handle_swap(db, fight, player_id, int(slot), forced=True)
            except ValueError as e:
                return False, str(e), {}
            await db.commit()
            state = await get_fight_state(fight_id)
            return True, "", state

        if fight.current_turn_player_id != player_id:
            return False, "not_your_turn", {}

        fight_ended = False
        escape_penalty = False

        if action == "attack":
            attack_key = detail.get("attack_key", "")
            try:
                fight_ended, _narrative = await _handle_attack(
                    db, fight, player_id, attack_key, detail
                )
            except ValueError as e:
                return False, str(e), {}

        elif action == "item":
            item_type = detail.get("item_type", "")
            try:
                await _handle_item(db, fight, player_id, item_type)
            except ValueError as e:
                return False, str(e), {}

        elif action == "swap":
            if fight.mode != "extended":
                return False, "swap_not_allowed", {}
            slot = detail.get("slot")
            if not slot:
                return False, "missing_slot", {}
            try:
                await _handle_swap(db, fight, player_id, int(slot), forced=False)
            except ValueError as e:
                return False, str(e), {}

        elif action == "run":
            try:
                fight_ended, escaped, roll, _narrative = await _handle_run(db, fight, player_id)
                if fight_ended and escaped and roll < 16:
                    escape_penalty = True
            except ValueError as e:
                return False, str(e), {}

        else:
            return False, "unknown_action", {}

        await db.commit()

    if fight_ended:
        updated_fight = await get_fight(fight_id)
        if updated_fight and updated_fight.winner_id:
            winner_id = updated_fight.winner_id
            loser_id = _other_player(fight, winner_id)
            await award_fight_pringles(
                winner_id=winner_id,
                loser_id=loser_id,
                mode=fight.mode,
                escape_penalty=escape_penalty,
            )

    state = await get_fight_state(fight_id)
    return True, "", state


async def expire_pending_challenges() -> None:
    """Expire fights that have been pending past their expiry time."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE fights SET status = 'expired' WHERE status = 'pending' AND expires_at < ?",
            (now,),
        )
        await db.commit()


async def expire_inactive_fights() -> None:
    """Expire active/lobby fights with no activity for 10 minutes."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=INACTIVITY_EXPIRE_MINUTES)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE fights SET status = 'expired' "
            "WHERE status IN ('active', 'lobby') AND last_activity_at < ?",
            (cutoff,),
        )
        await db.commit()


async def get_fight_leaderboard(sort_by: str = "wins") -> list[dict]:
    """Return top 10 players ranked by fight stats.

    sort_by: 'wins' | 'win_rate' | 'fights_played' | 'pringle_balance' | 'escapes'
    All rows: {discord_id, display_name, total}.
    win_rate rows also include {total_fights} for display formatting.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if sort_by == "win_rate":
            async with db.execute("""
                SELECT discord_id, display_name,
                  CAST(wins AS REAL) / total_fights AS total,
                  total_fights
                FROM (
                  SELECT m.discord_id, m.display_name,
                    SUM(CASE WHEN f.winner_id = m.discord_id THEN 1 ELSE 0 END) AS wins,
                    COUNT(*) AS total_fights
                  FROM members m
                  JOIN fights f
                    ON (f.challenger_id = m.discord_id OR f.opponent_id = m.discord_id)
                  WHERE f.status = 'completed' AND m.is_excluded = 0
                  GROUP BY m.discord_id
                  HAVING total_fights >= 3
                )
                ORDER BY total DESC LIMIT 10
            """) as cur:
                rows = await cur.fetchall()
            return [
                {
                    "discord_id": r[0],
                    "display_name": r[1],
                    "total": r[2],
                    "total_fights": r[3],
                }
                for r in rows
            ]

        if sort_by == "fights_played":
            sql = """
                SELECT m.discord_id, m.display_name, COUNT(*) AS total
                FROM members m
                JOIN fights f
                  ON (f.challenger_id = m.discord_id OR f.opponent_id = m.discord_id)
                WHERE f.status = 'completed' AND m.is_excluded = 0
                GROUP BY m.discord_id ORDER BY total DESC LIMIT 10
            """
        elif sort_by == "pringle_balance":
            sql = """
                SELECT discord_id, display_name, pringle_balance AS total
                FROM members WHERE is_excluded = 0
                ORDER BY pringle_balance DESC LIMIT 10
            """
        elif sort_by == "escapes":
            sql = """
                SELECT fl.actor_id AS discord_id, m.display_name, COUNT(*) AS total
                FROM fight_log fl
                JOIN fights f ON f.id = fl.fight_id
                JOIN members m ON m.discord_id = fl.actor_id
                WHERE fl.action_type = 'run'
                  AND f.status = 'completed'
                  AND f.winner_id != fl.actor_id
                  AND m.is_excluded = 0
                GROUP BY fl.actor_id ORDER BY total DESC LIMIT 10
            """
        else:  # wins
            sql = """
                SELECT m.discord_id, m.display_name, COUNT(*) AS total
                FROM fights f JOIN members m ON m.discord_id = f.winner_id
                WHERE f.status = 'completed' AND m.is_excluded = 0
                GROUP BY f.winner_id ORDER BY total DESC LIMIT 10
            """

        async with db.execute(sql) as cur:
            rows = await cur.fetchall()
    return [{"discord_id": r[0], "display_name": r[1], "total": r[2]} for r in rows]
