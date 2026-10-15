from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from conftest import submit

from backend import config, domain, importer, main
from backend.store import LocalStore
from scripts.build_image_manifest import parse_name


def test_admin_bank_pagination_search_and_permissions(env):
    client = env["admin"]
    first = client.get("/api/v1/admin/problems?page=1&page_size=2").json()
    second = client.get("/api/v1/admin/problems?page=2&page_size=2").json()
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert not ({p["id"] for p in first["items"]} & {p["id"] for p in second["items"]})
    result = client.get("/api/v1/admin/problems?page=1&q=test-q2").json()
    assert result["total"] == 1 and result["items"][0]["id"] == "test-q2"
    assert client.get("/api/v1/admin/problems?page=0").status_code == 422
    assert env["one"].get("/api/v1/admin/problems?page=1").status_code == 403


def test_screenshot_names_and_order():
    assert parse_name("1 (4)上") == ("1.4", 0)
    assert parse_name("1(4)下") == ("1.4", 2)
    assert parse_name("1.4") == ("1.4", 0)
    assert parse_name("6下") == ("6", 2)


def test_admin_bank_reads_share_one_connection_and_keep_permissions(env, monkeypatch):
    db = env["db"]
    assert env["guest"].get("/api/v1/admin/problems").status_code == 401
    assert env["one"].get("/api/v1/admin/problems").status_code == 403
    connect = db.connect
    connections = []

    def counted_connect():
        connections.append(True)
        return connect()

    monkeypatch.setattr(db, "connect", counted_connect)
    response = env["admin"].get("/api/v1/admin/problems")
    assert response.status_code == 200
    assert len(connections) == 3  # Session, profile and one bank snapshot.
    rows = response.json()
    assert len(rows) == 3
    assert {p["id"] for p in rows} == {"test-q1", "test-q2", "test-q3"}
    for p in rows:
        assert p["question_images"][0]["id"] == "question-image"
        assert p["answer"]["images"][0]["id"] == "answer-image"
        assert p["source"]["id"] == "test-source"


def test_filters_and_no_answer_leak(env):
    guest = env["guest"]
    data = guest.get("/api/v1/problems?year=2024,2025&type=blank").json()
    assert data["total"] == 1
    p = data["items"][0]
    assert "answer" not in p and "correct_options" not in p
    assert guest.get("/api/v1/practice/random?year=1900").status_code == 404
    assert guest.get("/api/v1/problems/test-q1/answer").status_code == 401
    assert guest.get("/api/v1/media/answer-image").status_code == 403
    assert guest.get("/api/v1/media/question-image").status_code == 200


def test_collection_filters_ignore_removed_dimensions_and_extend_with_bank(env):
    guest, db = env["guest"], env["db"]
    first = db.get("problems", id="test-q1")
    first["tags"] = ["全国大学生数学竞赛·非数"]
    db.put("problems", first)
    second = db.get("problems", id="test-q2")
    second["tags"] = ["考研数学"]
    db.put("problems", second)
    removed = "group_name=不存在&subject=不存在&source_id=不存在&difficulty=5"
    catalog = guest.get("/api/v1/catalog").json()
    assert set(catalog) == {"total", "years", "tags", "counts"}
    assert "全国大学生数学竞赛·非数" in catalog["tags"]
    assert "考研数学" in catalog["tags"]
    assert guest.get("/api/v1/problems?" + removed).json()["total"] == 3
    selected = guest.get(
        "/api/v1/problems?tag=全国大学生数学竞赛·非数&year=2025&" + removed
    ).json()
    assert selected["total"] == 1 and selected["items"][0]["id"] == first["id"]
    assert "difficulty" not in selected["items"][0]
    assert (
        guest.get("/api/v1/practice/random?tag=考研数学&" + removed).json()["id"]
        == second["id"]
    )
    daily = (
        env["one"]
        .post(
            "/api/v1/practice/daily",
            json={"tag": "考研数学", "subject": "不存在", "difficulty": "5"},
        )
        .json()
    )
    assert daily["problem"]["id"] == second["id"]
    assert daily["assignment"]["filters"] == {"tag": "考研数学"}


