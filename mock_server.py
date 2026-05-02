"""Mock server for UX testing — no Discord credentials or database needed.

Start with:  uv run python mock_server.py
Then open:   http://localhost:8080
"""

import random
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

TEMPLATES_DIR = Path("src/superpal/webapp/templates")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(docs_url=None, redoc_url=None)

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

MOCK_OWNED = [
    {"member_id": "1", "display_name": "Dingus Supreme", "avatar_url": None, "rarity": "legendary", "quantity": 1},
    {"member_id": "2", "display_name": "Florp Xennial",  "avatar_url": None, "rarity": "rare",      "quantity": 1},
    {"member_id": "3", "display_name": "Gozney Wumble",  "avatar_url": None, "rarity": "uncommon",  "quantity": 2},
    {"member_id": "4", "display_name": "Bingus McFlop",  "avatar_url": None, "rarity": "common",    "quantity": 3},
    {"member_id": "5", "display_name": "Plonk Dervis",   "avatar_url": None, "rarity": "common",    "quantity": 1},
    {"member_id": "6", "display_name": "Zibble Frang",   "avatar_url": None, "rarity": "uncommon",  "quantity": 1},
    {"member_id": "7", "display_name": "Crispy Noodleman","avatar_url": None, "rarity": "rare",     "quantity": 1},
    {"member_id": "8", "display_name": "Wumpo the Bold", "avatar_url": None, "rarity": "common",    "quantity": 2},
]

MOCK_UNDISCOVERED = [
    {"discord_id": "9",  "display_name": "???", "avatar_url": None},
    {"discord_id": "10", "display_name": "???", "avatar_url": None},
    {"discord_id": "11", "display_name": "???", "avatar_url": None},
]

MOCK_COUNTS = {
    "common":    sum(c["quantity"] for c in MOCK_OWNED if c["rarity"] == "common"),
    "uncommon":  sum(c["quantity"] for c in MOCK_OWNED if c["rarity"] == "uncommon"),
    "rare":      sum(c["quantity"] for c in MOCK_OWNED if c["rarity"] == "rare"),
    "legendary": sum(c["quantity"] for c in MOCK_OWNED if c["rarity"] == "legendary"),
}

MOCK_MEMBERS = [
    {"discord_id": "1", "display_name": "Dingus Supreme",  "avatar_url": None, "is_excluded": False},
    {"discord_id": "2", "display_name": "Florp Xennial",   "avatar_url": None, "is_excluded": False},
    {"discord_id": "3", "display_name": "Gozney Wumble",   "avatar_url": None, "is_excluded": True},
    {"discord_id": "4", "display_name": "Bingus McFlop",   "avatar_url": None, "is_excluded": False},
    {"discord_id": "5", "display_name": "Plonk Dervis",    "avatar_url": None, "is_excluded": False},
    {"discord_id": "6", "display_name": "Zibble Frang",    "avatar_url": None, "is_excluded": False},
    {"discord_id": "7", "display_name": "Crispy Noodleman","avatar_url": None, "is_excluded": True},
    {"discord_id": "8", "display_name": "Wumpo the Bold",  "avatar_url": None, "is_excluded": False},
    {"discord_id": "9", "display_name": "Snergle Puffkin", "avatar_url": None, "is_excluded": False},
]

MOCK_STATS = {
    "eligible":    sum(1 for m in MOCK_MEMBERS if not m["is_excluded"]),
    "excluded":    sum(1 for m in MOCK_MEMBERS if m["is_excluded"]),
    "total_cards": sum(c["quantity"] for c in MOCK_OWNED),
}

# ---------------------------------------------------------------------------
# Index redirect
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return RedirectResponse(url="/collection")


# ---------------------------------------------------------------------------
# Collection routes
# ---------------------------------------------------------------------------

@app.get("/collection", response_class=HTMLResponse)
async def collection(request: Request):
    total_cards = sum(c["quantity"] for c in MOCK_OWNED)
    unique_members = len({c["member_id"] for c in MOCK_OWNED})
    return templates.TemplateResponse(request, "collection.html", {
        "display_name": "Bingus McFlop",
        "avatar_url": None,
        "owned": MOCK_OWNED,
        "undiscovered": MOCK_UNDISCOVERED,
        "counts": MOCK_COUNTS,
        "total_cards": total_cards,
        "unique_members": unique_members,
    })


@app.post("/collection/trade-in", response_class=HTMLResponse)
async def collection_trade_in(
    request: Request,
    member_id: str = Form(...),
    rarity: str = Form(...),
):
    # Deduct 3 from the traded card (mock only — mutates in-memory list)
    for card in MOCK_OWNED:
        if card["member_id"] == member_id and card["rarity"] == rarity:
            card["quantity"] -= 3
            break
    MOCK_OWNED[:] = [c for c in MOCK_OWNED if c["quantity"] > 0]

    # Pick a random eligible member as the result
    eligible = [m for m in MOCK_MEMBERS if not m["is_excluded"]]
    received = random.choice(eligible) if eligible else {"display_name": "Unknown", "avatar_url": None}

    # Update or add received card in mock collection
    existing = next(
        (c for c in MOCK_OWNED if c["member_id"] == received["discord_id"] and c["rarity"] == rarity),
        None,
    )
    if existing:
        existing["quantity"] += 1
        new_qty = existing["quantity"]
    else:
        MOCK_OWNED.append({"member_id": received["discord_id"], "display_name": received["display_name"],
                           "avatar_url": received["avatar_url"], "rarity": rarity, "quantity": 1})
        new_qty = 1

    return templates.TemplateResponse(request, "trade_result.html", {
        "display_name": received["display_name"],
        "avatar_url": received["avatar_url"],
        "rarity": rarity,
        "quantity": new_qty,
    })


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
async def admin(request: Request):
    return templates.TemplateResponse(request, "admin.html", {
        "members": MOCK_MEMBERS,
        "stats": MOCK_STATS,
    })


@app.post("/admin/exclude/{member_id}")
async def toggle_exclude(member_id: str):
    # Toggle in the mock list so the page reflects the change
    for m in MOCK_MEMBERS:
        if m["discord_id"] == member_id:
            m["is_excluded"] = not m["is_excluded"]
    MOCK_STATS["eligible"] = sum(1 for m in MOCK_MEMBERS if not m["is_excluded"])
    MOCK_STATS["excluded"] = sum(1 for m in MOCK_MEMBERS if m["is_excluded"])
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/sync")
async def admin_sync():
    return RedirectResponse(url="/admin", status_code=303)


# ---------------------------------------------------------------------------
# Expired page (for reference)
# ---------------------------------------------------------------------------

@app.get("/expired", response_class=HTMLResponse)
async def expired(request: Request):
    return templates.TemplateResponse(request, "expired.html")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Mock server running:")
    print("  Collection view  → http://localhost:8080/collection")
    print("  Admin dashboard  → http://localhost:8080/admin")
    print("  Expired page     → http://localhost:8080/expired")
    uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")
