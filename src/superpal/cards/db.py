import os

import aiosqlite

DB_PATH: str = os.getenv("CARDS_DB_PATH", "cards.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    discord_id   TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    avatar_url   TEXT,
    is_excluded  BOOLEAN NOT NULL DEFAULT 0,
    synced_at    TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS fights (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    mode                   TEXT NOT NULL,
    challenger_id          TEXT NOT NULL REFERENCES members(discord_id),
    opponent_id            TEXT NOT NULL REFERENCES members(discord_id),
    status                 TEXT NOT NULL DEFAULT 'pending',
    winner_id              TEXT REFERENCES members(discord_id),
    current_turn_player_id TEXT REFERENCES members(discord_id),
    pending_swap_player_id TEXT REFERENCES members(discord_id),
    channel_id             TEXT,
    challenger_ready       INTEGER NOT NULL DEFAULT 0,
    opponent_ready         INTEGER NOT NULL DEFAULT 0,
    challenger_atk_boost   INTEGER NOT NULL DEFAULT 0,
    opponent_atk_boost     INTEGER NOT NULL DEFAULT 0,
    challenger_smoked      INTEGER NOT NULL DEFAULT 0,
    opponent_smoked        INTEGER NOT NULL DEFAULT 0,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at             TIMESTAMP,
    completed_at           TIMESTAMP,
    expires_at             TIMESTAMP,
    last_activity_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    turn_started_at        TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fight_cards (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fight_id       INTEGER NOT NULL REFERENCES fights(id),
    player_id      TEXT NOT NULL REFERENCES members(discord_id),
    card_member_id TEXT NOT NULL REFERENCES members(discord_id),
    rarity         TEXT NOT NULL,
    slot           INTEGER NOT NULL,
    hp_current     INTEGER NOT NULL,
    hp_max         INTEGER NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 0,
    is_fainted     INTEGER NOT NULL DEFAULT 0,
    UNIQUE(fight_id, player_id, slot)
);

CREATE TABLE IF NOT EXISTS fight_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    fight_id       INTEGER NOT NULL REFERENCES fights(id),
    actor_id       TEXT REFERENCES members(discord_id),
    action_type    TEXT NOT NULL,
    action_detail  TEXT,
    d20_roll       INTEGER,
    damage_dealt   INTEGER,
    narrative_text TEXT NOT NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_items (
    player_id  TEXT NOT NULL REFERENCES members(discord_id),
    item_type  TEXT NOT NULL,
    quantity   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, item_type)
);

CREATE TABLE IF NOT EXISTS fight_tokens (
    token         TEXT PRIMARY KEY,
    fight_id      INTEGER NOT NULL REFERENCES fights(id),
    player_id     TEXT NOT NULL REFERENCES members(discord_id),
    created_at    TIMESTAMP NOT NULL,
    expires_at    TIMESTAMP NOT NULL,
    session_token TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    scope      TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS user_cards (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id          TEXT NOT NULL REFERENCES members(discord_id),
    card_member_id    TEXT NOT NULL REFERENCES members(discord_id),
    rarity            TEXT NOT NULL CHECK(rarity IN ('common','uncommon','rare','legendary')),
    quantity          INTEGER NOT NULL DEFAULT 1,
    first_acquired_at TIMESTAMP NOT NULL,
    UNIQUE(owner_id, card_member_id, rarity)
);

CREATE TABLE IF NOT EXISTS draw_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL REFERENCES members(discord_id),
    week_start TEXT NOT NULL,
    draws_used INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, week_start)
);

CREATE TABLE IF NOT EXISTS magic_links (
    token              TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    link_type          TEXT NOT NULL CHECK(link_type IN ('collection','admin')),
    created_at         TIMESTAMP NOT NULL,
    consumed_at        TIMESTAMP,
    session_token      TEXT,
    session_expires_at TIMESTAMP
);

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