def test_collection_migration_preserves_edits_versions_and_learning_state(
    env, monkeypatch
):
    db = env["db"]
    p = db.get("problems", id="test-q1")
    p.update(
        id="cmc-nonmath-test-q1",
        title="人工修订标题",
        tags=["全国大学生数学竞赛", "非数学类", "高等数学", "2025"],
        difficulty=5,
        manual_revision=True,
        version=7,
    )
    db.put("problems", p)
    answer = db.get("problem_answers", problem_id="test-q1")
    answer["problem_id"] = p["id"]
    db.put("problem_answers", answer)
    assert submit(env["one"], pid=p["id"], version=7).status_code == 200
    progress_before = db.list("user_problem_progress")
    with db.connect() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=4")
    monkeypatch.setattr(config, "ROOT", Path(__file__).resolve().parents[1])
    migrated = LocalStore(db.path)
    revised = migrated.get("problems", id=p["id"])
    assert revised["tags"] == p["tags"]
    assert revised["difficulty"] is None
    assert revised == {**p, "tags": revised["tags"], "difficulty": None}
    assert migrated.list("user_problem_progress") == progress_before
    assert migrated.get("problem_answers", problem_id=p["id"]) == answer
    revised["tags"] = ["新增题库分类"]
    migrated.put("problems", revised)
    assert LocalStore(db.path).get("problems", id=p["id"])["tags"] == ["新增题库分类"]


def test_preview_is_local_only(env, monkeypatch):
    monkeypatch.setattr(config, "PREVIEW", False)
    assert env["guest"].get("/api/v1/catalog").json()["total"] == 0
    assert env["guest"].get("/api/v1/problems/test-q1").status_code == 404


def test_random_resume_uses_latest_visible_question_and_filters(env):
    guest, db = env["guest"], env["db"]
    url = "/api/v1/practice/random?resume=test-q1"
    assert guest.get(url).json()["id"] == "test-q1"
    problem = db.get("problems", id="test-q1")
    problem.update(title="修改后的题目", version=2)
    db.put("problems", problem)
    restored = guest.get(url).json()
    assert restored["title"] == "修改后的题目" and restored["version"] == 2
    assert restored["progress"] is None
    assert not db.list("user_problem_progress") and not db.list("daily_assignments")
    assert guest.get(url + "&year=2024").json()["id"] == "test-q3"
    assert guest.get(url + "&exclude=test-q1").json()["id"] != "test-q1"
    problem["status"] = "archived"
    db.put("problems", problem)
    assert guest.get(url).json()["id"] != "test-q1"
    assert guest.get(url + "&type=blank").status_code == 404
    assert guest.get("/api/v1/practice/random?resume=deleted").status_code == 200


def test_daily_selection_is_read_only_until_opened_and_locks_filters(env):
    client, db = env["one"], env["db"]
    problem = db.get("problems", id="test-q2")
    problem["tags"] = ["积分"]
    db.put("problems", problem)
    url = "/api/v1/practice/daily"
    assert env["guest"].get(url).status_code == 401
    assert client.get(url).json() == {"assignment": None}
    assert client.get("/api/v1/problems?tag=积分&page_size=1").json()["total"] == 1
    assert not db.list("daily_assignments")
    assert client.post(url, json={"tag": "不存在的标签"}).status_code == 404
    assert client.get(url).json() == {"assignment": None}
    first = client.post(url, json={"tag": "积分", "type": "solution"}).json()
    assert first["problem"]["id"] == "test-q2"
    assert first["assignment"]["filters"] == {"tag": "积分", "type": "solution"}
    assert client.get(url).json()["assignment"] == first["assignment"]
    assert env["two"].get(url).json() == {"assignment": None}
    assert (
        client.post(url, json={"type": "blank"}).json()["assignment"]
        == first["assignment"]
    )
    assert len(db.list("daily_assignments")) == 1
    assert client.get("/api/v1/me").json()["stats"]["checkins"] == 0


