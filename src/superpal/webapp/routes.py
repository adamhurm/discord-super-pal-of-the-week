import asyncio
import dataclasses
import json
import uuid
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import superpal.palymarket.service as palymarket_svc
from superpal.economy import boin_service, exchange_service
from superpal.cards.db import DB_PATH
from superpal.cards.fight_service import (
    ATTACKS,
    RARITY_STATS,
    get_fight,
    get_fight_session,
    get_fight_state,
    mark_player_ready,
    process_action,
    set_fight_cards,
    use_fight_token,
)
from superpal.cards.models import CardRef
from superpal.cards.pringle_service import ITEM_NAMES, get_player_items
from superpal.cards.service import (
    accept_offer,
    add_draws,
    add_member,
    award_card,
    cancel_listing,
    cancel_offer,
    create_listing,
    create_offer,
    decline_offer,
    get_active_listings,
    get_all_members_for_admin,
    get_collection,
    get_draw_audit,
    get_my_offers,
    get_player_listings,
    get_pool_stats,
    reset_draw_log,
    set_excluded,
    set_forced_rarity,
    set_member_avatar,
    set_member_bio_stats,
    trade_in,
    use_magic_link,
)
from superpal.cards.service import (
    sync_members as _sync_members,
)
from superpal.webapp.auth import get_session_from_request, set_session_cookie

# fight_id -> {player_id: WebSocket}
_fight_connections: dict[int, dict[str, WebSocket]] = {}

IMAGES_DIR = Path(DB_PATH).parent / "images"

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _tojson_dc(value: object) -> str:
    def _default(o: object) -> object:
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")
    return json.dumps(value, default=_default)


templates.env.filters["tojson"] = _tojson_dc

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse(request, "index.html")


