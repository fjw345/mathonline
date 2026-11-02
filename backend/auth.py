import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request

from . import config
from .store import store


def now():
    return datetime.now(UTC).isoformat()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
    ).hex()
    return salt + ":" + value


def valid_password(password, value):
    return secrets.compare_digest(password_hash(password, value.split(":")[0]), value)


def auth_request(method, path, **kwargs):
    response = httpx.request(
        method,
        config.SB_URL + "/auth/v1/" + path,
        headers={"apikey": config.SB_ANON, **kwargs.pop("headers", {})},
        timeout=25,
        **kwargs,
    )
    if response.status_code >= 400:
        raise HTTPException(400, "登录信息无效或认证服务暂不可用，请检查后重试。")
    return response.json() if response.content else {}


def session_cookies(response, token, refresh=None):
    secure = config.MODE == "supabase"
    response.set_cookie(
        "math_session",
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=3600 if refresh else 60 * 60 * 24 * 7,
        path="/",
    )
    if refresh:
        response.set_cookie(
            "math_refresh",
            refresh,
            httponly=True,
            secure=secure,
            samesite="lax",
            max_age=60 * 60 * 24 * 30,
            path="/api/v1/auth",
        )


def profile_for_remote(user):
    existing = store.get("profiles", id=user["id"])
    if existing:
        return existing
    return store.put(
        "profiles",
        {
            "id": user["id"],
            "email": user.get("email", ""),
            "nickname": user.get("user_metadata", {}).get("nickname", "同学"),
            "role": "user",
            "created_at": now(),
        },
    )


def current_user(request: Request, required=True):
    token = request.cookies.get("math_session") or request.headers.get(
        "authorization", ""
    ).removeprefix("Bearer ")
    user = None
    if token:
        if config.MODE == "local":
            session = store.get(
                "local_sessions", token_hash=hashlib.sha256(token.encode()).hexdigest()
            )
            if session and session["expires_at"] > now():
                user = store.get("profiles", id=session["user_id"])
        else:
            try:
                remote = auth_request(
                    "GET", "user", headers={"Authorization": "Bearer " + token}
                )
                user = profile_for_remote(remote)
            except HTTPException:
                pass
    if not user and required:
        raise HTTPException(401, "请先登录。")
    return user


def require_admin(request, moderation=False):
    user = current_user(request)
    roles = ("admin", "moderator") if moderation else ("admin",)
    if user["role"] not in roles:
        raise HTTPException(403, "没有管理权限。")
    return user


def register_local(email, password, nickname):
    with store.transaction() as tx:
        if tx.get("profiles", email=email):
            raise HTTPException(409, "邮箱已注册。")
        uid = str(uuid4())
        role = "admin" if not tx.list("profiles") else "user"
        profile = tx.put(
            "profiles",
            {
                "id": uid,
                "email": email,
                "nickname": nickname,
                "role": role,
                "created_at": now(),
            },
        )
        tx.put("local_users", {"id": uid, "password_hash": password_hash(password)})
    return profile


def login_local(email, password, response):
    profile = store.get("profiles", email=email)
    creds = store.get("local_users", id=profile["id"]) if profile else None
    if not creds or not valid_password(password, creds["password_hash"]):
        raise HTTPException(400, "邮箱或密码不正确。")
    token = secrets.token_urlsafe(32)
    store.put(
        "local_sessions",
        {
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "user_id": profile["id"],
            "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
    )
    session_cookies(response, token)
    return profile
