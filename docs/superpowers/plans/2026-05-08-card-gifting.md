# Card Gifting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/card-gift @recipient @member rarity` — a one-step Discord command that lets a player give a card they own to another player, with an ephemeral confirmation step before the gift is announced publicly.

**Architecture:** Extend `build_card_embed` with an `action_label` parameter so the footer can say "gifted by X to Y" instead of "drawn by X". Add a `gift_card` service function in `service.py` that atomically deducts from the gifter and awards to the recipient. Add a `GiftConfirmView` Discord UI view and `/card-gift` slash command in `bot.py`.

**Tech Stack:** Python 3.13, discord.py (`discord.ui.View`), aiosqlite, pytest-asyncio.

---

### Task 1: Extend `build_card_embed` with `action_label`

The embed footer currently hardcodes `"drawn by"`. Add an optional `action_label` parameter so gift embeds can say `"gifted by Alice to Bob"`.

**Files:**
- Modify: `src/superpal/cards/embeds.py`
- Modify: `tests/cards/test_embeds.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/cards/test_embeds.py`:

```python
def test_build_card_embed_custom_action_label():
    embed = build_card_embed(
        display_name="Bingus",
        avatar_url=None,
        rarity="rare",
        card_number=5,
        drawn_by="Alice",
        action_label="gifted by Gifter to",
    )
    assert "gifted by Gifter to Alice" in embed.footer.text
    assert "drawn by" not in embed.footer.text


def test_build_card_embed_default_action_label():
    embed = build_card_embed(
        display_name="Bingus",
        avatar_url=None,
        rarity="common",
        card_number=1,
        drawn_by="Alice",
    )
    assert "drawn by Alice" in embed.footer.text
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/python -m pytest tests/cards/test_embeds.py::test_build_card_embed_custom_action_label -v
```

Expected: FAIL with `TypeError` (unexpected keyword argument) or assertion error.

- [ ] **Step 3: Implement**

In `src/superpal/cards/embeds.py`, add `action_label: str = "drawn by"` to the signature and update the footer line:

```python
def build_card_embed(
    *,
    display_name: str,
    avatar_url: Optional[str],
    rarity: str,
    card_number: int,
    drawn_by: str,
    bio: Optional[str] = None,
    stats_pairs: Optional[list[tuple[str, str]]] = None,
    action_label: str = "drawn by",
) -> discord.Embed:
    """Build a Discord embed for a drawn card."""
    color = discord.Color(RARITY_COLORS[rarity])
    label = RARITY_LABELS[rarity]

    embed = discord.Embed(description=bio if bio else None, color=color)
    embed.set_author(name=display_name, icon_url=avatar_url)
    embed.set_footer(text=f"{label} · #{card_number} · Bringus Card Game · {action_label} {drawn_by}")
    embed.set_thumbnail(url=avatar_url)

    if stats_pairs:
        value = "\n".join(f"**{k}** {v}" for k, v in stats_pairs)
        embed.add_field(name="Stats", value=value, inline=False)

    return embed
```

- [ ] **Step 4: Run all embed tests**

```
.venv/bin/python -m pytest tests/cards/test_embeds.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/superpal/cards/embeds.py tests/cards/test_embeds.py
git commit -m "feat: add action_label param to build_card_embed for gift footer"
```

---

### Task 2: Add `gift_card` service function

Atomically deducts one card from the gifter and awards it to the recipient inside a single `BEGIN EXCLUSIVE` transaction.

**Files:**
- Modify: `src/superpal/cards/service.py`
- Modify: `tests/cards/test_service.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/cards/test_service.py`:

