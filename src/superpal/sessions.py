"""Unified web sessions for the card webapp.

One cookie-backed session model for every surface: magic links redeem
into scope 'collection' or 'admin', fight tokens into 'fight:<id>'.
Sessions roll — each successful lookup extends the expiry.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

from superpal.cards.db import DB_PATH

SESSION_TTL_HOURS = 24


@dataclass
class Session:
    token: str
    user_id: str
    scope: str  # 'collection' | 'admin' | 'fight:<id>'
    created_at: str
    expires_at: str

    @property
    def is_admin(self) -> bool:
        return self.scope == "admin"

    @property
    def fight_id(self) -> int | None:
        if self.scope.startswith("fight:"):
            return int(self.scope.removeprefix("fight:"))
        return None


async def create_session(user_id: str, scope: str) -> Session:
    """Create a new session with a fresh token."""
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at = (now + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (token, user_id, scope, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (token, user_id, scope, created_at, expires_at),
        )
        await db.commit()
    return Session(
        token=token, user_id=user_id, scope=scope, created_at=created_at, expires_at=expires_at
    )


async def get_session(token: str) -> Session | None:
    """Look up an active session and extend its expiry (rolling TTL)."""
    now = datetime.now(timezone.utc)
    new_expiry = (now + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT token, user_id, scope, created_at, expires_at "
            "FROM sessions WHERE token = ? AND expires_at > ?",
            (token, now.isoformat()),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        await db.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?", (new_expiry, token)
        )
        await db.commit()
    return Session(
        token=row[0], user_id=row[1], scope=row[2], created_at=row[3], expires_at=new_expiry
    )


async def delete_expired_sessions() -> int:
    """Delete sessions past their expiry. Returns the number removed."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        await db.commit()
        return cur.rowcount
