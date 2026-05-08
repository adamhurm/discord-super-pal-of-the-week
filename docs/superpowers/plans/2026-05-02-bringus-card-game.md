# Bringus Card Game Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a collectible card game to the Bringus Discord bot — members draw weekly cards from the server roster, trade duplicates, and view their collection via a one-time magic link webapp.

**Architecture:** Single Python process: the existing discord.py bot and a new FastAPI webapp run together using `asyncio.gather`. All state lives in a single SQLite file. New code lives in two new modules — `src/superpal/cards/` (game logic) and `src/superpal/webapp/` (HTTP layer) — keeping `bot.py` as the command entrypoint.

**Tech Stack:** Python 3.14, discord.py 2.4.x, FastAPI, uvicorn, aiosqlite, Jinja2, existing pytest/pytest-asyncio test suite.

---

## File Map

**Create:**
- `src/superpal/cards/__init__.py`
- `src/superpal/cards/db.py` — SQLite init and `DB_PATH` constant
- `src/superpal/cards/models.py` — dataclasses: `Member`, `UserCard`, `DrawLog`, `MagicLink`; rarity constants
- `src/superpal/cards/service.py` — `draw_card()`, `trade_in()`, `upgrade()`, `sync_members()`, `generate_magic_link()`, `consume_magic_link()`
- `src/superpal/cards/embeds.py` — `build_card_embed()` returning a `discord.Embed`
- `src/superpal/webapp/__init__.py`
- `src/superpal/webapp/app.py` — `create_app()` FastAPI factory
- `src/superpal/webapp/auth.py` — session cookie helpers: `set_session()`, `get_session()`
- `src/superpal/webapp/routes.py` — all HTTP route handlers
- `src/superpal/webapp/templates/collection.html`
- `src/superpal/webapp/templates/admin.html`
- `src/superpal/webapp/templates/expired.html`
- `tests/cards/test_db.py`
- `tests/cards/test_service.py`
- `tests/cards/test_embeds.py`
- `tests/webapp/test_auth.py`
- `tests/webapp/test_routes.py`
- `tests/cards/__init__.py`
- `tests/webapp/__init__.py`

**Modify:**
- `requirements.txt` — add `fastapi`, `uvicorn[standard]`, `aiosqlite`, `jinja2`, `python-multipart`
- `src/bot.py` — add card slash commands; change `bot.run()` to `asyncio.run(main())` pattern
- `src/superpal/env.py` — add `WEBAPP_HOST`, `WEBAPP_PORT`, `CARDS_DB_PATH` env vars
- `k8s/deploy-super-pal.yaml` — add `WEBAPP_PORT` env var and expose port

---

## Task 1: Add dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add new packages**

Open `requirements.txt` and append these lines (pin to current stable at time of writing — look up exact versions with `pip index versions <pkg>` if needed):

```
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
aiosqlite>=0.20.0
jinja2>=3.1.4
python-multipart>=0.0.12
```

- [ ] **Step 2: Install and verify**

```bash
pip install -r requirements.txt
python -c "import fastapi, uvicorn, aiosqlite, jinja2; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat(cards): add fastapi, uvicorn, aiosqlite, jinja2 dependencies"
```

---

## Task 2: Database module

**Files:**
- Create: `src/superpal/cards/__init__.py`
- Create: `src/superpal/cards/db.py`
- Create: `tests/cards/__init__.py`
- Create: `tests/cards/test_db.py`

- [ ] **Step 1: Create package init files**

`src/superpal/cards/__init__.py` — empty file.
`tests/cards/__init__.py` — empty file.

- [ ] **Step 2: Write the failing test**

Create `tests/cards/test_db.py`:

```python
import asyncio
import os
import pytest
import aiosqlite
from superpal.cards.db import init_db, DB_PATH


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_cards.db")
    monkeypatch.setenv("CARDS_DB_PATH", db_file)
    # Re-import to pick up patched env var
    import importlib
    import superpal.cards.db as db_mod
    importlib.reload(db_mod)
    return db_mod


@pytest.mark.asyncio
async def test_init_db_creates_tables(tmp_db):
    await tmp_db.init_db()
    async with aiosqlite.connect(tmp_db.DB_PATH) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
    assert {"members", "user_cards", "draw_log", "magic_links"}.issubset(tables)


@pytest.mark.asyncio
async def test_init_db_is_idempotent(tmp_db):
    await tmp_db.init_db()
    await tmp_db.init_db()  # second call must not raise
```

- [ ] **Step 3: Run test to verify it fails**

