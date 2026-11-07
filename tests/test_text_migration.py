import json
from pathlib import Path

from backend import config
from backend.text_migration import (
    project_ref,
    restore_text,
    snapshot_text,
    verify_text,
    write_snapshot,
)


def manifest_from_db(db):
    problems = []
    for problem in db.list("problems"):
        answer = db.get("problem_answers", problem_id=problem["id"])
        problems.append(
            {
                **problem,
                "answer": {
                    key: value
                    for key, value in answer.items()
                    if key != "problem_id"
                },
            }
        )
    return {
        "schema_version": 1,
        "sources": [],
        "assets": [],
        "problems": problems,
    }


def test_snapshot_and_restore_only_text(env):
    db = env["db"]
    snapshot = snapshot_text(db)
    assert len(snapshot["problems"]) == 3
    assert snapshot["problems"][0]["question_text"] == ""
    assert snapshot["problems"][0]["version"] == 1

    problem = db.get("problems", id="test-q1")
    problem["question_text"] = "迁移后题面"
    db.put("problems", problem)
    answer = db.get("problem_answers", problem_id="test-q1")
    answer["text"] = "迁移后答案"
    db.put("problem_answers", answer)

    dry = restore_text(snapshot, repository=db)
    assert dry["planned"] == 3 and dry["updated"] == 0
    assert db.get("problems", id="test-q1")["question_text"] == "迁移后题面"

    restored = restore_text(snapshot, dry_run=False, repository=db)
    assert restored["updated"] == 3
    saved = db.get("problems", id="test-q1")
    assert saved["question_text"] == ""
    assert saved["version"] == 1
    assert db.get("problem_answers", problem_id="test-q1")["text"] == ""


def test_write_snapshot_refuses_overwrite(env, tmp_path):
    path = tmp_path / "backup.json"
    write_snapshot(path, repository=env["db"])
    try:
        write_snapshot(path, repository=env["db"])
        raise AssertionError("expected FileExistsError")
    except FileExistsError:
        pass
    assert json.loads(path.read_text(encoding="utf-8"))["problems"]


def test_verify_reports_text_mismatch_and_strict_mode(env):
    db = env["db"]
    manifest = manifest_from_db(db)
    assert verify_text(manifest, repository=db)["ok"]
    problem = db.get("problems", id="test-q1")
    problem["question_text"] = "管理员后加的文字"
    db.put("problems", problem)
    normal = verify_text(manifest, repository=db)
    assert normal["ok"]
    assert normal["text_mismatch"]
    strict = verify_text(manifest, strict=True, repository=db)
    assert not strict["ok"]
    assert strict["text_mismatch"][0]["field"] == "question"
    assert strict["actual"]["question_text"] == 1


def test_verify_detects_missing_problem_and_version_change(env):
    db = env["db"]
    manifest = manifest_from_db(db)
    snapshot = snapshot_text(db)
    problem = db.get("problems", id="test-q1")
    problem["version"] = 2
    db.put("problems", problem)
    changed = verify_text(manifest, backup=snapshot, repository=db)
    assert not changed["ok"]
    assert changed["version_changes"]
    manifest["problems"].append(
        {
            "id": "does-not-exist",
            "question_text": "",
            "question_images": [],
            "answer": {"images": [], "text": ""},
        }
    )
    missing = verify_text(manifest, repository=db)
    assert not missing["ok"]
    assert missing["missing"] == ["does-not-exist"]


def test_verify_rejects_backup_from_another_project(env, monkeypatch):
    db = env["db"]
    manifest = manifest_from_db(db)
    backup = snapshot_text(db)
    backup["project_ref"] = "old-project"
    monkeypatch.setattr(config, "SB_URL", "https://current.supabase.co")
    report = verify_text(manifest, backup=backup, repository=db)
    assert report["project_mismatch"] and not report["ok"]


def test_project_ref_from_supabase_url(monkeypatch):
    monkeypatch.setattr(
        config, "SB_URL", "https://rakgupcirierpnzhptoz.supabase.co"
    )
    assert project_ref() == "rakgupcirierpnzhptoz"
    monkeypatch.setattr(config, "SB_URL", "")
    assert project_ref() is None
