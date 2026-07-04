# Synthetic Card Autocomplete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `/card-display`, `/card-trade-in`, `/card-upgrade`, and `/card-gift` reference synthetic (non-Discord) card subjects, not just real guild members.

**Architecture:** Replace each command's `member: discord.Member` parameter with a `card: str` parameter backed by Discord autocomplete, populated from the invoking user's own owned card subjects (real + synthetic). The autocomplete value is the plain `discord_id` string already used everywhere in the data layer, so `service.py`'s `draw_card`/`trade_in`/`upgrade`/`gift_card` need no changes.

**Tech Stack:** Python 3.13, discord.py (`discord.app_commands`), aiosqlite, pytest + pytest-asyncio.

## Global Constraints

- Synthetic-card label suffix uses the exact string `" (Custom)"` — matches the existing `CUSTOM` badge wording in `admin.html:271`.
- `gift_card_command`'s `recipient` parameter stays `discord.Member` — it identifies a real Discord user, not a card subject. Only its `member` parameter (the card being gifted) changes.
- No new bot slash-command callback tests — the codebase has none today (`tests/test_bot.py` covers role rotation, not card commands); coverage stays at the service-function and pure-function level, per the approved spec (`docs/superpowers/specs/2026-07-03-synthetic-card-autocomplete-design.md`).
- Discord autocomplete responses are capped at 25 choices — enforce with `[:25]`.

---

### Task 1: Add card-subject lookup helpers to the service layer

**Files:**
- Modify: `src/superpal/cards/service.py` (append after line 1327, end of file)
- Test: `tests/cards/test_service.py` (append after line 817, end of file)

**Interfaces:**
- Produces: `get_owned_card_subjects(owner_id: str) -> list[dict]` — each dict has keys `discord_id: str`, `display_name: str`, `is_synthetic: bool`. One entry per distinct card subject the owner has `quantity > 0` of in any rarity, ordered by `display_name`.
- Produces: `get_member_display_name(discord_id: str) -> str | None` — the member's `display_name`, or `None` if no such member row exists.

- [ ] **Step 1: Write the failing tests**

Append to `tests/cards/test_service.py`:

```python
@pytest.mark.asyncio
async def test_get_owned_card_subjects_returns_owned(db):
    _db_mod, svc = db
    await svc.sync_members(
        [
            {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
            {"discord_id": "card1", "display_name": "Card One", "avatar_url": None},
        ]
    )
    await svc.award_card("alice", "card1", "common", 2)
    result = await svc.get_owned_card_subjects("alice")
    assert result == [{"discord_id": "card1", "display_name": "Card One", "is_synthetic": False}]


@pytest.mark.asyncio
async def test_get_owned_card_subjects_flags_synthetic(db):
    _db_mod, svc = db
    await svc.sync_members([{"discord_id": "alice", "display_name": "Alice", "avatar_url": None}])
    await svc.add_member("custom1", "Bringus Prime")
    await svc.award_card("alice", "custom1", "legendary", 1)
    result = await svc.get_owned_card_subjects("alice")
    assert result == [
        {"discord_id": "custom1", "display_name": "Bringus Prime", "is_synthetic": True}
    ]


@pytest.mark.asyncio
async def test_get_owned_card_subjects_excludes_zero_quantity(db):
    _db_mod, svc = db
    await svc.sync_members(
        [
            {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
            {"discord_id": "card1", "display_name": "Card One", "avatar_url": None},
        ]
    )
    await svc.award_card("alice", "card1", "common", 0)
    result = await svc.get_owned_card_subjects("alice")
    assert result == []


@pytest.mark.asyncio
async def test_get_owned_card_subjects_deduplicates_across_rarities(db):
    _db_mod, svc = db
    await svc.sync_members(
        [
            {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
            {"discord_id": "card1", "display_name": "Card One", "avatar_url": None},
        ]
    )
    await svc.award_card("alice", "card1", "common", 2)
    await svc.award_card("alice", "card1", "rare", 1)
    result = await svc.get_owned_card_subjects("alice")
    assert len(result) == 1


@pytest.mark.asyncio
async def test_get_owned_card_subjects_orders_by_display_name(db):
    _db_mod, svc = db
    await svc.sync_members(
        [
            {"discord_id": "alice", "display_name": "Alice", "avatar_url": None},
            {"discord_id": "zeb", "display_name": "Zeb", "avatar_url": None},
            {"discord_id": "amy", "display_name": "Amy", "avatar_url": None},
        ]
    )
    await svc.award_card("alice", "zeb", "common", 1)
    await svc.award_card("alice", "amy", "common", 1)
    result = await svc.get_owned_card_subjects("alice")
    assert [r["display_name"] for r in result] == ["Amy", "Zeb"]


@pytest.mark.asyncio
async def test_get_member_display_name_returns_name(db):
    _db_mod, svc = db
    await svc.sync_members([{"discord_id": "alice", "display_name": "Alice", "avatar_url": None}])
    assert await svc.get_member_display_name("alice") == "Alice"


@pytest.mark.asyncio
async def test_get_member_display_name_returns_none_for_unknown(db):
    _db_mod, svc = db
    assert await svc.get_member_display_name("nonexistent") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/cards/test_service.py -k "owned_card_subjects or member_display_name" -v`