```bash
pytest tests/cards/test_db.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` (module doesn't exist yet).

- [ ] **Step 4: Create `src/superpal/cards/db.py`**

```python
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
"""


async def init_db() -> None:
    """Create all tables if they don't already exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/cards/test_db.py -v
```

Expected: 2 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/superpal/cards/__init__.py src/superpal/cards/db.py \
        tests/cards/__init__.py tests/cards/test_db.py
git commit -m "feat(cards): add SQLite schema init module"
```

---

## Task 3: Data models and rarity constants

**Files:**
- Create: `src/superpal/cards/models.py`

No separate test file — models are plain dataclasses validated through service tests.

- [ ] **Step 1: Create `src/superpal/cards/models.py`**

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


RARITY_ORDER: list[str] = ["common", "uncommon", "rare", "legendary"]

RARITY_WEIGHTS: dict[str, int] = {
    "common": 60,
    "uncommon": 25,
    "rare": 12,
    "legendary": 3,
}

RARITY_COLORS: dict[str, int] = {
    "common": 0x95A5A6,
    "uncommon": 0x27AE60,
    "rare": 0x2980B9,
    "legendary": 0xF39C12,
}

RARITY_LABELS: dict[str, str] = {
    "common": "COMMON",
    "uncommon": "UNCOMMON",
    "rare": "RARE",
    "legendary": "LEGENDARY",
}


@dataclass
class Member:
    discord_id: str
    display_name: str
    avatar_url: Optional[str]
    is_excluded: bool
    synced_at: datetime


@dataclass
class UserCard:
    id: int
    owner_id: str
    card_member_id: str
    rarity: str
    quantity: int
    first_acquired_at: datetime


@dataclass
class DrawLog:
    user_id: str
    week_start: str
    draws_used: int


@dataclass
class MagicLink:
    token: str
    user_id: str
    link_type: str
    created_at: datetime
    consumed_at: Optional[datetime]
    session_token: Optional[str]
    session_expires_at: Optional[datetime]
```

- [ ] **Step 2: Verify import**

```bash
python -c "from superpal.cards.models import Member, UserCard, RARITY_WEIGHTS; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/superpal/cards/models.py
git commit -m "feat(cards): add data models and rarity constants"
```

---

## Task 4: Card service — member sync and draw logic

**Files:**
- Create: `src/superpal/cards/service.py`
- Create: `tests/cards/test_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/cards/test_service.py`:

```python
import pytest
import aiosqlite
import importlib
from datetime import datetime, timezone


@pytest.fixture
async def db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("CARDS_DB_PATH", db_file)
    import superpal.cards.db as db_mod
    import superpal.cards.service as svc_mod
    importlib.reload(db_mod)
    importlib.reload(svc_mod)
    await db_mod.init_db()
    return db_mod, svc_mod


@pytest.mark.asyncio
async def test_sync_members_upserts(db):
    db_mod, svc = db
    members = [
        {"discord_id": "111", "display_name": "Alice", "avatar_url": "http://a.com/a.png"},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
    ]
    await svc.sync_members(members)
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute("SELECT discord_id FROM members ORDER BY discord_id") as cur:
            rows = await cur.fetchall()
    assert [r[0] for r in rows] == ["111", "222"]


@pytest.mark.asyncio
async def test_draw_card_returns_card(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
    ])
    card = await svc.draw_card(owner_id="111", max_draws=1)
    assert card is not None
    assert card.rarity in ("common", "uncommon", "rare", "legendary")
    assert card.owner_id == "111"
    assert card.quantity == 1


@pytest.mark.asyncio
async def test_draw_card_respects_weekly_limit(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    await svc.draw_card(owner_id="111", max_draws=1)
    second = await svc.draw_card(owner_id="111", max_draws=1)
    assert second is None


@pytest.mark.asyncio
async def test_draw_card_super_pal_gets_two(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    first = await svc.draw_card(owner_id="111", max_draws=2)
    second = await svc.draw_card(owner_id="111", max_draws=2)
    third = await svc.draw_card(owner_id="111", max_draws=2)
    assert first is not None
    assert second is not None
    assert third is None


@pytest.mark.asyncio
async def test_draw_card_excluded_member_not_in_pool(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Excluded", "avatar_url": None},
    ])
    await svc.set_excluded("222", excluded=True)
    # Draw many times; excluded member should never appear
    results = set()
    for _ in range(20):
        card = await svc.draw_card(owner_id="111", max_draws=99)
        if card:
            results.add(card.card_member_id)
    assert "222" not in results


@pytest.mark.asyncio
async def test_draw_card_increments_quantity_on_duplicate(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    # Force two draws of the same member+rarity by patching random
    import unittest.mock as mock
    import superpal.cards.service as svc_mod
    with mock.patch.object(svc_mod, "_roll_rarity", return_value="common"), \
         mock.patch("random.choice", return_value="111"):
        await svc.draw_card(owner_id="111", max_draws=2)
        card = await svc.draw_card(owner_id="111", max_draws=2)
    assert card is not None
    assert card.quantity == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/cards/test_service.py -v
```

Expected: `ImportError` (service.py doesn't exist yet).

- [ ] **Step 3: Create `src/superpal/cards/service.py`**

```python
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
    """Trade 3× [card_member_id, rarity] for a random card of the same rarity.
    Returns the new card, or None if insufficient duplicates or rarity not valid."""
    qty = await get_card_quantity(owner_id, card_member_id, rarity)
    if qty < 3:
        return None

    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        # Deduct 3
        await db.execute(
            "UPDATE user_cards SET quantity = quantity - 3 "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (owner_id, card_member_id, rarity),
        )
        # Remove rows with 0 quantity
        await db.execute(
            "DELETE FROM user_cards WHERE owner_id = ? AND quantity <= 0",
            (owner_id,),
        )

        # Pick a random eligible member
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
    """Trade 5× [card_member_id, rarity] for 1× same member at next rarity tier.
    Returns upgraded card, or None if insufficient copies, already Legendary, or invalid rarity."""
    if rarity == "legendary" or rarity not in RARITY_ORDER:
        return None

    qty = await get_card_quantity(owner_id, card_member_id, rarity)
    if qty < 5:
        return None

    next_rarity = RARITY_ORDER[RARITY_ORDER.index(rarity) + 1]
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
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
    """Return all cards for a user plus silhouettes for undiscovered members.

    Returns:
        {
            "owned": [{"member": Member, "rarity": str, "quantity": int}, ...],
            "undiscovered": [Member, ...],   # non-excluded members not yet in collection
            "counts": {"common": int, ...}
        }
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_id, display_name, avatar_url, is_excluded, synced_at "
            "FROM members WHERE is_excluded = 0"
        ) as cur:
            all_members = {
                r[0]: {"discord_id": r[0], "display_name": r[1], "avatar_url": r[2]}
                for r in await cur.fetchall()
            }

        async with db.execute(
            "SELECT uc.card_member_id, m.display_name, m.avatar_url, uc.rarity, uc.quantity "
            "FROM user_cards uc JOIN members m ON uc.card_member_id = m.discord_id "
            "WHERE uc.owner_id = ? ORDER BY uc.rarity, m.display_name",
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/cards/test_service.py -v
```

Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/superpal/cards/service.py tests/cards/test_service.py
git commit -m "feat(cards): add card service (draw, trade-in, upgrade, magic links)"
```

---

## Task 5: Trade-in and upgrade service tests

**Files:**
- Modify: `tests/cards/test_service.py`

- [ ] **Step 1: Append trade-in and upgrade tests**

Add to `tests/cards/test_service.py`:

```python
@pytest.mark.asyncio
async def test_trade_in_requires_three(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
    ])
    # Give owner 2 copies of Bob's common card
    import aiosqlite
    from datetime import datetime, timezone
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('111', '222', 'common', 2, ?)",
            (datetime.now(timezone.utc).isoformat(),)
        )
        await conn.commit()
    result = await svc.trade_in("111", "222", "common")
    assert result is None  # not enough


