"""Local PDF question region cropper.

The tool deliberately keeps all state in this directory.  It never writes to
the repository's resource folders or to the old question-bank database.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import uuid
import zipfile
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
from fastapi import FastAPI, File, HTTPException, UploadFile, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image


ROOT = Path(__file__).resolve().parent
PROJECTS = ROOT / "projects"
EXPORTS = ROOT / "exports"
STATIC = ROOT / "static"
TOOL_VERSION = "0.1.0"
WORKSPACE = ROOT.parents[1]
LOCK = threading.RLock()  # PyMuPDF is not thread safe; serialize its operations.
COORDINATES = "PyMuPDF unrotated page, top-left origin, point (1/72 inch), 1-based page"
MAX_UPLOAD_BYTES = 150 * 1024 * 1024
PROJECTS.mkdir(exist_ok=True)
EXPORTS.mkdir(exist_ok=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(value: str) -> str:
    name = Path(value or "source.pdf").name
    name = re.sub(r"[^\w .()\-\u4e00-\u9fff]", "_", name).strip(" .")
    return (name or "source.pdf")[:180]


def project_dir(project_id: str) -> Path:
    if not re.fullmatch(r"proj-[a-f0-9]{12}", project_id):
        raise HTTPException(400, "项目编号无效")
    path = PROJECTS / project_id
    if path.is_symlink() or path.resolve().parent != PROJECTS.resolve():
        raise HTTPException(400, "项目路径越界")
    if not path.is_dir():
        raise HTTPException(404, "项目不存在")
    return path


def read_project(project_id: str) -> dict[str, Any]:
    path = project_dir(project_id) / "project.json"
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"项目文件损坏: {exc}") from exc
    source = project_dir(project_id) / "source.pdf"
    warnings = []
    project["source_blocked"] = not source.exists() or sha256(source) != project["source_sha256"]
    if project["source_blocked"]:
        warnings.append("项目 PDF 副本丢失或哈希变化，已停止预览和导出。请重新导入正确 PDF。")
    original = Path(project.get("source_path", ""))
    if original.is_absolute():
        if not original.is_file():
            warnings.append("原 PDF 已移动或无法访问；继续使用导入时保存的、哈希一致的副本。")
        elif sha256(original) != project["source_sha256"]:
            warnings.append("原 PDF 哈希已变化；继续使用导入时保存的、哈希一致的副本。")
    project["source_warning"] = "\n".join(warnings)
    project["excluded"] = "月考" in project.get("source_path", "") or "月考" in project.get("source_pdf", "")
    project["exclude_reason"] = "路径或文件名含“月考”，已排除导出。" if project["excluded"] else ""
    return project


def write_project(project: dict[str, Any]) -> None:
    path = project_dir(project["id"]) / "project.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def page_metadata(pdf: Path) -> list[dict[str, Any]]:
    with fitz.open(pdf) as doc:
        result = []
        for index, page in enumerate(doc):
            rotation = page.rotation
            page.set_rotation(0)  # Normalize only the in-memory view, never save PDF.
            result.append({"page": index + 1, "width": page.rect.width,
                           "height": page.rect.height, "rotation": rotation})
        return result


def make_project(project_id: str, original_name: str, source: Path) -> dict[str, Any]:
    pages = page_metadata(source)
    stamp = now()
    return {
        "id": project_id,
        "tool_version": TOOL_VERSION,
        "created_at": stamp,
        "updated_at": stamp,
        "name": Path(original_name).stem or project_id,
        "source_pdf": safe_filename(original_name),
        "source_path": safe_filename(original_name),
        "source_sha256": sha256(source),
        "subject": "",
        "year": "",
        "copyright_status": "待人工核验",
        "coordinate_system": COORDINATES,
        "excluded": "月考" in original_name,
        "exclude_reason": "路径或文件名含“月考”，已排除导出。" if "月考" in original_name else "",
        "pages": pages,
        "questions": [],
    }


def validate_project(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    questions = payload.get("questions")
    if not isinstance(questions, list):
        raise HTTPException(422, "questions 必须是数组")
    page_info = {p["page"]: p for p in current["pages"]}
    clean_questions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(questions, 1):
        if not isinstance(raw, dict):
            raise HTTPException(422, "题目数据无效")
        qid = str(raw.get("id") or f"q-{uuid.uuid4().hex[:10]}")
        if not re.fullmatch(r"q-[a-f0-9]{10,32}", qid) or qid in seen:
            raise HTTPException(422, "题目编号无效或重复")
        seen.add(qid)
        regions = raw.get("regions") or []
        if not isinstance(regions, list):
            raise HTTPException(422, "区域数据无效")
        clean_regions: list[dict[str, Any]] = []
        for region in regions:
            try:
                page = int(region["page"])
                x0, y0, x1, y1 = (float(v) for v in region.get("bbox_pt", []))
            except (KeyError, TypeError, ValueError):
                raise HTTPException(422, "区域坐标无效")
            if page not in page_info or not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or not (x1 > x0 and y1 > y0):
                raise HTTPException(422, "区域超出页面范围")
            info = page_info[page]
            if x0 < 0 or y0 < 0 or x1 > info["width"] + .001 or y1 > info["height"] + .001:
                raise HTTPException(422, "区域超出页面范围")
            x0, x1 = max(0.0, min(x0, info["width"])), max(0.0, min(x1, info["width"]))
            y0, y1 = max(0.0, min(y0, info["height"])), max(0.0, min(y1, info["height"]))
            if x1 - x0 < 1 or y1 - y0 < 1:
                raise HTTPException(422, "区域太小")
            clean_regions.append({
                "page": page,
                "bbox_pt": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
                "page_width": info["width"],
                "page_height": info["height"],
                "rotation": info["rotation"],
            })
        tags = raw.get("tags", [])
        if isinstance(tags, str):
            tags = [item.strip() for item in tags.split(",")]
        tags = [str(item).strip() for item in tags if str(item).strip()]
        status = str(raw.get("status") or "draft")
        if status not in {"draft", "review", "approved"}:
            status = "draft"
        clean_questions.append({
            "id": qid,
            "display_number": str(raw.get("display_number") or index),
            "order": index,
            "status": status,
            "requires_review": status != "approved",
            "tags": tags,
            "source": str(raw.get("source") or ""),
            "year": str(raw.get("year") or ""),
            "subject": str(raw.get("subject") or ""),
            "source_question_number": str(raw.get("source_question_number") or ""),
            "ocr_text": str(raw.get("ocr_text") or ""),
            "regions": clean_regions,
        })
    updated = dict(current)
    for key in ("name", "subject", "year", "copyright_status"):
        if key in payload:
            updated[key] = str(payload[key] or "")
    updated["questions"] = clean_questions
    updated["updated_at"] = now()
    return updated


def open_pdf(project_id: str) -> fitz.Document:
    if read_project(project_id)["source_blocked"]:
        raise HTTPException(409, "PDF 副本已变化，不能继续使用旧坐标")
    return fitz.open(project_dir(project_id) / "source.pdf")


def region_image(project_id: str, question: dict[str, Any], scale: float = 2.0) -> bytes:
    pieces: list[Image.Image] = []
    with open_pdf(project_id) as doc:
        for region in sorted(question.get("regions", []), key=lambda r: int(r["page"])):
            page = doc[int(region["page"]) - 1]
            page.set_rotation(0)
            rect = (fitz.Rect(*region["bbox_pt"]) + (-1, -1, 1, 1)).intersect(page.rect)
            if rect.width * rect.height * scale * scale > 40_000_000:
                raise HTTPException(422, "选区过大，请缩小区域")
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
            pieces.append(Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples))
    if not pieces:
        raise HTTPException(400, "题目没有框选区域")
    width = max(piece.width for piece in pieces)
    height = sum(piece.height for piece in pieces) + max(0, len(pieces) - 1) * 12
    if width * height > 60_000_000:
        raise HTTPException(422, "跨页拼接图片过大，请减少区域")
    result = Image.new("RGB", (width, height), "white")
    y = 0
    for piece in pieces:
        result.paste(piece, (0, y))
        y += piece.height + 12
    output = io.BytesIO()
    result.save(output, format="PNG", optimize=True)
    return output.getvalue()


def question_ocr(project_id: str, question: dict[str, Any]) -> str:
    chunks: list[str] = []
    with open_pdf(project_id) as doc:
        for region in sorted(question.get("regions", []), key=lambda r: (int(r["page"]), r["bbox_pt"][1])):
            page = doc[int(region["page"]) - 1]
            chunks.append(page.get_text("text", clip=fitz.Rect(*region["bbox_pt"])).strip())
    return "\n".join(item for item in chunks if item)


def export_target(value: str, project_id: str) -> Path:
    raw = Path(value) if value.strip() else EXPORTS
    if not raw.is_absolute() or ".." in raw.parts:
        raise HTTPException(422, "请输入绝对目录路径，不允许 .. 路径跳转")
    base = raw.resolve()
    for protected in (WORKSPACE / "resource", WORKSPACE / "olddirect", WORKSPACE / ".git", PROJECTS, STATIC):
        if base == protected.resolve() or base.is_relative_to(protected.resolve()):
            raise HTTPException(422, "不能导出到资源、归档或工具内部工作目录")
    if base.exists() and not base.is_dir():
        raise HTTPException(422, "目标不是文件夹")
    out = base / project_id
    if out.is_symlink() or out.resolve().parent != base:
        raise HTTPException(422, "导出路径包含越界链接")
    return out


def export_project(project: dict[str, Any], directory: str = "", overwrite: bool = False) -> Path:
    if project.get("excluded") or "月考" in project.get("source_path", ""):
        raise HTTPException(422, "路径或文件名含“月考”，已排除导出")
    if not project.get("questions") or any(not q["regions"] for q in project["questions"]):
        raise HTTPException(422, "请先框选题目；不能导出没有区域的空题目")
    out = export_target(directory, project["id"])
    if out.exists():
        try:
            previous = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HTTPException(409, "目标已有其他文件，请选择新的父目录")
        if previous.get("project_id") != project["id"]:
            raise HTTPException(409, "目标不属于此项目，请选择新的父目录")
        if not overwrite:
            raise HTTPException(409, {"overwrite_required": True, "path": str(out)})
    # Render fully in a temporary sibling before touching the existing export.
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".cropper-", dir=out.parent))
    try:
        _write_export(project, staging)
        out.mkdir(exist_ok=True)
        (out / "images").mkdir(exist_ok=True)
        for path in staging.rglob("*"):
            if path.is_file():
                dest = out / path.relative_to(staging)
                if dest.is_symlink() or not dest.resolve().is_relative_to(out.resolve()):
                    raise HTTPException(422, "导出文件包含越界链接")
                os.replace(path, dest)
        # Only remove known, generated question images, never unrelated files.
        keep = {q["id"] + ".png" for q in project["questions"]}
        for path in (out / "images").glob("q-*.png"):
            if re.fullmatch(r"q-[a-f0-9]{10,32}\.png", path.name) and path.name not in keep:
                path.unlink()
    finally:
        shutil.rmtree(staging)
    return out


def _write_export(project: dict[str, Any], out: Path) -> None:
    images = out / "images"
    images.mkdir(parents=True)
    manifest_questions: list[dict[str, Any]] = []
    for index, question in enumerate(project.get("questions", []), 1):
        filename = f"{question['id']}.png"
        data = region_image(project["id"], question)
        (images / filename).write_bytes(data)
        image_hash = hashlib.sha256(data).hexdigest()
        ocr = question.get("ocr_text", "")
        item = dict(question)
        item.update({
            "image_path": f"images/{filename}",
            "image_sha256": image_hash,
            "ocr_text": ocr,
            "source_page_start": min((r["page"] for r in question["regions"]), default=None),
            "source_page_end": max((r["page"] for r in question["regions"]), default=None),
        })
        manifest_questions.append(item)
    manifest = {
        "tool": "question_cropper",
        "project_id": project["id"],
        "coordinate_system": COORDINATES,
        "render": {"dpi": 144, "padding_pt": 1, "gap_px": 12},
        "tool_version": TOOL_VERSION,
        "exported_at": now(),
        "source_pdf": project.get("source_pdf"),
        "source_path": project.get("source_path"),
        "source_sha256": project.get("source_sha256"),
        "subject": project.get("subject", ""),
        "year": project.get("year", ""),
        "copyright_status": project.get("copyright_status", ""),
        "questions": manifest_questions,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "display_number", "order", "status", "requires_review", "tags", "source", "year", "subject", "source_page_start", "source_page_end", "regions", "image_path", "image_sha256", "ocr_text"])
        writer.writeheader()
        for item in manifest_questions:
            writer.writerow({key: ("|".join(item["tags"]) if key == "tags" else json.dumps(item["regions"], ensure_ascii=False) if key == "regions" else item.get(key, "")) for key in writer.fieldnames})
    (out / "README.md").write_text(
        f"# 题目框选导出\n\n- 源 PDF：`{project.get('source_pdf', '')}`\n- SHA-256：`{project.get('source_sha256', '')}`\n- 导出时间：`{manifest['exported_at']}`\n- 坐标：{COORDINATES}\n- 图片来自原始 PDF 区域渲染，不依赖 OCR 重绘。\n- `status` 为 `draft`、`review` 或 `approved`，请人工复核后再发布。\n",
        encoding="utf-8",
    )
    (out / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")


class ProjectUpdate(BaseModel):
    name: str = ""
    subject: str = ""
    year: str = ""
    copyright_status: str = ""
    questions: list[dict[str, Any]] = Field(default_factory=list)


class ExportOptions(BaseModel):
    directory: str = ""
    overwrite: bool = False


class ImportPath(BaseModel):
    path: str


app = FastAPI(title="题目 PDF 框选工具", version=TOOL_VERSION)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def local_only(request: Request, call_next):
    # No cross-site requests may cause local file reads/writes. Serialize PDF work.
    import asyncio
    if request.url.hostname not in {"127.0.0.1", "localhost", "test", "testserver"}:
        return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "不允许跨来源访问本地文件"}, status_code=403)
    if request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
    if not hasattr(app.state, "pdf_lock"):
        app.state.pdf_lock = asyncio.Lock()
    async with app.state.pdf_lock:
        return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    result = []
    for directory in sorted(PROJECTS.iterdir() if PROJECTS.exists() else [], reverse=True):
        if not directory.is_dir() or not (directory / "project.json").exists():
            continue
        try:
            item = json.loads((directory / "project.json").read_text(encoding="utf-8"))
            result.append({"id": item["id"], "name": item.get("name", item["id"]), "source_pdf": item.get("source_pdf", ""), "updated_at": item.get("updated_at", "")})
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    return result


@app.post("/api/projects")
async def create_project(file: UploadFile = File(...)) -> dict[str, Any]:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(415, "请选择 PDF 文件")
    project_id = f"proj-{uuid.uuid4().hex[:12]}"
    directory = PROJECTS / project_id
    directory.mkdir()
    source = directory / "source.pdf"
    total = 0
    try:
        with source.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "PDF 文件不能超过 150 MB")
                handle.write(chunk)
        with fitz.open(source) as doc:
            if not doc.is_pdf or doc.needs_pass or doc.page_count == 0:
                raise ValueError("文件不是有效 PDF、已加密或没有页面")
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(400, f"无法读取 PDF: {exc}") from exc
    project = make_project(project_id, file.filename or "source.pdf", source)
    write_project(project)
    return project


@app.post("/api/import-path")
def import_path(payload: ImportPath) -> dict[str, Any]:
    source = Path(payload.path.strip().strip('"'))
    if not source.is_absolute() or ".." in source.parts or source.suffix.lower() != ".pdf":
        raise HTTPException(422, "请输入本地 PDF 的绝对路径")
    source = source.resolve()
    if not source.is_file() or source.stat().st_size > MAX_UPLOAD_BYTES:
        raise HTTPException(422, "PDF 不存在或超过 150 MB")
    try:
        with fitz.open(source) as doc:
            if not doc.is_pdf or doc.needs_pass or not doc.page_count:
                raise ValueError("无法读取加密或空 PDF")
    except Exception as exc:
        raise HTTPException(422, f"PDF 无法读取：{exc}") from exc
    pid = f"proj-{uuid.uuid4().hex[:12]}"
    directory = PROJECTS / pid
    directory.mkdir()
    shutil.copyfile(source, directory / "source.pdf")
    project = make_project(pid, source.name, directory / "source.pdf")
    project["source_path"] = str(source)
    project["excluded"] = "月考" in str(source)
    project["exclude_reason"] = "路径或文件名含“月考”，已排除导出。" if project["excluded"] else ""
    write_project(project)
    return project


@app.post("/api/restore")
async def restore(project_file: UploadFile = File(...), pdf: UploadFile = File(...)) -> dict[str, Any]:
    raw = await project_file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(413, "项目 JSON 太大")
    try:
        previous = json.loads(raw)
        if previous.get("coordinate_system") != COORDINATES:
            raise ValueError("项目坐标版本不兼容")
    except (ValueError, AttributeError):
        raise HTTPException(422, "请选择本工具导出的 project.json")
    data = await pdf.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES or hashlib.sha256(data).hexdigest() != previous.get("source_sha256"):
        raise HTTPException(409, "PDF 哈希不匹配；不能将旧区域套用到不同 PDF")
    await pdf.seek(0)
    created = await create_project(pdf)
    restored = validate_project(previous, created)
    restored["source_path"] = str(previous.get("source_path") or pdf.filename)
    restored["excluded"] = "月考" in restored["source_path"] or "月考" in created["source_pdf"]
    write_project(restored)
    return read_project(restored["id"])


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    return read_project(project_id)


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdate) -> dict[str, Any]:
    current = read_project(project_id)
    updated = validate_project(payload.model_dump(), current)
    write_project(updated)
    return updated


@app.get("/api/projects/{project_id}/pages/{page_no}")
def render_page(project_id: str, page_no: int, scale: float = 1.35) -> StreamingResponse:
    project_dir(project_id)
    if not 0.15 <= scale <= 3.0:
        raise HTTPException(422, "缩放比例应在 0.15 到 3.0 之间")
    with open_pdf(project_id) as doc:
        if page_no < 1 or page_no > doc.page_count:
            raise HTTPException(404, "页码不存在")
        page = doc[page_no - 1]
        page.set_rotation(0)
        page_width, page_height = page.rect.width, page.rect.height
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        data = pixmap.tobytes("png")
    return StreamingResponse(io.BytesIO(data), media_type="image/png", headers={"X-Page-Width": str(page_width), "X-Page-Height": str(page_height), "X-Render-Scale": str(scale)})


@app.get("/api/projects/{project_id}/questions/{question_id}/preview")
def preview(project_id: str, question_id: str) -> StreamingResponse:
    project = read_project(project_id)
    question = next((q for q in project["questions"] if q["id"] == question_id), None)
    if question is None:
        raise HTTPException(404, "题目不存在")
    return StreamingResponse(io.BytesIO(region_image(project_id, question)), media_type="image/png")


@app.post("/api/projects/{project_id}/export")
def export(project_id: str, options: ExportOptions = ExportOptions()) -> dict[str, Any]:
    project = read_project(project_id)
    out = export_project(project, options.directory, options.overwrite)
    archive = project_dir(project_id) / "export.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in out.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(out))
    return {"path": str(out), "download": f"/api/projects/{project_id}/download", "count": len(project["questions"])}


@app.get("/api/projects/{project_id}/download")
def download(project_id: str) -> FileResponse:
    archive = project_dir(project_id) / "export.zip"
    if not archive.exists():
        raise HTTPException(404, "请先导出")
    return FileResponse(archive, media_type="application/zip", filename=f"{project_id}.zip")


@app.get("/api/projects/{project_id}/export")
def export_info(project_id: str) -> JSONResponse:
    project = read_project(project_id)
    out = EXPORTS / project_id
    return JSONResponse({"path": str(out), "exists": out.exists(), "question_count": len(project.get("questions", []))})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8765, reload=False)
