"""北交期末模拟卷：往年试卷分析 → AI 预测蓝图 → 题库组卷 + AI 生题 → 判分。

组卷原则：
- 优先从现有题库按知识点、题型、难度选题（质量高）；
- 每张卷至少在选择题或填空题中出现一道 AI 仿题，且必须通过独立求解校验；
- 题库候选不足时按实际可用数量收缩蓝图，不重复用题；
- 模拟卷不写回公开题库，答案在交卷前不下发。

AI 不可用时明确返回 503，不生成“没有 AI 题”的卷子，避免悄悄降低质量。
"""

import json
import random
import re
from uuid import uuid4

from . import ai, config, grading, question_gen, topics, variant_pool
from .auth import now

SUBJECTS = ("微积分上", "微积分下", "线性代数", "概率论")
TYPE_NAMES = {"choice": "选择题", "blank": "填空题", "solution": "解答题"}
DEFAULT_DURATION = 120
MIN_ITEMS = 6
MAX_ITEMS = 25
PROFILE_YEARS = 6
DIFFICULTY_SHIFT = {"standard": 0.0, "easier": -0.8, "harder": 0.8}

_RNG = random.SystemRandom()
_SYSTEM = (
    "你是北京交通大学数学期末考试命题组的顾问。你要根据往年试卷的统计数据"
    "预测下一次期末考试的题型结构、知识点分布与难度。只依据给出的统计，"
    "不臆造不存在的知识点。严格输出一个 JSON 对象，不要输出 Markdown。"
)


class MockUnavailable(Exception):
    """AI 生题不可用或多次校验失败，无法按规则出卷。"""


def subjects_overview(problems):
    """公开的考试预测入口数据：每科往年题量、题型结构与主要知识点。"""
    result = []
    for subject in SUBJECTS:
        profile = subject_profile(subject, problems)
        if not profile:
            continue
        result.append(
            {
                "subject": subject,
                "problems": profile["problems"],
                "years": profile["years"],
                "type_mix": profile["type_mix"],
                "difficulty": profile["difficulty"],
                "knowledge": profile["knowledge"][:8],
                "ready": profile["problems"] >= MIN_ITEMS,
            }
        )
    return result


def subject_profile(subject, problems):
    rows = [
        p
        for p in problems
        if p.get("subject") == subject
        and (p.get("question_text") or "").strip()
        and p.get("status") == "published"
    ]
    if not rows:
        return None
    years = sorted({p["year"] for p in rows}, reverse=True)
    recent_years = years[:PROFILE_YEARS]
    recent = [p for p in rows if p["year"] in recent_years]
    per_year = []
    for year in recent_years:
        year_rows = [p for p in rows if p["year"] == year]
        per_year.append(
            {
                "year": year,
                "total": len(year_rows),
                "types": _type_counts(year_rows),
                "knowledge": _knowledge_counts(year_rows, limit=6),
            }
        )
    # 每份卷的平均题型数：用近 6 年、题量不少于 5 道的年份求均值。
    usable = [item for item in per_year if item["total"] >= 5]
    type_mix = {
        kind: (
            max(1, round(sum(item["types"].get(kind, 0) for item in usable) / len(usable)))
            if usable
            else 0
        )
        for kind in ("choice", "blank", "solution")
    }
    # 按年份衰减统计知识点权重：最新一年权重 1，向前每年乘 0.85。
    weights = {}
    counts = {}
    for problem in recent:
        age = recent_years.index(problem["year"])
        decay = 0.85**age
        for code in problem.get("knowledge_points") or []:
            if not topics.knows(code):
                continue
            weights[code] = weights.get(code, 0.0) + decay
            counts[code] = counts.get(code, 0) + 1
    total_weight = sum(weights.values()) or 1.0
    knowledge = [
        {
            "code": code,
            "name": topics.name_for(code),
            "count": counts[code],
            "weight": round(weights[code] / total_weight, 4),
        }
        for code in sorted(weights, key=lambda c: (-weights[c], c))
    ]
    available = {
        kind: sum(1 for p in rows if p.get("type") == kind)
        for kind in ("choice", "blank", "solution")
    }
    difficulties = [
        topics.clamp_difficulty(p.get("difficulty"))
        for p in recent
        if p.get("difficulty")
    ]
    return {
        "subject": subject,
        "problems": len(rows),
        "years": years,
        "recent_years": recent_years,
        "per_year": per_year,
        "type_mix": type_mix,
        "available": available,
        "knowledge": knowledge,
        "difficulty": {
            "mean": round(sum(difficulties) / len(difficulties), 2) if difficulties else 3.0,
            "sample": len(difficulties),
        },
        "samples": _samples(rows, recent_years),
    }