@pytest.mark.asyncio
async def test_trade_in_succeeds_with_three(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
    ])
    import aiosqlite
    from datetime import datetime, timezone
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('111', '222', 'common', 3, ?)",
            (datetime.now(timezone.utc).isoformat(),)
        )
        await conn.commit()
    result = await svc.trade_in("111", "222", "common")
    assert result is not None
    assert result.rarity == "common"
    assert result.owner_id == "111"
    # Source cards deducted
    remaining = await svc.get_card_quantity("111", "222", "common")
    assert remaining == 0


@pytest.mark.asyncio
async def test_upgrade_legendary_rejected(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    result = await svc.upgrade("111", "111", "legendary")
    assert result is None


@pytest.mark.asyncio
async def test_upgrade_requires_five(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    import aiosqlite
    from datetime import datetime, timezone
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('111', '111', 'common', 4, ?)",
            (datetime.now(timezone.utc).isoformat(),)
        )
        await conn.commit()
    result = await svc.upgrade("111", "111", "common")
    assert result is None


@pytest.mark.asyncio
async def test_upgrade_succeeds(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
    ])
    import aiosqlite
    from datetime import datetime, timezone
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at) "
            "VALUES ('111', '111', 'common', 5, ?)",
            (datetime.now(timezone.utc).isoformat(),)
        )
        await conn.commit()
    result = await svc.upgrade("111", "111", "common")
    assert result is not None
    assert result.rarity == "uncommon"
    assert result.card_member_id == "111"
    remaining = await svc.get_card_quantity("111", "111", "common")
    assert remaining == 0


@pytest.mark.asyncio
async def test_magic_link_consumed_once(db):
    db_mod, svc = db
    url = await svc.generate_magic_link("111", "collection", "http://localhost:8080")
    token = url.split("/")[-1]
    link1 = await svc.consume_magic_link(token)
    link2 = await svc.consume_magic_link(token)
    assert link1 is not None
    assert link1.session_token is not None
    assert link2 is None  # already consumed
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/cards/test_service.py -v
```

Expected: all PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/cards/test_service.py
git commit -m "test(cards): add trade-in, upgrade, and magic link tests"
```

---

## Task 6: Discord embed builder

**Files:**
- Create: `src/superpal/cards/embeds.py`
- Create: `tests/cards/test_embeds.py`

- [ ] **Step 1: Write failing test**

Create `tests/cards/test_embeds.py`:

```python
import discord
import pytest
from superpal.cards.embeds import build_card_embed
from superpal.cards.models import RARITY_COLORS, RARITY_LABELS


def test_build_card_embed_common():
    embed = build_card_embed(
        display_name="Bingus McFlop",
        avatar_url="https://cdn.discordapp.com/avatars/123/abc.png",
        rarity="common",
        card_number=7,
        drawn_by="DiscordUser",
    )
    assert isinstance(embed, discord.Embed)
    assert embed.color.value == RARITY_COLORS["common"]
    assert "COMMON" in embed.footer.text
    assert "#7" in embed.footer.text
    assert embed.author.name == "Bingus McFlop"


def test_build_card_embed_legendary():
    embed = build_card_embed(
        display_name="Dingus Supreme",
        avatar_url=None,
        rarity="legendary",
        card_number=1,
        drawn_by="SomeUser",
    )
    assert embed.color.value == RARITY_COLORS["legendary"]
    assert "LEGENDARY" in embed.footer.text


def test_build_card_embed_has_description_placeholder():
    embed = build_card_embed(
        display_name="Test",
        avatar_url=None,
        rarity="rare",
        card_number=3,
        drawn_by="User",
    )
    assert embed.description is not None  # placeholder row exists
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/cards/test_embeds.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Create `src/superpal/cards/embeds.py`**

```python
from typing import Optional
import discord
from superpal.cards.models import RARITY_COLORS, RARITY_LABELS


def build_card_embed(
    *,
    display_name: str,
    avatar_url: Optional[str],
    rarity: str,
    card_number: int,
    drawn_by: str,
) -> discord.Embed:
    """Build a Discord embed for a drawn card."""
    color = discord.Color(RARITY_COLORS[rarity])
    label = RARITY_LABELS[rarity]

    embed = discord.Embed(
        description="*[ Stats & lore coming soon ]*",
        color=color,
    )
    embed.set_author(name=display_name, icon_url=avatar_url)
    embed.set_footer(text=f"{label} · #{card_number} · Bringus Card Game")
    embed.set_thumbnail(url=avatar_url)

    return embed
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/cards/test_embeds.py -v
```

Expected: all PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/superpal/cards/embeds.py tests/cards/test_embeds.py
git commit -m "feat(cards): add Discord embed builder for card draws"
```

---

## Task 7: Add env vars for webapp

**Files:**
- Modify: `src/superpal/env.py`

- [ ] **Step 1: Read the current env.py**

Open `src/superpal/env.py` and locate the `get_env` and `get_env_int` helper functions. You will add three new optional env vars below the existing ones.

- [ ] **Step 2: Add webapp env vars**

At the bottom of `src/superpal/env.py`, before any `if __name__ == "__main__"` block (if one exists), add:

```python
WEBAPP_HOST: str = get_env("WEBAPP_HOST", default="0.0.0.0")
WEBAPP_PORT: int = get_env_int("WEBAPP_PORT", default=8080)
WEBAPP_BASE_URL: str = get_env("WEBAPP_BASE_URL", default=f"http://localhost:{WEBAPP_PORT}")
```

Where `get_env(key, default=...)` already exists in that file — use the same pattern. If `get_env` doesn't support a `default` parameter, check whether it does already, and use the correct call signature.

- [ ] **Step 3: Verify import**

```bash
python -c "from superpal.env import WEBAPP_HOST, WEBAPP_PORT, WEBAPP_BASE_URL; print(WEBAPP_HOST, WEBAPP_PORT, WEBAPP_BASE_URL)"
```

Expected: `0.0.0.0 8080 http://localhost:8080`

- [ ] **Step 4: Commit**

```bash
git add src/superpal/env.py
git commit -m "feat(webapp): add WEBAPP_HOST, WEBAPP_PORT, WEBAPP_BASE_URL env vars"
```

---

## Task 8: Bot command — /draw-card

