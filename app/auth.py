"""Password authentication, revocable sessions, and CSRF protection."""

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from app.database import connection

router = APIRouter(prefix="/api/auth", tags=["account"])
COOKIE = "wishlist_session"
_attempts = defaultdict(deque)
_lock = threading.Lock()


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        value = value.strip().lower()
        if value.count("@") != 1 or "." not in value.split("@")[-1] or any(c.isspace() for c in value):
            raise ValueError("Enter a valid email address")
        return value


class Registration(Credentials):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def name_format(cls, value):
        if not value.strip():
            raise ValueError("Name cannot be empty")
        return value.strip()


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password, encoded):
    _, salt, expected = encoded.split("$")
    actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(actual, expected)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def check_rate(request):
    key = request.client.host if request.client else "local"
    now = time.monotonic()
    with _lock:
        entries = _attempts[key]
        while entries and entries[0] < now - 300:
            entries.popleft()
        if len(entries) >= 20:
            raise HTTPException(429, "Too many attempts. Try again in five minutes.")
        entries.append(now)


def current_user(request: Request):
    with connection() as db:
        row = db.execute(
            """SELECT users.id, users.email, users.name, sessions.csrf FROM sessions
            JOIN users ON users.id=sessions.user_id WHERE token_hash=? AND expires_at>?""",
            (token_hash(request.cookies.get(COOKIE, "")), time.time()),
        ).fetchone()
    if row is None:
        raise HTTPException(401, "Please sign in")
    user = dict(row)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), user["csrf"]):
            raise HTTPException(403, "Session verification failed. Refresh the page.")
    return user


def open_session(response, user_id, request):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    seconds = get_settings().session_days * 86400
    with connection() as db:
        db.execute(
            "DELETE FROM sessions WHERE expires_at<? OR token_hash=?",
            (time.time(), token_hash(request.cookies.get(COOKIE, ""))),
        )
        db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?)", (token_hash(token), user_id, csrf, time.time() + seconds)
        )
        user = dict(db.execute("SELECT id,email,name FROM users WHERE id=?", (user_id,)).fetchone())
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=get_settings().secure_cookies,
        max_age=seconds,
        path="/",
    )
    return {**user, "csrf": csrf}


@router.post("/register", status_code=201)
def register(data: Registration, request: Request, response: Response):
    check_rate(request)
    encoded = hash_password(data.password)
    try:
        with connection() as db:
            uid = db.execute(
                "INSERT INTO users(email,name,password_hash,created_at) VALUES(?,?,?,?)",
                (data.email, data.name, encoded, time.time()),
            ).lastrowid
            db.execute(
                "INSERT INTO wishlists(user_id,name,created_at) VALUES(?,?,?)",
                (uid, "My wishlist", time.time()),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "An account with this email already exists") from None
    return open_session(response, uid, request)


@router.post("/login")
def login(data: Credentials, request: Request, response: Response):
    check_rate(request)
    with connection() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (data.email,)).fetchone()
    encoded = user["password_hash"] if user else "scrypt$" + "00" * 16 + "$" + "00" * 64
    if not verify_password(data.password, encoded) or not user:
        raise HTTPException(401, "Email or password is incorrect")
    return open_session(response, user["id"], request)


@router.get("/me")
def me(user=Depends(current_user)):
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user=Depends(current_user)):
    with connection() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(request.cookies.get(COOKIE, "")),))
    response.delete_cookie(COOKIE, path="/")