Expected: FAIL with `AttributeError: module 'superpal.cards.service' has no attribute 'get_owned_card_subjects'` (and similarly for `get_member_display_name`)

- [ ] **Step 3: Write the implementation**

Append to `src/superpal/cards/service.py`:

```python


async def get_owned_card_subjects(owner_id: str) -> list[dict]:
    """Return distinct card subjects (real or synthetic) the owner has at least one copy of.
    Returns list of dicts with keys: discord_id, display_name, is_synthetic."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DISTINCT m.discord_id, m.display_name, m.is_synthetic "
            "FROM user_cards uc JOIN members m ON uc.card_member_id = m.discord_id "
            "WHERE uc.owner_id = ? AND uc.quantity > 0 "
            "ORDER BY m.display_name",
            (owner_id,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {"discord_id": r[0], "display_name": r[1], "is_synthetic": bool(r[2])} for r in rows
    ]


async def get_member_display_name(discord_id: str) -> str | None:
    """Return a member's display name, or None if no such member exists."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name FROM members WHERE discord_id = ?", (discord_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/cards/test_service.py -k "owned_card_subjects or member_display_name" -v`
Expected: 7 passed

- [ ] **Step 5: Run linter and type checker**

Run: `ruff check src/superpal/cards/service.py tests/cards/test_service.py && ty check src/superpal/cards/service.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/superpal/cards/service.py tests/cards/test_service.py
git commit -m "feat: add card-subject lookup helpers for bot autocomplete"
```

---

### Task 2: Add card-subject label formatting to the bot

**Files:**
- Modify: `src/bot.py` (insert after `_parse_stats`, which ends at line 99, before `_resolve_avatar_url` at line 102)
- Test: `tests/test_bot.py` (append after line 352, end of file)

