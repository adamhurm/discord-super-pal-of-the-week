import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import cast

import aiosqlite

from superpal.cards.db import DB_PATH
from superpal.cards.models import (
    RARITY_ORDER,
    RARITY_WEIGHTS,
    CardRef,
    MagicLink,
    MemberCardContext,
    TradeListingFull,
    TradeOfferFull,
    UserCard,
)
from superpal.schedule import next_sunday_noon_utc

TRADE_OFFER_EXPIRY_HOURS = 24


def _parse_stats(raw: str | None) -> list[tuple[str, str]]:
    if not raw:
        return []
    try:
        return list(json.loads(raw).items())
    except (json.JSONDecodeError, AttributeError):
        return []


def _get_week_start() -> str:
    """ISO datetime string for Sunday noon UTC marking the start of the current draw week."""
    return (next_sunday_noon_utc() - timedelta(weeks=1)).isoformat()


def _roll_rarity() -> str:
    population = list(RARITY_WEIGHTS.keys())
    weights = list(RARITY_WEIGHTS.values())
    return random.choices(population, weights=weights, k=1)[0]


async def sync_members(members: list[dict]) -> None:
    """Upsert Discord members. Synthetic (manually-created) members are never modified."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            """
            INSERT INTO members
                (discord_id, display_name, avatar_url, is_excluded, is_synthetic, synced_at)
            VALUES (:discord_id, :display_name, :avatar_url, 0, 0, :synced_at)
            ON CONFLICT(discord_id) DO UPDATE SET
                display_name = excluded.display_name,
                avatar_url   = excluded.avatar_url,
                synced_at    = excluded.synced_at
            WHERE members.is_synthetic = 0
        """,
            [{"synced_at": now, **m} for m in members],
        )
        await db.commit()


async def set_excluded(discord_id: str, *, excluded: bool) -> None:
    """Toggle exclusion status for a member."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE members SET is_excluded = ? WHERE discord_id = ?",
            (1 if excluded else 0, discord_id),
        )
        await db.commit()


async def set_forced_rarity(discord_id: str, rarity: str | None) -> None:
    """Lock a member to a specific rarity tier, or clear the lock when rarity is None."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE members SET forced_rarity = ? WHERE discord_id = ?",
            (rarity or None, discord_id),
        )
        await db.commit()


async def draw_card(owner_id: str, max_draws: int, drawn_by_name: str = "") -> UserCard | None:
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

        await db.execute(
            """
            INSERT INTO user_cards
                (owner_id, card_member_id, rarity, quantity, first_acquired_at, drawn_by_name)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + 1
        """,
            (owner_id, card_member_id, rarity, now, drawn_by_name),
        )

        await db.execute(
            """
            INSERT INTO draw_log (user_id, week_start, draws_used)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, week_start)
            DO UPDATE SET draws_used = draws_used + 1
        """,
            (owner_id, week_start),
        )

        await db.commit()

        async with db.execute(
            "SELECT id, owner_id, card_member_id, rarity, quantity, "
            "first_acquired_at, drawn_by_name "
            "FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        ) as cur:
            r = await cur.fetchone()

        assert r is not None
        return UserCard(
            id=r[0],
            owner_id=r[1],
            card_member_id=r[2],
            rarity=r[3],
            quantity=r[4],
            first_acquired_at=r[5],
            drawn_by_name=r[6],
        )


async def get_card_quantity(owner_id: str, card_member_id: str, rarity: str) -> int:
    """Return how many copies owner has of [card_member_id, rarity]."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM user_cards "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0


