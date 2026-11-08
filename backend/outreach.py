"""Announcements, account invitations and gated resource downloads."""

import re
import secrets
import sqlite3
from uuid import uuid4

import httpx
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from . import config


def available(operation):
    """Give the feature a useful error before its cloud migration is installed."""
    try:
        return operation()
    except httpx.HTTPStatusError as exc:
        try:
            code = exc.response.json().get("code")
        except (ValueError, AttributeError):
            code = None
        if code in {"PGRST202", "PGRST205", "42P01", "42703"}:
            raise HTTPException(503, "此功能需要先执行公告与邀请数据库迁移。") from exc
        raise


def resolve_inviter(repository, code):
    code = code.strip().upper()
    if not code:
        return None
    if not re.fullmatch(r"[A-Z0-9]{10}", code):
        raise HTTPException(400, "邀请码无效，请核对后重试。")
    row = available(lambda: repository.get("invite_codes", code=code))
    if not row:
        raise HTTPException(400, "邀请码无效，请核对后重试。")
    return row["id"]


def invite_code(repository, user_id, timestamp):
    for _ in range(3):
        try:
            return available(
                lambda: repository.ensure_invite_code(
                    user_id, secrets.token_hex(5).upper(), timestamp
                )
            )["code"]
        except sqlite3.IntegrityError:
            continue
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
    raise HTTPException(503, "邀请码暂时无法生成，请重试。")


def save_announcement(repository, user, body, timestamp, aid=None):
    aid = aid or str(uuid4())
    old = available(lambda: repository.get("announcements", id=aid))
    if body.revision is not None and (old is None or old["revision"] != body.revision):
        raise HTTPException(409, "公告已更新，请刷新后再编辑。")
    item = {
        "id": aid,
        "author_id": user["id"],
        "title": body.title.strip(),
        "body": body.body.strip(),
        "published": body.published,
        "revision": (old["revision"] if old else 0) + 1,
        "created_at": old["created_at"] if old else timestamp,
        "updated_at": timestamp,
    }
    if not item["title"] or not item["body"]:
        raise HTTPException(400, "请填写标题和更新内容。")
    row = available(lambda: repository.save_announcement(item, item["revision"] - 1))
    if row is None:
        raise HTTPException(409, "公告已更新，请刷新后再编辑。")
    return row


def download_resource(repository, user):
    status = available(lambda: repository.invite_status(user["id"]))
    if status["confirmed"] < 2:
        raise HTTPException(403, "邀请 2 名用户后解锁下载。")
    if not config.RESOURCE_DOWNLOAD_URL.startswith("https://"):
        raise HTTPException(503, "资料下载地址尚未配置，请联系管理员。")
    result = RedirectResponse(config.RESOURCE_DOWNLOAD_URL, status_code=303)
    result.headers["Cache-Control"] = "private, no-store"
    result.headers["Referrer-Policy"] = "no-referrer"
    return result
