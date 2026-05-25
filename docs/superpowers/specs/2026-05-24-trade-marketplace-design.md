# Trade Marketplace Design

**Date:** 2026-05-24  
**Status:** Approved

## Context

The existing trade system is Discord-only: `/card-trade` prompts the user for five parameters (recipient, offer card, offer rarity, request card, request rarity), posts a channel message with Accept/Decline buttons, and expires in 10 minutes. It only supports 1-for-1 same-session trades. There is no way to browse what other players are willing to trade, no way to list cards passively, and no multi-card bundles.

The goal is to make trading a first-class social feature: players list cards they want to trade on their profile, browse a global marketplace, and send flexible bundle offers — all from the web UI. Discord remains the notification layer, not the primary interface.

---

## Data Model

Four new tables added to `src/superpal/cards/db.py` via `init_db()` as idempotent `CREATE TABLE IF NOT EXISTS` blocks. `pending_trades` is kept as-is for historical rows; no new rows are written to it.

### `trade_listings`
A player's offer of one or more cards, optionally with a stated ask.

```sql
CREATE TABLE IF NOT EXISTS trade_listings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    TEXT NOT NULL REFERENCES members(discord_id),
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'cancelled', 'completed')),
    ask_note    TEXT,  -- nullable free-text e.g. "want a Rare"
    created_at  TIMESTAMP NOT NULL
);
```

### `trade_listing_items`
Individual cards bundled in a listing (one row per card slot).

```sql
CREATE TABLE IF NOT EXISTS trade_listing_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id     INTEGER NOT NULL REFERENCES trade_listings(id),
    card_member_id TEXT NOT NULL REFERENCES members(discord_id),
    rarity         TEXT NOT NULL CHECK(rarity IN ('common','uncommon','rare','legendary'))
);
```

### `trade_offers`
A counter-offer made against a listing.

```sql
CREATE TABLE IF NOT EXISTS trade_offers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id          INTEGER NOT NULL REFERENCES trade_listings(id),
    proposer_id         TEXT NOT NULL REFERENCES members(discord_id),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','accepted','declined','expired','cancelled')),
    created_at          TIMESTAMP NOT NULL,
    expires_at          TIMESTAMP NOT NULL,  -- 24h default
    discord_message_id  TEXT  -- nullable; set after DM is sent so web UI can edit it
);
```

### `trade_offer_items`
Individual cards bundled in an offer (one row per card slot).

```sql
CREATE TABLE IF NOT EXISTS trade_offer_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id       INTEGER NOT NULL REFERENCES trade_offers(id),
    card_member_id TEXT NOT NULL REFERENCES members(discord_id),
    rarity         TEXT NOT NULL CHECK(rarity IN ('common','uncommon','rare','legendary'))
);
```

---

## New Model Dataclasses

Added to `src/superpal/cards/models.py`:

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
    items: list[CardRef]        # cards being offered
    offer_count: int            # pending offer count

@dataclass
class TradeOfferFull:
    id: int
    listing_id: int
    proposer_id: str
    proposer_display_name: str
    status: str
    created_at: str
    expires_at: str
    items: list[CardRef]        # cards proposer is offering
    listing: TradeListingFull   # the listing this is against
