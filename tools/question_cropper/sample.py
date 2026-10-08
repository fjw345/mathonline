"""A deliberately small, visually prepared trial; no OCR accuracy claim."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / "data" / "cropper_trial"


def build_sample() -> dict:
    specs = [
        ("single", "e682f0a58108f2afcf557a1e", "01 · 单栏与跨页", [1, 2],
         "2019 考研数一", "先检查第 1–10 题。第 6 题的 C、D 选项在下一页。", ["考研数一", "2019年"]),
        ("scan", "8e7d45bf5ac5e37f8ec8f797", "02 · 扫描件", [2, 3],
         "2026 考研数一", "重点看积分上下限和根号。第 5 题的选项在下一页。", ["考研数一", "2026年"]),
        ("columns", "71ff67a37b0267fe0b357db6", "03 · 双栏页面", [1],
         "2024 考研数二", "先读左栏再读右栏。放大检查矩阵边缘和小字号公式。", ["考研数二", "2024年"]),
    ]
    documents, pages, questions = [], [], []
    with sqlite3.connect(f"{(ROOT / 'data/problem_bank.sqlite3').as_uri()}?mode=ro", uri=True) as db:
        for key, source_id, title, numbers, name, hint, tags in specs:
            row = db.execute("SELECT path FROM problem_sources WHERE id=? AND included=1", (source_id,)).fetchone()
            if row is None:
                raise ValueError(f"样本来源未找到：{name}")
            path = (ROOT / row[0]).resolve()
            path.relative_to((ROOT / "resource").resolve())
            documents.append(dict(id=key, source_id=source_id, title=title, name=name, hint=hint,
                                  path=path.relative_to(ROOT).as_posix(), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), tags=tags))
            with fitz.open(path) as pdf:
                for number in numbers:
                    page = pdf[number - 1]
                    pages.append(dict(id=f"{key}-p{number}", document_id=key, number=number,
                                      width=page.rect.width, height=page.rect.height,
                                      rotation=page.rotation, has_text=bool(page.get_text().strip())))

    def add(doc, number, boxes, kind="choice"):
        qid = f"{doc}-q{number}"
        questions.append(dict(id=qid, document_id=doc, number=str(number), kind=kind,
                              answer="", tags=next(d["tags"][:] for d in documents if d["id"] == doc),
                              reviewed=False, note="", regions=[dict(id=f"{qid}-r{i+1}", page_id=f"{doc}-p{p}",
                              bbox=bbox, role="stem") for i, (p, bbox) in enumerate(boxes)]))

    # Coordinates are relative to the displayed PDF page, 0..1, not screen pixels.
    # Seeds combine PDF text positions with visual inspection of the original page.
    for n, (top, bottom) in enumerate([(0.152, .209), (.216, .311), (.317, .475),
                                     (.485, .624), (.628, .757), (.762, .987)], 1):
        boxes = [(1, [.07, top, .965, bottom])]
        if n == 6:
            boxes.append((2, [.085, .063, .935, .099]))
        add("single", n, boxes)
    for n, (top, bottom) in enumerate([(.103, .212), (.217, .324), (.354, .405),
                                     (.409, .449), (.45, .498), (.499, .540),
                                     (.541, .605), (.608, .705), (.729, .861)], 7):
        add("single", n, [(2, [.085, top, .945, bottom])], "choice" if n <= 8 else "blank" if n <= 14 else "solution")
    for n, (top, bottom) in enumerate([(.148, .285), (.300, .414), (.425, .603), (.610, .83), (.834, .9)], 1):
        boxes = [(2, [.11, top, .905, bottom])]
        if n == 5:
            boxes.append((3, [.11, .087, .905, .146]))
        add("scan", n, boxes)
    for n, (top, bottom) in enumerate([(.149, .322), (.326, .570), (.575, .684), (.688, .832), (.836, .925)], 6):
        add("scan", n, [(3, [.11, top, .92, bottom])])
    for n, (top, bottom) in enumerate([(274, 329), (331, 406), (407, 486), (489, 588), (590, 738)], 1):
        add("columns", n, [(1, [.055, top / 841.92, .489, bottom / 841.92])])
    for n, (top, bottom) in enumerate([(100, 229), (232, 416), (418, 529), (530, 580),
                                     (581, 630), (678, 712), (713, 746)], 6):
        add("columns", n, [(1, [.502, top / 841.92, .950, bottom / 841.92])], "blank" if n >= 11 else "choice")
    return dict(schema_version=1, title="切题小规模测试", documents=documents, pages=pages,
                seed_method="PDF 文字位置辅助 + 原图视觉初划；扫描件为视觉初划。仅用于验证人工修正流程。",
                questions=questions)


def ensure_sample(data_dir: Path = DEFAULT_DATA) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "sample.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    sample = build_sample()
    with path.open("x", encoding="utf-8") as file:
        json.dump(sample, file, ensure_ascii=False, indent=2)
    return sample


if __name__ == "__main__":
    result = ensure_sample()
    print(f"Prepared {len(result['pages'])} pages, {len(result['questions'])} questions in {DEFAULT_DATA}")
