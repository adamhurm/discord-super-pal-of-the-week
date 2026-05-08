# Bringus Card Game — Player Guide

Collect cards of your fellow server members, trade duplicates, and upgrade your rarest finds. Every card features the member's avatar and a rarity tier — Common through Legendary.

---

## Drawing Cards

Use `/card-draw` in the server to receive one random card each week.

- The card is posted publicly in the channel as a Discord embed.
- **Super Pal of the Week** holders get **2 draws per week** instead of one.
- Your weekly allowance resets every Monday. If you've used all your draws, the bot will tell you — try again next week.

### Rarity odds

| Rarity | Chance | Embed color |
|---|---|---|
| Common | 60% | Grey |
| Uncommon | 25% | Green |
| Rare | 12% | Blue |
| Legendary | 3% | Gold |

Duplicate cards stack — you can own multiple copies of the same member at the same rarity.

---

## Viewing Your Collection

Run `/card-collection` and the bot will **DM you a private link**. The link is never posted to the channel.

Clicking the link opens your collection page at [cards.bring-us.com](https://cards.bring-us.com), where you can see:

- Every card you own, with their avatar, name, rarity, and quantity
- Silhouettes (`???`) for server members you haven't drawn yet — so you know the full roster exists
- A rarity summary (Common ×N, Uncommon ×N, …) at the top

### Link rules

- The link works **once**. The first click opens a 24-hour session and the link is spent.
- Any attempt to reuse the same link shows an "expired" page.
- After 24 hours your session expires. Just run `/card-collection` again for a fresh link.

---

## Trading In Duplicates

`/card-trade-in @member rarity`

Spend **3 copies** of the same [member + rarity] card to receive **1 random card of the same rarity** from the eligible member pool.

**Example:** `/card-trade-in @Bingus common` — if you own 3× Common Bingus, you'll trade them in for a random Common card. You might get the same card back; it's random.

The bot replies privately (ephemeral) with your new card embed.

---

## Upgrading Cards

`/card-upgrade @member rarity`

Spend **5 copies** of the same [member + rarity] card to receive **1 copy of the same member at the next rarity tier**.

| From | To |
|---|---|
| Common | Uncommon |
| Uncommon | Rare |
| Rare | Legendary |

**Legendary cards cannot be upgraded** — they're already at the top tier. The bot will tell you if you try.

**Example:** `/card-upgrade @Dingus uncommon` — if you own 5× Uncommon Dingus, you'll receive 1× Rare Dingus.

The bot replies privately (ephemeral) with your upgraded card embed.

---

## FAQ

**Can I hold unlimited cards?**
Yes. There's no cap on how many cards you can own or how many duplicates can stack.

**Do excluded members ever appear?**
No. Members excluded by an admin are removed from the draw pool entirely and won't appear in `/card-draw`, `/card-trade-in`, or `/card-upgrade` results.

**What if I can't receive DMs?**
The bot will let you know that it couldn't send the link. Open your Discord privacy settings and allow DMs from server members, then try again.

**My link is expired but my session should still be active — what happened?**
Sessions last 24 hours from the moment you first click the link, not from when the command was run. If 24 hours have passed, run `/card-collection` again.

---

## Admin Reference

Admins with the **The Clippy** role can run `/admin-link` to receive a private link to the admin dashboard.

The dashboard lets you:

- **Exclude a member** from the card pool (they stop appearing in draws)
- **Re-include** a previously excluded member
- **Sync the member list** from Discord to pick up new or renamed members
- View pool stats: eligible count, excluded count, total cards in circulation

The admin link follows the same one-time / 24-hour session rules as collection links.
