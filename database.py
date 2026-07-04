# database.py
# Barcha SQLite bilan ishlash funksiyalari shu yerda.
# aiosqlite - asinxron SQLite drayveri (pip install aiosqlite)

import os
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional

from config import DB_PATH, SUPER_ADMIN_ID, PREMIUM_DURATION_DAYS

DATE_FMT = "%Y-%m-%d %H:%M:%S"


def now_str() -> str:
    return datetime.now().strftime(DATE_FMT)


# =====================================================================
# INIT
# =====================================================================

async def init_db() -> None:
    # DB fayli joylashadigan papka mavjud bo'lmasa, avval uni yaratamiz
    # (masalan DB_PATH environment variable orqali /data/... kabi
    # yangi papkaga ko'rsatilgan bo'lsa).
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT,
                username TEXT,
                status TEXT DEFAULT 'freemium',      -- 'freemium' | 'premium'
                premium_until TEXT,                   -- datetime string yoki NULL
                joined_at TEXT,
                balance INTEGER DEFAULT 0,            -- referral uchun to'plangan pul
                referred_by INTEGER,
                friends_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS movies (
                code TEXT PRIMARY KEY,
                file_id TEXT,
                name TEXT,
                description TEXT,
                genre TEXT,
                language TEXT,
                year TEXT,
                price INTEGER DEFAULT 0,
                added_at TEXT
            );

            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                movie_code TEXT,
                purchased_at TEXT,
                UNIQUE(user_id, movie_code)
            );

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS mandatory_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT,
                chat_id TEXT,
                platform TEXT   -- 'telegram' | 'instagram'
            );

            CREATE TABLE IF NOT EXISTS main_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link TEXT,
                platform TEXT   -- 'telegram' | 'instagram'
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS pending_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                kind TEXT,            -- 'movie' | 'premium'
                movie_code TEXT,
                amount INTEGER,
                status TEXT DEFAULT 'pending',   -- pending | approved | rejected
                created_at TEXT,
                admin_chat_message_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        await db.commit()

    # Super admin har doim admins jadvalida bo'lsin
    await add_admin(SUPER_ADMIN_ID)
    # Default sozlamalar
    await _ensure_setting("premium_price", "0")
    await _ensure_setting("card_number", "")
    await _ensure_setting("card_owner", "")
    await _ensure_setting("referral_bonus", "0")
    await _ensure_setting("total_messages", "0")


