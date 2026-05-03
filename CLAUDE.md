# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Create venv and install all deps (first time or after requirements change)
uv venv --python 3.13 && uv pip install -r requirements.txt -r requirements-dev.txt

# Run all tests
.venv/bin/python -m pytest tests/ -q

# Run a single test file
.venv/bin/python -m pytest tests/cards/test_service.py -q

# Run a single test by name
.venv/bin/python -m pytest tests/cards/test_service.py -k "test_draw_card" -q

# Run the bot locally (requires .env with all vars from env.py)
cd src && ../.venv/bin/python bot.py
```

There is no linter/formatter configured in the project yet (no ruff, black, or mypy config).

## Architecture

Two processes run concurrently inside a single container via `asyncio.gather` in `src/bot.py`:

1. **Discord bot** (`discord.py`) — handles slash commands, weekly role rotation, card game commands
2. **FastAPI webapp** (uvicorn) — serves the card collection UI at `WEBAPP_BASE_URL` (production: `cards.bring-us.com`)

The webapp's `lifespan` handler in `src/superpal/webapp/app.py` calls `init_db()` before the first request, ensuring all SQLite schema migrations run before any HTTP handler touches the DB. The bot's `on_ready` event also calls `init_db()` — both are safe because all migrations are idempotent `ALTER TABLE ... ADD COLUMN` wrapped in `try/except OperationalError`.

### Card game data layer (`src/superpal/cards/`)

- **`db.py`** — `DB_PATH` (env `CARDS_DB_PATH`, default `cards.db`), schema definition, `init_db()` with inline migrations
- **`service.py`** — all async business logic: draw, trade-in, upgrade, peer trades, magic links, admin ops
- **`models.py`** — dataclasses (`Member`, `UserCard`, `MagicLink`, `PendingTrade`) and rarity constants
- **`embeds.py`** — Discord embed builders for card draw results

### Webapp (`src/superpal/webapp/`)

- **`app.py`** — `create_app()` factory, mounts `/static/avatars` from the persistent volume and `/static` from the package directory
- **`routes.py`** — all FastAPI routes; admin routes are guarded by `session.link_type == "admin"`
- **`auth.py`** — session cookie `bringus_session` (24h, httponly, secure); `get_session_from_request` is the auth check used on every protected route
- **`templates/`** — Jinja2 HTML templates (Discord dark theme)

### Auth flow

`/admin-link` or `/my-collection` in Discord → `generate_magic_link()` writes a token to `magic_links` table → bot DMs user a `WEBAPP_BASE_URL/link/{token}` URL → `use_magic_link()` validates and issues a session cookie → subsequent requests authenticate via `get_session_from_request()`.

### Deployment

- Two Docker images: `adamhurm/discord-super-pal` (main bot + webapp) and `adamhurm/discord-spin-the-wheel` (separate spin-the-wheel bot), both in the same k8s Deployment pod in the `discord` namespace.
- **Release process**: push a git tag (`git tag vX.Y.Z && git push origin vX.Y.Z`), then create a GitHub release for that tag (`gh release create vX.Y.Z`). The `docker-build-and-upload.yml` workflow triggers on `release: published`, not on tag push alone.
- k8s manifests live in a separate homelab repo at `~/Desktop/homelab/k8s/discord-super-pal-of-the-week/`. Bump the image tag in `deployment.yaml` there after each release.
- The SQLite DB and card images are on a PVC mounted at `/data` (`CARDS_DB_PATH=/data/cards.db`). To run ad-hoc SQL against the live DB, use the Python stdlib since the container has no `sqlite3` binary: `kubectl exec -n discord deploy/super-pal -- python3 -c "import sqlite3; ..."`.

### Environment variables

Required: `SUPERPAL_TOKEN`, `GUILD_ID`, `CHANNEL_ID`
Optional: `EMOJI_GUILD_ID`, `ART_CHANNEL_ID`, `OPENAI_API_KEY`, `GPT_ASSISTANT_ID`, `GPT_ASSISTANT_THREAD_ID`, `WEBAPP_PORT` (default 8080), `WEBAPP_BASE_URL`, `CARDS_DB_PATH`

All loaded via `python-dotenv` in `src/superpal/env.py`; missing required vars log an error but don't hard-crash at import time.

### Known pre-existing test failures

`tests/webapp/test_routes.py::test_link_redirect_on_valid_token` and `test_link_expired_returns_expired_page` both fail because they patch a non-existent `consume_magic_link` symbol (the real function is `use_magic_link`). These were broken before v1.4.x and are not regressions.
