import aiosqlite
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from superpal.cards.db import DB_PATH
from superpal.cards.service import (
    consume_magic_link,
    generate_magic_link,
    get_collection,
    get_all_members_for_admin,
    get_pool_stats,
    set_excluded,
    sync_members as _sync_members,
)
from superpal.env import WEBAPP_BASE_URL
from superpal.webapp.auth import get_session_from_request, set_session_cookie

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


@router.post("/collection/refresh")
async def collection_refresh(request: Request):
    session = await get_session_from_request(request)
    if session is None:
        return templates.TemplateResponse(request, "expired.html")
    url = await generate_magic_link(session.user_id, "collection", WEBAPP_BASE_URL)
    token = url.split("/")[-1]
    link = await consume_magic_link(token)
    resp = RedirectResponse(url="/collection", status_code=303)
    if link:
        set_session_cookie(resp, link.session_token)
    return resp


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
