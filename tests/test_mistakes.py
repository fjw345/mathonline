"""Mistake membership must not fabricate attempts, grades, or check-ins."""

from pathlib import Path

import httpx
import pytest
from conftest import submit

from backend import config, main
from backend.store import LocalStore


def ids(client, scope="mistakes"):
    data = client.get("/api/v1/me/history", params={"scope": scope}).json()
    return [p["id"] for p in data["items"]]


def give_up(client, pid="test-q1"):
    return client.post(
        f"/api/v1/me/problems/{pid}/give-up", json={"problem_version": 1}
    )


def assess(client, result):
    revision = submit(client).json()["progress"]["revision"]
    return client.patch(
        "/api/v1/me/problems/test-q1/self-assessment",
        json={"result": result, "revision": revision},
    )


def test_give_up_adds_mistake_without_submission_or_checkin(env):
    one, two = env["one"], env["two"]
    day = one.post("/api/v1/practice/daily", json={"type": "blank"}).json()[
        "assignment"
    ]
    assert give_up(one).status_code == 200
    assert ids(one) == ["test-q1"]
    assert ids(two) == []
    assert ids(one, "all") == []
    stats = one.get("/api/v1/me").json()["stats"]
    assert stats["mistakes"] == 1
    assert stats["practiced"] == stats["checkins"] == stats["correct"] == 0
    assert env["db"].get("daily_assignments", id=day["id"]) == day
    assert not env["db"].list("user_problem_progress")
    assert give_up(one).status_code == 200
    assert ids(one) == ["test-q1"]


def test_master_removes_only_own_membership_and_is_idempotent(env):
    one, two, guest = env["one"], env["two"], env["guest"]
    give_up(one)
    give_up(two)
    url = "/api/v1/me/mistakes/test-q1"
    assert guest.delete(url).status_code == 401
    assert one.delete(url).status_code == 200
    assert one.delete(url).status_code == 200
    assert ids(one) == []
    assert ids(two) == ["test-q1"]
    assert one.get("/api/v1/me").json()["stats"]["mistakes"] == 0
    assert one.get("/api/v1/problems/test-q1/answer").status_code == 200
    assert not env["db"].list("user_problem_progress")
    assert one.delete("/api/v1/me/mistakes/unknown").status_code == 200
    assert give_up(one).status_code == 200
    assert ids(one) == ["test-q1"]


def test_master_preserves_history_and_correct_work_does_not_restore_mistake(env):
    one = env["one"]
    assert assess(one, "incorrect").status_code == 200
    assert ids(one) == ["test-q1"]
    assert assess(one, "correct").status_code == 200
    assert ids(one) == ["test-q1"]
    before = one.get("/api/v1/problems/test-q1").json()["progress"]
    assert one.delete("/api/v1/me/mistakes/test-q1").status_code == 200
    after = one.get("/api/v1/problems/test-q1").json()["progress"]
    assert after == before
    assert after["ever_incorrect"] and after["ever_correct"] and after["can_publish"]
    assert ids(one, "all") == ["test-q1"]
    assert assess(one, "correct").status_code == 200
    assert ids(one) == []
    assert assess(one, "incorrect").status_code == 200
    assert ids(one) == ["test-q1"]


def test_local_backfill_and_restart_keep_mastery(env, monkeypatch):
    one, db = env["one"], env["db"]
    give_up(one)
    assess(one, "incorrect")
    db.delete("user_mistakes", problem_id="test-q1")
    # Fixtures point ROOT at a temporary asset directory; use the real schema here.
    monkeypatch.setattr(config, "ROOT", Path(__file__).resolve().parents[1])
    reopened = LocalStore(db.path)
    assert len(reopened.list("user_mistakes", active=True)) == 1
    monkeypatch.setattr(main, "store", reopened)
    one.delete("/api/v1/me/mistakes/test-q1")
    restarted = LocalStore(db.path)
    assert restarted.list("user_mistakes", active=True) == []
    assert len(restarted.list("answer_access")) == 1
    assert restarted.list("user_problem_progress")[0]["ever_incorrect"]


def test_cloud_reads_survive_pending_migration_and_mastery_is_not_faked(
    env, monkeypatch
):
    one, db = env["one"], env["db"]
    give_up(one)

    class PendingMigration:
        def list(self, table, **where):
            if table == "user_mistakes":
                response = httpx.Response(
                    404,
                    json={"code": "PGRST205"},
                    request=httpx.Request("GET", "https://test.invalid"),
                )
                response.raise_for_status()
            return db.list(table, **where)

        def get(self, table, **where):
            return next(iter(self.list(table, **where)), None)

    monkeypatch.setattr(main, "store", PendingMigration())
    assert one.get("/api/v1/me").json()["stats"]["mistakes"] == 1
    assert ids(one) == ["test-q1"]
    response = one.delete("/api/v1/me/mistakes/test-q1")
    assert response.status_code == 503
    assert ids(one) == ["test-q1"]


def test_other_database_errors_are_not_hidden(env, monkeypatch):
    class BrokenStore:
        def list(self, *_args, **_kwargs):
            response = httpx.Response(
                500,
                json={"code": "XX000"},
                request=httpx.Request("GET", "https://test.invalid"),
            )
            response.raise_for_status()

    monkeypatch.setattr(main, "store", BrokenStore())
    with pytest.raises(httpx.HTTPStatusError):
        main.mistake_rows({"id": "one"})
