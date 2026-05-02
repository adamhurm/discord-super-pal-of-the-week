import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from superpal.webapp.app import create_app
from superpal.cards.models import MagicLink
from datetime import datetime, timezone, timedelta


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _link(link_type="collection") -> MagicLink:
    now = datetime.now(timezone.utc)
    return MagicLink(
        token="abc123",
        user_id="111",
        link_type=link_type,
        created_at=now.isoformat(),
        consumed_at=now.isoformat(),
        session_token="sess_abc",
        session_expires_at=(now + timedelta(hours=24)).isoformat(),
    )


@pytest.mark.asyncio
async def test_link_redirect_on_valid_token(client):
    link = _link()
    with patch("superpal.webapp.routes.consume_magic_link", new=AsyncMock(return_value=link)):
        response = await client.get("/link/abc123", follow_redirects=False)
    assert response.status_code in (302, 303)
    assert "bringus_session" in response.cookies


@pytest.mark.asyncio
async def test_link_expired_returns_expired_page(client):
    with patch("superpal.webapp.routes.consume_magic_link", new=AsyncMock(return_value=None)):
        response = await client.get("/link/deadbeef", follow_redirects=False)
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_collection_shows_expired_without_session(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.get("/collection")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_shows_expired_without_session(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.get("/admin")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_collection_session_cant_access_admin(client):
    """A collection session should not be able to access the admin page."""
    link = _link(link_type="collection")
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)):
        response = await client.get("/admin")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_collection_refresh_without_session_shows_expired(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.post("/collection/refresh")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_exclude_without_session_shows_expired(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.post("/admin/exclude/123")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_sync_without_session_shows_expired(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.post("/admin/sync")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_exclude_collection_session_shows_expired(client):
    link = _link(link_type="collection")
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)):
        response = await client.post("/admin/exclude/123")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_sync_collection_session_shows_expired(client):
    link = _link(link_type="collection")
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)):
        response = await client.post("/admin/sync")
    assert response.status_code == 200
    assert "expired" in response.text.lower()
