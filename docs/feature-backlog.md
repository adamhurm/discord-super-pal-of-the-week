# Card Game Feature Backlog

## Planned

### Card Gifting
Send one of your cards to another player with no swap required. Good for birthdays, congratulations, or helping new players.
- Command: `/card-gift @user @member rarity`
- Guard: can only gift cards you own (quantity >= 1)
- The service layer already has `award_card`; main work is a bot command

### Leaderboard
Show who's winning — top collectors by total cards, by legendary count, and by unique members collected. Pure read on existing data, no new mechanics.
- Command: `/card-leaderboard`

### Set Completion Milestone
Give players a tangible goal. Celebrate finishing a rarity tier or collecting all versions of one member.
- `/card-progress` command showing completion % per rarity and per member
- Optional: banner on the web collection UI when a set is completed
