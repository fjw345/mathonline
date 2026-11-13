"""图片作答转写：把用户上传的作答图片转成文字，再交给既有 AI 判分。

只读取当前用户、purpose=submission 的图片，不接触参考答案图片。转写结果按
「用户 + 题目 + 修订号 + 图片顺序」缓存，重试或刷新不会重复调用视觉模型。
转写失败一律抛 VisionUnavailable，由判分端点降级为待自检，不伪造文字。
"""

import base64
import io
import logging

import httpx
from PIL import Image, UnidentifiedImageError

from . import ai, config
from .auth import now

MAX_IMAGES = 5
MAX_SIDE = 1600
JPEG_QUALITY = 82
MAX_TRANSCRIPT = 6000
_EMPTY_MARKS = ("[?]", "（看不清）", "(看不清)", "无法辨认", "无法识别")

LOGGER = logging.getLogger("mathonline")
_SYSTEM = (
    "你是数学作答转写助手。只转写图片中学生的作答内容，不解题、不补充、不评价。"
    "保留原有的步骤、公式与最终答案，公式用 Markdown/LaTeX 表示。"
    "看不清的地方写 [?]，不要猜测或编造。只输出转写文本，不要输出解释。"
)


class VisionUnavailable(Exception):
    """视觉模型未配置、图片读取失败或转写结果不可用。"""


def enabled():
    return bool(config.AI_VISION_ENABLED)


def transcribe(user, problem, state, repository):
    """返回 {text, model, images}；失败抛 VisionUnavailable。"""
    image_ids = list(state.get("image_ids") or [])[:MAX_IMAGES]
    if not image_ids:
        raise VisionUnavailable("没有可转写的作答图片")
    if not enabled():
        raise VisionUnavailable("图片作答转写未配置，请自检")
    cached = _cached(user, problem, state, image_ids, repository)
    if cached:
        return cached
    data_urls = []
    for aid in image_ids:
        asset = repository.get("media_assets", id=aid)
        if (
            not asset
            or asset.get("owner_id") != user["id"]
            or asset.get("purpose") != "submission"
        ):
            raise VisionUnavailable("作答图片不属于当前用户，无法转写")
        data_urls.append(_image_data_url(asset))
    if not data_urls:
        raise VisionUnavailable("作答图片读取失败，请自检")
    messages = _messages(len(data_urls), data_urls)
    try:
        raw = ai.chat(
            messages,
            model=config.AI_VISION_MODEL,
            base_url=config.AI_VISION_BASE_URL,
            api_key=config.AI_VISION_API_KEY,
            timeout=config.AI_VISION_TIMEOUT,
            max_tokens=2000,
            json_mode=False,
            retries=1,
        )
    except ai.AIUnavailable as exc:
        raise VisionUnavailable("图片作答转写暂不可用，请自检") from exc
    text = _clean(raw)
    if not _usable(text):
        raise VisionUnavailable("图片作答未能识别出文字，请自检")
    record = {
        "id": user["id"] + ":" + problem["id"],
        "user_id": user["id"],
        "problem_id": problem["id"],
        "revision": state.get("revision", 0),
        "image_ids": image_ids,
        "text": text,
        "model": config.AI_VISION_MODEL,
        "created_at": now(),
    }
    try:
        repository.put("submission_transcripts", record)
    except Exception as exc:  # 缓存失败不影响本次判分
        LOGGER.warning("transcript cache write failed: %s", type(exc).__name__)
    return {"text": text, "model": config.AI_VISION_MODEL, "images": len(data_urls)}


def _cached(user, problem, state, image_ids, repository):
    try:
        row = repository.get(
            "submission_transcripts", id=user["id"] + ":" + problem["id"]
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (400, 404):
            return None
        raise
    if (
        row
        and row.get("revision") == state.get("revision", 0)
        and list(row.get("image_ids") or []) == image_ids
        and (row.get("text") or "").strip()
    ):
        return {
            "text": row["text"].strip(),
            "model": row.get("model", ""),
            "images": len(image_ids),
        }
    return None


def _messages(count, data_urls):
    intro = (
        "请按上传顺序转写这些图片中的学生作答，它们属于同一份作答。"
        if count > 1
        else "请转写这张图片中的学生作答。"
    )
    content = [{"type": "text", "text": intro}]
    content.extend(
        {"type": "image_url", "image_url": {"url": url}} for url in data_urls
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": content},
    ]


def _image_data_url(asset):
    raw = _asset_bytes(asset)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            picture = image.convert("RGB")
            picture.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            picture.save(out, format="JPEG", quality=JPEG_QUALITY)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise VisionUnavailable("作答图片读取失败，请自检") from exc
    encoded = base64.b64encode(out.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + encoded


def _asset_bytes(asset):
    path = str(asset.get("path") or "")
    if not path:
        raise VisionUnavailable("作答图片路径缺失，请重新上传")
    if config.MODE == "local":
        target = (
            config.ROOT
            / "data"
            / (path if path.startswith("uploads/") else "assets/" + path)
        )
        if not target.is_file():
            raise VisionUnavailable("作答图片文件缺失，请重新上传")
        return target.read_bytes()
    try:
        response = httpx.get(
            config.SB_URL + "/storage/v1/object/" + config.BUCKET + "/" + path,
            headers={
                "apikey": config.SB_KEY,
                "Authorization": "Bearer " + config.SB_KEY,
            },
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise VisionUnavailable("作答图片读取失败，请重新上传") from exc
    return response.content


def _clean(raw):
    text = (raw or "").strip()
    if text.startswith(chr(96) * 3):
        lines = text.splitlines()
        if lines and lines[0].startswith(chr(96) * 3):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith(chr(96) * 3):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if text.startswith("{"):
        try:
            payload = ai.json_object(text)
        except ai.AIUnavailable:
            payload = None
        if isinstance(payload, dict):
            value = payload.get("text") or payload.get("transcript")
            if isinstance(value, str):
                text = value.strip()
    for prefix in ("转写：", "转写:", "作答：", "作答:"):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)[:MAX_TRANSCRIPT].strip()


def _usable(text):
    if len(text) < 2:
        return False
    compact = text.replace(" ", "").replace("\n", "")
    return compact not in _EMPTY_MARKS