def welcome_text(profile):
    top = profile["knowledge"][:4]
    if not top:
        return "该科目题目的知识点标注尚未完成，组卷会优先使用现有题型结构。"
    return "近 %d 年考查最多：%s。" % (
        len(profile["recent_years"]),
        "、".join(item["name"] for item in top),
    )


def build_blueprint(subject, profile, difficulty="standard"):
    if ai.enabled():
        try:
            payload = ai.complete_json(
                _blueprint_messages(subject, profile, difficulty),
                max_tokens=2600,
                model=config.AI_BLUEPRINT_MODEL,
            )
            blueprint = _validate_blueprint(payload, profile)
            if blueprint:
                blueprint["source"] = "ai"
                blueprint["model"] = config.AI_BLUEPRINT_MODEL
                return blueprint
        except ai.AIUnavailable:
            pass
    blueprint = _fallback_blueprint(profile)
    blueprint["source"] = "heuristic"
    blueprint["model"] = ""
    return blueprint


def _blueprint_messages(subject, profile, difficulty):
    allowed = [
        {"code": item["code"], "name": item["name"], "weight": item["weight"]}
        for item in profile["knowledge"][:14]
    ]
    payload = {
        "subject": subject,
        "years": profile["recent_years"],
        "type_mix_per_paper": profile["type_mix"],
        "available_by_type": profile["available"],
        "knowledge": allowed,
        "difficulty_mean": profile["difficulty"],
        "per_year": profile["per_year"],
        "samples": profile["samples"],
    }
    body = (
        "【往年试卷统计】\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n\n【要求】\n"
        "1. 预测下一次" + subject + "期末考试的题型结构；每段给出 type（choice/blank/"
        "solution）、count（题量）、points_each（每题分值）、knowledge（本题段重点考查"
        "的知识点编码，必须来自上面 knowledge 列表）。\n"
        "2. 题量总和 8 至 20 道，尽量贴近往年每卷题量；分值总和接近 100 分。\n"
        "3. 选择题或填空题至少保留一段，用于放置一道 AI 仿题。\n"
        "4. knowledge 的编码不要新造；每段 2 至 5 个编码，按主次排列。\n"
        "5. rationale 用一两句话说明预测依据；emphasis 列出 3 个最可能考的知识点及理由。\n"
        "6. 难度取向：" + difficulty + "（更贴近往年平均难度即可，不要刻意拔高）。\n"
        "只返回 JSON：{\"title\":\"卷名\",\"duration_minutes\":120,"
        "\"sections\":[{\"type\":\"choice\",\"count\":5,\"points_each\":3,"
        "\"knowledge\":[\"limit\"],\"note\":\"说明\"}],"
        "\"emphasis\":[{\"code\":\"limit\",\"reason\":\"理由\"}],"
        "\"rationale\":\"预测依据\"}"
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": body},
    ]