**Files:**
- Modify: `src/bot.py`

- [ ] **Step 1: Read `src/bot.py`**

Locate the block where slash commands are registered (look for `@bot.tree.command` decorators). You'll add the new card commands in the same style. Also find the `on_ready` event handler — you'll add member sync there.

- [ ] **Step 2: Add imports at top of `src/bot.py`**

After the existing imports, add:

```python
from superpal.cards.db import init_db
from superpal.cards.service import (
    draw_card, sync_members, generate_magic_link,
    trade_in, upgrade,
)
from superpal.cards.embeds import build_card_embed
from superpal.cards.models import RARITY_ORDER
from superpal.env import WEBAPP_BASE_URL
from superpal.static import SUPER_PAL_ROLE_NAME
```

- [ ] **Step 3: Initialize DB and sync members in `on_ready`**

In the existing `on_ready` event handler, after the bot is confirmed connected, add:

```python
await init_db()
guild = bot.get_guild(int(get_env("GUILD_ID")))
if guild:
    members_data = [
        {
            "discord_id": str(m.id),
            "display_name": m.display_name,
            "avatar_url": str(m.display_avatar.url) if m.display_avatar else None,
        }
        for m in guild.members
        if not m.bot
    ]
    await sync_members(members_data)
    logger.info("Synced %d members to card DB", len(members_data))
```

- [ ] **Step 4: Add `/draw-card` command**

Add after the existing slash commands:

```python
@bot.tree.command(name="draw-card", description="Draw a card from the Bringus deck (once per week)")
async def draw_card_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer()
    member = interaction.user
    guild = interaction.guild
    super_pal_role = get_super_pal_role(guild)
    is_super_pal = super_pal_role in (member.roles if hasattr(member, "roles") else [])
    max_draws = 2 if is_super_pal else 1

    card = await draw_card(owner_id=str(member.id), max_draws=max_draws)
    if card is None:
        limit_label = "2 draws" if is_super_pal else "1 draw"
        await interaction.followup.send(
            f"You've used your {limit_label} for this week. Come back Monday!",
            ephemeral=True,
        )
        return

    # Fetch the card member's display info
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url FROM members WHERE discord_id = ?",
            (card.card_member_id,),
        ) as cur:
            row = await cur.fetchone()

    display_name = row[0] if row else "Unknown"
    avatar_url = row[1] if row else None

    embed = build_card_embed(
        display_name=display_name,
        avatar_url=avatar_url,
        rarity=card.rarity,
        card_number=card.id,
        drawn_by=member.display_name,
    )
    embed.set_footer(
        text=f"{embed.footer.text} · drawn by {member.display_name}"
    )
    await interaction.followup.send(embed=embed)
```

Add the missing import at top of bot.py:
```python
import aiosqlite
from superpal.cards.db import DB_PATH
```

- [ ] **Step 5: Verify the bot starts without errors**

```bash
python src/bot.py --help 2>&1 || python -c "
import ast, sys
with open('src/bot.py') as f:
    ast.parse(f.read())
print('syntax ok')
"
```

Expected: `syntax ok` (bot won't connect without a real token — that's fine).

- [ ] **Step 6: Commit**

```bash
git add src/bot.py
git commit -m "feat(cards): add /draw-card slash command and on_ready member sync"
```

---

## Task 9: Bot commands — /my-collection, /trade-in, /upgrade, /admin-link

**Files:**
- Modify: `src/bot.py`

- [ ] **Step 1: Add `/my-collection`**

```python
@bot.tree.command(name="my-collection", description="Get a private link to your card collection")
async def my_collection_command(interaction: discord.Interaction) -> None:
    url = await generate_magic_link(
        user_id=str(interaction.user.id),
        link_type="collection",
        base_url=WEBAPP_BASE_URL,
    )
    await interaction.user.send(
        f"Here's your private collection link (valid for 24 hours after first click):\n{url}"
    )
    await interaction.response.send_message(
        "Check your DMs for your collection link!", ephemeral=True
    )
```

- [ ] **Step 2: Add `/trade-in`**

```python
@bot.tree.command(name="trade-in", description="Trade 3 duplicate cards for a random card of the same rarity")
@discord.app_commands.describe(
    member="The member whose card you want to trade in",
    rarity="The rarity of the card to trade",
)
@discord.app_commands.choices(rarity=[
    discord.app_commands.Choice(name="Common", value="common"),
    discord.app_commands.Choice(name="Uncommon", value="uncommon"),
    discord.app_commands.Choice(name="Rare", value="rare"),
    discord.app_commands.Choice(name="Legendary", value="legendary"),
])
async def trade_in_command(
    interaction: discord.Interaction,
    member: discord.Member,
    rarity: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    card = await trade_in(
        owner_id=str(interaction.user.id),
        card_member_id=str(member.id),
        rarity=rarity,
    )
    if card is None:
        await interaction.followup.send(
            f"You need at least 3× {rarity.upper()} {member.display_name} to trade in.",
            ephemeral=True,
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url FROM members WHERE discord_id = ?",
            (card.card_member_id,),
        ) as cur:
            row = await cur.fetchone()

    display_name = row[0] if row else "Unknown"
    avatar_url = row[1] if row else None
    embed = build_card_embed(
        display_name=display_name,
        avatar_url=avatar_url,
        rarity=card.rarity,
        card_number=card.id,
        drawn_by=interaction.user.display_name,
    )
    await interaction.followup.send(
        f"Trade complete! You received:", embed=embed, ephemeral=True
    )
```

- [ ] **Step 3: Add `/upgrade`**

