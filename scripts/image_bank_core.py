"""Coordinates and conservative associations for an offline image bank.

All automatic crops and answer associations remain drafts. OCR is used only
for coordinates and hints; pixels always come from the original PDF page.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import subprocess
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "image_bank"
ENGINE_VERSION = 1
TESS = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
Q_START = re.compile(r"^\s*(\d{1,2})\s*[.、:．]\s*(.*)")
Q_PAREN = re.compile(r"^\s*[（(](\d{1,2})[）)]\s*(.*)")
SECTION = re.compile(r"^[一二三四五六七八九十]+\s*[、.．:：]\s*(.*(?:选择|单选|填空|解答|计算|证明|综合|判断).*)")
SOLUTION = re.compile(r"^\s*(?:【(?:答案|解析|解答)】|\[(?:答案|解析)\]|解[：:]|证[：:]|证明[：:]|答[：:]|解析[：:]|答案[：:]|解答[：:])")
YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def normalize(text):
    return unicodedata.normalize("NFKC", text).strip()


def native_lines(page):
    result = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = line["spans"]
            text = "".join(span["text"] for span in spans).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                result.append(dict(text=normalize(text), bbox=[x0/page.rect.width, y0/page.rect.height,
                                                            x1/page.rect.width, y1/page.rect.height], method="text"))
    return result


def ocr_lines(page, cache):
    if cache.exists():
        return json.loads(cache.read_text("utf-8"))["lines"]
    if not TESS.exists():
        raise RuntimeError("Tesseract is unavailable")
    with tempfile.TemporaryDirectory(prefix="mathonline-coordinate-") as raw:
        path = Path(raw) / "page.png"
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), colorspace=fitz.csGRAY, alpha=False)
        pix.save(path)
        command = [str(TESS), str(path), "stdout", "--tessdata-dir", str(ROOT / "data" / "tessdata"),
                   "-l", "chi_sim", "--psm", "3", "quiet", "tsv"]
        process = subprocess.run(command, capture_output=True, timeout=120)
        if process.returncode:
            raise RuntimeError(process.stderr.decode("utf-8", "replace")[-500:])
        words = defaultdict(list)
        for word in csv.DictReader(io.StringIO(process.stdout.decode("utf-8", "replace")), delimiter="\t"):
            if word.get("text", "").strip():
                words[(word["block_num"], word["par_num"], word["line_num"])].append(word)
        lines = []
        for group in words.values():
            x0 = min(int(w["left"]) for w in group)
            y0 = min(int(w["top"]) for w in group)
            x1 = max(int(w["left"])+int(w["width"]) for w in group)
            y1 = max(int(w["top"])+int(w["height"]) for w in group)
            lines.append(dict(text=normalize(" ".join(w["text"] for w in group)),
                              bbox=[x0/pix.width, y0/pix.height, x1/pix.width, y1/pix.height],
                              confidence=sum(float(w["conf"]) for w in group)/len(group), method="ocr"))
    write_json(cache, dict(lines=lines, engine="tesseract-chi_sim-psm3"))
    return lines


def kind_of(text, fallback="unknown"):
    if "选择" in text or "单选" in text:
        return "choice"
    if "填空" in text:
        return "blank"
    if any(word in text for word in ("解答", "计算", "证明", "综合")):
        return "solution"
    return fallback


def identity(item):
    name, path = item["name"], item["path"]
    years = YEAR.findall(name)
    single_year = years[0] if len(set(years)) == 1 else None
    if not single_year and re.match(r"^26数[一二三]", name):
        single_year = "2026"
    if "考研" in path:
        volume = re.search(r"(?:数学|数|卷)\s*[（(]?([一二三])", name)
        if not volume:
            volume = re.search(r"数学\s*[（(]([一二三])", path)
        category = "考研数" + (volume.group(1) if volume else "待确定")
    elif "全国大学生数学竞赛" in path:
        category = "全国大学生数学竞赛" + ("非数学" if "非数" in name else "数学")
        category += "A" if "A" in name else "B" if "B" in name else ""
        edition = re.search(r"(?:第)?(\d{1,2})届", name)
        if edition and not re.search(r"\d+-\d+届", name):
            single_year = str(2008 + int(edition.group(1)))
    elif "北京市" in path:
        category = "北京市大学生数学竞赛"
    elif "数学竞赛" in path:
        category = "北交大数学竞赛"
    else:
        category = "北交月考" if item.get("monthly") else "北交期末考"
    return dict(category=category, year=single_year, complete=bool(single_year),
                collection=not bool(single_year), group=None)


def question_marker(line):
    text = line["text"]
    match = Q_START.match(text)
    if match and 1 <= int(match.group(1)) <= 60:
        return int(match.group(1)), match.group(2)
    # Parenthesized numbers only at a main left margin, not nested proof steps.
    match = Q_PAREN.match(text)
    if match and line["bbox"][0] < .15 and len(match.group(2)) > 4:
        return int(match.group(1)), match.group(2)
    return None


def reading_lanes(lines):
    markers = [line for line in lines if question_marker(line)]
    left = sum(line["bbox"][0] < .2 for line in markers)
    right = sum(.45 < line["bbox"][0] < .65 for line in markers)
    crossing = sum(line["bbox"][0] < .35 and line["bbox"][2] > .7 and len(line["text"]) > 15 for line in lines)
    if left >= 2 and right >= 2 and crossing < 3:
        return [(0.025, .495), (.495, .985)]
    return [(0.025, .985)]


def detect(item):
    cache = OUT / "detections" / f"{item['id']}.json"
    pdfpath = ROOT / item["path"] if item["format"] == "pdf" else OUT / "converted" / f"{item['id']}.pdf"
    if not pdfpath.exists():
        return dict(item=item, status="conversion_pending", pages=[], questions=[], issues=[dict(type="missing_pdf")])
    if cache.exists():
        previous = json.loads(cache.read_text("utf-8"))
        if previous.get("sha256") == item["sha256"] and previous.get("engine_version") == ENGINE_VERSION:
            return previous
    ident = identity(item)
    pages, questions, issues = [], [], []
    current, section, segment = None, "unknown", 0
    segment_identity = dict(ident)
    occurrences = Counter()
    with fitz.open(pdfpath) as pdf:
        for index, page in enumerate(pdf):
            pageid = f"{item['id']}-p{index+1}"
            lines = native_lines(page)
            if sum(len(line["text"]) for line in lines) < 80:
                try:
                    lines = ocr_lines(page, OUT / "ocr_coordinates" / item["id"] / f"p{index+1}.json")
                except Exception as exc:
                    issues.append(dict(type="ocr_error", page=index+1, details=str(exc)))
            usable = [line for line in lines if .04 < line["bbox"][1] < .955]
            pageinfo = dict(id=pageid, document_id=item["id"], number=index+1, width=page.rect.width,
                            height=page.rect.height, rotation=page.rotation, has_text=bool(page.get_text().strip()),
                            method="ocr" if any(l["method"] == "ocr" for l in lines) else "text")
            pages.append(pageinfo)
            lanes = reading_lanes(usable)
            for lane_index, (left, right) in enumerate(lanes):
                ordered = sorted([l for l in usable if left <= l["bbox"][0] < right], key=lambda l:(round(l["bbox"][1], 3), l["bbox"][0]))
                anchors = [l for l in ordered if question_marker(l)]
                if anchors:
                    # Reject indented formula list numbers. Real question starts share a margin.
                    margin = min(l["bbox"][0] for l in anchors)
                    anchors = [l for l in anchors if l["bbox"][0] <= margin+.065]
                anchor_ids = {id(l) for l in anchors}
                events = []
                for line in ordered:
                    if id(line) in anchor_ids:
                        events.append((line, "question"))
                    elif SECTION.match(line["text"]):
                        events.append((line, "section"))
                    elif SOLUTION.match(line["text"]):
                        events.append((line, "solution"))
                    elif (YEAR.search(line["text"]) and len(line["text"]) < 130 and
                          any(word in line["text"] for word in ("考试", "试题", "真题", "试卷", "学期", "竞赛")) and
                          not question_marker(line)):
                        events.append((line, "identity"))
                    elif re.search(r"(?:参考答案|答案与解析|答案及解析|参考解答|试题解答)", line["text"]) and len(line["text"]) < 100:
                        events.append((line, "answer_heading"))
                boundaries = [(line["bbox"][1], event, line) for line, event in events]
                cursor = max(.04, min((l["bbox"][1] for l in ordered), default=.04)-.01)
                for y, event, line in boundaries + [(.95, "end", None)]:
                    if current and y > cursor+.003:
                        # Include tall math on the first text line, but stay above the next question.
                        top = max(.02, cursor-.009)
                        bottom = max(top+.003, min(.96, y-.002))
                        region = dict(id=f"{current['id']}-r{len(current['regions'])+1}", page_id=pageid,
                                      bbox=[left, top, right, bottom], role=current["active_role"])
                        current["regions"].append(region)
                        content = [l["text"] for l in ordered if cursor-.003 <= l["bbox"][1] < y-.002]
                        current["text_by_role"][current["active_role"]].extend(content)
                    if event == "question":
                        number, rest = question_marker(line)
                        # A repeated number after an answer heading is an answer record.
                        occurrence_key = (segment, section, number)
                        occurrences[occurrence_key] += 1
                        qid = f"{item['id']}-s{segment}-{section}-q{number}-o{occurrences[occurrence_key]}"
                        role = "answer" if section == "answers" else "stem"
                        current = dict(id=qid, document_id=item["id"], number=str(number), kind=section if section in {"choice","blank","solution"} else "unknown",
                                       answer="", tags=[segment_identity["category"]] + ([segment_identity["year"]+"年"] if segment_identity.get("year") else []),
                                       reviewed=False, note="自动初切，边界与答案分别待核对。", regions=[],
                                       active_role=role, text_by_role={"stem":[],"answer":[],"explanation":[]},
                                       identity=dict(segment_identity), section=section, segment=segment,
                                       boundary_status="pending", answer_status="unmatched", grading_status="unverified")
                        questions.append(current)
                    elif event == "section":
                        section = kind_of(line["text"])
                        current = None
                    elif event == "identity":
                        years = YEAR.findall(line["text"])
                        if years:
                            new_year = years[0]
                            if new_year != segment_identity.get("year") or ident["collection"]:
                                segment += 1
                                segment_identity = dict(ident, year=new_year, complete=True, heading=line["text"])
                                group = re.search(r"([甲乙AB])\s*(?:组|卷)", line["text"])
                                if group:
                                    segment_identity["group"] = group.group(1)
                                section, current = "unknown", None
                    elif event == "answer_heading":
                        section, current = "answers", None
                    elif event == "solution" and current:
                        current["active_role"] = "explanation"
                    cursor = y
                if not anchors and not current:
                    issues.append(dict(type="no_question_anchor", page=index+1, lane=lane_index,
                                       details="本页未定位题号，保留原页待核对；可能为封面、答案表或延续页。"))
    # Remove tiny starts. Retain answer-only blocks in separate association data.
    records = []
    for q in questions:
        q.pop("active_role", None)
        if not q["regions"]:
            issues.append(dict(type="empty_question", number=q["number"]))
            continue
        q["text_hint"] = "\n".join(q["text_by_role"]["stem"])[:16000]
        q["answer_hint"] = "\n".join(q["text_by_role"]["answer"] + q["text_by_role"]["explanation"])[:16000]
        q.pop("text_by_role")
        if q["kind"] == "unknown" and "考研" in ident["category"] and ident.get("year") and int(ident["year"]) >= 2021:
            n = int(q["number"])
            q["kind"] = "choice" if n <= 10 else "blank" if n <= 16 else "solution"
        choice = re.search(r"(?:故选|应选|选|答案[：:]|答[：:])\s*[（(]?\s*([A-D])\s*[）)]?", q["answer_hint"])
        if choice:
            q["answer"] = choice.group(1)
        if any(r["role"] in {"answer","explanation"} for r in q["regions"]) and any(r["role"] == "stem" for r in q["regions"]):
            q["answer_status"] = "inline_candidate"
        # Solutions / answer tables must never silently become practice stems.
        q["suspected_answer_in_stem"] = bool(re.search(r"【答案】|故选|参考答案|答案[：:]", q["text_hint"]))
        records.append(q)
    result = dict(item=item, sha256=item["sha256"], engine_version=ENGINE_VERSION, status="detected",
                  pdf_path=pdfpath.relative_to(ROOT).as_posix(), pdf_sha256=hashlib.sha256(pdfpath.read_bytes()).hexdigest(),
                  pages=pages, questions=records, issues=issues, identity=ident)
    write_json(cache, result)
    print(f"DETECTED {item['name']} {len(pages)} pages {len(records)} blocks", flush=True)
    return result


def associate(results):
    stems, answers = [], []
    for result in results:
        for q in result["questions"]:
            (stems if any(r["role"] == "stem" for r in q["regions"]) else answers).append(q)
    candidates = defaultdict(list)
    for answer in answers:
        ident = answer["identity"]
        if ident.get("complete"):
            candidates[(ident["category"], ident["year"], ident.get("group"), answer["number"])].append(answer)
    for stem in stems:
        if stem["answer_status"] != "unmatched":
            continue
        ident = stem["identity"]
        if not ident.get("complete"):
            continue
        pool = candidates[(ident["category"], ident["year"], ident.get("group"), stem["number"])]
        # Repeated numbers in chapters are ambiguous unless the type is also known.
        same_type = [a for a in pool if a["kind"] == stem["kind"] and stem["kind"] != "unknown"]
        if same_type:
            pool = same_type
        if len(pool) == 1:
            answer = pool[0]
            stem["answer_candidates"] = [dict(record_id=answer["id"], document_id=answer["document_id"],
                                               regions=answer["regions"], evidence="same exam category/year/group/number; content not yet verified")]
            stem["answer_status"] = "candidate_pending_content_check"
        elif pool:
            stem["answer_candidates"] = [dict(record_id=a["id"], document_id=a["document_id"]) for a in pool]
            stem["answer_status"] = "ambiguous"
    return stems, answers


def export_images(sample, state, folder):
    """Persist separate original-page crops without holding the full bank in RAM."""
    documents = {d["id"]:d for d in sample["documents"]}
    pages = {p["id"]:p for p in sample["pages"]}
    pdfs, manifest = {}, []
    for q in state["questions"]:
        item = dict(q, files={})
        target = folder / q["id"]
        target.mkdir(parents=True, exist_ok=True)
        for role in ("stem", "answer", "explanation"):
            pieces = []
            for region in q["regions"]:
                if region["role"] != role:
                    continue
                pageinfo = pages[region["page_id"]]
                docid = pageinfo["document_id"]
                if docid not in pdfs:
                    pdfs[docid] = fitz.open(ROOT / documents[docid]["path"])
                page = pdfs[docid][pageinfo["number"]-1]
                rect = page.rect
                box = fitz.Rect(*(v * (rect.width if i%2 == 0 else rect.height) for i,v in enumerate(region["bbox"])))
                pix = page.get_pixmap(matrix=fitz.Matrix(2.5,2.5), clip=box, alpha=False, colorspace=fitz.csRGB)
                piece = Image.frombytes("RGB", (pix.width,pix.height), pix.samples)
                filename = target / f"{region['id']}.png"
                piece.save(filename)
                pieces.append(piece)
            if pieces:
                combined = Image.new("RGB",(max(p.width for p in pieces),sum(p.height for p in pieces)+24*(len(pieces)-1)),"white")
                y = 0
                for piece in pieces:
                    combined.paste(piece,(0,y)); y += piece.height+24
                filename = target / f"{role}.png"
                combined.save(filename)
                item["files"][role] = filename.relative_to(OUT).as_posix()
        manifest.append(item)
    for pdf in pdfs.values():
        pdf.close()
    return manifest
