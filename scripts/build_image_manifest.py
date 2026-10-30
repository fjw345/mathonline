"""Normalize existing screenshots once into an explicit, editable manifest.
Never rewrites an existing manifest or any source screenshot.
"""

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "devided-math/数学竞赛历年题目及解析/全国大学生数学竞赛/非数"


def parse_name(stem):
    value = re.sub(r"\s+", "", stem)
    match = re.fullmatch(r"(\d+)(?:\((\d+)\)|\.(\d+))?(上|中|下)?", value)
    if not match:
        raise ValueError("Unrecognized screenshot name: " + stem)
    major, sub1, sub2, part = match.groups()
    number = major + ("." + (sub1 or sub2) if sub1 or sub2 else "")
    return number, {"上": 0, None: 0, "中": 1, "下": 2}[part]


def build(output):
    if output.exists():
        raise SystemExit(
            "Manifest already exists; edit it or choose a new --output. Existing mapping was preserved."
        )
    problems, sources, assets, issues = [], [], [], []
    for folder in sorted(BASE.iterdir()):
        if not folder.is_dir():
            continue
        year = int(folder.name[:4])
        group = "非数学 " + (
            folder.name[-1] + " 类" if folder.name[-1] in "AB" else "类"
        )
        if "补赛" in folder.name:
            group += " · " + folder.name[4:]
        sid = "cmc-nonmath-" + folder.name
        sources.append(
            {
                "id": sid,
                "name": "全国大学生数学竞赛",
                "year": year,
                "group_name": group,
                "source_url": "",
                "rights_note": "已有拆分素材；公开展示使用依据待确认",
                "verified": False,
            }
        )
        parts = {}
        for role, dirname in [("question", "题目"), ("answer", "答案")]:
            mapping = defaultdict(list)
            for image in sorted((folder / dirname).glob("*.png")):
                number, order = parse_name(image.stem)
                raw = image.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                aid = (
                    "img-"
                    + hashlib.sha256(
                        (role + str(image.relative_to(ROOT)) + digest).encode()
                    ).hexdigest()[:24]
                )
                with Image.open(image) as im:
                    width, height = im.size
                assets.append(
                    {
                        "id": aid,
                        "owner_id": None,
                        "purpose": role,
                        "path": digest + ".png",
                        "content_type": "image/png",
                        "width": width,
                        "height": height,
                        "problem_id": None,
                        "source_path": image.relative_to(ROOT).as_posix(),
                    }
                )
                mapping[number].append((order, aid))
            parts[role] = {
                n: [aid for _, aid in sorted(values)] for n, values in mapping.items()
            }
        for number, images in parts["question"].items():
            pid = sid + "-q" + number.replace(".", "-")
            answer = parts["answer"].get(number, [])
            if not answer:
                issues.append(pid + ": missing answer")
            p = {
                "id": pid,
                "source_id": sid,
                "title": f"{year} 年 {group} · 第 {number} 题",
                "type": "blank" if number.startswith("1.") else "solution",
                "year": year,
                "group_name": group,
                "subject": "高等数学",
                "tags": ["全国大学生数学竞赛·非数"],
                "difficulty": None,
                "status": "published" if answer else "draft",
                "version": 1,
                "grading_mode": "self_assessment",
                "question_images": images,
                "choice_options": [],
                "multiple": False,
                "verified": False,
                "answer_verified": False,
                "manual_revision": False,
                "answer": {
                    "images": answer,
                    "correct_options": [],
                    "provenance": "原卷配套答案截图",
                },
            }
            problems.append(p)
            for asset in assets:
                if asset["id"] in images + answer:
                    asset["problem_id"] = pid
        for number in set(parts["answer"]) - set(parts["question"]):
            issues.append(sid + ": orphan answer " + number)
    manifest = {
        "schema_version": 1,
        "sources": sources,
        "assets": assets,
        "problems": problems,
        "issues": issues,
        "review_note": "文件名规范化仅辅助建立候选对应关系；题面完整性、内容对应与使用权限尚未人工逐题核实。",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "sources": len(sources),
                "problems": len(problems),
                "assets": len(assets),
                "types": {
                    t: sum(p["type"] == t for p in problems)
                    for t in ("blank", "solution")
                },
                "issues": issues,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/nonmath-manifest.json"
    )
    build(parser.parse_args().output)
