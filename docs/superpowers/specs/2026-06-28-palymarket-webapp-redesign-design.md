# Palymarket Webapp Redesign

**Date:** 2026-06-28  
**Status:** Approved

## Context

The palymarket prediction market system has a functional but minimal webapp (list, detail, pending-approvals pages). It shows raw pool sizes and simple bet forms. The goal is a full redesign modeled after real prediction market sites (Polymarket, Manifold) — with implied probability display, portfolio tracking, activity feed, historical probability charts, and a market proposal form — all staying within the existing FastAPI/Jinja2 app and Discord dark theme.

## Approach

Server-rendered Jinja2 pages only. No JS framework, no build step. Probability chart rendered as an inline SVG path computed server-side. Same pattern as the rest of the app (collection, marketplace, economy, fight pages).

## Pages

| URL | Template | Auth |
|---|---|---|
| `/palymarket` | `palymarket_list.html` | session required |
| `/palymarket/portfolio` | `palymarket_portfolio.html` | session required |
| `/palymarket/activity` | `palymarket_activity.html` | session required |
| `/palymarket/propose` | `palymarket_propose.html` | session required |
| `/palymarket/{id}` | `palymarket_detail.html` | session required |
| `/palymarket/pending` | `palymarket_pending.html` | admin only |
| `POST /palymarket/propose` | redirect | session required |

All palymarket pages share a persistent sub-nav bar (Markets / Portfolio / Activity / Propose) rendered inline at the top of each template. Admin sessions see a pending-approval count badge on the nav.

## Data Layer

### New table: `market_probability_history`

```sql
CREATE TABLE market_probability_history (
    id INTEGER PRIMARY KEY,
    market_id INTEGER NOT NULL,
    yes_pct REAL NOT NULL,
    yes_pool INTEGER NOT NULL,
    no_pool INTEGER NOT NULL,
    recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (market_id) REFERENCES markets(id)
)
```

Added as an idempotent `CREATE TABLE IF NOT EXISTS` in `init_db()` (`src/superpal/cards/db.py`).

### New service functions (`src/superpal/palymarket/service.py`)

- `record_probability_snapshot(market_id)` — inserts a row after pool update; called at the end of `place_or_update_bet()` after writing the new pools to `markets`
- `get_probability_history(market_id) -> list[tuple[float, datetime]]` — returns `(yes_pct, recorded_at)` pairs ordered by time; used for SVG chart
- `get_player_portfolio(player_id) -> list[dict]` — for each non-resolved market where the player has a bet: `{market, bet, yes_pct, estimated_payout}`; estimated payout = `floor(bet.amount / winning_pool * total_pool)` using the player's current side
- `get_recent_activity(limit=50) -> list[dict]` — `SELECT mb.*, m.title, m.id as market_id, mem.display_name FROM market_bets mb JOIN markets m ON ... JOIN members mem ON ... ORDER BY mb.placed_at DESC LIMIT ?`

### Existing functions reused unchanged

- `place_or_update_bet()` — `src/superpal/palymarket/service.py:233`
- `get_palycoin_balance()` — `src/superpal/palymarket/service.py:41`
- `propose_market()` — `src/superpal/palymarket/service.py`
- `list_markets(status)` — `src/superpal/palymarket/service.py`
- `get_bets_for_market()` — `src/superpal/palymarket/service.py`
- `get_player_bet()` — `src/superpal/palymarket/service.py`

## UI Components

### Sub-nav bar (shared across all palymarket pages)

Horizontal tab strip below the page title:
- **Markets** → `/palymarket`
- **Portfolio** → `/palymarket/portfolio`
- **Activity** → `/palymarket/activity`
- **Propose** → `/palymarket/propose`
- **Pending (N)** → `/palymarket/pending` (admin only, badge shows count)

