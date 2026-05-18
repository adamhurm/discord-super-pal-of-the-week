# Fight Leaderboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/card-fight-leaderboard` with five sort options and rename `/card-leaderboard` to `/card-collection-leaderboard`.

**Architecture:** New `get_fight_leaderboard(sort_by)` function in `fight_service.py` runs one query per sort metric against the existing `fights`, `fight_log`, and `members` tables — no schema changes. Bot command mirrors the existing `card-collection-leaderboard` pattern. Docs updated in the same pass.

**Tech Stack:** Python 3.13, aiosqlite, discord.py app_commands, pytest (asyncio_mode=auto)

---

### Task 1: Add `get_fight_leaderboard` to `fight_service.py`

**Files:**
- Modify: `src/superpal/cards/fight_service.py` (append after `expire_inactive_fights`)
- Test: `tests/cards/test_fight_service.py` (append at bottom)

- [ ] **Step 1: Write the failing tests**

Append to `tests/cards/test_fight_service.py`:

```python
# ─── get_fight_leaderboard tests ────────────────────────────────────────────

async def _insert_completed_fight(conn, challenger_id: str, opponent_id: str, winner_id: str, now: str) -> int:
    cur = await conn.execute(
        "INSERT INTO fights (mode, challenger_id, opponent_id, status, winner_id, "
        "created_at, last_activity_at) VALUES ('quick', ?, ?, 'completed', ?, ?, ?)",
        (challenger_id, opponent_id, winner_id, now, now),
    )
    return cur.lastrowid


@pytest.mark.asyncio
async def test_fight_leaderboard_wins(db):
    db_mod, svc_mod, fs_mod, ps_mod = db
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        # p1 wins 2, p2 wins 1
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p2", "p1", "p2", now)
        await conn.commit()

    rows = await fs_mod.get_fight_leaderboard("wins")
    assert rows[0]["discord_id"] == "p1"
    assert rows[0]["total"] == 2
    assert rows[1]["discord_id"] == "p2"
    assert rows[1]["total"] == 1


@pytest.mark.asyncio
async def test_fight_leaderboard_wins_empty(db):
    db_mod, svc_mod, fs_mod, ps_mod = db
    rows = await fs_mod.get_fight_leaderboard("wins")
    assert rows == []


@pytest.mark.asyncio
async def test_fight_leaderboard_fights_played(db):
    db_mod, svc_mod, fs_mod, ps_mod = db
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        # p1 plays 3, p2 plays 3 (same fights) — but also add p1 as challenger vs p2 a 4th time
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p1", "p2", "p2", now)
        await _insert_completed_fight(conn, "p2", "p1", "p1", now)
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await conn.commit()

    rows = await fs_mod.get_fight_leaderboard("fights_played")
    # Both p1 and p2 played 4 fights (all same fights) — equal, order by discord_id or first found
    totals = {r["discord_id"]: r["total"] for r in rows}
    assert totals["p1"] == 4
    assert totals["p2"] == 4


@pytest.mark.asyncio
async def test_fight_leaderboard_win_rate_minimum_threshold(db):
    db_mod, svc_mod, fs_mod, ps_mod = db
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        # Add a third member with only 2 fights (should not appear)
        await conn.execute(
            "INSERT INTO members (discord_id, display_name, avatar_url, is_excluded, synced_at) "
            "VALUES ('p3', 'Carol', NULL, 0, ?)", (now,)
        )
        # p1 and p2 each play 3 fights; p3 plays 2 fights
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p1", "p2", "p1", now)
        await _insert_completed_fight(conn, "p2", "p1", "p2", now)
        await _insert_completed_fight(conn, "p1", "p3", "p1", now)
        await _insert_completed_fight(conn, "p3", "p2", "p3", now)
        await conn.commit()

    rows = await fs_mod.get_fight_leaderboard("win_rate")
    discord_ids = [r["discord_id"] for r in rows]
    # p3 has only 2 completed fights — must not appear
    assert "p3" not in discord_ids
    # p1: 3 wins / 4 fights = 0.75; p2: 1 win / 4 fights = 0.25
    assert rows[0]["discord_id"] == "p1"
    assert abs(rows[0]["total"] - 0.75) < 0.01
    assert rows[0]["total_fights"] == 4


@pytest.mark.asyncio
async def test_fight_leaderboard_pringle_balance(db):
    db_mod, svc_mod, fs_mod, ps_mod = db
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        await conn.execute("UPDATE members SET pringle_balance = 200 WHERE discord_id = 'p2'")
        await conn.execute("UPDATE members SET pringle_balance = 50 WHERE discord_id = 'p1'")
        await conn.commit()

    rows = await fs_mod.get_fight_leaderboard("pringle_balance")
    assert rows[0]["discord_id"] == "p2"
    assert rows[0]["total"] == 200
    assert rows[1]["discord_id"] == "p1"
    assert rows[1]["total"] == 50


@pytest.mark.asyncio
async def test_fight_leaderboard_escapes(db):
    db_mod, svc_mod, fs_mod, ps_mod = db
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_mod.DB_PATH) as conn:
        # p1 escapes 2 fights; p2 escapes 1 fight
        fid1 = await _insert_completed_fight(conn, "p1", "p2", "p2", now)  # p1 ran, p2 wins
        fid2 = await _insert_completed_fight(conn, "p1", "p2", "p2", now)  # p1 ran again
        fid3 = await _insert_completed_fight(conn, "p2", "p1", "p1", now)  # p2 ran, p1 wins
        for fid, actor in [(fid1, "p1"), (fid2, "p1"), (fid3, "p2")]:
            await conn.execute(
                "INSERT INTO fight_log (fight_id, actor_id, action_type, narrative_text) "
                "VALUES (?, ?, 'run', 'Escape successful')",
                (fid, actor),
            )
        await conn.commit()

    rows = await fs_mod.get_fight_leaderboard("escapes")
    assert rows[0]["discord_id"] == "p1"
    assert rows[0]["total"] == 2
    assert rows[1]["discord_id"] == "p2"
    assert rows[1]["total"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/achurm/Documents/discord-super-pal-of-the-week
.venv/bin/python -m pytest tests/cards/test_fight_service.py -k "leaderboard" -q
```

