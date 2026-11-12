"""学情分析：把练习记录聚合到知识点，并按需生成 AI 诊断报告。

统计是确定性的、每次现算；报告只依据聚合后的数据，不把学生作答原文送给模型，
因此报告可缓存、可复现，也不增加隐私暴露面。模型不可用时只返回统计结果。
"""

import hashlib
import json

from . import ai, topics
from .store import read_store

MAX_KNOWLEDGE_IN_PROMPT = 16
MAX_REPORT_SUMMARY = 900
MAX_REPORT_ITEMS = 4
MAX_PLAN_ITEMS = 5

_SYSTEM = (
    "你是大学数学课程的学习教练。只依据给出的练习统计做判断，不臆测题目内容，"
    "不编造数据。语气简洁、具体、可执行，面向准备期末考试的本科生。"
    "严格输出一个 JSON 对象，不要输出 Markdown 代码围栏。"
)


def load_problems(ids, repository):
    """按请求批量读取题目；云端只取用到的 ID，本地一次取全表。"""
    wanted = [pid for pid in dict.fromkeys(ids) if pid]
    if not wanted:
        return {}
    with read_store(repository) as reader:
        if hasattr(reader, "prime"):
            reader.prime("problems", wanted)
            return {
                pid: reader.get("problems", id=pid)
                for pid in wanted
                if reader.get("problems", id=pid)
            }
        rows = reader.list("problems")
    index = {row["id"]: row for row in rows}
    return {pid: index[pid] for pid in wanted if pid in index}


def build(user, repository, mistake_ids=None, states=None, mock_papers=None):
    states = (
        states
        if states is not None
        else repository.list("user_problem_progress", user_id=user["id"])
    )
    problems = load_problems([s["problem_id"] for s in states], repository)
    return aggregate(states, problems, set(mistake_ids or ()), mock_papers)


def aggregate(progress_rows, problems, mistake_ids, mock_papers=None):
    buckets = {}
    domains = {}
    totals = {
        "practiced": 0,
        "correct": 0,
        "incorrect": 0,
        "pending": 0,
        "mistakes": 0,
    }
    for row in progress_rows:
        problem = problems.get(row.get("problem_id"))
        if not problem:
            continue
        result = row.get("result")
        totals["practiced"] += 1
        if result in ("correct", "incorrect"):
            totals[result] += 1
        elif result == "pending":
            totals["pending"] += 1
        codes = topics.parse_filter(problem.get("knowledge_points") or [])
        if not codes:
            continue
        is_mistake = problem["id"] in mistake_ids
        if is_mistake:
            totals["mistakes"] += 1
        seen_domains = set()
        for code in codes:
            bucket = buckets.setdefault(code, _bucket(code))
            bucket["attempts"] += 1
            if result in ("correct", "incorrect"):
                bucket[result] += 1
            elif result == "pending":
                bucket["pending"] += 1
            if is_mistake:
                bucket["mistakes"] += 1
            seen_domains.add(topics.domain_for(code))
        for domain in seen_domains:
            if not domain:
                continue
            current = domains.setdefault(
                domain,
                {
                    "domain": domain,
                    "name": topics.DOMAIN_NAMES.get(domain, domain),
                    "attempts": 0,
                    "correct": 0,
                    "incorrect": 0,
                },
            )
            current["attempts"] += 1
            if result in ("correct", "incorrect"):
                current[result] += 1

    knowledge = [_finalize(bucket) for bucket in buckets.values()]
    knowledge.sort(
        key=lambda item: (-item["weakness"], -item["attempts"], item["name"])
    )
    graded = [item for item in knowledge if item["graded"]]
    weak = [
        item
        for item in knowledge
        if (item["graded"] >= 2 and item["accuracy"] < 0.75)
        or item["mistakes"] >= 2
        or (item["graded"] == 1 and item["incorrect"] and item["mistakes"])
    ]
    if not weak and graded:
        weak = graded[:2]
    strengths = sorted(
        (
            item
            for item in graded
            if item["graded"] >= 2 and item["accuracy"] >= 0.8
        ),
        key=lambda item: (-item["accuracy"], -item["graded"]),
    )[:3]
    for domain in domains.values():
        domain_graded = domain["correct"] + domain["incorrect"]
        domain["accuracy"] = (
            round(domain["correct"] / domain_graded, 4) if domain_graded else None
        )
    domain_rows = sorted(
        domains.values(), key=lambda item: (-item["attempts"], item["name"])
    )
    stats = {
        "totals": totals,
        "knowledge": knowledge,
        "weakest": weak[:3],
        "strengths": strengths,
        "domains": domain_rows,
        "mock": mock_summary(mock_papers),
    }
    stats["fingerprint"] = fingerprint(stats)
    return stats


def mock_summary(mock_papers):
    """最近几次模拟卷的分数与知识点得分，供学情分析与 AI 报告引用。"""
    rows = []
    for paper in mock_papers or []:
        if paper.get("status") != "submitted":
            continue
        rows.append(
            {
                "paper_id": paper["id"],
                "subject": paper.get("subject", ""),
                "title": paper.get("title", ""),
                "score": paper.get("score", 0),
                "max_score": paper.get("max_score", 0),
                "submitted_at": paper.get("submitted_at"),
                "knowledge": [
                    {
                        "code": item.get("code"),
                        "name": item.get("name"),
                        "score": item.get("score", 0),
                        "max": item.get("max", 0),
                    }
                    for item in paper.get("knowledge_summary") or []
                    if item.get("code")
                ],
            }
        )
    rows.sort(key=lambda item: item.get("submitted_at") or "", reverse=True)
    return rows[:10]


