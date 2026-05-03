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
    request_rarity    TEXT NOT NULL CHECK(request_rarity IN ('common','uncommon','rare','legendary')),
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending','accepted','declined','expired')),
    created_at        TIMESTAMP NOT NULL,
    expires_at        TIMESTAMP NOT NULL
);
"""


async def init_db() -> None:
    """Create all tables if they don't already exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
