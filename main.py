import os
import json
import uuid
import shutil
from pathlib import Path

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

import db

load_dotenv()

SECRET_KEY    = os.getenv("SECRET_KEY", "change-me")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
TELEGRAM_LINK  = os.getenv("TELEGRAM_LINK", "https://t.me/username")
PHOTOS_DIR     = Path("static/photos")
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

db.init_db()

CATEGORIES = ["Apparel", "Toys", "Other"]


# ── helpers ────────────────────────────────────────────────────────────────────

def cart(request: Request) -> list[int]:
    return request.session.get("cart", [])

def _require_admin(request: Request):
    if not request.session.get("admin"):
        return RedirectResponse("/admin/login", status_code=302)

def _ctx(request: Request, **kwargs):
    return {"cart_count": len(cart(request)), "telegram_link": TELEGRAM_LINK, **kwargs}


# ── public ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, category: str = None):
    db.release_expired()
    if category not in CATEGORIES:
        category = None
    items = db.get_items(category)
    return templates.TemplateResponse(request, "index.html", _ctx(
        request, items=items, category=category, categories=CATEGORIES
    ))


@app.get("/item/{item_id}", response_class=HTMLResponse)
async def item_detail(request: Request, item_id: int):
    item = db.get_item(item_id)
    if not item or item["status"] != "available":
        raise HTTPException(404)
    return templates.TemplateResponse(request, "item.html", _ctx(
        request, item=item, in_cart=(item_id in cart(request))
    ))


@app.post("/cart/add/{item_id}")
async def cart_add(request: Request, item_id: int):
    ok = db.reserve_item(item_id)
    if ok:
        c = cart(request)
        if item_id not in c:
            c.append(item_id)
        request.session["cart"] = c
    return RedirectResponse(request.headers.get("referer", "/"), status_code=303)


@app.post("/cart/remove/{item_id}")
async def cart_remove(request: Request, item_id: int):
    db.release_item(item_id)
    request.session["cart"] = [i for i in cart(request) if i != item_id]
    return RedirectResponse(request.headers.get("referer", "/checkout"), status_code=303)


@app.get("/checkout", response_class=HTMLResponse)
async def checkout_get(request: Request):
    items = [i for i in (db.get_item(i) for i in cart(request)) if i]
    total = sum(i["price"] for i in items)
    return templates.TemplateResponse(request, "checkout.html", _ctx(
        request, items=items, total=total
    ))


@app.post("/checkout")
async def checkout_post(
    request: Request,
    buyer_name: str = Form(...),
    buyer_contact: str = Form(...),
    note: str = Form(""),
):
    c = cart(request)
    if not c:
        return RedirectResponse("/", status_code=303)
    db.create_order(c, buyer_name, buyer_contact, note)
    request.session["cart"] = []
    return RedirectResponse("/done", status_code=303)


@app.get("/done", response_class=HTMLResponse)
async def done(request: Request):
    return templates.TemplateResponse(request, "done.html", _ctx(request))


# ── admin ──────────────────────────────────────────────────────────────────────

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_get(request: Request):
    return templates.TemplateResponse(request, "admin/login.html", {"error": None})


@app.post("/admin/login")
async def admin_login_post(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        request.session["admin"] = True
        return RedirectResponse("/admin/items", status_code=303)
    return templates.TemplateResponse(request, "admin/login.html", {"error": "Wrong password"})


@app.get("/admin/logout")
async def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


@app.get("/admin/items", response_class=HTMLResponse)
async def admin_items(request: Request):
    if r := _require_admin(request): return r
    db.release_expired()
    items = db.get_all_items_admin()
    return templates.TemplateResponse(request, "admin/items.html", {"items": items})


@app.get("/admin/items/add", response_class=HTMLResponse)
async def admin_item_add_get(request: Request):
    if r := _require_admin(request): return r
    return templates.TemplateResponse(request, "admin/item_form.html", {
        "item": None, "categories": CATEGORIES
    })


@app.post("/admin/items/add")
async def admin_item_add_post(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form(...),
    price: float = Form(...),
    size: str = Form(""),
    photos: list[UploadFile] = File(default=[]),
):
    if r := _require_admin(request): return r
    photo_names = _save_photos(photos)
    db.create_item({
        "title": title, "description": description, "category": category,
        "price": price, "size": size or None, "photos": json.dumps(photo_names),
    })
    return RedirectResponse("/admin/items", status_code=303)


@app.get("/admin/items/{item_id}/edit", response_class=HTMLResponse)
async def admin_item_edit_get(request: Request, item_id: int):
    if r := _require_admin(request): return r
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "admin/item_form.html", {
        "item": item, "categories": CATEGORIES
    })


@app.post("/admin/items/{item_id}/edit")
async def admin_item_edit_post(
    request: Request,
    item_id: int,
    title: str = Form(...),
    description: str = Form(""),
    category: str = Form(...),
    price: float = Form(...),
    size: str = Form(""),
    keep_photos: list[str] = Form(default=[]),
    new_photos: list[UploadFile] = File(default=[]),
):
    if r := _require_admin(request): return r
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404)
    # delete photos that were unchecked
    for name in item["photos"]:
        if name not in keep_photos:
            p = PHOTOS_DIR / name
            if p.exists():
                p.unlink()
    photo_names = list(keep_photos) + _save_photos(new_photos)
    db.update_item(item_id, {
        "title": title, "description": description, "category": category,
        "price": price, "size": size or None, "photos": json.dumps(photo_names),
    })
    return RedirectResponse("/admin/items", status_code=303)


@app.post("/admin/items/{item_id}/delete")
async def admin_item_delete(request: Request, item_id: int):
    if r := _require_admin(request): return r
    item = db.get_item(item_id)
    if item:
        for name in item["photos"]:
            p = PHOTOS_DIR / name
            if p.exists():
                p.unlink()
        db.delete_item(item_id)
    return RedirectResponse("/admin/items", status_code=303)


@app.get("/admin/orders", response_class=HTMLResponse)
async def admin_orders(request: Request):
    if r := _require_admin(request): return r
    orders = db.get_orders()
    return templates.TemplateResponse(request, "admin/orders.html", {"orders": orders})


@app.post("/admin/orders/{order_id}/approve")
async def admin_approve(request: Request, order_id: int):
    if r := _require_admin(request): return r
    db.approve_order(order_id)
    return RedirectResponse("/admin/orders", status_code=303)


@app.post("/admin/orders/{order_id}/reject")
async def admin_reject(request: Request, order_id: int):
    if r := _require_admin(request): return r
    db.reject_order(order_id)
    return RedirectResponse("/admin/orders", status_code=303)


# ── util ───────────────────────────────────────────────────────────────────────

def _save_photos(uploads: list[UploadFile]) -> list[str]:
    names = []
    for f in uploads:
        if f.filename:
            ext = Path(f.filename).suffix.lower() or ".jpg"
            name = f"{uuid.uuid4()}{ext}"
            with open(PHOTOS_DIR / name, "wb") as out:
                shutil.copyfileobj(f.file, out)
            names.append(name)
    return names
