"""错题变式练习（一题多练）：以原题为母题生成同思路、新数据的题目。

生成结果单独存放，不写回题库；答案在用户点击“查看答案”之前不下发。
"""

from uuid import uuid4

from . import ai, question_gen, topics
from .auth import now


def create_variant(user, problem, answer, repository):
    if not ai.enabled():
        raise ai.AIUnavailable("AI 变式练习未配置")
    kind = problem.get("type")
    if kind not in ("choice", "blank", "solution"):
        raise ValueError("该题型暂不支持变式练习。")
    answer_text = (answer or {}).get("text", "")
    generated = question_gen.generate_verified(
        question_gen.seed_payload(problem, answer_text),
        answer_text,
        problem.get("subject", ""),
        kind,
    )
    row = {
        "id": "variant-" + uuid4().hex[:20],
        "user_id": user["id"],
        "problem_id": problem["id"],
        "subject": problem.get("subject", ""),
        "type": generated["type"],
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
        "result": "",
        "created_at": now(),
        "answered_at": None,
    }
    return repository.put("practice_variants", row)


def variant_payload(row, include_answer=False):
    payload = {
        "id": row["id"],
        "problem_id": row["problem_id"],
        "subject": row.get("subject", ""),
        "type": row.get("type", ""),
        "knowledge_points": topics.resolve(
            row.get("knowledge_points") or [], row.get("subject")
        ),
        "question_text": row.get("question_text", ""),
        "choice_options": row.get("choice_options") or [],
        "multiple": bool(row.get("multiple")),
        "model": row.get("model", ""),
        "result": row.get("result", ""),
        "created_at": row.get("created_at", ""),
        "answered_at": row.get("answered_at"),
    }
    if include_answer:
        payload.update(
            answer_text=row.get("answer_text", ""),
            correct_options=row.get("correct_options") or [],
            explanation=row.get("explanation", ""),
        )
    return payload


def mark_result(row, result):
    row["result"] = result
    row["answered_at"] = now()
    return row