async def gift_card(
    gifter_id: str,
    recipient_id: str,
    card_member_id: str,
    rarity: str,
    drawn_by_name: str = "",
) -> tuple[UserCard | None, str | None]:
    """Transfer one copy of [card_member_id, rarity] from gifter to recipient.
    Returns (UserCard, None) on success or (None, reason) on failure.
    Reasons: 'self_gift', 'invalid_rarity', 'no_card'."""
    if gifter_id == recipient_id:
        return None, "self_gift"
    if rarity not in RARITY_ORDER:
        return None, "invalid_rarity"

    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")

        async with db.execute(
            "SELECT quantity FROM user_cards "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (gifter_id, card_member_id, rarity),
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] < 1:
            return None, "no_card"

        await db.execute(
            "UPDATE user_cards SET quantity = quantity - 1 "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (gifter_id, card_member_id, rarity),
        )
        await db.execute(
            "DELETE FROM user_cards WHERE owner_id = ? AND quantity <= 0",
            (gifter_id,),
        )

        await db.execute(
            """
            INSERT INTO user_cards
                (owner_id, card_member_id, rarity, quantity, first_acquired_at, drawn_by_name)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + 1
        """,
            (recipient_id, card_member_id, rarity, now, drawn_by_name),
        )

        await db.commit()

        async with db.execute(
            "SELECT id, owner_id, card_member_id, rarity, quantity, "
            "first_acquired_at, drawn_by_name "
            "FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (recipient_id, card_member_id, rarity),
        ) as cur:
            r = await cur.fetchone()

    assert r is not None
    return UserCard(
        id=r[0],
        owner_id=r[1],
        card_member_id=r[2],
        rarity=r[3],
        quantity=r[4],
        first_acquired_at=r[5],
        drawn_by_name=r[6],
    ), None


async def trade_in(
    owner_id: str, card_member_id: str, rarity: str, drawn_by_name: str = ""
) -> UserCard | None:
    """Trade 3x [card_member_id, rarity] for a random card of the same rarity.
    Returns the new card, or None if insufficient duplicates or invalid rarity."""
    if rarity not in RARITY_ORDER:
        return None

    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM user_cards "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
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

        await db.execute(
            """
            INSERT INTO user_cards
                (owner_id, card_member_id, rarity, quantity, first_acquired_at, drawn_by_name)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + 1
        """,
            (owner_id, new_member_id, rarity, now, drawn_by_name),
        )

        await db.commit()

        async with db.execute(
            "SELECT id, owner_id, card_member_id, rarity, quantity, "
            "first_acquired_at, drawn_by_name "
            "FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, new_member_id, rarity),
        ) as cur:
            r = await cur.fetchone()

    assert r is not None
    return UserCard(
        id=r[0],
        owner_id=r[1],
        card_member_id=r[2],
        rarity=r[3],
        quantity=r[4],
        first_acquired_at=r[5],
        drawn_by_name=r[6],
    )


async def upgrade(
    owner_id: str, card_member_id: str, rarity: str, drawn_by_name: str = ""
) -> UserCard | None:
    """Trade 5x [card_member_id, rarity] for 1x same member at next rarity tier.
    Returns upgraded card, or None if insufficient copies, already Legendary, or invalid rarity."""
    if rarity == "legendary" or rarity not in RARITY_ORDER:
        return None

    next_rarity = RARITY_ORDER[RARITY_ORDER.index(rarity) + 1]
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM user_cards "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
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
        await db.execute(
            """
            INSERT INTO user_cards
                (owner_id, card_member_id, rarity, quantity, first_acquired_at, drawn_by_name)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + 1
        """,
            (owner_id, card_member_id, next_rarity, now, drawn_by_name),
        )
        await db.commit()

        async with db.execute(
            "SELECT id, owner_id, card_member_id, rarity, quantity, "
            "first_acquired_at, drawn_by_name "
            "FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, next_rarity),
        ) as cur:
            r = await cur.fetchone()

    assert r is not None
    return UserCard(
        id=r[0],
        owner_id=r[1],
        card_member_id=r[2],
        rarity=r[3],
        quantity=r[4],
        first_acquired_at=r[5],
        drawn_by_name=r[6],
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


async def use_magic_link(token: str) -> MagicLink | None:
    """Return a valid MagicLink for token if it exists and hasn't expired (24h from creation).
    Each call issues a fresh session expiring at the same time as the link.
    Returns None if the token is unknown or the link has expired."""
    now = datetime.now(timezone.utc)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT token, user_id, link_type, created_at, consumed_at "
            "FROM magic_links WHERE token = ?",
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
            "UPDATE magic_links "
            "SET consumed_at = ?, session_token = ?, session_expires_at = ? WHERE token = ?",
            (consumed_at, session_token, session_expires, token),
        )
        await db.commit()

        return MagicLink(
            token=row[0],
            user_id=row[1],
            link_type=row[2],
            created_at=row[3],
            consumed_at=consumed_at,
            session_token=session_token,
            session_expires_at=session_expires,
        )


