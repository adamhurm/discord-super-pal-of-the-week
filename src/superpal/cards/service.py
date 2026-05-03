import json
import random
import uuid
import aiosqlite
from datetime import datetime, timedelta, timezone
from typing import Optional

from superpal.cards.db import DB_PATH
from superpal.cards.models import (
    UserCard, MagicLink, PendingTrade, RARITY_ORDER, RARITY_WEIGHTS
)

TRADE_EXPIRY_MINUTES = 10


def _get_week_start() -> str:
    """ISO date string for the Sunday of the current UTC week."""
    today = datetime.now(timezone.utc).date()
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    return sunday.isoformat()


def _roll_rarity() -> str:
    population = list(RARITY_WEIGHTS.keys())
    weights = list(RARITY_WEIGHTS.values())
    return random.choices(population, weights=weights, k=1)[0]


async def sync_members(members: list[dict]) -> None:
    """Upsert Discord members. Synthetic (manually-created) members are never modified."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany("""
            INSERT INTO members (discord_id, display_name, avatar_url, is_excluded, is_synthetic, synced_at)
            VALUES (:discord_id, :display_name, :avatar_url, 0, 0, :synced_at)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name = excluded.display_name,
                avatar_url   = excluded.avatar_url,
                synced_at    = excluded.synced_at
            WHERE members.is_synthetic = 0
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


async def set_forced_rarity(discord_id: str, rarity: Optional[str]) -> None:
    """Lock a member to a specific rarity tier, or clear the lock when rarity is None."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE members SET forced_rarity = ? WHERE discord_id = ?",
            (rarity or None, discord_id),
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

        rarity = _roll_rarity()

        async with db.execute(
            "SELECT discord_id FROM members "
            "WHERE is_excluded = 0 AND (forced_rarity IS NULL OR forced_rarity = ?)",
            (rarity,),
        ) as cur:
            eligible = [r[0] for r in await cur.fetchall()]

        if not eligible:
            return None

        card_member_id = random.choice(eligible)

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
            "SELECT discord_id FROM members "
            "WHERE is_excluded = 0 AND (forced_rarity IS NULL OR forced_rarity = ?)",
            (rarity,),
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


async def use_magic_link(token: str) -> Optional[MagicLink]:
    """Return a valid MagicLink for token if it exists and hasn't expired (24h from creation).
    Reusable within the 24h window — each call issues a fresh session expiring at the same time as the link.
    Returns None if the token is unknown or the link has expired."""
    now = datetime.now(timezone.utc)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT token, user_id, link_type, created_at, consumed_at FROM magic_links WHERE token = ?",
            (token,),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return None

        created_at = datetime.fromisoformat(row[3])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        link_expires = created_at + timedelta(hours=24)
        if now >= link_expires:
            return None

        session_token = str(uuid.uuid4())
        session_expires = link_expires.isoformat()

        # Record first use time but don't gate on it.
        consumed_at = row[4] if row[4] is not None else now.isoformat()
        await db.execute(
            "UPDATE magic_links SET consumed_at = ?, session_token = ?, session_expires_at = ? WHERE token = ?",
            (consumed_at, session_token, session_expires, token),
        )
        await db.commit()

        return MagicLink(
            token=row[0], user_id=row[1], link_type=row[2],
            created_at=row[3], consumed_at=consumed_at,
            session_token=session_token, session_expires_at=session_expires,
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
            "SELECT uc.card_member_id, m.display_name, m.avatar_url, uc.rarity, uc.quantity, m.bio, m.stats "
            "FROM user_cards uc JOIN members m ON uc.card_member_id = m.discord_id "
            "WHERE uc.owner_id = ? AND m.is_excluded = 0 ORDER BY uc.rarity, m.display_name",
            (owner_id,),
        ) as cur:
            owned_rows = await cur.fetchall()

    def _parse_stats(raw: str | None) -> list[tuple[str, str]]:
        if not raw:
            return []
        try:
            return list(json.loads(raw).items())
        except (json.JSONDecodeError, AttributeError):
            return []

    owned = [
        {
            "member_id": r[0],
            "display_name": r[1],
            "avatar_url": r[2],
            "rarity": r[3],
            "quantity": r[4],
            "bio": r[5],
            "stats_pairs": _parse_stats(r[6]),
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


async def reset_draw_log() -> None:
    """Delete all draw_log entries for the current week, restoring everyone's draws."""
    week_start = _get_week_start()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM draw_log WHERE week_start = ?", (week_start,))
        await db.commit()


async def get_all_members_for_admin() -> list[dict]:
    """Return all members with exclusion and rarity-lock status for admin dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_id, display_name, avatar_url, is_excluded, forced_rarity, is_synthetic, bio, stats "
            "FROM members ORDER BY display_name"
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "discord_id": r[0], "display_name": r[1], "avatar_url": r[2],
            "is_excluded": bool(r[3]), "forced_rarity": r[4], "is_synthetic": bool(r[5]),
            "bio": r[6], "stats": r[7],
        }
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
    """Insert a synthetic (non-Discord) member, or update its display name if it already exists."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO members (discord_id, display_name, avatar_url, is_excluded, is_synthetic, synced_at)
            VALUES (?, ?, NULL, 0, 1, ?)
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