```python
@bot.tree.command(name="upgrade", description="Spend 5 duplicate cards to upgrade a member's card rarity")
@discord.app_commands.describe(
    member="The member whose card you want to upgrade",
    rarity="The current rarity of the card",
)
@discord.app_commands.choices(rarity=[
    discord.app_commands.Choice(name="Common", value="common"),
    discord.app_commands.Choice(name="Uncommon", value="uncommon"),
    discord.app_commands.Choice(name="Rare", value="rare"),
])
async def upgrade_command(
    interaction: discord.Interaction,
    member: discord.Member,
    rarity: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    card = await upgrade(
        owner_id=str(interaction.user.id),
        card_member_id=str(member.id),
        rarity=rarity,
    )
    if card is None:
        next_idx = RARITY_ORDER.index(rarity) + 1 if rarity in RARITY_ORDER and rarity != "legendary" else None
        if next_idx is None:
            msg = "Legendary cards cannot be upgraded further."
        else:
            msg = f"You need at least 5× {rarity.upper()} {member.display_name} to upgrade."
        await interaction.followup.send(msg, ephemeral=True)
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url FROM members WHERE discord_id = ?",
            (card.card_member_id,),
        ) as cur:
            row = await cur.fetchone()

    display_name = row[0] if row else "Unknown"
    avatar_url = row[1] if row else None
    embed = build_card_embed(
        display_name=display_name,
        avatar_url=avatar_url,
        rarity=card.rarity,
        card_number=card.id,
        drawn_by=interaction.user.display_name,
    )
    await interaction.followup.send(
        f"Upgrade complete! {member.display_name} is now {card.rarity.upper()}:",
        embed=embed,
        ephemeral=True,
    )
```

- [ ] **Step 4: Add `/admin-link`**

```python
CLIPPY_ROLE_ID = 1085646770006151259

@bot.tree.command(name="admin-link", description="Get a private admin dashboard link (The Clippy only)")
async def admin_link_command(interaction: discord.Interaction) -> None:
    member = interaction.user
    role_ids = [r.id for r in member.roles] if hasattr(member, "roles") else []
    if CLIPPY_ROLE_ID not in role_ids:
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return
    url = await generate_magic_link(
        user_id=str(member.id),
        link_type="admin",
        base_url=WEBAPP_BASE_URL,
    )
    await member.send(
        f"Here's your private admin dashboard link (valid for 24 hours after first click):\n{url}"
    )
    await interaction.response.send_message(
        "Check your DMs for your admin link!", ephemeral=True
    )
```

- [ ] **Step 5: Syntax check**

```bash
python -c "
import ast
with open('src/bot.py') as f:
    ast.parse(f.read())
print('syntax ok')
"
```

Expected: `syntax ok`

- [ ] **Step 6: Commit**

```bash
git add src/bot.py
git commit -m "feat(cards): add /my-collection, /trade-in, /upgrade, /admin-link commands"
```

---

## Task 10: Webapp auth module

**Files:**
- Create: `src/superpal/webapp/__init__.py`
- Create: `src/superpal/webapp/auth.py`
- Create: `tests/webapp/__init__.py`
- Create: `tests/webapp/test_auth.py`

- [ ] **Step 1: Create package init files**

`src/superpal/webapp/__init__.py` — empty.
`tests/webapp/__init__.py` — empty.

- [ ] **Step 2: Write failing tests**

Create `tests/webapp/test_auth.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from superpal.webapp.auth import get_session_from_request, SESSION_COOKIE_NAME
from superpal.cards.models import MagicLink
from datetime import datetime, timezone, timedelta


def _make_link(link_type="collection") -> MagicLink:
    now = datetime.now(timezone.utc)
    return MagicLink(
        token="tok",
        user_id="111",
        link_type=link_type,
        created_at=now.isoformat(),
        consumed_at=now.isoformat(),
        session_token="sess123",
        session_expires_at=(now + timedelta(hours=24)).isoformat(),
    )


@pytest.mark.asyncio
async def test_get_session_returns_none_when_no_cookie():
    request = MagicMock()
    request.cookies = {}
    with patch("superpal.webapp.auth.get_session", new=AsyncMock(return_value=None)):
        result = await get_session_from_request(request)
    assert result is None


@pytest.mark.asyncio
async def test_get_session_returns_link_when_valid():
    link = _make_link()
    request = MagicMock()
    request.cookies = {SESSION_COOKIE_NAME: "sess123"}
    with patch("superpal.webapp.auth.get_session", new=AsyncMock(return_value=link)):
        result = await get_session_from_request(request)
    assert result is not None
    assert result.user_id == "111"
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest tests/webapp/test_auth.py -v
```

Expected: `ImportError`.

- [ ] **Step 4: Create `src/superpal/webapp/auth.py`**

```python
from typing import Optional
from fastapi import Request, Response
from superpal.cards.models import MagicLink
from superpal.cards.service import get_session

SESSION_COOKIE_NAME = "bringus_session"
SESSION_MAX_AGE = 60 * 60 * 24  # 24 hours in seconds


async def get_session_from_request(request: Request) -> Optional[MagicLink]:
    """Extract and validate the session cookie from a request."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return await get_session(token)


def set_session_cookie(response: Response, session_token: str) -> None:
    """Write the session cookie onto a response."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/webapp/test_auth.py -v
```

Expected: all PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/superpal/webapp/__init__.py src/superpal/webapp/auth.py \
        tests/webapp/__init__.py tests/webapp/test_auth.py
