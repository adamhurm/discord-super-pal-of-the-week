# Fighting System Design

**Date:** 2026-05-18  
**Status:** Approved

## Context

Add a Pokemon-style card fighting system to the Discord bot. Players act as trainers and field cards from their collection as fighters. Two battle modes exist: Quick (1v1) and Extended (3v3). Wins/losses transfer Purple Bringle Coins (Pringles) between players. Pringles can be spent at an item shop or traded in for card draws. Fights happen in the browser (FastAPI webapp) with real-time WebSocket sync.

Builds on the existing `pending_trades` pattern (DB-backed state, magic link auth, `discord.ui.View` buttons) and the existing webapp (FastAPI + Jinja2 + session cookies).

---

## Design Decisions

- **Fighter model**: Trainer + card party (hybrid). Players pick cards from their collection; the card fights on their behalf.
- **Battle modes**: Quick (1 card each) and Extended (3 cards each).
- **Stats**: Rarity-tier based — same rarity = same HP/ATK bonus. No per-card variation.
- **Attacks**: Shared pool of 4 attacks, all cards use the same menu. D20 determines hit/miss/damage tier.
- **Items**: Purchasable with Pringles. Sunday noon UTC handicap resets 0-heal players to 2 Heal Potions.
- **Real-time**: Full WebSocket browser battle. Discord handles challenge initiation and final result only.
- **Running**: Extended only. Three-tier D20 outcome.

---

## Data Model

### New columns on `members`
```sql
ALTER TABLE members ADD COLUMN pringle_balance INTEGER DEFAULT 0;
ALTER TABLE members ADD COLUMN bank_debt INTEGER DEFAULT 0;
```

### New table: `player_items`
```sql
CREATE TABLE player_items (
    player_id TEXT NOT NULL REFERENCES members(discord_id),
    item_type TEXT NOT NULL,  -- heal_potion|super_potion|bringus_boost|smoke_screen
    quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, item_type)
);
```

### New table: `fights`
```sql
CREATE TABLE fights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,  -- quick|extended
    challenger_id TEXT NOT NULL REFERENCES members(discord_id),
    opponent_id TEXT NOT NULL REFERENCES members(discord_id),
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|lobby|active|completed|expired
    winner_id TEXT REFERENCES members(discord_id),
    current_turn_player_id TEXT REFERENCES members(discord_id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    expires_at TIMESTAMP
);
```

### New table: `fight_cards`
```sql
CREATE TABLE fight_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fight_id INTEGER NOT NULL REFERENCES fights(id),
    player_id TEXT NOT NULL REFERENCES members(discord_id),
    card_member_id TEXT NOT NULL REFERENCES members(discord_id),
    rarity TEXT NOT NULL,
    slot INTEGER NOT NULL,  -- 1, 2, or 3
    hp_current INTEGER NOT NULL,
    hp_max INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,  -- 1 = currently fighting
    is_fainted INTEGER NOT NULL DEFAULT 0,
    UNIQUE(fight_id, player_id, slot)
);
```

