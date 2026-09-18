"""Probe newly added 考研数学试卷 PDFs without changing the question bank."""
from __future__ import annotations

import csv
import json
import re
import subprocess
import tempfile
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[1]
RESOURCE = ROOT / "resource" / "考研数学"
DATA = ROOT / "data"
TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
TESSDATA = DATA / "tessdata"
EXAM_DIRS = [RESOURCE / "数学一", RESOURCE / "数学二", RESOURCE / "数学三"]


def text_stats(path: Path) -> dict[str, object]:
    doc = fitz.open(path)
    pages = []
    for page in doc:
        pages.append(page.get_text("text") or "")
    chars = [len(text.strip()) for text in pages]
    total = sum(chars)
    if total == 0:
        status = "no_text_layer"
    elif total < 100:
        status = "likely_answer_or_unusable_text"
    else:
        status = "text_extractable"
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "pages": len(pages),
        "text_chars": total,
        "nonempty_pages": sum(bool(text.strip()) for text in pages),
        "status": status,
    }


def ocr_sample(path: Path) -> dict[str, object]:
    doc = fitz.open(path)
    page_chars: list[int] = []
    preview = ""
    with tempfile.TemporaryDirectory(prefix="mathonline-new-exam-ocr-") as temp:
        temp_dir = Path(temp)
        for number, page in enumerate(doc, 1):
            image = temp_dir / f"page-{number:03d}.png"
            page.get_pixmap(matrix=fitz.Matrix(2.5, 2.5), colorspace=fitz.csGRAY, alpha=False).save(image)
            result = subprocess.run(
                [str(TESSERACT), str(image), "stdout", "--tessdata-dir", str(TESSDATA), "-l", "chi_sim", "--psm", "6", "quiet"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            text = result.stdout.decode("utf-8", "replace").strip()
            page_chars.append(len(text))
            if not preview and text:
                preview = text[:500]
    questions = len(re.findall(r"(?:^|\n)\s*(?:\d{1,2}[、.)]|[一二三四五六七八九十]+、)", preview))
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "pages": len(doc),
        "ocr_chars": sum(page_chars),
        "ocr_nonempty_pages": sum(char > 0 for char in page_chars),
        "ocr_question_markers_in_preview": questions,
        "preview": preview,
    }


def main() -> None:
    pdfs = sorted(path for directory in EXAM_DIRS for path in directory.rglob("*.pdf"))
    stats = [text_stats(path) for path in pdfs]
    by_dir: dict[str, dict[str, int]] = {}
    for directory in EXAM_DIRS:
        subset = [row for row in stats if row["path"].startswith(directory.relative_to(ROOT).as_posix() + "/")]
        by_dir[directory.name] = {
            "pdfs": len(subset),
            "pages": sum(int(row["pages"]) for row in subset),
            "text_extractable": sum(row["status"] == "text_extractable" for row in subset),
            "likely_answer_or_unusable_text": sum(row["status"] == "likely_answer_or_unusable_text" for row in subset),
            "no_text_layer": sum(row["status"] == "no_text_layer" for row in subset),
        }

    samples: list[dict[str, object]] = []
    for directory in EXAM_DIRS:
        candidates = [
            path for path, row in zip(pdfs, stats)
            if path.is_relative_to(directory) and row["status"] == "no_text_layer" and "真题" in str(path)
        ]
        if candidates:
            samples.append(ocr_sample(candidates[0]))

    report = {
        "scope": "resource/考研数学/数学一、数学二、数学三",
        "pdf_count": len(pdfs),
        "page_count": sum(int(row["pages"]) for row in stats),
        "by_directory": by_dir,
        "text_status_counts": {
            status: sum(row["status"] == status for row in stats)
            for status in ("text_extractable", "likely_answer_or_unusable_text", "no_text_layer")
        },
        "files": stats,
        "ocr_samples": samples,
        "database_changed": False,
    }
    output = DATA / "new_exam_resource_probe.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    unreadable = DATA / "new_exam_resource_unreadable.csv"
    with unreadable.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "pages", "text_chars", "nonempty_pages", "status"])
        writer.writeheader()
        writer.writerows(row for row in stats if row["status"] != "text_extractable")
    print(json.dumps({key: report[key] for key in ("pdf_count", "page_count", "by_directory", "text_status_counts", "ocr_samples", "database_changed")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