```python
@pytest.mark.asyncio
async def test_gift_card_transfers_card(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
        {"discord_id": "333", "display_name": "CardMember", "avatar_url": None},
    ])
    # Give Alice a card to gift
    await svc.award_card("111", "333", "rare", 1)

    card, err = await svc.gift_card(
        gifter_id="111",
        recipient_id="222",
        card_member_id="333",
        rarity="rare",
        drawn_by_name="Alice",
    )

    assert err is None
    assert card is not None
    assert card.owner_id == "222"
    assert card.card_member_id == "333"
    assert card.rarity == "rare"
    # Alice's card should be gone
    assert await svc.get_card_quantity("111", "333", "rare") == 0
    # Bob should have it
    assert await svc.get_card_quantity("222", "333", "rare") == 1


@pytest.mark.asyncio
async def test_gift_card_gifter_keeps_extra_copy(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
        {"discord_id": "333", "display_name": "CardMember", "avatar_url": None},
    ])
    await svc.award_card("111", "333", "common", 3)

    card, err = await svc.gift_card("111", "222", "333", "common", "Alice")

    assert err is None
    assert await svc.get_card_quantity("111", "333", "common") == 2
    assert await svc.get_card_quantity("222", "333", "common") == 1


@pytest.mark.asyncio
async def test_gift_card_fails_when_not_owned(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "222", "display_name": "Bob", "avatar_url": None},
        {"discord_id": "333", "display_name": "CardMember", "avatar_url": None},
    ])

    card, err = await svc.gift_card("111", "222", "333", "rare", "Alice")

    assert card is None
    assert err == "no_card"


@pytest.mark.asyncio
async def test_gift_card_fails_self_gift(db):
    db_mod, svc = db
    await svc.sync_members([
        {"discord_id": "111", "display_name": "Alice", "avatar_url": None},
        {"discord_id": "333", "display_name": "CardMember", "avatar_url": None},
    ])
    await svc.award_card("111", "333", "common", 1)

    card, err = await svc.gift_card("111", "111", "333", "common", "Alice")

    assert card is None
    assert err == "self_gift"
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv/bin/python -m pytest tests/cards/test_service.py::test_gift_card_transfers_card tests/cards/test_service.py::test_gift_card_gifter_keeps_extra_copy tests/cards/test_service.py::test_gift_card_fails_when_not_owned tests/cards/test_service.py::test_gift_card_fails_self_gift -v
```

Expected: all FAIL with `AttributeError: module has no attribute 'gift_card'`.

- [ ] **Step 3: Implement `gift_card` in `src/superpal/cards/service.py`**

Add this function after `get_card_quantity`:

```python
async def gift_card(
    gifter_id: str,
    recipient_id: str,
    card_member_id: str,
    rarity: str,
    drawn_by_name: str = "",
) -> tuple[Optional[UserCard], Optional[str]]:
    """Transfer one copy of [card_member_id, rarity] from gifter to recipient.
    Returns (UserCard, None) on success or (None, reason) on failure.
    Reasons: 'self_gift', 'no_card'."""
    if gifter_id == recipient_id:
        return None, "self_gift"
    if rarity not in RARITY_ORDER:
        return None, "invalid_rarity"

    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN EXCLUSIVE")

        async with db.execute(
            "SELECT quantity FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (gifter_id, card_member_id, rarity),
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] < 1:
            return None, "no_card"

        await db.execute(
            "UPDATE user_cards SET quantity = quantity - 1 "
            "WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (gifter_id, card_member_id, rarity),
        )
        await db.execute(
            "DELETE FROM user_cards WHERE owner_id = ? AND quantity <= 0",
            (gifter_id,),
        )

        await db.execute("""
            INSERT INTO user_cards (owner_id, card_member_id, rarity, quantity, first_acquired_at, drawn_by_name)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(owner_id, card_member_id, rarity)
            DO UPDATE SET quantity = quantity + 1
        """, (recipient_id, card_member_id, rarity, now, drawn_by_name))

        await db.commit()

        async with db.execute(
            "SELECT id, owner_id, card_member_id, rarity, quantity, first_acquired_at, drawn_by_name "
            "FROM user_cards WHERE owner_id = ? AND card_member_id = ? AND rarity = ?",
            (recipient_id, card_member_id, rarity),
        ) as cur:
            r = await cur.fetchone()

    return UserCard(
        id=r[0], owner_id=r[1], card_member_id=r[2],
        rarity=r[3], quantity=r[4], first_acquired_at=r[5],
        drawn_by_name=r[6],
    ), None
```

Also add `gift_card` to the import in `src/bot.py` (done in Task 3).

- [ ] **Step 4: Run tests to verify they pass**

```
.venv/bin/python -m pytest tests/cards/test_service.py::test_gift_card_transfers_card tests/cards/test_service.py::test_gift_card_gifter_keeps_extra_copy tests/cards/test_service.py::test_gift_card_fails_when_not_owned tests/cards/test_service.py::test_gift_card_fails_self_gift -v
```

Expected: all PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

```
.venv/bin/python -m pytest tests/ -q
```

