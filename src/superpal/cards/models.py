from dataclasses import dataclass
from datetime import datetime
from typing import Optional


RARITY_ORDER: list[str] = ["common", "uncommon", "rare", "legendary"]

RARITY_WEIGHTS: dict[str, int] = {
    "common": 60,
    "uncommon": 25,
    "rare": 12,
    "legendary": 3,
}

RARITY_COLORS: dict[str, int] = {
    "common": 0x95A5A6,
    "uncommon": 0x27AE60,
    "rare": 0x2980B9,
    "legendary": 0xF39C12,
}

RARITY_LABELS: dict[str, str] = {
    "common": "COMMON",
    "uncommon": "UNCOMMON",
    "rare": "RARE",
    "legendary": "LEGENDARY",
}


@dataclass
class Member:
    discord_id: str
    display_name: str
    avatar_url: Optional[str]
    is_excluded: bool
    synced_at: datetime


@dataclass
class UserCard:
    id: int
    owner_id: str
    card_member_id: str
    rarity: str
    quantity: int
    first_acquired_at: datetime


@dataclass
class DrawLog:
    user_id: str
    week_start: str
    draws_used: int


@dataclass
class MagicLink:
    token: str
    user_id: str
    link_type: str
    created_at: datetime
    consumed_at: Optional[datetime]
    session_token: Optional[str]
    session_expires_at: Optional[datetime]


@dataclass
class PendingTrade:
    id: int
    proposer_id: str
    recipient_id: str
    offer_member_id: str
    offer_rarity: str
    request_member_id: str
    request_rarity: str
    status: str
    created_at: str
    expires_at: str