### New table: `fight_log`
```sql
CREATE TABLE fight_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fight_id INTEGER NOT NULL REFERENCES fights(id),
    actor_id TEXT REFERENCES members(discord_id),
    action_type TEXT NOT NULL,  -- attack|item|swap|run|system
    action_detail TEXT,  -- JSON blob
    d20_roll INTEGER,
    damage_dealt INTEGER,
    narrative_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

All new tables added to `init_db()` in `src/superpal/cards/db.py` as idempotent `CREATE TABLE IF NOT EXISTS` statements. The two `ALTER TABLE` statements wrapped in `try/except OperationalError` per existing migration pattern.

---

## Fight Mechanics

### Rarity-tier stats
| Rarity    | HP  | ATK Bonus |
|-----------|-----|-----------|
| Common    | 80  | +0        |
| Uncommon  | 100 | +5        |
| Rare      | 130 | +10       |
| Legendary | 170 | +20       |

### Shared attack pool
| Attack             | Base Damage | Min Roll to Land        |
|--------------------|-------------|-------------------------|
| Vibe Check         | 15          | 1 (always hits)         |
| Body Slam          | 20          | 6                       |
| Hype Strike        | 25          | 10                      |
| Super Bringus Beam | 35          | 14                      |

### D20 damage multipliers (applied after min-roll check)
| Roll Range | Result       | Multiplier             |
|------------|--------------|------------------------|
| Below min  | Miss         | 0×                     |
| 1–10       | Glancing     | 0.5×                   |
| 11–16      | Direct hit   | 1.0×                   |
| 17–19      | Critical hit | 1.5×                   |
| 20         | Nat 20       | 2.0× + flavor text     |

**Formula**: `floor((base_damage + atk_bonus) × multiplier)`

Vibe Check special case: rolls 1–10 still deal glancing damage (0.5×) — it never fully misses.

### Running (Extended only)
| D20 Roll | Outcome                      |
|----------|------------------------------|
| 16–20    | Free escape — no Pringle cost |
| 11–15    | Escape — forfeit 25 Pringles |
| 1–10     | Failed — lose your turn      |

Running is not available in Quick Battle.

### Card swap (Extended only)
- Costs your turn.
- Incoming card enters at its current HP (not reset to full).
- Fainted cards cannot be swapped in.

### Extended battle flow
- 3 cards per player; one active card fights at a time.
- When active card faints, trainer immediately picks their next card (opponent waits).
- Battle ends when all 3 of one trainer's cards have fainted, or a player successfully runs.

---

## Economy & Items

### Pringle payouts
| Event                         | Pringles |
|-------------------------------|----------|
| Win (either mode)             | +50      |
| Lose (either mode)            | −50      |
| Extended participation (both) | +25      |
| Escape (11–15 run roll)       | −25      |
| Free escape (16+ run roll)    | ±0       |

Extended net result: winner +75, loser −25.

### Bank of Bringus (unpayable debt)
If loser balance < 50:
1. Loser pays their full balance (floored at 0 — no negative balances).
2. `shortfall = 50 − loser_paid`
3. Bank pays winner `floor(shortfall × 0.5)`.
4. Bot DMs winner a flavor message noting the Bank covered the difference.
5. `bank_debt` on loser increments by shortfall (informational, not enforced as a repayment obligation).

### Item shop
| Item          | Cost (Pringles) | Effect                             |
|---------------|-----------------|------------------------------------|
| Heal Potion   | 50              | Restore 40 HP to active card       |
| Super Potion  | 100             | Restore 80 HP to active card       |
| Bringus Boost | 75              | +10 ATK for next 3 turns           |
| Smoke Screen  | 60              | Opponent's next attack auto-misses |

Items are used on your turn, consuming the turn.

### Sunday noon UTC handicap
- `discord.ext.tasks` loop fires every week at Sunday 12:00 UTC.
- Any player with `heal_potion` quantity = 0 gets reset to 2.
- Silent reset — no notification.

### Pringle → card draw
- `/card-pringles trade-in` spends 100 Pringles for one card draw.
- Reuses existing `draw_card()` in `service.py`.

---

## Discord Commands

| Command                           | Description                        |
|-----------------------------------|------------------------------------|
| `/card-fight @opponent mode:...`  | Challenge opponent (quick/extended)|
| `/card-shop buy <item>`           | Buy item with Pringles             |
| `/card-shop list`                 | Show item prices and your balance  |
| `/card-pringles balance`          | Show your Pringle balance          |
| `/card-pringles trade-in`         | Spend 100 Pringles for a card draw |

Challenge creates a `fights` row (`status='pending'`) and sends a Discord embed with Accept/Decline buttons via `FightChallengeView` (mirrors `TradeView`). On accept, bot DMs both players magic links to the lobby. Expires after 5 minutes with no response.

---

## WebSocket Flow

### 1. Challenge & lobby
1. `/card-fight` → insert `fights` row, send embed with `FightChallengeView`.
2. Accept → `status='lobby'`, DM both players `/fight/{id}/lobby` magic links.
3. Lobby: each player picks card(s) and clicks Ready. Server writes `fight_cards` rows.
4. Both ready → coin toss → `status='active'`, set `current_turn_player_id`, redirect both browsers to `/fight/{id}/battle`.

### 2. Battle
- WS endpoint: `GET /ws/fight/{id}?token={session_token}`
- Server sends full fight state JSON on connect.
- Client messages: `{"action": "attack"|"item"|"swap"|"run", "detail": {...}}`
- Server: validate turn ownership → validate action legality → roll D20 → update DB → append `fight_log` → broadcast new state to both connections.
- State JSON: both parties' card HP, active cards, last log entries, whose turn, pending swap flag.

### 3. Disconnect handling
- Client reconnects on page focus.
- Active player disconnected 3+ minutes: server auto-passes their turn (system log entry).
- Fight expires after 10 minutes total inactivity: `status='expired'`, no Pringle transfer.

### 4. Fight end
- Server detects win condition → `status='completed'`, `winner_id` → award Pringles → broadcast final state.
- Bot posts result embed to the original Discord channel.

---

## Files

### New
| Path | Purpose |
|---|---|
| `src/superpal/cards/fight_service.py` | Fight business logic (create, action processing, awards, expiry) |
| `src/superpal/cards/pringle_service.py` | Pringle balance reads/writes, Bank of Bringus logic |
| `src/superpal/webapp/templates/fight_lobby.html` | Card picker UI, Ready button |
| `src/superpal/webapp/templates/fight_battle.html` | Live battle UI with WS client JS |

### Modified
| Path | Change |
|---|---|
| `src/superpal/cards/db.py` | Add 4 new tables + 2 member columns to `init_db()` |
| `src/superpal/cards/models.py` | Add `Fight`, `FightCard`, `FightLogEntry`, `PlayerItem` dataclasses |
| `src/superpal/webapp/routes.py` | Add `/fight/{id}/lobby`, `/fight/{id}/battle`, `/ws/fight/{id}` |
| `src/bot.py` | Add `/card-fight`, `/card-shop`, `/card-pringles` commands + Sunday heal task |

---

## Verification

### Unit tests
New files: `tests/cards/test_fight_service.py`, `tests/cards/test_pringle_service.py`

Cover:
- D20 damage formula: each attack × each rarity × each roll tier
- Run mechanic: all three D20 outcome tiers
- Bank of Bringus: full pay, partial pay, zero pay
- Extended win condition: all 3 cards fainted
- Item effects: HP restore, ATK boost tracking, smoke screen flag

### Integration (manual)
1. `/card-fight` challenge → accept → both browser windows open lobby
2. Pick cards, both click Ready → coin toss fires → battle page loads
3. Full Quick Battle to completion → verify Pringle balances update
4. Full Extended Battle with a card swap and a run attempt
5. Bank of Bringus DM fires when loser balance < 50

### Regression
- `pytest tests/ -q` passes with no new failures
- Existing card draw, trade, and upgrade flows unaffected
