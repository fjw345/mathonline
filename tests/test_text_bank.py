import json
from pathlib import Path

from backend.importer import apply_text_manifest, import_manifest
from scripts.build_text_bank import (
    _fix_vertical_fraction,
    build_text_bank,
    clean_text,
    iter_image_refs,
    render_part,
)


def test_clean_text_removes_scan_furniture_but_keeps_math():
    raw = "\ufeff评阅人 （1）计算 $\\iint_D x\\,dx$\r\n\r\n\r\n得分三、证明题\r\n第 3 页\r\n"
    cleaned = clean_text(raw)
    assert "评阅人" not in cleaned
    assert "第 3 页" not in cleaned
    assert cleaned.startswith("（1）计算")
    assert "三、证明题" in cleaned
    assert cleaned.count("\n\n") <= 1


def test_clean_text_keeps_numbers_but_escapes_ordered_list_marker():
    cleaned = clean_text("1. 设函数 $f(x)$\n5.\n(A) 2")
    assert cleaned.startswith("1\\. 设函数")
    assert "\n5.\n" not in cleaned
    assert "\\tag" not in clean_text("$x\\tag {2$分$}$")


def test_vertical_fraction_recovery_only_for_portrait_images():
    assert _fix_vertical_fraction("16 15", (39, 88)) == "$\\frac{16}{15}$"
    assert _fix_vertical_fraction("16 15", (200, 60)) == "16 15"
    assert _fix_vertical_fraction("16, 15", (39, 88)) == "16, 15"


def test_iter_image_refs_keeps_parentheses_in_filenames():
    text = "![a](1 (4).assets/block-5.png) 与 ![b](x.png)"
    assert [ref[2] for ref in iter_image_refs(text)] == [
        "1 (4).assets/block-5.png",
        "x.png",
    ]


def test_render_part_rewrites_inline_figure_and_builds_stable_asset(tmp_path):
    root = tmp_path
    text_root = root / "devided-math-text"
    text_root.mkdir()
    md = text_root / "q.md"
    md.write_text("题面\n\n![fig](q.assets/block-1.png)\n", encoding="utf-8")
    assets_dir = text_root / "q.assets"
    assets_dir.mkdir()
    from PIL import Image

    Image.new("RGB", (40, 30), "white").save(assets_dir / "block-1.png")
    text, assets, error = render_part(md, "p1", "question", root)
    assert error is None
    assert "/api/v1/media/img-" in text
    assert len(assets) == 1
    assert assets[0]["problem_id"] == "p1"
    assert assets[0]["width"] == 40 and assets[0]["height"] == 30
    assert assets[0]["path"].endswith(".png")
    # Same input produces the same id, so relaunches are idempotent.
    again, _, _ = render_part(md, "p1", "question", root)
    assert again == text


def test_build_text_bank_covers_the_whole_image_bank():
    manifest, report = build_text_bank()
    assert report["problems"] == 1868
    assert report["text_ready"] == 1868
    assert report["image_fallback_problems"] == 0
    assert len(manifest["problems"]) == 1868
    ids = {p["id"] for p in manifest["problems"]}
    assert len(ids) == len(manifest["problems"])
    assert report["image_fallback"] == []
    assets = {a["id"]: a for a in manifest["assets"]}
    assert len(assets) == len(manifest["assets"])
    for problem in manifest["problems"]:
        assert problem["question_text"].strip()
        assert problem["answer"]["text"].strip()
        for text in (problem["question_text"], problem["answer"]["text"]):
            for ref in iter_image_refs(text):
                assert ref[2].startswith("/api/v1/media/")
                assert ref[2].rsplit("/", 1)[-1] in assets


def test_apply_text_manifest_is_additive_and_idempotent(env):
    db = env["db"]
    existing = db.get("problems", id="test-q1")
    answer = {
        k: v
        for k, v in db.get("problem_answers", problem_id="test-q1").items()
        if k != "problem_id"
    }
    manifest = {
        "schema_version": 1,
        "sources": db.list("problem_sources"),
        "assets": [],
        "problems": [
            {
                **existing,
                "question_text": "题面 $x^2$",
                "answer": {**answer, "text": "答案 $x$"},
            }
        ],
    }
    result = apply_text_manifest(manifest, dry_run=True)
    assert result["updated"] == 1 and result["dry_run"]
    assert db.get("problems", id="test-q1")["version"] == existing["version"]
    assert db.get("problems", id="test-q1")["question_text"] == ""

    applied = apply_text_manifest(manifest)
    assert applied["updated"] == 1
    saved = db.get("problems", id="test-q1")
    assert saved["question_text"] == "题面 $x^2$"
    assert saved["version"] == existing["version"]
    assert saved["updated_at"] == existing["updated_at"]
    assert db.get("problem_answers", problem_id="test-q1")["text"] == "答案 $x$"

    before = db.get("problems", id="test-q1")
    assert apply_text_manifest(manifest)["skipped"] == 1
    assert db.get("problems", id="test-q1") == before

    # Non-empty text is preserved unless the caller explicitly overwrites.
    changed = json.loads(json.dumps(manifest))
    changed["problems"][0]["question_text"] = "新题面"
    assert apply_text_manifest(changed)["skipped"] == 1
    assert db.get("problems", id="test-q1")["question_text"] == "题面 $x^2$"
    assert apply_text_manifest(changed, only_empty=False)["updated"] == 1
    assert db.get("problems", id="test-q1")["question_text"] == "新题面"