git commit -m "feat(webapp): add session cookie auth helpers"
```

---

## Task 11: HTML templates

**Files:**
- Create: `src/superpal/webapp/templates/collection.html`
- Create: `src/superpal/webapp/templates/admin.html`
- Create: `src/superpal/webapp/templates/expired.html`

No tests — templates are verified by manual smoke test in Task 16.

- [ ] **Step 1: Create `src/superpal/webapp/templates/expired.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Link Expired — Bringus Card Game</title>
  <style>
    body { background: #1e1f22; color: #dcddde; font-family: sans-serif;
           display: flex; align-items: center; justify-content: center;
           min-height: 100vh; margin: 0; }
    .box { text-align: center; max-width: 400px; padding: 32px; }
    h1 { color: #ed4245; font-size: 2rem; margin-bottom: 8px; }
    p { color: #72767d; }
  </style>
</head>
<body>
  <div class="box">
    <h1>Link Expired</h1>
    <p>This link has already been used or has expired.</p>
    <p>Run <code style="color:#96a0ff">/my-collection</code> in Discord to generate a new one.</p>
  </div>
</body>
</html>
```

- [ ] **Step 2: Create `src/superpal/webapp/templates/collection.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ display_name }}'s Collection — Bringus Card Game</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1e1f22; color: #dcddde; font-family: sans-serif; padding: 24px; }
    .header { display: flex; align-items: center; gap: 16px; margin-bottom: 24px;
              padding-bottom: 16px; border-bottom: 1px solid #3f4147; flex-wrap: wrap; }
    .avatar { width: 56px; height: 56px; border-radius: 50%; background: #5865f2;
              object-fit: cover; }
    .avatar-placeholder { width: 56px; height: 56px; border-radius: 50%;
                          background: linear-gradient(135deg, #667eea, #764ba2);
                          display: flex; align-items: center; justify-content: center;
                          color: #fff; font-weight: 700; font-size: 20px; }
    h1 { font-size: 1.4rem; font-weight: 700; }
    .subtitle { color: #72767d; font-size: 13px; margin-top: 2px; }
    .refresh-btn { margin-left: auto; background: #5865f2; color: #fff; border: none;
                   padding: 8px 18px; border-radius: 4px; cursor: pointer; font-size: 13px; }
    .refresh-btn:hover { background: #4752c4; }
    .pills { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; }
    .pill { padding: 4px 10px; border-radius: 3px; font-size: 12px; font-weight: 700; color: #fff; }
    .pill-common { background: #95a5a6; }
    .pill-uncommon { background: #27ae60; }
    .pill-rare { background: #2980b9; }
    .pill-legendary { background: #f39c12; color: #7d4f00; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }
    .card { background: #2b2d31; border-radius: 8px; padding: 14px;
            border-left: 4px solid #95a5a6; }
    .card-common { border-left-color: #95a5a6; }
    .card-uncommon { border-left-color: #27ae60; }
    .card-rare { border-left-color: #2980b9; }
    .card-legendary { border-left-color: #f39c12; }
    .card-avatar { width: 44px; height: 44px; border-radius: 50%; object-fit: cover;
                   margin-bottom: 8px; }
    .card-avatar-placeholder { width: 44px; height: 44px; border-radius: 50%;
                               background: #555; margin-bottom: 8px; }
    .card-name { font-weight: 600; font-size: 13px; color: #fff; margin-bottom: 4px;
                 white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .card-rarity { font-size: 11px; font-weight: 700; letter-spacing: 1px; }
    .rarity-common { color: #95a5a6; }
    .rarity-uncommon { color: #27ae60; }
    .rarity-rare { color: #2980b9; }
    .rarity-legendary { color: #f39c12; }
    .qty-badge { display: inline-block; background: #3f4147; color: #b9bbbe;
                 font-size: 10px; padding: 1px 5px; border-radius: 3px; margin-top: 2px; }
    .unknown { opacity: 0.4; border-left-color: #3f4147; }
    .unknown .card-name { color: #555; }
  </style>
</head>
<body>
  <div class="header">
    {% if avatar_url %}
      <img class="avatar" src="{{ avatar_url }}" alt="">
    {% else %}
      <div class="avatar-placeholder">{{ display_name[0] | upper }}</div>
    {% endif %}
    <div>
      <h1>{{ display_name }}'s Collection</h1>
      <div class="subtitle">{{ total_cards }} card{{ 's' if total_cards != 1 else '' }} · {{ unique_members }} unique member{{ 's' if unique_members != 1 else '' }}</div>
    </div>
    <form method="post" action="/collection/refresh" style="margin-left:auto">
      <button class="refresh-btn" type="submit">Generate New Link</button>
    </form>
  </div>

  <div class="pills">
    <span class="pill pill-common">COMMON ×{{ counts.common }}</span>
    <span class="pill pill-uncommon">UNCOMMON ×{{ counts.uncommon }}</span>
    <span class="pill pill-rare">RARE ×{{ counts.rare }}</span>
    <span class="pill pill-legendary">LEGENDARY ×{{ counts.legendary }}</span>
  </div>

  <div class="grid">
    {% for card in owned %}
    <div class="card card-{{ card.rarity }}">
      {% if card.avatar_url %}
        <img class="card-avatar" src="{{ card.avatar_url }}" alt="">
      {% else %}
        <div class="card-avatar-placeholder"></div>
      {% endif %}
      <div class="card-name">{{ card.display_name }}</div>
      <div class="card-rarity rarity-{{ card.rarity }}">{{ card.rarity | upper }}</div>
      {% if card.quantity > 1 %}
        <div class="qty-badge">×{{ card.quantity }}</div>
      {% endif %}
    </div>
    {% endfor %}

    {% for m in undiscovered %}
    <div class="card unknown">
      <div class="card-avatar-placeholder"></div>
      <div class="card-name">???</div>
      <div class="card-rarity" style="color:#555">NOT YET FOUND</div>
    </div>
    {% endfor %}
  </div>
</body>
</html>
```

- [ ] **Step 3: Create `src/superpal/webapp/templates/admin.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Admin Dashboard — Bringus Card Game</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1e1f22; color: #dcddde; font-family: sans-serif; padding: 24px; max-width: 720px; margin: 0 auto; }
    h1 { font-size: 1.4rem; margin-bottom: 4px; }
    .subtitle { color: #72767d; font-size: 13px; margin-bottom: 24px; }
    .stats { background: #2b2d31; border-radius: 6px; padding: 14px; margin-bottom: 20px; font-size: 13px; color: #72767d; }
    .stats strong { color: #dcddde; }
    .sync-btn { background: #5865f2; color: #fff; border: none; padding: 8px 16px;
                border-radius: 4px; cursor: pointer; font-size: 13px; margin-top: 10px; display: block; }
    .sync-btn:hover { background: #4752c4; }
    .member-list { display: flex; flex-direction: column; gap: 8px; }
    .member-row { background: #2b2d31; border-radius: 6px; padding: 10px 14px;
                  display: flex; align-items: center; gap: 12px; }
    .member-row.excluded { opacity: 0.55; border: 1px dashed #ed4245; }
    .member-avatar { width: 36px; height: 36px; border-radius: 50%; object-fit: cover; background: #555; }
    .member-info { flex: 1; }
    .member-name { font-weight: 600; font-size: 13px; }
    .member-id { font-size: 11px; color: #72767d; margin-top: 2px; }
    .excluded-label { font-size: 11px; color: #ed4245; margin-top: 2px; }
    .exclude-btn { background: #ed4245; color: #fff; border: none; padding: 5px 12px;
                   border-radius: 3px; cursor: pointer; font-size: 12px; }
    .include-btn { background: #57f287; color: #1e1f22; border: none; padding: 5px 12px;
                   border-radius: 3px; cursor: pointer; font-size: 12px; font-weight: 700; }
  </style>
</head>
<body>
  <h1>Admin Dashboard</h1>
  <p class="subtitle">Excluded members do not appear in the card draw pool.</p>

  <div class="stats">
    <strong>{{ stats.eligible }}</strong> eligible ·
    <strong>{{ stats.excluded }}</strong> excluded ·
    <strong>{{ stats.total_cards }}</strong> cards in circulation
    <form method="post" action="/admin/sync">
      <button class="sync-btn" type="submit">Sync Member List from Discord</button>
    </form>
  </div>

  <div class="member-list">
    {% for m in members %}
    <div class="member-row {% if m.is_excluded %}excluded{% endif %}">
      {% if m.avatar_url %}
        <img class="member-avatar" src="{{ m.avatar_url }}" alt="">
      {% else %}
        <div class="member-avatar"></div>
      {% endif %}
      <div class="member-info">
        <div class="member-name">{{ m.display_name }}</div>
        {% if m.is_excluded %}
          <div class="excluded-label">Excluded — not in card pool</div>
        {% else %}
          <div class="member-id">ID: {{ m.discord_id }}</div>
        {% endif %}
      </div>
      <form method="post" action="/admin/exclude/{{ m.discord_id }}">
        {% if m.is_excluded %}
          <button class="include-btn" type="submit">Re-include</button>
        {% else %}
          <button class="exclude-btn" type="submit">Exclude</button>
        {% endif %}
      </form>
    </div>
    {% endfor %}
  </div>
</body>
</html>
```

- [ ] **Step 4: Commit**

```bash
git add src/superpal/webapp/templates/
git commit -m "feat(webapp): add collection, admin, and expired HTML templates"
```

---

## Task 12: Webapp routes

**Files:**
- Create: `src/superpal/webapp/app.py` (minimal factory — bot integration added in Task 13)
- Create: `src/superpal/webapp/routes.py`
- Create: `tests/webapp/test_routes.py`

- [ ] **Step 1: Create minimal `src/superpal/webapp/app.py`**

The test imports `create_app` — create it now so the import resolves. Task 13 will add bot startup integration.

```python
from fastapi import FastAPI
from superpal.webapp.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="Bringus Card Game", docs_url=None, redoc_url=None)
    app.include_router(router)
    return app
```

- [ ] **Step 2: Write failing tests**

Create `tests/webapp/test_routes.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport
from superpal.webapp.app import create_app
from superpal.cards.models import MagicLink
from datetime import datetime, timezone, timedelta


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _link(link_type="collection") -> MagicLink:
    now = datetime.now(timezone.utc)
    return MagicLink(
        token="abc123",
        user_id="111",
        link_type=link_type,
        created_at=now.isoformat(),
        consumed_at=now.isoformat(),
        session_token="sess_abc",
        session_expires_at=(now + timedelta(hours=24)).isoformat(),
    )


@pytest.mark.asyncio
async def test_link_redirect_on_valid_token(client):
    link = _link()
    with patch("superpal.webapp.routes.consume_magic_link", new=AsyncMock(return_value=link)):
        response = await client.get("/link/abc123", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert "bringus_session" in response.cookies


@pytest.mark.asyncio
async def test_link_expired_returns_expired_page(client):
    with patch("superpal.webapp.routes.consume_magic_link", new=AsyncMock(return_value=None)):
        response = await client.get("/link/deadbeef", follow_redirects=False)
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_collection_shows_expired_without_session(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.get("/collection")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_shows_expired_without_session(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.get("/admin")
    assert response.status_code == 200
    assert "expired" in response.text.lower()
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest tests/webapp/test_routes.py -v
```

Expected: `ImportError` (routes.py doesn't exist yet).

- [ ] **Step 4: Create `src/superpal/webapp/routes.py`**

```python
from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from superpal.cards.service import (
    consume_magic_link, get_collection, get_all_members_for_admin,
    get_pool_stats, set_excluded, sync_members as _sync_members,
)
from superpal.webapp.auth import get_session_from_request, set_session_cookie

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


@router.get("/link/{token}")
async def magic_link_landing(token: str, request: Request, response: Response):
    link = await consume_magic_link(token)
    if link is None:
        return templates.TemplateResponse("expired.html", {"request": request})
    redirect_path = "/admin" if link.link_type == "admin" else "/collection"
    resp = RedirectResponse(url=redirect_path, status_code=303)
    set_session_cookie(resp, link.session_token)
    return resp


@router.get("/collection", response_class=HTMLResponse)
async def collection_view(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse("expired.html", {"request": request})
    data = await get_collection(session.user_id)
    # Fetch avatar for the session owner
    import aiosqlite
    from superpal.cards.db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url FROM members WHERE discord_id = ?",
            (session.user_id,),
        ) as cur:
            row = await cur.fetchone()
    display_name = row[0] if row else "Unknown"
    avatar_url = row[1] if row else None
    total_cards = sum(c["quantity"] for c in data["owned"])
    unique_members = len(data["owned"])
    return templates.TemplateResponse("collection.html", {
        "request": request,
        "display_name": display_name,
        "avatar_url": avatar_url,
        "owned": data["owned"],
        "undiscovered": data["undiscovered"],
        "counts": data["counts"],
        "total_cards": total_cards,
        "unique_members": unique_members,
    })


@router.post("/collection/refresh")
async def collection_refresh(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse("expired.html", {"request": request})
    from superpal.cards.service import generate_magic_link, consume_magic_link as consume
    from superpal.env import WEBAPP_BASE_URL
    url = await generate_magic_link(session.user_id, "collection", WEBAPP_BASE_URL)
    token = url.split("/")[-1]
    link = await consume(token)
    resp = RedirectResponse(url="/collection", status_code=303)
    if link:
        set_session_cookie(resp, link.session_token)
    return resp


@router.get("/admin", response_class=HTMLResponse)
async def admin_view(request: Request):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse("expired.html", {"request": request})
    members = await get_all_members_for_admin()
    stats = await get_pool_stats()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "members": members,
        "stats": stats,
    })


@router.post("/admin/exclude/{member_id}")
async def toggle_exclude(member_id: str, request: Request):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return RedirectResponse(url="/", status_code=303)
    members = await get_all_members_for_admin()
    current = next((m for m in members if m["discord_id"] == member_id), None)
    if current:
        await set_excluded(member_id, excluded=not current["is_excluded"])
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/sync")
async def admin_sync(request: Request):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return RedirectResponse(url="/", status_code=303)
    # Sync is triggered manually; pull from the shared bot client
    # The bot sets _guild_members_cache after on_ready — import it here
    try:
        from bot import _guild_members_cache
        if _guild_members_cache:
            await _sync_members(_guild_members_cache)
    except ImportError:
        pass  # running in isolation — sync skipped
    return RedirectResponse(url="/admin", status_code=303)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/webapp/test_routes.py -v
```

Expected: all PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/superpal/webapp/app.py src/superpal/webapp/routes.py tests/webapp/test_routes.py
git commit -m "feat(webapp): add FastAPI app factory and HTTP routes"
```

---

## Task 13: Bot integration — asyncio.gather and member cache

**Files:**
- Modify: `src/bot.py`

- [ ] **Step 1: Expose guild members cache in `src/bot.py`**

Near the top of `bot.py`, after the bot instance is created, add:

```python
_guild_members_cache: list[dict] = []
```

In the `on_ready` handler, after syncing members to the DB, also populate the cache:

```python
_guild_members_cache.clear()
_guild_members_cache.extend(members_data)
```

- [ ] **Step 2: Change `bot.run()` to asyncio.gather pattern**

Find the bottom of `bot.py` where `bot.run(TOKEN)` is called. Replace it with:

```python
import asyncio
import uvicorn
from superpal.webapp.app import create_app
from superpal.env import WEBAPP_HOST, WEBAPP_PORT


async def _main() -> None:
    webapp = create_app()
    config = uvicorn.Config(webapp, host=WEBAPP_HOST, port=WEBAPP_PORT, log_level="info")
    server = uvicorn.Server(config)
    async with bot:
        await asyncio.gather(
            bot.start(get_env("SUPERPAL_TOKEN")),
            server.serve(),
        )


if __name__ == "__main__":
    asyncio.run(_main())
```

Remove any existing `bot.run(TOKEN)` or `asyncio.run(bot.start(...))` call — replace it entirely.

- [ ] **Step 3: Syntax check**

```bash
python -c "
import ast
with open('src/bot.py') as f:
    ast.parse(f.read())
print('bot.py syntax ok')
"
```

Expected: `syntax ok`.

- [ ] **Step 4: Verify webapp starts alone (no Discord token needed)**

```bash
cd src && python -c "
from superpal.webapp.app import create_app
app = create_app()
print('routes:', [r.path for r in app.routes])
"
```

Expected: prints a list including `/link/{token}`, `/collection`, `/admin`.

- [ ] **Step 5: Commit**

```bash
git add src/bot.py
git commit -m "feat(webapp): integrate FastAPI with Discord bot via asyncio.gather"
```

---

## Task 14: Full test suite pass

**Files:** none

- [ ] **Step 1: Run entire test suite**

```bash
cd /Users/achurm/Downloads/discord-super-pal-of-the-week
pytest -v
```

Expected: all tests PASSED. Fix any failures before proceeding.

- [ ] **Step 2: Run linter**

```bash
ruff check src/ tests/
```

Fix all reported issues. Then:

```bash
ruff check src/ tests/
```

Expected: no output (clean).

- [ ] **Step 3: Commit any fixes**

```bash
git add -p  # stage only lint fixes
git commit -m "fix: resolve ruff lint issues across cards and webapp modules"
```

---

## Task 15: Kubernetes update

**Files:**
- Modify: `k8s/deploy-super-pal.yaml`

- [ ] **Step 1: Add `WEBAPP_PORT` and `WEBAPP_BASE_URL` env vars to the super-pal container**

Open `k8s/deploy-super-pal.yaml`. Find the `super-pal` container's `env:` block. Add:

```yaml
- name: WEBAPP_PORT
  value: "8080"
- name: WEBAPP_BASE_URL
  value: "https://cards.bringus.gg"  # replace with actual hostname
- name: CARDS_DB_PATH
  value: "/data/cards.db"
```

- [ ] **Step 2: Add container port declaration**

In the `super-pal` container spec, add or extend the `ports:` block:

```yaml
ports:
  - containerPort: 8080
    name: webapp
```

- [ ] **Step 3: Add a volumeMount for the SQLite file (if not already using a PVC)**

Under the `super-pal` container's `volumeMounts:`:

```yaml
volumeMounts:
  - name: card-data
    mountPath: /data
```

Under the pod-level `volumes:`:

```yaml
volumes:
  - name: card-data
    emptyDir: {}   # replace with a PersistentVolumeClaim for production durability
```

**Note:** `emptyDir` is wiped on pod restart — swap for a PVC before deploying to production.

- [ ] **Step 4: Commit**

```bash
git add k8s/deploy-super-pal.yaml
git commit -m "feat(k8s): expose webapp port and add SQLite volume mount"
```

---

## Task 16: Smoke test (manual verification)

No code changes. Follow the verification steps from the design spec.

- [ ] Run bot locally: `cd src && SUPERPAL_TOKEN=x GUILD_ID=y CHANNEL_ID=z python bot.py`
- [ ] Open `http://localhost:8080/collection` — should show `expired.html` (no session).
- [ ] In Discord, run `/draw-card` — embed appears in channel with correct rarity color.
- [ ] Run `/draw-card` again in the same week — bot replies with limit message.
- [ ] Run `/my-collection` — DM arrives with link. Confirm link is NOT posted to channel.
- [ ] Click the link — collection view loads with owned cards and `???` silhouettes.
- [ ] Click the same link again — `expired.html` shown.
- [ ] Give yourself 3× common of any member via direct DB insert; run `/trade-in` — new card received.
- [ ] Give yourself 5× common of any member via direct DB insert; run `/upgrade` — uncommon card received.
- [ ] Run `/upgrade` on a legendary card — clear rejection message.
- [ ] Run `/admin-link` without Clippy role — rejected. With Clippy role — DM received.
- [ ] Admin dashboard: exclude a member, draw cards and confirm that member never appears.
- [ ] Admin sync button — confirm new/renamed members appear.
