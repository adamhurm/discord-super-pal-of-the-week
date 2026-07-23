from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from superpal.sessions import Session
from superpal.webapp.auth import SESSION_COOKIE_NAME, get_session_from_request


def _make_session(scope="collection") -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        token="sess123",
        user_id="111",
        scope=scope,
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=24)).isoformat(),
    )


@pytest.mark.asyncio
async def test_get_session_returns_none_when_no_cookie():
    request = MagicMock()
    request.cookies = {}
    with patch("superpal.webapp.auth.get_session", new=AsyncMock(return_value=None)):
        result = await get_session_from_request(request)
    assert result is None


@pytest.mark.asyncio
async def test_get_session_returns_session_when_valid():
    session = _make_session()
    request = MagicMock()
    request.cookies = {SESSION_COOKIE_NAME: "sess123"}
    with patch("superpal.webapp.auth.get_session", new=AsyncMock(return_value=session)):
        result = await get_session_from_request(request)
    assert result is not None
    assert result.user_id == "111"
    assert result.is_admin is False
