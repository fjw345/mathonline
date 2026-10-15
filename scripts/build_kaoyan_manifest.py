"""Build import-ready postgraduate exam banks, without accessing a database.

Existing output is protected unless --replace is explicitly supplied.
Unknown or contradictory choice keys and missing images go into a separate queue.
"""
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.models import ProblemRecord


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_part(stem):
    m = re.fullmatch(r"(\d+)(上|中|下)?(\d+)?", stem)
    if not m:
        raise ValueError(f"Unrecognized screenshot name: {stem}")
    return int(m[1]), {None: 0, "上": 0, "中": 1, "下": 100}[m[2]] + int(m[3] or 0)


def scan(folder):
    result = {}
    for role, dirname in (("question", "题目"), ("answer", "答案")):
        groups = {}
        for path in (folder / dirname).glob("*.png"):
            number, order = parse_part(path.stem)
            groups.setdefault(number, []).append((order, path))
        for number, parts in groups.items():
            if len({order for order, _ in parts}) != len(parts):
                raise ValueError(f"Ambiguous fragment order: {folder}, {number}")
        result[role] = {n: [p for _, p in sorted(parts)] for n, parts in groups.items()}
    return result


def asset(path, role, pid):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    relative = path.relative_to(ROOT).as_posix()
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        width, height = im.size
    return {
        "id": "img-" + hashlib.sha256((role + relative + digest).encode()).hexdigest()[:24],
        "owner_id": None, "purpose": role, "path": digest + ".png",
        "content_type": "image/png", "width": width, "height": height,
        "problem_id": pid, "source_path": relative,
    }


