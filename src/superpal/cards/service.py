import random
import uuid
import aiosqlite
from datetime import datetime, timedelta, timezone
from typing import Optional

from superpal.cards.db import DB_PATH
from superpal.cards.models import (
    UserCard, MagicLink, RARITY_ORDER, RARITY_WEIGHTS
)


def _get_week_start() -> str:
    """ISO date string for the Monday of the current UTC week."""
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()


def _roll_rarity() -> str:
    population = list(RARITY_WEIGHTS.keys())
    weights = list(RARITY_WEIGHTS.values())
    return random.choices(population, weights=weights, k=1)[0]


async def sync_members(members: list[dict]) -> None:
    """Upsert members from a list of dicts with discord_id, display_name, avatar_url."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany("""
            INSERT INTO members (discord_id, display_name, avatar_url, is_excluded, synced_at)
            VALUES (:discord_id, :display_name, :avatar_url, 0, :synced_at)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name = excluded.display_name,
                avatar_url   = excluded.avatar_url,
                synced_at    = excluded.synced_at
        """, [{"synced_at": now, **m} for m in members])
        await db.commit()


async def set_excluded(discord_id: str, *, excluded: bool) -> None:
    """Toggle exclusion status for a member."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE members SET is_excluded = ? WHERE discord_id = ?",
            (1 if excluded else 0, discord_id)
        )
        await db.commit()


async def draw_card(owner_id: str, max_draws: int) -> Optional[UserCard]:
    """Draw a card for owner_id. Returns UserCard or None if weekly limit reached."""
    week_start = _get_week_start()
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT draws_used FROM draw_log WHERE user_id = ? AND week_start = ?",
            (owner_id, week_start),
        ) as cur:
            row = await cur.fetchone()
        draws_used = row[0] if row else 0
        if draws_used >= max_draws:
            return None

        async with db.execute(
            "SELECT discord_id FROM members WHERE is_excluded = 0"
        ) as cur:
            eligible = [r[0] for r in await cur.fetchall()]

        if not eligible:
            return None

        card_member_id = random.choice(eligible)
        rarity = _roll_rarity()

        await db.execute("""
            INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + 1
        """, (owner_id, card_member_id, rarity, now))

        await db.execute("""
            INSERT INTO draw_log (user_id, week_start, draws_used)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, week_start)
            DO UPDATE SET draws_used = draws_used + 1
        """, (owner_id, week_start))

        await db.commit()

        async with db.execute(
            "SELECT id, owner_id, card_member_id, rarity, quantity, first_acquired_at "
            "FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        ) as cur:
            r = await cur.fetchone()

        return UserCard(
            id=r[0], owner_id=r[1], card_member_id=r[2],
            rarity=r[3], quantity=r[4], first_acquired_at=r[5],
        )


async def get_card_quantity(owner_id: str, card_member_id: str, rarity: str) -> int:
    """Return how many copies owner has of [card_member_id, rarity]."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def trade_in(owner_id: str, card_member_id: str, rarity: str) -> Optional[UserCard]:
    """Trade 3x [card_member_id, rarity] for a random card of the same rarity.
    Returns the new card, or None if insufficient duplicates or invalid rarity."""
    if rarity not in RARITY_ORDER:
        return None

    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] < 3:
            return None

        await db.execute(
            "UPDATE user_cards SET quantity = quantity - 3 "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        )
        await db.execute(
            "DELETE FROM user_cards WHERE owner_id = ? AND quantity <= 0",
            (owner_id,),
        )

        async with db.execute(
            "SELECT discord_id FROM members WHERE is_excluded = 0"
        ) as cur:
            eligible = [r[0] for r in await cur.fetchall()]

        if not eligible:
            await db.commit()
            return None

        new_member_id = random.choice(eligible)

        await db.execute("""
            INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + 1
        """, (owner_id, new_member_id, rarity, now))

        await db.commit()

        async with db.execute(
            "SELECT id, owner_id, card_member_id, rarity, quantity, first_acquired_at "
            "FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, new_member_id, rarity),
        ) as cur:
            r = await cur.fetchone()

    return UserCard(
        id=r[0], owner_id=r[1], card_member_id=r[2],
        rarity=r[3], quantity=r[4], first_acquired_at=r[5],
    )


async def upgrade(owner_id: str, card_member_id: str, rarity: str) -> Optional[UserCard]:
    """Trade 5x [card_member_id, rarity] for 1x same member at next rarity tier.
    Returns upgraded card, or None if insufficient copies, already Legendary, or invalid rarity."""
    if rarity == "legendary" or rarity not in RARITY_ORDER:
        return None

    next_rarity = RARITY_ORDER[RARITY_ORDER.index(rarity) + 1]
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] < 5:
            return None

        await db.execute(
            "UPDATE user_cards SET quantity = quantity - 5 "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        )
        await db.execute(
            "DELETE FROM user_cards WHERE owner_id = ? AND quantity <= 0",
            (owner_id,),
        )
        await db.execute("""
            INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + 1
        """, (owner_id, card_member_id, next_rarity, now))
        await db.commit()

        async with db.execute(
            "SELECT id, owner_id, card_member_id, rarity, quantity, first_acquired_at "
            "FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, next_rarity),
        ) as cur:
            r = await cur.fetchone()

    return UserCard(
        id=r[0], owner_id=r[1], card_member_id=r[2],
        rarity=r[3], quantity=r[4], first_acquired_at=r[5],
    )


async def generate_magic_link(user_id: str, link_type: str, base_url: str) -> str:
    """Insert a new unconsumed token and return the full URL."""
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO magic_links (token, user_id, link_type, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, link_type, now),
        )
        await db.commit()
    return f"{base_url}/link/{token}"


async def consume_magic_link(token: str) -> Optional[MagicLink]:
    """Consume a token on first use. Returns MagicLink with session_token set, or None if already used."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT token, user_id, link_type, created_at, consumed_at FROM magic_links WHERE token = ?",
            (token,),
        ) as cur:
            row = await cur.fetchone()

        if not row or row[4] is not None:
            return None

        session_token = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(hours=24)).isoformat()
        now_str = now.isoformat()

        await db.execute(
            "UPDATE magic_links SET consumed_at = ?, session_token = ?, session_expires_at = ? WHERE token = ?",
            (now_str, session_token, expires, token),
        )
        await db.commit()

        return MagicLink(
            token=row[0], user_id=row[1], link_type=row[2],
            created_at=row[3], consumed_at=now_str,
            session_token=session_token, session_expires_at=expires,
        )


