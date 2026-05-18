# Trading Cards — Interface Matrix

Tracks which card operations are available from each surface.

**Legend:** ✅ available &nbsp;|&nbsp; ❌ not available &nbsp;|&nbsp; 🔒 admin only &nbsp;|&nbsp; 🔧 internal (no direct user entry point)

---

## User-Facing Operations

| Feature | Description | Discord | Web UI |
|---|---|:---:|:---:|
| Draw card | Draw a random card (5/week; 10/week for Super Pal role) | ✅ `/card-draw` | ❌ |
| Display card | Post a card you own to the channel | ✅ `/card-display` | ❌ |
| View collection | Browse your full card collection with silhouettes for undiscovered members | ✅ `/card-collection` (magic link) | ✅ `/collection` |
| Trade-in | Trade 3 duplicates for a random card of the same rarity | ✅ `/card-trade-in` | ✅ `/collection/trade-in` |
| Upgrade | Trade 5 copies of one member for 1 card of the same member at the next rarity | ✅ `/card-upgrade` | ❌ |
| Peer trade | Offer to swap a card with another player (10-minute expiry) | ✅ `/card-trade` | ❌ |
| Accept/decline trade | Respond to an incoming trade offer via interactive buttons | 🔧 button interaction | ❌ |
| Gift card | Transfer one card to another player (requires confirmation) | ✅ `/card-gift` | ❌ |
| Collection Leaderboard | Top 10 collectors ranked by total cards, legendary count, or unique members | ✅ `/card-collection-leaderboard` | ❌ |
| Fight Leaderboard | Top 10 fighters ranked by wins, win rate, fights played, Pringle balance, or escapes | ✅ `/card-fight-leaderboard` | ❌ |
| Progress | Check your collection completion stats | ✅ `/card-progress` | ❌ |

---

## Admin Operations

| Feature | Description | Discord | Web UI |
|---|---|:---:|:---:|
| Admin dashboard | View all members, pool stats, and admin actions | ✅ `/admin-link` (magic link) | ✅ `/admin` |
| Award cards | Manually grant any card at any rarity to a player | ❌ | 🔒 `/admin/award` |
| Reset draw log | Clear all draw records for the current week, restoring everyone's draws | ❌ | 🔒 `/admin/reset-draws` |
| Sync members | Pull current Discord guild members into the database | 🔧 on bot `on_ready` | 🔒 `/admin/sync` |
| Add synthetic member | Add a non-Discord member to the card pool | ❌ | 🔒 `/admin/member/add` |
| Upload avatar | Set or replace a member's card avatar image | ❌ | 🔒 `/admin/member/{id}/avatar` |
| Set bio & stats | Edit a member's lore text and stats JSON blob | ❌ | 🔒 `/admin/member/{id}/bio-stats` |
| Toggle exclusion | Exclude or re-include a member from the draw pool | ❌ | 🔒 `/admin/exclude/{id}` |
| Set forced rarity | Lock a member to a specific rarity tier (or clear the lock) | ❌ | 🔒 `/admin/member/{id}/forced-rarity` |
| Pool stats | View eligible/excluded member counts and total cards in circulation | ❌ | 🔒 `/admin` (embedded in dashboard) |

---

## Auth / Linking

| Feature | Description | Discord | Web UI |
|---|---|:---:|:---:|
| Generate magic link | Create a 24-hour access token and DM the URL to the user | 🔧 called by `/card-collection` and `/admin-link` | ❌ |
| Activate magic link | Validate a token and issue a session cookie | ❌ | 🔧 `/link/{token}` |
| Session validation | Authenticate subsequent web requests via `bringus_session` cookie | ❌ | 🔧 `get_session_from_request` |