async def get_session(session_token: str) -> MagicLink | None:
    """Look up an active session by session_token. Returns None if expired or not found."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT token, user_id, link_type, created_at, consumed_at, "
            "session_token, session_expires_at "
            "FROM magic_links WHERE session_token = ? AND session_expires_at > ?",
            (session_token, now),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return MagicLink(
        token=row[0],
        user_id=row[1],
        link_type=row[2],
        created_at=row[3],
        consumed_at=row[4],
        session_token=row[5],
        session_expires_at=row[6],
    )


async def get_collection(owner_id: str) -> dict:
    """Return all cards for a user plus silhouettes for undiscovered members."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_id, display_name, avatar_url FROM members WHERE is_excluded = 0"
        ) as cur:
            all_members = {
                r[0]: {"discord_id": r[0], "display_name": r[1], "avatar_url": r[2]}
                for r in await cur.fetchall()
            }

        async with db.execute(
            "SELECT uc.card_member_id, m.display_name, m.avatar_url, "
            "uc.rarity, uc.quantity, m.bio, m.stats "
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
            "bio": r[5],
            "stats_pairs": _parse_stats(r[6]),
        }
        for r in owned_rows
    ]

    owned_member_ids = {r["member_id"] for r in owned}
    undiscovered = [m for mid, m in all_members.items() if mid not in owned_member_ids]

    counts: dict[str, int] = {"common": 0, "uncommon": 0, "rare": 0, "legendary": 0}
    for card in owned:
        counts[str(card["rarity"])] += cast(int, card["quantity"])

    return {"owned": owned, "undiscovered": undiscovered, "counts": counts}


async def reset_draw_log() -> None:
    """Delete all draw_log entries for the current week, restoring everyone's draws."""
    week_start = _get_week_start()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM draw_log WHERE week_start = ?", (week_start,))
        await db.commit()


async def add_draws(user_id: str, quantity: int) -> None:
    """Restore up to `quantity` draws for a user in the current week."""
    week_start = _get_week_start()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE draw_log SET draws_used = MAX(0, draws_used - ?) "
            "WHERE user_id = ? AND week_start = ?",
            (quantity, user_id, week_start),
        )
        await db.commit()


async def get_draw_audit(user_id: str) -> dict:
    """Return draw count and newly acquired cards this week for a user."""
    week_start = _get_week_start()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT draws_used FROM draw_log WHERE user_id = ? AND week_start = ?",
            (user_id, week_start),
        ) as cur:
            row = await cur.fetchone()
        draws_used = row[0] if row else 0

        async with db.execute(
            "SELECT display_name FROM members WHERE discord_id = ?",
            (user_id,),
        ) as cur:
            mrow = await cur.fetchone()
        display_name = mrow[0] if mrow else user_id

        async with db.execute(
            "SELECT COALESCE(SUM(draws_used), 0) FROM draw_log WHERE user_id = ?",
            (user_id,),
        ) as cur:
            trow = await cur.fetchone()
        total_draws = trow[0] if trow else 0

        async with db.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM user_cards WHERE owner_id = ?",
            (user_id,),
        ) as cur:
            crow = await cur.fetchone()
        total_cards = crow[0] if crow else 0

        async with db.execute(
            """SELECT m.display_name, uc.rarity, uc.first_acquired_at
               FROM user_cards uc
               JOIN members m ON m.discord_id = uc.card_member_id
               WHERE uc.owner_id = ? AND uc.first_acquired_at >= ?
               ORDER BY uc.first_acquired_at""",
            (user_id, week_start),
        ) as cur:
            new_cards = [
                {"card_name": r[0], "rarity": r[1], "acquired_at": r[2]}
                for r in await cur.fetchall()
            ]

    return {
        "display_name": display_name,
        "draws_used": draws_used,
        "total_draws": total_draws,
        "total_cards": total_cards,
        "week_start": week_start,
        "new_cards": new_cards,
    }


async def get_all_members_for_admin() -> list[dict]:
    """Return all members with exclusion and rarity-lock status for admin dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_id, display_name, avatar_url, is_excluded, forced_rarity, "
            "is_synthetic, bio, stats FROM members ORDER BY display_name"
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "discord_id": r[0],
            "display_name": r[1],
            "avatar_url": r[2],
            "is_excluded": bool(r[3]),
            "forced_rarity": r[4],
            "is_synthetic": bool(r[5]),
            "bio": r[6],
            "stats": r[7],
        }
        for r in rows
    ]


async def get_pool_stats() -> dict:
    """Card pool statistics for admin dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM members WHERE is_excluded = 0") as cur:
            row = await cur.fetchone()
            assert row is not None
            eligible = row[0]
        async with db.execute("SELECT COUNT(*) FROM members WHERE is_excluded = 1") as cur:
            row = await cur.fetchone()
            assert row is not None
            excluded = row[0]
        async with db.execute("SELECT COALESCE(SUM(quantity), 0) FROM user_cards") as cur:
            row = await cur.fetchone()
            assert row is not None
            total_cards = row[0]
    return {"eligible": eligible, "excluded": excluded, "total_cards": total_cards}


