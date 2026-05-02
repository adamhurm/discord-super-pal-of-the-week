import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from superpal.webapp.auth import get_session_from_request, SESSION_COOKIE_NAME
from superpal.cards.models import MagicLink
from datetime import datetime, timezone, timedelta


def _make_link(link_type="collection") -> MagicLink:
    now = datetime.now(timezone.utc)
    return MagicLink(
        token="tok",
        user_id="111",
        link_type=link_type,
        created_at=now.isoformat(),
        consumed_at=now.isoformat(),
        session_token="sess123",
        session_expires_at=(now + timedelta(hours=24)).isoformat(),
    )


@pytest.mark.asyncio
async def test_get_session_returns_none_when_no_cookie():
    request = MagicMock()
    request.cookies = {}
    with patch("superpal.webapp.auth.get_session", new=AsyncMock(return_value=None)):
        result = await get_session_from_request(request)
    assert result is None


@pytest.mark.asyncio
async def test_get_session_returns_link_when_valid():
    link = _make_link()
    request = MagicMock()
    request.cookies = {SESSION_COOKIE_NAME: "sess123"}
    with patch("superpal.webapp.auth.get_session", new=AsyncMock(return_value=link)):
        result = await get_session_from_request(request)
    assert result is not None
    assert result.user_id == "111"
