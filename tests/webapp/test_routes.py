from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from httpx import ASGITransport, AsyncClient

from superpal.cards.models import MagicLink, MemberCardContext
from superpal.sessions import Session
from superpal.webapp.app import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _member(display_name="TestUser") -> MemberCardContext:
    return MemberCardContext(
        discord_id="111",
        display_name=display_name,
        avatar_url=None,
        bio=None,
        stats_pairs=[],
    )


def _session(scope="collection") -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        token="sess_abc",
        user_id="111",
        scope=scope,
        created_at=now.isoformat(),
        expires_at=(now + timedelta(hours=24)).isoformat(),
    )


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
    fake_collection = {
        "owned": [],
        "undiscovered": [],
        "counts": {"common": 0, "uncommon": 0, "rare": 0, "legendary": 0},
    }
    mock_cursor = MagicMock()
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock(return_value=False)
    mock_cursor.fetchone = AsyncMock(return_value=(0,))
    mock_conn = MagicMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = MagicMock(return_value=mock_cursor)
    with (
        patch("superpal.webapp.routes.use_magic_link", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.get_collection", new=AsyncMock(return_value=fake_collection)),
        patch(
            "superpal.webapp.routes.get_member_card_context",
            new=AsyncMock(return_value=_member()),
        ),
        patch("superpal.webapp.routes.get_player_listings", new=AsyncMock(return_value=[])),
        patch("superpal.webapp.routes.aiosqlite.connect", return_value=mock_conn),
    ):
        response = await client.get("/link/abc123", follow_redirects=False)
    assert response.status_code == 200
    assert "bringus_session" in response.cookies


@pytest.mark.asyncio
async def test_link_expired_returns_expired_page(client):
    mock_cursor = MagicMock()
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock(return_value=False)
    mock_cursor.fetchone = AsyncMock(return_value=None)
    mock_conn = MagicMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = MagicMock(return_value=mock_cursor)
    with (
        patch("superpal.webapp.routes.use_magic_link", new=AsyncMock(return_value=None)),
        patch("superpal.webapp.routes.aiosqlite.connect", return_value=mock_conn),
    ):
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
    link = _session()
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
    link = _session()
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)):
        response = await client.post("/admin/exclude/123")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_sync_collection_session_shows_expired(client):
    link = _session()
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
    from datetime import datetime, timezone

    from superpal.cards.models import UserCard

    link = _session()
    received_card = UserCard(
        id=42,
        owner_id="111",
        card_member_id="222",
        rarity="common",
        quantity=1,
        first_acquired_at=datetime.now(timezone.utc).isoformat(),
    )

    with (
        patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.trade_in", new=AsyncMock(return_value=received_card)),
        patch(
            "superpal.webapp.routes.get_member_card_context",
            new=AsyncMock(return_value=_member("Florp Xennial")),
        ),
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
    link = _session()
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
    link = _session()
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)):
        response = await client.post(
            "/admin/member/add",
            data={"discord_id": "test_123", "display_name": "Test User"},
        )
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_admin_add_member_success_redirects(client):
    link = _session("admin")
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
    link = _session("admin")
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
    link = _session()
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

    link = _session("admin")
    received = UserCard(
        id=1,
        owner_id="111",
        card_member_id="222",
        rarity="common",
        quantity=1,
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
async def test_admin_award_card_everyone_skips_excluded_members(client):
    link = _session("admin")
    members = [
        {"discord_id": "111", "is_excluded": False},
        {"discord_id": "222", "is_excluded": True},
        {"discord_id": "333", "is_excluded": False},
    ]
    award_card_mock = AsyncMock(return_value=None)
    get_members_mock = AsyncMock(return_value=members)
    with (
        patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.get_all_members_for_admin", new=get_members_mock),
        patch("superpal.webapp.routes.award_card", new=award_card_mock),
    ):
        award_data = {
            "owner_id": "everyone",
            "card_member_id": "999",
            "rarity": "common",
            "quantity": "2",
        }
        response = await client.post("/admin/award", data=award_data, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert award_card_mock.await_args_list == [
        call("111", "999", "common", 2),
        call("333", "999", "common", 2),
    ]


@pytest.mark.asyncio
async def test_admin_add_draws_everyone_skips_excluded_members(client):
    link = _session("admin")
    members = [
        {"discord_id": "111", "is_excluded": False},
        {"discord_id": "222", "is_excluded": True},
        {"discord_id": "333", "is_excluded": False},
    ]
    add_draws_mock = AsyncMock(return_value=None)
    get_members_mock = AsyncMock(return_value=members)
    with (
        patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.get_all_members_for_admin", new=get_members_mock),
        patch("superpal.webapp.routes.add_draws", new=add_draws_mock),
    ):
        response = await client.post(
            "/admin/add-draws",
            data={"user_id": "everyone", "quantity": "3"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert add_draws_mock.await_args_list == [
        call("111", 3),
        call("333", 3),
    ]


@pytest.mark.asyncio
async def test_collection_shows_completion_pct(client):
    link = _session()
    fake_collection = {
        "owned": [
            {
                "member_id": "111",
                "display_name": "Alice",
                "avatar_url": None,
                "rarity": "common",
                "quantity": 1,
                "bio": None,
                "stats_pairs": [],
            }
        ],
        "undiscovered": [{"discord_id": "222", "display_name": "Bob", "avatar_url": None}],
        "counts": {"common": 1, "uncommon": 0, "rare": 0, "legendary": 0},
    }

    mock_cursor = MagicMock()
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock(return_value=False)
    mock_cursor.fetchone = AsyncMock(return_value=(0,))

    mock_conn = MagicMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.execute = MagicMock(return_value=mock_cursor)

    with (
        patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=link)),
        patch("superpal.webapp.routes.get_collection", new=AsyncMock(return_value=fake_collection)),
        patch(
            "superpal.webapp.routes.get_member_card_context",
            new=AsyncMock(return_value=_member("Alice")),
        ),
        patch("superpal.webapp.routes.get_player_listings", new=AsyncMock(return_value=[])),
        patch("superpal.webapp.routes.aiosqlite.connect", return_value=mock_conn),
    ):
        response = await client.get("/collection")
    assert response.status_code == 200
    assert "50%" in response.text


# ─── Fight route auth tests ─────────────────────────────────────────────────


def _fight(status="lobby", challenger_id="111", opponent_id="222", mode="quick"):
    fight = MagicMock()
    fight.status = status
    fight.challenger_id = challenger_id
    fight.opponent_id = opponent_id
    fight.mode = mode
    fight.challenger_ready = False
    fight.opponent_ready = False
    return fight


@pytest.mark.asyncio
async def test_fight_lobby_unauthenticated_shows_expired(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.get("/fight/1/lobby")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_fight_token_redemption_sets_cookie_and_redirects(client):
    with (
        patch(
            "superpal.webapp.routes.use_fight_token",
            new=AsyncMock(return_value=(1, "111", "fight_sess_tok")),
        ),
        patch(
            "superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)
        ),
    ):
        response = await client.get("/fight/1/lobby?ft=onetime", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/fight/1/lobby"
    assert response.cookies.get("bringus_session") == "fight_sess_tok"


@pytest.mark.asyncio
async def test_fight_token_redemption_keeps_existing_cookie_for_same_user(client):
    with (
        patch(
            "superpal.webapp.routes.use_fight_token",
            new=AsyncMock(return_value=(1, "111", "fight_sess_tok")),
        ),
        patch(
            "superpal.webapp.routes.get_session_from_request",
            new=AsyncMock(return_value=_session()),  # user 111 already logged in
        ),
    ):
        response = await client.get("/fight/1/lobby?ft=onetime", follow_redirects=False)
    assert response.status_code == 303
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_fight_token_invalid_shows_expired(client):
    with patch("superpal.webapp.routes.use_fight_token", new=AsyncMock(return_value=None)):
        response = await client.get("/fight/1/lobby?ft=deadbeef")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_fight_lobby_with_fight_scoped_cookie(client):
    with (
        patch(
            "superpal.webapp.routes.get_session_from_request",
            new=AsyncMock(return_value=_session("fight:1")),
        ),
        patch("superpal.webapp.routes.get_fight", new=AsyncMock(return_value=_fight())),
        patch(
            "superpal.webapp.routes.get_member_display_name",
            new=AsyncMock(return_value="Opponent Bob"),
        ),
        patch(
            "superpal.webapp.routes.get_collection",
            new=AsyncMock(return_value={"owned": []}),
        ),
    ):
        response = await client.get("/fight/1/lobby")
    assert response.status_code == 200
    assert "Opponent Bob" in response.text


@pytest.mark.asyncio
async def test_fight_lobby_wrong_fight_scope_shows_expired(client):
    with (
        patch(
            "superpal.webapp.routes.get_session_from_request",
            new=AsyncMock(return_value=_session("fight:2")),
        ),
        patch("superpal.webapp.routes.get_fight", new=AsyncMock(return_value=_fight())),
    ):
        response = await client.get("/fight/1/lobby")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_fight_lobby_participant_collection_session_fallback(client):
    with (
        patch(
            "superpal.webapp.routes.get_session_from_request",
            new=AsyncMock(return_value=_session()),  # collection scope, user 111
        ),
        patch("superpal.webapp.routes.get_fight", new=AsyncMock(return_value=_fight())),
        patch(
            "superpal.webapp.routes.get_member_display_name",
            new=AsyncMock(return_value="Opponent Bob"),
        ),
        patch(
            "superpal.webapp.routes.get_collection",
            new=AsyncMock(return_value={"owned": []}),
        ),
    ):
        response = await client.get("/fight/1/lobby")
    assert response.status_code == 200
    assert "Opponent Bob" in response.text


@pytest.mark.asyncio
async def test_fight_lobby_non_participant_shows_expired(client):
    fight = _fight(challenger_id="888", opponent_id="999")
    with (
        patch(
            "superpal.webapp.routes.get_session_from_request",
            new=AsyncMock(return_value=_session()),  # user 111 is not in this fight
        ),
        patch("superpal.webapp.routes.get_fight", new=AsyncMock(return_value=fight)),
    ):
        response = await client.get("/fight/1/lobby")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_fight_state_api_unauthorized(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.get("/api/fight/1/state")
    assert response.status_code == 401


# ─── Shop & fights page tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shop_unauthenticated_shows_expired(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.get("/shop")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_shop_renders_items_and_balance(client):
    with (
        patch(
            "superpal.webapp.routes.get_session_from_request",
            new=AsyncMock(return_value=_session()),
        ),
        patch("superpal.webapp.routes.get_balance", new=AsyncMock(return_value=125)),
        patch(
            "superpal.webapp.routes.get_player_items",
            new=AsyncMock(return_value={"heal_potion": 2}),
        ),
        patch(
            "superpal.webapp.routes.get_member_card_context",
            new=AsyncMock(return_value=_member()),
        ),
    ):
        response = await client.get("/shop")
    assert response.status_code == 200
    assert "125" in response.text
    assert "Heal Potion" in response.text
    assert "Smoke Screen" in response.text


@pytest.mark.asyncio
async def test_shop_buy_success_redirects(client):
    with (
        patch(
            "superpal.webapp.routes.get_session_from_request",
            new=AsyncMock(return_value=_session()),
        ),
        patch("superpal.webapp.routes.buy_item", new=AsyncMock(return_value=(True, ""))),
    ):
        response = await client.post(
            "/shop/buy", data={"item_type": "heal_potion"}, follow_redirects=False
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop?bought=heal_potion"


@pytest.mark.asyncio
async def test_shop_buy_insufficient_redirects_with_error(client):
    with (
        patch(
            "superpal.webapp.routes.get_session_from_request",
            new=AsyncMock(return_value=_session()),
        ),
        patch(
            "superpal.webapp.routes.buy_item",
            new=AsyncMock(return_value=(False, "insufficient_pringles")),
        ),
    ):
        response = await client.post(
            "/shop/buy", data={"item_type": "heal_potion"}, follow_redirects=False
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/shop?error=insufficient_pringles"


@pytest.mark.asyncio
async def test_fights_page_unauthenticated_shows_expired(client):
    with patch("superpal.webapp.routes.get_session_from_request", new=AsyncMock(return_value=None)):
        response = await client.get("/fights")
    assert response.status_code == 200
    assert "expired" in response.text.lower()


@pytest.mark.asyncio
async def test_fights_page_renders_rows(client):
    fights = [
        {
            "id": 7,
            "mode": "quick",
            "status": "active",
            "winner_id": None,
            "is_your_turn": True,
            "created_at": "2026-07-01",
            "opponent_id": "222",
            "opponent_display_name": "Bob",
            "you_won": None,
        },
        {
            "id": 6,
            "mode": "extended",
            "status": "completed",
            "winner_id": "111",
            "is_your_turn": False,
            "created_at": "2026-06-01",
            "opponent_id": "333",
            "opponent_display_name": "Carol",
            "you_won": True,
        },
    ]
    with (
        patch(
            "superpal.webapp.routes.get_session_from_request",
            new=AsyncMock(return_value=_session()),
        ),
        patch("superpal.webapp.routes.get_player_fights", new=AsyncMock(return_value=fights)),
        patch(
            "superpal.webapp.routes.get_member_card_context",
            new=AsyncMock(return_value=_member()),
        ),
    ):
        response = await client.get("/fights")
    assert response.status_code == 200
    assert "Bob" in response.text
    assert "your turn" in response.text
    assert "/fight/7/battle" in response.text
    assert "Carol" in response.text
    assert "you won" in response.text
