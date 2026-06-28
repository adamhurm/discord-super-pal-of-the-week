#!/usr/bin/env python3
"""One-time script to seed boin balances from the captured leaderboard snapshot.

Run from the repo root:
    uv run scripts/import_boins.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from superpal.economy.boin_service import import_initial_balances

LEADERBOARD: dict[str, int] = {
    "nosip": 71732,
    "paperclippy": 64097,
    "perks.of.being.me": 38036,
    "bigjessejay": 14789,
    "muffinbaker": 10571,
    "blargh6026": 5352,
    "loosir": 5096,
    "filbertthewiz": 0,
}


async def main() -> None:
    print(f"Importing boin balances for {len(LEADERBOARD)} members...")
    await import_initial_balances(LEADERBOARD)
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
