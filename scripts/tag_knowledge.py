"""为题库批量写入知识点标签与内部难度。

默认只补缺失标签，不清空已有标签；人工标注（knowledge_source=manual）除非显式
--override-manual，否则跳过。AI 未配置、请求失败或输出不合规时自动用关键词规则
兜底，保证每道题至少有一个知识点。只更新 knowledge_points / knowledge_source /
difficulty 三列，不改题面、答案、图片、版本和学习记录。

用法（先本地，再线上；线上执行前务必先 --dry-run）：
    python -X utf8 scripts/tag_knowledge.py --dry-run --limit 20
    python -X utf8 scripts/tag_knowledge.py --workers 6
    APP_MODE=supabase python -X utf8 scripts/tag_knowledge.py --dry-run
"""

import argparse
import json
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import ai, config, tagging, topics  # noqa: E402
from backend.store import store  # noqa: E402

MAX_SAMPLE = 8


def select_targets(problems, retag, override_manual):
    return tagging.select_targets(problems, retag, override_manual)


def run(problems, answers, workers, use_ai):
    results = []
    lock = threading.Lock()
    counter = {"done": 0}

    def work(problem):
        answer_text = answers.get(problem["id"], "")
        tag = tagging.tag_problem(problem, answer_text, use_ai=use_ai)
        with lock:
            counter["done"] += 1
            if counter["done"] % 20 == 0 or counter["done"] == len(problems):
                print("  已标注 %d / %d" % (counter["done"], len(problems)), flush=True)
        return {
            "id": problem["id"],
            "subject": problem.get("subject") or "",
            "knowledge_points": tag["knowledge_points"],
            "difficulty": tag["difficulty"],
            "knowledge_source": tag["source"],
            "model": tag["model"],
        }

    if workers <= 1:
        for problem in problems:
            results.append(work(problem))
        return results
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, problem) for problem in problems]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def summarize(rows, problems, answers):
    by_subject = defaultdict(Counter)
    sources = Counter()
    difficulty = Counter()
    invalid = []
    for row in rows:
        sources[row["knowledge_source"]] += 1
        difficulty[str(row["difficulty"])] += 1
        by_subject[row["subject"]]["total"] += 1
        if row["knowledge_points"]:
            by_subject[row["subject"]]["tagged"] += 1
        for code in row["knowledge_points"]:
            if not topics.knows(code):
                invalid.append({"id": row["id"], "code": code})
    coverage = {}
    for subject, counts in sorted(by_subject.items()):
        coverage[subject] = {
            "selected": counts["total"],
            "tagged": counts["tagged"],
        }
    return {
        "processed": len(rows),
        "sources": dict(sources),
        "difficulty": dict(sorted(difficulty.items())),
        "coverage": coverage,
        "invalid": invalid,
        "ai_enabled": ai.enabled(),
        "model": config.AI_MODEL if ai.enabled() else "",
        "problems_in_bank": len(problems),
        "problems_with_text": sum(
            1 for p in problems if (p.get("question_text") or "").strip()
        ),
        "answers_with_text": sum(1 for text in answers.values() if (text or "").strip()),
    }


def main():
    parser = argparse.ArgumentParser(description="批量标注题库知识点与内部难度")
    parser.add_argument("--dry-run", action="store_true", help="只统计与抽样，不写库")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少道题")
    parser.add_argument("--subject", default="", help="只处理指定学科，如 线性代数")
    parser.add_argument("--workers", type=int, default=4, help="AI 并发数，默认 4")
    parser.add_argument("--retag", action="store_true", help="重算已有 AI 标签")
    parser.add_argument(
        "--override-manual", action="store_true", help="覆盖人工标注（慎用）"
    )
    parser.add_argument("--no-ai", action="store_true", help="只用关键词规则")
    parser.add_argument("--report", type=Path, help="把汇总 JSON 写到指定路径")
    parser.add_argument(
        "--strict-ai",
        action="store_true",
        help="配置了 AI 却没有任何 AI 标签时以非零码退出",
    )
    args = parser.parse_args()

    problems = store.list("problems")
    if args.subject:
        problems = [p for p in problems if p.get("subject") == args.subject]
    answers = {a["problem_id"]: a.get("text", "") for a in store.list("problem_answers")}
    targets = select_targets(problems, args.retag, args.override_manual)
    targets.sort(key=lambda p: (p.get("subject") or "", p.get("year") or 0, p["id"]))
    if args.limit > 0:
        targets = targets[: args.limit]

    print(
        "环境 %s；题库 %d 道，本次待标注 %d 道；模型 %s"
        % (
            config.MODE,
            len(problems),
            len(targets),
            config.AI_MODEL if ai.enabled() and not args.no_ai else "未启用（关键词兜底）",
        ),
        flush=True,
    )
    if not targets:
        print("没有需要标注的题目。")
        return 0

    rows = run(targets, answers, max(1, args.workers), not args.no_ai)
    summary = summarize(rows, problems, answers)

    if args.dry_run:
        print("干跑模式，不写库。前 %d 条示例：" % min(MAX_SAMPLE, len(rows)))
        for row in rows[:MAX_SAMPLE]:
            print(
                "  %s %s -> %s (难度 %s, 来源 %s)"
                % (
                    row["id"],
                    row["subject"],
                    ",".join(row["knowledge_points"]),
                    row["difficulty"],
                    row["knowledge_source"],
                )
            )
    else:
        updated = 0
        items = [
            {
                "id": row["id"],
                "knowledge_points": row["knowledge_points"],
                "difficulty": row["difficulty"],
                "knowledge_source": row["knowledge_source"],
            }
            for row in rows
        ]
        for start in range(0, len(items), 100):
            updated += store.apply_knowledge(items[start : start + 100])
        summary["updated"] = updated
        print("已写入 %d 道题的知识点与难度。" % updated)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.strict_ai and summary["ai_enabled"] and not summary["sources"].get("ai"):
        print("配置了模型但没有任何 AI 标注，请检查模型配置与连通性。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
