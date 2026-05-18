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
    drawn_by_name: Optional[str] = None


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
class Fight:
    id: int
    mode: str
    challenger_id: str
    opponent_id: str
    status: str
    winner_id: Optional[str]
    current_turn_player_id: Optional[str]
    pending_swap_player_id: Optional[str]
    channel_id: Optional[str]
    challenger_ready: bool
    opponent_ready: bool
    challenger_atk_boost: int
    opponent_atk_boost: int
    challenger_smoked: bool
    opponent_smoked: bool
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    expires_at: Optional[str]
    last_activity_at: Optional[str]


@dataclass
class FightCard:
    id: int
    fight_id: int
    player_id: str
    card_member_id: str
    rarity: str
    slot: int
    hp_current: int
    hp_max: int
    is_active: bool
    is_fainted: bool


@dataclass
class FightLogEntry:
    id: int
    fight_id: int
    actor_id: Optional[str]
    action_type: str
    action_detail: Optional[str]
    d20_roll: Optional[int]
    damage_dealt: Optional[int]
    narrative_text: str
    created_at: str


@dataclass
class PlayerItem:
    player_id: str
    item_type: str
    quantity: int


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