def _validate_blueprint(payload, profile):
    allowed = {item["code"] for item in profile["knowledge"]}
    type_mix = profile["type_mix"]
    sections = []
    for raw in ai.parse_list(payload.get("sections")):
        if not isinstance(raw, dict):
            continue
        kind = raw.get("type")
        if kind not in ("choice", "blank", "solution"):
            continue
        if profile["available"].get(kind, 0) <= 0:
            continue
        count = _int(raw.get("count"), 1, 15)
        points = _int(raw.get("points_each"), 1, 20)
        codes = [
            code
            for code in ai.parse_list(raw.get("knowledge"))
            if isinstance(code, str) and code in allowed
        ][:5]
        if not codes:
            codes = [
                item["code"]
                for item in profile["knowledge"]
                if topics.domain_for(item["code"]) == topics.domain_for_subject(
                    profile["subject"]
                )
            ][:3]
        if not codes:
            continue
        count = min(count, profile["available"].get(kind, 0))
        sections.append(
            {
                "type": kind,
                "count": max(1, count),
                "points_each": points,
                "knowledge": codes[:5],
                "note": ai.clean_text(raw.get("note"), 200),
            }
        )
    if not sections:
        return None
    if not any(section["type"] in ("choice", "blank") for section in sections):
        return None
    sections = _trim_sections(sections)
    sections = _ensure_ai_slot(sections)
    total = sum(section["count"] for section in sections)
    if total < MIN_ITEMS:
        return None
    emphasis = []
    for item in ai.parse_list(payload.get("emphasis")):
        if not isinstance(item, dict) or item.get("code") not in allowed:
            continue
        emphasis.append(
            {
                "code": item["code"],
                "name": topics.name_for(item["code"]),
                "reason": ai.clean_text(item.get("reason"), 200),
            }
        )
    title = ai.clean_text(payload.get("title"), 120) or (
        "北京交通大学 " + profile["subject"] + " 期末模拟卷（AI 预测）"
    )
    duration = _int(payload.get("duration_minutes"), 45, 180) or DEFAULT_DURATION
    return {
        "title": title,
        "duration_minutes": duration,
        "sections": sections,
        "emphasis": emphasis[:3],
        "rationale": ai.clean_text(payload.get("rationale"), 500),
        "type_mix": type_mix,
    }


def _fallback_blueprint(profile):
    available = profile["available"]
    sections = []
    for kind in ("choice", "blank", "solution"):
        count = min(profile["type_mix"].get(kind, 0), available.get(kind, 0))
        if count <= 0:
            continue
        codes = [item["code"] for item in profile["knowledge"]][:4]
        sections.append(
            {
                "type": kind,
                "count": max(1, count),
                "points_each": {"choice": 3, "blank": 4, "solution": 10}[kind],
                "knowledge": codes,
                "note": "按往年该题型平均题量生成",
            }
        )
    sections = _ensure_ai_slot(_trim_sections(sections))
    return {
        "title": "北京交通大学 " + profile["subject"] + " 期末模拟卷",
        "duration_minutes": DEFAULT_DURATION,
        "sections": sections,
        "emphasis": [
            {"code": item["code"], "name": item["name"], "reason": "往年高频考点"}
            for item in profile["knowledge"][:3]
        ],
        "rationale": "按近 %d 年题型结构与知识点权重自动组卷。"
        % len(profile["recent_years"]),
        "type_mix": profile["type_mix"],
    }


def _ensure_ai_slot(sections):
    """保证至少有一段选择/填空能容纳「一道题库题 + 一道 AI 仿题」。"""
    candidates = [
        section
        for section in sections
        if section["type"] in ("choice", "blank")
    ]
    if not candidates:
        return sections
    if any(section["count"] >= 2 for section in candidates):
        return sections
    target = candidates[0]
    target["count"] = 2
    while sum(section["count"] for section in sections) > MAX_ITEMS:
        trimmable = [
            section
            for section in sections
            if section is not target and section["count"] > 1
        ]
        if not trimmable:
            break
        max(trimmable, key=lambda section: section["count"])["count"] -= 1
    return sections


def _trim_sections(sections):
    total = sum(section["count"] for section in sections)
    while total > MAX_ITEMS:
        largest = max(sections, key=lambda section: section["count"])
        if largest["count"] <= 1:
            break
        largest["count"] -= 1
        total -= 1
    return [section for section in sections if section["count"] > 0]