Expected: all pre-existing passing tests still pass (the two known-broken webapp tests may still fail — that's pre-existing).

- [ ] **Step 6: Commit**

```bash
git add src/superpal/cards/service.py tests/cards/test_service.py
git commit -m "feat: add gift_card service function for atomic card transfers"
```

---

### Task 3: Add `GiftConfirmView` and `/card-gift` command in `bot.py`

**Files:**
- Modify: `src/bot.py`

The command: validates ownership and self-gift upfront, shows an ephemeral confirmation embed with Confirm/Cancel buttons. On Confirm, calls `gift_card`, fetches member info, posts a public embed.

- [ ] **Step 1: Add `gift_card` and `get_card_quantity` to the import from `superpal.cards.service`**

Find the existing import block near the top of `src/bot.py` (currently lines 23-26):

```python
from superpal.cards.service import (
    draw_card, sync_members, generate_magic_link, trade_in, upgrade,
    create_trade_offer, execute_trade, decline_trade, TRADE_EXPIRY_MINUTES,
)
```

Replace with:

```python
from superpal.cards.service import (
    draw_card, sync_members, generate_magic_link, trade_in, upgrade,
    create_trade_offer, execute_trade, decline_trade, TRADE_EXPIRY_MINUTES,
    gift_card, get_card_quantity,
)
```

- [ ] **Step 2: Add `GiftConfirmView` class**

Add this class immediately after the `TradeView` class (around line 178, before the `# Slash commands #` comment):

```python
class GiftConfirmView(discord.ui.View):
    def __init__(self, gifter_id: str, recipient: discord.Member, card_member_id: str, rarity: str):
        super().__init__(timeout=60)
        self.gifter_id = gifter_id
        self.recipient = recipient
        self.card_member_id = card_member_id
        self.rarity = rarity

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if str(interaction.user.id) != self.gifter_id:
            await interaction.response.send_message(
                "Only the gifter can confirm this.", ephemeral=True
            )
            return

        card, err = await gift_card(
            gifter_id=self.gifter_id,
            recipient_id=str(self.recipient.id),
            card_member_id=self.card_member_id,
            rarity=self.rarity,
            drawn_by_name=interaction.user.display_name,
        )
        self.stop()

        if card is None:
            msg = {
                "no_card": "You no longer have that card.",
                "self_gift": "You can't gift a card to yourself.",
            }.get(err or "", "Gift failed.")
            await interaction.response.edit_message(content=msg, view=None)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT display_name, avatar_url, bio, stats FROM members WHERE discord_id = ?",
                (self.card_member_id,),
            ) as cur:
                row = await cur.fetchone()

        display_name = row[0] if row else "Unknown"
        avatar_url = row[1] if row else None
        embed = build_card_embed(
            display_name=display_name,
            avatar_url=avatar_url,
            rarity=self.rarity,
            card_number=card.id,
            drawn_by=self.recipient.display_name,
            bio=row[2] if row else None,
            stats_pairs=_parse_stats(row[3] if row else None),
            action_label=f"gifted by {interaction.user.display_name} to",
        )
        await interaction.response.edit_message(content="Gift sent!", view=None)
        if interaction.channel:
            await interaction.channel.send(
                content=f"{self.recipient.mention} just received a gift from {interaction.user.mention}!",
                embed=embed,
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if str(interaction.user.id) != self.gifter_id:
            await interaction.response.send_message(
                "Only the gifter can cancel this.", ephemeral=True
            )
            return
        self.stop()
        await interaction.response.edit_message(content="Gift cancelled.", view=None)
```

- [ ] **Step 3: Add the `/card-gift` slash command**

Add this after the `/card-trade` command block (after line ~524, before `/admin-link`):

```python
@bot.tree.command(name="card-gift", description="Give one of your cards to another player")
@discord.app_commands.describe(
    recipient="The server member to receive the gift",
    member="The member card you want to gift",
    rarity="The rarity of the card to gift",
)
@discord.app_commands.choices(rarity=[
    discord.app_commands.Choice(name="Common", value="common"),
    discord.app_commands.Choice(name="Uncommon", value="uncommon"),
    discord.app_commands.Choice(name="Rare", value="rare"),
    discord.app_commands.Choice(name="Legendary", value="legendary"),
])
async def gift_card_command(
    interaction: discord.Interaction,
    recipient: discord.Member,
    member: discord.Member,
    rarity: str,
) -> None:
    gifter_id = str(interaction.user.id)

    if interaction.user.id == recipient.id:
        await interaction.response.send_message(
            "You can't gift a card to yourself.", ephemeral=True
        )
        return

    qty = await get_card_quantity(gifter_id, str(member.id), rarity)
    if qty < 1:
        await interaction.response.send_message(
            f"You don't own a {rarity.upper()} {member.display_name} card.",
            ephemeral=True,
        )
        return

    view = GiftConfirmView(
        gifter_id=gifter_id,
        recipient=recipient,
        card_member_id=str(member.id),
        rarity=rarity,
    )
    await interaction.response.send_message(
        f"You're about to gift a **{RARITY_LABELS[rarity]} {member.display_name}** to {recipient.mention} — confirm?",
        view=view,
        ephemeral=True,
    )
```

- [ ] **Step 4: Run the full test suite**

```
.venv/bin/python -m pytest tests/ -q
```

Expected: same pass/fail as after Task 2 (no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/bot.py
git commit -m "feat: add /card-gift command with ephemeral confirmation step"
```
