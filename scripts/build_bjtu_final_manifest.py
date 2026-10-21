"""Build BJTU final exam manifests offline; never import config/store or touch a DB."""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.models import ProblemRecord
from scripts.build_kaoyan_manifest import asset

BASE = ROOT / "devided-math/北交大历年期末真题"
NUMBERS = {c: n for n, c in enumerate("一二三四五六七八九十", 1)}


def parse_name(stem):
    """Keep section/subquestion distinct; never confuse 二11 with 二1 + fragment 1."""
    match = re.fullmatch(r"([一二三四五六七八九十]|\d+)(?:\((\d+)\)|(\d+))?(上|中|下)?", stem)
    if not match:
        raise ValueError(f"Unknown screenshot name: {stem}")
    major, paren, sub, part = match.groups()
    major = NUMBERS[major] if major in NUMBERS else int(major)
    number = int(paren or sub) if paren or sub else None
    return (major, number), {None: 0, "上": 0, "中": 1, "下": 2}[part], part


def label_text(label):
    major, sub = label
    return str(major) + (f".{sub}" if sub is not None else "")


def problem_id(code, year, label):
    major, sub = label
    return f"bjtu-final-{code}-{year}-s{major:02d}" + (f"-q{sub:02d}" if sub is not None else "")


def choice_keys(section):
    """Each entry describes one question; AB in a list means a multiple choice key."""
    numbers, letters = section["numbers"], section["choice_keys"]
    if (not isinstance(letters, (str, list)) or len(numbers) != len(letters)
            or len(numbers) != len(set(numbers))
            or any(not isinstance(n, int) or n < 1 for n in numbers)):
        raise ValueError("Invalid choice numbering or answer count")
    for key in letters:
        if (not isinstance(key, str) or not key or len(set(key)) != len(key)
                or (key != "?" and set(key) - set("ABCD"))):
            raise ValueError("Invalid choice key")
    return dict(zip(numbers, letters))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(output, replace=False):
    config = json.loads((ROOT / "data/bjtu-final-config.json").read_text(encoding="utf-8"))
    targets = [output / f"{c}-manifest.json" for c in config["courses"]]
    targets += [output / "pending.json", output / "inventory.json", output / "REPORT.md"]
    if not replace and any(p.exists() for p in targets):
        raise SystemExit("Output exists; preserve edits or explicitly use --replace.")
    aliases = {(a["course"], a["year"], a["role"], a["stem"]): a for a in config["aliases"]}
    singles = {(a["course"], a["year"], a["role"], a["stem"]): a for a in config["singleton_parts"]}
    used_aliases, used_singles = set(), set()
    manifests, pending, inventory, empty, coverage = {}, [], [], [], []
    for code, course in config["courses"].items():
        manifest = {"schema_version": 1, "sources": [], "assets": [], "problems": []}
        folder = BASE / course["folder"]
        populated = {p.name for p in folder.iterdir() if p.is_dir() and list(p.rglob("*.png"))}
        if populated != set(course["exams"]):
            raise ValueError(f"Exam configuration differs from populated years: {code}")
        for yd in sorted(p for p in folder.iterdir() if p.is_dir()):
            if yd.name not in populated:
                empty.append(f"{course['name']} {yd.name}")
                continue
            year, exam = int(yd.name), course["exams"][yd.name]
            parts, notes = {}, defaultdict(list)
            for role, dirname in (("question", "题目"), ("answer", "答案")):
                groups = defaultdict(list)
                for path in sorted((yd / dirname).glob("*.png")):
                    identity = (code, year, role, path.stem)
                    label, order, part = parse_name(path.stem)
                    if identity in aliases:
                        a = aliases[identity]
                        label = tuple(int(v) for v in a["label"].split("."))
                        label = (label[0], label[1] if len(label) > 1 else None)
                        used_aliases.add(identity)
                        notes[label].append(a["note"])
                    if code == "probability" and year == 2020 and path.stem.rstrip("上下中").isdigit():
                        label = (1, label[0])
                    groups[label].append((order, path, part))
                    if identity in singles:
                        used_singles.add(identity)
                        notes[label].append(singles[identity]["note"])
                parts[role] = groups
            sid = f"bjtu-final-{code}-{year}"
            manifest["sources"].append({"id": sid, "name": f"北京交通大学期末考试·{course['name']}",
                "year": year, "group_name": "期末考试", "source_url": "",
                "rights_note": "已有拆分素材；公开展示使用依据待确认", "verified": False})
            before = len(manifest["problems"])
            labels = sorted(set(parts["question"]) | set(parts["answer"]), key=lambda v: (v[0], v[1] or 0))
            keys = {}
            for major, section in exam["sections"].items():
                if section["type"] not in {"choice", "blank", "solution"}:
                    raise ValueError(f"Invalid section type: {code} {year}")
                if section["type"] == "choice":
                    for number, letter in choice_keys(section).items():
                        keys[(int(major), number)] = letter
                    if not set((int(major), n) for n in section["numbers"]) <= set(labels):
                        raise ValueError(f"Configured choice number missing: {code} {year}")
            for label in labels:
                pid = problem_id(code, year, label)
                refs, reasons = {}, []
                for role in ("question", "answer"):
                    fragments = sorted(parts[role].get(label, []))
                    if len({f[0] for f in fragments}) != len(fragments):
                        raise ValueError(f"Ambiguous image order: {pid} {role}")
                    refs[role] = [p for _, p, _ in fragments]
                    if not fragments:
                        reasons.append("缺少题目截图" if role == "question" else "缺少答案截图")
                    elif any(part for _, _, part in fragments):
                        part_set = {part for _, _, part in fragments}
                        if not {"上", "下"} <= part_set and not all((code, year, role, p.stem) in singles for _, p, _ in fragments):
                            reasons.append(f"{role} 分段缺少上片或下片")
                section = exam["sections"].get(str(label[0]), {})
                kind = section.get("type", "solution") if label[1] is not None else "solution"
                if kind == "choice":
                    if label not in keys:
                        raise ValueError(f"Choice number lacks explicit configuration: {pid}")
                    if keys[label] == "?":
                        reasons.append("正确选项尚未确认")
                if pid in config["issues"]:
                    reasons.append(config["issues"][pid])
                record = {"id": pid, "course": code, "year": year, "original_number": label_text(label),
                    "type": kind, "notes": list(dict.fromkeys(notes[label])),
                    **{role: [p.relative_to(ROOT).as_posix() for p in paths] for role, paths in refs.items()}}
                record["disposition"] = "pending" if reasons else "ready"
                inventory.append(record)
                if reasons:
                    pending.append({**record, "reasons": reasons})
                    continue
                assets = {r: [asset(p, r, pid) for p in paths] for r, paths in refs.items()}
                p = ProblemRecord.model_validate({"id": pid, "source_id": sid,
                    "title": f"{year} 年北交大{course['name']}期末 · 第 {label_text(label)} 题",
                    "type": kind, "year": year, "group_name": "期末考试", "subject": course["name"],
                    "tags": [f"北交期末·{course['name']}"], "status": "published",
                    "grading_mode": "choice" if kind == "choice" else "self_assessment",
                    "question_images": [a["id"] for a in assets["question"]],
                    "choice_options": list("ABCD") if kind == "choice" else [],
                    "multiple": kind == "choice" and len(keys[label]) > 1,
                    "answer": {"images": [a["id"] for a in assets["answer"]],
                        "correct_options": list(keys[label]) if kind == "choice" else [],
                        "provenance": "同科目、同年份原卷配套截图；显式题号映射及上中下顺序。选择题按答案字母或表达式对照选项。" + "".join(record["notes"]) + "未完成逐题数学审校。"}
                }).model_dump()
                manifest["problems"].append(p)
                manifest["assets"].extend(assets["question"] + assets["answer"])
            coverage.append((course["name"], year, len(labels), len(manifest["problems"]) - before))
        manifests[code] = manifest
    if used_aliases != set(aliases) or used_singles != set(singles):
        raise ValueError("Unused manual mapping exception; inspect configuration")
    # Account for every screenshot exactly once, even pending/orphan materials.
    accounted = [p for row in inventory for role in ("question", "answer") for p in row[role]]
    actual = {p.relative_to(ROOT).as_posix() for p in BASE.rglob("*.png")}
    if len(accounted) != len(set(accounted)) or set(accounted) != actual:
        raise ValueError("Screenshot coverage mismatch")
    output.mkdir(parents=True, exist_ok=True)
    for code, manifest in manifests.items():
        write_json(output / f"{code}-manifest.json", manifest)
    write_json(output / "pending.json", {"note": "待补/待核对材料，不可直接导入", "problems": pending})
    write_json(output / "inventory.json", {"schema_version": 1, "screenshots": len(actual), "empty_directories": empty,
        "note": "按现有截图统计；未据此声称原卷所有题目齐全。", "problems": inventory})
    report = ["# 北交期末题库导入清单", "", "本次仅生成清单，未写入本地或 Supabase 数据库，未上传图片。", "",
        "完整、可配置判题的题目默认发布；不设例行预审。缺图、选项未确认及明确内容矛盾单独列在 pending.json。已核对题型和选择题键值；并未完成全部截图的数学正确性审校。", "",
        "| 科目 | 有素材年份 | 可导入 | 待核对 | 题图/答案图 |", "| --- | --- | ---: | ---: | ---: |"]
    for code, manifest in manifests.items():
        course = config["courses"][code]
        report.append(f"| {course['name']} | {', '.join(course['exams'])} | {len(manifest['problems'])} | {sum(r['course']==code for r in pending)} | {len(manifest['assets'])} |")
        print(code, len(manifest['problems']), 'problems;', len(manifest['assets']), 'images;', dict(Counter(p['type'] for p in manifest['problems'])))
    report += ["", f"全量盘点 {len(actual)} 张截图、{len(inventory)} 个候选题目。所有截图均进入可导入清单或待核对记录，无遗漏。", "",
        "## 文件与规则", "", "- 四份 *-manifest.json 可以直接交给 scripts/import_bank.py；不要导入 pending.json、inventory.json 或配置文件。",
        "- 稳定 ID：bjtu-final-{科目代码}-{年份}-s{大题号}[-q{小题号}]；保留原卷大题/小题层级，不连续重编号。",
        "- 概率论 2020 年的 1—15 文件是选择小题，映射为 s01-q01 至 s01-q15；原始路径保留不变。",
        "- 标签按课程区分：北交期末·微积分上、北交期末·微积分下、北交期末·概率论、北交期末·线性代数。导入后筛选自动出现。",
        "- 单片带“上/下”的例外及连续题号别名已逐项记录在 ../bjtu-final-config.json；没有全局删除尾号或跨年猜配。",
        "- 已确认概率论 2021 六19、2022 三16 与对应大题一致；微积分上 2019 三上、四下包含完整解法和最终结果，作为单片保留。",
        "- 选择题正确选项取自配套答案字母或答案表达式与选项的对应；? 保留在待核对队列。概率论 2019 第 2.1 题配套推导同时满足 A、B，配置为多选。",
        "", "## 后续导入", "", "确认目标 .env 后执行以下命令（会写库并上传图片；本次未执行）：", "", "```powershell",
        *[f"python -X utf8 scripts/import_bank.py data/bjtu-finals/{code}-manifest.json" for code in manifests], "```", "",
        "导入器自动读取根目录 .env，相同内容幂等跳过，管理员人工修改默认受保护。无须重跑旧 SQL 或修改表结构。", "",
        "## 扩充与修改", "", "维护 ../bjtu-final-config.json 的逐卷题型、正确选项与映射例外，再运行：", "", "```powershell",
        "python -X utf8 scripts/build_bjtu_final_manifest.py --replace", "```", "",
        "默认拒绝覆盖输出。使用 --replace 前请保存对生成清单的人工修改。扩充年份或课程时同步补充配置，未知年份会停止构建，不会套用其他试卷的题型。",
        "", "## 空目录（不计作试卷）", "", *[f"- {v}" for v in empty], "", "## 逐卷覆盖", "",
        "统计范围是本次已有素材；不会仅因大题号连续就认定原卷齐全。", "",
        "| 科目 | 年份 | 候选题 | 可导入 | 待核对 |", "| --- | --- | ---: | ---: | ---: |",
        *[f"| {name} | {year} | {count} | {ready} | {count-ready} |" for name, year, count, ready in coverage],
        "", "## 待核对明细", "", *[f"- {r['id']}：{'；'.join(r['reasons'])}" for r in pending], ""]
    (output / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print('Ready:',sum(len(m['problems']) for m in manifests.values()), 'Pending:',len(pending),'Screenshots:',len(actual))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/bjtu-finals")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    build(args.output, args.replace)