def test_self_assessment_daily_and_history(env):
    c = env["one"]
    first = c.post("/api/v1/practice/daily", json={"type": "blank"}).json()
    assert first["assignment"]["problem_id"] == "test-q1"
    assert (
        c.post("/api/v1/practice/daily", json={"year": "1900"}).json()["assignment"][
            "problem_id"
        ]
        == "test-q1"
    )
    assert c.get("/api/v1/problems/test-q1/answer").status_code == 403
    assert c.get("/api/v1/me").json()["stats"]["checkins"] == 0
    assert submit(c, text=" ").status_code == 400
    pending = submit(c).json()["progress"]
    assert c.get("/api/v1/problems/test-q1/answer").status_code == 200
    assert pending["result"] == "pending" and not pending["ever_correct"]
    assert c.get("/api/v1/me").json()["stats"]["checkins"] == 0
    wrong = c.patch(
        "/api/v1/me/problems/test-q1/self-assessment",
        json={"result": "incorrect", "revision": pending["revision"]},
    ).json()["progress"]
    assert (
        wrong["ever_incorrect"] and c.get("/api/v1/me").json()["stats"]["checkins"] == 1
    )
    next_state = submit(c).json()["progress"]
    assert next_state["result"] == "pending"
    right = c.patch(
        "/api/v1/me/problems/test-q1/self-assessment",
        json={"result": "correct", "revision": next_state["revision"]},
    ).json()["progress"]
    assert right["ever_incorrect"] and right["ever_correct"] and right["can_publish"]
    assert c.get("/api/v1/me/history?scope=mistakes").json()["total"] == 1
    assert c.get("/api/v1/me").json()["stats"]["practiced"] == 1
    assert "attempts" not in right


def test_answer_requires_successful_submission_or_give_up(env):
    one, two, guest = env["one"], env["two"], env["guest"]
    url = "/api/v1/problems/test-q1/answer"
    give_up = "/api/v1/me/problems/test-q1/give-up"
    assert one.get(url).status_code == 403
    assert not one.get("/api/v1/problems/test-q1").json()["answer_unlocked"]
    assert one.get("/api/v1/media/answer-image").status_code == 403
    assert submit(one, text=" ").status_code == 400
    assert one.get(url).status_code == 403
    assert guest.post(give_up, json={"problem_version": 1}).status_code == 401
    assert one.post(give_up, json={"problem_version": 2}).status_code == 409
    assert one.get(url).status_code == 403
    data = one.post(give_up, json={"problem_version": 1}).json()
    assert data["answer_unlocked"] and data["progress"] is None
    assert one.get(url).json()["images"]
    assert one.get("/api/v1/media/answer-image").status_code == 200
    assert two.get(url).status_code == 403
    assert two.get("/api/v1/media/answer-image").status_code == 403
    assert not two.get("/api/v1/problems/test-q1").json()["answer_unlocked"]
    assert guest.get(url).status_code == 401
    one.post(give_up, json={"problem_version": 1})
    assert len(env["db"].list("answer_access")) == 1
    assert not env["db"].list("user_problem_progress")
    assert one.get("/api/v1/me").json()["stats"]["practiced"] == 0
    assert one.get("/api/v1/me").json()["stats"]["checkins"] == 0
    assert one.get("/api/v1/problems/test-q2/answer").status_code == 403
    assert one.get("/api/v1/admin/problems").status_code == 403
    assert env["admin"].get("/api/v1/admin/problems").json()[0]["answer"]["images"]
    assert env["admin"].get("/api/v1/media/answer-image").status_code == 200


