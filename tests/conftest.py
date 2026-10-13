import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import auth, config, domain, importer, main, rate_limit
from backend.store import LocalStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = LocalStore(tmp_path / "test.sqlite")
    for module in (main, auth, domain, importer, rate_limit):
        monkeypatch.setattr(module, "store", db)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "MODE", "local")
    monkeypatch.setattr(config, "PREVIEW", True)
    monkeypatch.setattr(rate_limit, "allow", lambda *args, **kwargs: True)
    source = {
        "id": "test-source",
        "name": "测试竞赛",
        "year": 2025,
        "group_name": "非数学 A 类",
        "source_url": "",
        "rights_note": "待确认",
        "verified": False,
    }
    db.put("problem_sources", source)
    raw = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(raw, format="PNG")
    raw = raw.getvalue()
    assets = tmp_path / "data/assets"
    assets.mkdir(parents=True)
    for aid, purpose in [("question-image", "question"), ("answer-image", "answer")]:
        (assets / (aid + ".png")).write_bytes(raw)
        db.put(
            "media_assets",
            {
                "id": aid,
                "owner_id": None,
                "purpose": purpose,
                "path": aid + ".png",
                "content_type": "image/png",
                "width": 200,
                "height": 100,
                "problem_id": None,
                "source_path": "",
                "created_at": auth.now(),
            },
        )
    for i in range(1, 4):
        p = {
            "id": f"test-q{i}",
            "source_id": source["id"],
            "title": f"测试题 {i}",
            "type": "blank" if i == 1 else "solution",
            "year": 2025 if i < 3 else 2024,
            "group_name": "非数学 A 类",
            "subject": "高等数学",
            "tags": ["测试"],
            "difficulty": None,
            "status": "draft",
            "version": 1,
            "grading_mode": "self_assessment",
            "question_images": ["question-image"],
            "choice_options": [],
            "multiple": False,
            "verified": False,
            "answer_verified": False,
            "manual_revision": False,
            "updated_at": auth.now(),
        }
        db.put("problems", p)
        db.put(
            "problem_answers",
            {
                "problem_id": p["id"],
                "images": ["answer-image"],
                "correct_options": [],
                "provenance": "测试答案",
            },
        )
    admin = TestClient(main.app)
    u1 = TestClient(main.app)
    u2 = TestClient(main.app)
    guest = TestClient(main.app)
    for client, email in [
        (admin, "admin@test.local"),
        (u1, "one@test.local"),
        (u2, "two@test.local"),
    ]:
        assert (
            client.post(
                "/api/v1/auth/register",
                json={
                    "email": email,
                    "password": "test-password",
                    "nickname": email.split("@")[0],
                },
            ).status_code
            == 200
        )
    return {"db": db, "admin": admin, "one": u1, "two": u2, "guest": guest, "raw": raw}


def submit(client, pid="test-q1", text="我的答案", version=1):
    return client.put(
        f"/api/v1/me/problems/{pid}/submission",
        json={"text": text, "problem_version": version},
    )
