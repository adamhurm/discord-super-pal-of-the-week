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
    last_activity_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

CREATE TABLE IF NOT EXISTS fight_sessions (
    session_token TEXT PRIMARY KEY,
    fight_id      INTEGER NOT NULL REFERENCES fights(id),
    player_id     TEXT NOT NULL REFERENCES members(discord_id),
    expires_at    TIMESTAMP NOT NULL
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

CREATE TABLE IF NOT EXISTS pending_trades (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    proposer_id       TEXT NOT NULL REFERENCES members(discord_id),
    recipient_id      TEXT NOT NULL REFERENCES members(discord_id),
    offer_member_id   TEXT NOT NULL REFERENCES members(discord_id),
    offer_rarity      TEXT NOT NULL CHECK(offer_rarity IN ('common','uncommon','rare','legendary')),
    request_member_id TEXT NOT NULL REFERENCES members(discord_id),
    request_rarity    TEXT NOT NULL
                      CHECK(request_rarity IN ('common','uncommon','rare','legendary')),
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending','accepted','declined','expired')),
    created_at        TIMESTAMP NOT NULL,
    expires_at        TIMESTAMP NOT NULL
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
"""


async def init_db() -> None:
    """Create all tables if they don't already exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
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
