import sqlite3
import json
from datetime import datetime, timezone, timedelta

DB_PATH = "shop.sqlite"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT NOT NULL,
                description   TEXT,
                category      TEXT NOT NULL CHECK(category IN ('Apparel','Toys','Other')),
                price         REAL NOT NULL,
                size          TEXT,
                photos        TEXT DEFAULT '[]',
                status        TEXT DEFAULT 'available'
                              CHECK(status IN ('available','reserved','sold')),
                reserved_until TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS orders (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                items_json    TEXT NOT NULL,
                buyer_name    TEXT,
                buyer_contact TEXT,
                note          TEXT,
                status        TEXT DEFAULT 'pending'
                              CHECK(status IN ('pending','approved','rejected')),
                created_at    TEXT DEFAULT (datetime('now'))
            );
        """)


def release_expired():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("""
            UPDATE items SET status='available', reserved_until=NULL
            WHERE status='reserved' AND reserved_until < ?
        """, (now,))
        conn.commit()


def get_items(category=None):
    with get_conn() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM items WHERE status='available' AND category=? ORDER BY id DESC",
                (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM items WHERE status='available' ORDER BY id DESC"
            ).fetchall()
    return [_parse(r) for r in rows]


def get_item(item_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    return _parse(row) if row else None


def get_all_items_admin():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM items ORDER BY CASE status WHEN 'available' THEN 0 WHEN 'reserved' THEN 1 ELSE 2 END, id DESC"
        ).fetchall()
    return [_parse(r) for r in rows]


def _parse(row):
    d = dict(row)
    d["photos"] = json.loads(d.get("photos") or "[]")
    return d


def create_item(data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO items (title, description, category, price, size, photos)
            VALUES (:title, :description, :category, :price, :size, :photos)
        """, data)
        conn.commit()


def update_item(item_id, data: dict):
    data["id"] = item_id
    with get_conn() as conn:
        conn.execute("""
            UPDATE items
            SET title=:title, description=:description, category=:category,
                price=:price, size=:size, photos=:photos
            WHERE id=:id
        """, data)
        conn.commit()


def delete_item(item_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        conn.commit()


def reserve_item(item_id) -> bool:
    until = (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE items SET status='reserved', reserved_until=? WHERE id=? AND status='available'",
            (until, item_id),
        )
        conn.commit()
        return conn.total_changes > 0


def release_item(item_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE items SET status='available', reserved_until=NULL WHERE id=? AND status='reserved'",
            (item_id,),
        )
        conn.commit()


def get_orders():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    orders = []
    for row in rows:
        o = dict(row)
        item_ids = json.loads(o["items_json"])
        o["items"] = [get_item(i) or {"id": i, "title": "(deleted)", "price": 0, "photos": []} for i in item_ids]
        orders.append(o)
    return orders


def create_order(item_ids: list, buyer_name: str, buyer_contact: str, note: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO orders (items_json, buyer_name, buyer_contact, note) VALUES (?,?,?,?)",
            (json.dumps(item_ids), buyer_name, buyer_contact, note),
        )
        conn.commit()


def approve_order(order_id):
    with get_conn() as conn:
        row = conn.execute("SELECT items_json FROM orders WHERE id=?", (order_id,)).fetchone()
        if row:
            for iid in json.loads(row["items_json"]):
                conn.execute("UPDATE items SET status='sold', reserved_until=NULL WHERE id=?", (iid,))
            conn.execute("UPDATE orders SET status='approved' WHERE id=?", (order_id,))
            conn.commit()


def reject_order(order_id):
    with get_conn() as conn:
        row = conn.execute("SELECT items_json FROM orders WHERE id=?", (order_id,)).fetchone()
        if row:
            for iid in json.loads(row["items_json"]):
                conn.execute("UPDATE items SET status='available', reserved_until=NULL WHERE id=?", (iid,))
            conn.execute("UPDATE orders SET status='rejected' WHERE id=?", (order_id,))
            conn.commit()
