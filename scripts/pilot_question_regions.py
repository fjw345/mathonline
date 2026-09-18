"""Create a small visual question-region pilot without changing any database."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
RESOURCE = ROOT / "resource" / "考研数学"
OUT = ROOT / "data" / "pilot_question_regions"
TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
TESSDATA = ROOT / "olddirect" / "data" / "tessdata"
RE_ANCHOR = re.compile(r"^(?:[（(]\s*(\d{1,2})\s*[）)]|\s*(\d{1,2})\s*[、.．:：])$")
SCALE = 2.5


def sha12(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def find_source(subject: str, predicate) -> Path:
    candidates = sorted((RESOURCE / subject).rglob("*.pdf"))
    matches = [path for path in candidates if predicate(path)]
    if not matches:
        raise FileNotFoundError(f"No pilot PDF found for {subject}")
    return matches[0]


def token_anchor(token: str, page_width: float) -> int | None:
    token = token.strip()
    match = RE_ANCHOR.match(token)
    if match:
        value = int(match.group(1) or match.group(2))
        return value if 1 <= value <= 40 else None
    # OCR occasionally drops punctuation. Only accept a bare number at the
    # left margin, which avoids treating formula numerals as question anchors.
    if token.isdigit() and 1 <= int(token) <= 40:
        return int(token)
    return None


def text_tokens(page: fitz.Page) -> list[dict[str, object]]:
    words = []
    for item in page.get_text("words"):
        x0, y0, x1, y1, text = item[:5]
        words.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    return words


def ocr_tokens(page: fitz.Page) -> list[dict[str, object]]:
    image = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), colorspace=fitz.csGRAY, alpha=False)
    result = subprocess.run(
        [str(TESSERACT), "stdin", "stdout", "--tessdata-dir", str(TESSDATA), "-l", "chi_sim", "--psm", "6", "-c", "tessedit_create_tsv=1"],
        input=image.tobytes("png"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    tokens = []
    lines = result.stdout.decode("utf-8", "replace").splitlines()
    for row in csv.DictReader(lines, delimiter="\t"):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row.get("conf") or -1)
            x, y, width, height = (float(row[key]) / SCALE for key in ("left", "top", "width", "height"))
        except (TypeError, ValueError):
            continue
        tokens.append({"text": text, "x0": x, "y0": y, "x1": x + width, "y1": y + height, "conf": conf})
    return tokens


def anchors_for_document(doc: fitz.Document) -> tuple[list[dict[str, object]], str, list[list[dict[str, object]]]]:
    all_tokens: list[list[dict[str, object]]] = []
    has_text = any((page.get_text("text") or "").strip() for page in doc)
    method = "text_words" if has_text else "ocr_tsv"
    for page in doc:
        all_tokens.append(text_tokens(page) if method == "text_words" else ocr_tokens(page))
    anchors: list[dict[str, object]] = []
    for page_no, tokens in enumerate(all_tokens):
        width = doc[page_no].rect.width
        for token in tokens:
            number = token_anchor(str(token["text"]), width)
            if number is None:
                continue
            # Question numbers in these papers sit in the left margin. This
            # rejects numerals in option columns and formula bodies.
            if float(token["x0"]) > width * 0.15:
                continue
            anchors.append({"page": page_no, "number": number, "x0": token["x0"], "y0": token["y0"], "x1": token["x1"], "y1": token["y1"]})
    anchors.sort(key=lambda item: (int(item["page"]), float(item["y0"]), float(item["x0"])))
    # Remove an accidental duplicate anchor on the same baseline.
    deduped: list[dict[str, object]] = []
    for anchor in anchors:
        if deduped and anchor["page"] == deduped[-1]["page"] and anchor["number"] == deduped[-1]["number"] and abs(float(anchor["y0"]) - float(deduped[-1]["y0"])) < 8:
            continue
        deduped.append(anchor)
    return deduped, method, all_tokens


def inside(token: dict[str, object], page: int, rect: fitz.Rect) -> bool:
    return page == int(token.get("page", page)) and float(token["y1"]) >= rect.y0 and float(token["y0"]) <= rect.y1 and float(token["x1"]) >= rect.x0 and float(token["x0"]) <= rect.x1


def render_region(doc: fitz.Document, boxes: list[dict[str, object]], output: Path) -> tuple[int, int]:
    parts: list[Image.Image] = []
    for box in boxes:
        page = doc[int(box["page"])]
        rect = fitz.Rect(float(box["x0"]), float(box["y0"]), float(box["x1"]), float(box["y1"])).intersect(page.rect)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), clip=rect, colorspace=fitz.csRGB, alpha=False)
        parts.append(Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB"))
    width = max(part.width for part in parts)
    height = sum(part.height for part in parts)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for part in parts:
        canvas.paste(part, (0, y))
        y += part.height
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)
    return canvas.size


def crop_boxes(doc: fitz.Document, anchors: list[dict[str, object]], all_tokens: list[list[dict[str, object]]]) -> list[list[dict[str, object]]]:
    boxes_by_question: list[list[dict[str, object]]] = []
    for index, anchor in enumerate(anchors):
        start_page = int(anchor["page"])
        next_anchor = anchors[index + 1] if index + 1 < len(anchors) else None
        boxes: list[dict[str, object]] = []
        if next_anchor is not None and int(next_anchor["page"]) == start_page:
            end_page = start_page
        elif next_anchor is not None:
            end_page = int(next_anchor["page"])
        else:
            end_page = start_page
        for page_no in range(start_page, end_page + 1):
            page = doc[page_no]
            y0 = float(anchor["y0"]) - 4 if page_no == start_page else 0
            if next_anchor is not None and page_no == int(next_anchor["page"]):
                y1 = float(next_anchor["y0"]) - 8
            elif page_no == end_page:
                # Keep the bottom of the actual page content while avoiding a
                # large blank footer where possible.
                content_bottom = max((float(token["y1"]) for token in all_tokens[page_no]), default=page.rect.height - 18)
                y1 = min(page.rect.height - 18, content_bottom + 12)
            else:
                y1 = page.rect.height
            boxes.append({"page": page_no, "x0": 0, "y0": max(0, y0), "x1": page.rect.width, "y1": min(page.rect.height, max(y0 + 20, y1))})
        boxes_by_question.append(boxes)
    return boxes_by_question


def text_for_region(doc: fitz.Document, boxes: list[dict[str, object]], tokens: list[list[dict[str, object]]], method: str) -> str:
    chunks: list[str] = []
    for box in boxes:
        page_no = int(box["page"])
        rect = fitz.Rect(float(box["x0"]), float(box["y0"]), float(box["x1"]), float(box["y1"]))
        if method == "text_words":
            chunks.append(doc[page_no].get_text("text", clip=rect).strip())
        else:
            page_tokens = [token for token in tokens[page_no] if float(token["y1"]) >= rect.y0 and float(token["y0"]) <= rect.y1]
            chunks.append(" ".join(str(token["text"]) for token in page_tokens).strip())
    return "\n".join(chunk for chunk in chunks if chunk)


def make_contact_sheet(records: list[dict[str, object]], output: Path) -> None:
    thumbs: list[tuple[Image.Image, str]] = []
    for record in records:
        image = Image.open(ROOT / record["image_path"]).convert("RGB")
        image.thumbnail((300, 400))
        thumbs.append((image, f"{record['subject']} Q{record['question_number']}"))
    columns, cell_w, cell_h = 3, 320, 440
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (image, label) in enumerate(thumbs):
        x, y = (index % columns) * cell_w, (index // columns) * cell_h
        sheet.paste(image, (x + 10, y + 26))
        draw.text((x + 10, y + 6), label, fill="black")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="PNG", optimize=True)


def main() -> None:
    sources = [
        ("数学一", find_source("数学一", lambda p: p.name == "2020年考研数学（一）真题.pdf")),
        ("数学二", find_source("数学二", lambda p: p.name.startswith("1989考研数学二真题") and "【01】" in str(p))),
        ("数学三", find_source("数学三", lambda p: p.name.startswith("2020年考研数学三真题") and "【01】" in str(p))),
    ]
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "images").mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    source_summary: list[dict[str, object]] = []
    for subject, source in sources:
        doc = fitz.open(source)
        anchors, method, all_tokens = anchors_for_document(doc)
        boxes = crop_boxes(doc, anchors, all_tokens)
        source_id = f"{subject}_{sha12(source)}"
        source_summary.append({"subject": subject, "source_path": source.relative_to(ROOT).as_posix(), "pages": len(doc), "method": method, "anchor_count": len(anchors)})
        for index, (anchor, region_boxes) in enumerate(zip(anchors, boxes), 1):
            image_path = OUT / "images" / f"{source_id}_q{index:02d}.png"
            width, height = render_region(doc, region_boxes, image_path)
            record = {
                "pilot_id": f"{source_id}_q{index:02d}",
                "subject": subject,
                "source_path": source.relative_to(ROOT).as_posix(),
                "question_number": int(anchor["number"]),
                "source_page_start": int(region_boxes[0]["page"]) + 1,
                "source_page_end": int(region_boxes[-1]["page"]) + 1,
                "boxes": region_boxes,
                "image_path": image_path.relative_to(ROOT).as_posix(),
                "image_width": width,
                "image_height": height,
                "extraction_method": method,
                "ocr_text": text_for_region(doc, region_boxes, all_tokens, method),
                "requires_review": True,
            }
            records.append(record)

    (OUT / "manifest.json").write_text(json.dumps({"sources": source_summary, "questions": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["pilot_id", "subject", "source_path", "question_number", "source_page_start", "source_page_end", "image_path", "image_width", "image_height", "extraction_method", "requires_review"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row[key] for key in fields} for row in records)
    make_contact_sheet(records, OUT / "contact_sheet.png")
    cards = []
    for row in records:
        cards.append(f'<article><h3>{html.escape(row["subject"])} Q{row["question_number"]} · 第{row["source_page_start"]}-{row["source_page_end"]}页</h3><img src="{html.escape(row["image_path"].replace(chr(92), "/"))}" loading="lazy"><pre>{html.escape(str(row["ocr_text"])[:1000])}</pre></article>')
    (OUT / "index.html").write_text("<meta charset='utf-8'><title>题目区域试验</title><style>body{font-family:Arial,sans-serif}article{display:inline-block;vertical-align:top;width:31%;margin:1%;box-sizing:border-box}img{max-width:100%;max-height:700px;border:1px solid #aaa}pre{white-space:pre-wrap;font-size:11px}</style>" + "".join(cards), encoding="utf-8")
    print(json.dumps({"sources": source_summary, "question_images": len(records), "output": str(OUT), "database_changed": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
