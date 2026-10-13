from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from conftest import submit

from backend import config, importer
from backend.store import LocalStore


def test_feedback_validation_privacy_and_answer_permissions(env):
    guest, one, admin, db = env["guest"], env["one"], env["admin"], env["db"]
    path = "/api/v1/problems/test-q1/feedback"
    data = {"kind": "incomplete", "detail": "  缺少第二问  ", "problem_version": 1}
    assert guest.post(path, json=data).status_code == 401
    assert one.post(path, json={**data, "kind": "invalid"}).status_code == 422
    assert one.post(path, json={**data, "detail": "x" * 2001}).status_code == 422
    assert (
        one.post(path, json={**data, "kind": "other", "detail": " "}).status_code == 400
    )
    assert one.post(path, json={**data, "problem_version": 99}).status_code == 409
    response = one.post(path, json=data)
    assert response.status_code == 200
    fid = response.json()["id"]
    assert db.get("problem_feedback", id=fid)["detail"] == "缺少第二问"
    assert db.list("user_problem_progress") == []
    assert db.list("answer_access") == []
    assert not one.get("/api/v1/problems/test-q1").json()["answer_unlocked"]
    assert one.get("/api/v1/problems/test-q1/answer").status_code == 403
    assert guest.get("/api/v1/admin/feedback").status_code == 401
    for client in [one, env["two"]]:
        assert client.get("/api/v1/admin/feedback").status_code == 403
        assert (
            client.patch(
                "/api/v1/admin/feedback/" + fid, json={"status": "resolved"}
            ).status_code
            == 403
        )
    rows = admin.get("/api/v1/admin/feedback").json()
    assert len(rows) == 1 and rows[0]["id"] == fid
    assert rows[0]["reporter"] == "one"
    assert rows[0]["problem_title"] == "测试题 1"
    assert rows[0]["current_version"] == rows[0]["problem_version"] == 1
    assert rows[0]["reviewer"] is None
    p = db.get("problems", id="test-q1")
    p["status"] = "archived"
    db.put("problems", p)
    assert one.post(path, json=data).status_code == 404
    assert admin.get("/api/v1/admin/feedback").json()[0]["id"] == fid


def test_feedback_review_keeps_reported_version_and_does_not_change_problem(env):
    one, admin, db = env["one"], env["admin"], env["db"]
    fid = one.post(
        "/api/v1/problems/test-q1/feedback",
        json={"kind": "answer_mismatch", "problem_version": 1},
    ).json()["id"]
    assert (
        admin.put(
            "/api/v1/admin/problems/test-q1", json={"version": 1, "title": "修改后标题"}
        ).status_code
        == 200
    )
    p = db.get("problems", id="test-q1")
    rows = admin.get("/api/v1/admin/feedback").json()
    assert rows[0]["problem_version"] == 1 and rows[0]["current_version"] == 2
    assert rows[0]["problem_title"] == "修改后标题"
    assert (
        admin.patch(
            "/api/v1/admin/feedback/" + fid,
            json={"status": "invalid"},
        ).status_code
        == 422
    )
    assert (
        admin.patch(
            "/api/v1/admin/feedback/" + fid,
            json={"status": "resolved", "review_note": "  已替换答案截图  "},
        ).status_code
        == 200
    )
    row = db.get("problem_feedback", id=fid)
    assert row["status"] == "resolved" and row["review_note"] == "已替换答案截图"
    assert row["reviewed_at"] and row["reviewed_by"]
    assert db.get("problems", id="test-q1") == p
    assert (
        admin.patch(
            "/api/v1/admin/feedback/" + fid, json={"status": "dismissed"}
        ).status_code
        == 409
    )
    assert len(db.list("moderation_logs", kind="problem_feedback")) == 1
    assert (
        admin.patch(
            "/api/v1/admin/feedback/missing", json={"status": "dismissed"}
        ).status_code
        == 404
    )
    # Community moderators cannot change the bank or inspect private reports.
    profile = db.get("profiles", nickname="two")
    profile["role"] = "moderator"
    db.put("profiles", profile)
    assert env["two"].get("/api/v1/admin/moderation").status_code == 200
    assert env["two"].get("/api/v1/admin/feedback").status_code == 403


def test_feedback_concurrent_review_has_one_winner(env):
    fid = (
        env["one"]
        .post(
            "/api/v1/problems/test-q1/feedback",
            json={"kind": "incomplete", "problem_version": 1},
        )
        .json()["id"]
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda status: (
                    env["admin"]
                    .patch("/api/v1/admin/feedback/" + fid, json={"status": status})
                    .status_code
                ),
                ["resolved", "dismissed"],
            )
        )
    assert sorted(responses) == [200, 409]
    assert len(env["db"].list("moderation_logs", kind="problem_feedback")) == 1


def test_initial_bank_publication_migration_preserves_edits_and_learning(
    env, monkeypatch
):
    db = env["db"]
    template = db.get("problems", id="test-q1")
    answer = db.get("problem_answers", problem_id="test-q1")
    for name, status, manual in [
        ("auto", "draft", False),
        ("manual", "draft", True),
        ("hidden", "archived", False),
        ("no-answer", "draft", False),
    ]:
        pid = "cmc-nonmath-" + name
        db.put(
            "problems",
            {**template, "id": pid, "status": status, "manual_revision": manual},
        )
        db.put(
            "problem_answers",
            {
                **answer,
                "problem_id": pid,
                "images": [] if name == "no-answer" else answer["images"],
            },
        )
    assert submit(env["one"], pid="cmc-nonmath-auto").status_code == 200
    progress = db.list("user_problem_progress")
    before = {p["id"]: p for p in db.list("problems")}
    with db.connect() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=5")
    monkeypatch.setattr(config, "ROOT", Path(__file__).resolve().parents[1])
    migrated = LocalStore(db.path)
    after = {p["id"]: p for p in migrated.list("problems")}
    before["cmc-nonmath-auto"]["status"] = "published"
    assert after == before
    assert migrated.list("user_problem_progress") == progress
    assert not after["cmc-nonmath-auto"]["verified"]
    assert LocalStore(db.path).list("problems") == migrated.list("problems")
    legacy = {
        **template,
        "id": "cmc-nonmath-auto",
        "answer": {k: v for k, v in answer.items() if k != "problem_id"},
    }
    imported = importer.import_manifest(
        {
            "schema_version": 1,
            "sources": db.list("problem_sources"),
            "assets": [],
            "problems": [legacy],
        }
    )
    assert imported["unchanged"] == 1
    assert db.get("problems", id="cmc-nonmath-auto")["status"] == "published"