Expected: All 6 tests FAIL with `AttributeError: module ... has no attribute 'get_fight_leaderboard'`

- [ ] **Step 3: Implement `get_fight_leaderboard` in `fight_service.py`**

Append after `expire_inactive_fights` (end of file):

```python
async def get_fight_leaderboard(sort_by: str = "wins") -> list[dict]:
    """Return top 10 players ranked by fight stats.

    sort_by: 'wins' | 'win_rate' | 'fights_played' | 'pringle_balance' | 'escapes'
    All rows: {discord_id, display_name, total}.
    win_rate rows also include {total_fights} for display formatting.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if sort_by == "win_rate":
            async with db.execute("""
                SELECT discord_id, display_name,
                  CAST(wins AS REAL) / total_fights AS total,
                  total_fights
                FROM (
                  SELECT m.discord_id, m.display_name,
                    SUM(CASE WHEN f.winner_id = m.discord_id THEN 1 ELSE 0 END) AS wins,
                    COUNT(*) AS total_fights
                  FROM members m
                  JOIN fights f
                    ON (f.challenger_id = m.discord_id OR f.opponent_id = m.discord_id)
                  WHERE f.status = 'completed'
                  GROUP BY m.discord_id
                  HAVING total_fights >= 3
                )
                ORDER BY total DESC LIMIT 10
            """) as cur:
                rows = await cur.fetchall()
            return [
                {
                    "discord_id": r[0],
                    "display_name": r[1],
                    "total": r[2],
                    "total_fights": r[3],
                }
                for r in rows
            ]

        if sort_by == "fights_played":
            sql = """
                SELECT m.discord_id, m.display_name, COUNT(*) AS total
                FROM members m
                JOIN fights f
                  ON (f.challenger_id = m.discord_id OR f.opponent_id = m.discord_id)
                WHERE f.status = 'completed'
                GROUP BY m.discord_id ORDER BY total DESC LIMIT 10
            """
        elif sort_by == "pringle_balance":
            sql = """
                SELECT discord_id, display_name, pringle_balance AS total
                FROM members WHERE is_excluded = 0
                ORDER BY pringle_balance DESC LIMIT 10
            """
        elif sort_by == "escapes":
            sql = """
                SELECT fl.actor_id AS discord_id, m.display_name, COUNT(*) AS total
                FROM fight_log fl
                JOIN fights f ON f.id = fl.fight_id
                JOIN members m ON m.discord_id = fl.actor_id
                WHERE fl.action_type = 'run'
                  AND f.status = 'completed'
                  AND f.winner_id != fl.actor_id
                GROUP BY fl.actor_id ORDER BY total DESC LIMIT 10
            """
        else:  # wins
            sql = """
                SELECT m.discord_id, m.display_name, COUNT(*) AS total
                FROM fights f JOIN members m ON m.discord_id = f.winner_id
                WHERE f.status = 'completed'
                GROUP BY f.winner_id ORDER BY total DESC LIMIT 10
            """

        async with db.execute(sql) as cur:
            rows = await cur.fetchall()
    return [{"discord_id": r[0], "display_name": r[1], "total": r[2]} for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/cards/test_fight_service.py -k "leaderboard" -q
```