def test_give_up_keeps_progress_and_daily_assignment_unchanged(env):
    c, db = env["one"], env["db"]
    day = c.post("/api/v1/practice/daily", json={"type": "blank"}).json()["assignment"]
    assert (
        c.post(
            "/api/v1/me/problems/test-q1/give-up", json={"problem_version": 1}
        ).status_code
        == 200
    )
    assert db.get("daily_assignments", id=day["id"]) == day
    pending = submit(c).json()["progress"]
    c.patch(
        "/api/v1/me/problems/test-q1/self-assessment",
        json={"result": "correct", "revision": pending["revision"]},
    )
    before = c.get("/api/v1/problems/test-q1").json()["progress"]
    after = c.post(
        "/api/v1/me/problems/test-q1/give-up", json={"problem_version": 1}
    ).json()["progress"]
    assert after == before
    p = db.get("problems", id="test-q1")
    p["version"] += 1
    db.put("problems", p)
    assert c.get("/api/v1/problems/test-q1").json()["answer_unlocked"]
    assert c.get("/api/v1/problems/test-q1/answer").status_code == 200


def test_stale_assessment_and_question_revision(env):
    c = env["one"]
    old = submit(c).json()["progress"]
    new = submit(c, text="新的作答").json()["progress"]
    assert (
        c.patch(
            "/api/v1/me/problems/test-q1/self-assessment",
            json={"result": "correct", "revision": old["revision"]},
        ).status_code
        == 409
    )
    assert (
        c.patch(
            "/api/v1/me/problems/test-q1/self-assessment",
            json={"result": "correct", "revision": new["revision"]},
        ).status_code
        == 200
    )
    p = env["db"].get("problems", id="test-q1")
    p["version"] = 2
    env["db"].put("problems", p)
    assert c.get("/api/v1/problems/test-q1").json()["progress"]["outdated"]
    assert not c.get("/api/v1/problems/test-q1").json()["progress"]["can_publish"]
    assert submit(c).status_code == 409
    assert (
        c.post(
            "/api/v1/problems/test-q1/solutions", json={"body": "旧答案"}
        ).status_code
        == 403
    )


def test_upload_ownership_and_private_visibility(env):
    one, two = env["one"], env["two"]
    upload = one.post(
        "/api/v1/uploads",
        data={"purpose": "submission"},
        files={"file": ("answer.png", env["raw"], "image/png")},
    ).json()
    assert one.get(upload["url"]).status_code == 200
    assert two.get(upload["url"]).status_code == 403
    assert env["guest"].get(upload["url"]).status_code == 403
    assert (
        two.put(
            "/api/v1/me/problems/test-q1/submission",
            json={"image_ids": [upload["id"]], "problem_version": 1},
        ).status_code
        == 403
    )
    assert (
        one.put(
            "/api/v1/me/problems/test-q1/submission",
            json={"image_ids": [upload["id"]], "problem_version": 1},
        ).status_code
        == 200
    )
    assert two.get("/api/v1/problems/test-q1").json()["progress"] is None
    assert (
        one.post(
            "/api/v1/uploads",
            files={"file": ("fake.png", b"not an image", "image/png")},
        ).status_code
        == 400
    )