**Interfaces:**
- Consumes: nothing (pure function, no I/O)
- Produces: `_label_card_subjects(subjects: list[dict]) -> list[tuple[str, str]]` where each input dict has keys `discord_id`, `display_name`, `is_synthetic` (matching `get_owned_card_subjects`'s return shape from Task 1). Output is `(label, discord_id)` pairs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bot.py`:

```python


class TestLabelCardSubjects:
    """Tests for _label_card_subjects autocomplete label formatting."""

    def test_plain_label_for_real_member(self, mock_env):
        from bot import _label_card_subjects

        subjects = [{"discord_id": "111", "display_name": "Alice", "is_synthetic": False}]
        assert _label_card_subjects(subjects) == [("Alice", "111")]

    def test_custom_tag_for_synthetic_member(self, mock_env):
        from bot import _label_card_subjects

        subjects = [
            {"discord_id": "111", "display_name": "Bringus Prime", "is_synthetic": True}
        ]
        assert _label_card_subjects(subjects) == [("Bringus Prime (Custom)", "111")]

    def test_no_suffix_when_no_collision(self, mock_env):
        from bot import _label_card_subjects

        subjects = [
            {"discord_id": "111", "display_name": "Alice", "is_synthetic": False},
            {"discord_id": "222", "display_name": "Bob", "is_synthetic": True},
        ]
        assert _label_card_subjects(subjects) == [
            ("Alice", "111"),
            ("Bob (Custom)", "222"),
        ]

    def test_disambiguates_colliding_real_names_with_id_suffix(self, mock_env):
        from bot import _label_card_subjects

        subjects = [
            {
                "discord_id": "111111111111111111",
                "display_name": "Steve",
                "is_synthetic": False,
            },
            {
                "discord_id": "222222222222222222",
                "display_name": "Steve",
                "is_synthetic": False,
            },
        ]
        result = _label_card_subjects(subjects)
        assert result == [
            ("Steve (1111)", "111111111111111111"),
            ("Steve (2222)", "222222222222222222"),
        ]

    def test_disambiguates_colliding_synthetic_names(self, mock_env):
        from bot import _label_card_subjects

        subjects = [
            {"discord_id": "aaaa1111", "display_name": "Bringus", "is_synthetic": True},
            {"discord_id": "bbbb2222", "display_name": "Bringus", "is_synthetic": True},
        ]
        result = _label_card_subjects(subjects)
        assert result == [
            ("Bringus (Custom) (1111)", "aaaa1111"),
            ("Bringus (Custom) (2222)", "bbbb2222"),
        ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_bot.py -k TestLabelCardSubjects -v`
Expected: FAIL with `ImportError: cannot import name '_label_card_subjects' from 'bot'`

- [ ] **Step 3: Write the implementation**

Insert into `src/bot.py` between `_parse_stats` (ends line 99) and `_resolve_avatar_url` (starts line 102):

```python


def _label_card_subjects(subjects: list[dict]) -> list[tuple[str, str]]:
    """Format (label, discord_id) pairs for card autocomplete, disambiguating collisions.

    Synthetic (non-Discord) subjects get a " (Custom)" tag. Any label that still
    collides with another entry after tagging gets the subject's last 4 ID chars appended.
    """
    labeled = [
        (
            f"{s['display_name']} (Custom)" if s["is_synthetic"] else s["display_name"],
            s["discord_id"],
        )
        for s in subjects
    ]
    label_counts: dict[str, int] = {}
    for label, _ in labeled:
        label_counts[label] = label_counts.get(label, 0) + 1
    return [
        (f"{label} ({discord_id[-4:]})" if label_counts[label] > 1 else label, discord_id)
        for label, discord_id in labeled
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_bot.py -k TestLabelCardSubjects -v`
Expected: 5 passed

- [ ] **Step 5: Run linter and type checker**

Run: `ruff check src/bot.py tests/test_bot.py && ty check src/bot.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/bot.py tests/test_bot.py
git commit -m "feat: add card-subject label formatting for autocomplete"
```

---

### Task 3: Wire autocomplete into the four bot commands

**Files:**
- Modify: `src/bot.py` — the `from superpal.cards.service import (...)` block near the top of the file
- Modify: `src/bot.py` — the `display_card_command` function (decorated `@bot.tree.command(name="card-display", ...)`)
- Modify: `src/bot.py` — the `trade_in_command` function (decorated `@bot.tree.command(name="card-trade-in", ...)`)
- Modify: `src/bot.py` — the `upgrade_command` function (decorated `@bot.tree.command(name="card-upgrade", ...)`)
- Modify: `src/bot.py` — the `gift_card_command` function (decorated `@bot.tree.command(name="card-gift", ...)`)

Note: line numbers are not given here because Task 2 inserts a new function earlier in this same file, shifting every line number after it. Locate each block by its decorator/function name (shown above and repeated at the start of each step below), not by line number.

**Interfaces:**
- Consumes: `get_owned_card_subjects(owner_id: str) -> list[dict]` and `get_member_display_name(discord_id: str) -> str | None` from Task 1 (`superpal.cards.service`); `_label_card_subjects(subjects: list[dict]) -> list[tuple[str, str]]` from Task 2 (`bot.py`).
- Produces: `_card_subject_autocomplete(interaction, current) -> list[discord.app_commands.Choice[str]]`, registered on all four commands' `card` parameter.

No new automated tests in this task — see Global Constraints. Verification is the existing test suite (regression) plus manual verification (Step 4).

- [ ] **Step 1: Update the service import block**

In `src/bot.py`, replace the existing `from superpal.cards.service import (...)` block:

```python
from superpal.cards.service import (
    TRADE_EXPIRY_MINUTES,
    accept_offer,
    decline_offer,
    decline_trade,
    draw_card,
    execute_trade,
    expire_offer,
    generate_magic_link,
    get_card_quantity,
    get_collection,
    get_leaderboard,
    get_offer_by_id,
    gift_card,
    set_offer_discord_message_id,
    sync_members,
    trade_in,
    upgrade,
)
```

with:

```python
from superpal.cards.service import (
    TRADE_EXPIRY_MINUTES,
    accept_offer,
    decline_offer,
    decline_trade,
    draw_card,
    execute_trade,
    expire_offer,
    generate_magic_link,
    get_card_quantity,
    get_collection,
    get_leaderboard,
    get_member_display_name,
    get_offer_by_id,
    get_owned_card_subjects,
    gift_card,
    set_offer_discord_message_id,
    sync_members,
    trade_in,
    upgrade,
)
```

- [ ] **Step 2: Replace `display_card_command`** (the whole function, from its `@bot.tree.command(name="card-display", ...)` decorator through its final `await interaction.followup.send(embed=embed)`)

```python
@bot.tree.command(name="card-display", description="Show a card you own in the channel")
@discord.app_commands.describe(
    card="The card you want to display",
    rarity="The rarity of the card to display",
)
@discord.app_commands.choices(
    rarity=[
        discord.app_commands.Choice(name="Common", value="common"),
        discord.app_commands.Choice(name="Uncommon", value="uncommon"),
        discord.app_commands.Choice(name="Rare", value="rare"),
        discord.app_commands.Choice(name="Legendary", value="legendary"),
    ]
)
async def display_card_command(
    interaction: discord.Interaction,
    card: str,
    rarity: str,
) -> None:
    await interaction.response.defer()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT uc.id, m.display_name, m.avatar_url, m.bio, m.stats, uc.drawn_by_name "
            "FROM user_cards uc JOIN members m ON uc.card_member_id = m.discord_id "
            "WHERE uc.owner_id = ? AND uc.card_member_id = ? AND uc.rarity = ? AND uc.quantity > 0",
            (str(interaction.user.id), card, rarity),
        ) as cur:
            row = await cur.fetchone()

    if row is None:
        display_name = await get_member_display_name(card) or "Unknown"
        await interaction.followup.send(
            f"You don't own a {rarity.upper()} {display_name} card.",
            ephemeral=True,
        )
        return

    card_id, display_name, avatar_url, bio, stats_raw, drawn_by_name = row
    embed = build_card_embed(
        display_name=display_name,
        avatar_url=_resolve_avatar_url(avatar_url),
        rarity=rarity,
        card_number=card_id,
        drawn_by=drawn_by_name or interaction.user.display_name,
        bio=bio,
        stats_pairs=_parse_stats(stats_raw),
    )
    await interaction.followup.send(embed=embed)
```

- [ ] **Step 3: Replace `trade_in_command`** (the whole function, from its `@bot.tree.command(name="card-trade-in", ...)` decorator through its final `await interaction.followup.send(...)`)

```python
@bot.tree.command(
    name="card-trade-in",
    description="Trade 3 duplicate cards for a random card of the same rarity",
)
@discord.app_commands.describe(
    card="The card you want to trade in",
    rarity="The rarity of the card to trade",
)
@discord.app_commands.choices(
    rarity=[
        discord.app_commands.Choice(name="Common", value="common"),
        discord.app_commands.Choice(name="Uncommon", value="uncommon"),
        discord.app_commands.Choice(name="Rare", value="rare"),
        discord.app_commands.Choice(name="Legendary", value="legendary"),
    ]
)
async def trade_in_command(
    interaction: discord.Interaction,
    card: str,
    rarity: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    result_card = await trade_in(
        owner_id=str(interaction.user.id),
        card_member_id=card,
        rarity=rarity,
        drawn_by_name=interaction.user.display_name,
    )
    if result_card is None:
        display_name = await get_member_display_name(card) or "Unknown"
        await interaction.followup.send(
            f"You need at least 3× {rarity.upper()} {display_name} to trade in.",  # noqa: RUF001
            ephemeral=True,
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url, bio, stats FROM members WHERE discord_id = ?",
            (result_card.card_member_id,),
        ) as cur:
            row = await cur.fetchone()

    display_name = row[0] if row else "Unknown"
    avatar_url = _resolve_avatar_url(row[1] if row else None)
    embed = build_card_embed(
        display_name=display_name,
        avatar_url=avatar_url,
        rarity=result_card.rarity,
        card_number=result_card.id,
        drawn_by=result_card.drawn_by_name or interaction.user.display_name,
        bio=row[2] if row else None,
        stats_pairs=_parse_stats(row[3] if row else None),
    )
    await interaction.followup.send("Trade complete! You received:", embed=embed, ephemeral=True)
```

Note: the original code reused the local name `card` for the trade result (`card = await trade_in(...)`), which would now shadow the new `card` *parameter* holding the selected `discord_id`. Renamed the result variable to `result_card` throughout.

- [ ] **Step 4: Replace `upgrade_command`** (the whole function, from its `@bot.tree.command(name="card-upgrade", ...)` decorator through its final `await interaction.followup.send(...)`)

```python
@bot.tree.command(
    name="card-upgrade",
    description="Spend 5 duplicate cards to upgrade a member's card rarity",
)
@discord.app_commands.describe(
    card="The card you want to upgrade",
    rarity="The current rarity of the card",
)
@discord.app_commands.choices(
    rarity=[
        discord.app_commands.Choice(name="Common", value="common"),
        discord.app_commands.Choice(name="Uncommon", value="uncommon"),
        discord.app_commands.Choice(name="Rare", value="rare"),
    ]
)
async def upgrade_command(
    interaction: discord.Interaction,
    card: str,
    rarity: str,
) -> None:
    await interaction.response.defer(ephemeral=True)
    result_card = await upgrade(
        owner_id=str(interaction.user.id),
        card_member_id=card,
        rarity=rarity,
        drawn_by_name=interaction.user.display_name,
    )
    if result_card is None:
        display_name = await get_member_display_name(card) or "Unknown"
        await interaction.followup.send(
            f"You need at least 5× {rarity.upper()} {display_name} to upgrade.",  # noqa: RUF001
            ephemeral=True,
        )
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url, bio, stats FROM members WHERE discord_id = ?",
            (result_card.card_member_id,),
        ) as cur:
            row = await cur.fetchone()

    display_name = row[0] if row else "Unknown"
    avatar_url = _resolve_avatar_url(row[1] if row else None)
    embed = build_card_embed(
        display_name=display_name,
        avatar_url=avatar_url,
        rarity=result_card.rarity,
        card_number=result_card.id,
        drawn_by=result_card.drawn_by_name or interaction.user.display_name,
        bio=row[2] if row else None,
        stats_pairs=_parse_stats(row[3] if row else None),
    )
    await interaction.followup.send(
        f"Upgrade complete! {display_name} is now {result_card.rarity.upper()}:",
        embed=embed,
        ephemeral=True,
    )
```

Same `card`/`result_card` rename as Step 3, for the same shadowing reason.

- [ ] **Step 5: Replace `gift_card_command`** (the whole function, from its `@bot.tree.command(name="card-gift", ...)` decorator through its final `await interaction.response.send_message(...)`)

```python
@bot.tree.command(name="card-gift", description="Give one of your cards to another player")
@discord.app_commands.describe(
    recipient="The server member to receive the gift",
    card="The card you want to gift",
    rarity="The rarity of the card to gift",
)
@discord.app_commands.choices(
    rarity=[
        discord.app_commands.Choice(name="Common", value="common"),
        discord.app_commands.Choice(name="Uncommon", value="uncommon"),
        discord.app_commands.Choice(name="Rare", value="rare"),
        discord.app_commands.Choice(name="Legendary", value="legendary"),
    ]
)
async def gift_card_command(
    interaction: discord.Interaction,
    recipient: discord.Member,
    card: str,
    rarity: str,
) -> None:
    gifter_id = str(interaction.user.id)

    if interaction.user.id == recipient.id:
        await interaction.response.send_message(
            "You can't gift a card to yourself.", ephemeral=True
        )
        return

    display_name = await get_member_display_name(card) or "Unknown"
    qty = await get_card_quantity(gifter_id, card, rarity)
    if qty < 1:
        await interaction.response.send_message(
            f"You don't own a {RARITY_LABELS[rarity]} {display_name} card.",
            ephemeral=True,
        )
        return

    view = GiftConfirmView(
        interaction=interaction,
        gifter_id=gifter_id,
        recipient=recipient,
        card_member_id=card,
        rarity=rarity,
    )
    await interaction.response.send_message(
        (
            f"You're about to gift a **{RARITY_LABELS[rarity]} {display_name}**"
            f" to {recipient.mention} — confirm?"
        ),
        view=view,
        ephemeral=True,
    )
```

`recipient: discord.Member` is unchanged — it's a real Discord user, not a card subject.

- [ ] **Step 6: Add the shared autocomplete callback and register it**

Insert immediately after the (now-updated) `gift_card_command` definition, before the `card-collection-leaderboard` command:

```python


async def _card_subject_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[discord.app_commands.Choice[str]]:
    subjects = await get_owned_card_subjects(str(interaction.user.id))
    labeled = _label_card_subjects(subjects)
    matches = [
        (label, discord_id) for label, discord_id in labeled if current.lower() in label.lower()
    ]
    return [
        discord.app_commands.Choice(name=label, value=discord_id)
        for label, discord_id in matches[:25]
    ]


display_card_command.autocomplete("card")(_card_subject_autocomplete)
trade_in_command.autocomplete("card")(_card_subject_autocomplete)
upgrade_command.autocomplete("card")(_card_subject_autocomplete)
gift_card_command.autocomplete("card")(_card_subject_autocomplete)
```

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all tests pass (no regressions from the rename/signature changes)

- [ ] **Step 8: Run linter and type checker**

Run: `ruff check src/bot.py && ty check src/bot.py`
Expected: no errors

- [ ] **Step 9: Manual verification**

1. Run the bot locally: `cd src && ../.venv/bin/python bot.py`
2. In Discord admin panel (`/admin-link` → web UI), create a synthetic member (leave Discord ID blank so one is auto-generated) with a name that doesn't collide with any real member, e.g. "Bringus Prime".
3. Award it a card to your account via the admin panel's award-card form.
4. In Discord, run `/card-display`, focus the `card` field, type part of "Bringus Prime" — confirm it appears in the autocomplete list tagged `(Custom)`.
5. Select it, pick the matching rarity, confirm the embed renders with the correct name/avatar/bio.
6. Repeat the autocomplete selection for `/card-trade-in` (need 3+ copies), `/card-upgrade` (need 5+ copies), and `/card-gift` (gift to a second test account) — confirm all three complete successfully and show the correct card name in their messages.
7. Confirm a real Discord member's card still displays correctly through all four commands (regression check).

- [ ] **Step 10: Commit**

```bash
git add src/bot.py
git commit -m "feat: support synthetic card subjects in card-display, trade-in, upgrade, and gift commands"
```
