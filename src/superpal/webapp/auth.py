from typing import Optional
from fastapi import Request, Response
from superpal.cards.models import MagicLink
from superpal.cards.service import get_session

SESSION_COOKIE_NAME = "bringus_session"
SESSION_MAX_AGE = 60 * 60 * 24  # 24 hours in seconds


async def get_session_from_request(request: Request) -> Optional[MagicLink]:
    """Extract and validate the session cookie from a request."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return await get_session(token)


def set_session_cookie(response: Response, session_token: str) -> None:
    """Write the session cookie onto a response."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=True,
    )