def test_daily_concurrency_and_rollover(env, monkeypatch):
    c = env["one"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(
            pool.map(
                lambda _: c.post("/api/v1/practice/daily", json={}).json(), range(8)
            )
        )
    assert len({a["assignment"]["problem_id"] for a in answers}) == 1
    assert len(env["db"].list("daily_assignments")) == 1
    monkeypatch.setattr(domain, "today", lambda: "2030-01-01")
    monkeypatch.setattr(main, "today", lambda: "2030-01-01")
    assert c.get("/api/v1/practice/daily").json() == {"assignment": None}
    c.post("/api/v1/practice/daily", json={})
    assert len(env["db"].list("daily_assignments")) == 2


def test_selection_grading_and_no_self_override(env):
    db = env["db"]
    p = db.get("problems", id="test-q2")
    p.update(
        type="choice",
        grading_mode="choice",
        choice_options=["A", "B", "C"],
        multiple=True,
    )
    db.put("problems", p)
    a = db.get("problem_answers", problem_id=p["id"])
    a["correct_options"] = ["A", "C"]
    db.put("problem_answers", a)
    c = env["one"]
    assert (
        c.put(
            "/api/v1/me/problems/test-q2/submission",
            json={"options": ["C", "A"], "problem_version": 1},
        ).json()["progress"]["result"]
        == "correct"
    )
    assert (
        c.put(
            "/api/v1/me/problems/test-q2/submission",
            json={"options": ["A"], "problem_version": 1},
        ).json()["progress"]["result"]
        == "incorrect"
    )
    assert (
        c.patch(
            "/api/v1/me/problems/test-q2/self-assessment",
            json={"result": "correct", "revision": 2},
        ).status_code
        == 400
    )


def test_community_moderation_likes_and_notifications(env):
    one, two, admin = env["one"], env["two"], env["admin"]
    pending = submit(one).json()["progress"]
    one.patch(
        "/api/v1/me/problems/test-q1/self-assessment",
        json={"result": "correct", "revision": pending["revision"]},
    )
    s = one.post(
        "/api/v1/problems/test-q1/solutions", json={"body": "我的解题思路"}
    ).json()
    assert two.get("/api/v1/problems/test-q1/solutions").json() == []
    assert len(one.get("/api/v1/problems/test-q1/solutions").json()) == 1
    assert two.get("/api/v1/admin/moderation").status_code == 403
    assert (
        admin.patch(
            "/api/v1/admin/moderation/solutions/" + s["id"],
            json={"status": "approved", "version": s["version"]},
        ).status_code
        == 200
    )
    assert len(two.get("/api/v1/problems/test-q1/solutions").json()) == 1
    for _ in range(3):
        assert two.put("/api/v1/solutions/" + s["id"] + "/like").status_code == 200
    assert len(env["db"].list("likes", kind="solution")) == 1
    assert len(one.get("/api/v1/me/notifications").json()) == 1
    comment = two.post(
        "/api/v1/solutions/" + s["id"] + "/comments", json={"body": "谢谢分享"}
    ).json()
    assert env["guest"].get("/api/v1/solutions/" + s["id"] + "/comments").json() == []
    admin.patch(
        "/api/v1/admin/moderation/comments/" + comment["id"],
        json={"status": "approved"},
    )
    assert len(one.get("/api/v1/me/notifications").json()) == 2
    updated = one.post(
        "/api/v1/problems/test-q1/solutions",
        json={"body": "修改后思路", "version": s["version"]},
    ).json()
    assert updated["status"] == "pending"
    assert (
        admin.patch(
            "/api/v1/admin/moderation/solutions/" + s["id"],
            json={"status": "approved", "version": s["version"]},
        ).status_code
        == 409
    )


def manifest_for(db):
    p = db.get("problems", id="test-q1")
    p["answer"] = {
        k: v
        for k, v in db.get("problem_answers", problem_id=p["id"]).items()
        if k != "problem_id"
    }
    return {
        "schema_version": 1,
        "sources": db.list("problem_sources"),
        "assets": [],
        "problems": [p],
    }


def test_reimport_manual_protection_and_extension(env):
    db = env["db"]
    manifest = manifest_for(db)
    assert importer.import_manifest(manifest)["unchanged"] == 1
    p = db.get("problems", id="test-q1")
    p.update(title="人工改过的标题", manual_revision=True, version=2)
    db.put("problems", p)
    assert importer.import_manifest(manifest)["protected"] == 1
    assert db.get("problems", id="test-q1")["title"] == "人工改过的标题"
    p2 = dict(manifest["problems"][0])
    p2["id"] = "new-stable-id"
    manifest["problems"] = [p2]
    assert importer.import_manifest(manifest)["created"] == 1
    assert db.get("problems", id="test-q1")["version"] == 2


def test_admin_edit_versions_and_publish_without_review(env):
    admin = env["admin"]
    pending = submit(env["one"]).json()["progress"]
    assert pending["result"] == "pending"
    body = {"version": 1, "title": "修改题目标题", "status": "published"}
    assert admin.put("/api/v1/admin/problems/test-q1", json=body).json()["version"] == 2
    saved = env["db"].get("problems", id="test-q1")
    assert saved["status"] == "published"
    assert not saved["verified"] and not saved["answer_verified"]
    assert env["one"].get("/api/v1/problems/test-q1").json()["progress"]["outdated"]
    assert admin.put("/api/v1/admin/problems/test-q1", json=body).status_code == 409
    assert (
        env["two"]
        .put("/api/v1/admin/problems/test-q1", json={"version": 2})
        .status_code
        == 403
    )


def test_auth_csrf_and_logout(env):
    c = env["one"]
    assert c.get("/api/v1/me").json()["user"]["role"] == "user"
    assert (
        c.post(
            "/api/v1/auth/logout", headers={"Origin": "https://evil.invalid"}
        ).status_code
        == 403
    )
    assert c.post("/api/v1/auth/logout").status_code == 200
    assert submit(c).status_code == 401
    assert (
        c.post(
            "/api/v1/auth/login", json={"email": "one@test.local", "password": "wrong"}
        ).status_code
        == 400
    )


def test_published_import_needs_images_without_review_gate(env):
    manifest = manifest_for(env["db"])
    manifest["problems"][0].update(status="published")
    response = env["admin"].post("/api/v1/admin/import", json=manifest)
    assert response.status_code == 200
    manifest["problems"][0]["answer"]["images"] = []
    response = env["admin"].post("/api/v1/admin/import", json=manifest)
    assert response.status_code == 400


def test_create_question_from_uploaded_assets_and_preserve_id(env):
    manifest = manifest_for(env["db"])
    p = manifest["problems"][0]
    p["id"] = "test-new-question"
    p["source_id"] = "new-source"
    p.pop("updated_at")
    p.pop("status")  # New questions publish by default, with no review flags.
    p["source"] = {
        "id": "new-source",
        "name": "新题源",
        "source_url": "",
        "rights_note": "待确认",
        "verified": False,
    }
    response = env["admin"].post("/api/v1/admin/problems", json=p)
    assert response.status_code == 200 and response.json()["created"] == 1
    assert env["guest"].get("/api/v1/problems/test-new-question").status_code == 200
    assert env["admin"].post("/api/v1/admin/problems", json=p).status_code == 409
    assert env["db"].get("problems", id="test-new-question")["manual_revision"]
    assert env["db"].get("problems", id="test-new-question")["status"] == "published"


def test_concurrent_submission_avoids_lost_revision(env):
    c = env["one"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(
            pool.map(lambda n: submit(c, text=f"答案 {n}").json()["progress"], range(8))
        )
    assert max(p["revision"] for p in rows) == 8
    assert (
        env["db"].get(
            "user_problem_progress",
            user_id=c.get("/api/v1/me").json()["user"]["id"],
            problem_id="test-q1",
        )["revision"]
        == 8
    )


def test_profile_cannot_escalate_role_and_local_rate_counter(env):
    from backend import rate_limit

    c = env["one"]
    response = c.patch(
        "/api/v1/me/profile", json={"nickname": "更新昵称", "role": "admin"}
    )
    assert response.status_code == 200
    assert (
        response.json()["nickname"] == "更新昵称" and response.json()["role"] == "user"
    )
    # Bypass the middleware test stub to exercise a real shared DB rate counter.
    import importlib

    original = rate_limit.allow
    importlib.reload(rate_limit)
    rate_limit.store = env["db"]
    try:
        assert rate_limit.allow("test-identity", 2)
        assert rate_limit.allow("test-identity", 2)
        assert not rate_limit.allow("test-identity", 2)
    finally:
        rate_limit.allow = original


def test_hide_question_keeps_history_and_published_media_visibility(env):
    c = env["one"]
    submit(c)
    p = env["db"].get("problems", id="test-q1")
    p["status"] = "archived"
    env["db"].put("problems", p)
    assert c.get("/api/v1/problems/test-q1").status_code == 404
    assert env["db"].list("user_problem_progress")
    assert c.get("/api/v1/me/history").json()["total"] == 0
