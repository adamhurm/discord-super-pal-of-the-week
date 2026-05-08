# Card Gifting — Design Spec

**Date:** 2026-05-08

## Summary

Add a `/card-gift` command that lets a player give one of their cards to another player for free. The gift is announced publicly in the channel via an embed. A confirmation step protects against fat-finger mistakes before anything is posted publicly.

## Command

```
/card-gift @recipient @member rarity
```

Parameters match the existing card command conventions: a Discord member mention for `@recipient` and `@member`, and a rarity string for `rarity`.

## Flow

1. Bot validates that the gifter owns at least 1 copy of `[member, rarity]`. If not, reply with an ephemeral error and stop.
2. Bot validates that the gifter is not gifting to themselves. If so, reply with an ephemeral error and stop.
3. Bot sends an ephemeral confirmation message to the gifter: *"You're about to gift a {RARITY} {Member} to @Recipient — confirm?"* with a **Confirm** button and a **Cancel** button.
4. On **Confirm**: execute the transfer (see Service Layer), then post a public embed in the channel.
5. On **Cancel**: dismiss the ephemeral message, no action taken.
6. On timeout (60 s, no interaction): treat as cancel — Discord clears the interaction automatically.

## Service Layer

No new service function. The bot command reuses:

- `get_card_quantity(gifter_id, member_id, rarity)` — ownership check before showing confirmation.
- A new inline DB write (single `aiosqlite` connection, `BEGIN EXCLUSIVE`) that:
  1. Re-checks quantity (race-condition guard between confirmation and button press).
  2. Decrements gifter's quantity by 1; deletes the row if quantity reaches 0 (same pattern as `trade_in` and `execute_trade`).
  3. Inlines the recipient INSERT directly (same SQL as `award_card`) — `award_card` opens its own connection so it cannot participate in the same transaction.

Wrapping both writes in a single exclusive transaction ensures no partial state if something fails between the deduct and the award.

## Public Embed

Reuses `build_card_embed` — same visual format as a draw result. Footer changes from *"Drawn by X"* to *"Gifted by X to @Recipient"*. Posted to the channel where the command was run.

## Edge Cases

| Scenario | Handling |
|---|---|
| Gifter doesn't own the card | Ephemeral error before confirmation shown |
| Gifter gifts to themselves | Ephemeral error before confirmation shown |
| Card gone by the time Confirm is pressed | Re-check inside the exclusive transaction; ephemeral error if quantity is now 0 |
| Recipient always receives the card | `award_card` always succeeds for a valid rarity — no failure path |

## Out of Scope

- Gift limits (gifting is unlimited as long as the gifter owns the card)
- Recipient acceptance/decline (gifts are unilateral)
- DM notification to recipient (the public embed is the notification)
