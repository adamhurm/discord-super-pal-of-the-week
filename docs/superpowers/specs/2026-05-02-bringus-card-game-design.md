# Bringus Card Game — Design Spec

**Date:** 2026-05-02
**Status:** Approved

---

## Context

The Bringus Discord server runs a "Super Pal of the Week" bot. This feature adds a collectible card game layer: server members appear as cards, users draw them weekly, and a companion webapp lets users view their collection via a secure one-time link. The admin dashboard lets the owner exclude members from the card pool.

---

## Architecture

Single Python process: the existing Discord bot and a new FastAPI webapp run together via asyncio. One SQLite file stores all state. This fits cleanly into the existing Kubernetes pod without new infrastructure.

---

## Bot Commands

| Command | Who | Behavior |
|---|---|---|
| `/draw-card` | Everyone | Draw 1 card/week (2/week for Super Pal). Posts embed to channel. |
| `/my-collection` | Everyone | Bot **DMs** a one-time magic link to the caller. Never posted to channel. |
| `/trade-in @member rarity` | Everyone | Spend 3× [member, rarity] → receive 1 random card of same rarity. |
| `/upgrade @member rarity` | Everyone | Spend 5× [member, rarity] → receive 1× same member at next rarity tier. |
| `/admin-link` | The Clippy role only (ID: `1085646770006151259`) | Bot **DMs** a one-time admin magic link to the caller. Never posted to channel. |

**Weekly draw limit:** tracked by ISO week start date (Monday). Resets automatically — no cron job needed, checked at draw time.

**Trade-in rules:**
- Requires exactly 3 copies of the same [member + rarity] combination.
- Result is a random card drawn from the eligible pool (excluding excluded members) at that rarity.
- Legendary cards have no upgrade path — `/upgrade` on a Legendary is rejected with a clear message.

---

## Card Design

Each card is a Discord rich embed:

- **Left border color:** signals rarity (grey / green / blue / gold gradient)
- **Author row:** member display name + Discord avatar
- **Description field:** reserved for future stats/lore — currently shows italic placeholder text
- **Footer:** rarity badge label + sequential card number + "Bringus Card Game"
- **Color field:** matches rarity color

**Rarity weights (random per draw):**

| Rarity | Weight | Embed color |
|---|---|---|
| Common | 60% | `#95a5a6` grey |
| Uncommon | 25% | `#27ae60` green |
| Rare | 12% | `#2980b9` blue |
| Legendary | 3% | `#f39c12` gold |

Duplicate cards are allowed and stack (quantity tracked per [owner, card_member, rarity]).

---

## Database Schema (SQLite)

```sql
CREATE TABLE members (
    discord_id   TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    avatar_url   TEXT,
    is_excluded  BOOLEAN NOT NULL DEFAULT 0,
    synced_at    TIMESTAMP NOT NULL
);

CREATE TABLE user_cards (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id         TEXT NOT NULL REFERENCES members(discord_id),
    card_member_id   TEXT NOT NULL REFERENCES members(discord_id),
    rarity           TEXT NOT NULL CHECK(rarity IN ('common','uncommon','rare','legendary')),
    quantity         INTEGER NOT NULL DEFAULT 1,
    first_acquired_at TIMESTAMP NOT NULL
);

CREATE TABLE draw_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES members(discord_id),
    week_start  TEXT NOT NULL,  -- YYYY-MM-DD (Monday of that week)
    draws_used  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, week_start)
);

CREATE TABLE magic_links (
    token             TEXT PRIMARY KEY,  -- UUID4
    user_id           TEXT NOT NULL,
    link_type         TEXT NOT NULL CHECK(link_type IN ('collection','admin')),
    created_at        TIMESTAMP NOT NULL,
    consumed_at       TIMESTAMP,         -- NULL = not yet used
    session_token     TEXT,              -- set on first use
    session_expires_at TIMESTAMP         -- 24h after first use
);
```

---

## Code Structure

New modules added inside `src/superpal/`:

```
src/
  bot.py                       # existing — card commands added here
  superpal/
    cards/
      __init__.py
      db.py                    # SQLite init, schema migration, connection helper
      models.py                # dataclasses: Member, UserCard, DrawLog, MagicLink
      service.py               # draw(), trade_in(), upgrade(), weekly_limit()
      embeds.py                # build_card_embed() per rarity
    webapp/
      __init__.py
      app.py                   # FastAPI app factory; started alongside bot via asyncio
      auth.py                  # generate_magic_link(), consume_link(), session cookie helpers
      routes.py                # GET /link/{token}, GET /collection, GET /admin, POST /admin/*
      templates/
        collection.html        # card grid with ??? silhouettes for undiscovered members
        admin.html             # member list with exclude/re-include buttons + sync
        expired.html           # shown when a link token has already been consumed
```

---

## Webapp Routes

| Route | Auth | Behavior |
|---|---|---|
| `GET /link/{token}` | None | Consumes token → sets session cookie → redirects. If already consumed: renders `expired.html`. |
| `GET /collection` | Session cookie | Shows card grid. Owned cards show avatar + rarity + quantity. Undiscovered members show as `???` silhouette. |
| `GET /admin` | Admin session cookie | Member management: exclude/re-include, sync member list, card pool stats. |
| `POST /admin/exclude/{member_id}` | Admin session cookie | Toggles `is_excluded` in DB. |
| `POST /admin/sync` | Admin session cookie | Fetches current guild member list from Discord API, upserts `members` table. |

---

## Magic Link Flow

1. User runs `/my-collection` (or `/admin-link`).
2. Bot generates a UUID4 token, inserts row into `magic_links` with `consumed_at = NULL`.
3. Bot **DMs** the link to the caller. Nothing posted to the channel.
4. First browser visit to `/link/{token}`: sets `consumed_at`, generates `session_token`, sets `session_expires_at = now + 24h`, writes session cookie, redirects to `/collection` or `/admin`.
5. Any subsequent visit to the same `/link/{token}`: renders `expired.html`.
6. `/collection` and `/admin` validate the session cookie on every request; expired sessions redirect to `expired.html`.

---

## Webapp UI

**Collection view:**
- Header: user avatar, display name, card count summary.
- Rarity summary pills (Common ×N, Uncommon ×N, …).
- Card grid: owned cards show avatar + name + rarity + quantity badge. Every non-excluded member with no owned cards shows as a `???` silhouette so users know the full roster exists.
- "Generate New Link" button — POSTs to `/collection/refresh`, creates a fresh session token, and redirects in-browser. No DM needed here since the user is already authenticated; the DM-only rule applies to the Discord command entrypoint only.

**Admin dashboard:**
- Member list: avatar, name, Discord ID, exclude/re-include button.
- Excluded members shown dimmed with a red dashed border.
- Card pool stats: eligible count, excluded count, total cards in circulation.
- "Sync Member List from Discord" button.

---

## Verification

1. Run bot locally with `SUPERPAL_TOKEN`, `GUILD_ID`, `CHANNEL_ID` set.
2. Confirm `/draw-card` posts an embed with correct rarity color to the channel.
3. Confirm a second `/draw-card` in the same week is rejected with a clear message.
4. Confirm Super Pal gets 2 draws before hitting the limit.
5. Run `/my-collection` — verify link arrives via DM, not in channel.
6. Click link → collection loads with owned cards + ??? silhouettes.
7. Click link again → `expired.html` shown.
8. After 24h (or manually expire the session in DB), confirm session cookie is rejected.
9. Run `/trade-in` with <3 duplicates → rejected. With 3 → card received.
10. Run `/upgrade` on Legendary → rejected with clear message.
11. Run `/admin-link` as non-Clippy user → rejected. As Clippy → DM received.
12. Admin dashboard: exclude a member → they no longer appear in draw pool. Re-include → restored.
13. Admin sync → new server members appear in `members` table and as silhouettes in collection view.