Active tab has accent-colored bottom border (`#5865f2`). Rendered inline in each template (no base template inheritance since the rest of the app doesn't use one).

### Probability bar component

Used on market list cards and market detail page:

```
YES  67% ████████████████░░░░░░░░  33% NO
```

- Horizontal `<div>` with a green left section and red right section, widths set via inline `style="width: 67%"` from Jinja
- YES% label left-aligned, NO% right-aligned
- Colors: YES green `#3ba55c`, NO red `#ed4245`

### Market list (`/palymarket`)

- Palycoin balance pill in the header
- **Open markets** section first — grid of market cards (matching existing card grid style, `minmax(280px, 1fr)`)
  - Each card: title (link to detail), probability bar, YES% / NO% percentages, total volume, player's position badge if they have a bet
- **Closed / Resolved** section below — muted styling, no bet form

### Market detail (`/palymarket/{id}`)

- Title, status badge, description
- Two large stat boxes: **67% YES** and **33% NO** (font ~2rem, prominent)
- Probability chart: inline SVG, 600×120px viewBox, polyline path from `get_probability_history()`. If fewer than 2 data points, show "No chart data yet." Dashed horizontal line at 50%.
- Bet form (only shown if market is `open`):
  - YES / NO toggle buttons (styled as radio but look like pills)
  - Amount input
  - Estimated payout line: "If YES wins → ~X Palycoins" computed client-side with a small inline `<script>` using current pool values embedded in data attributes
- Your current position box (if player has a bet)
- Bets table (player display name, side badge, amount, time)
- Admin controls section (if admin): Close market, Resolve with outcome dropdown

### Portfolio (`/palymarket/portfolio`)

- Summary row: total staked, open position count
- Active positions: one card per open market with a bet — market title, your side + amount, current YES%, estimated payout if you win, link to market
- Resolved history below: market title, your side, outcome, amount staked, amount returned (0 if lost), win/loss badge

### Activity feed (`/palymarket/activity`)

- Dense list, newest first
- Each row: display name (bold), action ("bet 50 on **YES**"), market title (link), relative time
- No pagination for now — limit 50 rows

### Propose form (`/palymarket/propose`)

- Title input (max 120 chars)
- Description textarea (max 500 chars)
- Submit → `POST /palymarket/propose` → calls `propose_market()` → redirect to `/palymarket` with query param `?proposed=1` → list page shows a flash banner "Market submitted for admin review"

## Styling

Stays on Discord dark theme:
- Backgrounds: `#1e1f22` page, `#2b2d31` cards
- Text: `#dcddde` primary, `#72767d` secondary
- Accent: `#5865f2` (Discord purple) for active nav, buttons
- YES: `#3ba55c` green; NO: `#ed4245` red
- No external CSS files; inline `<style>` per template matching existing app convention

SVG chart: stroke `#5865f2`, no fills, 1.5px line, no external charting library.

## Routes to add/modify (`src/superpal/webapp/routes.py`)

New handlers:
- `GET /palymarket/portfolio` → `palymarket_portfolio()`
- `GET /palymarket/activity` → `palymarket_activity()`
- `GET /palymarket/propose` → `palymarket_propose_form()`
- `POST /palymarket/propose` → `palymarket_propose_submit()`

Modified handlers:
- `GET /palymarket` — add probability percent computation and flash message handling
- `GET /palymarket/{id}` — add probability history fetch and chart data
- `POST /palymarket/{id}/bet` — call `record_probability_snapshot()` after bet placed (or handle inside service)

Route ordering note: `/palymarket/pending`, `/palymarket/portfolio`, `/palymarket/activity`, `/palymarket/propose` must be registered **before** `/palymarket/{id}` to prevent FastAPI matching them as `market_id`.

## Verification

1. Run `pytest tests/ -q` — all existing tests pass
2. Start bot locally (`cd src && ../.venv/bin/python bot.py`)
3. Get a magic link via Discord → open webapp
4. Navigate to `/palymarket` — see sub-nav, balance, market cards with probability bars
5. Place a bet on an open market → verify probability bar updates, history row inserted
6. Navigate to `/palymarket/{id}` → verify SVG chart appears with data points
7. Navigate to `/palymarket/portfolio` → verify active position appears with estimated payout
8. Navigate to `/palymarket/activity` → verify bet appears newest-first
9. Submit a propose form → verify redirect to list with flash banner, market appears on `/palymarket/pending`
10. Admin: approve, close, resolve a market → verify resolved market appears in portfolio history with correct payout