async def add_member(discord_id: str, display_name: str) -> None:
    """Insert a synthetic (non-Discord) member, or update its display name if it already exists."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO members
                (discord_id, display_name, avatar_url, is_excluded, is_synthetic, synced_at)
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


async def _load_listing_full(db: aiosqlite.Connection, listing_id: int) -> TradeListingFull | None:
    """Load a TradeListingFull from an open aiosqlite connection."""
    async with db.execute(
        "SELECT tl.id, tl.owner_id, m.display_name, tl.status, tl.ask_note, tl.created_at, "
        "COUNT(DISTINCT to_.id) "
        "FROM trade_listings tl "
        "JOIN members m ON tl.owner_id = m.discord_id "
        "LEFT JOIN trade_offers to_ ON to_.listing_id = tl.id AND to_.status = 'pending' "
        "WHERE tl.id = ? GROUP BY tl.id",
        (listing_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    listing_id_, owner_id, owner_name, status, ask_note, created_at, offer_count = row
    async with db.execute(
        "SELECT card_member_id, rarity FROM trade_listing_items WHERE listing_id = ?",
        (listing_id,),
    ) as cur:
        items = [CardRef(member_id=r[0], rarity=r[1]) for r in await cur.fetchall()]
    return TradeListingFull(
        id=listing_id_,
        owner_id=owner_id,
        owner_display_name=owner_name,
        status=status,
        ask_note=ask_note,
        created_at=created_at,
        items=items,
        offer_count=offer_count,
    )


async def create_listing(
    owner_id: str,
    items: list[CardRef],
    ask_note: str | None,
) -> TradeListingFull | str:
    """Create a trade listing. Returns TradeListingFull or error key."""
    if not items:
        return "empty_items"
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        for item in items:
            async with db.execute(
                "SELECT quantity FROM user_cards "
                "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
                (owner_id, item.member_id, item.rarity),
            ) as cur:
                row = await cur.fetchone()
            if not row or row[0] < 1:
                return "no_card"
        await db.execute(
            "INSERT INTO trade_listings (owner_id, status, ask_note, created_at) "
            "VALUES (?, 'active', ?, ?)",
            (owner_id, ask_note or None, now),
        )
        async with db.execute("SELECT last_insert_rowid()") as cur:
            listing_id = (await cur.fetchone())[0]
        for item in items:
            await db.execute(
                "INSERT INTO trade_listing_items (listing_id, card_member_id, rarity) "
                "VALUES (?, ?, ?)",
                (listing_id, item.member_id, item.rarity),
            )
        await db.commit()
        listing = await _load_listing_full(db, listing_id)
    return listing or "no_card"


async def cancel_listing(listing_id: int, owner_id: str) -> bool:
    """Cancel an active listing. Returns True if found and cancelled."""
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute(
            "UPDATE trade_listings SET status = 'cancelled' "
            "WHERE id = ? AND owner_id = ? AND status = 'active'",
            (listing_id, owner_id),
        )
        await db.commit()
    return result.rowcount > 0


async def get_active_listings(
    exclude_owner_id: str | None = None,
) -> list[TradeListingFull]:
    """Return all active listings, newest first. Optionally exclude one owner."""
    async with aiosqlite.connect(DB_PATH) as db:
        if exclude_owner_id:
            async with db.execute(
                "SELECT id FROM trade_listings WHERE status = 'active' AND owner_id != ? "
                "ORDER BY created_at DESC",
                (exclude_owner_id,),
            ) as cur:
                ids = [r[0] for r in await cur.fetchall()]
        else:
            async with db.execute(
                "SELECT id FROM trade_listings WHERE status = 'active' ORDER BY created_at DESC"
            ) as cur:
                ids = [r[0] for r in await cur.fetchall()]
        results = []
        for lid in ids:
            listing = await _load_listing_full(db, lid)
            if listing:
                results.append(listing)
    return results


async def get_player_listings(player_id: str) -> list[TradeListingFull]:
    """Return all active listings for a specific player."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM trade_listings WHERE status = 'active' AND owner_id = ? "
            "ORDER BY created_at DESC",
            (player_id,),
        ) as cur:
            ids = [r[0] for r in await cur.fetchall()]
        results = []
        for lid in ids:
            listing = await _load_listing_full(db, lid)
            if listing:
                results.append(listing)
    return results