"""

# Shared with the trade_offers rebuild in _migrate_trade_offers(), which recreates the
# table rather than altering it: listing_id had to become nullable (direct trades have no
# listing) and the status CHECK had to gain 'countered'.
_TRADE_OFFERS_DDL = """
CREATE TABLE IF NOT EXISTS trade_offers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id          INTEGER REFERENCES trade_listings(id),
    proposer_id         TEXT NOT NULL REFERENCES members(discord_id),
    recipient_id        TEXT NOT NULL REFERENCES members(discord_id),
    counter_of_id       INTEGER REFERENCES trade_offers(id),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','accepted','declined','expired',
                                         'cancelled','countered')),
    created_at          TIMESTAMP NOT NULL,
    expires_at          TIMESTAMP NOT NULL,
    discord_message_id  TEXT
);
"""

_TRADE_OFFER_ITEMS_DDL = """
CREATE TABLE IF NOT EXISTS trade_offer_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id       INTEGER NOT NULL REFERENCES trade_offers(id),
    card_member_id TEXT NOT NULL REFERENCES members(discord_id),
    rarity         TEXT NOT NULL CHECK(rarity IN ('common','uncommon','rare','legendary')),
    side           TEXT NOT NULL DEFAULT 'give' CHECK(side IN ('give','get'))
);
"""

_SCHEMA = _SCHEMA + _TRADE_OFFERS_DDL + _TRADE_OFFER_ITEMS_DDL


async def _migrate_trade_offers(db: aiosqlite.Connection) -> None:
    """Rebuild trade_offers in the direct-trade shape, preserving existing offers.

    SQLite cannot relax a NOT NULL or widen a CHECK in place, so the table is recreated
    and copied. recipient_id is backfilled from the listing owner, which is who could
    accept an offer under the listing-only model.
    """
    async with db.execute("PRAGMA table_info(trade_offers)") as cur:
        columns = {row[1] for row in await cur.fetchall()}
    if not columns or "recipient_id" in columns:
        return
    await db.execute("DROP TABLE IF EXISTS trade_offers_old")
    await db.execute("ALTER TABLE trade_offers RENAME TO trade_offers_old")
    await db.execute(_TRADE_OFFERS_DDL)
    await db.execute(
        "INSERT INTO trade_offers (id, listing_id, proposer_id, recipient_id, counter_of_id, "
        "status, created_at, expires_at, discord_message_id) "
        "SELECT o.id, o.listing_id, o.proposer_id, tl.owner_id, NULL, o.status, o.created_at, "
        "o.expires_at, o.discord_message_id "
        "FROM trade_offers_old o JOIN trade_listings tl ON o.listing_id = tl.id"
    )
    await db.execute("DROP TABLE trade_offers_old")
    await db.commit()


async def _backfill_offer_get_side(db: aiosqlite.Connection) -> None:
    """Snapshot each pending listing's items onto its offers as the 'get' side.

    Offers used to imply what the proposer receives by joining back to the listing;
    they now carry both sides themselves. NOT EXISTS makes the backfill idempotent.
    """
    await db.execute(
        "INSERT INTO trade_offer_items (offer_id, card_member_id, rarity, side) "
        "SELECT o.id, tli.card_member_id, tli.rarity, 'get' "
        "FROM trade_offers o "
        "JOIN trade_listing_items tli ON tli.listing_id = o.listing_id "
        "WHERE o.status = 'pending' AND NOT EXISTS ("
        "  SELECT 1 FROM trade_offer_items i WHERE i.offer_id = o.id AND i.side = 'get')"
    )
    await db.commit()


async def init_db() -> None:
    """Create all tables if they don't already exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL lets reads and writes proceed concurrently instead of serializing
        # on one exclusive lock per write — without it, concurrent card draws
        # and admin bulk-awards contend for the same lock and can time out.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(_SCHEMA)
        await db.commit()
        # The DM-based pending_trades flow was replaced by the marketplace
        # (trade_listings/trade_offers); drop the orphaned table.
        await db.execute("DROP TABLE IF EXISTS pending_trades")
        await db.commit()
        # fight_sessions was folded into the unified sessions table.
        await db.execute("DROP TABLE IF EXISTS fight_sessions")
        await db.commit()
        try:
            await db.execute(
                "ALTER TABLE members ADD COLUMN forced_rarity TEXT "
                "CHECK(forced_rarity IN ('common','uncommon','rare','legendary'))"
            )
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute(
                "ALTER TABLE members ADD COLUMN is_synthetic BOOLEAN NOT NULL DEFAULT 0"
            )
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute("ALTER TABLE user_cards ADD COLUMN drawn_by_name TEXT")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute("ALTER TABLE members ADD COLUMN bio TEXT")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute("ALTER TABLE members ADD COLUMN stats TEXT")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute("ALTER TABLE members ADD COLUMN pringle_balance INTEGER DEFAULT 0")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute("ALTER TABLE members ADD COLUMN bank_debt INTEGER DEFAULT 0")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute("ALTER TABLE fight_tokens ADD COLUMN session_token TEXT")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute("ALTER TABLE members ADD COLUMN palycoin_balance INTEGER DEFAULT 0")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute("ALTER TABLE members ADD COLUMN boin_balance INTEGER DEFAULT 0")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute("ALTER TABLE fights ADD COLUMN turn_started_at TIMESTAMP")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        try:
            await db.execute(
                "ALTER TABLE trade_offer_items ADD COLUMN side TEXT NOT NULL "
                "DEFAULT 'give' CHECK(side IN ('give','get'))"
            )
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # column already exists
        await _migrate_trade_offers(db)
        await _backfill_offer_get_side(db)
        await db.execute(
            """CREATE TABLE IF NOT EXISTS markets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    description   TEXT,
    created_by    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending_approval'
                  CHECK(status IN ('pending_approval','open','closed','resolved','rejected')),
    outcome       TEXT CHECK(outcome IN ('yes','no')),
    yes_pool      INTEGER NOT NULL DEFAULT 0,
    no_pool       INTEGER NOT NULL DEFAULT 0,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at   DATETIME,
    resolved_by   TEXT
)"""
        )
        await db.commit()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS market_bets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   INTEGER NOT NULL REFERENCES markets(id),
    player_id   TEXT NOT NULL,
    side        TEXT NOT NULL CHECK(side IN ('yes','no')),
    amount      INTEGER NOT NULL,
    placed_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market_id, player_id)
)"""
        )
        await db.commit()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS market_probability_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   INTEGER NOT NULL REFERENCES markets(id),
    yes_pct     REAL NOT NULL,
    yes_pool    INTEGER NOT NULL,
    no_pool     INTEGER NOT NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""
        )
        await db.commit()
