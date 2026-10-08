import hashlib
import hmac
import secrets
import sqlite3
import time
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Request, Response
from .store import uid, now

COOKIE = "rw_session"
SESSION_SECONDS = 7 * 24 * 3600


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password, stored):
    _, salt, _ = stored.split("$")
    return hmac.compare_digest(password_hash(password, salt), stored)


def public_user(user):
    return {key: user[key] for key in ("id", "username", "display_name", "role", "created_at")}


def session_user(store, token):
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    users = store.query("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (token_hash, time.time()))
    return public_user(users[0]) if users else None


def set_session(store, user_id, response):
    token = secrets.token_urlsafe(32)
    with store.lock, store.conn:
        store.conn.execute("DELETE FROM sessions WHERE expires_at<?", (time.time(),))
        store.conn.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time() + SESSION_SECONDS))
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=SESSION_SECONDS, path="/")


class Register(BaseModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,32}$")
    display_name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=10, max_length=128)


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


def account_router(store_getter):
    router = APIRouter(prefix="/api/auth", tags=["accounts"])

    @router.get("/bootstrap")
    def bootstrap():
        count = store_getter().query("SELECT COUNT(*) AS count FROM users")[0]["count"]
        return {"needs_admin": count == 0, "registration_open": True}

    @router.post("/register", status_code=201)
    async def register(body: Register, response: Response):
        import asyncio
        store = store_getter()
        hashed = await asyncio.to_thread(password_hash, body.password)
        name = body.display_name.strip()
        if not name:
            raise HTTPException(422, "显示名称不能为空")
        try:
            with store.lock, store.conn:
                first = store.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
                user_id = uid("user")
                store.conn.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (user_id, body.username.lower(), name, hashed, "admin" if first else "user", now()))
                if first:
                    store.conn.execute("UPDATE projects SET owner_id=? WHERE owner_id IS NULL", (user_id,))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "用户名已存在")
        user = store.query("SELECT * FROM users WHERE id=?", (user_id,))[0]
        set_session(store, user_id, response)
        return {"user": public_user(user)}

    @router.post("/login")
    async def login(body: Login, request: Request, response: Response):
        import asyncio
        store = store_getter()
        key = hashlib.sha256(f"{request.client.host if request.client else 'local'}:{body.username.lower()}".encode()).hexdigest()
        attempts = store.query("SELECT * FROM login_attempts WHERE key=?", (key,))
        if attempts and attempts[0]["blocked_until"] > time.time():
            raise HTTPException(429, "尝试次数过多，请稍后再试")
        users = store.query("SELECT * FROM users WHERE username=?", (body.username.lower(),))
        # Equal-cost hashing also for nonexistent accounts.
        stored = users[0]["password_hash"] if users else password_hash("dummy-not-a-valid-password", "0" * 32)
        matched = await asyncio.to_thread(verify_password, body.password, stored)
        if not users or not matched:
            with store.lock, store.conn:
                previous = attempts[0]["failures"] if attempts and attempts[0]["blocked_until"] == 0 else 0
                failures = previous + 1
                store.conn.execute("INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)", (key, failures, time.time() + 60 if failures >= 5 else 0))
            raise HTTPException(401, "用户名或密码不正确")
        with store.lock, store.conn:
            store.conn.execute("DELETE FROM login_attempts WHERE key=?", (key,))
        set_session(store, users[0]["id"], response)
        return {"user": public_user(users[0])}

    @router.get("/me")
    def me(request: Request):
        return {"user": request.state.user}

    @router.post("/logout")
    def logout(request: Request, response: Response):
        store = store_getter()
        token = request.cookies.get(COOKIE, "")
        with store.lock, store.conn:
            store.conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    return router
