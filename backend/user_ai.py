"""用户个人模型配置与 API 密钥的加密存储。

安全约定：

- 密钥只以 AES-GCM 密文形式保存；`AI_KEY_SECRET` 是唯一的解密密钥，缺失时
  保存与读取都明确失败，绝不退化为明文。
- 加密时把用户 ID 作为附加认证数据（AAD），数据库里两行互换也无法解密。
- 对外只返回末四位掩码；接口、日志和异常都不回显完整密钥。
- 个人配置只支持固定的 OpenAI / DeepSeek 官方端点，不接受任意 URL。
"""

import base64
import json
import os
import re
from datetime import UTC, datetime

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config
from .store import store

TABLE = "user_ai_keys"
PREFIX = "v1:"
MAX_LENGTH = 512
_KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9_-]{10,%d}$" % (MAX_LENGTH - 3))
PROVIDERS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
}


def normalize_settings(provider, model, vision_model=""):
    if provider not in PROVIDERS:
        raise ValueError("请选择 OpenAI 或 DeepSeek。")
    values = []
    for value in (model, vision_model):
        value = (value or "").strip()
        if value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", value):
            raise ValueError("模型名称格式不正确。")
        values.append(value)
    if not values[0]:
        raise ValueError("请填写文字模型名称。")
    return {"provider": provider, "model": values[0], "vision_model": values[1]}


class KeyUnavailable(Exception):
    """服务端未配置加密密钥，或密文无法解密。"""


def now():
    return datetime.now(UTC).isoformat()


def normalize(api_key):
    """校验并返回去空白后的密钥；格式不对抛 ValueError，不回显原值。"""
    value = (api_key or "").strip()
    if not value:
        raise ValueError("请填写 API 密钥。")
    if len(value) > MAX_LENGTH or not _KEY_PATTERN.fullmatch(value):
        raise ValueError("API 密钥格式不正确，应以 sk- 开头，只包含字母、数字、- 或 _。")
    return value


def mask(value):
    """只保留末四位，用于页面展示。"""
    return "••••••••" + value[-4:] if len(value) >= 4 else "••••••••"


def _secret():
    raw = (config.AI_KEY_SECRET or "").strip()
    if not raw:
        raise KeyUnavailable(
            "服务端未配置密钥加密密钥（AI_KEY_SECRET），暂时不能保存 API 密钥。"
        )
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception as exc:
        raise KeyUnavailable("服务端密钥加密密钥格式错误，请联系管理员。") from exc
    if len(key) != 32:
        raise KeyUnavailable("服务端密钥加密密钥长度应为 32 字节，请联系管理员。")
    return key


def ensure_ready():
    """保存前检查服务端加密密钥是否可用。"""
    _secret()


def encrypt(user_id, api_key):
    nonce = os.urandom(12)
    sealed = AESGCM(_secret()).encrypt(
        nonce, api_key.encode("utf-8"), user_id.encode("utf-8")
    )
    return PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def decrypt(user_id, ciphertext):
    if not isinstance(ciphertext, str) or not ciphertext.startswith(PREFIX):
        raise KeyUnavailable("API 密钥数据格式不支持，请重新保存。")
    try:
        raw = base64.urlsafe_b64decode(ciphertext[len(PREFIX) :])
        nonce, sealed = raw[:12], raw[12:]
        value = AESGCM(_secret()).decrypt(nonce, sealed, user_id.encode("utf-8"))
        return value.decode("utf-8")
    except (ValueError, InvalidTag, UnicodeDecodeError) as exc:
        raise KeyUnavailable("API 密钥无法解密，请重新保存。") from exc


def status(user_id):
    """只返回安全的配置摘要，不返回密钥或密文。"""
    row = store.get(TABLE, user_id=user_id)
    try:
        _secret()
        encryption_ready = True
    except KeyUnavailable as exc:
        encryption_ready = False
        reason = str(exc)
    else:
        reason = ""
    settings = {}
    if row and encryption_ready:
        try:
            settings = load_settings(user_id) or {}
        except KeyUnavailable as exc:
            reason = str(exc)
    return {
        "configured": bool(row),
        "hint": (row or {}).get("hint") or "",
        "updated_at": (row or {}).get("updated_at") or "",
        "encryption_ready": encryption_ready,
        "reason": reason,
        "provider": settings.get("provider", ""),
        "model": settings.get("model", ""),
        "vision_model": settings.get("vision_model", ""),
    }


def save(user_id, api_key, settings=None):
    """校验、加密并保存；返回与 status 相同结构。"""
    value = normalize(api_key)
    payload = json.dumps({**settings, "api_key": value}) if settings else value
    ciphertext = encrypt(user_id, payload)
    existing = store.get(TABLE, user_id=user_id)
    stamp = now()
    store.put(
        TABLE,
        {
            "user_id": user_id,
            "ciphertext": ciphertext,
            "hint": mask(value),
            "created_at": (existing or {}).get("created_at") or stamp,
            "updated_at": stamp,
        },
    )
    return status(user_id)


def load_settings(user_id):
    """解密请求级配置；未配置返回 None；异常抛 KeyUnavailable。"""
    try:
        row = store.get(TABLE, user_id=user_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 404):
            raise KeyUnavailable("AI 密钥功能待管理员完成数据库更新。") from exc
        raise
    if not row:
        return None
    value = decrypt(user_id, row.get("ciphertext"))
    if value.startswith("{"):
        try:
            saved = json.loads(value)
            settings = normalize_settings(
                saved.get("provider"), saved.get("model"), saved.get("vision_model")
            )
            return {**settings, "api_key": normalize(saved.get("api_key"))}
        except (ValueError, TypeError, AttributeError) as exc:
            raise KeyUnavailable("AI 配置无法读取，请重新保存。") from exc
    # Previously saved keys keep their original server endpoint and model.
    return {"api_key": value, "provider": "", "model": "", "vision_model": ""}


def load_key(user_id):
    settings = load_settings(user_id)
    return settings["api_key"] if settings else None


def remove(user_id):
    store.delete(TABLE, user_id=user_id)


def verify(api_key, settings=None):
    """用 /models 轻量校验密钥；无效抛 ValueError，服务异常抛 KeyUnavailable。"""
    base = PROVIDERS[settings["provider"]] if settings else config.AI_BASE_URL
    if not base:
        raise KeyUnavailable("服务端未配置 AI 接口地址（AI_BASE_URL）。")
    try:
        response = httpx.get(
            base + "/models",
            headers={"Authorization": "Bearer " + api_key},
            timeout=min(15.0, config.AI_TIMEOUT),
        )
    except Exception as exc:
        raise KeyUnavailable("暂时无法验证密钥，请稍后再试。") from exc
    if response.status_code in (401, 403):
        raise ValueError("密钥无效或已被撤销，请检查后重试。")
    if response.status_code == 402:
        raise ValueError("密钥余额不足，暂时无法使用。")
    if response.status_code >= 400:
        raise KeyUnavailable(
            "密钥验证失败（状态码 %s），请稍后再试。" % response.status_code
        )
