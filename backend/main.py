import hashlib
import io
import logging
import re
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import config
from .auth import (
    auth_request,
    current_user,
    login_local,
    now,
    profile_for_remote,
    register_local,
    require_admin,
    session_cookies,
)
from .domain import (
    apply_progress,
    assign_daily,
    choose,
    filtered,
    problem_or_404,
    progress,
    today,
    visible,
)
from .models import ProblemRecord
from .store import atomic_store, read_store, store

app = FastAPI(title="来学数学 API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def same_origin(request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in config.ORIGINS:
            return JSONResponse({"detail": "请求来源不被允许。"}, status_code=403)
        if request.url.path.startswith("/api/v1/"):
            from .rate_limit import allow

            # A shared database counter works across serverless instances.
            session = request.cookies.get("math_session", "")
            identity = (
                "auth:" + str(request.client.host)
                if "/auth/" in request.url.path
                else "write:" + (session or str(request.client.host))
            )
            if not allow(identity, 20 if "/auth/" in request.url.path else 90):
                return JSONResponse(
                    {"detail": "操作过于频繁，请稍后再试。"},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(httpx.HTTPError)
async def upstream_error(request, exc):
    logging.getLogger("mathonline").error(
        "Upstream operation failed: %s", type(exc).__name__
    )
    return JSONResponse({"detail": "云端服务暂不可用，请稍后重试。"}, status_code=503)


class AuthInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(default="", max_length=128)
    nickname: str = Field(default="同学", min_length=1, max_length=30)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("邮箱格式不正确")
        return value


class FeedbackInput(BaseModel):
    kind: Literal["incomplete", "answer_mismatch", "other"]
    detail: str = Field(default="", max_length=2000)
    problem_version: int = Field(ge=1)


class FeedbackReview(BaseModel):
    status: Literal["resolved", "dismissed"]
    review_note: str = Field(default="", max_length=1000)


class Submission(BaseModel):
    text: str = Field(default="", max_length=20000)
    image_ids: list[str] = Field(default_factory=list, max_length=5)
    options: list[str] = Field(default_factory=list, max_length=20)
    problem_version: int = Field(ge=1)


class Assessment(BaseModel):
    result: Literal["correct", "incorrect"]
    revision: int = Field(ge=1)


class Content(BaseModel):
    body: str = Field(default="", max_length=20000)
    image_ids: list[str] = Field(default_factory=list, max_length=5)
    version: int | None = None


class Decision(BaseModel):
    status: Literal["approved", "hidden"]
    reason: str = Field(default="", max_length=500)
    version: int | None = None


def asset_info(aid, repository=None):
    repository = repository or store
    asset = repository.get("media_assets", id=aid)
    if not asset:
        raise HTTPException(400, "图片不存在，请重新上传。")
    return {
        "id": aid,
        "url": f"/api/v1/media/{aid}",
        "width": asset["width"],
        "height": asset["height"],
    }


def images_info(ids, repository=None):
    return [asset_info(aid, repository) for aid in ids]


def public_problem(p, user=None, repository=None):
    repository = repository or store
    result = dict(p)
    result.pop("difficulty", None)
    result["question_images"] = images_info(p["question_images"], repository)
    state = progress(user, p["id"], repository)
    if state:
        state["outdated"] = state["problem_version"] != p["version"]
        state["can_publish"] = state["correct_version"] == p["version"]
        state["images"] = images_info(state["image_ids"], repository)
    result["progress"] = state
    result["answer_unlocked"] = bool(
        state
        or (
            user
            and repository.get("answer_access", user_id=user["id"], problem_id=p["id"])
        )
    )
    result["liked"] = bool(
        user and repository.get("likes", id=user["id"] + ":problem:" + p["id"])
    )
    result["source"] = repository.get("problem_sources", id=p["source_id"])
    return result


def validate_images(ids, user, purpose):
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "图片不可重复。")
    for aid in ids:
        asset = store.get("media_assets", id=aid)
        if not asset or asset["owner_id"] != user["id"] or asset["purpose"] != purpose:
            raise HTTPException(403, "图片不属于本次内容或当前用户。")


def filters_for(request):
    return {
        k: request.query_params[k]
        for k in ("year", "type", "tag", "q")
        if request.query_params.get(k)
    }


def notify(user_id, actor, target, event, event_id, repository=None):
    repository = repository or store
    if user_id == actor:
        return
    if not repository.get("notifications", id=event_id):
        repository.put(
            "notifications",
            {
                "id": event_id,
                "user_id": user_id,
                "actor_id": actor,
                "target_id": target,
                "event": event,
                "is_read": False,
                "created_at": now(),
            },
        )


@app.get("/api/v1/health")
def health():
    return {
        "ok": True,
        "mode": config.MODE,
        "draft_preview": config.PREVIEW,
        "date": today(),
    }


@app.get("/api/v1/catalog")
def catalog():
    rows = filtered({})
    return {
        "total": len(rows),
        "years": sorted({p["year"] for p in rows}, reverse=True),
        "tags": sorted({t for p in rows for t in p["tags"]}),
        "counts": {
            t: sum(p["type"] == t for p in rows)
            for t in ("blank", "solution", "choice")
        },
    }


@app.post("/api/v1/auth/register")
def register(body: AuthInput, response: Response):
    if len(body.password) < 8:
        raise HTTPException(400, "密码至少 8 位。")
    if config.MODE == "local":
        register_local(body.email, body.password, body.nickname.strip() or "同学")
        return {
            "user": login_local(body.email, body.password, response),
            "verification_required": False,
        }
    data = auth_request(
        "POST",
        "signup",
        json={
            "email": body.email,
            "password": body.password,
            "data": {"nickname": body.nickname},
        },
    )
    if data.get("access_token"):
        session_cookies(response, data["access_token"], data["refresh_token"])
        return {
            "user": profile_for_remote(data["user"]),
            "verification_required": False,
        }
    return {"user": None, "verification_required": True}


@app.post("/api/v1/auth/login")
def login(body: AuthInput, response: Response):
    if config.MODE == "local":
        return {"user": login_local(body.email, body.password, response)}
    data = auth_request(
        "POST",
        "token?grant_type=password",
        json={"email": body.email, "password": body.password},
    )
    session_cookies(response, data["access_token"], data["refresh_token"])
    return {"user": profile_for_remote(data["user"])}


@app.post("/api/v1/auth/refresh")
def refresh(request: Request, response: Response):
    token = request.cookies.get("math_refresh")
    if config.MODE != "supabase" or not token:
        raise HTTPException(401, "请重新登录。")
    data = auth_request(
        "POST", "token?grant_type=refresh_token", json={"refresh_token": token}
    )
    session_cookies(response, data["access_token"], data["refresh_token"])
    return {"user": profile_for_remote(data["user"])}


@app.post("/api/v1/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("math_session")
    if token:
        if config.MODE == "local":
            store.delete(
                "local_sessions", token_hash=hashlib.sha256(token.encode()).hexdigest()
            )
        else:
            try:
                auth_request(
                    "POST", "logout", headers={"Authorization": "Bearer " + token}
                )
            except HTTPException:
                pass
    response.delete_cookie("math_session", path="/")
    response.delete_cookie("math_refresh", path="/api/v1/auth")
    return {"ok": True}


@app.post("/api/v1/auth/reset")
def reset(body: AuthInput):
    if config.MODE == "local":
        raise HTTPException(
            400, "本地试用未接入邮件服务，接入 Supabase 后可通过邮箱重置密码。"
        )
    auth_request(
        "POST",
        "recover",
        json={
            "email": body.email,
            "redirect_to": config.ORIGINS[0] + "/auth?mode=recovery",
        },
    )
    return {"ok": True}


@app.post("/api/v1/auth/session")
def callback(body: dict, response: Response):
    if config.MODE != "supabase":
        raise HTTPException(400, "当前未启用云端认证。")
    token = str(body.get("access_token", ""))
    refresh = str(body.get("refresh_token", ""))
    if not token or not refresh:
        raise HTTPException(400, "认证链接不完整。")
    user = auth_request("GET", "user", headers={"Authorization": "Bearer " + token})
    session_cookies(response, token, refresh)
    return {"user": profile_for_remote(user)}


@app.patch("/api/v1/auth/password")
def change_password(body: dict, request: Request):
    current_user(request)
    password = str(body.get("password", ""))
    if not 8 <= len(password) <= 128:
        raise HTTPException(400, "密码需要 8 至 128 位。")
    if config.MODE != "supabase":
        raise HTTPException(400, "本地模式不支持邮件重置。")
    auth_request(
        "PUT",
        "user",
        headers={"Authorization": "Bearer " + request.cookies.get("math_session", "")},
        json={"password": password},
    )
    return {"ok": True}


@app.get("/api/v1/me")
def me(request: Request):
    user = current_user(request, False)
    if not user:
        return {"user": None}
    states = store.list("user_problem_progress", user_id=user["id"])
    days = store.list("daily_assignments", user_id=user["id"])
    return {
        "user": user,
        "stats": {
            "practiced": len(states),
            "correct": sum(p["ever_correct"] for p in states),
            "mistakes": sum(p["ever_incorrect"] for p in states),
            "checkins": sum(bool(d["completed_at"]) for d in days),
        },
        "daily": next((d for d in days if d["local_date"] == today()), None),
    }


@app.patch("/api/v1/me/profile")
def edit_profile(body: dict, request: Request):
    user = current_user(request)
    nickname = str(body.get("nickname", "")).strip()
    if not nickname or len(nickname) > 30:
        raise HTTPException(400, "昵称需要 1 至 30 个字符。")
    user["nickname"] = nickname
    store.put("profiles", user)
    return user


@app.get("/api/v1/problems")
def problems(request: Request, page: int = 1, page_size: int = 12):
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(400, "分页参数无效。")
    user = current_user(request, False)
    with read_store(store) as reader:
        rows = filtered(filters_for(request), reader)
        return {
            "total": len(rows),
            "items": [
                public_problem(p, user, reader)
                for p in rows[(page - 1) * page_size : page * page_size]
            ],
        }


@app.get("/api/v1/problems/{pid}")
def get_problem(pid: str, request: Request):
    return public_problem(problem_or_404(pid), current_user(request, False))


def can_read_answer(user, pid):
    return bool(
        user
        and (
            progress(user, pid)
            or store.get("answer_access", user_id=user["id"], problem_id=pid)
        )
    )


@app.get("/api/v1/problems/{pid}/answer")
def answer(pid: str, request: Request):
    problem_or_404(pid)
    user = current_user(request)
    if not can_read_answer(user, pid):
        raise HTTPException(403, "请先提交作答或点击“不会”，再查看参考答案。")
    a = store.get("problem_answers", problem_id=pid)
    if not a:
        raise HTTPException(404, "参考答案尚未整理。")
    return {**a, "images": images_info(a["images"])}


class GiveUpInput(BaseModel):
    problem_version: int = Field(ge=1)


@app.post("/api/v1/me/problems/{pid}/give-up")
def give_up(pid: str, body: GiveUpInput, request: Request):
    user = current_user(request)
    p = problem_or_404(pid)
    if body.problem_version != p["version"]:
        raise HTTPException(409, "题目已修改，请刷新后重试。")
    # Answer access is separate from a submission, grading and daily check-in.
    store.put(
        "answer_access",
        {
            "id": user["id"] + ":" + pid,
            "user_id": user["id"],
            "problem_id": pid,
            "created_at": now(),
        },
    )
    return public_problem(p, user)


@app.get("/api/v1/practice/random")
def random_problem(
    request: Request, exclude: str | None = None, resume: str | None = None
):
    return public_problem(
        choose(filters_for(request), exclude, resume), current_user(request, False)
    )


@app.get("/api/v1/practice/daily")
def daily_status(request: Request):
    user = current_user(request)
    return {
        "assignment": store.get(
            "daily_assignments", user_id=user["id"], local_date=today()
        ),
    }


@app.post("/api/v1/practice/daily")
def daily(request: Request, body: dict):
    user = current_user(request)
    assignment = assign_daily(
        user,
        {k: v for k, v in body.items() if k in ("year", "type", "tag", "q")},
    )
    p = problem_or_404(assignment["problem_id"])
    return {"assignment": assignment, "problem": public_problem(p, user)}


@app.put("/api/v1/me/problems/{pid}/submission")
def submit(pid: str, body: Submission, request: Request):
    user = current_user(request)
    p = problem_or_404(pid)
    if body.problem_version != p["version"]:
        raise HTTPException(409, "题目已修改，请刷新后再提交。")
    validate_images(body.image_ids, user, "submission")
    if p["grading_mode"] == "choice":
        options = set(body.options)
        if (
            not options
            or not options <= set(p["choice_options"])
            or (not p["multiple"] and len(options) != 1)
        ):
            raise HTTPException(400, "请选择有效选项。")
        a = store.get("problem_answers", problem_id=pid)
        result = "correct" if options == set(a["correct_options"]) else "incorrect"
        text = ", ".join(sorted(options))
    else:
        text = body.text.strip()
        if not text and not body.image_ids:
            raise HTTPException(400, "请填写作答或上传作答图片。")
        result = "pending"
    apply_progress(user, p, text, body.image_ids, result)
    return public_problem(p, user)


@app.patch("/api/v1/me/problems/{pid}/self-assessment")
def assess(pid: str, body: Assessment, request: Request):
    user = current_user(request)
    p = problem_or_404(pid)
    if p["grading_mode"] != "self_assessment":
        raise HTTPException(400, "选择题不接受自评。")
    apply_progress(user, p, "", [], body.result, body.revision)
    return public_problem(p, user)


@app.get("/api/v1/me/history")
def history(request: Request, scope: str = "all", page: int = 1):
    user = current_user(request)
    states = store.list("user_problem_progress", user_id=user["id"])
    if scope == "mistakes":
        states = [p for p in states if p["ever_incorrect"]]
    if scope == "recent_mistakes":
        states = [p for p in states if p["result"] == "incorrect"]
    states.sort(key=lambda p: p["last_at"], reverse=True)
    rows = [store.get("problems", id=p["problem_id"]) for p in states]
    rows = [p for p in rows if p and visible(p)]
    return {
        "total": len(rows),
        "items": [
            public_problem(p, user) for p in rows[max(0, page - 1) * 12 : page * 12]
        ],
    }


@app.get("/api/v1/me/likes")
def my_likes(request: Request):
    user = current_user(request)
    likes = store.list("likes", user_id=user["id"])
    ps = []
    ss = []
    for like in likes:
        if like["kind"] == "problem":
            p = store.get("problems", id=like["target_id"])
            if p and visible(p):
                ps.append(public_problem(p, user))
        else:
            s = store.get("solutions", id=like["target_id"])
            if s and s["status"] == "approved":
                ss.append(decorate_content(s, user))
    return {"problems": ps, "solutions": ss}


@app.get("/api/v1/me/solutions")
def my_solutions(request: Request):
    user = current_user(request)
    return [
        decorate_content(s, user) for s in store.list("solutions", user_id=user["id"])
    ]


def decorate_content(s, user=None):
    author = store.get("profiles", id=s["user_id"])
    r = {**s, "author": author["nickname"] if author else "同学"}
    if "image_ids" in s:
        r["images"] = images_info(s["image_ids"])
    r["likes"] = len(store.list("likes", kind="solution", target_id=s["id"]))
    r["liked"] = bool(
        user and store.get("likes", id=user["id"] + ":solution:" + s["id"])
    )
    return r


@app.get("/api/v1/problems/{pid}/solutions")
def solutions(pid: str, request: Request):
    problem_or_404(pid)
    user = current_user(request, False)
    rows = store.list("solutions", problem_id=pid)
    return [
        decorate_content(s, user)
        for s in rows
        if s["status"] == "approved" or (user and s["user_id"] == user["id"])
    ]


@app.post("/api/v1/problems/{pid}/solutions")
def publish_solution(pid: str, body: Content, request: Request):
    user = current_user(request)
    p = problem_or_404(pid)
    state = progress(user, pid)
    if not state or state["correct_version"] != p["version"]:
        raise HTTPException(403, "先完成本版本的题目并标记正确，再分享题解。")
    validate_images(body.image_ids, user, "solution")
    if not body.body.strip() and not body.image_ids:
        raise HTTPException(400, "请填写题解或上传图片。")
    sid = user["id"] + ":" + pid
    with atomic_store(store) as tx:
        latest = tx.get("problems", id=pid)
        state = tx.get("user_problem_progress", user_id=user["id"], problem_id=pid)
        if not state or state["correct_version"] != latest["version"]:
            raise HTTPException(409, "题目已修改，请重新核对后发布。")
        old = tx.get("solutions", id=sid)
        if old and body.version != old["version"]:
            raise HTTPException(409, "题解已修改，请刷新后编辑。")
        row = {
            "id": sid,
            "user_id": user["id"],
            "problem_id": pid,
            "body": body.body.strip(),
            "image_ids": body.image_ids,
            "status": "pending",
            "version": old["version"] + 1 if old else 1,
            "created_at": old["created_at"] if old else now(),
            "updated_at": now(),
        }
        tx.put("solutions", row)
    return decorate_content(row, user)


@app.delete("/api/v1/solutions/{sid}")
def remove_solution(sid: str, request: Request):
    user = current_user(request)
    s = store.get("solutions", id=sid)
    if not s or s["user_id"] != user["id"]:
        raise HTTPException(403, "仅作者可以隐藏题解。")
    with atomic_store(store) as tx:
        s = tx.get("solutions", id=sid)
        s["status"] = "hidden"
        s["version"] += 1
        s["updated_at"] = now()
        tx.put("solutions", s)
    return {"ok": True}


@app.get("/api/v1/solutions/{sid}/comments")
def comments(sid: str, request: Request):
    s = store.get("solutions", id=sid)
    user = current_user(request, False)
    if not s or s["status"] != "approved":
        raise HTTPException(404, "题解不可见。")
    problem_or_404(s["problem_id"])
    return [
        decorate_content(c, user)
        for c in store.list("comments", solution_id=sid)
        if c["status"] == "approved" or (user and c["user_id"] == user["id"])
    ]


@app.post("/api/v1/solutions/{sid}/comments")
def add_comment(sid: str, body: Content, request: Request):
    user = current_user(request)
    s = store.get("solutions", id=sid)
    if not s or s["status"] != "approved":
        raise HTTPException(404, "题解不可见。")
    problem_or_404(s["problem_id"])
    if not body.body.strip() or len(body.body) > 2000:
        raise HTTPException(400, "评论需要 1 至 2000 个字符。")
    c = {
        "id": str(uuid4()),
        "solution_id": sid,
        "user_id": user["id"],
        "body": body.body.strip(),
        "status": "pending",
        "created_at": now(),
    }
    store.put("comments", c)
    return decorate_content(c, user)


@app.api_route("/api/v1/{kind}/{target}/like", methods=["PUT", "DELETE"])
def like(kind: str, target: str, request: Request):
    user = current_user(request)
    if kind == "problems":
        problem_or_404(target)
        category = "problem"
        owner = None
    elif kind == "solutions":
        s = store.get("solutions", id=target)
        if not s or s["status"] != "approved":
            raise HTTPException(404, "题解不可见。")
        problem_or_404(s["problem_id"])
        category = "solution"
        owner = s["user_id"]
    else:
        raise HTTPException(404, "目标不存在。")
    lid = user["id"] + ":" + category + ":" + target
    with atomic_store(store) as tx:
        if request.method == "DELETE":
            tx.delete("likes", id=lid)
        else:
            old = tx.get("likes", id=lid)
            tx.put(
                "likes",
                {
                    "id": lid,
                    "user_id": user["id"],
                    "target_id": target,
                    "kind": category,
                    "created_at": old["created_at"] if old else now(),
                },
            )
            if owner:
                notify(owner, user["id"], target, "like", "like:" + lid, tx)
    return {"liked": request.method == "PUT"}


@app.get("/api/v1/me/notifications")
def notifications(request: Request):
    user = current_user(request)
    rows = store.list("notifications", user_id=user["id"])
    return sorted(rows, key=lambda n: n["created_at"], reverse=True)[:100]


@app.patch("/api/v1/me/notifications/{nid}")
def read_notification(nid: str, request: Request):
    user = current_user(request)
    n = store.get("notifications", id=nid, user_id=user["id"])
    if not n:
        raise HTTPException(404, "通知不存在。")
    n["is_read"] = True
    store.put("notifications", n)
    return n


@app.post("/api/v1/uploads")
async def upload(
    request: Request, file: UploadFile = File(...), purpose: str = Form("submission")
):
    user = current_user(request)
    if purpose not in ("submission", "solution", "question", "answer"):
        raise HTTPException(400, "图片用途无效。")
    if purpose in ("question", "answer"):
        require_admin(request)
    if len(store.list("media_assets", owner_id=user["id"])) >= 200:
        raise HTTPException(400, "已达到试用上传配额，请联系管理员清理未使用图片。")
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "单张图片不得超过 5 MB。")
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if (
                im.format not in ("JPEG", "PNG", "WEBP")
                or im.width * im.height > 25_000_000
            ):
                raise ValueError()
            im.load()
            width, height = im.size
            out = io.BytesIO()
            im.convert("RGB").save(out, format="WEBP", quality=92)
            raw = out.getvalue()
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        raise HTTPException(
            400, "请选择有效的 JPEG、PNG 或 WebP 图片（最多 2500 万像素）。"
        )
    aid = str(uuid4())
    path = "uploads/" + user["id"] + "/" + aid + ".webp"
    if config.MODE == "local":
        destination = config.ROOT / "data" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    else:
        store.request(
            "POST",
            f"/storage/v1/object/{config.BUCKET}/{path}",
            headers={"Content-Type": "image/webp"},
            content=raw,
        )
    store.put(
        "media_assets",
        {
            "id": aid,
            "owner_id": user["id"],
            "purpose": purpose,
            "path": path,
            "content_type": "image/webp",
            "width": width,
            "height": height,
            "problem_id": None,
            "source_path": "",
            "created_at": now(),
        },
    )
    return asset_info(aid)


@app.get("/api/v1/media/{aid}")
def media(aid: str, request: Request):
    a = store.get("media_assets", id=aid)
    user = current_user(request, False)
    if not a:
        raise HTTPException(404, "图片不存在。")
    allowed = bool(user and a["owner_id"] == user["id"] and a["purpose"] != "answer")
    if a["purpose"] in ("question", "answer"):
        candidates = (
            store.list("problems")
            if a["purpose"] == "question"
            else store.list("problem_answers")
        )
        for row in candidates:
            if aid in row.get("question_images", row.get("images", [])):
                p = (
                    row
                    if "question_images" in row
                    else store.get("problems", id=row["problem_id"])
                )
                if (
                    p
                    and visible(p)
                    and (a["purpose"] == "question" or can_read_answer(user, p["id"]))
                ):
                    allowed = True
                    break
        if user and user["role"] == "admin":
            allowed = True
    elif a["purpose"] == "solution":
        allowed = allowed or bool(user and user["role"] in ("admin", "moderator"))
        for s in store.list("solutions", status="approved"):
            if aid in s["image_ids"] and visible(
                store.get("problems", id=s["problem_id"])
            ):
                allowed = True
                break
    if not allowed:
        raise HTTPException(403, "图片仅对本人或符合可见性规则的内容开放。")
    if config.MODE == "supabase":
        data = store.request(
            "POST",
            f"/storage/v1/object/sign/{config.BUCKET}/{a['path']}",
            json={"expiresIn": 300},
        )
        return RedirectResponse(config.SB_URL + "/storage/v1" + data["signedURL"])
    path = (
        config.ROOT
        / "data"
        / (a["path"] if a["path"].startswith("uploads/") else "assets/" + a["path"])
    )
    if not path.is_file():
        raise HTTPException(404, "图片文件缺失，请联系管理员。")
    return FileResponse(
        path,
        media_type=a["content_type"],
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/api/v1/admin/problems")
def admin_problems(request: Request):
    require_admin(request)
    with read_store(store) as reader:
        result = []
        for p in reader.list("problems"):
            answer = reader.get("problem_answers", problem_id=p["id"])
            result.append(
                {
                    **p,
                    "question_images": images_info(p["question_images"], reader),
                    "answer": {
                        **answer,
                        "images": images_info(answer["images"], reader),
                    },
                    "source": reader.get("problem_sources", id=p["source_id"]),
                }
            )
        return result


@app.post("/api/v1/admin/problems")
def create_problem(body: dict, request: Request):
    require_admin(request)
    source = body.get("source", {})
    raw = {k: v for k, v in body.items() if k != "source"}
    try:
        p = ProblemRecord.model_validate(raw).model_dump()
    except ValidationError:
        raise HTTPException(400, "请填写有效的稳定 ID、来源和题目信息。")
    if store.get("problems", id=p["id"]):
        raise HTTPException(409, "稳定 ID 已存在，请编辑原题或使用新的 ID。")
    for role, ids in [
        ("question", p["question_images"]),
        ("answer", p["answer"]["images"]),
    ]:
        for aid in ids:
            asset = store.get("media_assets", id=aid)
            if not asset or asset["purpose"] != role:
                raise HTTPException(400, "题干或答案图片用途不匹配。")
    existing_source = store.get("problem_sources", id=p["source_id"])
    if not existing_source:
        if (
            source.get("id") != p["source_id"]
            or not str(source.get("name", "")).strip()
        ):
            raise HTTPException(400, "请填写来源名称与来源 ID。")
        source = {
            k: source.get(k, "")
            for k in (
                "id",
                "name",
                "year",
                "group_name",
                "source_url",
                "rights_note",
                "verified",
            )
        }
        source["year"] = p["year"]
        source["group_name"] = p["group_name"]
        source["verified"] = bool(source["verified"])
    else:
        source = existing_source
    p["manual_revision"] = True
    from .importer import import_manifest

    try:
        result = import_manifest(
            {"schema_version": 1, "sources": [source], "assets": [], "problems": [p]}
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@app.put("/api/v1/admin/problems/{pid}")
def edit_problem(pid: str, body: dict, request: Request):
    user = require_admin(request)
    p = problem_or_404(pid, True)
    a = store.get("problem_answers", problem_id=pid)
    if body.get("version") != p["version"]:
        raise HTTPException(409, "题目被其他页面修改，请刷新后重试。")
    allowed = {
        "title",
        "subject",
        "type",
        "group_name",
        "year",
        "tags",
        "status",
        "verified",
        "answer_verified",
        "choice_options",
        "multiple",
        "question_images",
    }
    for k in allowed:
        if k in body:
            p[k] = body[k]
    for k in ("images", "provenance", "correct_options"):
        if k in body.get("answer", {}):
            a[k] = body["answer"][k]
    if p["type"] not in ("blank", "solution", "choice") or p["status"] not in (
        "draft",
        "published",
        "archived",
    ):
        raise HTTPException(400, "题型或状态无效。")
    if (
        not isinstance(p["title"], str)
        or not p["title"].strip()
        or len(p["title"]) > 200
    ):
        raise HTTPException(400, "标题需要 1 至 200 个字符。")
    p["difficulty"] = None
    if not isinstance(p["tags"], list) or any(
        not isinstance(t, str) or len(t) > 60 for t in p["tags"]
    ):
        raise HTTPException(400, "标签格式无效。")
    if not p["question_images"]:
        raise HTTPException(400, "至少保留一张题干图片。")
    for role, ids in [("question", p["question_images"]), ("answer", a["images"])]:
        if not isinstance(ids, list) or len(ids) > 20 or len(set(ids)) != len(ids):
            raise HTTPException(400, "图片列表无效。")
        for aid in ids:
            asset = store.get("media_assets", id=aid)
            if not asset or asset["purpose"] != role:
                raise HTTPException(400, "图片用途不匹配。")
    p["grading_mode"] = "choice" if p["type"] == "choice" else "self_assessment"
    if p["type"] == "choice" and (
        not p["choice_options"]
        or not a["correct_options"]
        or not set(a["correct_options"]) <= set(p["choice_options"])
        or (not p["multiple"] and len(a["correct_options"]) != 1)
    ):
        raise HTTPException(400, "请配置有效选择题选项和答案。")
    try:
        ProblemRecord.model_validate(
            {**p, "answer": {k: v for k, v in a.items() if k != "problem_id"}}
        )
    except ValidationError:
        raise HTTPException(400, "题目元数据格式无效，请检查年份、标签与选项配置。")
    if p["status"] == "published" and not a["images"]:
        raise HTTPException(400, "请添加参考答案图片后发布。")
    expected = p["version"]
    p["version"] += 1
    p["manual_revision"] = True
    p["updated_at"] = now()
    if hasattr(store, "rpc"):
        store.rpc(
            "save_problem", {"p_problem": p, "p_answer": a, "p_expected": expected}
        )
    else:
        with store.transaction() as tx:
            if tx.get("problems", id=pid)["version"] != expected:
                raise HTTPException(409, "版本冲突，请刷新。")
            tx.put("problems", p)
            tx.put("problem_answers", a)
    store.put(
        "moderation_logs",
        {
            "id": str(uuid4()),
            "actor_id": user["id"],
            "target_id": pid,
            "kind": "problem",
            "action": "edit",
            "reason": "题库维护",
            "created_at": now(),
        },
    )
    return {"ok": True, "version": p["version"]}


@app.patch("/api/v1/admin/sources/{sid}")
def edit_source(sid: str, body: dict, request: Request):
    require_admin(request)
    s = store.get("problem_sources", id=sid)
    if not s:
        raise HTTPException(404, "来源不存在。")
    for k in ("source_url", "rights_note", "verified"):
        if k in body:
            s[k] = body[k]
    if s["verified"] and not s["rights_note"].strip():
        raise HTTPException(400, "请填写使用依据。")
    store.put("problem_sources", s)
    return s


@app.post("/api/v1/admin/import")
def admin_import(body: dict, request: Request):
    require_admin(request)
    from .importer import import_manifest

    try:
        return import_manifest(body)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/v1/admin/export")
def admin_export(request: Request):
    require_admin(request)
    return {
        "schema_version": 1,
        "sources": store.list("problem_sources"),
        "assets": [],
        "problems": [
            {
                **p,
                "answer": {
                    k: v
                    for k, v in store.get("problem_answers", problem_id=p["id"]).items()
                    if k != "problem_id"
                },
            }
            for p in store.list("problems")
        ],
    }


@app.get("/api/v1/admin/moderation")
def moderation(request: Request):
    require_admin(request, True)
    return {
        "solutions": [
            decorate_content(s) for s in store.list("solutions", status="pending")
        ],
        "comments": [
            decorate_content(c) for c in store.list("comments", status="pending")
        ],
    }


@app.patch("/api/v1/admin/moderation/{kind}/{cid}")
def moderate(kind: str, cid: str, body: Decision, request: Request):
    user = require_admin(request, True)
    if kind not in ("solutions", "comments"):
        raise HTTPException(404, "内容类型不存在。")
    with atomic_store(store) as tx:
        row = tx.get(kind, id=cid)
        if not row:
            raise HTTPException(404, "内容不存在。")
        if kind == "solutions" and body.version != row["version"]:
            raise HTTPException(409, "题解已修改，请刷新后审核。")
        row["status"] = body.status
        tx.put(kind, row)
        tx.put(
            "moderation_logs",
            {
                "id": str(uuid4()),
                "actor_id": user["id"],
                "target_id": cid,
                "kind": kind,
                "action": body.status,
                "reason": body.reason,
                "created_at": now(),
            },
        )
        if kind == "comments" and body.status == "approved":
            s = tx.get("solutions", id=row["solution_id"])
            notify(
                s["user_id"], row["user_id"], s["id"], "comment", "comment:" + cid, tx
            )
    return {"ok": True}


@app.get("/api/v1/admin/users")
def admin_users(request: Request):
    require_admin(request)
    return store.list("profiles")


@app.patch("/api/v1/admin/users/{uid}")
def role(uid: str, body: dict, request: Request):
    require_admin(request)
    user = store.get("profiles", id=uid)
    if not user or body.get("role") not in ("user", "moderator", "admin"):
        raise HTTPException(400, "用户或角色无效。")
    user["role"] = body["role"]
    store.put("profiles", user)
    return user


@app.post("/api/v1/problems/{pid}/feedback")
def submit_feedback(pid: str, body: FeedbackInput, request: Request):
    user = current_user(request)
    problem = problem_or_404(pid)
    if body.problem_version > problem["version"]:
        raise HTTPException(409, "题目版本无效，请刷新后重试。")
    detail = body.detail.strip()
    if body.kind == "other" and not detail:
        raise HTTPException(400, "请填写问题说明。")
    row = {
        "id": str(uuid4()),
        "user_id": user["id"],
        "problem_id": pid,
        "problem_version": body.problem_version,
        "kind": body.kind,
        "detail": detail,
        "status": "pending",
        "review_note": "",
        "reviewed_by": None,
        "created_at": now(),
        "reviewed_at": None,
    }
    store.put("problem_feedback", row)
    return {"id": row["id"], "status": row["status"]}


@app.get("/api/v1/admin/feedback")
def admin_feedback(request: Request):
    require_admin(request)
    with read_store(store) as reader:
        result = []
        for row in reader.list("problem_feedback"):
            p = reader.get("problems", id=row["problem_id"])
            reporter = reader.get("profiles", id=row["user_id"])
            reviewer = (
                reader.get("profiles", id=row["reviewed_by"])
                if row["reviewed_by"]
                else None
            )
            result.append(
                {
                    **row,
                    "problem_title": p["title"],
                    "current_version": p["version"],
                    "reporter": reporter["nickname"],
                    "reviewer": reviewer["nickname"] if reviewer else None,
                }
            )
        return sorted(result, key=lambda r: r["created_at"], reverse=True)


@app.patch("/api/v1/admin/feedback/{fid}")
def review_feedback(fid: str, body: FeedbackReview, request: Request):
    user = require_admin(request)
    if hasattr(store, "rpc"):
        try:
            return store.rpc(
                "review_feedback",
                {
                    "p_id": fid,
                    "p_actor": user["id"],
                    "p_status": body.status,
                    "p_note": body.review_note.strip(),
                },
            )
        except httpx.HTTPStatusError as e:
            if "stale feedback" in e.response.text:
                raise HTTPException(409, "反馈已处理，请刷新列表。") from e
            raise
    with atomic_store(store) as tx:
        row = tx.get("problem_feedback", id=fid)
        if not row:
            raise HTTPException(404, "反馈不存在。")
        if row["status"] != "pending":
            raise HTTPException(409, "反馈已处理，请刷新列表。")
        row.update(
            status=body.status,
            review_note=body.review_note.strip(),
            reviewed_by=user["id"],
            reviewed_at=now(),
        )
        tx.put("problem_feedback", row)
        tx.put(
            "moderation_logs",
            {
                "id": str(uuid4()),
                "actor_id": user["id"],
                "target_id": fid,
                "kind": "problem_feedback",
                "action": body.status,
                "reason": row["review_note"],
                "created_at": row["reviewed_at"],
            },
        )
    return {"ok": True}


# Built SPA is served by FastAPI for one-command local use. API routes stay first.
dist = config.ROOT / "frontend/dist"
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在。")
        if path in {"favicon.svg", "favicon.ico", "apple-touch-icon.png"}:
            return FileResponse(dist / path)
        return FileResponse(dist / "index.html")
