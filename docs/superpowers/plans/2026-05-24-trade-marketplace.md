# Trade Marketplace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-card Discord-only trade system with a web marketplace that supports flexible multi-card bundle trades, browsable listings, and Discord DM notifications.

**Architecture:** Four new normalized DB tables (`trade_listings`, `trade_listing_items`, `trade_offers`, `trade_offer_items`) replace `pending_trades` for new trades. The webapp hosts the primary trade UI; the Discord bot sends DM notifications with Accept/Decline buttons. `pending_trades` and its existing service functions are left untouched.

**Tech Stack:** Python 3.13, aiosqlite, FastAPI, Jinja2, discord.py, vanilla JS (no new libraries)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/superpal/cards/db.py` | Modify | Add 4 new table definitions to `_SCHEMA` |
| `src/superpal/cards/models.py` | Modify | Add `CardRef`, `TradeListingFull`, `TradeOfferFull` dataclasses |
| `src/superpal/cards/service.py` | Modify | Add all listing/offer service functions |
| `src/superpal/webapp/routes.py` | Modify | Add marketplace routes; update collection context with listing status |
| `src/superpal/webapp/templates/collection.html` | Modify | Add right-click context menu, listing badge overlay |
| `src/superpal/webapp/templates/marketplace.html` | Create | Marketplace UI: listing grid, sidebar, offer modal, My Offers tab |
| `src/bot.py` | Modify | Update `/card-trade` to send marketplace link; add `TradeOfferView`; add `notify_trade_offer` |
| `tests/cards/test_trade_service.py` | Create | Unit tests for all new service functions |

---

## Task 1: DB Schema

**Files:**
- Modify: `src/superpal/cards/db.py`

- [ ] **Step 1: Add the four new tables to `_SCHEMA`**

In `src/superpal/cards/db.py`, append to the `_SCHEMA` string (before the closing `"""`):

```python
CREATE TABLE IF NOT EXISTS trade_listings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    TEXT NOT NULL REFERENCES members(discord_id),
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'cancelled', 'completed')),
    ask_note    TEXT,
    created_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_listing_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id     INTEGER NOT NULL REFERENCES trade_listings(id),
    card_member_id TEXT NOT NULL REFERENCES members(discord_id),
    rarity         TEXT NOT NULL CHECK(rarity IN ('common','uncommon','rare','legendary'))
);

CREATE TABLE IF NOT EXISTS trade_offers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id          INTEGER NOT NULL REFERENCES trade_listings(id),
    proposer_id         TEXT NOT NULL REFERENCES members(discord_id),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','accepted','declined','expired','cancelled')),
    created_at          TIMESTAMP NOT NULL,
    expires_at          TIMESTAMP NOT NULL,
    discord_message_id  TEXT
);

CREATE TABLE IF NOT EXISTS trade_offer_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id       INTEGER NOT NULL REFERENCES trade_offers(id),
    card_member_id TEXT NOT NULL REFERENCES members(discord_id),
    rarity         TEXT NOT NULL CHECK(rarity IN ('common','uncommon','rare','legendary'))
);
```

- [ ] **Step 2: Verify schema runs clean**

```bash
cd src && ../.venv/bin/python -c "
import asyncio, os, tempfile
os.environ['CARDS_DB_PATH'] = tempfile.mktemp(suffix='.db')
import importlib
import superpal.cards.db as db
importlib.reload(db)
asyncio.run(db.init_db())
print('schema OK')
"
```

Expected: `schema OK`

- [ ] **Step 3: Commit**

```bash
git add src/superpal/cards/db.py
git commit -m "feat: add trade marketplace DB tables"
```

---

## Task 2: Models

**Files:**
- Modify: `src/superpal/cards/models.py`

- [ ] **Step 1: Add three new dataclasses**

Append to `src/superpal/cards/models.py` (after the existing `PendingTrade` class):

```python
@dataclass
class CardRef:
    member_id: str
    rarity: str


@dataclass
class TradeListingFull:
    id: int
    owner_id: str
    owner_display_name: str
    status: str
    ask_note: str | None
    created_at: str
    items: list[CardRef]
    offer_count: int


@dataclass
class TradeOfferFull:
    id: int
    listing_id: int
    proposer_id: str
    proposer_display_name: str
    status: str
    created_at: str
    expires_at: str
    items: list[CardRef]
    listing: TradeListingFull
```

- [ ] **Step 2: Verify import**

```bash
cd src && ../.venv/bin/python -c "from superpal.cards.models import CardRef, TradeListingFull, TradeOfferFull; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/superpal/cards/models.py
git commit -m "feat: add CardRef, TradeListingFull, TradeOfferFull models"
```

---

## Task 3: Service — Listing Functions

**Files:**
- Modify: `src/superpal/cards/service.py`

- [ ] **Step 1: Update imports at top of service.py**

The existing import line is:
```python
from superpal.cards.models import RARITY_ORDER, RARITY_WEIGHTS, MagicLink, PendingTrade, UserCard
```

Replace with:
```python
from superpal.cards.models import (
    RARITY_ORDER,
    RARITY_WEIGHTS,
    CardRef,
    MagicLink,
    PendingTrade,
    TradeListingFull,
    TradeOfferFull,
    UserCard,
)
```

Also add after `TRADE_EXPIRY_MINUTES = 10`:
```python
TRADE_OFFER_EXPIRY_HOURS = 24
```

- [ ] **Step 2: Add a helper to load listing items from an open DB connection**

Add this private helper near the bottom of service.py (before the `create_trade_offer` function area is fine):

```python
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
```

- [ ] **Step 3: Add `create_listing`**

```python
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
```

- [ ] **Step 4: Add `cancel_listing`**

```python
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
```

- [ ] **Step 5: Add `get_active_listings`**

```python
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
```

- [ ] **Step 6: Add `get_player_listings`**

```python
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
```

- [ ] **Step 7: Verify syntax**

```bash
cd src && ../.venv/bin/python -c "
import importlib, superpal.cards.service as svc
importlib.reload(svc)
print('service imports OK')
"
```

Expected: `service imports OK`

- [ ] **Step 8: Commit**

```bash
git add src/superpal/cards/service.py
git commit -m "feat: add trade listing service functions"
```

---

## Task 4: Service — Offer Functions

**Files:**
- Modify: `src/superpal/cards/service.py`

- [ ] **Step 1: Add a private helper to load an offer with its items**

```python
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
```

- [ ] **Step 2: Add `create_offer`**

```python
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
            return "not_found"
        if row[0] == proposer_id:
            return "self_offer"
        async with db.execute(
            "SELECT id FROM trade_offers "
            "WHERE listing_id = ? AND proposer_id = ? AND status = 'pending'",
            (listing_id, proposer_id),
        ) as cur:
            if await cur.fetchone():
                return "duplicate_offer"
        for item in items:
            async with db.execute(
                "SELECT quantity FROM user_cards "
                "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
                (proposer_id, item.member_id, item.rarity),
            ) as cur:
                card_row = await cur.fetchone()
            if not card_row or card_row[0] < 1:
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
```

- [ ] **Step 3: Add `accept_offer`**

```python
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
            return False, "not_found"
        listing_id, proposer_id, listing_owner_id = row
        if listing_owner_id != recipient_id:
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
                    return False, "listing_no_card"
        for card_member_id, rarity in offer_items:
            async with db.execute(
                "SELECT quantity FROM user_cards "
                "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
                (proposer_id, card_member_id, rarity),
            ) as cur:
                if not (row2 := await cur.fetchone()) or row2[0] < 1:
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
```

- [ ] **Step 4: Add `decline_offer`, `cancel_offer`, `expire_offer`**

```python
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
```

- [ ] **Step 5: Add `get_offers_for_listing`, `get_my_offers`, `get_offer_by_id`, `set_offer_discord_message_id`**

```python
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
```

- [ ] **Step 6: Verify all functions import**

```bash
cd src && ../.venv/bin/python -c "
from superpal.cards.service import (
    create_listing, cancel_listing, get_active_listings, get_player_listings,
    create_offer, accept_offer, decline_offer, cancel_offer, expire_offer,
    get_offers_for_listing, get_my_offers, get_offer_by_id, set_offer_discord_message_id,
)
print('all service functions OK')
"
```

Expected: `all service functions OK`

- [ ] **Step 7: Commit**

```bash
git add src/superpal/cards/service.py
git commit -m "feat: add trade offer service functions"
```

---

## Task 5: Unit Tests

**Files:**
- Create: `tests/cards/test_trade_service.py`

Tests use the `db` fixture from `tests/cards/conftest.py` (provides `db_mod, svc_mod` after `init_db()`). The `db_mods` fixture patches `CARDS_DB_PATH` to a temp file and reloads all card modules.

- [ ] **Step 1: Create the test file**

```python
# tests/cards/test_trade_service.py
import pytest

from superpal.cards.models import CardRef


@pytest.fixture
async def db(db_mods):
    db_mod, svc_mod, *_ = db_mods
    await db_mod.init_db()
    return db_mod, svc_mod


async def _seed_two_players(svc):
    """Insert Alice (111) and Bob (222) as members."""
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
    ])


async def _give_card(db_mod, owner_id: str, member_id: str, rarity: str, qty: int = 1):
    """Directly insert a user_card row."""
    import aiosqlite
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as db:
        await db.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(owner_id, card_member_id, rarity) DO UPDATE SET quantity = ?",
            (owner_id, member_id, rarity, qty, now, qty),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_create_listing_rejects_empty_items(db):
    _db_mod, svc = db
    await _seed_two_players(svc)
    result = await svc.create_listing("111", [], None)
    assert result == "empty_items"


@pytest.mark.asyncio
async def test_create_listing_rejects_unowned_card(db):
    _db_mod, svc = db
    await _seed_two_players(svc)
    result = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert result == "no_card"


@pytest.mark.asyncio
async def test_create_listing_success(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    result = await svc.create_listing("111", [CardRef("222", "common")], "want a rare")
    assert not isinstance(result, str)
    assert result.owner_id == "111"
    assert result.ask_note == "want a rare"
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_cancel_listing_owner_only(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    # Non-owner cannot cancel
    assert not await svc.cancel_listing(listing.id, "222")
    # Owner can cancel
    assert await svc.cancel_listing(listing.id, "111")


@pytest.mark.asyncio
async def test_get_active_listings_excludes_own(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    await svc.create_listing("111", [CardRef("222", "common")], None)
    listings = await svc.get_active_listings(exclude_owner_id="111")
    assert len(listings) == 0
    listings_all = await svc.get_active_listings()
    assert len(listings_all) == 1


@pytest.mark.asyncio
async def test_create_offer_rejects_self_offer(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    await _give_card(db_mod, "111", "222", "uncommon")
    result = await svc.create_offer(listing.id, "111", [CardRef("222", "uncommon")])
    assert result == "self_offer"


@pytest.mark.asyncio
async def test_create_offer_rejects_unowned_card(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    # Bob has no cards
    result = await svc.create_offer(listing.id, "222", [CardRef("111", "rare")])
    assert result == "no_card"


@pytest.mark.asyncio
async def test_create_offer_rejects_duplicate(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    await _give_card(db_mod, "222", "111", "uncommon", qty=2)
    await svc.create_offer(listing.id, "222", [CardRef("111", "uncommon")])
    result = await svc.create_offer(listing.id, "222", [CardRef("111", "uncommon")])
    assert result == "duplicate_offer"


@pytest.mark.asyncio
async def test_accept_offer_swaps_cards_and_declines_siblings(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    # Alice lists a COMMON of Bob
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    # Bob offers an UNCOMMON of Alice
    await _give_card(db_mod, "222", "111", "uncommon")
    offer = await svc.create_offer(listing.id, "222", [CardRef("111", "uncommon")])
    assert not isinstance(offer, str)
    # A second offer from a third player (use "333")
    await svc.sync_members([{"discord_id": "333", "display_name": "Carol", "avatar_url": None}])
    await _give_card(db_mod, "333", "111", "rare")
    offer2 = await svc.create_offer(listing.id, "333", [CardRef("111", "rare")])
    assert not isinstance(offer2, str)
    # Alice accepts Bob's offer
    ok, err = await svc.accept_offer(offer.id, "111")
    assert ok is True and err is None
    # Bob now has COMMON of Bob; Alice now has UNCOMMON of Alice
    import aiosqlite
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT quantity FROM user_cards WHERE owner_id='222' AND card_member_id='222' AND rarity='common'"
        ) as cur:
            bob_common = (await cur.fetchone())[0]
        async with conn.execute(
            "SELECT quantity FROM user_cards WHERE owner_id='111' AND card_member_id='111' AND rarity='uncommon'"
        ) as cur:
            alice_uncommon = (await cur.fetchone())[0]
        async with conn.execute(
            "SELECT status FROM trade_offers WHERE id=?", (offer2.id,)
        ) as cur:
            sibling_status = (await cur.fetchone())[0]
    assert bob_common == 1
    assert alice_uncommon == 1
    assert sibling_status == "declined"


@pytest.mark.asyncio
async def test_accept_offer_fails_if_card_no_longer_held(db):
    db_mod, svc = db
    await _seed_two_players(svc)
    await _give_card(db_mod, "111", "222", "common")
    listing = await svc.create_listing("111", [CardRef("222", "common")], None)
    assert not isinstance(listing, str)
    await _give_card(db_mod, "222", "111", "uncommon")
    offer = await svc.create_offer(listing.id, "222", [CardRef("111", "uncommon")])
    assert not isinstance(offer, str)
    # Remove Alice's listing card before she accepts
    import aiosqlite
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "UPDATE user_cards SET quantity = 0 WHERE owner_id='111' AND card_member_id='222' AND rarity='common'"
        )
        await conn.commit()
    ok, err = await svc.accept_offer(offer.id, "111")
    assert ok is False
    assert err == "listing_no_card"
```

- [ ] **Step 2: Run the tests**

```bash
.venv/bin/python -m pytest tests/cards/test_trade_service.py -v
```

Expected: all tests pass (green).

- [ ] **Step 3: Commit**

```bash
git add tests/cards/test_trade_service.py
git commit -m "test: add trade marketplace service unit tests"
```

---

## Task 6: Webapp Routes

**Files:**
- Modify: `src/superpal/webapp/routes.py`

- [ ] **Step 1: Add new imports to routes.py**

Add to the existing `from superpal.cards.service import (...)` block:

```python
from superpal.cards.service import (
    # ... existing imports ...
    accept_offer,
    cancel_listing,
    cancel_offer,
    create_listing,
    create_offer,
    decline_offer,
    get_active_listings,
    get_my_offers,
    get_offers_for_listing,
    get_player_listings,
)
from superpal.cards.models import CardRef
```

- [ ] **Step 2: Update `_collection_context` to include listing status**

Replace the existing `_collection_context` function (lines 60–87) with:

```python
async def _collection_context(user_id: str) -> dict:
    data = await get_collection(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url FROM members WHERE discord_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        async with db.execute(
            "SELECT COALESCE(SUM(draws_used), 0) FROM draw_log WHERE user_id = ?",
            (user_id,),
        ) as cur:
            drow = await cur.fetchone()
    total_draws = drow[0] if drow else 0
    unique_members = len({c["member_id"] for c in data["owned"]})
    total_eligible = unique_members + len(data["undiscovered"])
    completion_pct = round(unique_members / total_eligible * 100) if total_eligible > 0 else 0

    # Build a lookup: (member_id, rarity) -> listing_id for the user's active listings
    my_listings = await get_player_listings(user_id)
    listed_card_keys: dict[str, int] = {}
    for listing in my_listings:
        for item in listing.items:
            listed_card_keys[f"{item.member_id}:{item.rarity}"] = listing.id

    for card in data["owned"]:
        key = f"{card['member_id']}:{card['rarity']}"
        card["listing_id"] = listed_card_keys.get(key)

    return {
        "display_name": row[0] if row else "Unknown",
        "avatar_url": row[1] if row else None,
        "owned": data["owned"],
        "undiscovered": data["undiscovered"],
        "counts": data["counts"],
        "total_cards": sum(c["quantity"] for c in data["owned"]),
        "total_draws": total_draws,
        "unique_members": unique_members,
        "completion_pct": completion_pct,
    }
```

- [ ] **Step 3: Add the marketplace context helper**

Add after `_collection_context`:

```python
async def _marketplace_context(user_id: str) -> dict:
    listings = await get_active_listings(exclude_owner_id=user_id)
    my_listings = await get_player_listings(user_id)
    my_offers = await get_my_offers(user_id)
    collection = await get_collection(user_id)

    # Aggregate active traders for sidebar
    trader_counts: dict[str, dict] = {}
    for listing in listings:
        oid = listing.owner_id
        if oid not in trader_counts:
            trader_counts[oid] = {"display_name": listing.owner_display_name, "count": 0}
        trader_counts[oid]["count"] += 1
    active_traders = sorted(trader_counts.values(), key=lambda x: -x["count"])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url FROM members WHERE discord_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()

    return {
        "display_name": row[0] if row else "Unknown",
        "avatar_url": row[1] if row else None,
        "listings": listings,
        "my_listings": my_listings,
        "my_offers": my_offers,
        "my_collection": collection["owned"],
        "active_traders": active_traders,
        "pending_offer_count": len(my_offers),
    }
```

- [ ] **Step 4: Add the GET /marketplace route**

```python
@router.get("/marketplace", response_class=HTMLResponse)
async def marketplace_view(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    ctx = await _marketplace_context(session.user_id)
    return templates.TemplateResponse(request, "marketplace.html", ctx)
```

- [ ] **Step 5: Add POST routes for listings**

```python
@router.post("/marketplace/listing")
async def create_listing_route(
    request: Request,
    card_member_ids: list[str] = Form(...),
    card_rarities: list[str] = Form(...),
    ask_note: str = Form(""),
):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    items = [
        CardRef(member_id=mid, rarity=rar)
        for mid, rar in zip(card_member_ids, card_rarities)
    ]
    await create_listing(session.user_id, items, ask_note.strip() or None)
    return RedirectResponse(url="/collection", status_code=303)


@router.post("/marketplace/listing/{listing_id}/cancel")
async def cancel_listing_route(listing_id: int, request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    await cancel_listing(listing_id, session.user_id)
    return RedirectResponse(url="/collection", status_code=303)
```

- [ ] **Step 6: Add POST routes for offers**

```python
@router.post("/marketplace/listing/{listing_id}/offer")
async def create_offer_route(
    listing_id: int,
    request: Request,
    card_member_ids: list[str] = Form(...),
    card_rarities: list[str] = Form(...),
):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    items = [
        CardRef(member_id=mid, rarity=rar)
        for mid, rar in zip(card_member_ids, card_rarities)
    ]
    offer = await create_offer(listing_id, session.user_id, items)
    if not isinstance(offer, str):
        try:
            from bot import notify_trade_offer as _notify
            import asyncio
            asyncio.create_task(_notify(offer.id))
        except ImportError:
            pass
    return RedirectResponse(url="/marketplace", status_code=303)


@router.post("/marketplace/offer/{offer_id}/accept")
async def accept_offer_route(offer_id: int, request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    ok, err = await accept_offer(offer_id, session.user_id)
    if ok:
        try:
            from bot import edit_offer_dm as _edit
            import asyncio
            asyncio.create_task(_edit(offer_id, "Trade accepted! Cards have been exchanged."))
        except ImportError:
            pass
    return RedirectResponse(url="/marketplace", status_code=303)


@router.post("/marketplace/offer/{offer_id}/decline")
async def decline_offer_route(offer_id: int, request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    await decline_offer(offer_id, session.user_id)
    try:
        from bot import edit_offer_dm as _edit
        import asyncio
        asyncio.create_task(_edit(offer_id, "Offer declined."))
    except ImportError:
        pass
    return RedirectResponse(url="/marketplace", status_code=303)


@router.post("/marketplace/offer/{offer_id}/cancel")
async def cancel_offer_route(offer_id: int, request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    await cancel_offer(offer_id, session.user_id)
    return RedirectResponse(url="/marketplace", status_code=303)
```

- [ ] **Step 7: Verify routes.py imports and syntax**

```bash
cd src && ../.venv/bin/python -c "import superpal.webapp.routes; print('routes OK')"
```

Expected: `routes OK`

- [ ] **Step 8: Run existing tests**

```bash
.venv/bin/python -m pytest tests/cards/ -q
```

Expected: all existing tests still pass.

- [ ] **Step 9: Commit**

```bash
git add src/superpal/webapp/routes.py
git commit -m "feat: add marketplace webapp routes"
```

---

## Task 7: marketplace.html Template

**Files:**
- Create: `src/superpal/webapp/templates/marketplace.html`

- [ ] **Step 1: Create the template**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Marketplace — Bringus Card Game</title>
  <link rel="icon" href="/static/favicon.ico">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1e1f22; color: #dcddde; font-family: sans-serif; }
    .page { display: flex; flex-direction: column; min-height: 100vh; }
    .topbar { display: flex; align-items: center; gap: 16px; padding: 14px 24px;
              border-bottom: 1px solid #3f4147; flex-wrap: wrap; }
    .topbar-avatar { width: 36px; height: 36px; border-radius: 50%; background: #5865f2;
                     object-fit: cover; }
    .topbar-avatar-placeholder { width: 36px; height: 36px; border-radius: 50%;
                                 background: linear-gradient(135deg,#667eea,#764ba2);
                                 display: flex; align-items: center; justify-content: center;
                                 color: #fff; font-weight: 700; font-size: 14px; }
    .topbar-name { font-weight: 600; font-size: 14px; }
    .nav { display: flex; gap: 4px; margin-left: auto; }
    .nav-btn { padding: 6px 14px; border-radius: 4px; font-size: 13px; font-weight: 600;
               cursor: pointer; border: none; background: transparent; color: #72767d;
               text-decoration: none; display: inline-block; }
    .nav-btn:hover { color: #dcddde; background: #313338; }
    .nav-btn.active { background: #5865f2; color: #fff; }
    .badge { background: #ed4245; color: #fff; border-radius: 10px;
             font-size: 10px; padding: 1px 5px; margin-left: 4px; }
    .layout { display: flex; flex: 1; padding: 20px 24px; gap: 20px; }
    .main { flex: 1; min-width: 0; }
    .sidebar { width: 220px; flex-shrink: 0; }
    .section-title { font-size: 11px; font-weight: 700; color: #72767d;
                     letter-spacing: 1px; text-transform: uppercase; margin-bottom: 12px; }
    .listing-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
                    gap: 12px; }
    .listing-card { background: #2b2d31; border-radius: 8px; padding: 14px;
                    border: 1px solid #3f4147; }
    .listing-card:hover { border-color: #5865f2; }
    .listing-owner { font-size: 11px; color: #72767d; margin-bottom: 8px; }
    .listing-items { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
    .listing-item { display: flex; align-items: center; gap: 5px; background: #313338;
                    border-radius: 4px; padding: 4px 8px; }
    .listing-item-avatar { width: 20px; height: 20px; border-radius: 50%;
                           background: #555; object-fit: cover; }
    .listing-item-name { font-size: 11px; color: #dcddde; }
    .rarity-common { color: #95a5a6; }
    .rarity-uncommon { color: #27ae60; }
    .rarity-rare { color: #2980b9; }
    .rarity-legendary { color: #f39c12; }
    .listing-ask { font-size: 11px; color: #72767d; font-style: italic; margin-bottom: 8px; }
    .listing-offers { font-size: 10px; color: #72767d; margin-bottom: 8px; }
    .btn { display: inline-block; padding: 5px 12px; border-radius: 4px;
           font-size: 12px; font-weight: 600; cursor: pointer; border: none; }
    .btn-primary { background: #5865f2; color: #fff; width: 100%; text-align: center; }
    .btn-primary:hover { background: #4752c4; }
    .btn-danger { background: #3f4147; color: #ed4245; }
    .btn-danger:hover { background: #ed4245; color: #fff; }
    .sidebar-section { background: #2b2d31; border-radius: 8px; padding: 14px;
                       margin-bottom: 16px; }
    .my-listing { padding: 8px 0; border-bottom: 1px solid #3f4147; }
    .my-listing:last-child { border-bottom: none; }
    .my-listing-items { font-size: 11px; color: #dcddde; margin-bottom: 4px; }
    .trader-row { display: flex; justify-content: space-between; align-items: center;
                  padding: 4px 0; font-size: 12px; }
    .trader-count { color: #72767d; font-size: 11px; }
    .empty-state { color: #72767d; font-size: 13px; font-style: italic; padding: 20px 0; }
    .tab-panel { display: none; }
    .tab-panel.active { display: block; }
    .offer-row { background: #2b2d31; border-radius: 6px; padding: 12px;
                 margin-bottom: 8px; border: 1px solid #3f4147; }
    .offer-meta { font-size: 11px; color: #72767d; margin-top: 4px; }
    .offer-status { font-size: 11px; font-weight: 700; }
    .status-pending { color: #faa61a; }
    .status-accepted { color: #3ba55c; }
    .status-declined { color: #ed4245; }
    .modal-backdrop { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8);
                      z-index: 100; align-items: flex-start; justify-content: center;
                      padding: 40px 16px; overflow-y: auto; }
    .modal-backdrop.open { display: flex; }
    .modal { background: #2b2d31; border-radius: 8px; padding: 24px;
             max-width: 560px; width: 100%; }
    .modal h2 { font-size: 1rem; margin-bottom: 4px; }
    .modal-close { float: right; background: none; border: none; color: #72767d;
                   font-size: 20px; cursor: pointer; margin-top: -4px; }
    .modal-close:hover { color: #fff; }
    .modal-listing { background: #313338; border-radius: 6px; padding: 10px;
                     margin-bottom: 16px; }
    .modal-section-title { font-size: 11px; color: #72767d; text-transform: uppercase;
                           letter-spacing: 1px; margin-bottom: 8px; }
    .pick-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(100px,1fr));
                 gap: 8px; max-height: 280px; overflow-y: auto; }
    .pick-card { background: #313338; border-radius: 6px; padding: 8px; text-align: center;
                 cursor: pointer; border: 2px solid transparent; }
    .pick-card:hover { border-color: #5865f2; }
    .pick-card.selected { border-color: #5865f2; background: #2a2d4a; }
    .pick-card-avatar { width: 32px; height: 32px; border-radius: 50%;
                        background: #555; object-fit: cover; margin: 0 auto 4px; }
    .pick-card-avatar-placeholder { width: 32px; height: 32px; border-radius: 50%;
                                    background: #555; margin: 0 auto 4px; }
    .pick-card-name { font-size: 10px; color: #dcddde; white-space: nowrap;
                      overflow: hidden; text-overflow: ellipsis; }
    .pick-card-rarity { font-size: 9px; font-weight: 700; }
    .offer-summary { margin: 12px 0; font-size: 12px; color: #72767d;
                     min-height: 18px; }
  </style>
</head>
<body>
<div class="page">
  <div class="topbar">
    {% if avatar_url %}
      <img class="topbar-avatar" src="{{ avatar_url }}" alt="">
    {% else %}
      <div class="topbar-avatar-placeholder">{{ display_name[0] | upper }}</div>
    {% endif %}
    <span class="topbar-name">{{ display_name }}</span>
    <div class="nav">
      <a class="nav-btn" href="/collection">My Collection</a>
      <button class="nav-btn active" onclick="showTab('listings')">Marketplace</button>
      <button class="nav-btn" onclick="showTab('offers')">
        My Offers{% if pending_offer_count %}<span class="badge">{{ pending_offer_count }}</span>{% endif %}
      </button>
    </div>
  </div>

  <div class="layout">
    <div class="main">

      <!-- Marketplace tab -->
      <div id="tab-listings" class="tab-panel active">
        <div class="section-title">Active Listings</div>
        {% if listings %}
        <div class="listing-grid">
          {% for listing in listings %}
          <div class="listing-card">
            <div class="listing-owner">Listed by <strong>{{ listing.owner_display_name }}</strong></div>
            <div class="listing-items">
              {% for item in listing.items %}
              <div class="listing-item">
                <div class="listing-item-avatar"></div>
                <span class="listing-item-name rarity-{{ item.rarity }}">{{ item.rarity | upper }}</span>
              </div>
              {% endfor %}
            </div>
            {% if listing.ask_note %}
            <div class="listing-ask">Wants: {{ listing.ask_note }}</div>
            {% else %}
            <div class="listing-ask">Open to any offer</div>
            {% endif %}
            {% if listing.offer_count %}
            <div class="listing-offers">{{ listing.offer_count }} pending offer{{ 's' if listing.offer_count != 1 else '' }}</div>
            {% endif %}
            <button class="btn btn-primary"
              onclick="openOfferModal({{ listing.id }}, {{ listing.items | tojson }}, '{{ listing.ask_note or '' }}', '{{ listing.owner_display_name | e }}')">
              Make Offer
            </button>
          </div>
          {% endfor %}
        </div>
        {% else %}
        <div class="empty-state">No active listings right now. Right-click a card on your collection page to list it.</div>
        {% endif %}
      </div>

      <!-- Offers tab -->
      <div id="tab-offers" class="tab-panel">
        <div class="section-title">Offers I've Sent</div>
        {% if my_offers %}
          {% for offer in my_offers %}
          <div class="offer-row">
            <div>
              Offering
              {% for item in offer.items %}<span class="rarity-{{ item.rarity }}">{{ item.rarity | upper }}</span>{% if not loop.last %}, {% endif %}{% endfor %}
              for
              {% for item in offer.listing.items %}<span class="rarity-{{ item.rarity }}">{{ item.rarity | upper }}</span>{% if not loop.last %}, {% endif %}{% endfor %}
              (listed by <strong>{{ offer.listing.owner_display_name }}</strong>)
            </div>
            <div class="offer-meta">
              <span class="offer-status status-{{ offer.status }}">{{ offer.status | upper }}</span>
              · expires {{ offer.expires_at[:10] }}
            </div>
            <form method="post" action="/marketplace/offer/{{ offer.id }}/cancel" style="margin-top:6px">
              <button class="btn btn-danger" type="submit">Cancel Offer</button>
            </form>
          </div>
          {% endfor %}
        {% else %}
          <div class="empty-state">You haven't sent any offers yet.</div>
        {% endif %}
      </div>

    </div>

    <!-- Sidebar -->
    <div class="sidebar">
      <div class="sidebar-section">
        <div class="section-title">My Listings</div>
        {% if my_listings %}
          {% for listing in my_listings %}
          <div class="my-listing">
            <div class="my-listing-items">
              {% for item in listing.items %}{{ item.rarity | upper }}{% if not loop.last %}, {% endif %}{% endfor %}
            </div>
            {% if listing.ask_note %}
            <div style="font-size:11px;color:#72767d;margin-bottom:4px;">{{ listing.ask_note }}</div>
            {% endif %}
            <form method="post" action="/marketplace/listing/{{ listing.id }}/cancel">
              <button class="btn btn-danger" type="submit" style="font-size:11px;padding:3px 8px;">Cancel</button>
            </form>
          </div>
          {% endfor %}
        {% else %}
          <div style="font-size:12px;color:#72767d;">No active listings.<br>Right-click a card in your collection to list it.</div>
        {% endif %}
      </div>

      {% if active_traders %}
      <div class="sidebar-section">
        <div class="section-title">Active Traders</div>
        {% for trader in active_traders %}
        <div class="trader-row">
          <span>{{ trader.display_name }}</span>
          <span class="trader-count">{{ trader.count }} listing{{ 's' if trader.count != 1 else '' }}</span>
        </div>
        {% endfor %}
      </div>
      {% endif %}
    </div>
  </div>
</div>

<!-- Offer modal -->
<div class="modal-backdrop" id="offer-modal">
  <div class="modal">
    <button class="modal-close" onclick="closeOfferModal()">×</button>
    <h2>Make an Offer</h2>
    <div style="font-size:12px;color:#72767d;margin-bottom:12px;">Listed by <strong id="modal-owner"></strong></div>
    <div class="modal-listing">
      <div class="modal-section-title">They're offering</div>
      <div id="modal-listing-items" class="listing-items"></div>
      <div id="modal-ask-note" class="listing-ask" style="display:none;margin-top:4px;"></div>
    </div>
    <div class="modal-section-title">Select cards from your collection to offer</div>
    <div class="pick-grid" id="pick-grid"></div>
    <div class="offer-summary" id="offer-summary">No cards selected.</div>
    <form id="offer-form" method="post">
      <div id="offer-hidden-inputs"></div>
      <button class="btn btn-primary" type="submit" id="offer-submit" disabled>Send Offer</button>
    </form>
  </div>
</div>

<script>
  const MY_COLLECTION = {{ my_collection | tojson }};
  const RARITY_COLORS = {
    common: '#95a5a6', uncommon: '#27ae60', rare: '#2980b9', legendary: '#f39c12'
  };

  function showTab(name) {
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
  }

  let _currentListingId = null;
  let _selectedCards = [];

  function openOfferModal(listingId, listingItems, askNote, ownerName) {
    _currentListingId = listingId;
    _selectedCards = [];
    document.getElementById('modal-owner').textContent = ownerName;

    const itemsEl = document.getElementById('modal-listing-items');
    itemsEl.innerHTML = listingItems.map(item =>
      `<div class="listing-item">
        <span class="listing-item-name" style="color:${RARITY_COLORS[item.rarity]}">${item.rarity.toUpperCase()}</span>
      </div>`
    ).join('');

    const askEl = document.getElementById('modal-ask-note');
    if (askNote) {
      askEl.textContent = 'Wants: ' + askNote;
      askEl.style.display = '';
    } else {
      askEl.style.display = 'none';
    }

    const grid = document.getElementById('pick-grid');
    grid.innerHTML = MY_COLLECTION.map((card, i) =>
      `<div class="pick-card" data-index="${i}" onclick="togglePickCard(this, ${i})"
            data-member-id="${card.member_id}" data-rarity="${card.rarity}">
        ${card.avatar_url
          ? `<img class="pick-card-avatar" src="${card.avatar_url}" alt="">`
          : `<div class="pick-card-avatar-placeholder"></div>`}
        <div class="pick-card-name">${card.display_name}</div>
        <div class="pick-card-rarity" style="color:${RARITY_COLORS[card.rarity]}">${card.rarity.toUpperCase()}</div>
        ${card.quantity > 1 ? `<div style="font-size:9px;color:#72767d;">×${card.quantity}</div>` : ''}
      </div>`
    ).join('');

    document.getElementById('offer-form').action = `/marketplace/listing/${listingId}/offer`;
    updateOfferSummary();
    document.getElementById('offer-modal').classList.add('open');
  }

  function closeOfferModal() {
    document.getElementById('offer-modal').classList.remove('open');
  }

  function togglePickCard(el, index) {
    const card = MY_COLLECTION[index];
    const key = card.member_id + ':' + card.rarity;
    const idx = _selectedCards.findIndex(c => c.member_id === card.member_id && c.rarity === card.rarity);
    if (idx >= 0) {
      _selectedCards.splice(idx, 1);
      el.classList.remove('selected');
    } else {
      _selectedCards.push(card);
      el.classList.add('selected');
    }
    updateOfferSummary();
  }

  function updateOfferSummary() {
    const summaryEl = document.getElementById('offer-summary');
    const submitEl = document.getElementById('offer-submit');
    const hiddenEl = document.getElementById('offer-hidden-inputs');
    if (_selectedCards.length === 0) {
      summaryEl.textContent = 'No cards selected.';
      submitEl.disabled = true;
      hiddenEl.innerHTML = '';
      return;
    }
    summaryEl.textContent = 'Offering: ' + _selectedCards.map(c =>
      c.rarity.toUpperCase() + ' ' + c.display_name
    ).join(', ');
    submitEl.disabled = false;
    hiddenEl.innerHTML = _selectedCards.map(c =>
      `<input type="hidden" name="card_member_ids" value="${c.member_id}">` +
      `<input type="hidden" name="card_rarities" value="${c.rarity}">`
    ).join('');
  }

  document.getElementById('offer-modal').addEventListener('click', function(e) {
    if (e.target === this) closeOfferModal();
  });
</script>
</body>
</html>
```

- [ ] **Step 2: Verify template renders (requires local bot run or a quick smoke test)**

```bash
cd src && ../.venv/bin/python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('superpal/webapp/templates'))
t = env.get_template('marketplace.html')
print('template parses OK')
"
```

Expected: `template parses OK`

- [ ] **Step 3: Commit**

```bash
git add src/superpal/webapp/templates/marketplace.html
git commit -m "feat: add marketplace.html template"
```

---

## Task 8: collection.html — Right-Click Menu and Listing Badge

**Files:**
- Modify: `src/superpal/webapp/templates/collection.html`

- [ ] **Step 1: Add a Marketplace nav link to the collection header**

In `collection.html`, the existing header help button is:

```html
<button class="help-btn" onclick="document.getElementById('help-modal').style.display='flex'" title="Help" style="margin-left:auto">?</button>
```

Replace with:

```html
<a href="/marketplace" style="margin-left:auto;padding:5px 12px;background:#3f4147;
   color:#b9bbbe;border-radius:4px;font-size:12px;font-weight:600;text-decoration:none;
   margin-right:8px;">🏪 Marketplace</a>
<button class="help-btn" onclick="document.getElementById('help-modal').style.display='flex'" title="Help">?</button>
```

- [ ] **Step 2: Add `data-member-id` and `data-listing-id` to each card div**

In the `{% for card in owned %}` block, the opening `<div class="card ...">` currently is:

```html
<div class="card card-{{ card.rarity }}"
     onclick="openCardDetail(this)"
     data-name="{{ card.display_name | e }}"
     data-rarity="{{ card.rarity }}"
     data-avatar="{{ card.avatar_url if card.avatar_url else '' }}"
     data-quantity="{{ card.quantity }}"
     data-bio="{{ card.bio | e if card.bio else '' }}"
     data-stats='{{ card.stats_pairs | tojson }}'>
```

Replace with:

```html
<div class="card card-{{ card.rarity }}"
     onclick="openCardDetail(this)"
     oncontextmenu="openCardMenu(event, this)"
     data-name="{{ card.display_name | e }}"
     data-rarity="{{ card.rarity }}"
     data-member-id="{{ card.member_id }}"
     data-listing-id="{{ card.listing_id or '' }}"
     data-avatar="{{ card.avatar_url if card.avatar_url else '' }}"
     data-quantity="{{ card.quantity }}"
     data-bio="{{ card.bio | e if card.bio else '' }}"
     data-stats='{{ card.stats_pairs | tojson }}'>
```

- [ ] **Step 3: Add the listing badge overlay inside each card**

After the `{% if card.quantity >= 3 %}` trade-in form block (just before the closing `</div>` of the card), add:

```html
{% if card.listing_id %}
  <div class="listing-badge" title="Listed for trade">🏪</div>
{% endif %}
```

Also add the badge style to the `<style>` block:

```css
.card { position: relative; }
.listing-badge { position: absolute; top: 6px; right: 6px; font-size: 13px;
                 background: rgba(0,0,0,0.5); border-radius: 3px; padding: 1px 3px; }
```

- [ ] **Step 4: Add the context menu HTML**

Before the `</body>` tag, add:

```html
<!-- Right-click context menu -->
<div id="card-menu" style="display:none;position:fixed;background:#2b2d31;
     border:1px solid #3f4147;border-radius:6px;padding:4px 0;z-index:200;min-width:150px;">
  <div id="menu-list-btn" class="menu-item" onclick="listSelectedCard()" style="display:none;
       padding:8px 14px;cursor:pointer;font-size:13px;">🏪 List for Trade</div>
  <div id="menu-unlist-btn" class="menu-item" onclick="unlistSelectedCard()" style="display:none;
       padding:8px 14px;cursor:pointer;font-size:13px;color:#ed4245;">Remove Listing</div>
</div>

<!-- Listing form (hidden) -->
<div id="list-form-backdrop" style="display:none;position:fixed;inset:0;
     background:rgba(0,0,0,0.75);z-index:150;align-items:center;justify-content:center;">
  <div style="background:#2b2d31;border-radius:8px;padding:24px;max-width:360px;width:90%;">
    <h2 style="font-size:1rem;margin-bottom:12px;color:#fff;">List Card for Trade</h2>
    <div id="list-form-card-info" style="font-size:13px;color:#b9bbbe;margin-bottom:12px;"></div>
    <form id="list-form" method="post" action="/marketplace/listing">
      <input type="hidden" id="list-form-member-id" name="card_member_ids" value="">
      <input type="hidden" id="list-form-rarity" name="card_rarities" value="">
      <label style="font-size:12px;color:#72767d;display:block;margin-bottom:4px;">
        What do you want in return? (optional)
      </label>
      <input type="text" name="ask_note" maxlength="100" placeholder="e.g. want a Rare"
             style="width:100%;background:#313338;border:1px solid #3f4147;border-radius:4px;
                    color:#dcddde;padding:6px 10px;font-size:13px;margin-bottom:12px;">
      <div style="display:flex;gap:8px;">
        <button type="submit"
                style="background:#5865f2;color:#fff;border:none;border-radius:4px;
                       padding:7px 16px;font-size:13px;font-weight:600;cursor:pointer;flex:1;">
          List It
        </button>
        <button type="button" onclick="closeListForm()"
                style="background:#3f4147;color:#b9bbbe;border:none;border-radius:4px;
                       padding:7px 16px;font-size:13px;cursor:pointer;">
          Cancel
        </button>
      </div>
    </form>
  </div>
</div>
```

Also add `.menu-item:hover { background: #313338; }` to the `<style>` block.

- [ ] **Step 5: Add the JS for the context menu**

Inside the `<script>` tag (after the existing `openCardDetail` function), add:

```javascript
let _menuCard = null;

function openCardMenu(e, el) {
  e.preventDefault();
  _menuCard = el;
  const menu = document.getElementById('card-menu');
  const listBtn = document.getElementById('menu-list-btn');
  const unlistBtn = document.getElementById('menu-unlist-btn');
  const listingId = el.dataset.listingId;

  listBtn.style.display = listingId ? 'none' : 'block';
  unlistBtn.style.display = listingId ? 'block' : 'none';

  menu.style.left = Math.min(e.clientX, window.innerWidth - 160) + 'px';
  menu.style.top = Math.min(e.clientY, window.innerHeight - 80) + 'px';
  menu.style.display = 'block';

  setTimeout(() => document.addEventListener('click', closeCardMenu, { once: true }), 0);
}

function closeCardMenu() {
  document.getElementById('card-menu').style.display = 'none';
}

function listSelectedCard() {
  closeCardMenu();
  if (!_menuCard) return;
  document.getElementById('list-form-member-id').value = _menuCard.dataset.memberId;
  document.getElementById('list-form-rarity').value = _menuCard.dataset.rarity;
  document.getElementById('list-form-card-info').textContent =
    _menuCard.dataset.name + ' — ' + _menuCard.dataset.rarity.toUpperCase();
  document.getElementById('list-form-backdrop').style.display = 'flex';
}

function closeListForm() {
  document.getElementById('list-form-backdrop').style.display = 'none';
}

function unlistSelectedCard() {
  closeCardMenu();
  if (!_menuCard || !_menuCard.dataset.listingId) return;
  const form = document.createElement('form');
  form.method = 'post';
  form.action = `/marketplace/listing/${_menuCard.dataset.listingId}/cancel`;
  document.body.appendChild(form);
  form.submit();
}
```

- [ ] **Step 6: Verify the template parses**

```bash
cd src && ../.venv/bin/python -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('superpal/webapp/templates'))
t = env.get_template('collection.html')
print('collection.html parses OK')
"
```

Expected: `collection.html parses OK`

- [ ] **Step 7: Commit**

```bash
git add src/superpal/webapp/templates/collection.html
git commit -m "feat: add listing badge and right-click menu to collection page"
```

---

## Task 9: Bot Changes

**Files:**
- Modify: `src/bot.py`

- [ ] **Step 1: Add new service imports to bot.py**

The existing import block from `superpal.cards.service` is:

```python
from superpal.cards.service import (
    TRADE_EXPIRY_MINUTES,
    create_trade_offer,
    decline_trade,
    draw_card,
    execute_trade,
    generate_magic_link,
    get_card_quantity,
    get_collection,
    get_leaderboard,
    gift_card,
    sync_members,
    trade_in,
    upgrade,
)
```

Add to it:

```python
from superpal.cards.service import (
    TRADE_EXPIRY_MINUTES,
    accept_offer,
    cancel_offer,
    create_trade_offer,
    decline_offer,
    decline_trade,
    draw_card,
    execute_trade,
    expire_offer,
    generate_magic_link,
    get_card_quantity,
    get_collection,
    get_leaderboard,
    get_offer_by_id,
    gift_card,
    set_offer_discord_message_id,
    sync_members,
    trade_in,
    upgrade,
)
```

- [ ] **Step 2: Add `TradeOfferView` class**

After the existing `TradeView` class (around line 183), add:

```python
class TradeOfferView(discord.ui.View):
    """Discord DM view sent when a marketplace offer arrives."""

    def __init__(self, offer_id: int, listing_owner_id: str):
        super().__init__(timeout=TRADE_OFFER_EXPIRY_HOURS * 3600)
        self.offer_id = offer_id
        self.listing_owner_id = listing_owner_id
        self.message: discord.Message | None = None

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.listing_owner_id:
            await interaction.response.send_message(
                "Only the listing owner can accept.", ephemeral=True
            )
            return
        success, reason = await accept_offer(self.offer_id, self.listing_owner_id)
        self.stop()
        if success:
            await interaction.response.edit_message(
                content="Trade accepted! Cards have been exchanged.", view=None
            )
        else:
            msg = {
                "not_found": "This offer no longer exists.",
                "not_owner": "You are not the listing owner.",
                "listing_no_card": "Trade failed — you no longer have those listing cards.",
                "offer_no_card": "Trade failed — the proposer no longer has their offered cards.",
            }.get(reason or "", "Trade failed.")
            await interaction.response.edit_message(content=msg, view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if str(interaction.user.id) != self.listing_owner_id:
            await interaction.response.send_message(
                "Only the listing owner can decline.", ephemeral=True
            )
            return
        await decline_offer(self.offer_id, self.listing_owner_id)
        self.stop()
        await interaction.response.edit_message(content="Offer declined.", view=None)

    async def on_timeout(self) -> None:
        await expire_offer(self.offer_id)
        if self.message:
            try:
                await self.message.edit(content="Offer expired.", view=None)
            except discord.NotFound:
                pass
```

Also add the constant after `TRADE_EXPIRY_MINUTES`:

```python
TRADE_OFFER_EXPIRY_HOURS = 24
```

(Import it from service or define it here — defining it in bot.py is fine since it controls the View timeout.)

- [ ] **Step 3: Add `notify_trade_offer` and `edit_offer_dm` functions to bot.py**

Add as module-level async functions (outside any class, near the other helper functions):

```python
async def notify_trade_offer(offer_id: int) -> None:
    """DM the listing owner about a new marketplace offer."""
    offer = await get_offer_by_id(offer_id)
    if offer is None:
        return
    guild = bot.get_guild(int(superpal_env.GUILD_ID))
    if guild is None:
        return
    member = guild.get_member(int(offer.listing.owner_id))
    if member is None:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        offer_names = []
        for item in offer.items:
            async with db.execute(
                "SELECT display_name FROM members WHERE discord_id = ?", (item.member_id,)
            ) as cur:
                row = await cur.fetchone()
            offer_names.append(f"{RARITY_LABELS[item.rarity]} {row[0] if row else item.member_id}")
        listing_names = []
        for item in offer.listing.items:
            async with db.execute(
                "SELECT display_name FROM members WHERE discord_id = ?", (item.member_id,)
            ) as cur:
                row = await cur.fetchone()
            listing_names.append(f"{RARITY_LABELS[item.rarity]} {row[0] if row else item.member_id}")
    view = TradeOfferView(offer_id=offer_id, listing_owner_id=offer.listing.owner_id)
    content = (
        f"**{offer.proposer_display_name}** made an offer on your listing!\n\n"
        f"Your listing: {', '.join(listing_names)}\n"
        f"Their offer: {', '.join(offer_names)}\n\n"
        f"View in marketplace: {WEBAPP_BASE_URL}/marketplace"
    )
    try:
        dm = await member.send(content=content, view=view)
        view.message = dm
        await set_offer_discord_message_id(offer_id, str(dm.id))
    except discord.Forbidden:
        pass


async def edit_offer_dm(offer_id: int, message: str) -> None:
    """Edit the DM notification for an offer after web-UI accept/decline."""
    offer = await get_offer_by_id(offer_id)
    if offer is None:
        return
    # Retrieve discord_message_id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_message_id, proposer_id FROM trade_offers WHERE id = ?", (offer_id,)
        ) as cur:
            row = await cur.fetchone()
    if not row or not row[0]:
        return
    discord_message_id, proposer_id = row
    guild = bot.get_guild(int(superpal_env.GUILD_ID))
    if guild is None:
        return
    owner_member = guild.get_member(int(offer.listing.owner_id))
    if owner_member is None:
        return
    try:
        dm_channel = await owner_member.create_dm()
        msg = await dm_channel.fetch_message(int(discord_message_id))
        await msg.edit(content=message, view=None)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass
```

- [ ] **Step 4: Update the `/card-trade` command**

Find the existing `propose_trade_command` function (around line 681) and replace the entire command + handler with:

```python
@bot.tree.command(
    name="card-trade",
    description="Open the trade marketplace to list cards and make offers",
)
async def propose_trade_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    url = await generate_magic_link(
        user_id=str(interaction.user.id),
        link_type="collection",
        base_url=WEBAPP_BASE_URL,
    )
    try:
        await interaction.user.send(
            f"Open this link to access the trade marketplace (valid 24 hours after first click):\n{url}\n\n"
            "Once open, click **Marketplace** in the top nav to browse listings and make offers. "
            "Right-click any card in your collection to list it for trade."
        )
        await interaction.followup.send("Check your DMs for your marketplace link!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            f"Here's your marketplace link (enable DMs to receive these privately):\n{url}",
            ephemeral=True,
        )
```

- [ ] **Step 5: Remove now-unused imports from bot.py**

The `TRADE_EXPIRY_MINUTES` import and `create_trade_offer` / `execute_trade` / `decline_trade` are still used by the existing `TradeView` class — leave them in place.

Remove the `@discord.app_commands.describe` and `@discord.app_commands.choices` decorators that were on the old `/card-trade` command parameters. They are already gone once you replaced the command in Step 4.

- [ ] **Step 6: Verify bot.py syntax**

```bash
cd src && ../.venv/bin/python -c "
import ast, pathlib
src = pathlib.Path('bot.py').read_text()
ast.parse(src)
print('bot.py syntax OK')
"
```

Expected: `bot.py syntax OK`

- [ ] **Step 7: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/bot.py
git commit -m "feat: update /card-trade command and add Discord trade offer notifications"
```

---

## Verification Checklist

After all tasks complete:

- [ ] `pytest tests/ -q` — green
- [ ] Start bot locally (`cd src && ../.venv/bin/python bot.py`) — no import errors on startup
- [ ] Open `/my-collection` magic link → right-click a card → "List for Trade" appears → submit → card shows 🏪 badge
- [ ] Open `/marketplace` in a second session → listing appears → click "Make Offer" → select a card → Submit → redirects back
- [ ] `/card-trade` Discord command → sends ephemeral message with marketplace link
- [ ] Accept an offer via web → card quantities update for both players
