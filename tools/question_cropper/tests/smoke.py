"""Run without pytest: python tools/question_cropper/tests/smoke.py"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import fitz
import httpx

import tools.question_cropper.app as module


def pdf_bytes() -> bytes:
    doc = fitz.open()
    for text in ("(1) 第一页题目 x²+y²", "(1) 跨页题目第二页"):
        page = doc.new_page(width=300, height=400)
        page.insert_text((30, 70), text, fontsize=14)
    data = doc.tobytes(); doc.close(); return data


async def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder); module.PROJECTS = root / "projects"; module.EXPORTS = root / "exports"; module.PROJECTS.mkdir(); module.EXPORTS.mkdir()
        source_for_path = root / "路径含月考.pdf"; source_for_path.write_bytes(pdf_bytes())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
            response = await client.post("/api/projects", files={"file": ("sample.pdf", pdf_bytes(), "application/pdf")})
            assert response.status_code == 200, response.text; project = response.json(); pid = project["id"]
            payload = {"name": "测试项目", "questions": [{"id": "q-1234567890", "display_number": "1", "status": "review", "tags": ["跨页"], "regions": [{"page": 1, "bbox_pt": [20, 30, 280, 130]}, {"page": 2, "bbox_pt": [20, 20, 280, 100]}]}]}
            response = await client.put(f"/api/projects/{pid}", json=payload); assert response.status_code == 200, response.text
            assert (await client.get(f"/api/projects/{pid}/pages/1?scale=0.24")).content.startswith(b"\x89PNG")
            assert (await client.get(f"/api/projects/{pid}/pages/1?scale=0.8")).content.startswith(b"\x89PNG")
            assert (await client.get(f"/api/projects/{pid}/questions/q-1234567890/preview")).content.startswith(b"\x89PNG")
            archive = await client.post(f"/api/projects/{pid}/export", json={"overwrite": True}); assert archive.status_code == 200, archive.text
            download = await client.get(f"/api/projects/{pid}/download"); assert download.status_code == 200 and download.content[:2] == b"PK"
            manifest = json.loads((root / "exports" / pid / "manifest.json").read_text(encoding="utf-8")); assert manifest["questions"][0]["source_page_end"] == 2
            duplicate = await client.post(f"/api/projects/{pid}/export"); assert duplicate.status_code == 409
            source = root / "projects" / pid / "source.pdf"; source.write_bytes(source.read_bytes() + b"changed")
            reopened = await client.get(f"/api/projects/{pid}"); assert reopened.json()["source_blocked"] is True
            assert (await client.get(f"/api/projects/{pid}/pages/1")).status_code == 409
            path_import = await client.post("/api/import-path", json={"path": str(source_for_path)})
            assert path_import.status_code == 200 and path_import.json()["excluded"] is True
            path_pid = path_import.json()["id"]
            assert (await client.post(f"/api/projects/{path_pid}/export", json={"overwrite": True})).status_code == 422
    print("question_cropper smoke ok")


if __name__ == "__main__":
    asyncio.run(main())
