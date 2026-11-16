"""AI 仿题共享题池：同一道题的生成结果在用户之间复用，节省模型调用。

- 线上（APP_MODE=supabase）默认开启；本地默认关闭，保留“每次生成新题”便于调试。
- 缓存键为「源题 ID + 题型 + 源哈希」；源哈希包含题目版本与文字答案，题目或答案
  改版后旧缓存自动失效，不会把过时仿题发给学生。
- 题池只保存题目内容与模型名，不记录生成者身份；每个用户的作答与自评仍单独存放。
- 同一道题在池中最多保留 AI_VARIANT_POOL_SIZE 道；用户把池内题目都做过之后，
  若池未满会再生成一道，池满则复用使用次数最少的一道，避免无限消耗 token。
"""

import hashlib
import logging
from uuid import uuid4

import httpx

from . import ai, config, question_gen, topics
from .auth import now

LOGGER = logging.getLogger("mathonline")


def source_hash(problem, answer_text):
    """题目版本 + 文字答案的摘要；任何一项变化都会让旧仿题失效。"""
    payload = "%s\n%s\n%s" % (
        problem.get("id", ""),
        problem.get("version", 1),
        (answer_text or "").strip(),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def enabled():
    return bool(config.AI_VARIANT_SHARE)


def get_or_create(
    problem,
    answer_text,
    subject,
    kind,
    repository,
    seen=None,
    allow_generate=None,
):
    """返回一道可用的仿题；优先命中共享题池，未命中才调用模型。

    `seen` 是当前用户已拿到的缓存 ID 集合（变式练习用）；模拟卷不传，直接复用。
    `allow_generate=False` 表示只允许用缓存（例如模型不可用时仍可从题池出卷）。
    """
    if kind not in ("choice", "blank", "solution"):
        raise ValueError("不支持的题型")
    answer_text = (answer_text or "").strip()
    if not answer_text:
        raise ai.AIUnavailable("题目缺少文字答案，无法生成仿题")
    digest = source_hash(problem, answer_text)
    if not enabled():
        return _generate_and_wrap(
            problem, answer_text, subject, kind, digest, allow_generate
        )
    rows = _cache_rows(problem, digest, kind, repository)
    selected = _pick(rows, seen)
    if selected is not None:
        _touch(selected, repository)
        return _payload(selected, digest, shared=True)
    if allow_generate is False or not ai.enabled():
        raise ai.AIUnavailable("题池暂无可用仿题，且 AI 生题未配置")
    generated = _generate(problem, answer_text, subject, kind)
    row = {
        "id": "varcache-" + uuid4().hex[:20],
        "problem_id": problem["id"],
        "source_hash": digest,
        "type": kind,
        "knowledge_points": [
            code
            for code in (problem.get("knowledge_points") or [])
            if topics.knows(code)
        ][:4],
        "question_text": generated["question_text"],
        "choice_options": generated["choice_options"],
        "multiple": generated["multiple"],
        "answer_text": generated["answer_text"],
        "correct_options": generated["correct_options"],
        "explanation": generated["explanation"],
        "model": generated["model"],
        "use_count": 1,
        "created_at": now(),
        "last_used_at": now(),
    }
    try:
        repository.put("variant_cache", row)
    except httpx.HTTPStatusError as exc:
        # 迁移未执行时仍返回生成结果，只是不进入共享题池。
        if exc.response.status_code not in (400, 404):
            raise
        LOGGER.warning("variant cache write skipped: %s", exc.response.status_code)
        return {
            **generated,
            "cache_id": "",
            "source_hash": digest,
            "shared": False,
        }
    return _payload(row, digest, shared=False)


def _generate_and_wrap(problem, answer_text, subject, kind, digest, allow_generate):
    if allow_generate is False or not ai.enabled():
        raise ai.AIUnavailable("AI 生题未配置")
    generated = _generate(problem, answer_text, subject, kind)
    return {
        **generated,
        "cache_id": "",
        "source_hash": digest,
        "shared": False,
    }


def _generate(problem, answer_text, subject, kind):
    return question_gen.generate_verified(
        question_gen.seed_payload(problem, answer_text),
        answer_text,
        subject,
        kind,
        attempts=3,
        model=config.AI_VARIANT_MODEL,
    )


def _cache_rows(problem, digest, kind, repository):
    try:
        rows = repository.list(
            "variant_cache",
            problem_id=problem["id"],
            source_hash=digest,
            type=kind,
        )
    except httpx.HTTPStatusError as exc:
        # 云端迁移尚未执行时按“无缓存”处理，不阻断生成流程。
        if exc.response.status_code in (400, 404):
            return []
        raise
    return [
        row
        for row in rows
        if row.get("question_text") and (row.get("answer_text") or "").strip()
    ]


def _pick(rows, seen):
    if not rows:
        return None
    seen = set(seen or ())
    unseen = [row for row in rows if row["id"] not in seen]
    if unseen:
        return min(
            unseen,
            key=lambda row: (row.get("use_count") or 0, row.get("last_used_at") or ""),
        )
    if len(rows) >= config.AI_VARIANT_POOL_SIZE:
        return min(
            rows,
            key=lambda row: (row.get("use_count") or 0, row.get("last_used_at") or ""),
        )
    return None


def _touch(row, repository):
    row["use_count"] = (row.get("use_count") or 0) + 1
    row["last_used_at"] = now()
    try:
        repository.put("variant_cache", row)
    except Exception as exc:  # 计数失败不应影响学生拿到题目
        LOGGER.warning("variant cache touch failed: %s", type(exc).__name__)


def _payload(row, digest, shared):
    return {
        "type": row.get("type"),
        "question_text": row.get("question_text", ""),
        "choice_options": row.get("choice_options") or [],
        "multiple": bool(row.get("multiple")),
        "answer_text": row.get("answer_text", ""),
        "correct_options": row.get("correct_options") or [],
        "explanation": row.get("explanation", ""),
        "model": row.get("model", ""),
        "cache_id": row.get("id", ""),
        "source_hash": digest,
        "shared": bool(shared),
    }
