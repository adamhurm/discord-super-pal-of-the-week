from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Market:
    id: int
    title: str
    description: str | None
    created_by: str
    status: str
    outcome: str | None
    yes_pool: int
    no_pool: int
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None


@dataclass
class Bet:
    id: int
    market_id: int
    player_id: str
    side: str
    amount: int
    placed_at: datetime