def test_old_image_manifest_does_not_erase_existing_text(env):
    db = env["db"]
    problem = db.get("problems", id="test-q1")
    problem["question_text"] = "已存在的题面"
    db.put("problems", problem)
    answer = db.get("problem_answers", problem_id="test-q1")
    answer["text"] = "已存在的答案"
    db.put("problem_answers", answer)
    legacy = {
        "schema_version": 1,
        "sources": db.list("problem_sources"),
        "assets": [],
        "problems": [
            {
                **problem,
                "answer": {
                    k: v
                    for k, v in answer.items()
                    if k not in ("problem_id", "text")
                },
            }
        ],
    }
    legacy["problems"][0].pop("question_text")
    assert import_manifest(legacy)["unchanged"] == 1
    assert db.get("problems", id="test-q1")["question_text"] == "已存在的题面"
    assert db.get("problem_answers", problem_id="test-q1")["text"] == "已存在的答案"


def test_text_backfill_skips_replaced_images(env):
    db = env["db"]
    problem = db.get("problems", id="test-q1")
    answer = {
        k: v
        for k, v in db.get("problem_answers", problem_id="test-q1").items()
        if k != "problem_id"
    }
    manifest = {
        "schema_version": 1,
        "sources": db.list("problem_sources"),
        "assets": [],
        "problems": [
            {
                **problem,
                "question_text": "新题面",
                "answer": {**answer, "text": "新答案"},
            }
        ],
    }
    problem["question_images"] = ["answer-image"]
    db.put("problems", problem)
    result = apply_text_manifest(manifest)
    assert result["image_mismatch"] == 1 and result["updated"] == 0
    assert db.get("problems", id="test-q1")["question_text"] == ""


def test_public_problem_and_answer_expose_text_after_unlock(env):
    admin, one = env["admin"], env["one"]
    response = admin.put(
        "/api/v1/admin/problems/test-q1",
        json={
            "version": 1,
            "question_text": "题干 $a+b$",
            "answer": {"text": "答案 $c$"},
        },
    )
    assert response.status_code == 200
    public = env["guest"].get("/api/v1/problems/test-q1").json()
    assert public["question_text"] == "题干 $a+b$"
    assert "answer" not in public
    assert one.get("/api/v1/problems/test-q1/answer").status_code == 403
    one.put(
        "/api/v1/me/problems/test-q1/submission",
        json={"text": "解答", "problem_version": 2},
    )
    answer = one.get("/api/v1/problems/test-q1/answer").json()
    assert answer["text"] == "答案 $c$"
    assert answer["images"][0]["id"] == "answer-image"


def test_missing_legacy_image_does_not_block_text(env):
    db = env["db"]
    problem = db.get("problems", id="test-q1")
    problem["question_text"] = "题面文字仍然可用"
    db.put("problems", problem)
    db.delete("media_assets", id="question-image")
    response = env["guest"].get("/api/v1/problems/test-q1")
    assert response.status_code == 200
    data = response.json()
    assert data["question_text"] == "题面文字仍然可用"
    assert data["question_images"] == []
    # Admin maintenance still surfaces incomplete image metadata.
    assert env["admin"].get("/api/v1/admin/problems").status_code == 400


def test_inline_answer_media_requires_answer_permission(env):
    db = env["db"]
    asset = {
        "id": "inline-answer",
        "owner_id": None,
        "purpose": "answer",
        "path": "inline-answer.png",
        "content_type": "image/png",
        "width": 10,
        "height": 10,
        "problem_id": "test-q1",
        "source_path": "devided-math-text/inline-answer.png",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    db.put("media_assets", asset)
    (Path(db.path).parent / "data/assets").mkdir(parents=True, exist_ok=True)
    (Path(db.path).parent / "data/assets/inline-answer.png").write_bytes(b"png")
    assert env["guest"].get("/api/v1/media/inline-answer").status_code == 403
    assert env["one"].get("/api/v1/media/inline-answer").status_code == 403
    env["one"].put(
        "/api/v1/me/problems/test-q1/submission",
        json={"text": "解答", "problem_version": 1},
    )
    assert env["one"].get("/api/v1/media/inline-answer").status_code == 200