def _type_counts(rows):
    counts = {}
    for row in rows:
        counts[row["type"]] = counts.get(row["type"], 0) + 1
    return counts


def _knowledge_counts(rows, limit=6):
    counts = {}
    for row in rows:
        for code in row.get("knowledge_points") or []:
            if topics.knows(code):
                counts[code] = counts.get(code, 0) + 1
    return [
        {"code": code, "name": topics.name_for(code), "count": count}
        for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[
            :limit
        ]
    ]


def _samples(rows, years):
    samples = {}
    for kind in ("choice", "blank", "solution"):
        pool = [row for row in rows if row["type"] == kind]
        if not pool:
            continue
        pool.sort(key=lambda row: (-row["year"], row["id"]))
        samples[kind] = ai.clip(pool[0].get("question_text"), 320)
    return samples


def _int(value, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return min(high, max(low, number))


def _knowledge_match(problem, codes):
    return len(set(problem.get("knowledge_points") or []) & set(codes))


def pick_bank_item(
    candidates,
    used,
    codes,
    difficulty,
    seen,
    index,
):
    """按知识点轮转 + 难度接近 + 未做过优先选题，返回 None 表示无候选。"""
    pool = [p for p in candidates if p["id"] not in used]
    if not pool:
        return None
    ordered = list(codes)
    target = ordered[index % len(ordered)] if ordered else ""
    for focus in ([target] if target else []) + [codes]:
        subset = [p for p in pool if _knowledge_match(p, focus)]
        if not subset:
            continue
        scored = []
        for problem in subset:
            overlap = _knowledge_match(problem, codes) / max(1, len(codes))
            level = topics.clamp_difficulty(problem.get("difficulty")) or 3
            closeness = 1 - min(4, abs(level - difficulty)) / 4
            score = (
                overlap * 3
                + closeness * 1.6
                + (0.8 if problem["id"] not in seen else 0)
                + _RNG.random() * 0.4
            )
            scored.append((score, problem))
        scored.sort(key=lambda item: -item[0])
        return scored[0][1]
    return min(pool, key=lambda p: _RNG.random())


_NUMERIC_ANSWER = re.compile(
    r"^[+-]?(?:\d+(?:\.\d+)?|\d+\s*/\s*\d+|\\frac\{[^{}]+\}\{[^{}]+\})$"
)


def _generation_seeds(candidates, codes, answers, limit=3):
    """挑选适合仿题的母题：答案可直接核对、题干短、难度适中优先。"""
    pool = []
    for problem in candidates:
        text = (problem.get("question_text") or "").strip()
        answer = ((answers.get(problem.get("id")) or {}).get("text") or "").strip()
        if not text or not answer:
            continue
        length = len(text)
        level = topics.clamp_difficulty(problem.get("difficulty")) or 3
        score = (
            (4 if _NUMERIC_ANSWER.match(grading.normalize(answer)) else 0)
            + (2 if length < 260 else 1 if length < 520 else 0)
            + (1 if level <= 3 else 0)
            + min(2, _knowledge_match(problem, codes))
            + _RNG.random() * 0.3
        )
        pool.append((score, problem))
    pool.sort(key=lambda item: -item[0])
    return [problem for _score, problem in pool[:limit]]


def _item_from_problem(problem, answer, points, origin="bank"):
    answer_text = (answer or {}).get("text", "")
    correct = (answer or {}).get("correct_options") or []
    return {
        "id": "",
        "position": 0,
        "type": problem["type"],
        "knowledge_points": [
            code
            for code in (problem.get("knowledge_points") or [])
            if topics.knows(code)
        ][:4],
        "difficulty": topics.clamp_difficulty(problem.get("difficulty")) or 3,
        "points": points,
        "origin": origin,
        "problem_id": problem["id"],
        "seed_problem_id": problem["id"],
        "question_text": problem.get("question_text") or "",
        "choice_options": list(problem.get("choice_options") or []),
        "multiple": bool(problem.get("multiple")),
        "answer_text": answer_text,
        "correct_options": list(correct),
        "explanation": answer_text,
        "model": "",
    }


def _item_from_generated(generated, seed, points):
    return {
        "id": "",
        "position": 0,
        "type": generated["type"],
        "knowledge_points": [
            code
            for code in (seed.get("knowledge_points") or [])
            if topics.knows(code)
        ][:4],
        "difficulty": topics.clamp_difficulty(seed.get("difficulty")) or 3,
        "points": points,
        "origin": "ai",
        "problem_id": None,
        "seed_problem_id": seed["id"],
        "question_text": generated["question_text"],
        "choice_options": generated["choice_options"],
        "multiple": generated["multiple"],
        "answer_text": generated["answer_text"],
        "correct_options": generated["correct_options"],
        "explanation": generated["explanation"],
        "model": generated["model"],
    }


def assemble_paper(
    user,
    profile,
    blueprint,
    problems,
    answers,
    seen,
    difficulty="standard",
    repository=None,
):
    shift = DIFFICULTY_SHIFT.get(difficulty, 0.0)
    target = (profile["difficulty"]["mean"] or 3.0) + shift
    usable = [
        problem
        for problem in problems
        if problem.get("subject") == profile["subject"]
        and problem.get("status") == "published"
        and (problem.get("question_text") or "").strip()
    ]
    by_type = {
        kind: [p for p in usable if p.get("type") == kind]
        for kind in ("choice", "blank", "solution")
    }
    used = set()
    groups = []
    for section in blueprint["sections"]:
        kind = section["type"]
        candidates = by_type.get(kind, [])
        chosen = []
        for index in range(section["count"]):
            problem = pick_bank_item(
                candidates, used, section["knowledge"], target, seen, index
            )
            if problem is None:
                raise MockUnavailable(
                    "题库中「%s」候选不足，无法按预测蓝图组卷。"
                    % TYPE_NAMES.get(kind, kind)
                )
            used.add(problem["id"])
            chosen.append(
                _item_from_problem(
                    problem,
                    answers.get(problem["id"]),
                    section["points_each"],
                )
            )
        groups.append(chosen)
    # 优先在填空题里放 AI 仿题（数值答案更容易校验），其次选择题；某一段
    # 生成失败时换另一段重试，不改变卷面题量。
    slots = [
        (index, section)
        for index, section in enumerate(blueprint["sections"])
        if section["type"] in ("choice", "blank") and len(groups[index]) >= 2
    ]
    slots.sort(key=lambda item: 0 if item[1]["type"] == "blank" else 1)
    if not slots:
        raise MockUnavailable("预测蓝图缺少可放置 AI 仿题的选择题或填空题段。")
    replaced = False
    last_error = ""
    for section_index, section in slots:
        group = groups[section_index]
        for seed in _generation_seeds(
            by_type.get(section["type"], []), section["knowledge"], answers
        ):
            seed_answer = (answers.get(seed["id"]) or {}).get("text", "")
            try:
                # 共享题池命中时不再调用模型；未命中才生成并写入题池。
                generated = variant_pool.get_or_create(
                    seed,
                    seed_answer,
                    profile["subject"],
                    section["type"],
                    repository,
                    allow_generate=ai.enabled(),
                )
            except (question_gen.GenerationError, ai.AIUnavailable) as exc:
                last_error = str(exc)
                continue
            group[-1] = _item_from_generated(
                generated, seed, section["points_each"]
            )
            replaced = True
            break
        if replaced:
            break
    if not replaced:
        if not ai.enabled():
            raise MockUnavailable(
                "AI 生题未配置且共享题池暂无可用仿题，暂时无法生成模拟卷。"
            )
        raise MockUnavailable(
            "AI 生题未通过可解性校验，请稍后重试。"
            + ("（" + last_error[:80] + "）" if last_error else "")
        )
    items = [item for group in groups for item in group]
    for position, item in enumerate(items, 1):
        item["id"] = "item-" + str(position)
        item["position"] = position
        item["result"] = ""
        item["score"] = 0
        item["feedback"] = ""
    max_score = sum(item["points"] for item in items)
    paper = {
        "id": "mock-" + uuid4().hex[:20],
        "user_id": user["id"],
        "subject": profile["subject"],
        "title": blueprint["title"],
        "status": "ready",
        "blueprint": blueprint,
        "profile": _profile_summary(profile),
        "duration_minutes": blueprint["duration_minutes"],
        "max_score": max_score,
        "score": 0,
        "items": items,
        "knowledge_summary": [],
        "submitted_at": None,
        "model": blueprint.get("model") or "",
        "created_at": now(),
        "updated_at": now(),
    }
    return paper


def _profile_summary(profile):
    return {
        "years": profile["years"][:PROFILE_YEARS],
        "problems": profile["problems"],
        "type_mix": profile["type_mix"],
        "knowledge": profile["knowledge"][:10],
        "difficulty": profile["difficulty"],
        "per_year": profile["per_year"],
        "welcome": welcome_text(profile),
    }


def create_paper(user, subject, difficulty, problems, answers, seen, repository):
    profile = subject_profile(subject, problems)
    if not profile:
        raise ValueError("该科目暂无可用题目。")
    blueprint = build_blueprint(subject, profile, difficulty)
    if not blueprint["sections"]:
        raise MockUnavailable("预测蓝图为空，请稍后重试。")
    return assemble_paper(
        user,
        profile,
        blueprint,
        problems,
        answers,
        seen,
        difficulty,
        repository,
    )


def grade_answers(paper, answers):
    """按题型判分；AI 不可用的解答题/填空边界题保持待自评。"""
    by_id = {item["id"]: item for item in paper["items"]}
    for item in paper["items"]:
        item["my_text"] = ""
        item["my_options"] = []
    ai_budget = 8
    for item_id, answer in answers.items():
        item = by_id.get(item_id)
        if not item:
            continue
        text = (answer.get("text") or "").strip()
        options = [str(option) for option in (answer.get("options") or [])]
        item["my_text"] = text
        item["my_options"] = options
        if item["type"] == "choice":
            if not options:
                item["result"], item["score"] = "", 0
                continue
            correct = set(item["correct_options"])
            selected = set(options)
            item["result"] = "correct" if selected == correct else "incorrect"
            item["score"] = item["points"] if selected == correct else 0
            item["feedback"] = ""
            continue
        if not text:
            item["result"], item["score"] = "", 0
            continue
        if item["type"] == "blank":
            verdict = grading.algorithmic_verdict(item["answer_text"], text)
            if verdict is None and ai_budget > 0 and ai.enabled():
                ai_budget -= 1
                verdict = _judge_blank(item, text)
            if verdict is None:
                item["result"], item["score"] = "pending", 0
                item["feedback"] = "本题待自评"
                continue
            item["result"] = verdict
            item["score"] = item["points"] if verdict == "correct" else 0
            item["feedback"] = ""
            continue
        if ai_budget <= 0 or not ai.enabled():
            item["result"], item["score"] = "pending", 0
            item["feedback"] = "解答题待自评"
            continue
        ai_budget -= 1
        graded = _judge_solution(item, text)
        if graded is None:
            item["result"], item["score"] = "pending", 0
            item["feedback"] = "解答题待自评"
        else:
            item["result"], item["score"], item["feedback"] = graded
    paper["score"] = sum(item["score"] for item in paper["items"])
    paper["status"] = "submitted"
    paper["submitted_at"] = now()
    paper["updated_at"] = now()
    paper["knowledge_summary"] = knowledge_summary(paper)
    return paper


def _judge_blank(item, text):
    body = (
        "【题目】\n"
        + ai.clip(item["question_text"], 3000)
        + "\n\n【参考答案】\n"
        + ai.clip(item["answer_text"], 1200)
        + "\n\n【学生作答】\n"
        + ai.clip(text, 1200)
        + '\n\n【要求】判断学生作答与参考答案是否数学等价，只返回 JSON：'
        '{"correct":true,"note":"不超过40字"}'
    )
    try:
        payload = ai.complete_json(
            [
                {"role": "system", "content": "你是严谨的数学阅卷人，只返回 JSON。"},
                {"role": "user", "content": body},
            ],
            max_tokens=1600,
        )
    except ai.AIUnavailable:
        return None
    value = payload.get("correct")
    if isinstance(value, bool):
        return "correct" if value else "incorrect"
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return "correct" if value.strip().lower() == "true" else "incorrect"
    return None


def _judge_solution(item, text):
    max_points = item["points"]
    body = (
        "【题目】\n"
        + ai.clip(item["question_text"], 3000)
        + "\n\n【参考答案与解析】\n"
        + ai.clip(item["answer_text"] + "\n" + item.get("explanation", ""), 2600)
        + "\n\n【学生作答】\n"
        + ai.clip(text, 2600)
        + "\n\n【要求】按满分 "
        + str(max_points)
        + " 分打分：步骤与结论都正确给满分；思路正确但有缺漏给部分分；"
        '完全错误给 0 分。只返回 JSON：{"score":0,"feedback":"不超过60字的评价"}'
    )
    try:
        payload = ai.complete_json(
            [
                {"role": "system", "content": "你是严谨的数学阅卷人，只返回 JSON。"},
                {"role": "user", "content": body},
            ],
            max_tokens=1600,
        )
    except ai.AIUnavailable:
        return None
    try:
        raw = float(payload.get("score"))
    except (TypeError, ValueError):
        return None
    score = round(min(max(raw, 0), max_points))
    result = "correct" if score >= max_points else "incorrect"
    return result, score, ai.clean_text(payload.get("feedback"), 200)


def knowledge_summary(paper):
    buckets = {}
    for item in paper["items"]:
        if item.get("result") in ("", "pending"):
            continue
        for code in item.get("knowledge_points") or []:
            bucket = buckets.setdefault(
                code,
                {
                    "code": code,
                    "name": topics.name_for(code),
                    "score": 0,
                    "max": 0,
                    "correct": 0,
                    "count": 0,
                },
            )
            bucket["score"] += item["score"]
            bucket["max"] += item["points"]
            bucket["count"] += 1
            if item["result"] == "correct":
                bucket["correct"] += 1
    return [
        bucket
        for bucket in sorted(
            buckets.values(), key=lambda item: (-item["max"], item["name"])
        )
    ]


def paper_payload(paper, include_answers=False):
    revealed = include_answers or paper.get("status") == "submitted"
    items = []
    for item in paper["items"]:
        row = {
            key: item.get(key)
            for key in (
                "id",
                "position",
                "type",
                "knowledge_points",
                "difficulty",
                "points",
                "origin",
                "question_text",
                "choice_options",
                "multiple",
                "model",
                "result",
                "score",
            )
        }
        if revealed:
            row.update(
                answer_text=item.get("answer_text", ""),
                correct_options=item.get("correct_options", []),
                explanation=item.get("explanation", ""),
                feedback=item.get("feedback", ""),
                my_text=item.get("my_text", ""),
                my_options=item.get("my_options", []),
            )
        items.append(row)
    return {
        "id": paper["id"],
        "subject": paper["subject"],
        "title": paper["title"],
        "status": paper["status"],
        "blueprint": paper.get("blueprint") or {},
        "profile": paper.get("profile") or {},
        "duration_minutes": paper.get("duration_minutes"),
        "max_score": paper.get("max_score"),
        "score": paper.get("score", 0),
        "items": items,
        "knowledge_summary": paper.get("knowledge_summary") or [],
        "submitted_at": paper.get("submitted_at"),
        "model": paper.get("model") or "",
        "created_at": paper.get("created_at"),
        "ai_items": sum(1 for item in paper["items"] if item.get("origin") == "ai"),
    }