async def get_session(session_token: str) -> Optional[MagicLink]:
    """Look up an active session by session_token. Returns None if expired or not found."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT token, user_id, link_type, created_at, consumed_at, session_token, session_expires_at "
            "FROM magic_links WHERE session_token = ? AND session_expires_at > ?",
            (session_token, now),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return MagicLink(
        token=row[0], user_id=row[1], link_type=row[2],
        created_at=row[3], consumed_at=row[4],
        session_token=row[5], session_expires_at=row[6],
    )


async def get_collection(owner_id: str) -> dict:
    """Return all cards for a user plus silhouettes for undiscovered members."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_id, display_name, avatar_url "
            "FROM members WHERE is_excluded = 0"
        ) as cur:
            all_members = {
                r[0]: {"discord_id": r[0], "display_name": r[1], "avatar_url": r[2]}
                for r in await cur.fetchall()
            }

        async with db.execute(
            "SELECT uc.card_member_id, m.display_name, m.avatar_url, uc.rarity, uc.quantity "
            "FROM user_cards uc JOIN members m ON uc.card_member_id = m.discord_id "
            "WHERE uc.owner_id = ? AND m.is_excluded = 0 ORDER BY uc.rarity, m.display_name",
            (owner_id,),
        ) as cur:
            owned_rows = await cur.fetchall()

    owned = [
        {
            "member_id": r[0],
            "display_name": r[1],
            "avatar_url": r[2],
            "rarity": r[3],
            "quantity": r[4],
        }
        for r in owned_rows
    ]

    owned_member_ids = {r["member_id"] for r in owned}
    undiscovered = [
        m for mid, m in all_members.items() if mid not in owned_member_ids
    ]

    counts = {"common": 0, "uncommon": 0, "rare": 0, "legendary": 0}
    for card in owned:
        counts[card["rarity"]] += card["quantity"]

    return {"owned": owned, "undiscovered": undiscovered, "counts": counts}


async def get_all_members_for_admin() -> list[dict]:
    """Return all members with exclusion status for admin dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_id, display_name, avatar_url, is_excluded FROM members ORDER BY display_name"
        ) as cur:
            rows = await cur.fetchall()
    return [
        {"discord_id": r[0], "display_name": r[1], "avatar_url": r[2], "is_excluded": bool(r[3])}
        for r in rows
    ]


async def get_pool_stats() -> dict:
    """Card pool statistics for admin dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM members WHERE is_excluded = 0") as cur:
            eligible = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM members WHERE is_excluded = 1") as cur:
            excluded = (await cur.fetchone())[0]
        async with db.execute("SELECT COALESCE(SUM(quantity), 0) FROM user_cards") as cur:
            total_cards = (await cur.fetchone())[0]
    return {"eligible": eligible, "excluded": excluded, "total_cards": total_cards}


async def add_member(discord_id: str, display_name: str) -> None:
    """Insert or update a member by ID (works for non-Discord test users too)."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO members (discord_id, display_name, avatar_url, is_excluded, synced_at)
            VALUES (?, ?, NULL, 0, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name = excluded.display_name,
                synced_at    = excluded.synced_at
            """,
            (discord_id, display_name, now),
        )
        await db.commit()


async def set_member_avatar(member_id: str, avatar_url: str) -> None:
    """Update the stored avatar URL for a member."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE members SET avatar_url = ? WHERE discord_id = ?",
            (avatar_url, member_id),
        )
        await db.commit()


async def award_card(owner_id: str, card_member_id: str, rarity: str, quantity: int) -> Optional[UserCard]:
    """Manually award cards to a user. Returns None if rarity is invalid."""
    if rarity not in RARITY_ORDER:
        return None
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + excluded.quantity
            """,
            (owner_id, card_member_id, rarity, quantity, now),
        )
        await db.commit()
        async with db.execute(
            "SELECT id, owner_id, card_member_id, rarity, quantity, first_acquired_at "
            "FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        ) as cur:
            r = await cur.fetchone()
    return UserCard(
        id=r[0], owner_id=r[1], card_member_id=r[2],
        rarity=r[3], quantity=r[4], first_acquired_at=r[5],
    )
