"""Card trading: marketplace listings and trade offers.

Every trade — whether it started from a marketplace listing or was proposed directly at
another player — is one `trade_offers` row carrying both sides: 'give' items move from
the proposer to the recipient, 'get' items move the other way. Accepting settles both
sides in a single transaction.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone

import aiosqlite

from superpal.cards.db import DB_PATH
from superpal.cards.models import CardRef, TradeListingFull, TradeOfferFull

TRADE_OFFER_EXPIRY_HOURS = 24
MAX_ITEMS_PER_SIDE = 8


def _tally(items: list[CardRef]) -> Counter[tuple[str, str]]:
    """Count how many copies of each (member, rarity) a side asks for."""
    return Counter((item.member_id, item.rarity) for item in items)


async def _holds_all(db: aiosqlite.Connection, owner_id: str, items: list[CardRef]) -> bool:
    """True if owner_id holds every card in items, counting duplicates."""
    for (member_id, rarity), needed in _tally(items).items():
        async with db.execute(
            "SELECT quantity FROM user_cards "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, member_id, rarity),
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] < needed:
            return False
    return True


# ─── Listings ────────────────────────────────────────────────────────────────


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
        "SELECT tli.card_member_id, tli.rarity, cm.display_name, cm.avatar_url "
        "FROM trade_listing_items tli "
        "LEFT JOIN members cm ON tli.card_member_id = cm.discord_id "
        "WHERE tli.listing_id = ?",
        (listing_id,),
    ) as cur:
        items = [
            CardRef(member_id=r[0], rarity=r[1], display_name=r[2], avatar_url=r[3])
            for r in await cur.fetchall()
        ]
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
    if len(items) > MAX_ITEMS_PER_SIDE:
        return "too_many_items"
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        if not await _holds_all(db, owner_id, items):
            await db.rollback()
            return "no_card"
        await db.execute(
            "INSERT INTO trade_listings (owner_id, status, ask_note, created_at) "
            "VALUES (?, 'active', ?, ?)",
            (owner_id, ask_note or None, now),
        )
        async with db.execute("SELECT last_insert_rowid()") as cur:
            rowid_row = await cur.fetchone()
        assert rowid_row is not None
        listing_id = rowid_row[0]
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


# ─── Offers ──────────────────────────────────────────────────────────────────


async def _load_side(db: aiosqlite.Connection, offer_id: int, side: str) -> list[CardRef]:
    async with db.execute(
        "SELECT toi.card_member_id, toi.rarity, cm.display_name, cm.avatar_url "
        "FROM trade_offer_items toi "
        "LEFT JOIN members cm ON toi.card_member_id = cm.discord_id "
        "WHERE toi.offer_id = ? AND toi.side = ?",
        (offer_id, side),
    ) as cur:
        return [
            CardRef(member_id=r[0], rarity=r[1], display_name=r[2], avatar_url=r[3])
            for r in await cur.fetchall()
        ]


async def _load_offer_full(db: aiosqlite.Connection, offer_id: int) -> TradeOfferFull | None:
    """Load a TradeOfferFull from an open aiosqlite connection."""
    async with db.execute(
        "SELECT to_.id, to_.listing_id, to_.proposer_id, pm.display_name, to_.recipient_id, "
        "rm.display_name, to_.status, to_.created_at, to_.expires_at, to_.counter_of_id "
        "FROM trade_offers to_ "
        "JOIN members pm ON to_.proposer_id = pm.discord_id "
        "JOIN members rm ON to_.recipient_id = rm.discord_id "
        "WHERE to_.id = ?",
        (offer_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    (
        offer_id_,
        listing_id,
        proposer_id,
        proposer_name,
        recipient_id,
        recipient_name,
        status,
        created_at,
        expires_at,
        counter_of_id,
    ) = row
    return TradeOfferFull(
        id=offer_id_,
        proposer_id=proposer_id,
        proposer_display_name=proposer_name,
        recipient_id=recipient_id,
        recipient_display_name=recipient_name,
        status=status,
        created_at=created_at,
        expires_at=expires_at,
        give_items=await _load_side(db, offer_id, "give"),
        get_items=await _load_side(db, offer_id, "get"),
        listing=await _load_listing_full(db, listing_id) if listing_id else None,
        counter_of_id=counter_of_id,
    )


async def _insert_offer(
    db: aiosqlite.Connection,
    proposer_id: str,
    recipient_id: str,
    give: list[CardRef],
    get: list[CardRef],
    listing_id: int | None,
    counter_of_id: int | None,
) -> int:
    """Write the offer row and both sides. Caller owns the transaction."""
    now = datetime.now(timezone.utc)
    await db.execute(
        "INSERT INTO trade_offers (listing_id, proposer_id, recipient_id, counter_of_id, "
        "status, created_at, expires_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (
            listing_id,
            proposer_id,
            recipient_id,
            counter_of_id,
            now.isoformat(),
            (now + timedelta(hours=TRADE_OFFER_EXPIRY_HOURS)).isoformat(),
        ),
    )
    async with db.execute("SELECT last_insert_rowid()") as cur:
        rowid_row = await cur.fetchone()
    assert rowid_row is not None
    offer_id = rowid_row[0]
    for side, items in (("give", give), ("get", get)):
        for item in items:
            await db.execute(
                "INSERT INTO trade_offer_items (offer_id, card_member_id, rarity, side) "
                "VALUES (?, ?, ?, ?)",
                (offer_id, item.member_id, item.rarity, side),
            )
    return offer_id


async def create_listing_offer(
    listing_id: int,
    proposer_id: str,
    give: list[CardRef],
) -> TradeOfferFull | str:
    """Offer `give` for everything on a marketplace listing.

    The listing's items are snapshotted as the offer's 'get' side so the trade settles
    from the offer alone. Returns TradeOfferFull or an error key.
    """
    if not give:
        return "empty_items"
    if len(give) > MAX_ITEMS_PER_SIDE:
        return "too_many_items"
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
        recipient_id = row[0]
        if recipient_id == proposer_id:
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
        if not await _holds_all(db, proposer_id, give):
            await db.rollback()
            return "no_card"
        async with db.execute(
            "SELECT tli.card_member_id, tli.rarity FROM trade_listing_items tli "
            "WHERE tli.listing_id = ?",
            (listing_id,),
        ) as cur:
            get = [CardRef(member_id=r[0], rarity=r[1]) for r in await cur.fetchall()]
        offer_id = await _insert_offer(db, proposer_id, recipient_id, give, get, listing_id, None)
        await db.commit()
        offer = await _load_offer_full(db, offer_id)
    return offer or "not_found"


async def create_direct_offer(
    proposer_id: str,
    recipient_id: str,
    give: list[CardRef],
    get: list[CardRef],
    counter_of_id: int | None = None,
) -> TradeOfferFull | str:
    """Propose a trade straight at another player, with no listing involved.

    Both sides are validated against what each player currently holds. When
    counter_of_id is given, that offer must be pending and addressed to the proposer;
    it is closed as 'countered' in the same transaction. Returns TradeOfferFull or an
    error key.
    """
    if not give or not get:
        return "empty_items"
    if len(give) > MAX_ITEMS_PER_SIDE or len(get) > MAX_ITEMS_PER_SIDE:
        return "too_many_items"
    if proposer_id == recipient_id:
        return "self_offer"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        if counter_of_id is not None:
            async with db.execute(
                "SELECT proposer_id, recipient_id FROM trade_offers "
                "WHERE id = ? AND status = 'pending'",
                (counter_of_id,),
            ) as cur:
                parent = await cur.fetchone()
            if not parent or parent[1] != proposer_id or parent[0] != recipient_id:
                await db.rollback()
                return "not_found"
        if not await _holds_all(db, proposer_id, give):
            await db.rollback()
            return "no_card"
        if not await _holds_all(db, recipient_id, get):
            await db.rollback()
            return "recipient_no_card"
        if counter_of_id is not None:
            await db.execute(
                "UPDATE trade_offers SET status = 'countered' WHERE id = ?", (counter_of_id,)
            )
        offer_id = await _insert_offer(
            db, proposer_id, recipient_id, give, get, None, counter_of_id
        )
        await db.commit()
        offer = await _load_offer_full(db, offer_id)
    return offer or "not_found"


async def _move_cards(
    db: aiosqlite.Connection, items: list[tuple[str, str]], from_id: str, to_id: str, now_iso: str
) -> None:
    for card_member_id, rarity in items:
        await db.execute(
            "UPDATE user_cards SET quantity = quantity - 1 "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (from_id, card_member_id, rarity),
        )
        await db.execute(
            "INSERT INTO user_cards "
            "(owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(owner_id, card_member_id, rarity) "
            "DO UPDATE SET quantity = quantity + 1",
            (to_id, card_member_id, rarity, now_iso),
        )


async def accept_offer(offer_id: int, recipient_id: str) -> tuple[bool, str | None]:
    """Accept an offer: atomically swap both sides and close out the listing, if any."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")
        async with db.execute(
            "SELECT listing_id, proposer_id, recipient_id, expires_at FROM trade_offers "
            "WHERE id = ? AND status = 'pending'",
            (offer_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.rollback()
            return False, "not_found"
        listing_id, proposer_id, offer_recipient_id, expires_at = row
        if offer_recipient_id != recipient_id:
            await db.rollback()
            return False, "not_recipient"
        now = datetime.now(timezone.utc)
        if datetime.fromisoformat(expires_at) <= now:
            await db.rollback()
            return False, "expired"

        sides: dict[str, list[tuple[str, str]]] = {}
        for side in ("give", "get"):
            async with db.execute(
                "SELECT card_member_id, rarity FROM trade_offer_items "
                "WHERE offer_id = ? AND side = ?",
                (offer_id, side),
            ) as cur:
                sides[side] = [(r[0], r[1]) for r in await cur.fetchall()]
        holdings = (
            ("give", proposer_id, "give_no_card"),
            ("get", recipient_id, "get_no_card"),
        )
        for side, owner_id, error in holdings:
            refs = [CardRef(member_id=m, rarity=r) for m, r in sides[side]]
            if not await _holds_all(db, owner_id, refs):
                await db.rollback()
                return False, error

        now_iso = now.isoformat()
        await _move_cards(db, sides["give"], proposer_id, recipient_id, now_iso)
        await _move_cards(db, sides["get"], recipient_id, proposer_id, now_iso)
        await db.execute("UPDATE trade_offers SET status = 'accepted' WHERE id = ?", (offer_id,))
        if listing_id is not None:
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
    """Decline an offer (called by its recipient)."""
    async with aiosqlite.connect(DB_PATH) as db:
        result = await db.execute(
            "UPDATE trade_offers SET status = 'declined' "
            "WHERE id = ? AND recipient_id = ? AND status = 'pending'",
            (offer_id, recipient_id),
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


async def _load_offers(query: str, params: tuple) -> list[TradeOfferFull]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cur:
            ids = [r[0] for r in await cur.fetchall()]
        return [o for oid in ids if (o := await _load_offer_full(db, oid))]


async def get_outgoing_offers(user_id: str) -> list[TradeOfferFull]:
    """Return all pending offers this user has sent."""
    return await _load_offers(
        "SELECT id FROM trade_offers WHERE proposer_id = ? AND status = 'pending' "
        "ORDER BY created_at DESC",
        (user_id,),
    )


async def get_incoming_offers(user_id: str) -> list[TradeOfferFull]:
    """Return all pending offers awaiting this user's answer."""
    return await _load_offers(
        "SELECT id FROM trade_offers WHERE recipient_id = ? AND status = 'pending' "
        "ORDER BY created_at DESC",
        (user_id,),
    )


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


async def get_offer_discord_message_id(offer_id: int) -> str | None:
    """Return the Discord DM message ID stored on an offer, or None."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_message_id FROM trade_offers WHERE id = ?", (offer_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] else None
