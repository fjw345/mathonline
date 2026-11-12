"""北交考试预测与模拟卷的离线测试：蓝图、组卷、AI 生题校验与交卷判分。"""

import json

import pytest

from backend import ai, config, mockexam, question_gen


def add_problem(db, pid, kind, year, knowledge, difficulty=3, answer="2"):
    options = ["A", "B", "C", "D"] if kind == "choice" else []
    correct = ["A"] if kind == "choice" else []
    problem = {
        "id": pid,
        "source_id": "test-source",
        "title": "模拟题 " + pid,
        "type": kind,
        "year": year,
        "group_name": "期末考试",
        "subject": "微积分上",
        "tags": ["北交期末·微积分上"],
        "difficulty": difficulty,
        "status": "published",
        "version": 1,
        "grading_mode": "choice" if kind == "choice" else "self_assessment",
        "question_images": [],
        "question_text": "题目 %s：已知条件，求结果。" % pid,
        "knowledge_points": knowledge,
        "knowledge_source": "manual",
        "choice_options": options,
        "multiple": False,
        "verified": True,
        "answer_verified": True,
        "manual_revision": False,
        "updated_at": "2026-11-13T00:00:00+00:00",
    }
    db.put("problems", problem)
    db.put(
        "problem_answers",
        {
            "problem_id": pid,
            "images": [],
            "text": answer,
            "correct_options": correct,
            "provenance": "测试",
        },
    )
    return problem


@pytest.fixture
def bank(env):
    db = env["db"]
    knowledge = [["limit"], ["derivative"], ["definite_integral"], ["mean_value"]]
    for year in (2022, 2023, 2024):
        for index, kind in enumerate(("choice", "blank", "solution")):
            for item in range(4):
                pid = "mockbank-%d-%s-%d" % (year, kind, item)
                add_problem(
                    db,
                    pid,
                    kind,
                    year,
                    knowledge[(index + item) % len(knowledge)],
                    difficulty=2 + item % 3,
                    answer=str(item + 1),
                )
    return env


def stub_chat(state):
    def chat(messages, **kwargs):
        body = "\n".join(message["content"] for message in messages)
        state["calls"] += 1
        if "往年试卷统计" in body:
            return json.dumps(
                {
                    "title": "北京交通大学 微积分上 期末预测卷",
                    "duration_minutes": 120,
                    "sections": [
                        {
                            "type": "choice",
                            "count": 2,
                            "points_each": 3,
                            "knowledge": ["limit"],
                            "note": "基础题",
                        },
                        {
                            "type": "blank",
                            "count": 2,
                            "points_each": 4,
                            "knowledge": ["derivative"],
                            "note": "计算",
                        },
                        {
                            "type": "solution",
                            "count": 3,
                            "points_each": 10,
                            "knowledge": ["definite_integral", "mean_value"],
                            "note": "综合",
                        },
                    ],
                    "emphasis": [{"code": "limit", "reason": "高频"}],
                    "rationale": "按近三年分布预测",
                },
                ensure_ascii=False,
            )
        if "仿照" in body:
            state["generated"] += 1
            if "把新题写成选择题" in body:
                return json.dumps(
                    {
                        "stem": "下列定积分等于 2 的是（ ）。",
                        "options": [
                            "A. $\\int_0^1 2x\\,dx$",
                            "B. $\\int_0^2 x\\,dx$",
                            "C. $\\int_0^1 x\\,dx$",
                            "D. $\\int_0^2 2x\\,dx$",
                        ],
                        "correct": "B",
                        "answer": "B",
                        "explanation": "$\\int_0^2 x\\,dx=2$。",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "stem": "计算 $\\int_0^2 3x^2\\,dx$ 的值。",
                    "options": [],
                    "correct": "",
                    "answer": "8",
                    "explanation": "原函数为 $x^3$，代入得 8。",
                },
                ensure_ascii=False,
            )
        if "独立求解" in body:
            state["verified"] += 1
            if "正确选项" in body:
                return json.dumps(
                    {
                        "solvable": True,
                        "unique": True,
                        "consistent": True,
                        "correct": "B",
                        "reason": "唯一正确选项",
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "solvable": True,
                    "unique": True,
                    "consistent": True,
                    "answer": "8",
                    "reason": "可解且答案唯一",
                },
                ensure_ascii=False,
            )
        if "判断学生作答与参考答案是否数学等价" in body:
            return json.dumps({"correct": True, "note": "等价"}, ensure_ascii=False)
        if "按满分" in body:
            return json.dumps({"score": 10, "feedback": "步骤完整"}, ensure_ascii=False)
        return json.dumps({"ok": True})

    return chat


def test_subject_profile_summarizes_structure(bank):
    problems = bank["db"].list("problems")
    profile = mockexam.subject_profile("微积分上", problems)
    assert profile["problems"] == 36
    assert profile["years"][:3] == [2024, 2023, 2022]
    assert profile["type_mix"][
        "choice"
    ] >= 2 and profile["type_mix"]["solution"] >= 2
    codes = {item["code"] for item in profile["knowledge"]}
    assert "limit" in codes and "definite_integral" in codes
    assert 1 <= profile["difficulty"]["mean"] <= 5
    assert profile["samples"]["choice"]


