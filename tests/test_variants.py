"""一题多练（AI 变式题）的离线测试：生成、答案隐藏、自评与降级。"""

import json

import pytest

from backend import ai, config, variants


def set_answer_text(db, pid, text):
    answer = db.get("problem_answers", problem_id=pid)
    answer["text"] = text
    db.put("problem_answers", answer)


def set_knowledge(db, pid, codes):
    problem = db.get("problems", id=pid)
    problem["knowledge_points"] = codes
    db.put("problems", problem)


def chat_stub(state):
    def chat(messages, **kwargs):
        body = "\n".join(message["content"] for message in messages)
        state["calls"] += 1
        if "仿照" in body:
            return json.dumps(
                {
                    "stem": "计算 $\\int_0^3 2x\\,dx$ 的值。",
                    "options": [],
                    "correct": "",
                    "answer": "9",
                    "explanation": "原函数为 $x^2$，代入得 9。",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "solvable": True,
                "unique": True,
                "consistent": True,
                "answer": "9",
                "explanation_answer": "9",
                "reason": "可解且唯一",
            },
            ensure_ascii=False,
        )

    return chat


def test_variant_payload_hides_answer_until_requested():
    row = {
        "id": "variant-1",
        "user_id": "u",
        "problem_id": "p",
        "subject": "高等数学",
        "type": "blank",
        "knowledge_points": ["limit"],
        "question_text": "新题",
        "choice_options": [],
        "multiple": False,
        "answer_text": "9",
        "correct_options": [],
        "explanation": "解析",
        "model": "stub",
        "result": "",
        "created_at": "2026-11-13T00:00:00+00:00",
        "answered_at": None,
    }
    hidden = variants.variant_payload(row)
    assert "answer_text" not in hidden
    assert hidden["knowledge_points"][0]["name"] == "极限与连续"
    shown = variants.variant_payload(row, include_answer=True)
    assert shown["answer_text"] == "9"
    assert shown["explanation"] == "解析"


def test_create_variant_endpoint(env, monkeypatch):
    db, client = env["db"], env["one"]
    set_answer_text(db, "test-q2", "参考解答：先求导再代入。")
    set_knowledge(db, "test-q2", ["definite_integral"])
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "AI_MODEL", "stub-model")
    state = {"calls": 0}
    monkeypatch.setattr(ai, "chat", chat_stub(state))
    response = client.post("/api/v1/me/problems/test-q2/variants")
    assert response.status_code == 200
    body = response.json()
    assert body["problem_id"] == "test-q2"
    assert body["type"] == "solution"
    assert "answer_text" not in body
    assert body["knowledge_points"][0]["code"] == "definite_integral"
    assert state["calls"] == 2
    listed = client.get("/api/v1/me/variants?problem_id=test-q2").json()
    assert listed["items"][0]["id"] == body["id"]
    answer = client.get("/api/v1/me/variants/" + body["id"] + "/answer")
    assert answer.status_code == 200
    assert answer.json()["answer_text"] == "9"
    marked = client.patch(
        "/api/v1/me/variants/" + body["id"], json={"result": "correct"}
    )
    assert marked.status_code == 200
    assert marked.json()["result"] == "correct"
    assert marked.json()["answered_at"]
    repeat = client.patch(
        "/api/v1/me/variants/" + body["id"], json={"result": "incorrect"}
    )
    assert repeat.status_code == 409
    assert db.get("practice_variants", id=body["id"])["result"] == "correct"


def test_other_user_cannot_read_variant(env, monkeypatch):
    db, client = env["db"], env["one"]
    set_answer_text(db, "test-q2", "参考解答：先求导再代入。")
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(ai, "chat", chat_stub({"calls": 0}))
    created = client.post("/api/v1/me/problems/test-q2/variants").json()
    response = env["two"].get("/api/v1/me/variants/" + created["id"] + "/answer")
    assert response.status_code == 404


def test_variant_requires_ai(env, monkeypatch):
    db, client = env["db"], env["one"]
    set_answer_text(db, "test-q2", "参考解答：先求导再代入。")
    monkeypatch.setattr(config, "AI_ENABLED", False)
    response = client.post("/api/v1/me/problems/test-q2/variants")
    assert response.status_code == 503
    assert "配置" in response.json()["detail"]
    assert db.list("practice_variants") == []


def test_variant_generation_failure_degrades(env, monkeypatch):
    db, client = env["db"], env["one"]
    set_answer_text(db, "test-q2", "参考解答：先求导再代入。")
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(ai, "chat", lambda *args, **kwargs: "不是 JSON")
    response = client.post("/api/v1/me/problems/test-q2/variants")
    assert response.status_code == 503
    assert "暂时无法生成可靠的变式" in response.json()["detail"]
    assert db.list("practice_variants") == []


def test_analysis_counts_variant_attempts(env, monkeypatch):
    db, client = env["db"], env["one"]
    set_answer_text(db, "test-q2", "参考解答：先求导再代入。")
    set_knowledge(db, "test-q2", ["definite_integral"])
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(ai, "chat", chat_stub({"calls": 0}))
    created = client.post("/api/v1/me/problems/test-q2/variants").json()
    client.patch("/api/v1/me/variants/" + created["id"], json={"result": "incorrect"})
    stats = client.get("/api/v1/me/analysis").json()["stats"]
    assert stats["variant_totals"] == {
        "practiced": 1,
        "correct": 0,
        "incorrect": 1,
    }
    bucket = next(
        item for item in stats["knowledge"] if item["code"] == "definite_integral"
    )
    assert bucket["variants"] == 1
    assert bucket["incorrect"] == 1