def build(output, replace=False):
    targets = [output / f"math{i}-manifest.json" for i in (1, 2, 3)]
    targets += [output / "pending.json", output / "REPORT.md"]
    if not replace and any(p.exists() for p in targets):
        raise SystemExit("Output exists; edit it directly or regenerate with --replace.")
    keys = read_json(ROOT / "data/kaoyan-choice-answers.json")
    mapping = read_json(ROOT / "data/math2_2010_2026/manifest.json")
    math2 = {q["id"]: q for q in mapping["questions"]}
    base = ROOT / "devided-math/考研数学"
    pending, stats, manifests = [], [], []
    for bank, name in ((1, "一"), (2, "二"), (3, "三")):
        folder = next(p for p in base.iterdir() if f"({name})" in p.name)
        manifest = {"schema_version": 1, "sources": [], "assets": [], "problems": []}
        for year in range(2010, 2027):
            parts = scan(folder / str(year))
            last, choices, blanks_end = (23, 8, 14) if year < 2021 else (22, 10, 16)
            expected = set(range(1, last + 1))
            if (set(parts["question"]) | set(parts["answer"])) - expected:
                raise ValueError(f"Unexpected numbering: math{bank} {year}")
            letters = keys[str(bank)][str(year)].replace(" ", "")
            if len(letters) != choices or set(letters) - set("ABCD?"):
                raise ValueError(f"Invalid answer key: math{bank} {year}")
            sid = f"kaoyan-math{bank}-{year}"
            group = f"数学{name}"
            manifest["sources"].append({
                "id": sid, "name": f"全国硕士研究生招生考试·{group}", "year": year,
                "group_name": group, "source_url": "", "verified": False,
                "rights_note": "已有拆分素材；公开展示使用依据待确认",
            })
            ready = 0
            for number in range(1, last + 1):
                pid = f"{sid}-{number:02d}"
                paths = {r: parts[r].get(number, []) for r in ("question", "answer")}
                provenance = "原卷配套答案截图，按年份与题号关联；未完成逐题内容校验。"
                if bank == 2 and paths["question"] and paths["answer"]:
                    original = math2[pid]
                    for role, field in (("question", "stem"), ("answer", "answer")):
                        regions = sorted(original[field], key=lambda r: r["order"])
                        ordered = [ROOT / r["image"] for r in regions]
                        if set(ordered) != set(paths[role]):
                            raise ValueError(f"Region mapping differs: {pid} {role}")
                        for path, region in zip(ordered, regions):
                            if hashlib.sha256(path.read_bytes()).hexdigest() != region["image_sha256"]:
                                raise ValueError(f"Region checksum changed: {path}")
                        paths[role] = ordered
                    provenance += "数学二沿用拆分清单中的原始页码、裁剪顺序和校验值。"
                    if year == 2016:
                        provenance += "采用2016新东方原卷，未使用误标为2016年的2014卷。"
                    if year in (2024, 2026):
                        provenance += "现有素材含简答，部分题目无完整解析。"
                reasons = []
                if not paths["question"]:
                    reasons.append("缺少题目截图")
                if not paths["answer"]:
                    reasons.append("缺少答案截图")
                if number <= choices and letters[number - 1] == "?":
                    reasons.append(keys.get("conflicts", {}).get(pid, "正确选项尚未确认"))
                if reasons:
                    pending.append({"id": pid, "bank": bank, "year": year, "number": number,
                                    "reasons": reasons, **{r: [p.relative_to(ROOT).as_posix() for p in ps] for r, ps in paths.items()}})
                    continue
                assets = {r: [asset(p, r, pid) for p in ps] for r, ps in paths.items()}
                kind = "choice" if number <= choices else "blank" if number <= blanks_end else "solution"
                problem = ProblemRecord.model_validate({
                    "id": pid, "source_id": sid, "title": f"{year} 年考研{group} · 第 {number} 题",
                    "type": kind, "year": year, "group_name": group,
                    "subject": "高等数学、线性代数" + ("、概率论与数理统计" if bank != 2 else ""),
                    "tags": [f"考研{group}"], "status": "published",
                    "grading_mode": "choice" if kind == "choice" else "self_assessment",
                    "question_images": [a["id"] for a in assets["question"]],
                    "choice_options": list("ABCD") if kind == "choice" else [],
                    "answer": {"images": [a["id"] for a in assets["answer"]],
                               "correct_options": [letters[number - 1]] if kind == "choice" else [],
                               "provenance": provenance},
                }).model_dump()
                manifest["problems"].append(problem)
                manifest["assets"].extend(assets["question"] + assets["answer"])
                ready += 1
            stats.append((bank, year, last, ready, last - ready))
        manifests.append(manifest)
    # Finish all validation before writing any output; no store or credentials loaded.
    for target, manifest in zip(targets[:3], manifests):
        write_json(target, manifest)
    write_json(output / "pending.json", {"note": "仅用于补充和核对，不可直接导入。", "problems": pending})
    report = ["# 考研题库导入清单", "", "范围：2010—2026 年数学一、二、三。仅生成文件，未写入本地或云端数据库。", "",
              "完整且可配置判题的题目默认发布；缺图、选项未确认或答案矛盾的题目保留在 pending.json，不猜配。截图内容并未逐题全面审校，发现问题后仍可通过网站反馈。", "",
              "| 题库 | 可导入 | 暂缺/待补 | 图片 |", "| --- | ---: | ---: | ---: |"]
    for b, m in enumerate(manifests, 1):
        types = Counter(p["type"] for p in m["problems"])
        report.append(f"| 数学{b} | {len(m['problems'])} | {sum(s[4] for s in stats if s[0] == b)} | {len(m['assets'])} |")
        print(f"math{b}: {len(m['problems'])} problems, {len(m['assets'])} images, {dict(types)}")
    report += ["", "## 导入", "", "在项目根目录、已配置目标环境变量的 PowerShell 窗口执行。以下命令会向当前 APP_MODE 指定的数据库导入，并上传图片：", "", "```powershell",
               *[f"python -X utf8 scripts/import_bank.py data/kaoyan/math{b}-manifest.json" for b in (1, 2, 3)], "```", "",
               "Supabase 环境配置见 [部署指南](../../docs/supabase-vercel-guide.md)。无需重新执行旧 SQL 迁移；这里只新增题库数据。当前会话未执行上述导入。", "",
               "## 扩充与修改", "", "- 稳定 ID 使用 kaoyan-math{组别}-{年份}-{两位题号}。不要为已有题目更换 ID。",
               "- 修改图片、顺序、标签或答案可编辑生成的 manifest，后台人工修改默认受导入器保护。不要随意使用 --override-manual。",
               "- 新增截图后可修改构建脚本的年份范围；选择题答案维护在 ../kaoyan-choice-answers.json，? 表示待补。",
               "- 重新生成：python -X utf8 scripts/build_kaoyan_manifest.py --replace。会覆盖这三个生成文件和报告，先保存对生成文件的人工修改。",
               "- 数学二沿用 data/math2_2010_2026/manifest.json 的裁剪顺序与校验值，该原始映射不是网站导入格式。",
               "- 未导入的截图完整路径保存在 pending.json；补齐后使用相同 ID 重新生成和导入。",
               "", "## 逐年覆盖", "", "| 题库 | 年份 | 应有 | 可导入 | 待补 |", "| --- | --- | ---: | ---: | ---: |",
               *[f"| 数学{b} | {y} | {total} | {ready} | {missing} |" for b, y, total, ready, missing in stats],
               "", "## 待补明细", "", *[f"- {p['id']}：{'；'.join(p['reasons'])}" for p in pending], ""]
    (output / "REPORT.md").write_text("\n".join(report), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/kaoyan")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    build(args.output, args.replace)
