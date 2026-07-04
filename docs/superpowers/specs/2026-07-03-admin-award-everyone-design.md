# Admin Dashboard: "Everyone" Recipient for Award Card / Add Draws

## Problem

The admin dashboard (`/admin`) has two forms that target a single member by
`discord_id`:

- **Award a Card** (`POST /admin/award`) — picks an `owner_id` (recipient),
  a `card_member_id` (which member's card design), a `rarity`, and a
  `quantity`.
- **Add Draws** (`POST /admin/add-draws`) — picks a `user_id` and a
  `quantity`.

There's no way to apply either action to all members at once; the admin has
to repeat the form once per member.

## Scope

Add an "Everyone" recipient option to both forms:

- **Award a Card**: applies only to the "Owner (receives the card)" dropdown.
  The "Card (which member's card)" dropdown is unaffected and stays
  single-select — awarding to "Everyone" still means one specific card
  design/rarity/quantity, given to every eligible member.
- **Add Draws**: applies to the single "User" dropdown.

"Everyone" means all members with `is_excluded = 0`. Members marked
`is_excluded` (excluded from the card pool) are skipped, consistent with how
exclusion is already used elsewhere to mean "not part of active play."

## Design

### UI (`admin.html`)

Add `<option value="everyone">Everyone</option>` as the first option in:
- the `owner_id` select in the Award a Card form
- the `user_id` select in the Add Draws form

No changes to the `card_member_id` select or the Audit form's `user_id`
select.

### Routes (`routes.py`)

In `admin_award_card`:
- If `owner_id == "everyone"`, fetch `get_all_members_for_admin()`, filter to
  `is_excluded == False`, and call `award_card(member_id, card_member_id,
  rarity, quantity)` once per remaining member.
- Otherwise, behave exactly as today (single recipient).

In `admin_add_draws`:
- If `user_id == "everyone"`, fetch `get_all_members_for_admin()`, filter to
  `is_excluded == False`, and call `add_draws(member_id, quantity)` once per
  remaining member.
- Otherwise, behave exactly as today (single recipient).

Both routes still end with `RedirectResponse(url="/admin", status_code=303)`.
No flash-message or summary UI is introduced — there isn't one today for the
single-recipient case, so the bulk case stays consistent (silent redirect).

### Service layer (`service.py`)

No changes. `award_card` and `add_draws` already operate on a single
`user_id`/`owner_id`; the route layer loops over members.

## Out of scope

- "Everyone" for the `card_member_id` dropdown (awarding every card design at
  once).
- Any confirmation UI, dry-run, or count-of-recipients feedback.
- Batching the per-member DB calls into a single transaction — member counts
  are small (Discord server roster), so sequential calls are fine.
