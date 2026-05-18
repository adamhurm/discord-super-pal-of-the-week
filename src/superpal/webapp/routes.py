import json
import uuid
import aiosqlite
from fastapi import APIRouter, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from superpal.cards.db import DB_PATH
from superpal.cards.service import (
    add_member,
    award_card,
    use_magic_link,
    get_collection,
    get_all_members_for_admin,
    get_pool_stats,
    reset_draw_log,
    set_excluded,
    set_forced_rarity,
    set_member_avatar,
    set_member_bio_stats,
    sync_members as _sync_members,
    trade_in,
)
from superpal.cards.fight_service import (
    use_fight_token,
    get_fight_session,
    get_fight,
    get_fight_state,
    set_fight_cards,
    mark_player_ready,
    process_action,
    ATTACKS,
    RARITY_STATS,
)
from superpal.cards.pringle_service import get_player_items, ITEM_NAMES, ITEM_DESCRIPTIONS
from superpal.webapp.auth import get_session_from_request, set_session_cookie

# fight_id -> {player_id: WebSocket}
_fight_connections: dict[int, dict[str, WebSocket]] = {}

IMAGES_DIR = Path(DB_PATH).parent / "images"

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

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
    unique_members = len({c["member_id"] for c in data["owned"]})
    total_eligible = unique_members + len(data["undiscovered"])
    completion_pct = round(unique_members / total_eligible * 100) if total_eligible > 0 else 0
    return {
        "display_name": row[0] if row else "Unknown",
        "avatar_url": row[1] if row else None,
        "owned": data["owned"],
        "undiscovered": data["undiscovered"],
        "counts": data["counts"],
        "total_cards": sum(c["quantity"] for c in data["owned"]),
        "unique_members": unique_members,
        "completion_pct": completion_pct,
    }


async def _admin_context() -> dict:
    return {
        "members": await get_all_members_for_admin(),
        "stats": await get_pool_stats(),
    }


async def _expired_command_for_token(token: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT link_type FROM magic_links WHERE token = ?", (token,)
        ) as cur:
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
    return templates.TemplateResponse(request, "trade_result.html", {
        "display_name": row[0] if row else "Unknown",
        "avatar_url": row[1] if row else None,
        "rarity": card.rarity,
        "quantity": card.quantity,
    })


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
    image: UploadFile = File(...),
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
            return templates.TemplateResponse(request, "expired.html",
                                              {"command": "/card-fight"})
        _, player_id, session_token = result
        return RedirectResponse(
            url=f"/fight/{fight_id}/lobby?fs={session_token}", status_code=303
        )

    player_id, _ = await _resolve_fight_session(request, fight_id)
    if not player_id:
        return templates.TemplateResponse(request, "expired.html",
                                          {"command": "/card-fight"})

    fight = await get_fight(fight_id)
    if not fight or fight.status not in ("lobby", "active"):
        return templates.TemplateResponse(request, "expired.html",
                                          {"command": "/card-fight"})

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

    return templates.TemplateResponse(request, "fight_lobby.html", {
        "fight_id": fight_id,
        "fight": fight,
        "player_id": player_id,
        "opponent_name": opponent_name,
        "owned_cards": owned_cards,
        "already_ready": already_ready,
        "session_token": session_token,
        "rarity_stats": RARITY_STATS,
    })


@router.post("/fight/{fight_id}/lobby/ready")
async def fight_ready(
    fight_id: int,
    request: Request,
    fs: str = Form(""),
    slots: list[str] = Form(default=[]),
):
    """Mark the player as ready with chosen cards."""
    info = await get_fight_session(fs)
    if not info or info["fight_id"] != fight_id:
        return templates.TemplateResponse(request, "expired.html",
                                          {"command": "/card-fight"})
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
        return templates.TemplateResponse(request, "expired.html",
                                          {"command": "/card-fight"})

    fight = await get_fight(fight_id)
    if not fight or fight.status not in ("active", "completed"):
        return templates.TemplateResponse(request, "expired.html",
                                          {"command": "/card-fight"})

    effective_fs = session_token or fs
    opponent_id = fight.opponent_id if player_id == fight.challenger_id else fight.challenger_id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name FROM members WHERE discord_id = ?", (opponent_id,)
        ) as cur:
            row = await cur.fetchone()
    opponent_name = row[0] if row else opponent_id

    items = await get_player_items(player_id)

    return templates.TemplateResponse(request, "fight_battle.html", {
        "fight_id": fight_id,
        "player_id": player_id,
        "opponent_id": opponent_id,
        "opponent_name": opponent_name,
        "fight_mode": fight.mode,
        "session_token": effective_fs,
        "attacks": ATTACKS,
        "items": items,
        "item_names": ITEM_NAMES,
    })


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
