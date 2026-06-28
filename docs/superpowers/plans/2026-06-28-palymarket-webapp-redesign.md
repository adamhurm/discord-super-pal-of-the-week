# Palymarket Webapp Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the palymarket webapp from minimal pool-size display into a full prediction market UI with probability bars, SVG probability charts, portfolio page, activity feed, and market proposal form.

**Architecture:** Server-rendered Jinja2 pages only (no JS framework). New `market_probability_history` table snapshots implied probability on every bet. SVG chart path computed server-side in route handlers. All pages share an inline sub-nav bar.

**Tech Stack:** Python 3.13, FastAPI, aiosqlite, Jinja2, inline CSS (Discord dark theme), inline SVG (no charting library)

## Global Constraints

- Discord dark theme: `#1e1f22` page bg, `#2b2d31` card bg, `#dcddde` text, `#5865f2` accent, `#3ba55c` YES-green, `#ed4245` NO-red
- Inline `<style>` per template (no external CSS file) — matches existing app convention
- No external JS libraries (anime.min.js is already there but not used here)
- All async functions use `aiosqlite.connect(DB_PATH)` with `db.row_factory = aiosqlite.Row`
- Run tests with: `.venv/bin/python -m pytest tests/ -q`
- Run a single test: `.venv/bin/python -m pytest tests/palymarket/test_service.py::test_name -q`

---

## File Map

**Modified:**
- `src/superpal/cards/db.py` — add `market_probability_history` table to `_SCHEMA`
- `src/superpal/palymarket/service.py` — add 5 new functions; modify `place_or_update_bet`
- `src/superpal/webapp/routes.py` — update 2 handlers; add 4 new handlers
- `src/superpal/webapp/templates/palymarket_list.html` — full redesign
- `src/superpal/webapp/templates/palymarket_detail.html` — full redesign
- `src/superpal/webapp/templates/palymarket_pending.html` — add sub-nav

**Created:**
- `src/superpal/webapp/templates/palymarket_portfolio.html`
- `src/superpal/webapp/templates/palymarket_activity.html`
- `src/superpal/webapp/templates/palymarket_propose.html`
- `tests/palymarket/test_history_service.py`

---

### Task 1: Add `market_probability_history` table to DB schema

**Files:**
- Modify: `src/superpal/cards/db.py` (after line 255, after the `market_bets` table creation)
- Test: `tests/palymarket/test_history_service.py` (new file)

**Interfaces:**
- Produces: table `market_probability_history(id, market_id, yes_pct, no_pool, yes_pool, recorded_at)`

- [ ] **Step 1: Write the failing test**

Create `tests/palymarket/test_history_service.py`:

```python
import aiosqlite
import importlib
import pytest


@pytest.fixture
async def db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("CARDS_DB_PATH", db_file)

    import superpal.cards.db as db_mod
    import superpal.palymarket.service as svc_mod

    importlib.reload(db_mod)
    importlib.reload(svc_mod)

    await db_mod.init_db()
    return db_mod, svc_mod


@pytest.mark.asyncio
async def test_probability_history_table_created(db):
    """init_db creates the market_probability_history table."""
    db_mod, _ = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        async with conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='market_probability_history'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/palymarket/test_history_service.py::test_probability_history_table_created -q
```

Expected: FAIL — `AssertionError: assert None is not None`

- [ ] **Step 3: Add the table to `_SCHEMA` in `src/superpal/cards/db.py`**

After the `market_bets` CREATE TABLE block (currently the last `await db.commit()` at line 255), add:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

```
.venv/bin/python -m pytest tests/palymarket/test_history_service.py::test_probability_history_table_created -q
```

Expected: PASS

- [ ] **Step 5: Run full test suite to confirm no regressions**

```
.venv/bin/python -m pytest tests/ -q
```

Expected: all existing tests pass

- [ ] **Step 6: Commit**

```bash
git add src/superpal/cards/db.py tests/palymarket/test_history_service.py
git commit -m "feat: add market_probability_history table to DB schema"
```

---

### Task 2: Service — probability history functions

**Files:**
- Modify: `src/superpal/palymarket/service.py`
- Test: `tests/palymarket/test_history_service.py`

**Interfaces:**
- Produces: `record_probability_snapshot(market_id: int) -> None`
- Produces: `get_probability_history(market_id: int) -> list[tuple[float, datetime]]`
- Modified: `place_or_update_bet` calls `record_probability_snapshot` before returning `True, ""`

- [ ] **Step 1: Write failing tests**

Append to `tests/palymarket/test_history_service.py`:

```python
_NOW = "2024-01-01 00:00:00"


async def _insert_member(db_path, discord_id, palycoin_balance=0):
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO members "
            "(discord_id, display_name, avatar_url, is_excluded, synced_at, palycoin_balance) "
            "VALUES (?, ?, NULL, 0, ?, ?)",
            (discord_id, discord_id, _NOW, palycoin_balance),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_place_bet_records_snapshot(db):
    """place_or_update_bet inserts a probability snapshot after the bet commits."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    await _insert_member(db_mod.DB_PATH, "p2", 100)
    market = await svc.propose_market("Test", None, "p1")
    await svc.approve_market(market.id, "admin")

    await svc.place_or_update_bet(market.id, "p1", "yes", 30)
    await svc.place_or_update_bet(market.id, "p2", "no", 70)

    history = await svc.get_probability_history(market.id)
    assert len(history) == 2
    pct1, _ = history[0]
    pct2, _ = history[1]
    # After first bet: 30 YES / 30 total = 1.0
    assert abs(pct1 - 1.0) < 0.001
    # After second bet: 30 YES / 100 total = 0.30
    assert abs(pct2 - 0.30) < 0.001


@pytest.mark.asyncio
async def test_get_probability_history_empty(db):
    """get_probability_history returns [] for a market with no bets yet."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    market = await svc.propose_market("Test", None, "p1")
    await svc.approve_market(market.id, "admin")
    history = await svc.get_probability_history(market.id)
    assert history == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/python -m pytest tests/palymarket/test_history_service.py -q
```

Expected: FAIL on the two new tests (AttributeError: module has no attribute `get_probability_history`)

- [ ] **Step 3: Implement the two new functions in `src/superpal/palymarket/service.py`**

Add after the imports (after line 10 `from superpal.palymarket.models import Bet, Market`):

```python
async def record_probability_snapshot(market_id: int) -> None:
    """Snapshot current YES% into market_probability_history."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT yes_pool, no_pool FROM markets WHERE id = ?",
            (market_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return
        total = row["yes_pool"] + row["no_pool"]
        yes_pct = row["yes_pool"] / total if total > 0 else 0.5
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "INSERT INTO market_probability_history "
            "(market_id, yes_pct, yes_pool, no_pool, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (market_id, yes_pct, row["yes_pool"], row["no_pool"], now),
        )
        await db.commit()


async def get_probability_history(market_id: int) -> list[tuple[float, datetime]]:
    """Return (yes_pct, recorded_at) pairs ordered by time."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT yes_pct, recorded_at FROM market_probability_history "
            "WHERE market_id = ? ORDER BY recorded_at",
            (market_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        (row["yes_pct"], datetime.fromisoformat(row["recorded_at"]))
        for row in rows
    ]
```

- [ ] **Step 4: Modify `place_or_update_bet` to call `record_probability_snapshot`**

In `place_or_update_bet`, the function currently ends with:
```python
        await db.commit()
    return True, ""
```

Change it to:
```python
        await db.commit()
    await record_probability_snapshot(market_id)
    return True, ""
```

(The `async with aiosqlite.connect` block closes before this call, so it opens a fresh connection.)

- [ ] **Step 5: Run the new tests to verify they pass**

```
.venv/bin/python -m pytest tests/palymarket/test_history_service.py -q
```

Expected: all 3 tests PASS

- [ ] **Step 6: Run full suite**

```
.venv/bin/python -m pytest tests/ -q
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/superpal/palymarket/service.py tests/palymarket/test_history_service.py
git commit -m "feat: record probability snapshot on every bet; add get_probability_history"
```

---

### Task 3: Service — portfolio, activity, and named bets functions

**Files:**
- Modify: `src/superpal/palymarket/service.py`
- Test: `tests/palymarket/test_history_service.py`

**Interfaces:**
- Produces: `get_player_portfolio(player_id: str) -> dict` with keys `"active"` and `"resolved"`, each a `list[dict]`
  - active item keys: `market` (Market), `side` (str), `amount` (int), `yes_pct` (int 0–100), `estimated_payout` (int)
  - resolved item keys: `market` (Market), `side` (str), `amount` (int), `outcome` (str), `amount_returned` (int), `won` (bool)