def test_fallback_blueprint_always_leaves_room_for_ai_item(bank):
    profile = mockexam.subject_profile("微积分上", bank["db"].list("problems"))
    blueprint = mockexam._fallback_blueprint(profile)
    assert blueprint["sections"]
    assert any(
        section["type"] in ("choice", "blank") and section["count"] >= 2
        for section in blueprint["sections"]
    )
    assert mockexam._ensure_ai_slot(
        [
            {"type": "choice", "count": 1},
            {"type": "solution", "count": 5},
        ]
    )[0]["count"] == 2


def test_question_gen_rejects_identical_and_verifies_answer(monkeypatch):
    seed = {
        "id": "seed",
        "type": "blank",
        "subject": "微积分上",
        "question_text": "计算 $\\int_0^1 x\\,dx$。",
    }
    replies = iter(
        [
            json.dumps(
                {
                    "stem": seed["question_text"],
                    "options": [],
                    "correct": "",
                    "answer": "1/2",
                    "explanation": "同题",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "stem": "计算 $\\int_0^2 x\\,dx$。",
                    "options": [],
                    "correct": "",
                    "answer": "2",
                    "explanation": "原函数",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "solvable": True,
                    "unique": True,
                    "consistent": True,
                    "answer": "2",
                    "reason": "",
                },
                ensure_ascii=False,
            ),
        ]
    )
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(ai, "chat", lambda *args, **kwargs: next(replies))
    generated = question_gen.generate_verified(seed, "1/2", "微积分上", "blank")
    assert generated["answer_text"] == "2"
    assert generated["question_text"].startswith("计算 $\\int_0^2")
    assert question_gen.answers_match("1/2", "0.5")
    assert not question_gen.answers_match("1/2", "0.4")


def test_create_paper_uses_bank_and_ai_item(bank, monkeypatch):
    db = bank["db"]
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "AI_MODEL", "stub-model")
    state = {"calls": 0, "generated": 0, "verified": 0}
    monkeypatch.setattr(ai, "chat", stub_chat(state))
    problems = db.list("problems")
    answers = {a["problem_id"]: a for a in db.list("problem_answers")}
    paper = mockexam.create_paper(
        {"id": "u1"}, "微积分上", "standard", problems, answers, set()
    )
    assert paper["status"] == "ready"
    assert len(paper["items"]) == 7
    assert paper["max_score"] == 2 * 3 + 2 * 4 + 3 * 10
    ai_items = [item for item in paper["items"] if item["origin"] == "ai"]
    assert len(ai_items) == 1
    assert ai_items[0]["type"] in ("choice", "blank")
    assert ai_items[0]["correct_options"] == []
    assert ai_items[0]["answer_text"] == "8"
    bank_ids = [
        item["problem_id"] for item in paper["items"] if item["origin"] == "bank"
    ]
    assert len(bank_ids) == len(set(bank_ids))
    assert state["generated"] == 1 and state["verified"] == 1
    assert paper["knowledge_summary"] == []
    hidden = mockexam.paper_payload(paper)
    assert "answer_text" not in hidden["items"][0]
    shown = mockexam.paper_payload(paper, include_answers=True)
    assert "answer_text" in shown["items"][0]


def test_mock_paper_endpoints_and_grading(bank, monkeypatch):
    client = bank["two"]
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "AI_MODEL", "stub-model")
    state = {"calls": 0, "generated": 0, "verified": 0}
    monkeypatch.setattr(ai, "chat", stub_chat(state))
    created = client.post(
        "/api/v1/mock/papers",
        json={"subject": "微积分上", "difficulty": "standard"},
    )
    assert created.status_code == 200
    paper = created.json()
    assert paper["status"] == "ready"
    assert all("answer_text" not in item for item in paper["items"])
    listed = client.get("/api/v1/mock/papers").json()
    assert listed["total"] == 1
    assert listed["items"][0]["ai_items"] == 1
    answers = []
    for item in paper["items"]:
        if item["type"] == "choice":
            answers.append({"item_id": item["id"], "options": ["A"]})
        else:
            answers.append({"item_id": item["id"], "text": "2"})
    submitted = client.put(
        "/api/v1/mock/papers/" + paper["id"] + "/submission",
        json={"answers": answers},
    )
    assert submitted.status_code == 200
    result = submitted.json()
    assert result["status"] == "submitted"
    assert all("answer_text" in item for item in result["items"])
    assert result["score"] > 0
    assert result["knowledge_summary"]
    again = client.put(
        "/api/v1/mock/papers/" + paper["id"] + "/submission",
        json={"answers": answers},
    )
    assert again.status_code == 409
    pending = [item for item in result["items"] if item["result"] == "pending"]
    if pending:
        reviewed = client.patch(
            "/api/v1/mock/papers/"
            + paper["id"]
            + "/items/"
            + pending[0]["id"],
            json={"result": "correct"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["score"] >= result["score"]
    assert client.delete("/api/v1/mock/papers/" + paper["id"]).status_code == 200
    assert client.get("/api/v1/mock/papers/" + paper["id"]).status_code == 404


def test_mock_creation_requires_ai(bank, monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    response = bank["two"].post(
        "/api/v1/mock/papers", json={"subject": "微积分上"}
    )
    assert response.status_code == 503
    assert "AI 生题未配置" in response.json()["detail"]
    assert bank["db"].list("mock_papers") == []


def test_mock_subjects_are_public(bank):
    response = bank["guest"].get("/api/v1/mock/subjects")
    assert response.status_code == 200
    body = response.json()
    subjects = {item["subject"]: item for item in body["subjects"]}
    assert "微积分上" in subjects
    assert subjects["微积分上"]["problems"] >= 6
    assert subjects["微积分上"]["ready"] is True
    assert body["ai"] is False
