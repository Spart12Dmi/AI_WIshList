"""Transaction-scoped SQLite connections; safe to use from independent workers."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings


@contextmanager
def connection():
    path = Path(get_settings().database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    with connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf TEXT NOT NULL, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wishlists (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, image_url TEXT, identity_key TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS offers (
                url TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id),
                title TEXT NOT NULL, shop TEXT NOT NULL, price TEXT NOT NULL, currency TEXT NOT NULL,
                region TEXT NOT NULL, image_url TEXT, availability TEXT, checked_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wishlist_items (
                id INTEGER PRIMARY KEY, wishlist_id INTEGER NOT NULL REFERENCES wishlists(id) ON DELETE CASCADE,
                product_id TEXT REFERENCES products(id), title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                target_price TEXT, target_currency TEXT, region TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS saved_product ON wishlist_items(wishlist_id, product_id)
                WHERE product_id IS NOT NULL;
            CREATE TABLE IF NOT EXISTS search_runs (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), query TEXT NOT NULL,
                region TEXT NOT NULL, status TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL,
                result_count INTEGER NOT NULL DEFAULT 0, warnings TEXT NOT NULL DEFAULT '[]'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS offer_documents USING fts5(
                title, content, url UNINDEXED, region UNINDEXED, checked_at UNINDEXED
            );
        """)
        # Additive migration; preserves all existing accounts and wishlists.
        columns = {row["name"] for row in db.execute("PRAGMA table_info(offers)")}
        if "source_url" not in columns:
            db.execute("ALTER TABLE offers ADD COLUMN source_url TEXT")
        for column in ("brand", "mpn", "color", "size", "gtin"):
            if column not in columns:
                db.execute(f"ALTER TABLE offers ADD COLUMN {column} TEXT")