def _bucket(code):
    return {
        "code": code,
        "name": topics.name_for(code),
        "domain": topics.domain_for(code),
        "attempts": 0,
        "correct": 0,
        "incorrect": 0,
        "pending": 0,
        "mistakes": 0,
    }


def _finalize(bucket):
    graded = bucket["correct"] + bucket["incorrect"]
    accuracy = round(bucket["correct"] / graded, 4) if graded else None
    weakness = 0.0
    if graded:
        weakness += 0.7 * (1 - bucket["correct"] / graded) * min(graded, 6) / 6
    weakness += 0.3 * min(bucket["mistakes"], 3) / 3
    return {
        **bucket,
        "graded": graded,
        "accuracy": accuracy,
        "weakness": round(weakness, 4),
    }


def fingerprint(stats):
    compact = {
        "totals": stats["totals"],
        "knowledge": [
            {
                key: item[key]
                for key in (
                    "code",
                    "attempts",
                    "correct",
                    "incorrect",
                    "pending",
                    "mistakes",
                )
            }
            for item in stats["knowledge"]
        ],
        "mock": [
            {
                "paper_id": paper["paper_id"],
                "score": paper["score"],
                "max_score": paper["max_score"],
                "knowledge": [
                    [item["code"], item["score"], item["max"]]
                    for item in paper["knowledge"]
                ],
            }
            for paper in stats.get("mock", [])
        ],
    }
    return hashlib.sha256(
        json.dumps(compact, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def report_messages(stats):
    entries = [
        {
            key: item[key]
            for key in (
                "code",
                "name",
                "attempts",
                "correct",
                "incorrect",
                "pending",
                "mistakes",
                "accuracy",
            )
        }
        for item in stats["knowledge"][:MAX_KNOWLEDGE_IN_PROMPT]
    ]
    payload = {
        "totals": stats["totals"],
        "knowledge": entries,
        "domains": stats["domains"],
        "mock": [
            {
                "subject": paper["subject"],
                "score": paper["score"],
                "max_score": paper["max_score"],
                "knowledge": paper["knowledge"][:8],
            }
            for paper in stats.get("mock", [])[:3]
        ],
    }
    body = (
        "【练习统计】\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n\n【要求】\n"
        "1. 根据正确率、错题数与练习量判断掌握情况；数据少的知识点要说明“样本较少”。\n"
        "2. 指出 1 至 3 个最薄弱的知识点，给出具体、可执行的复习建议"
        "（例如先复习哪个定理、做哪类题），不要泛泛而谈。\n"
        "3. 指出 1 至 3 个相对掌握较好的知识点，说明依据。\n"
        "4. 如果有模拟卷成绩，结合模拟卷得分分析失分点；没有就忽略。\n"
        "5. 给出 3 条一周内的练习计划，每条不超过 60 字。\n"
        "6. knowledge 的 code 必须来自上面统计中的编码，不要新造。\n"
        "只返回 JSON："
        '{"summary":"不超过200字的总评",'
        '"strengths":[{"code":"limit","reason":"依据"}],'
        '"weaknesses":[{"code":"derivative","reason":"薄弱原因",'
        '"suggestion":"怎么补"}],'
        '"plan":["第一步","第二步","第三步"]}'
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": body},
    ]


def generate_report(stats):
    """生成并校验报告；模型不可用或输出不可用时抛 ai.AIUnavailable。"""
    if not ai.enabled():
        raise ai.AIUnavailable("AI 学情分析未配置")
    payload = ai.complete_json(report_messages(stats), max_tokens=2200)
    return validate_report(payload, stats)


def validate_report(payload, stats):
    known = {item["code"]: item["name"] for item in stats["knowledge"]}
    summary = ai.clean_text(payload.get("summary"), MAX_REPORT_SUMMARY)
    strengths = _items(payload.get("strengths"), known, with_suggestion=False)
    weaknesses = _items(payload.get("weaknesses"), known, with_suggestion=True)
    plan = [
        ai.clean_text(item, 160)
        for item in ai.parse_list(payload.get("plan"))
        if isinstance(item, str) and item.strip()
    ][:MAX_PLAN_ITEMS]
    if not summary and not strengths and not weaknesses:
        raise ai.AIOutputError("模型未返回可用分析")
    return {
        "summary": summary,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "plan": plan,
    }


def _items(value, known, with_suggestion):
    result = []
    for item in ai.parse_list(value):
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not isinstance(code, str) or code not in known:
            continue
        entry = {
            "code": code,
            "name": known[code],
            "reason": ai.clean_text(item.get("reason"), 240),
        }
        if with_suggestion:
            entry["suggestion"] = ai.clean_text(item.get("suggestion"), 300)
        result.append(entry)
        if len(result) >= MAX_REPORT_ITEMS:
            break
    return result
