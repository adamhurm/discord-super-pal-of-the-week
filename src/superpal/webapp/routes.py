import aiosqlite
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from superpal.cards.db import DB_PATH
from superpal.cards.service import (
    add_member,
    award_card,
    consume_magic_link,
    get_collection,
    get_all_members_for_admin,
    get_pool_stats,
    set_excluded,
    set_member_avatar,
    sync_members as _sync_members,
    trade_in,
)
from superpal.webapp.auth import get_session_from_request, set_session_cookie

IMAGES_DIR = Path(DB_PATH).parent / "images"

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


@router.get("/link/{token}")
async def magic_link_landing(token: str, request: Request):
    link = await consume_magic_link(token)
    if link is None:
        return templates.TemplateResponse(request, "expired.html")
    redirect_path = "/admin" if link.link_type == "admin" else "/collection"
    resp = RedirectResponse(url=redirect_path, status_code=303)
    set_session_cookie(resp, link.session_token)
    return resp


@router.get("/collection", response_class=HTMLResponse)
async def collection_view(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    data = await get_collection(session.user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT display_name, avatar_url FROM members WHERE discord_id = ?",
            (session.user_id,),
        ) as cur:
            row = await cur.fetchone()
    display_name = row[0] if row else "Unknown"
    avatar_url = row[1] if row else None
    total_cards = sum(c["quantity"] for c in data["owned"])
    unique_members = len({c["member_id"] for c in data["owned"]})
    return templates.TemplateResponse(request, "collection.html", {
        "display_name": display_name,
        "avatar_url": avatar_url,
        "owned": data["owned"],
        "undiscovered": data["undiscovered"],
        "counts": data["counts"],
        "total_cards": total_cards,
        "unique_members": unique_members,
    })


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
        return templates.TemplateResponse(request, "expired.html")
    members = await get_all_members_for_admin()
    stats = await get_pool_stats()
    return templates.TemplateResponse(request, "admin.html", {
        "members": members,
        "stats": stats,
    })


@router.post("/admin/exclude/{member_id}")
async def toggle_exclude(member_id: str, request: Request):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {})
    members = await get_all_members_for_admin()
    current = next((m for m in members if m["discord_id"] == member_id), None)
    if current:
        await set_excluded(member_id, excluded=not current["is_excluded"])
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/sync")
async def admin_sync(request: Request):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {})
    try:
        from bot import _guild_members_cache
        if _guild_members_cache:
            await _sync_members(_guild_members_cache)
    except ImportError:
        pass  # running in isolation — sync skipped
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/member/add")
async def admin_add_member(
    request: Request,
    discord_id: str = Form(...),
    display_name: str = Form(...),
):
    session = await get_session_from_request(request)
    if session is None or session.link_type != "admin":
        return templates.TemplateResponse(request, "expired.html", {})
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
        return templates.TemplateResponse(request, "expired.html", {})
    suffix = Path(image.filename or "upload.png").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        suffix = ".png"
    filename = f"{member_id}{suffix}"
    IMAGES_DIR.mkdir(exist_ok=True)
    (IMAGES_DIR / filename).write_bytes(await image.read())
    await set_member_avatar(member_id, f"/static/avatars/{filename}")
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
        return templates.TemplateResponse(request, "expired.html", {})
    await award_card(owner_id, card_member_id, rarity, max(1, quantity))
    return RedirectResponse(url="/admin", status_code=303)
