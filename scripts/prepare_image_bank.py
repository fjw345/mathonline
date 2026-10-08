"""Resume-safe inventory, original-page extraction, and review workspaces.

This pipeline never rebuilds the existing SQLite bank or overwrites corrections.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import re
from collections import Counter
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "image_bank"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def inspect():
    OUT.mkdir(parents=True, exist_ok=True)
    backup = OUT / "backups" / "trial-r53"
    backup.mkdir(parents=True, exist_ok=True)
    for name in ("sample.json", "annotations.json", "annotations.previous.json"):
        source = ROOT / "data" / "cropper_trial" / name
        if source.exists():
            target = backup / name
            if not target.exists():
                shutil.copy2(source, target)
    inventory = []
    for path in sorted((ROOT / "resource").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix()
        item = dict(id="d-" + hashlib.sha256(relative.encode()).hexdigest()[:20],
                    path=relative, name=path.name, sha256=digest(path),
                    format=path.suffix.lower().lstrip("."), bytes=path.stat().st_size,
                    included=True, monthly="月考" in path.name, status="pending")
        if path.suffix.lower() == ".pdf":
            errors = []
            try:
                with fitz.open(path) as pdf:
                    item["pages"] = len(pdf)
                    item["page_details"] = []
                    for number, page in enumerate(pdf, 1):
                        detail = dict(number=number, width=page.rect.width, height=page.rect.height)
                        try:
                            detail["text_chars"] = len(page.get_text())
                            page.get_pixmap(matrix=fitz.Matrix(.15, .15), alpha=False)
                            detail["renderable"] = True
                        except Exception as exc:
                            detail.update(renderable=False, error=str(exc))
                            errors.append(dict(page=number, error=str(exc)))
                        item["page_details"].append(detail)
                item["status"] = "renderable" if not errors else "render_errors"
                item["errors"] = errors
            except Exception as exc:
                item.update(status="open_error", error=str(exc))
        else:
            item["status"] = "needs_pdf_conversion"
        inventory.append(item)
        print(f"INVENTORY {len(inventory)} {item['status']} {path.name}", flush=True)
    write_json(OUT / "inventory.json", dict(schema_version=1, scope="all resource files, including monthly exams", files=inventory))
    print(json.dumps(dict(files=len(inventory), formats=dict(Counter(x["format"] for x in inventory)),
                          states=dict(Counter(x["status"] for x in inventory)),
                          pages=sum(x.get("pages", 0) for x in inventory)), ensure_ascii=False))
    trial = json.loads((ROOT / "data" / "cropper_trial" / "sample.json").read_text("utf-8"))
    scan = next(x for x in trial["documents"] if x["id"] == "scan")
    with fitz.open(ROOT / scan["path"]) as pdf:
        pdf[3].get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False).save(OUT / "scan-p4.png")


def fix_trial():
    folder = ROOT / "data" / "cropper_trial"
    sample = json.loads((folder / "sample.json").read_text("utf-8"))
    state = json.loads((folder / "annotations.json").read_text("utf-8"))
    question = next(q for q in state["questions"] if q["id"] == "scan-q10")
    if any(r["page_id"] == "scan-p4" for r in question["regions"]):
        print("Trial cross-page fix already present")
        return
    backup = OUT / "backups" / f"trial-r{state['revision']}-before-cross-page-fix"
    backup.mkdir(parents=True, exist_ok=True)
    for name in ("sample.json", "annotations.json", "annotations.previous.json"):
        if not (backup / name).exists():
            shutil.copy2(folder / name, backup / name)
    doc = next(d for d in sample["documents"] if d["id"] == "scan")
    with fitz.open(ROOT / doc["path"]) as pdf:
        page = pdf[3]
        sample["pages"].append(dict(id="scan-p4", document_id="scan", number=4,
                                   width=page.rect.width, height=page.rect.height,
                                   rotation=page.rotation, has_text=bool(page.get_text().strip())))
    question["regions"].append(dict(id="scan-q10-r2", page_id="scan-p4",
                                     bbox=[.11, .086, .92, .192], role="stem"))
    question["reviewed"] = False
    question["note"] = (question["note"] + "\n已从原卷第4页补齐 B/C/D 选项；原有人工边界保留。新增片段待检查。").strip()
    state["page_notes"]["scan-p4"] = "第10题 B/C/D 选项延续页；仅补本题，不替换其他人工修正。"
    shutil.copy2(folder / "annotations.json", folder / "annotations.previous.json")
    state["revision"] += 1
    write_json(folder / "sample.json", sample)
    write_json(folder / "annotations.json", state)
    import sys
    sys.path.insert(0, str(ROOT / "tools" / "question_cropper"))
    from app import Store, pixel_box
    from PIL import Image
    import io
    store = Store(folder)
    pieces = [Image.open(io.BytesIO(store.page_png(r["page_id"], "export"))).convert("RGB") for r in question["regions"]]
    pieces = [p.crop(pixel_box(r["bbox"], p.size)) for p, r in zip(pieces, question["regions"])]
    combined = Image.new("RGB", (max(p.width for p in pieces), sum(p.height for p in pieces) + 24), "white")
    offset = 0
    for piece in pieces:
        combined.paste(piece, (0, offset))
        offset += piece.height + 24
    combined.save(OUT / "scan-q10-fixed.png")
    print(f"Trial revision {state['revision']}; only scan-q10 changed")


def profile():
    inventory = json.loads((OUT / "inventory.json").read_text("utf-8"))["files"]
    summary, examples = [], []
    for item in inventory:
        path = ROOT / item["path"] if item["format"] == "pdf" else OUT / "converted" / f"{item['id']}.pdf"
        if not path.exists():
            continue
        with fitz.open(path) as pdf:
            counts = []
            texts = []
            for page in pdf:
                text = page.get_text()
                counts.append(len(text))
                if len(texts) < 2:
                    texts.append(text[:2500])
            summary.append(dict(id=item["id"], name=item["name"], pages=len(pdf),
                                sparse=sum(n < 80 for n in counts), chars=sum(counts)))
            if len(examples) < 30 or item["name"] in ["26数一真题.pdf", "26数一参考答案.pdf", "2024考研数学二真题试卷.pdf"]:
                examples.append(dict(name=item["name"], path=item["path"], text=texts))
    write_json(OUT / "profile.json", dict(summary=summary, examples=examples))
    print(json.dumps(dict(documents=len(summary), pages=sum(x['pages'] for x in summary), sparse=sum(x['sparse'] for x in summary)), ensure_ascii=False))
    for example in examples[:10] + examples[-3:]:
        print(example["name"], repr(example["text"][0][:1600]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "fix-trial", "profile"])
    args = parser.parse_args()
    if args.command == "inspect":
        inspect()
    elif args.command == "fix-trial":
        fix_trial()
    elif args.command == "profile":
        profile()


if __name__ == "__main__":
    main()
