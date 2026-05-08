import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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


@pytest.mark.asyncio
async def test_trade_in_without_session_shows_expired(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.post(
            "/collection/trade-in",
            data={"member_id": "111", "rarity": "common"},
        )
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_trade_in_success_shows_result(client):
    from superpal.cards.models import UserCard
    from datetime import datetime, timezone

    link = _link()
    received_card = UserCard(
        id=42, owner_id="111", card_member_id="222",
        rarity="common", quantity=1,
        first_acquired_at=datetime.now(timezone.utc).isoformat(),
    )

    mock_cursor = MagicMock()
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock(return_value=False)
    mock_cursor.fetchone = AsyncMock(return_value=("Florp Xennial", None))

    mock_conn = MagicMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = MagicMock(return_value=mock_cursor)

    with (
        patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.trade_in", new=AsyncMock(return_value=received_card)),
        patch("superpal.webapp.routes.aiosqlite.connect", return_value=mock_conn),
    ):
        response = await client.post(
            "/collection/trade-in",
            data={"member_id": "222", "rarity": "common"},
        )
    assert response.status_code == 200
    assert "trade" in response.text.lower()
    assert "Florp Xennial" in response.text


@pytest.mark.asyncio
async def test_trade_in_insufficient_redirects_to_collection(client):
    link = _link()
    with (
        patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.trade_in", new=AsyncMock(return_value=None)),
    ):
        response = await client.post(
            "/collection/trade-in",
            data={"member_id": "999", "rarity": "common"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/collection"


@pytest.mark.asyncio
async def test_admin_add_member_without_session_shows_expired(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.post(
            "/admin/member/add",
            data={"discord_id": "test_123", "display_name": "Test User"},
        )
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_add_member_collection_session_shows_expired(client):
    link = _link(link_type="collection")
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)):
        response = await client.post(
            "/admin/member/add",
            data={"discord_id": "test_123", "display_name": "Test User"},
        )
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_add_member_success_redirects(client):
    link = _link(link_type="admin")
    with (
        patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.add_member", new=AsyncMock()),
    ):
        response = await client.post(
            "/admin/member/add",
            data={"discord_id": "test_123", "display_name": "Test User"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


@pytest.mark.asyncio
async def test_admin_set_avatar_without_session_shows_expired(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.post(
            "/admin/member/111/avatar",
            files={"image": ("test.png", b"\x89PNG\r\n", "image/png")},
        )
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_set_avatar_success_redirects(client, tmp_path, monkeypatch):
    import superpal.webapp.routes as routes_mod
    monkeypatch.setattr(routes_mod, "IMAGES_DIR", tmp_path)
    link = _link(link_type="admin")
    with (
        patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.set_member_avatar", new=AsyncMock()),
    ):
        response = await client.post(
            "/admin/member/111/avatar",
            files={"image": ("test.png", b"\x89PNG\r\n", "image/png")},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


@pytest.mark.asyncio
async def test_admin_award_card_without_session_shows_expired(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.post(
            "/admin/award",
            data={"owner_id": "111", "card_member_id": "222", "rarity": "common", "quantity": "1"},
        )
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_award_card_collection_session_shows_expired(client):
    link = _link(link_type="collection")
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)):
        response = await client.post(
            "/admin/award",
            data={"owner_id": "111", "card_member_id": "222", "rarity": "common", "quantity": "1"},
        )
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_award_card_success_redirects(client):
    from superpal.cards.models import UserCard
    link = _link(link_type="admin")
    received = UserCard(
        id=1, owner_id="111", card_member_id="222",
        rarity="common", quantity=1,
        first_acquired_at=datetime.now(timezone.utc).isoformat(),
    )
    with (
        patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.award_card", new=AsyncMock(return_value=received)),
    ):
        response = await client.post(
            "/admin/award",
            data={"owner_id": "111", "card_member_id": "222", "rarity": "common", "quantity": "1"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


@pytest.mark.asyncio
async def test_collection_shows_completion_pct(client):
    link = _link()
    fake_collection = {
        "owned": [{"member_id": "111", "display_name": "Alice", "avatar_url": None,
                   "rarity": "common", "quantity": 1, "bio": None, "stats_pairs": []}],
        "undiscovered": [{"discord_id": "222", "display_name": "Bob", "avatar_url": None}],
        "counts": {"common": 1, "uncommon": 0, "rare": 0, "legendary": 0},
    }

    mock_cursor = MagicMock()
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock(return_value=False)
    mock_cursor.fetchone = AsyncMock(return_value=("Alice", None))

    mock_conn = MagicMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = MagicMock(return_value=mock_cursor)

    with (
        patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.get_collection", new=AsyncMock(return_value=fake_collection)),
        patch("superpal.webapp.routes.aiosqlite.connect", return_value=mock_conn),
    ):
        response = await client.get("/collection")
    assert response.status_code == 200
    assert "50%" in response.text
