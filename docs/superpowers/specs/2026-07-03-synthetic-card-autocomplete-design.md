# Synthetic Card Autocomplete Design

**Date:** 2026-07-03
**Status:** Approved

## Context

The card game supports "synthetic" members — card subjects created by an admin via `/admin` (`add_member()` in `src/superpal/cards/service.py`) that don't correspond to a real Discord guild member. These are flagged with `members.is_synthetic = 1` and protected from being overwritten by `sync_members()`. They can be drawn, awarded, and traded exactly like real members' cards — the `members` table makes no distinction at draw/award time.

However, four bot slash commands take a `member: discord.Member` parameter to identify *which* card the command acts on: `/card-display`, `/card-trade-in`, `/card-upgrade`, and `/card-gift` (its `member` param specifically — `recipient` is a separate, legitimately-real Discord member). Discord's native member picker can only resolve real guild members, so there is currently no way to reference a synthetic card's subject through any of these four commands — synthetic cards can be drawn and viewed on the web collection page, but never displayed, traded in, upgraded, or gifted from Discord.

## Approach

Replace the `member: discord.Member` parameter on all four commands with a `card: str` parameter backed by Discord's autocomplete feature. The autocomplete list is populated from the invoking user's own owned card subjects (real and synthetic alike), not the guild's member list. The autocomplete value is the plain `discord_id` string, so no changes are needed to the underlying service functions (`draw_card`, `trade_in`, `upgrade`, `gift_card` in `service.py`) — they already accept `card_member_id: str`.

**Alternatives considered:**
- *Parallel `-custom` commands* (e.g. `/card-display-custom`) for synthetic cards — rejected: doubles the command surface, and requires the user to know upfront whether their card is synthetic.
- *Optional secondary "custom name" text param* alongside the existing `discord.Member` param — rejected: clunkier UX, still requires manually typing a name, and needs awkward mutual-exclusion logic between the two params.

Autocomplete over the user's owned cards is simplest: one unified list, no new commands, and it naturally scopes to cards the user can actually act on (rather than every member in the guild).

## Components

### `get_owned_card_subjects(owner_id: str) -> list[dict]` (new, `src/superpal/cards/service.py`)

Returns one row per distinct card subject the user owns at least one copy of (any rarity, `quantity > 0`), each with `discord_id`, `display_name`, and `is_synthetic`. Query joins `user_cards` to `members` on `card_member_id = discord_id`, grouped/deduplicated by member, ordered by `display_name`. Mirrors the existing join pattern already used in `get_collection()` (`service.py:456-504`).

### `get_member_display_name(discord_id: str) -> str | None` (new, `src/superpal/cards/service.py`)

Simple lookup used by command error paths that need a display name but don't have a `discord.Member` object anymore (see below). Returns `None` if no such member row exists, mirroring the existing `"Unknown"` fallback convention already used at `bot.py:598, 724, 779`.

### Label formatting (new, plain function in `src/bot.py`, no Discord types)

Given a list of `(discord_id, display_name, is_synthetic)`:
1. Base label is `display_name`.
2. If `is_synthetic`, append `" (Custom)"` — matches the existing `CUSTOM` badge wording used in `admin.html:271`.
3. If, after step 2, a label still collides with another entry in the same list (e.g. two real members sharing a display name), append the last 4 characters of `discord_id` in parentheses to force uniqueness.

Kept as a plain function (input: list of tuples, output: list of `(label, discord_id)`) so it's testable without mocking `discord.Interaction`.

### Shared autocomplete callback (new, `src/bot.py`)

```python
async def _card_subject_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[discord.app_commands.Choice[str]]:
    subjects = await get_owned_card_subjects(str(interaction.user.id))
    labeled = _label_card_subjects(subjects)
    matches = [
        (label, discord_id) for label, discord_id in labeled
        if current.lower() in label.lower()
    ]
    return [
        discord.app_commands.Choice(name=label, value=discord_id)
        for label, discord_id in matches[:25]
    ]
```

Registered on all four commands via `@<command>.autocomplete("card")`.

### Per-command changes (`src/bot.py`)

For `display_card_command`, `trade_in_command`, `upgrade_command`, and `gift_card_command`:
- Rename the `member` parameter to `card`, type `str`, with `describe()` text updated (e.g. `"The card you want to display"`).
- Wire `@<command>.autocomplete("card")` to the shared callback.
- Replace `member.id` with `card` directly (it's already the `discord_id` string).
- Replace `member.display_name` references in messages with a call to `get_member_display_name(card)`, falling back to `"Unknown"` if `None` (matching the existing fallback convention).

`gift_card_command`'s `recipient: discord.Member` parameter is unchanged — it identifies a real Discord user to receive the gift, not a card subject.

## Data flow example

1. User types `/card-display` in Discord, focuses the `card` field, types "ste".
2. Discord calls the autocomplete callback with `current="ste"`.
3. Callback fetches the user's owned subjects, formats labels (e.g. `"Steve"`, `"Steve (Custom)"` if both a real and synthetic "Steve" exist and are owned), filters to those containing "ste", returns up to 25 `Choice(name=label, value=discord_id)`.
4. User picks `"Steve (Custom)"`; Discord submits `card="<synthetic-uuid>"`.
5. Command body queries `user_cards`/`members` using that ID exactly as it does today for real members — no other code path changes.

## Error handling

- No owned cards yet → autocomplete returns an empty list (Discord shows no suggestions, same UX as any param with no valid choices today).
- Card ID no longer valid by submit time (e.g. traded away between typing and submitting) → existing `qty < 1` / `card is None` branches already handle this; display-name lookups fall back to `"Unknown"`.

## Testing

- Unit tests for `get_owned_card_subjects` in `tests/cards/test_service.py`: mixed real+synthetic ownership, zero-quantity rows excluded, ordering by display name.
- Unit tests for `get_member_display_name`: existing member, nonexistent ID.
- Unit tests for the label-formatting function: synthetic tagging, same-name collision suffix, no collision (no suffix added).
- No existing tests invoke bot slash-command callbacks directly (`tests/test_bot.py` covers role rotation, not card commands) — service-level and pure-function coverage matches the codebase's existing test pattern, so no new callback-level tests are added.
- Manual verification: run the bot locally, create a synthetic member via `/admin`, draw or award it a card, confirm it appears in `/card-display`'s autocomplete, and that `/card-trade-in`, `/card-upgrade`, and `/card-gift` all resolve it correctly too.
