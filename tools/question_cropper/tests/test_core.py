from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import fitz
import httpx

from tools.question_cropper.app import app


def sample_pdf() -> bytes:
    document = fitz.open()
    for text in ("(1) 第一页题目\n公式 x² + y²", "(1) 跨页题目的第二页"):
        page = document.new_page(width=300, height=400)
        page.insert_text((30, 70), text, fontsize=14)
    data = document.tobytes()
    document.close()
    return data


def test_project_save_render_and_export(tmp_path: Path, monkeypatch):
    # Keep the integration test isolated from the developer's working projects.
    import tools.question_cropper.app as module
    monkeypatch.setattr(module, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(module, "EXPORTS", tmp_path / "exports")
    module.PROJECTS.mkdir(); module.EXPORTS.mkdir()

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/projects", files={"file": ("sample.pdf", sample_pdf(), "application/pdf")})
            assert response.status_code == 200
            project = response.json()
            q = {"id": "q-1234567890", "display_number": "1", "status": "review", "tags": ["跨页"], "regions": [
                {"page": 1, "bbox_pt": [20, 30, 280, 130]},
                {"page": 2, "bbox_pt": [20, 20, 280, 100]},
            ]}
            response = await client.put(f"/api/projects/{project['id']}", json={"name": "测试项目", "questions": [q]})
            assert response.status_code == 200
            saved = response.json()
            assert saved["questions"][0]["regions"][1]["page_width"] == 300
            assert (await client.get(f"/api/projects/{project['id']}/pages/1?scale=0.8")).headers["content-type"].startswith("image/png")
            preview = await client.get(f"/api/projects/{project['id']}/questions/q-1234567890/preview")
            assert preview.status_code == 200 and preview.content.startswith(b"\x89PNG")
            archive = await client.post(f"/api/projects/{project['id']}/export", json={"overwrite": True})
            assert archive.status_code == 200
            download = await client.get(f"/api/projects/{project['id']}/download")
            assert download.status_code == 200 and download.content[:2] == b"PK"
            manifest = json.loads((tmp_path / "exports" / project["id"] / "manifest.json").read_text(encoding="utf-8"))
            assert manifest["questions"][0]["source_page_start"] == 1
            assert manifest["questions"][0]["source_page_end"] == 2

    asyncio.run(run())
