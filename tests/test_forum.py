from copy import deepcopy

import httpx
from conftest import submit
from test_cloud_reads import cloud

from backend import main


def seed(env, count=15):
    db = env["db"]
    user = env["one"].get("/api/v1/me").json()["user"]
    base = db.get("problems", id="test-q1")
    result = []
    for i in range(count):
        p = {**base, "id": f"forum-q{i:02}", "status": "published"}
        db.put("problems", p)
        s = {
            "id": user["id"] + ":" + p["id"],
            "user_id": user["id"],
            "problem_id": p["id"],
            "body": f"题解 {i}",
            "image_ids": [],
            "status": "approved",
            "version": 1,
            "created_at": f"2026-10-01T00:{i:02}:00+00:00",
            "updated_at": "2026-10-25T00:00:00+00:00",
        }
        db.put("solutions", s)
        result.append(s)
    return result


def test_public_feed_is_ordered_paginated_and_private_data_is_not_exposed(env):
    rows = seed(env)
    one, guest, db = env["one"], env["guest"], env["db"]
    # All excluded rows are newer than the visible feed. Filter before pagination.
    for row, status in zip(rows[-3:], ("pending", "hidden", "approved")):
        db.put("solutions", {**row, "status": status})
    db.put(
        "problems",
        {**db.get("problems", id=rows[-1]["problem_id"]), "status": "archived"},
    )
    first = guest.get("/api/v1/forum?page_size=5").json()
    second = guest.get("/api/v1/forum?page=2&page_size=5").json()
    third = guest.get("/api/v1/forum?page=3&page_size=5").json()
    assert first["total"] == second["total"] == third["total"] == 12
    assert [x["id"] for x in first["items"] + second["items"] + third["items"]] == [
        s["id"] for s in reversed(rows[:12])
    ]
    assert one.get("/api/v1/forum?page_size=5").json() == first
    post = first["items"][0]
    assert post["author"] == "one" and post["problem"]["title"] == "测试题 1"
    assert set(post) == {
        "id",
        "author",
        "body",
        "images",
        "created_at",
        "updated_at",
        "problem",
    }
    assert set(post["problem"]) == {"id", "title", "type", "year", "tags"}
    assert guest.get("/api/v1/forum?page=99").json()["items"] == []
    for query in ("page=0", "page_size=0", "page_size=51", "page=oops"):
        assert guest.get("/api/v1/forum?" + query).status_code == 422
    # Preview drafts are not public forum content, even for their author/admin.
    db.put(
        "problems", {**db.get("problems", id=rows[0]["problem_id"]), "status": "draft"}
    )
    assert env["admin"].get("/api/v1/forum").json()["total"] == 11


def test_publication_edit_and_hide_follow_existing_moderation(env):
    one, guest, admin = env["one"], env["guest"], env["admin"]
    p = env["db"].get("problems", id="test-q1")
    env["db"].put("problems", {**p, "status": "published"})
    revision = submit(one).json()["progress"]["revision"]
    one.patch(
        "/api/v1/me/problems/test-q1/self-assessment",
        json={"result": "correct", "revision": revision},
    )
    published = one.post(
        "/api/v1/problems/test-q1/solutions", json={"body": "我的题解"}
    ).json()
    sid = published["id"]
    assert guest.get("/api/v1/forum").json()["total"] == 0
    url = "/api/v1/admin/moderation/solutions/" + sid
    assert (
        admin.patch(url, json={"status": "approved", "version": 1}).status_code == 200
    )
    initial = guest.get("/api/v1/forum").json()["items"][0]
    assert initial["body"] == "我的题解"
    assert (
        one.post(
            "/api/v1/problems/test-q1/solutions", json={"body": "修改后", "version": 1}
        ).status_code
        == 200
    )
    assert guest.get("/api/v1/forum").json()["total"] == 0
    admin.patch(url, json={"status": "approved", "version": 2})
    current = guest.get("/api/v1/forum").json()["items"][0]
    assert (
        current["created_at"] == initial["created_at"] and current["body"] == "修改后"
    )
    assert one.delete("/api/v1/solutions/" + sid).status_code == 200
    assert guest.get("/api/v1/forum").json()["total"] == 0


def test_cloud_feed_filters_and_joins_before_page_and_batches_images(env, monkeypatch):
    calls = []
    post = {
        "id": "alice:q",
        "body": "图解",
        "image_ids": ["b", "a"],
        "author": {"nickname": "Alice"},
        "problem": {
            "id": "q",
            "title": "Question",
            "type": "solution",
            "year": 2025,
            "tags": ["数学"],
        },
        "created_at": "2026-10-25T00:00:00+00:00",
        "updated_at": "2026-10-25T01:00:00+00:00",
    }

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/solutions"):
            return httpx.Response(
                200, json=[deepcopy(post)], headers={"Content-Range": "12-12/13"}
            )
        assert request.url.path.endswith("/media_assets")
        return httpx.Response(
            200, json=[{"id": aid, "width": 100, "height": 50} for aid in ("a", "b")]
        )

    repo = cloud(handler, monkeypatch)
    monkeypatch.setattr(main, "store", repo)
    data = env["guest"].get("/api/v1/forum?page=2").json()
    params = calls[0].url.params
    assert params["status"] == "eq.approved"
    assert params["problem.status"] == "eq.published"
    assert (
        "problems!inner" in params["select"]
        and "profiles(nickname)" in params["select"]
    )
    assert "email" not in params["select"] and "progress" not in params["select"]
    assert params["order"] == "created_at.desc,id.desc"
    assert params["offset"] == "12" and params["limit"] == "12"
    assert calls[0].headers["Prefer"] == "count=exact"
    assert len(calls) == 2 and data["total"] == 13
    assert [i["id"] for i in data["items"][0]["images"]] == ["b", "a"]
    assert data["items"][0]["images"][0]["url"] == "/api/v1/media/b"
    repo.client.close()


def test_equal_timestamps_have_stable_order(env):
    rows = seed(env, 3)
    for row in rows:
        env["db"].put("solutions", {**row, "created_at": rows[0]["created_at"]})
    ids = [
        env["guest"].get(f"/api/v1/forum?page={n}&page_size=1").json()["items"][0]["id"]
        for n in (1, 2, 3)
    ]
    assert ids == sorted([row["id"] for row in rows], reverse=True)


def test_cloud_page_removed_by_moderation_returns_remaining_total(monkeypatch):
    def handler(_request):
        return httpx.Response(
            416, headers={"Content-Range": "*/12"}, json={"code": "PGRST103"}
        )

    repo = cloud(handler, monkeypatch)
    assert repo.forum_page(2, 12) == {"total": 12, "rows": []}
    repo.client.close()