async def _ensure_setting(key: str, default: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        if row is None:
            await db.execute("INSERT INTO settings(key, value) VALUES (?, ?)", (key, default))
            await db.commit()


# =====================================================================
# USERS
# =====================================================================

async def get_user(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def create_user_if_not_exists(user_id: int, full_name: str, username: Optional[str],
                                     referred_by: Optional[int] = None) -> bool:
    """Returns True if a new user was created (i.e. first /start)."""
    existing = await get_user(user_id)
    if existing:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users (user_id, full_name, username, status, joined_at, balance, referred_by, friends_count)
               VALUES (?, ?, ?, 'freemium', ?, 0, ?, 0)""",
            (user_id, full_name, username or None, now_str(), referred_by),
        )
        await db.commit()
    return True


async def is_premium(user_id: int) -> bool:
    user = await get_user(user_id)
    if not user or user["status"] != "premium":
        return False
    if user["premium_until"]:
        until = datetime.strptime(user["premium_until"], DATE_FMT)
        if until < datetime.now():
            await set_freemium(user_id)
            return False
    return True


async def set_premium(user_id: int, days: int = PREMIUM_DURATION_DAYS) -> None:
    until = (datetime.now() + timedelta(days=days)).strftime(DATE_FMT)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET status='premium', premium_until=? WHERE user_id=?",
            (until, user_id),
        )
        await db.commit()


async def set_freemium(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET status='freemium', premium_until=NULL WHERE user_id=?",
            (user_id,),
        )
        await db.commit()


async def add_balance(user_id: int, amount: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id)
        )
        await db.commit()


async def deduct_balance(user_id: int, amount: int) -> bool:
    user = await get_user(user_id)
    if not user or user["balance"] < amount:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id)
        )
        await db.commit()
    return True


async def increment_friends(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET friends_count = friends_count + 1 WHERE user_id=?", (user_id,)
        )
        await db.commit()


async def get_all_user_ids() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users")
        rows = await cur.fetchall()
        return [r[0] for r in rows]


async def count_users(since_days: Optional[int] = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        if since_days is None:
            cur = await db.execute("SELECT COUNT(*) FROM users")
        else:
            since = (datetime.now() - timedelta(days=since_days)).strftime(DATE_FMT)
            cur = await db.execute("SELECT COUNT(*) FROM users WHERE joined_at >= ?", (since,))
        row = await cur.fetchone()
        return row[0]


# =====================================================================
# MOVIES
# =====================================================================

async def add_movie(code: str, file_id: str, name: str, description: str,
                     genre: str, language: str, year: str, price: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO movies (code, file_id, name, description, genre, language, year, price, added_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, file_id, name, description, genre, language, year, price, now_str()),
        )
        await db.commit()


async def get_movie(code: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM movies WHERE code=?", (code,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def search_movies_by_name(query: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM movies WHERE name LIKE ? ORDER BY added_at DESC", (f"%{query}%",)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def update_movie_field(code: str, field: str, value) -> None:
    allowed = {"file_id", "code", "name", "description", "genre", "language", "price", "year"}
    if field not in allowed:
        raise ValueError("Invalid field")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE movies SET {field}=? WHERE code=?", (value, code))
        await db.commit()


async def delete_movie(code: str) -> bool:
    movie = await get_movie(code)
    if not movie:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM movies WHERE code=?", (code,))
        await db.commit()
    return True


async def list_movies(offset: int = 0, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM movies ORDER BY added_at DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def count_movies() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM movies")
        row = await cur.fetchone()
        return row[0]


# =====================================================================
# PURCHASES
# =====================================================================

async def add_purchase(user_id: int, movie_code: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO purchases (user_id, movie_code, purchased_at) VALUES (?, ?, ?)",
            (user_id, movie_code, now_str()),
        )
        await db.commit()


async def has_purchased(user_id: int, movie_code: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM purchases WHERE user_id=? AND movie_code=?", (user_id, movie_code)
        )
        row = await cur.fetchone()
        return row is not None


async def get_user_purchases(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT m.* FROM purchases p JOIN movies m ON p.movie_code = m.code
               WHERE p.user_id=? ORDER BY p.purchased_at DESC""",
            (user_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# =====================================================================
# ADMINS
# =====================================================================

async def add_admin(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        await db.commit()


async def is_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row is not None


async def count_admins() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM admins")
        row = await cur.fetchone()
        return row[0]


# =====================================================================
# CHANNELS (majburiy va asosiy)
# =====================================================================

async def add_mandatory_channel(link: str, chat_id: str, platform: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO mandatory_channels (link, chat_id, platform) VALUES (?, ?, ?)",
            (link, chat_id, platform),
        )
        await db.commit()


async def remove_mandatory_channel(channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM mandatory_channels WHERE id=?", (channel_id,))
        await db.commit()


async def list_mandatory_channels() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM mandatory_channels")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def add_main_channel(link: str, platform: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO main_channels (link, platform) VALUES (?, ?)", (link, platform)
        )
        await db.commit()


async def remove_main_channel(channel_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM main_channels WHERE id=?", (channel_id,))
        await db.commit()


async def list_main_channels() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM main_channels")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# =====================================================================
# SETTINGS (karta, premium narxi, referral bonus)
# =====================================================================

async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


async def get_setting(key: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


# =====================================================================
# PENDING PAYMENTS (chek tekshiruv jarayoni)
# =====================================================================

async def create_pending_payment(user_id: int, kind: str, movie_code: Optional[str],
                                  amount: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO pending_payments (user_id, kind, movie_code, amount, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (user_id, kind, movie_code, amount, now_str()),
        )
        await db.commit()
        return cur.lastrowid


async def set_pending_admin_message(payment_id: int, message_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE pending_payments SET admin_chat_message_id=? WHERE id=?",
            (message_id, payment_id),
        )
        await db.commit()


async def get_pending_payment(payment_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM pending_payments WHERE id=?", (payment_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_payment_status(payment_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE pending_payments SET status=? WHERE id=?", (status, payment_id)
        )
        await db.commit()


# =====================================================================
# STATISTIKA
# =====================================================================

async def increment_total_messages() -> None:
    current = await get_setting("total_messages") or "0"
    await set_setting("total_messages", str(int(current) + 1))


async def get_total_messages() -> int:
    return int(await get_setting("total_messages") or "0")


async def get_monthly_revenue() -> int:
    since = (datetime.now() - timedelta(days=30)).strftime(DATE_FMT)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT pp.amount FROM pending_payments pp
               WHERE pp.status='approved' AND pp.created_at >= ?""",
            (since,),
        )
        rows = await cur.fetchall()
        return sum(r["amount"] for r in rows)
