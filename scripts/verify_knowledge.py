"""只读核对题库知识点标注与组卷准备情况，适合部署前后各跑一次。

不写数据库、不调用模型。默认在发现未标注题目或非法编码时返回非零码，
方便部署流程把它当作门禁；临时排查可加 --allow-incomplete。
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config, mockexam, topics  # noqa: E402
from backend.store import store  # noqa: E402


def summarize(problems, answers):
    by_subject = defaultdict(
        lambda: {"total": 0, "tagged": 0, "valid": 0, "untagged": []}
    )
    sources = Counter()
    knowledge = Counter()
    difficulty = Counter()
    invalid = []
    for problem in problems:
        subject = problem.get("subject") or "未分类"
        bucket = by_subject[subject]
        bucket["total"] += 1
        codes = problem.get("knowledge_points") or []
        valid = [code for code in codes if topics.knows(code)]
        if codes:
            bucket["tagged"] += 1
        else:
            bucket["untagged"].append(problem["id"])
        for code in codes:
            if not topics.knows(code):
                invalid.append({"id": problem["id"], "code": code})
                continue
            knowledge[code] += 1
        if problem.get("knowledge_source"):
            sources[problem["knowledge_source"]] += 1
        level = topics.clamp_difficulty(problem.get("difficulty"))
        difficulty[str(level or "未标注")] += 1
        bucket["valid"] += len(valid)
    subjects = {}
    for subject, bucket in sorted(by_subject.items()):
        ready = bucket["total"] >= mockexam.MIN_ITEMS and all(
            (p.get("question_text") or "").strip()
            for p in problems
            if (p.get("subject") or "未分类") == subject
        )
        subjects[subject] = {
            "total": bucket["total"],
            "tagged": bucket["tagged"],
            "coverage": round(bucket["tagged"] / max(1, bucket["total"]), 4),
            "untagged": bucket["untagged"][:10],
            "mock_ready": ready,
        }
    return {
        "mode": config.MODE,
        "problems": len(problems),
        "answers_with_text": sum(
            1 for row in answers if (row.get("text") or "").strip()
        ),
        "coverage": round(
            sum(1 for p in problems if p.get("knowledge_points")) / max(1, len(problems)),
            4,
        ),
        "sources": dict(sources),
        "invalid": invalid[:20],
        "difficulty": dict(sorted(difficulty.items())),
        "knowledge": [
            {"code": code, "name": topics.name_for(code), "count": count}
            for code, count in knowledge.most_common()
        ],
        "subjects": subjects,
        "mock_subjects": [
            item
            for item in mockexam.subjects_overview(problems)
            if item["subject"] in mockexam.SUBJECTS
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="只读核对知识点标注与模拟卷准备度")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="存在未标注题目时仍返回 0",
    )
    args = parser.parse_args()
    problems = store.list("problems")
    answers = store.list("problem_answers")
    report = summarize(problems, answers)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            "环境 %s：题库 %d 道，覆盖率 %.1f%%，有文字答案 %d 道"
            % (
                report["mode"],
                report["problems"],
                report["coverage"] * 100,
                report["answers_with_text"],
            )
        )
        print("标注来源：", report["sources"] or "无")
        print("难度分布：", report["difficulty"])
        print("知识点分布：")
        for item in report["knowledge"]:
            print("  %-22s %-16s %d" % (item["code"], item["name"], item["count"]))
        print("分学科：")
        for subject, item in report["subjects"].items():
            print(
                "  %-8s %4d 道，覆盖 %5.1f%%，组卷准备 %s%s"
                % (
                    subject,
                    item["total"],
                    item["coverage"] * 100,
                    "是" if item["mock_ready"] else "否",
                    "；未标注示例 " + ",".join(item["untagged"])
                    if item["untagged"]
                    else "",
                )
            )
        untagged = sum(
            1 for p in problems if not (p.get("knowledge_points") or [])
        )
        if untagged or report["invalid"]:
            print(
                "检查未通过：%d 道未标注，%d 个非法编码"
                % (untagged, len(report["invalid"])),
                file=sys.stderr,
            )
            if not args.allow_incomplete:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
