"""学情分析的离线测试：聚合、报告校验与接口缓存，不发真实模型请求。"""

import json

from backend import ai, analysis, config

from conftest import submit


def problem(pid, codes, subject="高等数学"):
    return {
        "id": pid,
        "subject": subject,
        "knowledge_points": codes,
        "type": "blank",
    }


def test_aggregate_groups_by_knowledge_and_ranks_weakness():
    problems = {
        "p1": problem("p1", ["limit"]),
        "p2": problem("p2", ["limit", "derivative"]),
        "p3": problem("p3", ["derivative"]),
    }
    rows = [
        {"problem_id": "p1", "result": "correct"},
        {"problem_id": "p2", "result": "incorrect"},
        {"problem_id": "p3", "result": "pending"},
    ]
    stats = analysis.aggregate(rows, problems, {"p2"})
    assert stats["totals"] == {
        "practiced": 3,
        "correct": 1,
        "incorrect": 1,
        "pending": 1,
        "mistakes": 1,
    }
    buckets = {item["code"]: item for item in stats["knowledge"]}
    assert buckets["limit"]["attempts"] == 2
    assert buckets["limit"]["correct"] == 1
    assert buckets["limit"]["incorrect"] == 1
    assert buckets["limit"]["accuracy"] == 0.5
    assert buckets["limit"]["mistakes"] == 1
    assert buckets["derivative"]["pending"] == 1
    assert buckets["derivative"]["graded"] == 1
    weakest = [item["code"] for item in stats["weakest"]]
    assert weakest[0] in {"limit", "derivative"}
    assert set(weakest) == {"limit", "derivative"}
    assert stats["weakest"][0]["weakness"] >= stats["weakest"][1]["weakness"]
    assert stats["domains"][0]["domain"] == "calculus"
    assert stats["domains"][0]["attempts"] == 3
    assert stats["fingerprint"]


def test_fingerprint_changes_when_progress_changes():
    problems = {"p1": problem("p1", ["limit"])}
    first = analysis.aggregate(
        [{"problem_id": "p1", "result": "correct"}], problems, set()
    )
    same = analysis.aggregate(
        [{"problem_id": "p1", "result": "correct"}], problems, set()
    )
    changed = analysis.aggregate(
        [{"problem_id": "p1", "result": "incorrect"}], problems, set()
    )
    assert first["fingerprint"] == same["fingerprint"]
    assert first["fingerprint"] != changed["fingerprint"]


def test_report_validation_drops_unknown_codes_and_empty_output():
    stats = analysis.aggregate(
        [{"problem_id": "p1", "result": "incorrect"}],
        {"p1": problem("p1", ["limit"])},
        {"p1"},
    )
    payload = {
        "summary": "极限部分错题较多。",
        "strengths": [
            {"code": "limit", "reason": "基础题能做。"},
            {"code": "made-up", "reason": "无效"},
        ],
        "weaknesses": [
            {"code": "limit", "reason": "错题集中。", "suggestion": "重做错题。"}
        ],
        "plan": ["复习极限定义", "每天 3 题", "重做错题"],
    }
    report = analysis.validate_report(payload, stats)
    assert report["summary"] == "极限部分错题较多。"
    assert [item["code"] for item in report["strengths"]] == ["limit"]
    assert report["weaknesses"][0]["suggestion"] == "重做错题。"
    assert len(report["plan"]) == 3
    try:
        analysis.validate_report({}, stats)
    except ai.AIOutputError:
        pass
    else:
        raise AssertionError("空报告应被拒绝")


def test_generate_report_requires_ai(monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    try:
        analysis.generate_report({"knowledge": [], "domains": [], "totals": {}})
    except ai.AIUnavailable:
        pass
    else:
        raise AssertionError("未配置模型时应不可用")


def set_knowledge(db, pid, codes):
    row = db.get("problems", id=pid)
    row["knowledge_points"] = codes
    row["difficulty"] = 3
    row["knowledge_source"] = "manual"
    db.put("problems", row)


def mark_incorrect(client, pid, version=1):
    state = submit(client, pid=pid, text="我的解答", version=version).json()
    revision = state["progress"]["revision"]
    response = client.patch(
        "/api/v1/me/problems/" + pid + "/self-assessment",
        json={"result": "incorrect", "revision": revision},
    )
    assert response.status_code == 200
    return response.json()


def test_analysis_endpoint_reports_weakest_without_ai(env, monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    db, client = env["db"], env["one"]
    set_knowledge(db, "test-q1", ["limit"])
    set_knowledge(db, "test-q2", ["definite_integral"])
    set_knowledge(db, "test-q3", ["series"])
    mark_incorrect(client, "test-q1")
    state = submit(client, pid="test-q2", text="解答", version=1).json()
    client.patch(
        "/api/v1/me/problems/test-q2/self-assessment",
        json={"result": "correct", "revision": state["progress"]["revision"]},
    )
    response = client.get("/api/v1/me/analysis")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["report"] is None
    assert "配置" in body["reason"]
    stats = body["stats"]
    assert stats["totals"]["practiced"] == 2
    assert stats["totals"]["incorrect"] == 1
    assert stats["totals"]["correct"] == 1
    weakest = stats["weakest"][0]
    assert weakest["code"] == "limit"
    assert weakest["accuracy"] == 0.0
    assert weakest["mistakes"] == 1
    codes = {item["code"] for item in stats["knowledge"]}
    assert codes == {"limit", "definite_integral"}


def test_analysis_report_generation_and_cache(env, monkeypatch):
    db, client = env["db"], env["one"]
    set_knowledge(db, "test-q1", ["limit"])
    mark_incorrect(client, "test-q1")
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "AI_MODEL", "stub-model")
    calls = []

    def chat(messages, **kwargs):
        calls.append(messages)
        return json.dumps(
            {
                "summary": "极限需要加强。",
                "strengths": [],
                "weaknesses": [
                    {"code": "limit", "reason": "错题", "suggestion": "重做"}
                ],
                "plan": ["复习定义", "做 5 题"],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(ai, "chat", chat)
    response = client.post("/api/v1/me/analysis")
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["summary"] == "极限需要加强。"
    assert body["report"]["model"] == "stub-model"
    assert len(calls) == 1
    user_id = client.get("/api/v1/me").json()["user"]["id"]
    row = db.get("learning_analyses", id=user_id)
    assert row and row["fingerprint"] == body["stats"]["fingerprint"]
    again = client.get("/api/v1/me/analysis").json()
    assert again["report"]["summary"] == "极限需要加强。"
    assert len(calls) == 1
    refreshed = client.post("/api/v1/me/analysis?refresh=true")
    assert refreshed.status_code == 200
    assert len(calls) == 2


def test_analysis_report_unavailable_degrades(env, monkeypatch):
    db, client = env["db"], env["one"]
    set_knowledge(db, "test-q1", ["limit"])
    mark_incorrect(client, "test-q1")
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(ai, "chat", lambda *args, **kwargs: "不是 JSON")
    response = client.post("/api/v1/me/analysis")
    assert response.status_code == 503
    assert "JSON" in response.json()["detail"]
    assert db.get("learning_analyses", id=client.get("/api/v1/me").json()["user"]["id"]) is None
