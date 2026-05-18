---
title: Fight Leaderboard & Command Rename
date: 2026-05-18
status: approved
---

## Summary

Add a `/card-fight-leaderboard` Discord slash command that ranks players by fight stats. Simultaneously rename the existing `/card-leaderboard` command to `/card-collection-leaderboard` for naming consistency.

## Scope

1. Rename `/card-leaderboard` → `/card-collection-leaderboard` in `bot.py`.
2. Add `get_fight_leaderboard(sort_by: str)` to `fight_service.py`.
3. Add `/card-fight-leaderboard` slash command to `bot.py`.
4. Update `docs/cards-interface-matrix.md` and `docs/feature-backlog.md`.

No schema changes required. All fight data needed already exists in `fights` and `fight_log`.

## Data layer — `fight_service.py`

New function `get_fight_leaderboard(sort_by: str) -> list[dict]` returns up to 10 rows of `{discord_id, display_name, total}`. One SQL query per sort option:

**wins** — completed fights where `winner_id = player`:
```sql
SELECT m.discord_id, m.display_name, COUNT(*) AS total
FROM fights f JOIN members m ON m.discord_id = f.winner_id
WHERE f.status = 'completed'
GROUP BY f.winner_id ORDER BY total DESC LIMIT 10
```

**win_rate** — win % among players with ≥ 3 completed fights. `total` is stored as a float (e.g. `0.667`); the command formats it as `"67% (12 fights)"`:
```sql
SELECT discord_id, display_name,
  CAST(wins AS REAL) / total_fights AS total,
  total_fights
FROM (
  SELECT m.discord_id, m.display_name,
    SUM(CASE WHEN f.winner_id = m.discord_id THEN 1 ELSE 0 END) AS wins,
    COUNT(*) AS total_fights
  FROM members m
  JOIN fights f ON (f.challenger_id = m.discord_id OR f.opponent_id = m.discord_id)
  WHERE f.status = 'completed'
  GROUP BY m.discord_id
  HAVING total_fights >= 3
)
ORDER BY total DESC LIMIT 10
```

**fights_played** — total completed fights (challenger or opponent):
```sql
SELECT m.discord_id, m.display_name, COUNT(*) AS total
FROM members m
JOIN fights f ON (f.challenger_id = m.discord_id OR f.opponent_id = m.discord_id)
WHERE f.status = 'completed'
GROUP BY m.discord_id ORDER BY total DESC LIMIT 10
```

**pringle_balance** — current balance from `members`:
```sql
SELECT discord_id, display_name, pringle_balance AS total
FROM members WHERE is_excluded = 0
ORDER BY pringle_balance DESC LIMIT 10
```

**escapes** — successful escapes: completed fights where the `run` actor is not the winner:
```sql
SELECT fl.actor_id AS discord_id, m.display_name, COUNT(*) AS total
FROM fight_log fl
JOIN fights f ON f.id = fl.fight_id
JOIN members m ON m.discord_id = fl.actor_id
WHERE fl.action_type = 'run'
  AND f.status = 'completed'
  AND f.winner_id != fl.actor_id
GROUP BY fl.actor_id ORDER BY total DESC LIMIT 10
```

## Bot command — `bot.py`

```
/card-fight-leaderboard [sort_by]
```

Sort choices (slash command `discord.app_commands.choices`):

| Label | Value |
|---|---|
| Most Wins | `wins` |
| Best Win Rate | `win_rate` |
| Most Fights Played | `fights_played` |
| Pringle Balance | `pringle_balance` |
| Most Escapes | `escapes` |

Default: `wins`.

Embed format mirrors `card-collection-leaderboard`:
- Discord blue (`0x5865F2`), numbered list, `"No fights recorded yet"` fallback.
- Win rate rows: `"1. PlayerName — 67% (12 fights)"` instead of a raw count.
- Other rows: `"1. PlayerName — 42 wins"` (unit label varies per sort).

## Command rename

`card_leaderboard_command` function in `bot.py`:
- `name="card-leaderboard"` → `name="card-collection-leaderboard"`
- `description` stays: `"Show the top 10 card collectors"`

## Docs updates

- `docs/cards-interface-matrix.md`: update Leaderboard row command name; add new Fight Leaderboard row.
- `docs/feature-backlog.md`: update Leaderboard entry command name; add Fight Leaderboard under Completed once shipped.

## Error handling

- Unknown `sort_by` value: raise `ValueError` in service; command falls back to `wins`.
- Empty result set: embed with `"No fights recorded yet"` message.

## Testing

Existing tests in `tests/` do not cover fight leaderboard. No new tests required beyond what already exists — fight service functions are integration-tested against an in-memory DB and the leaderboard query is simple enough to verify manually.
