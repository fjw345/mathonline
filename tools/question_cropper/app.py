"""Local, non-destructive PDF region editor for a five-page trial."""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
import mimetypes
import re
import threading
import time
import zipfile
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import fitz
from PIL import Image

from sample import DEFAULT_DATA, ROOT, ensure_sample

STATIC = Path(__file__).parent / "static"
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
RENDER_LOCK = threading.Lock()  # PyMuPDF does not support concurrent threads.


class Conflict(ValueError):
    pass


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")


def atomic_write(path, body):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def pixel_box(bbox, size):
    w, h = size
    x0, y0, x1, y1 = bbox
    return max(0, math.floor(x0*w)), max(0, math.floor(y0*h)), min(w, math.ceil(x1*w)), min(h, math.ceil(y1*h))


class Store:
    def __init__(self, data_dir=DEFAULT_DATA):
        self.data_dir = Path(data_dir).resolve()
        self.sample = ensure_sample(self.data_dir)
        self.pages = {p["id"]: p for p in self.sample["pages"]}
        self.documents = {d["id"]: d for d in self.sample["documents"]}
        self.lock = threading.RLock()
        self.path = self.data_dir / "annotations.json"
        self.source_paths = {}
        for doc in self.documents.values():
            source = (ROOT / doc["path"]).resolve()
            source.relative_to((ROOT / "resource").resolve())
            if hashlib.sha256(source.read_bytes()).hexdigest() != doc["sha256"]:
                raise ValueError(f"原卷已改变，不能套用旧坐标：{doc['name']}")
            self.source_paths[doc["id"]] = source
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
            self.validate(self.state)
        else:
            self.state = dict(schema_version=1, revision=0, questions=copy.deepcopy(self.sample["questions"]),
                              page_notes={p: "" for p in self.pages}, updated_at=None)
            atomic_write(self.path, json_bytes(self.state))

    def validate(self, payload):
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("标注格式版本不正确")
        if type(payload.get("revision")) is not int or payload["revision"] < 0:
            raise ValueError("缺少保存版本")
        questions = payload.get("questions")
        if not isinstance(questions, list) or len(questions) > 500:
            raise ValueError("题目列表不正确或超过测试上限")
        qids, rids = set(), set()
        for q in questions:
            if not isinstance(q, dict):
                raise ValueError("题目格式不正确")
            qid = q.get("id", "")
            if not isinstance(qid, str) or not SAFE_ID.fullmatch(qid) or qid in qids:
                raise ValueError("题目 ID 无效或重复")
            qids.add(qid)
            if q.get("document_id") not in self.documents:
                raise ValueError("题目来源不正确")
            for key, limit in (("number", 40), ("answer", 2000), ("note", 4000)):
                if not isinstance(q.get(key), str) or len(q[key]) > limit:
                    raise ValueError(f"{key} 内容无效或过长")
            if not q["number"].strip() or q.get("kind") not in {"choice", "blank", "solution", "unknown"}:
                raise ValueError("请填写题号并选择题型")
            if type(q.get("reviewed")) is not bool:
                raise ValueError("复核状态不正确")
            if not isinstance(q.get("tags"), list) or len(q["tags"]) > 30 or any(not isinstance(t, str) or len(t) > 100 for t in q["tags"]):
                raise ValueError("标签不正确")
            regions = q.get("regions")
            if not isinstance(regions, list) or not 1 <= len(regions) <= 30:
                raise ValueError("每题需要 1–30 个区域")
            if not any(r.get("role") == "stem" for r in regions if isinstance(r, dict)):
                raise ValueError("每道题至少保留一个题干区域")
            for r in regions:
                if not isinstance(r, dict):
                    raise ValueError("区域格式错误")
                rid = r.get("id", "")
                if not isinstance(rid, str) or not SAFE_ID.fullmatch(rid) or rid in rids:
                    raise ValueError("区域 ID 无效或重复")
                rids.add(rid)
                page = self.pages.get(r.get("page_id"))
                if not page or page["document_id"] != q["document_id"]:
                    raise ValueError("区域只能来自同一份原卷")
                box = r.get("bbox")
                if not isinstance(box, list) or len(box) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in box):
                    raise ValueError("裁剪坐标必须在原页范围内")
                if box[2] - box[0] < .002 or box[3] - box[1] < .002:
                    raise ValueError("裁剪区域过小或边界顺序错误")
                if r.get("role") not in {"stem", "answer", "explanation"}:
                    raise ValueError("区域类型不正确")
        notes = payload.get("page_notes")
        if not isinstance(notes, dict) or any(p not in self.pages or not isinstance(v, str) or len(v) > 4000 for p, v in notes.items()):
            raise ValueError("页面备注不正确")

    def get(self):
        with self.lock:
            return dict(sample=self.sample, state=copy.deepcopy(self.state))

    def save(self, payload):
        self.validate(payload)
        with self.lock:
            if payload["revision"] != self.state["revision"]:
                raise Conflict("另一个窗口已保存修改。本窗口修改仍在浏览器备份中；请下载本地草稿后刷新核对。")
            candidate = dict(schema_version=1, revision=self.state["revision"] + 1,
                             questions=copy.deepcopy(payload["questions"]), page_notes=copy.deepcopy(payload["page_notes"]),
                             updated_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            atomic_write(self.data_dir / "annotations.previous.json", json_bytes(self.state))
            atomic_write(self.path, json_bytes(candidate))
            self.state = candidate
            return dict(revision=candidate["revision"], updated_at=candidate["updated_at"])

    @lru_cache(maxsize=12)
    def page_png(self, page_id, quality="preview"):
        page = self.pages[page_id]
        scale = min(3.0, 5000 / max(page["width"], page["height"])) if quality == "export" else 1800 / max(page["width"], page["height"])
        with RENDER_LOCK:
            with fitz.open(self.source_paths[page["document_id"]]) as pdf:
                pix = pdf[page["number"] - 1].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
                return pix.tobytes("png")

    def report(self, state):
        originals = {q["id"]: q for q in self.sample["questions"]}
        original_regions = {r["id"]: r for q in originals.values() for r in q["regions"]}
        regions = [r for q in state["questions"] for r in q["regions"]]
        return dict(total_questions=len(state["questions"]), confirmed=sum(q["reviewed"] for q in state["questions"]),
                    added_questions=sum(q["id"] not in originals for q in state["questions"]),
                    removed_questions=len(set(originals) - {q["id"] for q in state["questions"]}),
                    changed_regions=sum(r["id"] in original_regions and r != original_regions[r["id"]] for r in regions),
                    added_regions=sum(r["id"] not in original_regions for r in regions), page_notes=state["page_notes"])

    def export(self):
        with self.lock:
            state = copy.deepcopy(self.state)
        confirmed = [q for q in state["questions"] if q["reviewed"]]
        if not confirmed:
            raise ValueError("请先确认至少一道题，再导出。")
        buffer = io.BytesIO()
        page_images = {}
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            manifest = dict(schema_version=1, revision=state["revision"], coordinate_system="normalized displayed PDF page; 0..1; x0,y0,x1,y1",
                            documents=self.sample["documents"], pages=self.sample["pages"], questions=[])
            for q in confirmed:
                item = copy.deepcopy(q)
                item["files"] = {}
                for role in ("stem", "answer", "explanation"):
                    pieces = []
                    for r in q["regions"]:
                        if r["role"] != role:
                            continue
                        if r["page_id"] not in page_images:
                            page_images[r["page_id"]] = Image.open(io.BytesIO(self.page_png(r["page_id"], "export"))).convert("RGB")
                        source = page_images[r["page_id"]]
                        piece = source.crop(pixel_box(r["bbox"], source.size))
                        part_name = f"images/{q['id']}/{r['id']}.png"
                        out = io.BytesIO(); piece.save(out, "PNG"); archive.writestr(part_name, out.getvalue())
                        next(z for z in item["regions"] if z["id"] == r["id"])["image"] = part_name
                        pieces.append(piece)
                    if pieces:
                        combined = Image.new("RGB", (max(p.width for p in pieces), sum(p.height for p in pieces) + 24*(len(pieces)-1)), "white")
                        top = 0
                        for piece in pieces:
                            combined.paste(piece, (0, top)); top += piece.height + 24
                        out = io.BytesIO(); combined.save(out, "PNG")
                        name = f"images/{q['id']}/{role}.png"
                        archive.writestr(name, out.getvalue()); item["files"][role] = name
                manifest["questions"].append(item)
            archive.writestr("questions.json", json_bytes(manifest))
            archive.writestr("all-annotations.json", json_bytes(state))
            archive.writestr("trial-report.json", json_bytes(self.report(state)))
            archive.writestr("README.txt", "仅导出人工确认的题图。题干、答案、解析分开保存。\nquestions.json 保留原卷、页码、坐标、顺序与文件关联。\n确认表示边界已核对，不代表标准答案已核实或可正式发布。\n原 PDF 不包含在此包内，请保留 resource 原文件。\n")
        body = buffer.getvalue()
        self.data_dir.joinpath("exports").mkdir(exist_ok=True)
        # Revision-specific exports cannot overwrite an earlier correction round.
        atomic_write(self.data_dir / "exports" / f"trial-r{state['revision']}.zip", body)
        return body


class Handler(BaseHTTPRequestHandler):
    store: Store

    def log_message(self, fmt, *args):
        if len(args) > 1 and str(args[1]) not in {"200", "304"}:
            super().log_message(fmt, *args)

    def reply(self, body, content_type="application/json; charset=utf-8", status=200, attachment=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def error(self, message, status=400):
        self.reply(json_bytes(dict(error=message)), status=status)

    def local_request(self):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        return host in allowed and (origin is None or origin == f"http://{host}") and self.headers.get("Sec-Fetch-Site") != "cross-site"

    def do_GET(self):
        if not self.local_request():
            return self.error("仅接受本机同源访问", 403)
        path = urlsplit(self.path).path
        try:
            if path == "/api/state":
                return self.reply(json_bytes(self.store.get()))
            if path == "/favicon.ico":
                return self.reply(b"", "image/x-icon", 204)
            if path.startswith("/api/pages/") and path.endswith(".png"):
                page_id = path[len("/api/pages/"):-4]
                if page_id not in self.store.pages:
                    return self.error("页面不存在", 404)
                return self.reply(self.store.page_png(page_id), "image/png")
            files = {"/": "index.html", "/static/app.js": "app.js", "/static/style.css": "style.css"}
            if path in files:
                file = STATIC / files[path]
                return self.reply(file.read_bytes(), (mimetypes.guess_type(file.name)[0] or "text/plain") + "; charset=utf-8")
            return self.error("页面不存在", 404)
        except Exception as exc:
            self.error(str(exc), 500)

    def do_POST(self):
        if not self.local_request() or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.error("仅接受本机 JSON 请求", 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_000_000:
                return self.error("请求大小不正确", 413)
            payload = json.loads(self.rfile.read(length))
            path = urlsplit(self.path).path
            if path == "/api/save":
                return self.reply(json_bytes(self.store.save(payload)))
            if path == "/api/export":
                if payload.get("revision") != self.store.get()["state"]["revision"]:
                    raise Conflict("请先保存最新修改再导出")
                return self.reply(self.store.export(), "application/zip", attachment="question-crops.zip")
            self.error("接口不存在", 404)
        except Conflict as exc:
            self.error(str(exc), 409)
        except (ValueError, TypeError, KeyError) as exc:
            self.error(str(exc))
        except Exception as exc:
            self.error(f"操作失败，修改未确认保存：{exc}", 500)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    args = parser.parse_args()
    Handler.store = Store(args.data_dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Question cropper: http://127.0.0.1:{args.port}/", flush=True)
    print(f"Corrections: {Handler.store.path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