async def _collection_context(user_id: str) -> dict:
    data = await get_collection(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url FROM members WHERE discord_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        async with db.execute(
            "SELECT COALESCE(SUM(draws_used), 0) FROM draw_log WHERE user_id = ?",
            (user_id,),
        ) as cur:
            drow = await cur.fetchone()
    total_draws = drow[0] if drow else 0
    unique_members = len({c["member_id"] for c in data["owned"]})
    total_eligible = unique_members + len(data["undiscovered"])
    completion_pct = round(unique_members / total_eligible * 100) if total_eligible > 0 else 0

    # Build a lookup: (member_id, rarity) -> listing_id for the user's active listings
    my_listings = await get_player_listings(user_id)
    listed_card_keys: dict[str, int] = {}
    for listing in my_listings:
        for item in listing.items:
            listed_card_keys[f"{item.member_id}:{item.rarity}"] = listing.id

    for card in data["owned"]:
        key = f"{card['member_id']}:{card['rarity']}"
        card["listing_id"] = listed_card_keys.get(key)

    return {
        "display_name": row[0] if row else "Unknown",
        "avatar_url": row[1] if row else None,
        "owned": data["owned"],
        "undiscovered": data["undiscovered"],
        "counts": data["counts"],
        "total_cards": sum(c["quantity"] for c in data["owned"]),
        "total_draws": total_draws,
        "unique_members": unique_members,
        "completion_pct": completion_pct,
    }


async def _marketplace_context(user_id: str) -> dict:
    listings = await get_active_listings(exclude_owner_id=user_id)
    my_listings = await get_player_listings(user_id)
    my_offers = await get_my_offers(user_id)
    collection = await get_collection(user_id)

    # Aggregate active traders for sidebar
    trader_counts: dict[str, dict] = {}
    for listing in listings:
        oid = listing.owner_id
        if oid not in trader_counts:
            trader_counts[oid] = {"display_name": listing.owner_display_name, "count": 0}
        trader_counts[oid]["count"] += 1
    active_traders = sorted(trader_counts.values(), key=lambda x: -x["count"])

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url FROM members WHERE discord_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()

    return {
        "display_name": row[0] if row else "Unknown",
        "avatar_url": row[1] if row else None,
        "listings": listings,
        "my_listings": my_listings,
        "my_offers": my_offers,
        "my_collection": collection["owned"],
        "active_traders": active_traders,
        "pending_offer_count": len(my_offers),
    }


@router.get("/marketplace", response_class=HTMLResponse)
async def marketplace_view(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    ctx = await _marketplace_context(session.user_id)
    return templates.TemplateResponse(request, "marketplace.html", ctx)


@router.post("/marketplace/listing")
async def create_listing_route(
    request: Request,
    card_member_ids: list[str] = Form(...),
    card_rarities: list[str] = Form(...),
    ask_note: str = Form(""),
):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    items = [
        CardRef(member_id=mid, rarity=rar)
        for mid, rar in zip(card_member_ids, card_rarities, strict=False)
    ]
    await create_listing(session.user_id, items, ask_note.strip() or None)
    return RedirectResponse(url="/collection", status_code=303)


@router.post("/marketplace/listing/{listing_id}/cancel")
async def cancel_listing_route(listing_id: int, request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    await cancel_listing(listing_id, session.user_id)
    return RedirectResponse(url="/collection", status_code=303)


@router.post("/marketplace/listing/{listing_id}/offer")
async def create_offer_route(
    listing_id: int,
    request: Request,
    card_member_ids: list[str] = Form(...),
    card_rarities: list[str] = Form(...),
):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    items = [
        CardRef(member_id=mid, rarity=rar)
        for mid, rar in zip(card_member_ids, card_rarities, strict=False)
    ]
    offer = await create_offer(listing_id, session.user_id, items)
    if not isinstance(offer, str):
        try:
            from bot import notify_trade_offer as _notify
            asyncio.create_task(_notify(offer.id))  # noqa: RUF006
        except ImportError:
            pass
    return RedirectResponse(url="/marketplace", status_code=303)


@router.post("/marketplace/offer/{offer_id}/accept")
async def accept_offer_route(offer_id: int, request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    ok, _err = await accept_offer(offer_id, session.user_id)
    if ok:
        try:
            from bot import edit_offer_dm as _edit
            asyncio.create_task(_edit(offer_id, "Trade accepted! Cards have been exchanged."))  # noqa: RUF006
        except ImportError:
            pass
    return RedirectResponse(url="/marketplace", status_code=303)


@router.post("/marketplace/offer/{offer_id}/decline")
async def decline_offer_route(offer_id: int, request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    await decline_offer(offer_id, session.user_id)
    try:
        from bot import edit_offer_dm as _edit
        asyncio.create_task(_edit(offer_id, "Offer declined."))  # noqa: RUF006
    except ImportError:
        pass
    return RedirectResponse(url="/marketplace", status_code=303)


@router.post("/marketplace/offer/{offer_id}/cancel")
async def cancel_offer_route(offer_id: int, request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    await cancel_offer(offer_id, session.user_id)
    return RedirectResponse(url="/marketplace", status_code=303)


async def _admin_context() -> dict:
    return {
        "members": await get_all_members_for_admin(),
        "stats": await get_pool_stats(),
    }


async def _expired_command_for_token(token: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT link_type FROM magic_links WHERE token = ?", (token,)) as cur:
            row = await cur.fetchone()
    return "/admin-link" if row and row[0] == "admin" else "/my-collection"


@router.get("/link/{token}")
async def magic_link_landing(token: str, request: Request):
    link = await use_magic_link(token)
    if link is None:
        command = await _expired_command_for_token(token)
        return templates.TemplateResponse(request, "expired.html", {"command": command})
    if link.link_type == "admin":
        ctx = await _admin_context()
        template, replace_url = "admin.html", "/admin"
    else:
        ctx = await _collection_context(link.user_id)
        template, replace_url = "collection.html", "/collection"
    resp = templates.TemplateResponse(request, template, {**ctx, "replace_url": replace_url})
    assert link.session_token is not None
    set_session_cookie(resp, link.session_token)
    return resp


@router.get("/collection", response_class=HTMLResponse)
async def collection_view(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    ctx = await _collection_context(session.user_id)
    return templates.TemplateResponse(request, "collection.html", ctx)


@router.post("/collection/trade-in", response_class=HTMLResponse)
async def collection_trade_in(
    request: Request,
    member_id: str = Form(...),
    rarity: str = Form(...),
):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    card = await trade_in(owner_id=session.user_id, card_member_id=member_id, rarity=rarity)
    if card is None:
        return RedirectResponse(url="/collection", status_code=303)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url FROM members WHERE discord_id = ?",
            (card.card_member_id,),
        ) as cur:
            row = await cur.fetchone()
    return templates.TemplateResponse(
        request,
        "trade_result.html",
        {
            "display_name": row[0] if row else "Unknown",
            "avatar_url": row[1] if row else None,
            "rarity": card.rarity,
            "quantity": card.quantity,
        },
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_view(request: Request):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    ctx = await _admin_context()
    return templates.TemplateResponse(request, "admin.html", ctx)


@router.post("/admin/exclude/{member_id}")
async def toggle_exclude(member_id: str, request: Request):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    members = await get_all_members_for_admin()
    current = next((m for m in members if m["discord_id"] == member_id), None)
    if current:
        await set_excluded(member_id, excluded=not current["is_excluded"])
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/sync")
async def admin_sync(request: Request):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    try:
        from bot import _guild_members_cache

        if _guild_members_cache:
            await _sync_members(_guild_members_cache)
    except ImportError:
        pass  # running in isolation — sync skipped
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/reset-draws")
async def admin_reset_draws(request: Request):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    await reset_draw_log()
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/member/add")
async def admin_add_member(
    request: Request,
    discord_id: str = Form(""),
    display_name: str = Form(...),
):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    if not discord_id.strip():
        discord_id = str(uuid.uuid4())
    await add_member(discord_id, display_name)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/member/{member_id}/avatar")
async def admin_set_member_avatar(
    member_id: str,
    request: Request,
    image: UploadFile = File(...),  # noqa: B008 — FastAPI sentinel pattern
):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    suffix = Path(image.filename or "upload.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        suffix = ".png"
    filename = f"{member_id}{suffix}"
    IMAGES_DIR.mkdir(exist_ok=True)
    (IMAGES_DIR / filename).write_bytes(await image.read())
    await set_member_avatar(member_id, f"/static/avatars/{filename}")
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/member/{member_id}/forced-rarity")
async def admin_set_forced_rarity(
    member_id: str,
    request: Request,
    rarity: str = Form(...),
):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    await set_forced_rarity(member_id, rarity or None)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/award")
async def admin_award_card(
    request: Request,
    owner_id: str = Form(...),
    card_member_id: str = Form(...),
    rarity: str = Form(...),
    quantity: int = Form(1),
):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    await award_card(owner_id, card_member_id, rarity, max(1, quantity))
    return RedirectResponse(url="/admin", status_code=303)


@router.get("/admin/audit", response_class=HTMLResponse)
async def admin_audit(request: Request, user_id: str = ""):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    ctx = await _admin_context()
    audit_result = await get_draw_audit(user_id) if user_id else None
    return templates.TemplateResponse(
        request,
        "admin.html",
        {**ctx, "audit_result": audit_result, "audit_user_id": user_id},
    )


@router.post("/admin/add-draws")
async def admin_add_draws(
    request: Request,
    user_id: str = Form(...),
    quantity: int = Form(1),
):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    await add_draws(user_id, max(1, quantity))
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/member/{member_id}/bio-stats")
async def admin_set_bio_stats(
    member_id: str,
    request: Request,
    bio: str = Form(""),
    stats_text: str = Form(""),
):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {"command": "/admin-link"})
    stats_dict: dict[str, str] = {}
    for line in stats_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            if k.strip():
                stats_dict[k.strip()] = v.strip()
    await set_member_bio_stats(
        member_id,
        bio.strip(),
        json.dumps(stats_dict) if stats_dict else "",
    )
    return RedirectResponse(url="/admin", status_code=303)


# ─── Fight routes ────────────────────────────────────────────────────────────


async def _resolve_fight_session(request: Request, fight_id: int) -> tuple[str | None, str | None]:
    """
    Determine the player_id for a fight request.
    Returns (player_id, session_token) or (None, None) if unauthenticated.
    Checks the `fs` query param first, then falls back to bringus_session.
    """
    fs = request.query_params.get("fs")
    if fs:
        info = await get_fight_session(fs)
        if info and info["fight_id"] == fight_id:
            return info["player_id"], fs

    # Fallback: regular bringus_session (e.g., player is already logged in)
    session = await get_session_from_request(request)
    if session:
        fight = await get_fight(fight_id)
        if fight and session.user_id in (fight.challenger_id, fight.opponent_id):
            return session.user_id, None

    return None, None


@router.get("/fight/{fight_id}/lobby", response_class=HTMLResponse)
async def fight_lobby(fight_id: int, request: Request, ft: str = "", fs: str = ""):
    """Fight lobby: pick cards and click Ready."""
    session_token = fs

    # Consume a one-time fight token if present
    if ft:
        result = await use_fight_token(ft)
        if result is None:
            return templates.TemplateResponse(request, "expired.html", {"command": "/card-fight"})
        _, player_id, session_token = result
        return RedirectResponse(url=f"/fight/{fight_id}/lobby?fs={session_token}", status_code=303)

    player_id, _ = await _resolve_fight_session(request, fight_id)
    if not player_id:
        return templates.TemplateResponse(request, "expired.html", {"command": "/card-fight"})

    fight = await get_fight(fight_id)
    if not fight or fight.status not in ("lobby", "active"):
        return templates.TemplateResponse(request, "expired.html", {"command": "/card-fight"})

    if fight.status == "active":
        return RedirectResponse(url=f"/fight/{fight_id}/battle?fs={session_token}", status_code=303)

    # Determine which player is ready
    is_challenger = player_id == fight.challenger_id
    already_ready = fight.challenger_ready if is_challenger else fight.opponent_ready

    # Load opponent name
    opponent_id = fight.opponent_id if is_challenger else fight.challenger_id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name FROM members WHERE discord_id = ?", (opponent_id,)
        ) as cur:
            row = await cur.fetchone()
    opponent_name = row[0] if row else opponent_id

    # Load user's cards for the picker
    data = await get_collection(player_id)
    owned_cards = [c for c in data["owned"] if c["quantity"] > 0]

    return templates.TemplateResponse(
        request,
        "fight_lobby.html",
        {
            "fight_id": fight_id,
            "fight": fight,
            "player_id": player_id,
            "opponent_name": opponent_name,
            "owned_cards": owned_cards,
            "already_ready": already_ready,
            "session_token": session_token,
            "rarity_stats": RARITY_STATS,
        },
    )


@router.post("/fight/{fight_id}/lobby/ready")
async def fight_ready(
    fight_id: int,
    request: Request,
    fs: str = Form(""),
    slots: list[str] = Form(default=[]),  # noqa: B008 — FastAPI sentinel pattern
):
    """Mark the player as ready with chosen cards."""
    info = await get_fight_session(fs)
    if not info or info["fight_id"] != fight_id:
        return templates.TemplateResponse(request, "expired.html", {"command": "/card-fight"})
    player_id = info["player_id"]

    fight = await get_fight(fight_id)
    if not fight or fight.status != "lobby":
        return RedirectResponse(url=f"/fight/{fight_id}/lobby?fs={fs}", status_code=303)

    required_slots = 1 if fight.mode == "quick" else 3
    if len(slots) != required_slots:
        return RedirectResponse(url=f"/fight/{fight_id}/lobby?fs={fs}", status_code=303)

    card_slots = []
    for i, slot_val in enumerate(slots, start=1):
        try:
            member_id, rarity = slot_val.split(":", 1)
        except ValueError:
            return RedirectResponse(url=f"/fight/{fight_id}/lobby?fs={fs}", status_code=303)
        card_slots.append({"card_member_id": member_id, "rarity": rarity, "slot": i})

    ok = await set_fight_cards(fight_id, player_id, card_slots)
    if not ok:
        return RedirectResponse(url=f"/fight/{fight_id}/lobby?fs={fs}", status_code=303)

    both_ready, _ = await mark_player_ready(fight_id, player_id)

    if both_ready:
        # Notify the other connected WS client (if any) that the fight started
        state = await get_fight_state(fight_id)
        await _broadcast(fight_id, {"type": "state", "data": state})
        return RedirectResponse(url=f"/fight/{fight_id}/battle?fs={fs}", status_code=303)

    return RedirectResponse(url=f"/fight/{fight_id}/lobby?fs={fs}", status_code=303)


@router.get("/fight/{fight_id}/battle", response_class=HTMLResponse)
async def fight_battle(fight_id: int, request: Request, fs: str = ""):
    player_id, session_token = await _resolve_fight_session(request, fight_id)
    if not player_id:
        return templates.TemplateResponse(request, "expired.html", {"command": "/card-fight"})

    fight = await get_fight(fight_id)
    if not fight or fight.status not in ("active", "completed"):
        return templates.TemplateResponse(request, "expired.html", {"command": "/card-fight"})

    effective_fs = session_token or fs
    opponent_id = fight.opponent_id if player_id == fight.challenger_id else fight.challenger_id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name FROM members WHERE discord_id = ?", (opponent_id,)
        ) as cur:
            row = await cur.fetchone()
    opponent_name = row[0] if row else opponent_id

    items = await get_player_items(player_id)

    return templates.TemplateResponse(
        request,
        "fight_battle.html",
        {
            "fight_id": fight_id,
            "player_id": player_id,
            "opponent_id": opponent_id,
            "opponent_name": opponent_name,
            "fight_mode": fight.mode,
            "session_token": effective_fs,
            "attacks": ATTACKS,
            "items": items,
            "item_names": ITEM_NAMES,
        },
    )


async def _broadcast(fight_id: int, message: dict) -> None:
    conns = _fight_connections.get(fight_id, {})
    dead = []
    for pid, ws in conns.items():
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(pid)
    for pid in dead:
        conns.pop(pid, None)


@router.websocket("/ws/fight/{fight_id}")
async def fight_ws(websocket: WebSocket, fight_id: int, fs: str = ""):
    info = await get_fight_session(fs) if fs else None
    if not info or info["fight_id"] != fight_id:
        await websocket.close(code=4003)
        return
    player_id = info["player_id"]

    fight = await get_fight(fight_id)
    if not fight or fight.status not in ("active", "completed"):
        await websocket.close(code=4004)
        return

    await websocket.accept()
    _fight_connections.setdefault(fight_id, {})[player_id] = websocket

    try:
        state = await get_fight_state(fight_id)
        await websocket.send_json({"type": "state", "data": state})

        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            detail = data.get("detail", {})

            success, err, new_state = await process_action(fight_id, player_id, action, detail)
            if not success:
                await websocket.send_json({"type": "error", "message": err})
                continue

            await _broadcast(fight_id, {"type": "state", "data": new_state})

            if new_state.get("status") == "completed":
                break

    except WebSocketDisconnect:
        pass
    finally:
        _fight_connections.get(fight_id, {}).pop(player_id, None)


@router.get("/api/fight/{fight_id}/state")
async def fight_state_api(fight_id: int, request: Request, fs: str = ""):
    """Lightweight fallback poll endpoint for the battle page."""
    player_id, _ = await _resolve_fight_session(request, fight_id)
    if not player_id:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    fight = await get_fight(fight_id)
    if not fight or fight.status not in ("active", "completed"):
        return JSONResponse({"error": "fight_not_found"}, status_code=404)
    state = await get_fight_state(fight_id)
    return JSONResponse(state)


# ─── Palymarket routes ───────────────────────────────────────────────────────


@router.get("/palymarket", response_class=HTMLResponse)
async def palymarket_list(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    balance = await palymarket_svc.get_palycoin_balance(session.user_id)
    markets = await palymarket_svc.list_markets()
    player_bets = await palymarket_svc.get_player_active_bets(session.user_id)
    bet_map = {bet.market_id: bet for _, bet in player_bets}
    return templates.TemplateResponse(request, "palymarket_list.html", {
        "balance": balance, "markets": markets, "bet_map": bet_map,
        "is_admin": session.link_type == "admin",
    })


@router.get("/palymarket/pending", response_class=HTMLResponse)
async def palymarket_pending(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    if session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html")
    markets = await palymarket_svc.list_pending_markets()
    return templates.TemplateResponse(request, "palymarket_pending.html", {"markets": markets})


@router.post("/palymarket/exchange")
async def palymarket_exchange(request: Request, pringle_amount: int = Form(...)):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    ok, reason, _ = await exchange_service.exchange(
        session.user_id, exchange_service.PRINGLES, exchange_service.PALYCOINS, pringle_amount
    )
    if not ok:
        return RedirectResponse(url=f"/palymarket?error={reason}", status_code=303)
    return RedirectResponse(url="/palymarket", status_code=303)


@router.get("/economy", response_class=HTMLResponse)
async def economy(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    boins = await boin_service.get_balance(session.user_id)
    from superpal.cards.pringle_service import get_balance as get_pringle_balance
    pringles = await get_pringle_balance(session.user_id)
    palycoins = await palymarket_svc.get_palycoin_balance(session.user_id)
    return templates.TemplateResponse(request, "economy.html", {
        "boins": boins, "pringles": pringles, "palycoins": palycoins,
    })


@router.post("/economy/exchange")
async def economy_exchange(
    request: Request,
    from_currency: str = Form(...),
    to_currency: str = Form(...),
    amount: int = Form(...),
):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    ok, reason, received = await exchange_service.exchange(
        session.user_id, from_currency, to_currency, amount
    )
    if not ok:
        return RedirectResponse(url=f"/economy?error={reason}", status_code=303)
    return RedirectResponse(
        url=f"/economy?success=Exchanged+{amount}+{from_currency}+for+{received}+{to_currency}",
        status_code=303,
    )


@router.get("/palymarket/{market_id}", response_class=HTMLResponse)
async def palymarket_detail(request: Request, market_id: int):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    market = await palymarket_svc.get_market(market_id)
    if market is None:
        return templates.TemplateResponse(request, "expired.html")
    bets = await palymarket_svc.get_bets_for_market(market_id)
    player_bet = await palymarket_svc.get_player_bet(market_id, session.user_id)
    balance = await palymarket_svc.get_palycoin_balance(session.user_id)
    return templates.TemplateResponse(request, "palymarket_detail.html", {
        "market": market, "bets": bets, "player_bet": player_bet,
        "balance": balance, "is_admin": session.link_type == "admin",
    })


@router.post("/palymarket/{market_id}/bet")
async def palymarket_bet(
    request: Request, market_id: int, side: str = Form(...), amount: int = Form(...)
):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    ok, reason = await palymarket_svc.place_or_update_bet(market_id, session.user_id, side, amount)
    if not ok:
        return RedirectResponse(url=f"/palymarket/{market_id}?error={reason}", status_code=303)
    return RedirectResponse(url=f"/palymarket/{market_id}", status_code=303)


@router.post("/palymarket/{market_id}/approve")
async def palymarket_approve(request: Request, market_id: int):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html")
    ok, reason = await palymarket_svc.approve_market(market_id, session.user_id)
    if not ok:
        return RedirectResponse(url=f"/palymarket/pending?error={reason}", status_code=303)
    return RedirectResponse(url="/palymarket/pending", status_code=303)


@router.post("/palymarket/{market_id}/reject")
async def palymarket_reject(request: Request, market_id: int):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html")
    ok, reason = await palymarket_svc.reject_market(market_id, session.user_id)
    if not ok:
        return RedirectResponse(url=f"/palymarket/pending?error={reason}", status_code=303)
    return RedirectResponse(url="/palymarket/pending", status_code=303)


@router.post("/palymarket/{market_id}/close")
async def palymarket_close(request: Request, market_id: int):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html")
    ok, reason = await palymarket_svc.close_market(market_id, session.user_id)
    if not ok:
        return RedirectResponse(url=f"/palymarket/{market_id}?error={reason}", status_code=303)
    return RedirectResponse(url=f"/palymarket/{market_id}", status_code=303)


@router.post("/palymarket/{market_id}/resolve")
async def palymarket_resolve(
    request: Request, market_id: int, outcome: str = Form(...)
):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html")
    result = await palymarket_svc.resolve_market(market_id, outcome, session.user_id)
    if "error" in result:
        return RedirectResponse(url=f"/palymarket/{market_id}?error={result['error']}", status_code=303)
    return RedirectResponse(url=f"/palymarket/{market_id}", status_code=303)
