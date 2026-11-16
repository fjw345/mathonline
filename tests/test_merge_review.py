"""Regression checks for integrating dev_ai; all model responses are offline."""

from copy import deepcopy

import httpx
import pytest

from conftest import submit
from backend import domain, grading
from backend.store import LocalStoreView
from test_cloud_reads import cloud


VERDICT = {
    "result": "correct", "score": 1, "max_score": 1,
    "points": [], "model": "offline-model",
}


def test_daily_preserves_requested_knowledge_and_empty_range(env):
    db, client = env["db"], env["one"]
    problem = db.get("problems", id="test-q2")
    problem["knowledge_points"] = ["limit"]
    db.put("problems", problem)
    response = client.post("/api/v1/practice/daily", json={"knowledge": "limit"})
    assert response.status_code == 200
    body = response.json()
    assert body["problem"]["id"] == "test-q2"
    assert body["assignment"]["filters"]["knowledge"] == "limit"
    again = client.post("/api/v1/practice/daily", json={"knowledge": "derivative"})
    assert again.json()["problem"]["id"] == "test-q2"
    empty = env["two"].post(
        "/api/v1/practice/daily", json={"knowledge": "definite_integral"}
    )
    assert empty.status_code == 404


def test_cloud_grading_does_not_overwrite_new_submission(monkeypatch):
    class ConcurrentStore:
        def __init__(self):
            self.calls = []
            self.row = {"id": "u:p", "revision": 2, "text": "new submission"}

        def rpc(self, name, args):
            self.calls.append((name, args))
            # Atomic grading committed revision 1; then a new submission arrives.
            return {"id": "u:p", "revision": 1, "result_source": "ai"}

        def put(self, table, data):
            pytest.fail("An RPC result must never be upserted outside its transaction")

    repository = ConcurrentStore()
    monkeypatch.setattr(domain, "store", repository)
    domain.apply_progress(
        {"id": "u"}, {"id": "p", "version": 1}, "", [], "correct", 1,
        result_source="ai", grading_snapshot=VERDICT,
    )
    assert repository.row["revision"] == 2
    name, args = repository.calls[0]
    assert name == "record_ai_grading"
    assert args["p_revision"] == 1 and args["p_grading"] == VERDICT


@pytest.mark.parametrize("intervening", ["submit", "self_assess"])
def test_slow_grading_cannot_overwrite_intervening_action(env, monkeypatch, intervening):
    db, client = env["db"], env["one"]
    first = submit(client, pid="test-q2", text="first").json()["progress"]
    answer = db.get("problem_answers", problem_id="test-q2")
    db.put("problem_answers", {**answer, "text": "reference"})

    def slow_judge(*args):
        if intervening == "submit":
            assert submit(client, pid="test-q2", text="new").status_code == 200
        else:
            assert client.patch(
                "/api/v1/me/problems/test-q2/self-assessment",
                json={"result": "incorrect", "revision": first["revision"]},
            ).status_code == 200
        return VERDICT

    monkeypatch.setattr(grading, "judge", slow_judge)
    response = client.post(
        "/api/v1/me/problems/test-q2/grade",
        json={"problem_version": 1, "revision": first["revision"]},
    )
    assert response.status_code == 409
    state = db.get("user_problem_progress", id=first["id"])
    if intervening == "submit":
        assert state["text"] == "new" and state["revision"] == first["revision"] + 1
    else:
        assert state["result"] == "incorrect" and state["result_source"] == "self_assessment"
    assert db.get("problem_grading", problem_id="test-q2") is None


def test_snapshot_failure_rolls_back_progress_mistakes_and_checkin(env, monkeypatch):
    db, client = env["db"], env["one"]
    assignment = client.post("/api/v1/practice/daily", json={}).json()["assignment"]
    pid = assignment["problem_id"]
    state = submit(client, pid=pid).json()["progress"]
    before = db.get("user_problem_progress", id=state["id"])
    answer = db.get("problem_answers", problem_id=pid)
    db.put("problem_answers", {**answer, "text": "reference"})
    original_put = LocalStoreView.put

    def fail_snapshot(self, table, data):
        if table == "problem_grading":
            raise RuntimeError("simulated storage failure")
        return original_put(self, table, data)

    monkeypatch.setattr(LocalStoreView, "put", fail_snapshot)
    monkeypatch.setattr(grading, "judge", lambda *args: {**VERDICT, "result": "incorrect"})
    response = client.post(
        f"/api/v1/me/problems/{pid}/grade",
        json={"problem_version": 1, "revision": state["revision"]},
    )
    assert response.status_code == 503
    assert db.get("user_problem_progress", id=state["id"]) == before
    assert db.get("daily_assignments", id=assignment["id"])["completed_at"] is None
    assert db.get("user_mistakes", user_id=state["user_id"], problem_id=pid) is None


def test_old_snapshot_is_hidden_after_new_submission(env, monkeypatch):
    db, client = env["db"], env["one"]
    state = submit(client, pid="test-q2").json()["progress"]
    answer = db.get("problem_answers", problem_id="test-q2")
    db.put("problem_answers", {**answer, "text": "reference"})
    monkeypatch.setattr(grading, "judge", lambda *args: deepcopy(VERDICT))
    response = client.post(
        "/api/v1/me/problems/test-q2/grade",
        json={"problem_version": 1, "revision": state["revision"]},
    )
    assert response.status_code == 200
    assert response.json()["problem"]["progress"]["ai_grading"]["result"] == "correct"
    repeated = submit(client, pid="test-q2", text="new attempt")
    assert repeated.json()["progress"]["ai_grading"] is None


def test_cloud_filter_groups_are_combined_not_overwritten(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[], headers={"Content-Range": "*/0"})

    db = cloud(handler, monkeypatch)
    db.problem_page({"tag": "A,B", "knowledge": "limit,derivative"}, 1, 12)
    params = calls[-1].url.params
    assert "or" not in params
    assert params["and"].startswith("(or.(tags.cs.")
    assert "),or.(knowledge_points.cs." in params["and"]


def test_cloud_keyword_search_matches_knowledge_names(monkeypatch):
    def handler(request):
        return httpx.Response(200, json=[
            {"id": "p", "title": "example", "tags": [], "knowledge_points": ["limit"]},
        ])

    db = cloud(handler, monkeypatch)
    assert db.problem_page({"q": "极限"}, 1, 12)["total"] == 1