async def _load_offer_full(db: aiosqlite.Connection, offer_id: int) -> TradeOfferFull | None:
    """Load a TradeOfferFull from an open aiosqlite connection."""
    async with db.execute(
        "SELECT to_.id, to_.listing_id, to_.proposer_id, pm.display_name, "
        "to_.status, to_.created_at, to_.expires_at "
        "FROM trade_offers to_ "
        "JOIN members pm ON to_.proposer_id = pm.discord_id "
        "WHERE to_.id = ?",
        (offer_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    offer_id_, listing_id, proposer_id, proposer_name, status, created_at, expires_at = row
    async with db.execute(
        "SELECT card_member_id, rarity FROM trade_offer_items WHERE offer_id = ?",
        (offer_id,),
    ) as cur:
        items = [CardRef(member_id=r[0], rarity=r[1]) for r in await cur.fetchall()]
    listing = await _load_listing_full(db, listing_id)
    if listing is None:
        return None
    return TradeOfferFull(
        id=offer_id_,
        listing_id=listing_id,
        proposer_id=proposer_id,
        proposer_display_name=proposer_name,
        status=status,
        created_at=created_at,
        expires_at=expires_at,
        items=items,
        listing=listing,
    )


async def create_offer(
    listing_id: int,
    proposer_id: str,
    items: list[CardRef],
) -> TradeOfferFull | str:
    """Create a trade offer against a listing. Returns TradeOfferFull or error key."""
    if not items:
        return "empty_items"
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(hours=TRADE_OFFER_EXPIRY_HOURS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT owner_id FROM trade_listings WHERE id = ? AND status = 'active'",
            (listing_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.rollback()
            return "not_found"
        if row[0] == proposer_id:
            await db.rollback()
            return "self_offer"
        async with db.execute(
            "SELECT id FROM trade_offers "
            "WHERE listing_id = ? AND proposer_id = ? AND status = 'pending'",
            (listing_id, proposer_id),
        ) as cur:
            if await cur.fetchone():
                await db.rollback()
                return "duplicate_offer"
        for item in items:
            async with db.execute(
                "SELECT quantity FROM user_cards "
                "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
                (proposer_id, item.member_id, item.rarity),
            ) as cur:
                card_row = await cur.fetchone()
            if not card_row or card_row[0] < 1:
                await db.rollback()
                return "no_card"
        await db.execute(
            "INSERT INTO trade_offers (listing_id, proposer_id, status, created_at, expires_at) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (listing_id, proposer_id, now_iso, expires_iso),
        )
        async with db.execute("SELECT last_insert_rowid()") as cur:
            offer_id = (await cur.fetchone())[0]
        for item in items:
            await db.execute(
                "INSERT INTO trade_offer_items (offer_id, card_member_id, rarity) "
                "VALUES (?, ?, ?)",
                (offer_id, item.member_id, item.rarity),
            )
        await db.commit()
        offer = await _load_offer_full(db, offer_id)
    return offer or "not_found"


async def accept_offer(offer_id: int, recipient_id: str) -> tuple[bool, str | None]:
    """Accept an offer: atomically swap cards, mark listing completed, decline siblings."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT to_.listing_id, to_.proposer_id, tl.owner_id "
            "FROM trade_offers to_ "
            "JOIN trade_listings tl ON to_.listing_id = tl.id "
            "WHERE to_.id = ? AND to_.status = 'pending'",
            (offer_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.rollback()
            return False, "not_found"
        listing_id, proposer_id, listing_owner_id = row
        if listing_owner_id != recipient_id:
            await db.rollback()
            return False, "not_owner"
        now_iso = datetime.now(timezone.utc).isoformat()
        async with db.execute(
            "SELECT card_member_id, rarity FROM trade_listing_items WHERE listing_id = ?",
            (listing_id,),
        ) as cur:
            listing_items = await cur.fetchall()
        async with db.execute(
            "SELECT card_member_id, rarity FROM trade_offer_items WHERE offer_id = ?",
            (offer_id,),
        ) as cur:
            offer_items = await cur.fetchall()
        for card_member_id, rarity in listing_items:
            async with db.execute(
                "SELECT quantity FROM user_cards "
                "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
                (recipient_id, card_member_id, rarity),
            ) as cur:
                if not (row2 := await cur.fetchone()) or row2[0] < 1:
                    await db.rollback()
                    return False, "listing_no_card"
        for card_member_id, rarity in offer_items:
            async with db.execute(
                "SELECT quantity FROM user_cards "
                "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
                (proposer_id, card_member_id, rarity),
            ) as cur:
                if not (row2 := await cur.fetchone()) or row2[0] < 1:
                    await db.rollback()
                    return False, "offer_no_card"
        # listing items: recipient → proposer
        for card_member_id, rarity in listing_items:
            await db.execute(
                "UPDATE user_cards SET quantity = quantity - 1 "
                "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
                (recipient_id, card_member_id, rarity),
            )
            await db.execute(
                "INSERT INTO user_cards "
                "(owner_id, card_member_id, rarity, quantity, first_acquired_at) "
                "VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(owner_id, card_member_id, rarity) "
                "DO UPDATE SET quantity = quantity + 1",
                (proposer_id, card_member_id, rarity, now_iso),
            )
        # offer items: proposer → recipient
        for card_member_id, rarity in offer_items:
            await db.execute(
                "UPDATE user_cards SET quantity = quantity - 1 "
                "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
                (proposer_id, card_member_id, rarity),
            )
            await db.execute(
                "INSERT INTO user_cards "
                "(owner_id, card_member_id, rarity, quantity, first_acquired_at) "
                "VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(owner_id, card_member_id, rarity) "
                "DO UPDATE SET quantity = quantity + 1",
                (recipient_id, card_member_id, rarity, now_iso),
            )
        await db.execute(
            "UPDATE trade_offers SET status = 'accepted' WHERE id = ?", (offer_id,)
        )
        await db.execute(
            "UPDATE trade_listings SET status = 'completed' WHERE id = ?", (listing_id,)
        )
        await db.execute(
            "UPDATE trade_offers SET status = 'declined' "
            "WHERE listing_id = ? AND id != ? AND status = 'pending'",
            (listing_id, offer_id),
        )
        await db.commit()
    return True, None


async def decline_offer(offer_id: int, recipient_id: str) -> bool:
    """Decline an offer (called by listing owner)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT tl.owner_id FROM trade_offers to_ "
            "JOIN trade_listings tl ON to_.listing_id = tl.id "
            "WHERE to_.id = ? AND to_.status = 'pending'",
            (offer_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] != recipient_id:
            return False
        result = await db.execute(
            "UPDATE trade_offers SET status = 'declined' WHERE id = ?", (offer_id,)
        )
        await db.commit()
    return result.rowcount > 0


async def cancel_offer(offer_id: int, proposer_id: str) -> bool:
    """Cancel an offer (called by the proposer)."""
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute(
            "UPDATE trade_offers SET status = 'cancelled' "
            "WHERE id = ? AND proposer_id = ? AND status = 'pending'",
            (offer_id, proposer_id),
        )
        await db.commit()
    return result.rowcount > 0


async def expire_offer(offer_id: int) -> None:
    """Mark an offer as expired (called on Discord view timeout)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE trade_offers SET status = 'expired' WHERE id = ? AND status = 'pending'",
            (offer_id,),
        )
        await db.commit()


async def get_offers_for_listing(listing_id: int) -> list[TradeOfferFull]:
    """Return all pending offers against a listing."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM trade_offers WHERE listing_id = ? AND status = 'pending'",
            (listing_id,),
        ) as cur:
            ids = [r[0] for r in await cur.fetchall()]
        return [o for oid in ids if (o := await _load_offer_full(db, oid))]


async def get_my_offers(user_id: str) -> list[TradeOfferFull]:
    """Return all pending offers sent by a user."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM trade_offers WHERE proposer_id = ? AND status = 'pending' "
            "ORDER BY created_at DESC",
            (user_id,),
        ) as cur:
            ids = [r[0] for r in await cur.fetchall()]
        return [o for oid in ids if (o := await _load_offer_full(db, oid))]


async def get_offer_by_id(offer_id: int) -> TradeOfferFull | None:
    """Load any offer by ID regardless of status."""
    async with aiosqlite.connect(DB_PATH) as db:
        return await _load_offer_full(db, offer_id)


async def set_offer_discord_message_id(offer_id: int, message_id: str) -> None:
    """Store the Discord DM message ID on an offer so the web UI can edit it."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE trade_offers SET discord_message_id = ? WHERE id = ?",
            (message_id, offer_id),
        )
        await db.commit()


async def get_leaderboard(sort_by: str = "total") -> list[dict]:
    """Return top 10 players ranked by sort_by ('total', 'legendary', 'unique').
    Returns list of dicts with keys: owner_id, display_name, total."""
    if sort_by == "legendary":
        sql = """
            SELECT uc.owner_id, m.display_name,
                COALESCE(SUM(
                    CASE WHEN uc.rarity = 'legendary' THEN uc.quantity ELSE 0 END
                ), 0) AS total
            FROM user_cards uc JOIN members m ON uc.owner_id = m.discord_id
            WHERE m.is_excluded = 0
            GROUP BY uc.owner_id, m.display_name
            ORDER BY total DESC LIMIT 10
        """
    elif sort_by == "unique":
        sql = """
            SELECT uc.owner_id, m.display_name,
                COUNT(DISTINCT uc.card_member_id) AS total
            FROM user_cards uc JOIN members m ON uc.owner_id = m.discord_id
            WHERE m.is_excluded = 0 AND uc.quantity > 0
            GROUP BY uc.owner_id, m.display_name
            ORDER BY total DESC LIMIT 10
        """
    else:
        sql = """
            SELECT uc.owner_id, m.display_name, SUM(uc.quantity) AS total
            FROM user_cards uc JOIN members m ON uc.owner_id = m.discord_id
            WHERE m.is_excluded = 0
            GROUP BY uc.owner_id, m.display_name
            ORDER BY total DESC LIMIT 10
        """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(sql) as cur:
            rows = await cur.fetchall()
    return [{"owner_id": r[0], "display_name": r[1], "total": r[2]} for r in rows]


async def award_card(
    owner_id: str, card_member_id: str, rarity: str, quantity: int, drawn_by_name: str = "admin"
) -> UserCard | None:
    """Manually award cards to a user. Returns None if rarity is invalid."""
    if rarity not in RARITY_ORDER:
        return None
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_cards
                (owner_id, card_member_id, rarity, quantity, first_acquired_at, drawn_by_name)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + excluded.quantity
            """,
            (owner_id, card_member_id, rarity, quantity, now, drawn_by_name),
        )
        await db.commit()
        async with db.execute(
            "SELECT id, owner_id, card_member_id, rarity, quantity, "
            "first_acquired_at, drawn_by_name "
            "FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        ) as cur:
            r = await cur.fetchone()
    assert r is not None
    return UserCard(
        id=r[0],
        owner_id=r[1],
        card_member_id=r[2],
        rarity=r[3],
        quantity=r[4],
        first_acquired_at=r[5],
        drawn_by_name=r[6],
    )


async def get_owned_card_subjects(owner_id: str) -> list[dict]:
    """Return distinct card subjects (real or synthetic) the owner has at least one copy of.
    Returns list of dicts with keys: discord_id, display_name, is_synthetic."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT m.discord_id, m.display_name, m.is_synthetic "
            "FROM user_cards uc JOIN members m ON uc.card_member_id = m.discord_id "
            "WHERE uc.owner_id = ? AND uc.quantity > 0 "
            "ORDER BY m.display_name",
            (owner_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {"discord_id": r[0], "display_name": r[1], "is_synthetic": bool(r[2])} for r in rows
    ]


async def get_member_display_name(discord_id: str) -> str | None:
    """Return a member's display name, or None if no such member exists."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name FROM members WHERE discord_id = ?", (discord_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def get_member_card_context(discord_id: str) -> MemberCardContext | None:
    """Return the member fields used to render card embeds and page headers."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url, bio, stats FROM members WHERE discord_id = ?",
            (discord_id,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return MemberCardContext(
        discord_id=discord_id,
        display_name=row[0],
        avatar_url=row[1],
        bio=row[2],
        stats_pairs=_parse_stats(row[3]),
    )


async def get_offer_discord_message_id(offer_id: int) -> str | None:
    """Return the Discord DM message ID stored on an offer, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_message_id FROM trade_offers WHERE id = ?", (offer_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] else None