Expected: All 6 pass, 0 failed.

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: All previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/superpal/cards/fight_service.py tests/cards/test_fight_service.py
git commit -m "feat(fight): add get_fight_leaderboard with wins/rate/played/balance/escapes sorts"
```

---

### Task 2: Update `bot.py` — rename command and add fight leaderboard command

**Files:**
- Modify: `src/bot.py`

- [ ] **Step 1: Add `get_fight_leaderboard` to the fight_service import**

Find this line (around line 31–34):
```python
from superpal.cards.fight_service import (
    create_fight, accept_fight, create_fight_token, expire_pending_challenges,
    FIGHT_TOKEN_EXPIRY_MINUTES,
)
```

Replace with:
```python
from superpal.cards.fight_service import (
    create_fight, accept_fight, create_fight_token, expire_pending_challenges,
    FIGHT_TOKEN_EXPIRY_MINUTES, get_fight_leaderboard,
)
```

- [ ] **Step 2: Rename the collection leaderboard command**

Find (around line 751):
```python
@bot.tree.command(name="card-leaderboard", description="Show the top 10 card collectors")
```

Replace with:
```python
@bot.tree.command(name="card-collection-leaderboard", description="Show the top 10 card collectors")
```

Also rename the handler function for clarity. Find (around line 758):
```python
async def card_leaderboard_command(
```

Replace with:
```python
async def card_collection_leaderboard_command(
```

- [ ] **Step 3: Add the fight leaderboard command**

Find the line just before the `card-shop` command (around line 889):
```python
@bot.tree.command(name="card-shop", description="Browse or buy items from the Pringle shop")
```

Insert the new command before it:
```python
@bot.tree.command(name="card-fight-leaderboard", description="Show the top 10 fight stats")
@discord.app_commands.describe(sort_by="What to rank players by")
@discord.app_commands.choices(sort_by=[
    discord.app_commands.Choice(name="Most Wins", value="wins"),
    discord.app_commands.Choice(name="Best Win Rate", value="win_rate"),
    discord.app_commands.Choice(name="Most Fights Played", value="fights_played"),
    discord.app_commands.Choice(name="Pringle Balance", value="pringle_balance"),
    discord.app_commands.Choice(name="Most Escapes", value="escapes"),
])
async def card_fight_leaderboard_command(
    interaction: discord.Interaction,
    sort_by: str = "wins",
) -> None:
    await interaction.response.defer()
    rows = await get_fight_leaderboard(sort_by)

    title_map = {
        "wins": "Most Wins",
        "win_rate": "Best Win Rate",
        "fights_played": "Most Fights Played",
        "pringle_balance": "Pringle Balance",
        "escapes": "Most Escapes",
    }
    unit_map = {
        "wins": "wins",
        "fights_played": "fights played",
        "pringle_balance": "Pringles",
        "escapes": "escapes",
    }
    title = f"Fight Leaderboard — {title_map.get(sort_by, 'Most Wins')}"

    if not rows:
        embed = discord.Embed(
            title=title,
            description="No fights recorded yet!",
            color=discord.Color(0x5865F2),
        )
    elif sort_by == "win_rate":
        lines = [
            f"{rank}. {row['display_name']} — {round(row['total'] * 100)}% ({row['total_fights']} fights)"
            for rank, row in enumerate(rows, start=1)
        ]
        embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color(0x5865F2))
    else:
        unit = unit_map.get(sort_by, "")
        lines = [
            f"{rank}. {row['display_name']} — {row['total']} {unit}"
            for rank, row in enumerate(rows, start=1)
        ]
        embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color(0x5865F2))

    await interaction.followup.send(embed=embed)


```

- [ ] **Step 4: Run the full test suite**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: All previously passing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/bot.py
git commit -m "feat(bot): add /card-fight-leaderboard, rename /card-leaderboard to /card-collection-leaderboard"
```

---

### Task 3: Update docs

**Files:**
- Modify: `docs/cards-interface-matrix.md`
- Modify: `docs/feature-backlog.md`

- [ ] **Step 1: Update `cards-interface-matrix.md`**

Find line 21:
```
| Leaderboard | Top 10 collectors ranked by total cards, legendary count, or unique members | ✅ `/card-leaderboard` | ❌ |
```

Replace with:
```
| Collection Leaderboard | Top 10 collectors ranked by total cards, legendary count, or unique members | ✅ `/card-collection-leaderboard` | ❌ |
| Fight Leaderboard | Top 10 fighters ranked by wins, win rate, fights played, Pringle balance, or escapes | ✅ `/card-fight-leaderboard` | ❌ |
```

- [ ] **Step 2: Update `feature-backlog.md`**

Find:
```
### Leaderboard
Top collectors by total cards, by legendary count, and by unique members collected.
- Command: `/card-leaderboard`
```

Replace with:
```
### Leaderboard
Top collectors by total cards, by legendary count, and by unique members collected.
- Command: `/card-collection-leaderboard`

### Fight Leaderboard
Top fighters ranked by wins, win rate, fights played, Pringle balance, or escapes.
- Command: `/card-fight-leaderboard`
```

- [ ] **Step 3: Commit**

```bash
git add docs/cards-interface-matrix.md docs/feature-backlog.md
git commit -m "docs: update leaderboard command names and add fight leaderboard entry"
```