async def set_member_bio_stats(member_id: str, bio: str, stats: str) -> None:
    """Update bio (lore text) and stats (JSON blob) for a member."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE members SET bio = ?, stats = ? WHERE discord_id = ?",
            (bio or None, stats or None, member_id),
        )
        await db.commit()


async def create_trade_offer(
    proposer_id: str,
    recipient_id: str,
    offer_member_id: str,
    offer_rarity: str,
    request_member_id: str,
    request_rarity: str,
) -> tuple[Optional[PendingTrade], Optional[str]]:
    """Create a pending trade offer. Returns (PendingTrade, None) on success or (None, reason) on failure."""
    if offer_rarity not in RARITY_ORDER or request_rarity not in RARITY_ORDER:
        return None, "invalid_rarity"
    if proposer_id == recipient_id:
        return None, "self_trade"

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(minutes=TRADE_EXPIRY_MINUTES)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")

        async with db.execute(
            "SELECT quantity FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (proposer_id, offer_member_id, offer_rarity),
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] < 1:
            return None, "no_offer_card"

        async with db.execute(
            "SELECT id FROM pending_trades WHERE proposer_id = ? AND status = 'pending' AND expires_at > ?",
            (proposer_id, now_iso),
        ) as cur:
            existing = await cur.fetchone()
        if existing:
            return None, "pending_exists"

        await db.execute(
            "INSERT INTO pending_trades "
            "(proposer_id, recipient_id, offer_member_id, offer_rarity, request_member_id, request_rarity, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (proposer_id, recipient_id, offer_member_id, offer_rarity, request_member_id, request_rarity, now_iso, expires_iso),
        )
        await db.commit()

        async with db.execute(
            "SELECT id, proposer_id, recipient_id, offer_member_id, offer_rarity, "
            "request_member_id, request_rarity, status, created_at, expires_at "
            "FROM pending_trades WHERE proposer_id = ? AND created_at = ?",
            (proposer_id, now_iso),
        ) as cur:
            r = await cur.fetchone()

    return PendingTrade(
        id=r[0], proposer_id=r[1], recipient_id=r[2],
        offer_member_id=r[3], offer_rarity=r[4],
        request_member_id=r[5], request_rarity=r[6],
        status=r[7], created_at=r[8], expires_at=r[9],
    ), None


async def execute_trade(trade_id: int) -> tuple[bool, Optional[str]]:
    """Atomically swap cards between proposer and recipient. Returns (True, None) on success."""
    now_iso = datetime.now(timezone.utc).isoformat()
    first_acquired = now_iso

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")

        async with db.execute(
            "SELECT proposer_id, recipient_id, offer_member_id, offer_rarity, "
            "request_member_id, request_rarity, status, expires_at "
            "FROM pending_trades WHERE id = ?",
            (trade_id,),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return False, "not_found"

        proposer_id, recipient_id, offer_member_id, offer_rarity, \
            request_member_id, request_rarity, status, expires_at = row

        if status != "pending":
            return False, "already_resolved"

        if expires_at <= now_iso:
            await db.execute(
                "UPDATE pending_trades SET status = 'expired' WHERE id = ?", (trade_id,)
            )
            await db.commit()
            return False, "expired"

        async with db.execute(
            "SELECT quantity FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (proposer_id, offer_member_id, offer_rarity),
        ) as cur:
            r = await cur.fetchone()
        if not r or r[0] < 1:
            return False, "proposer_missing_card"

        async with db.execute(
            "SELECT quantity FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (recipient_id, request_member_id, request_rarity),
        ) as cur:
            r = await cur.fetchone()
        if not r or r[0] < 1:
            return False, "recipient_missing_card"

        # Deduct offered card from proposer
        await db.execute(
            "UPDATE user_cards SET quantity = quantity - 1 "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (proposer_id, offer_member_id, offer_rarity),
        )
        await db.execute("DELETE FROM user_cards WHERE owner_id = ? AND quantity <= 0", (proposer_id,))

        # Award offered card to recipient
        await db.execute("""
            INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + 1
        """, (recipient_id, offer_member_id, offer_rarity, first_acquired))

        # Deduct requested card from recipient
        await db.execute(
            "UPDATE user_cards SET quantity = quantity - 1 "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (recipient_id, request_member_id, request_rarity),
        )
        await db.execute("DELETE FROM user_cards WHERE owner_id = ? AND quantity <= 0", (recipient_id,))

        # Award requested card to proposer
        await db.execute("""
            INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + 1
        """, (proposer_id, request_member_id, request_rarity, first_acquired))

        await db.execute(
            "UPDATE pending_trades SET status = 'accepted' WHERE id = ?", (trade_id,)
        )
        await db.commit()

    return True, None


async def decline_trade(trade_id: int) -> bool:
    """Mark a pending trade as declined. Returns True if a pending trade was found and updated."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE pending_trades SET status = 'declined' WHERE id = ? AND status = 'pending'",
            (trade_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


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