- Produces: `get_recent_activity(limit: int = 50) -> list[dict]`
  - keys: `display_name` (str), `player_id` (str), `side` (str), `amount` (int), `market_id` (int), `market_title` (str), `placed_at` (datetime)
- Produces: `get_bets_for_market_with_names(market_id: int) -> list[dict]`
  - keys: `player_id` (str), `display_name` (str), `side` (str), `amount` (int), `placed_at` (datetime)

- [ ] **Step 1: Write failing tests**

Append to `tests/palymarket/test_history_service.py`:

```python
@pytest.mark.asyncio
async def test_get_player_portfolio_active(db):
    """Active bet appears in portfolio with correct yes_pct and estimated_payout."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    await _insert_member(db_mod.DB_PATH, "p2", 100)
    market = await svc.propose_market("Test", None, "p1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "p1", "yes", 50)
    await svc.place_or_update_bet(market.id, "p2", "no", 50)

    portfolio = await svc.get_player_portfolio("p1")
    assert len(portfolio["active"]) == 1
    assert portfolio["resolved"] == []
    pos = portfolio["active"][0]
    assert pos["market"].id == market.id
    assert pos["side"] == "yes"
    assert pos["amount"] == 50
    assert pos["yes_pct"] == 50          # 50 YES / 100 total = 50%
    assert pos["estimated_payout"] == 100  # floor(50/50 * 100) = 100


@pytest.mark.asyncio
async def test_get_player_portfolio_resolved(db):
    """Resolved win/loss appears in portfolio["resolved"] with correct amount_returned."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    await _insert_member(db_mod.DB_PATH, "p2", 100)
    market = await svc.propose_market("Test", None, "p1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "p1", "yes", 50)
    await svc.place_or_update_bet(market.id, "p2", "no", 50)
    await svc.close_market(market.id, "admin")
    await svc.resolve_market(market.id, "yes", "admin")

    portfolio_winner = await svc.get_player_portfolio("p1")
    assert portfolio_winner["active"] == []
    assert len(portfolio_winner["resolved"]) == 1
    r = portfolio_winner["resolved"][0]
    assert r["won"] is True
    assert r["amount_returned"] == 100  # floor(50/50 * 100)

    portfolio_loser = await svc.get_player_portfolio("p2")
    r2 = portfolio_loser["resolved"][0]
    assert r2["won"] is False
    assert r2["amount_returned"] == 0


@pytest.mark.asyncio
async def test_get_recent_activity(db):
    """Recent activity returns bets newest-first with display_name and market title."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    market = await svc.propose_market("My Market", None, "p1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "p1", "yes", 40)

    activity = await svc.get_recent_activity(limit=10)
    assert len(activity) >= 1
    row = activity[0]
    assert row["market_title"] == "My Market"
    assert row["side"] == "yes"
    assert row["amount"] == 40
    assert row["display_name"] == "p1"  # display_name equals discord_id in test fixture


@pytest.mark.asyncio
async def test_get_bets_for_market_with_names(db):
    """Returns bets with display_name from members table."""
    db_mod, svc = db
    await _insert_member(db_mod.DB_PATH, "p1", 100)
    market = await svc.propose_market("Test", None, "p1")
    await svc.approve_market(market.id, "admin")
    await svc.place_or_update_bet(market.id, "p1", "no", 25)

    bets = await svc.get_bets_for_market_with_names(market.id)
    assert len(bets) == 1
    assert bets[0]["display_name"] == "p1"
    assert bets[0]["side"] == "no"
    assert bets[0]["amount"] == 25
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/python -m pytest tests/palymarket/test_history_service.py -q
```

Expected: FAIL on the 4 new tests (AttributeError: no attribute `get_player_portfolio`)

- [ ] **Step 3: Implement the three new functions in `src/superpal/palymarket/service.py`**

Add after `get_probability_history`:

