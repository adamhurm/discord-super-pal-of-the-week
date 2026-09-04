import aiosqlite
import pytest


@pytest.fixture
async def tmp_db(db_mods):
    db_mod, *_ = db_mods
    return db_mod


@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_db):
    await tmp_db.init_db()
    async with aiosqlite.connect(tmp_db.DB_PATH) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            tables = {row[0] for row in await cur.fetchall()}
    expected = {
        "members",
        "user_cards",
        "draw_log",
        "magic_links",
        "trade_listings",
        "trade_offers",
    }
    assert expected.issubset(tables)


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_db):
    await tmp_db.init_db()
    await tmp_db.init_db()  # second call must not raise


_OLD_TRADE_OFFERS_SCHEMA = """
CREATE TABLE trade_offers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id          INTEGER NOT NULL REFERENCES trade_listings(id),
    proposer_id         TEXT NOT NULL REFERENCES members(discord_id),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','accepted','declined','expired','cancelled')),
    created_at          TIMESTAMP NOT NULL,
    expires_at          TIMESTAMP NOT NULL,
    discord_message_id  TEXT
);
CREATE TABLE trade_offer_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id       INTEGER NOT NULL REFERENCES trade_offers(id),
    card_member_id TEXT NOT NULL REFERENCES members(discord_id),
    rarity         TEXT NOT NULL CHECK(rarity IN ('common','uncommon','rare','legendary'))
);
CREATE TABLE trade_listings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    TEXT NOT NULL REFERENCES members(discord_id),
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'cancelled', 'completed')),
    ask_note    TEXT,
    created_at  TIMESTAMP NOT NULL
);
CREATE TABLE trade_listing_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id     INTEGER NOT NULL REFERENCES trade_listings(id),
    card_member_id TEXT NOT NULL REFERENCES members(discord_id),
    rarity         TEXT NOT NULL CHECK(rarity IN ('common','uncommon','rare','legendary'))
);
"""


async def _seed_pre_migration_offer(db_path: str) -> None:
    """Build a database in the pre-direct-trade shape with one pending offer."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_OLD_TRADE_OFFERS_SCHEMA)
        await db.execute(
            "INSERT INTO trade_listings (id, owner_id, status, ask_note, created_at) "
            "VALUES (1, 'owner1', 'active', 'want legendaries', '2026-01-01T00:00:00+00:00')"
        )
        await db.execute(
            "INSERT INTO trade_listing_items (listing_id, card_member_id, rarity) "
            "VALUES (1, 'cardA', 'rare')"
        )
        await db.execute(
            "INSERT INTO trade_offers "
            "(id, listing_id, proposer_id, status, created_at, expires_at, discord_message_id) "
            "VALUES (7, 1, 'proposer1', 'pending', '2026-01-01T00:00:00+00:00', "
            "'2026-01-02T00:00:00+00:00', '999')"
        )
        await db.execute(
            "INSERT INTO trade_offer_items (offer_id, card_member_id, rarity) "
            "VALUES (7, 'cardB', 'common')"
        )
        await db.commit()


@pytest.mark.asyncio
async def test_migration_backfills_recipient_from_listing_owner(tmp_db):
    await _seed_pre_migration_offer(tmp_db.DB_PATH)
    await tmp_db.init_db()
    async with aiosqlite.connect(tmp_db.DB_PATH) as db:
        async with db.execute(
            "SELECT listing_id, proposer_id, recipient_id, counter_of_id, status, "
            "discord_message_id FROM trade_offers WHERE id = 7"
        ) as cur:
            row = await cur.fetchone()
    assert row == (1, "proposer1", "owner1", None, "pending", "999")


@pytest.mark.asyncio
async def test_migration_snapshots_listing_items_as_get_side(tmp_db):
    await _seed_pre_migration_offer(tmp_db.DB_PATH)
    await tmp_db.init_db()
    async with aiosqlite.connect(tmp_db.DB_PATH) as db:
        async with db.execute(
            "SELECT side, card_member_id, rarity FROM trade_offer_items "
            "WHERE offer_id = 7 ORDER BY side"
        ) as cur:
            rows = await cur.fetchall()
    assert rows == [("get", "cardA", "rare"), ("give", "cardB", "common")]


async def _count(db, table: str) -> int:
    async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
        row = await cur.fetchone()
    assert row is not None
    return row[0]


@pytest.mark.asyncio
async def test_migration_is_idempotent(tmp_db):
    await _seed_pre_migration_offer(tmp_db.DB_PATH)
    await tmp_db.init_db()
    await tmp_db.init_db()
    async with aiosqlite.connect(tmp_db.DB_PATH) as db:
        counts = (await _count(db, "trade_offer_items"), await _count(db, "trade_offers"))
    assert counts == (2, 1)


@pytest.mark.asyncio
async def test_direct_trade_offer_allows_null_listing(tmp_db):
    await tmp_db.init_db()
    async with aiosqlite.connect(tmp_db.DB_PATH) as db:
        await db.execute(
            "INSERT INTO trade_offers "
            "(listing_id, proposer_id, recipient_id, status, created_at, expires_at) "
            "VALUES (NULL, 'a', 'b', 'pending', '2026-01-01T00:00:00+00:00', "
            "'2026-01-02T00:00:00+00:00')"
        )
        await db.commit()
        async with db.execute("SELECT COUNT(*) FROM trade_offers WHERE listing_id IS NULL") as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1