```

---

## Service Layer

New functions in `src/superpal/cards/service.py`:

### Listings
- `create_listing(owner_id: str, items: list[CardRef], ask_note: str | None) -> TradeListingFull | str`  
  Validates owner holds all listed cards (quantity ≥ 1 each). Returns listing or error key: `"no_card"`, `"empty_items"`.

- `cancel_listing(listing_id: int, owner_id: str) -> bool`  
  Marks listing `cancelled`. Returns False if not found or caller isn't owner.

- `get_active_listings(exclude_owner_id: str | None = None) -> list[TradeListingFull]`  
  All active listings, newest first. Excludes the caller's own if `exclude_owner_id` supplied.

- `get_player_listings(player_id: str) -> list[TradeListingFull]`  
  Active listings for one player (for sidebar profile section).

### Offers
- `create_offer(listing_id: int, proposer_id: str, items: list[CardRef]) -> TradeOfferFull | str`  
  Validates listing is active, proposer isn't the listing owner, proposer holds all offered cards, no existing pending offer from same proposer on this listing. Returns offer or error key: `"not_found"`, `"self_offer"`, `"no_card"`, `"duplicate_offer"`.

- `accept_offer(offer_id: int, recipient_id: str) -> tuple[bool, str | None]`  
  Validates recipient is listing owner, both sides still hold their cards. Atomically swaps all cards (listing items → proposer, offer items → owner). Marks listing `completed`, offer `accepted`, all other pending offers on listing `declined`. Error keys: `"not_found"`, `"not_owner"`, `"listing_no_card"`, `"offer_no_card"`.

- `decline_offer(offer_id: int, recipient_id: str) -> bool`

- `cancel_offer(offer_id: int, proposer_id: str) -> bool`

- `get_offers_for_listing(listing_id: int) -> list[TradeOfferFull]`  
  All pending offers on a listing (shown to listing owner).

- `get_my_offers(user_id: str) -> list[TradeOfferFull]`  
  Pending offers the user has sent.

---

## Webapp Routes

All routes require a valid `bringus_session` cookie (same auth as `/collection`).

| Method | Path | Action |
|--------|------|--------|
| GET | `/marketplace` | Render `marketplace.html` |
| POST | `/marketplace/listing` | Create listing, redirect to `/marketplace` |
| POST | `/marketplace/listing/{id}/cancel` | Cancel listing, redirect to `/marketplace` |
| POST | `/marketplace/listing/{id}/offer` | Submit offer, redirect to `/marketplace` |
| POST | `/marketplace/offer/{id}/accept` | Accept offer, redirect to `/marketplace` |
| POST | `/marketplace/offer/{id}/decline` | Decline offer, redirect to `/marketplace` |
| POST | `/marketplace/offer/{id}/cancel` | Cancel own offer, redirect to `/marketplace` |

---

## Templates

### `marketplace.html` (new)
- Nav tabs matching the Discord dark theme: **My Collection** | **Marketplace** | **Offers (N)**
- Main area: grid of active listings from other players. Each listing card shows:
  - Avatar(s) of cards being offered, rarity badges, owner name
  - `ask_note` if set, otherwise "Open to offers"
  - **Make Offer** button → opens offer modal
- Offer modal (inline): shows the listing at top, then a compact grid of the logged-in user's cards to select as the offer bundle. Submit POSTs to `/marketplace/listing/{id}/offer`.
- Right sidebar:
  - **My Listings** section: own active listings with Cancel button
  - **Active Traders** list: players with listings, with card count
- Offers tab: sent offers with status badges

### `collection.html` (modified)
- Right-click context menu on each owned card in the grid:
  - "List for Trade" → opens a small inline form (ask_note input + confirm) that POSTs to `/marketplace/listing`
  - "Remove Listing" (only shown if card is already in an active listing) → POSTs to cancel
- Cards in an active listing get a `🏪` badge overlay (small, top-right of card tile)
- Implemented in vanilla JS — no new library

---

## Bot Changes

### `/card-trade` command (`src/bot.py`)
- Remove all existing parameters
- New behavior: call `generate_magic_link(user_id)`, send ephemeral reply:  
  `"Head to the marketplace to create a trade: {WEBAPP_BASE_URL}/link/{token}"`

### Trade offer notifications
- After `POST /marketplace/listing/{id}/offer` succeeds, route calls `bot.notify_trade_offer(listing_id, offer_id)` — an async method on the bot instance (same pattern as fight DM notifications)
- `notify_trade_offer` DMs the listing owner with offer details and a `TradeOfferView`
- `TradeOfferView(offer_id, listing_owner_id)`:
  - **Accept** button → calls `accept_offer()`, edits message to confirm
  - **Decline** button → calls `decline_offer()`, edits message to confirm
  - `on_timeout` (24h) → calls `cancel_offer()`, edits message to "Offer expired"
- When listing owner accepts/declines via web UI: the route edits the Discord DM message to reflect the updated status using `discord_message_id` stored on the offer row

---

## Migration

- `init_db()` gains four `CREATE TABLE IF NOT EXISTS` blocks — idempotent, safe to deploy alongside existing DB
- `pending_trades` untouched; `TradeView` / `create_trade_offer` / `execute_trade` / `decline_trade` remain for in-flight Discord trades
- Add `.superpowers/` to `.gitignore`

---

## Verification

1. **Existing tests pass:** `pytest tests/cards/ -q`
2. **New unit tests** in `tests/cards/test_trade_service.py`:
   - `create_listing` rejects if owner doesn't hold the card
   - `accept_offer` atomically swaps cards and declines sibling offers
   - `accept_offer` fails when either party no longer holds their cards
   - `cancel_listing` rejects if caller isn't the owner
   - `create_offer` rejects self-offers and duplicate pending offers
3. **Manual web flow:** `/card-collection` → right-click card → list it → `/marketplace` in second session → make offer → accept via web → verify quantities in both collections
4. **Manual Discord flow:** submit offer via web → confirm DM arrives → click Accept in Discord → verify card swap