```python
async def get_player_portfolio(player_id: str) -> dict:
    """Return active positions and resolved history for portfolio page."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                m.id AS m_id, m.title, m.description, m.created_by, m.status,
                m.outcome, m.yes_pool, m.no_pool, m.created_at,
                m.resolved_at, m.resolved_by,
                b.side, b.amount, b.placed_at
            FROM market_bets b
            JOIN markets m ON m.id = b.market_id
            WHERE b.player_id = ?
              AND m.status NOT IN ('rejected', 'pending_approval')
            ORDER BY b.placed_at DESC
            """,
            (player_id,),
        ) as cur:
            rows = await cur.fetchall()

    active: list[dict] = []
    resolved: list[dict] = []
    for row in rows:
        market = Market(
            id=row["m_id"],
            title=row["title"],
            description=row["description"],
            created_by=row["created_by"],
            status=row["status"],
            outcome=row["outcome"],
            yes_pool=row["yes_pool"],
            no_pool=row["no_pool"],
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=(
                datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None
            ),
            resolved_by=row["resolved_by"],
        )
        side = row["side"]
        amount = row["amount"]
        total = market.yes_pool + market.no_pool
        yes_pct = round(market.yes_pool / total * 100) if total > 0 else 50

        if market.status in ("open", "closed"):
            winning_pool = market.yes_pool if side == "yes" else market.no_pool
            estimated_payout = (
                math.floor(amount / winning_pool * total) if winning_pool > 0 else 0
            )
            active.append(
                {
                    "market": market,
                    "side": side,
                    "amount": amount,
                    "yes_pct": yes_pct,
                    "estimated_payout": estimated_payout,
                }
            )
        elif market.status == "resolved":
            won = market.outcome == side
            if won:
                winning_pool = market.yes_pool if side == "yes" else market.no_pool
                amount_returned = (
                    math.floor(amount / winning_pool * total) if winning_pool > 0 else 0
                )
            else:
                amount_returned = 0
            resolved.append(
                {
                    "market": market,
                    "side": side,
                    "amount": amount,
                    "outcome": market.outcome,
                    "amount_returned": amount_returned,
                    "won": won,
                }
            )

    return {"active": active, "resolved": resolved}


async def get_recent_activity(limit: int = 50) -> list[dict]:
    """Return recent bets across all markets, newest first, with display names."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT mb.player_id, mb.side, mb.amount, mb.placed_at,
                   m.id AS market_id, m.title AS market_title,
                   mem.display_name
            FROM market_bets mb
            JOIN markets m ON m.id = mb.market_id
            JOIN members mem ON mem.discord_id = mb.player_id
            ORDER BY mb.placed_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "display_name": row["display_name"],
            "player_id": row["player_id"],
            "side": row["side"],
            "amount": row["amount"],
            "market_id": row["market_id"],
            "market_title": row["market_title"],
            "placed_at": datetime.fromisoformat(row["placed_at"]),
        }
        for row in rows
    ]


async def get_bets_for_market_with_names(market_id: int) -> list[dict]:
    """Return bets for a market with player display names, ordered by placed_at."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT mb.player_id, mb.side, mb.amount, mb.placed_at,
                   mem.display_name
            FROM market_bets mb
            LEFT JOIN members mem ON mem.discord_id = mb.player_id
            WHERE mb.market_id = ?
            ORDER BY mb.placed_at
            """,
            (market_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {
            "player_id": row["player_id"],
            "display_name": row["display_name"] or row["player_id"],
            "side": row["side"],
            "amount": row["amount"],
            "placed_at": datetime.fromisoformat(row["placed_at"]),
        }
        for row in rows
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

```
.venv/bin/python -m pytest tests/palymarket/test_history_service.py -q
```

Expected: all 7 tests PASS

- [ ] **Step 5: Run full suite**

```
.venv/bin/python -m pytest tests/ -q
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/superpal/palymarket/service.py tests/palymarket/test_history_service.py
git commit -m "feat: add portfolio, activity, and named-bets service functions"
```

---

### Task 4: Update route handlers in `routes.py`

**Files:**
- Modify: `src/superpal/webapp/routes.py`

**Interfaces:**
- Consumes: `palymarket_svc.get_probability_history`, `palymarket_svc.get_player_portfolio`, `palymarket_svc.get_recent_activity`, `palymarket_svc.get_bets_for_market_with_names`, `palymarket_svc.list_pending_markets`
- Produces: all palymarket route handlers render templates with the context variables listed below

The `palymarket_list` and `palymarket_detail` handlers are modified; four handlers are new. All new GET routes must be inserted **before** the existing `GET /palymarket/{market_id}` handler (currently at line 794) to prevent FastAPI matching them as integer market IDs.

- [ ] **Step 1: Update the `palymarket_list` handler**

Replace the existing `palymarket_list` function (lines 720–732) with:

```python
@router.get("/palymarket", response_class=HTMLResponse)
async def palymarket_list(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    is_admin = session.link_type == "admin"
    balance = await palymarket_svc.get_palycoin_balance(session.user_id)
    markets = await palymarket_svc.list_markets()
    player_bets = await palymarket_svc.get_player_active_bets(session.user_id)
    bet_map = {bet.market_id: bet for _, bet in player_bets}
    pending_count = len(await palymarket_svc.list_pending_markets()) if is_admin else 0
    # Attach yes_pct to each market for template use
    for m in markets:
        total = m.yes_pool + m.no_pool
        m._yes_pct = round(m.yes_pool / total * 100) if total > 0 else 50
    return templates.TemplateResponse(request, "palymarket_list.html", {
        "balance": balance,
        "markets": markets,
        "bet_map": bet_map,
        "is_admin": is_admin,
        "pending_count": pending_count,
        "proposed": request.query_params.get("proposed") == "1",
        "error": request.query_params.get("error"),
        "active_tab": "markets",
    })
```

Note: attaching `_yes_pct` as a dynamic attribute on the Market dataclass works because Python dataclasses don't prevent extra attribute assignment. An alternative is to build a list of dicts, but the attribute approach is simpler here.

- [ ] **Step 2: Add four new handlers before `GET /palymarket/{market_id}`**

Insert the following block between the `palymarket_exchange` handler (ends ~line 756) and the `economy` handler (starts ~line 759). The exact insertion point is after the closing `return RedirectResponse(url="/palymarket", status_code=303)` of `palymarket_exchange` and before `@router.get("/economy"`:

```python
@router.get("/palymarket/portfolio", response_class=HTMLResponse)
async def palymarket_portfolio(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    is_admin = session.link_type == "admin"
    portfolio = await palymarket_svc.get_player_portfolio(session.user_id)
    pending_count = len(await palymarket_svc.list_pending_markets()) if is_admin else 0
    total_staked = sum(p["amount"] for p in portfolio["active"])
    return templates.TemplateResponse(request, "palymarket_portfolio.html", {
        "active": portfolio["active"],
        "resolved": portfolio["resolved"],
        "total_staked": total_staked,
        "is_admin": is_admin,
        "pending_count": pending_count,
        "active_tab": "portfolio",
    })


@router.get("/palymarket/activity", response_class=HTMLResponse)
async def palymarket_activity(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    is_admin = session.link_type == "admin"
    activity = await palymarket_svc.get_recent_activity(limit=50)
    pending_count = len(await palymarket_svc.list_pending_markets()) if is_admin else 0
    return templates.TemplateResponse(request, "palymarket_activity.html", {
        "activity": activity,
        "is_admin": is_admin,
        "pending_count": pending_count,
        "active_tab": "activity",
    })


@router.get("/palymarket/propose", response_class=HTMLResponse)
async def palymarket_propose_form(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    is_admin = session.link_type == "admin"
    pending_count = len(await palymarket_svc.list_pending_markets()) if is_admin else 0
    return templates.TemplateResponse(request, "palymarket_propose.html", {
        "is_admin": is_admin,
        "pending_count": pending_count,
        "active_tab": "propose",
    })


@router.post("/palymarket/propose")
async def palymarket_propose_submit(
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    title = title.strip()[:120]
    description = description.strip()[:500]
    if not title:
        return RedirectResponse(url="/palymarket/propose?error=title_required", status_code=303)
    await palymarket_svc.propose_market(title, description, session.user_id)
    return RedirectResponse(url="/palymarket?proposed=1", status_code=303)
```

- [ ] **Step 3: Update the `palymarket_detail` handler**

Replace the existing `palymarket_detail` function (lines 794–808) with:

```python
@router.get("/palymarket/{market_id}", response_class=HTMLResponse)
async def palymarket_detail(request: Request, market_id: int):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    market = await palymarket_svc.get_market(market_id)
    if market is None:
        return templates.TemplateResponse(request, "expired.html")
    is_admin = session.link_type == "admin"
    bets = await palymarket_svc.get_bets_for_market_with_names(market_id)
    player_bet = await palymarket_svc.get_player_bet(market_id, session.user_id)
    balance = await palymarket_svc.get_palycoin_balance(session.user_id)
    pending_count = len(await palymarket_svc.list_pending_markets()) if is_admin else 0

    total = market.yes_pool + market.no_pool
    yes_pct = round(market.yes_pool / total * 100) if total > 0 else 50
    no_pct = 100 - yes_pct

    history = await palymarket_svc.get_probability_history(market_id)
    if len(history) >= 2:
        t_min = history[0][1].timestamp()
        t_max = history[-1][1].timestamp()
        t_range = t_max - t_min or 1.0
        svg_points = " ".join(
            f"{round((ts.timestamp() - t_min) / t_range * 580 + 10)},"
            f"{round((1.0 - pct) * 100 + 10)}"
            for pct, ts in history
        )
    else:
        svg_points = None

    return templates.TemplateResponse(request, "palymarket_detail.html", {
        "market": market,
        "bets": bets,
        "player_bet": player_bet,
        "balance": balance,
        "is_admin": is_admin,
        "pending_count": pending_count,
        "yes_pct": yes_pct,
        "no_pct": no_pct,
        "svg_points": svg_points,
        "active_tab": None,
        "error": request.query_params.get("error"),
    })
```

- [ ] **Step 4: Update `palymarket_pending` to pass sub-nav context**

Replace the existing `palymarket_pending` function (lines 735–743) with:

```python
@router.get("/palymarket/pending", response_class=HTMLResponse)
async def palymarket_pending(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    if session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html")
    markets = await palymarket_svc.list_pending_markets()
    return templates.TemplateResponse(request, "palymarket_pending.html", {
        "markets": markets,
        "is_admin": True,
        "pending_count": len(markets),
        "active_tab": "pending",
    })
```

- [ ] **Step 5: Run full test suite**

```
.venv/bin/python -m pytest tests/ -q
```

Expected: all tests pass (route tests should still work since function signatures haven't changed in breaking ways)

- [ ] **Step 6: Commit**

```bash
git add src/superpal/webapp/routes.py
git commit -m "feat: add palymarket portfolio/activity/propose routes; update list and detail handlers"
```

---

### Task 5: Redesign `palymarket_list.html` and `palymarket_detail.html`

**Files:**
- Modify: `src/superpal/webapp/templates/palymarket_list.html`
- Modify: `src/superpal/webapp/templates/palymarket_detail.html`

The sub-nav macro is an inline HTML block repeated in every template (no Jinja2 inheritance). It uses `active_tab` to highlight the current page.

- [ ] **Step 1: Replace `palymarket_list.html`**

Overwrite the file entirely with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Palymarket — Prediction Markets</title>
  <link rel="icon" href="/static/favicon.ico">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1e1f22; color: #dcddde; font-family: sans-serif; min-height: 100vh; }
    .topbar { display: flex; align-items: center; gap: 16px; padding: 16px 24px;
              border-bottom: 1px solid #3f4147; flex-wrap: wrap; }
    .topbar h1 { font-size: 1.3rem; font-weight: 700; }
    .topbar-sub { color: #72767d; font-size: 12px; }
    .balance-pill { margin-left: auto; background: #2b2d31; border: 1px solid #3f4147;
                    border-radius: 20px; padding: 6px 14px; font-size: 13px;
                    font-weight: 600; color: #faa61a; }
    .subnav { display: flex; gap: 0; border-bottom: 1px solid #3f4147;
              padding: 0 24px; background: #1e1f22; }
    .subnav-link { display: inline-block; padding: 12px 16px; font-size: 13px;
                   font-weight: 600; color: #72767d; text-decoration: none;
                   border-bottom: 2px solid transparent; margin-bottom: -1px; }
    .subnav-link:hover { color: #dcddde; }
    .subnav-link.active { color: #fff; border-bottom-color: #5865f2; }
    .badge { display: inline-block; background: #ed4245; color: #fff; border-radius: 10px;
             font-size: 10px; font-weight: 700; padding: 1px 5px; margin-left: 4px;
             vertical-align: middle; }
    .page { padding: 24px; max-width: 900px; }
    .flash { padding: 10px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
    .flash-success { background: #3ba55c20; border: 1px solid #3ba55c40; color: #3ba55c; }
    .flash-error { background: #ed424520; border: 1px solid #ed424540; color: #ed4245; }
    .section-label { font-size: 11px; font-weight: 700; letter-spacing: 1px;
                     text-transform: uppercase; color: #72767d; margin: 24px 0 12px; }
    .market-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
                   gap: 12px; }
    .market-card { background: #2b2d31; border: 1px solid #3f4147; border-radius: 8px;
                   padding: 16px; text-decoration: none; display: block; color: inherit;
                   transition: border-color 0.1s; }
    .market-card:hover { border-color: #5865f2; }
    .market-card.muted { opacity: 0.65; }
    .mc-title { font-size: 14px; font-weight: 700; color: #fff; margin-bottom: 6px;
                line-height: 1.3; }
    .mc-desc { font-size: 11px; color: #72767d; margin-bottom: 12px;
               line-height: 1.4; max-height: 2.8em; overflow: hidden; }
    .prob-bar-wrap { margin-bottom: 8px; }
    .prob-bar { display: flex; height: 6px; border-radius: 3px; overflow: hidden; margin-bottom: 4px; }
    .prob-bar-yes { background: #3ba55c; }
    .prob-bar-no { background: #ed4245; }
    .prob-labels { display: flex; justify-content: space-between; font-size: 12px; font-weight: 700; }
    .prob-yes { color: #3ba55c; }
    .prob-no { color: #ed4245; }
    .mc-footer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
                 margin-top: 10px; font-size: 11px; }
    .status-badge { padding: 2px 7px; border-radius: 3px; font-size: 10px; font-weight: 700; }
    .status-open { background: #3ba55c20; color: #3ba55c; }
    .status-closed { background: #faa61a20; color: #faa61a; }
    .status-resolved { background: #5865f220; color: #5865f2; }
    .volume-note { color: #72767d; }
    .my-pos { background: #5865f220; color: #96a0ff; border-radius: 3px; padding: 2px 7px; }
    .empty { color: #72767d; font-size: 13px; font-style: italic; padding: 20px 0; }
  </style>
</head>
<body>
  <div class="topbar">
    <div>
      <h1>Palymarket</h1>
      <div class="topbar-sub">Prediction markets — bet Palycoins on outcomes</div>
    </div>
    <div class="balance-pill">{{ balance }} Palycoins</div>
  </div>

  <nav class="subnav">
    <a class="subnav-link {% if active_tab == 'markets' %}active{% endif %}" href="/palymarket">Markets</a>
    <a class="subnav-link {% if active_tab == 'portfolio' %}active{% endif %}" href="/palymarket/portfolio">Portfolio</a>
    <a class="subnav-link {% if active_tab == 'activity' %}active{% endif %}" href="/palymarket/activity">Activity</a>
    <a class="subnav-link {% if active_tab == 'propose' %}active{% endif %}" href="/palymarket/propose">Propose</a>
    {% if is_admin %}
    <a class="subnav-link {% if active_tab == 'pending' %}active{% endif %}" href="/palymarket/pending">
      Pending{% if pending_count %}<span class="badge">{{ pending_count }}</span>{% endif %}
    </a>
    {% endif %}
  </nav>

  <div class="page">
    {% if proposed %}
    <div class="flash flash-success">Market submitted for admin review.</div>
    {% endif %}
    {% if error %}
    <div class="flash flash-error">Error: {{ error | replace('_', ' ') }}</div>
    {% endif %}

    {% set open_markets = markets | selectattr("status", "equalto", "open") | list %}
    {% set closed_markets = markets | selectattr("status", "equalto", "closed") | list %}
    {% set resolved_markets = markets | selectattr("status", "equalto", "resolved") | list %}

    {% if open_markets %}
    <div class="section-label">Open Markets</div>
    <div class="market-grid">
      {% for m in open_markets %}
      <a class="market-card" href="/palymarket/{{ m.id }}">
        <div class="mc-title">{{ m.title }}</div>
        {% if m.description %}<div class="mc-desc">{{ m.description }}</div>{% endif %}
        <div class="prob-bar-wrap">
          <div class="prob-bar">
            <div class="prob-bar-yes" style="width: {{ m._yes_pct }}%"></div>
            <div class="prob-bar-no" style="width: {{ 100 - m._yes_pct }}%"></div>
          </div>
          <div class="prob-labels">
            <span class="prob-yes">{{ m._yes_pct }}% YES</span>
            <span class="prob-no">{{ 100 - m._yes_pct }}% NO</span>
          </div>
        </div>
        <div class="mc-footer">
          <span class="status-badge status-open">OPEN</span>
          <span class="volume-note">{{ m.yes_pool + m.no_pool }} vol</span>
          {% if m.id in bet_map %}
          <span class="my-pos">You: {{ bet_map[m.id].side | upper }} {{ bet_map[m.id].amount }}</span>
          {% endif %}
        </div>
      </a>
      {% endfor %}
    </div>
    {% endif %}

    {% if closed_markets %}
    <div class="section-label">Awaiting Resolution</div>
    <div class="market-grid">
      {% for m in closed_markets %}
      <a class="market-card" href="/palymarket/{{ m.id }}">
        <div class="mc-title">{{ m.title }}</div>
        {% if m.description %}<div class="mc-desc">{{ m.description }}</div>{% endif %}
        <div class="prob-bar-wrap">
          <div class="prob-bar">
            <div class="prob-bar-yes" style="width: {{ m._yes_pct }}%"></div>
            <div class="prob-bar-no" style="width: {{ 100 - m._yes_pct }}%"></div>
          </div>
          <div class="prob-labels">
            <span class="prob-yes">{{ m._yes_pct }}% YES</span>
            <span class="prob-no">{{ 100 - m._yes_pct }}% NO</span>
          </div>
        </div>
        <div class="mc-footer">
          <span class="status-badge status-closed">CLOSED</span>
          <span class="volume-note">{{ m.yes_pool + m.no_pool }} vol</span>
          {% if m.id in bet_map %}
          <span class="my-pos">You: {{ bet_map[m.id].side | upper }} {{ bet_map[m.id].amount }}</span>
          {% endif %}
        </div>
      </a>
      {% endfor %}
    </div>
    {% endif %}

    {% if resolved_markets %}
    <div class="section-label">Resolved</div>
    <div class="market-grid">
      {% for m in resolved_markets %}
      <a class="market-card muted" href="/palymarket/{{ m.id }}">
        <div class="mc-title">{{ m.title }}</div>
        <div class="mc-footer">
          <span class="status-badge status-resolved">{{ m.outcome | upper }} WON</span>
          <span class="volume-note">{{ m.yes_pool + m.no_pool }} vol</span>
        </div>
      </a>
      {% endfor %}
    </div>
    {% endif %}

    {% if not open_markets and not closed_markets and not resolved_markets %}
    <p class="empty">No markets yet. Use <code style="color:#96a0ff">/palymarket-propose</code> in Discord or <a href="/palymarket/propose" style="color:#5865f2">propose one here</a>.</p>
    {% endif %}
  </div>
</body>
</html>
```

- [ ] **Step 2: Replace `palymarket_detail.html`**

Overwrite the file entirely with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ market.title }} — Palymarket</title>
  <link rel="icon" href="/static/favicon.ico">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1e1f22; color: #dcddde; font-family: sans-serif; min-height: 100vh; }
    .topbar { display: flex; align-items: center; gap: 16px; padding: 16px 24px;
              border-bottom: 1px solid #3f4147; }
    .topbar h1 { font-size: 1.3rem; font-weight: 700; }
    .subnav { display: flex; gap: 0; border-bottom: 1px solid #3f4147;
              padding: 0 24px; background: #1e1f22; }
    .subnav-link { display: inline-block; padding: 12px 16px; font-size: 13px;
                   font-weight: 600; color: #72767d; text-decoration: none;
                   border-bottom: 2px solid transparent; margin-bottom: -1px; }
    .subnav-link:hover { color: #dcddde; }
    .subnav-link.active { color: #fff; border-bottom-color: #5865f2; }
    .badge { display: inline-block; background: #ed4245; color: #fff; border-radius: 10px;
             font-size: 10px; font-weight: 700; padding: 1px 5px; margin-left: 4px;
             vertical-align: middle; }
    .page { padding: 24px; max-width: 800px; }
    .back { color: #72767d; text-decoration: none; font-size: 12px; display: inline-block;
            margin-bottom: 16px; }
    .back:hover { color: #dcddde; }
    .market-title { font-size: 1.5rem; font-weight: 700; margin-bottom: 6px; line-height: 1.3; }
    .market-desc { color: #72767d; font-size: 13px; line-height: 1.5; margin-bottom: 12px; }
    .status-badge { display: inline-block; padding: 3px 10px; border-radius: 3px;
                    font-size: 11px; font-weight: 700; margin-bottom: 20px; }
    .status-open { background: #3ba55c20; color: #3ba55c; }
    .status-closed { background: #faa61a20; color: #faa61a; }
    .status-resolved { background: #5865f220; color: #5865f2; }
    .prob-section { display: flex; gap: 0; margin-bottom: 20px; border-radius: 8px;
                    overflow: hidden; border: 1px solid #3f4147; }
    .prob-box { flex: 1; padding: 20px 16px; text-align: center; }
    .prob-box-yes { background: #3ba55c12; border-right: 1px solid #3f4147; }
    .prob-box-no { background: #ed424512; }
    .prob-pct { font-size: 2.2rem; font-weight: 800; line-height: 1; }
    .prob-pct-yes { color: #3ba55c; }
    .prob-pct-no { color: #ed4245; }
    .prob-label { font-size: 11px; font-weight: 700; letter-spacing: 1px;
                  text-transform: uppercase; color: #72767d; margin-top: 4px; }
    .prob-sub { font-size: 11px; color: #72767d; margin-top: 2px; }
    .prob-bar { display: flex; height: 8px; border-radius: 0; margin-bottom: 20px; }
    .prob-bar-yes { background: #3ba55c; }
    .prob-bar-no { background: #ed4245; }
    .chart-section { background: #2b2d31; border: 1px solid #3f4147; border-radius: 8px;
                     padding: 16px; margin-bottom: 16px; }
    .chart-title { font-size: 11px; font-weight: 700; letter-spacing: 1px;
                   text-transform: uppercase; color: #72767d; margin-bottom: 10px; }
    .chart-empty { font-size: 12px; color: #72767d; font-style: italic; padding: 20px 0; text-align: center; }
    svg.prob-chart { width: 100%; height: auto; display: block; }
    .outcome-banner { background: #5865f220; border: 1px solid #5865f240; border-radius: 8px;
                      padding: 14px 18px; margin-bottom: 16px; font-size: 14px; font-weight: 700;
                      color: #96a0ff; text-align: center; }
    .section { background: #2b2d31; border: 1px solid #3f4147; border-radius: 8px;
               padding: 16px; margin-bottom: 16px; }
    .section-title { font-size: 11px; font-weight: 700; letter-spacing: 1px;
                     text-transform: uppercase; color: #72767d; margin-bottom: 12px; }
    .balance-note { font-size: 12px; color: #72767d; margin-bottom: 10px; }
    .current-pos { background: #313338; border-radius: 6px; padding: 10px 14px;
                   font-size: 13px; margin-bottom: 12px; }
    .side-toggle { display: flex; gap: 0; margin-bottom: 10px; border-radius: 6px; overflow: hidden;
                   border: 1px solid #3f4147; width: fit-content; }
    .side-toggle label { padding: 7px 20px; font-size: 13px; font-weight: 700; cursor: pointer;
                         transition: background 0.1s; }
    .side-toggle input[type="radio"] { display: none; }
    .side-toggle input[value="yes"]:checked + label { background: #3ba55c; color: #fff; }
    .side-toggle input[value="no"]:checked + label { background: #ed4245; color: #fff; }
    .side-toggle label { background: #313338; color: #72767d; }
    .side-toggle label:hover { background: #3f4147; color: #dcddde; }
    .bet-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    input[type="number"] { background: #313338; border: 1px solid #3f4147; border-radius: 4px;
                           color: #dcddde; padding: 7px 10px; font-size: 13px; width: 110px; }
    .btn { display: inline-block; padding: 7px 16px; border-radius: 4px;
           font-size: 13px; font-weight: 600; cursor: pointer; border: none; }
    .btn-primary { background: #5865f2; color: #fff; }
    .btn-primary:hover { background: #4752c4; }
    .btn-warning { background: #faa61a; color: #000; }
    .btn-warning:hover { background: #e09416; }
    .payout-preview { font-size: 12px; color: #96a0ff; margin-top: 8px; min-height: 16px; }
    .admin-divider { margin-top: 14px; padding-top: 14px; border-top: 1px solid #3f4147;
                     display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .admin-label { font-size: 11px; font-weight: 700; color: #72767d;
                   letter-spacing: 1px; text-transform: uppercase; }
    .resolve-form { display: flex; gap: 8px; align-items: center; }
    select { background: #313338; border: 1px solid #3f4147; border-radius: 4px;
             color: #dcddde; padding: 7px 10px; font-size: 13px; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th { color: #72767d; text-align: left; padding: 6px 8px;
         border-bottom: 1px solid #3f4147; font-weight: 600; }
    td { padding: 7px 8px; border-bottom: 1px solid #3f4147; color: #b9bbbe; }
    tr:last-child td { border-bottom: none; }
    .td-yes { color: #3ba55c; font-weight: 700; }
    .td-no { color: #ed4245; font-weight: 700; }
    .flash-error { background: #ed424520; border: 1px solid #ed424540; color: #ed4245;
                   padding: 10px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
  </style>
</head>
<body>
  <div class="topbar">
    <div><h1>Palymarket</h1></div>
  </div>
  <nav class="subnav">
    <a class="subnav-link" href="/palymarket">Markets</a>
    <a class="subnav-link" href="/palymarket/portfolio">Portfolio</a>
    <a class="subnav-link" href="/palymarket/activity">Activity</a>
    <a class="subnav-link" href="/palymarket/propose">Propose</a>
    {% if is_admin %}
    <a class="subnav-link" href="/palymarket/pending">
      Pending{% if pending_count %}<span class="badge">{{ pending_count }}</span>{% endif %}
    </a>
    {% endif %}
  </nav>

  <div class="page">
    <a class="back" href="/palymarket">← Markets</a>

    {% if error %}
    <div class="flash-error">Error: {{ error | replace('_', ' ') }}</div>
    {% endif %}

    <div class="market-title">{{ market.title }}</div>
    {% if market.description %}<div class="market-desc">{{ market.description }}</div>{% endif %}

    {% if market.status == "open" %}<span class="status-badge status-open">OPEN</span>
    {% elif market.status == "closed" %}<span class="status-badge status-closed">AWAITING RESOLUTION</span>
    {% elif market.status == "resolved" %}<span class="status-badge status-resolved">RESOLVED</span>
    {% else %}<span class="status-badge" style="background:#3f414780;color:#72767d;">{{ market.status | upper }}</span>
    {% endif %}

    {% if market.status == "resolved" %}
    <div class="outcome-banner">
      Outcome: <strong>{{ market.outcome | upper }}</strong>
      {% if market.outcome == "yes" %} — YES wins!{% else %} — NO wins!{% endif %}
    </div>
    {% endif %}

    <div class="prob-section">
      <div class="prob-box prob-box-yes">
        <div class="prob-pct prob-pct-yes">{{ yes_pct }}%</div>
        <div class="prob-label">YES</div>
        <div class="prob-sub">{{ market.yes_pool }} Palycoins</div>
      </div>
      <div class="prob-box prob-box-no">
        <div class="prob-pct prob-pct-no">{{ no_pct }}%</div>
        <div class="prob-label">NO</div>
        <div class="prob-sub">{{ market.no_pool }} Palycoins</div>
      </div>
    </div>
    <div class="prob-bar">
      <div class="prob-bar-yes" style="width: {{ yes_pct }}%"></div>
      <div class="prob-bar-no" style="width: {{ no_pct }}%"></div>
    </div>

    <div class="chart-section">
      <div class="chart-title">Probability History</div>
      {% if svg_points %}
      <svg class="prob-chart" viewBox="0 0 600 120" xmlns="http://www.w3.org/2000/svg">
        <line x1="10" y1="60" x2="590" y2="60"
              stroke="#3f4147" stroke-width="1" stroke-dasharray="4,4"/>
        <polyline points="{{ svg_points }}"
                  fill="none" stroke="#5865f2" stroke-width="1.5" stroke-linejoin="round"/>
      </svg>
      {% else %}
      <div class="chart-empty">No chart data yet — probability history builds as bets are placed.</div>
      {% endif %}
    </div>

    {% if market.status == "open" %}
    <div class="section">
      <div class="section-title">{% if player_bet %}Update Your Bet{% else %}Place a Bet{% endif %}</div>
      <div class="balance-note">Balance: {{ balance }} Palycoins</div>
      {% if player_bet %}
      <div class="current-pos">
        Current position:
        <span class="{% if player_bet.side == 'yes' %}td-yes{% else %}td-no{% endif %}">
          {{ player_bet.side | upper }}
        </span>
        &mdash; {{ player_bet.amount }} Palycoins
      </div>
      {% endif %}
      <form method="post" action="/palymarket/{{ market.id }}/bet"
            id="bet-form"
            data-yes-pool="{{ market.yes_pool }}"
            data-no-pool="{{ market.no_pool }}"
            data-old-amount="{{ player_bet.amount if player_bet else 0 }}"
            data-old-side="{{ player_bet.side if player_bet else '' }}">
        <div class="side-toggle">
          <input type="radio" name="side" id="side-yes" value="yes"
                 {% if not player_bet or player_bet.side == "yes" %}checked{% endif %}>
          <label for="side-yes">YES</label>
          <input type="radio" name="side" id="side-no" value="no"
                 {% if player_bet and player_bet.side == "no" %}checked{% endif %}>
          <label for="side-no">NO</label>
        </div>
        <div class="bet-row">
          <input type="number" name="amount" id="bet-amount" min="1"
                 value="{{ player_bet.amount if player_bet else 1 }}" required>
          <button class="btn btn-primary" type="submit">
            {% if player_bet %}Update Bet{% else %}Place Bet{% endif %}
          </button>
        </div>
        <div class="payout-preview" id="payout-preview"></div>
      </form>
      {% if is_admin %}
      <div class="admin-divider">
        <span class="admin-label">Admin</span>
        <form method="post" action="/palymarket/{{ market.id }}/close">
          <button class="btn btn-warning" type="submit">Close Market</button>
        </form>
      </div>
      {% endif %}
    </div>
    {% elif market.status == "closed" and is_admin %}
    <div class="section">
      <div class="section-title">Admin — Resolve Market</div>
      <form method="post" action="/palymarket/{{ market.id }}/resolve" class="resolve-form">
        <select name="outcome">
          <option value="yes">YES</option>
          <option value="no">NO</option>
        </select>
        <button class="btn btn-primary" type="submit">Resolve</button>
      </form>
    </div>
    {% endif %}

    {% if bets %}
    <div class="section">
      <div class="section-title">All Bets ({{ bets | length }})</div>
      <table>
        <thead>
          <tr><th>Player</th><th>Side</th><th>Amount</th><th>Time</th></tr>
        </thead>
        <tbody>
          {% for bet in bets %}
          <tr>
            <td>{{ bet.display_name }}</td>
            <td class="td-{{ bet.side }}">{{ bet.side | upper }}</td>
            <td>{{ bet.amount }}</td>
            <td style="color:#72767d">{{ bet.placed_at.strftime('%b %d %H:%M') }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}
  </div>

  <script>
    const form = document.getElementById('bet-form');
    if (form) {
      const yesPool = parseInt(form.dataset.yesPool);
      const noPool = parseInt(form.dataset.noPool);
      const oldAmount = parseInt(form.dataset.oldAmount) || 0;
      const oldSide = form.dataset.oldSide;
      const preview = document.getElementById('payout-preview');

      function updatePreview() {
        const side = document.querySelector('input[name="side"]:checked').value;
        const amount = parseInt(document.getElementById('bet-amount').value) || 0;
        if (amount <= 0) { preview.textContent = ''; return; }
        let yp = yesPool, np = noPool;
        if (oldSide === 'yes') yp -= oldAmount;
        else if (oldSide === 'no') np -= oldAmount;
        if (side === 'yes') yp += amount;
        else np += amount;
        const winning = side === 'yes' ? yp : np;
        const total = yp + np;
        const payout = winning > 0 ? Math.floor(amount / winning * total) : amount;
        preview.textContent = `If ${side.toUpperCase()} wins → ~${payout} Palycoins`;
      }

      document.querySelectorAll('input[name="side"], #bet-amount')
        .forEach(el => el.addEventListener('input', updatePreview));
      updatePreview();
    }
  </script>
</body>
</html>
```

- [ ] **Step 3: Start the app and visually verify**

```
cd src && ../.venv/bin/python bot.py
```

Navigate to `/palymarket` in a browser with a valid session. Verify:
- Sub-nav bar shows Markets / Portfolio / Activity / Propose (and Pending if admin)
- "Markets" tab is active/highlighted
- Open markets show probability bars and YES%/NO% labels
- Flash banner appears when `?proposed=1` is in URL

Navigate to `/palymarket/{id}` for a market with bets. Verify:
- Two large probability numbers displayed
- Probability bar shown
- SVG chart appears if ≥2 bets placed (shows "No chart data yet" otherwise)
- YES/NO toggle buttons in bet form
- Payout preview updates as you type

- [ ] **Step 4: Commit**

```bash
git add src/superpal/webapp/templates/palymarket_list.html \
        src/superpal/webapp/templates/palymarket_detail.html
git commit -m "feat: redesign palymarket list and detail pages with probability UI"
```

---

### Task 6: New templates — portfolio, activity, propose; update pending

**Files:**
- Create: `src/superpal/webapp/templates/palymarket_portfolio.html`
- Create: `src/superpal/webapp/templates/palymarket_activity.html`
- Create: `src/superpal/webapp/templates/palymarket_propose.html`
- Modify: `src/superpal/webapp/templates/palymarket_pending.html`

- [ ] **Step 1: Create `palymarket_portfolio.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Portfolio — Palymarket</title>
  <link rel="icon" href="/static/favicon.ico">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1e1f22; color: #dcddde; font-family: sans-serif; min-height: 100vh; }
    .topbar { display: flex; align-items: center; gap: 16px; padding: 16px 24px;
              border-bottom: 1px solid #3f4147; }
    .topbar h1 { font-size: 1.3rem; font-weight: 700; }
    .subnav { display: flex; gap: 0; border-bottom: 1px solid #3f4147;
              padding: 0 24px; background: #1e1f22; }
    .subnav-link { display: inline-block; padding: 12px 16px; font-size: 13px;
                   font-weight: 600; color: #72767d; text-decoration: none;
                   border-bottom: 2px solid transparent; margin-bottom: -1px; }
    .subnav-link:hover { color: #dcddde; }
    .subnav-link.active { color: #fff; border-bottom-color: #5865f2; }
    .badge { display: inline-block; background: #ed4245; color: #fff; border-radius: 10px;
             font-size: 10px; font-weight: 700; padding: 1px 5px; margin-left: 4px;
             vertical-align: middle; }
    .page { padding: 24px; max-width: 800px; }
    .summary { display: flex; gap: 12px; margin-bottom: 24px; flex-wrap: wrap; }
    .summary-card { background: #2b2d31; border: 1px solid #3f4147; border-radius: 8px;
                    padding: 14px 20px; min-width: 140px; }
    .summary-val { font-size: 1.6rem; font-weight: 700; color: #fff; }
    .summary-label { font-size: 11px; color: #72767d; margin-top: 2px;
                     text-transform: uppercase; letter-spacing: 1px; font-weight: 700; }
    .section-label { font-size: 11px; font-weight: 700; letter-spacing: 1px;
                     text-transform: uppercase; color: #72767d; margin: 20px 0 12px; }
    .pos-card { background: #2b2d31; border: 1px solid #3f4147; border-radius: 8px;
                padding: 14px 16px; margin-bottom: 8px; display: flex;
                align-items: center; gap: 14px; flex-wrap: wrap; }
    .pos-title { font-size: 13px; font-weight: 700; color: #fff; flex: 1; min-width: 140px; }
    .pos-title a { color: inherit; text-decoration: none; }
    .pos-title a:hover { color: #5865f2; }
    .pos-side-yes { color: #3ba55c; font-size: 12px; font-weight: 700; }
    .pos-side-no { color: #ed4245; font-size: 12px; font-weight: 700; }
    .pos-pct { font-size: 12px; color: #72767d; }
    .pos-payout { font-size: 12px; color: #96a0ff; }
    .pos-amount { font-size: 12px; color: #b9bbbe; }
    .res-card { background: #2b2d31; border: 1px solid #3f4147; border-radius: 8px;
                padding: 12px 16px; margin-bottom: 8px; display: flex;
                align-items: center; gap: 14px; flex-wrap: wrap; opacity: 0.8; }
    .res-title { font-size: 13px; color: #b9bbbe; flex: 1; min-width: 140px; }
    .res-title a { color: inherit; text-decoration: none; }
    .res-title a:hover { color: #5865f2; }
    .win-badge { padding: 2px 8px; border-radius: 3px; font-size: 10px; font-weight: 700; }
    .win-yes { background: #3ba55c20; color: #3ba55c; }
    .win-no { background: #ed424520; color: #ed4245; }
    .empty { color: #72767d; font-size: 13px; font-style: italic; padding: 12px 0; }
  </style>
</head>
<body>
  <div class="topbar"><div><h1>Palymarket</h1></div></div>
  <nav class="subnav">
    <a class="subnav-link {% if active_tab == 'markets' %}active{% endif %}" href="/palymarket">Markets</a>
    <a class="subnav-link {% if active_tab == 'portfolio' %}active{% endif %}" href="/palymarket/portfolio">Portfolio</a>
    <a class="subnav-link {% if active_tab == 'activity' %}active{% endif %}" href="/palymarket/activity">Activity</a>
    <a class="subnav-link {% if active_tab == 'propose' %}active{% endif %}" href="/palymarket/propose">Propose</a>
    {% if is_admin %}
    <a class="subnav-link {% if active_tab == 'pending' %}active{% endif %}" href="/palymarket/pending">
      Pending{% if pending_count %}<span class="badge">{{ pending_count }}</span>{% endif %}
    </a>
    {% endif %}
  </nav>

  <div class="page">
    <div class="summary">
      <div class="summary-card">
        <div class="summary-val">{{ active | length }}</div>
        <div class="summary-label">Open Positions</div>
      </div>
      <div class="summary-card">
        <div class="summary-val">{{ total_staked }}</div>
        <div class="summary-label">Palycoins Staked</div>
      </div>
    </div>

    <div class="section-label">Active Positions</div>
    {% if active %}
    {% for pos in active %}
    <div class="pos-card">
      <div class="pos-title"><a href="/palymarket/{{ pos.market.id }}">{{ pos.market.title }}</a></div>
      <span class="pos-side-{{ pos.side }}">{{ pos.side | upper }} {{ pos.amount }}</span>
      <span class="pos-pct">{{ pos.yes_pct }}% YES</span>
      <span class="pos-payout">~{{ pos.estimated_payout }} if {{ pos.side | upper }} wins</span>
    </div>
    {% endfor %}
    {% else %}
    <p class="empty">No active positions. <a href="/palymarket" style="color:#5865f2">Browse markets</a> to place a bet.</p>
    {% endif %}

    {% if resolved %}
    <div class="section-label">Resolved History</div>
    {% for r in resolved %}
    <div class="res-card">
      <div class="res-title"><a href="/palymarket/{{ r.market.id }}">{{ r.market.title }}</a></div>
      <span class="pos-side-{{ r.side }}">{{ r.side | upper }} {{ r.amount }}</span>
      <span class="win-badge {% if r.won %}win-yes{% else %}win-no{% endif %}">
        {% if r.won %}WON +{{ r.amount_returned }}{% else %}LOST{% endif %}
      </span>
    </div>
    {% endfor %}
    {% endif %}
  </div>
</body>
</html>
```

- [ ] **Step 2: Create `palymarket_activity.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Activity — Palymarket</title>
  <link rel="icon" href="/static/favicon.ico">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1e1f22; color: #dcddde; font-family: sans-serif; min-height: 100vh; }
    .topbar { display: flex; align-items: center; gap: 16px; padding: 16px 24px;
              border-bottom: 1px solid #3f4147; }
    .topbar h1 { font-size: 1.3rem; font-weight: 700; }
    .subnav { display: flex; gap: 0; border-bottom: 1px solid #3f4147;
              padding: 0 24px; background: #1e1f22; }
    .subnav-link { display: inline-block; padding: 12px 16px; font-size: 13px;
                   font-weight: 600; color: #72767d; text-decoration: none;
                   border-bottom: 2px solid transparent; margin-bottom: -1px; }
    .subnav-link:hover { color: #dcddde; }
    .subnav-link.active { color: #fff; border-bottom-color: #5865f2; }
    .badge { display: inline-block; background: #ed4245; color: #fff; border-radius: 10px;
             font-size: 10px; font-weight: 700; padding: 1px 5px; margin-left: 4px;
             vertical-align: middle; }
    .page { padding: 24px; max-width: 700px; }
    .feed-row { display: flex; align-items: baseline; gap: 8px; padding: 10px 0;
                border-bottom: 1px solid #2b2d31; font-size: 13px; flex-wrap: wrap; }
    .feed-row:last-child { border-bottom: none; }
    .feed-name { font-weight: 700; color: #fff; }
    .feed-action { color: #b9bbbe; }
    .feed-yes { color: #3ba55c; font-weight: 700; }
    .feed-no { color: #ed4245; font-weight: 700; }
    .feed-market a { color: #5865f2; text-decoration: none; }
    .feed-market a:hover { color: #96a0ff; }
    .feed-time { margin-left: auto; color: #72767d; font-size: 11px; white-space: nowrap; }
    .empty { color: #72767d; font-size: 13px; font-style: italic; padding: 12px 0; }
  </style>
</head>
<body>
  <div class="topbar"><div><h1>Palymarket</h1></div></div>
  <nav class="subnav">
    <a class="subnav-link {% if active_tab == 'markets' %}active{% endif %}" href="/palymarket">Markets</a>
    <a class="subnav-link {% if active_tab == 'portfolio' %}active{% endif %}" href="/palymarket/portfolio">Portfolio</a>
    <a class="subnav-link {% if active_tab == 'activity' %}active{% endif %}" href="/palymarket/activity">Activity</a>
    <a class="subnav-link {% if active_tab == 'propose' %}active{% endif %}" href="/palymarket/propose">Propose</a>
    {% if is_admin %}
    <a class="subnav-link {% if active_tab == 'pending' %}active{% endif %}" href="/palymarket/pending">
      Pending{% if pending_count %}<span class="badge">{{ pending_count }}</span>{% endif %}
    </a>
    {% endif %}
  </nav>

  <div class="page">
    {% if activity %}
    {% for row in activity %}
    <div class="feed-row">
      <span class="feed-name">{{ row.display_name }}</span>
      <span class="feed-action">bet {{ row.amount }} on</span>
      <span class="feed-{{ row.side }}">{{ row.side | upper }}</span>
      <span class="feed-market">on <a href="/palymarket/{{ row.market_id }}">{{ row.market_title }}</a></span>
      <span class="feed-time">{{ row.placed_at.strftime('%b %d %H:%M') }}</span>
    </div>
    {% endfor %}
    {% else %}
    <p class="empty">No betting activity yet.</p>
    {% endif %}
  </div>
</body>
</html>
```

- [ ] **Step 3: Create `palymarket_propose.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Propose Market — Palymarket</title>
  <link rel="icon" href="/static/favicon.ico">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1e1f22; color: #dcddde; font-family: sans-serif; min-height: 100vh; }
    .topbar { display: flex; align-items: center; gap: 16px; padding: 16px 24px;
              border-bottom: 1px solid #3f4147; }
    .topbar h1 { font-size: 1.3rem; font-weight: 700; }
    .subnav { display: flex; gap: 0; border-bottom: 1px solid #3f4147;
              padding: 0 24px; background: #1e1f22; }
    .subnav-link { display: inline-block; padding: 12px 16px; font-size: 13px;
                   font-weight: 600; color: #72767d; text-decoration: none;
                   border-bottom: 2px solid transparent; margin-bottom: -1px; }
    .subnav-link:hover { color: #dcddde; }
    .subnav-link.active { color: #fff; border-bottom-color: #5865f2; }
    .badge { display: inline-block; background: #ed4245; color: #fff; border-radius: 10px;
             font-size: 10px; font-weight: 700; padding: 1px 5px; margin-left: 4px;
             vertical-align: middle; }
    .page { padding: 24px; max-width: 540px; }
    .propose-card { background: #2b2d31; border: 1px solid #3f4147; border-radius: 8px;
                    padding: 24px; }
    .propose-title { font-size: 15px; font-weight: 700; margin-bottom: 4px; }
    .propose-sub { font-size: 12px; color: #72767d; margin-bottom: 20px; line-height: 1.5; }
    .field { margin-bottom: 16px; }
    label { display: block; font-size: 11px; font-weight: 700; letter-spacing: 1px;
            text-transform: uppercase; color: #72767d; margin-bottom: 6px; }
    input[type="text"], textarea {
      width: 100%; background: #313338; border: 1px solid #3f4147; border-radius: 4px;
      color: #dcddde; padding: 8px 12px; font-size: 13px; font-family: inherit; }
    input[type="text"]:focus, textarea:focus {
      outline: none; border-color: #5865f2; }
    textarea { resize: vertical; min-height: 90px; }
    .char-note { font-size: 11px; color: #72767d; margin-top: 4px; }
    .btn { display: inline-block; padding: 8px 20px; border-radius: 4px;
           font-size: 13px; font-weight: 600; cursor: pointer; border: none; }
    .btn-primary { background: #5865f2; color: #fff; }
    .btn-primary:hover { background: #4752c4; }
    .flash-error { background: #ed424520; border: 1px solid #ed424540; color: #ed4245;
                   padding: 10px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
  </style>
</head>
<body>
  <div class="topbar"><div><h1>Palymarket</h1></div></div>
  <nav class="subnav">
    <a class="subnav-link {% if active_tab == 'markets' %}active{% endif %}" href="/palymarket">Markets</a>
    <a class="subnav-link {% if active_tab == 'portfolio' %}active{% endif %}" href="/palymarket/portfolio">Portfolio</a>
    <a class="subnav-link {% if active_tab == 'activity' %}active{% endif %}" href="/palymarket/activity">Activity</a>
    <a class="subnav-link {% if active_tab == 'propose' %}active{% endif %}" href="/palymarket/propose">Propose</a>
    {% if is_admin %}
    <a class="subnav-link {% if active_tab == 'pending' %}active{% endif %}" href="/palymarket/pending">
      Pending{% if pending_count %}<span class="badge">{{ pending_count }}</span>{% endif %}
    </a>
    {% endif %}
  </nav>

  <div class="page">
    {% if request.query_params.get('error') %}
    <div class="flash-error">{{ request.query_params.get('error') | replace('_', ' ') }}</div>
    {% endif %}
    <div class="propose-card">
      <div class="propose-title">Propose a Market</div>
      <div class="propose-sub">
        Submitted markets require admin approval before opening for betting.
        Frame your question as something that can resolve YES or NO.
      </div>
      <form method="post" action="/palymarket/propose">
        <div class="field">
          <label for="title">Question</label>
          <input type="text" id="title" name="title" maxlength="120"
                 placeholder="Will X happen before Y date?" required>
          <div class="char-note">Max 120 characters</div>
        </div>
        <div class="field">
          <label for="description">Description (optional)</label>
          <textarea id="description" name="description" maxlength="500"
                    placeholder="Resolution criteria, context, or relevant details…"></textarea>
          <div class="char-note">Max 500 characters</div>
        </div>
        <button class="btn btn-primary" type="submit">Submit for Review</button>
      </form>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 4: Update `palymarket_pending.html` to add sub-nav**

Replace the existing file with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Pending Approvals — Palymarket</title>
  <link rel="icon" href="/static/favicon.ico">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1e1f22; color: #dcddde; font-family: sans-serif; min-height: 100vh; }
    .topbar { display: flex; align-items: center; gap: 16px; padding: 16px 24px;
              border-bottom: 1px solid #3f4147; }
    .topbar h1 { font-size: 1.3rem; font-weight: 700; }
    .subnav { display: flex; gap: 0; border-bottom: 1px solid #3f4147;
              padding: 0 24px; background: #1e1f22; }
    .subnav-link { display: inline-block; padding: 12px 16px; font-size: 13px;
                   font-weight: 600; color: #72767d; text-decoration: none;
                   border-bottom: 2px solid transparent; margin-bottom: -1px; }
    .subnav-link:hover { color: #dcddde; }
    .subnav-link.active { color: #fff; border-bottom-color: #5865f2; }
    .badge { display: inline-block; background: #ed4245; color: #fff; border-radius: 10px;
             font-size: 10px; font-weight: 700; padding: 1px 5px; margin-left: 4px;
             vertical-align: middle; }
    .page { padding: 24px; max-width: 700px; }
    .section-label { font-size: 11px; font-weight: 700; letter-spacing: 1px;
                     text-transform: uppercase; color: #72767d; margin-bottom: 12px; }
    .pending-card { background: #2b2d31; border: 1px solid #3f4147; border-radius: 8px;
                    padding: 16px; margin-bottom: 10px; }
    .pc-title { font-size: 14px; font-weight: 700; color: #fff; margin-bottom: 4px; }
    .pc-desc { font-size: 12px; color: #72767d; margin-bottom: 12px; line-height: 1.4; }
    .pc-meta { font-size: 11px; color: #72767d; margin-bottom: 10px; }
    .pc-actions { display: flex; gap: 8px; }
    .btn { display: inline-block; padding: 6px 14px; border-radius: 4px;
           font-size: 13px; font-weight: 600; cursor: pointer; border: none; }
    .btn-primary { background: #5865f2; color: #fff; }
    .btn-primary:hover { background: #4752c4; }
    .btn-danger { background: #ed4245; color: #fff; }
    .btn-danger:hover { background: #c03537; }
    .empty { color: #72767d; font-size: 13px; font-style: italic; }
    .flash-error { background: #ed424520; border: 1px solid #ed424540; color: #ed4245;
                   padding: 10px 16px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
  </style>
</head>
<body>
  <div class="topbar"><div><h1>Palymarket</h1></div></div>
  <nav class="subnav">
    <a class="subnav-link" href="/palymarket">Markets</a>
    <a class="subnav-link" href="/palymarket/portfolio">Portfolio</a>
    <a class="subnav-link" href="/palymarket/activity">Activity</a>
    <a class="subnav-link" href="/palymarket/propose">Propose</a>
    <a class="subnav-link active" href="/palymarket/pending">
      Pending{% if pending_count %}<span class="badge">{{ pending_count }}</span>{% endif %}
    </a>
  </nav>

  <div class="page">
    {% if request.query_params.get('error') %}
    <div class="flash-error">Error: {{ request.query_params.get('error') | replace('_', ' ') }}</div>
    {% endif %}

    <div class="section-label">Pending Approval ({{ markets | length }})</div>

    {% if markets %}
    {% for market in markets %}
    <div class="pending-card">
      <div class="pc-title">{{ market.title }}</div>
      {% if market.description %}<div class="pc-desc">{{ market.description }}</div>{% endif %}
      <div class="pc-meta">Proposed by {{ market.created_by }} · {{ market.created_at.strftime('%b %d %Y') }}</div>
      <div class="pc-actions">
        <form method="post" action="/palymarket/{{ market.id }}/approve">
          <button class="btn btn-primary" type="submit">Approve</button>
        </form>
        <form method="post" action="/palymarket/{{ market.id }}/reject">
          <button class="btn btn-danger" type="submit">Reject</button>
        </form>
      </div>
    </div>
    {% endfor %}
    {% else %}
    <p class="empty">No markets awaiting approval.</p>
    {% endif %}
  </div>
</body>
</html>
```

- [ ] **Step 5: Start the app and visually verify all new pages**

```
cd src && ../.venv/bin/python bot.py
```

Verify each page:
- `/palymarket/portfolio` — sub-nav active on Portfolio, summary cards, active/resolved positions
- `/palymarket/activity` — sub-nav active on Activity, feed rows with names and market links
- `/palymarket/propose` — sub-nav active on Propose, form with title/description, submits and redirects to list with flash
- `/palymarket/pending` (admin session) — sub-nav active on Pending with badge count, approve/reject buttons

- [ ] **Step 6: Run full test suite**

```
.venv/bin/python -m pytest tests/ -q
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/superpal/webapp/templates/palymarket_portfolio.html \
        src/superpal/webapp/templates/palymarket_activity.html \
        src/superpal/webapp/templates/palymarket_propose.html \
        src/superpal/webapp/templates/palymarket_pending.html
git commit -m "feat: add portfolio, activity, propose templates; update pending with sub-nav"
```

---

## Spec Coverage Self-Review

| Spec requirement | Task |
|---|---|
| `market_probability_history` table | Task 1 |
| `record_probability_snapshot` called on every bet | Task 2 |
| `get_probability_history` → SVG chart data | Tasks 2, 4 |
| `get_player_portfolio` with active + resolved | Task 3 |
| `get_recent_activity` with display names | Task 3 |
| Sub-nav bar on all pages | Tasks 5, 6 |
| Probability bars on market list | Task 5 |
| Large YES%/NO% on detail page | Task 5 |
| SVG probability chart on detail page | Tasks 4, 5 |
| YES/NO toggle buttons in bet form | Task 5 |
| Client-side payout estimate | Task 5 |
| Bets table with display names | Tasks 3, 5 |
| Portfolio page (active + resolved) | Tasks 4, 6 |
| Activity feed (50 rows, newest first) | Tasks 4, 6 |
| Propose form with webapp submission | Tasks 4, 6 |
| Flash banner on successful proposal | Tasks 4, 5 |
| Pending page with sub-nav | Task 6 |
| Pending count badge (admin) | Tasks 4, 5, 6 |
| Route ordering (statics before `{id}`) | Task 4 |
