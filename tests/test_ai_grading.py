"""AI 自动核对答案的离线测试：不配置真实模型，不发起任何网络请求。"""

import hashlib
import json

import httpx
import pytest

from conftest import submit

from backend import config, grading


@pytest.fixture
def offline(monkeypatch):
    """断网守卫：任何真实 HTTP 调用都会抛出断言，并记录被调用的地址。"""
    attempts = []

    def guard(*args, **kwargs):
        attempts.append(args[0] if args else "")
        raise AssertionError("AI 判分测试不允许真实 HTTP 请求")

    monkeypatch.setattr(httpx, "post", guard)
    return attempts


@pytest.fixture
def ai(env, offline, monkeypatch):
    """在临时库上跑判分：store 指向隔离库，模型默认未配置。"""
    monkeypatch.setattr(grading, "store", env["db"])
    monkeypatch.setattr(config, "AI_ENABLED", False)
    monkeypatch.setattr(config, "AI_MODEL", "stub-model")
    env["http"] = offline
    return env


def set_answer_text(db, pid, text):
    answer = db.get("problem_answers", problem_id=pid)
    answer["text"] = text
    db.put("problem_answers", answer)
    return answer


def solution_problem(pid="test-q2"):
    return {
        "id": pid,
        "type": "solution",
        "question_text": "求极限 lim(x->0) sin(x)/x。",
        "version": 1,
    }


def chat_stub(state):
    """按提示内容分流：生成得分点 / 逐点判分，并把调用次数记在 state 里。"""

    def chat(messages):
        body = messages[-1]["content"]
        state["calls"] += 1
        if "得分点列表：" in body:
            verdicts = [
                {"id": index + 1, "hit": hit, "note": "第%d点" % (index + 1)}
                for index, hit in enumerate(state["hits"])
            ]
            return json.dumps({"points": verdicts}, ensure_ascii=False)
        return json.dumps({"points": state["rubric"]}, ensure_ascii=False)

    return chat


def grade(client, pid, version=1, revision=1):
    return client.post(
        "/api/v1/me/problems/" + pid + "/grade",
        json={"problem_version": version, "revision": revision},
    )


def test_normalize_folds_notation_variants():
    assert grading.normalize("２") == "2"
    assert grading.normalize("ＡＢＣ") == "ABC"
    assert grading.normalize("$x^2$") == "x^2"
    assert grading.normalize("\\left( x \\right)") == "( x )"
    assert grading.normalize("\\dfrac{1}{2}") == "\\frac{1}{2}"
    assert grading.normalize(" 2   +\t3 ") == "2 + 3"
    assert grading.normalize("答案。") == "答案"
    assert grading.normalize("x=2；") == "x=2"
    assert grading.normalize("2.0") == "2.0"
    assert grading.normalize("") == ""
    assert grading.normalize(None) == ""


@pytest.mark.parametrize(
    ("reference", "submitted", "expected"),
    [
        ("2", "2.0", "correct"),
        ("1/2", "0.5", "correct"),
        ("\\dfrac{1}{2}", "0.5", "correct"),
        ("\\frac{1}{2}", "1/2", "correct"),
        ("1 或 2", "2", "correct"),
        ("1；2", "2", "correct"),
        ("2", "3", "incorrect"),
        ("1/2", "0.4", "incorrect"),
        ("x=2", "2", None),
        ("x=2", "y=3", None),
        ("\\begin{cases}1\\end{cases}", "1", None),
        ("2", "", None),
        ("", "2", None),
        ("x", "2", None),
        ("2", "x", None),
        ("2\n3", "2", None),
        ("1" * 81, "1", None),
    ],
)
def test_algorithmic_verdict_branches(reference, submitted, expected):
    assert grading.algorithmic_verdict(reference, submitted) is expected


def test_judge_without_ai_degrades_and_never_calls_http(offline, monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", False)
    blank = {"id": "test-q1", "type": "blank", "question_text": "2 的平方是多少"}
    assert grading.judge(solution_problem(), {"text": "详解"}, "我的作答") is None
    hit = grading.judge(blank, {"text": "2"}, "2.0")
    assert hit["result"] == "correct"
    assert hit["score"] == 1
    assert hit["max_score"] == 1
    assert hit["points"] == []
    assert hit["model"] == "algorithm"
    miss = grading.judge(blank, {"text": "2"}, "3")
    assert miss["result"] == "incorrect"
    assert miss["score"] == 0
    assert grading.judge(blank, {"text": "x=2"}, "2") is None
    assert offline == []


def test_judge_solution_requires_every_rubric_point(ai, monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", True)
    state = {"rubric": ["求出导数", "代入求值", "写出结论"], "hits": [True, True, True], "calls": 0}
    monkeypatch.setattr(grading, "_chat", chat_stub(state))
    problem = solution_problem()
    answer = {"text": "详解：先求导，再代入，最后写结论。"}
    full = grading.judge(problem, answer, "完整作答")
    assert full["result"] == "correct"
    assert full["score"] == 3
    assert full["max_score"] == 3
    assert [item["hit"] for item in full["points"]] == [True, True, True]
    assert [item["point"] for item in full["points"]] == state["rubric"]
    assert full["model"] == "stub-model"
    assert state["calls"] == 2
    state["hits"] = [True, True, False]
    partial = grading.judge(problem, answer, "只写了一半")
    assert partial["result"] == "incorrect"
    assert partial["score"] == 2
    assert partial["max_score"] == 3
    assert partial["score"] < partial["max_score"]
    assert [item["hit"] for item in partial["points"]] == [True, True, False]
    assert partial["points"][2]["note"] == "第3点"
    assert state["calls"] == 3
    assert ai["http"] == []


def test_rubric_cache_reuses_and_invalidates_on_answer_change(ai, monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", True)
    state = {"rubric": ["求出导数", "代入求值"], "hits": [True, True], "calls": 0}
    monkeypatch.setattr(grading, "_chat", chat_stub(state))
    db = ai["db"]
    problem = solution_problem()
    first = "详解版本一：先求导，再代入。"
    points = grading.rubric_for(problem, {"text": first})
    assert [item["point"] for item in points] == ["求出导数", "代入求值"]
    assert [item["id"] for item in points] == [1, 2]
    assert state["calls"] == 1
    cached = db.get("problem_rubrics", problem_id="test-q2")
    assert cached["answer_hash"] == hashlib.sha256(first.encode("utf-8")).hexdigest()
    assert cached["model"] == "stub-model"
    again = grading.rubric_for(problem, {"text": first})
    assert [item["point"] for item in again] == ["求出导数", "代入求值"]
    assert state["calls"] == 1
    second = "详解版本二：改用洛必达法则。"
    regraded = grading.rubric_for(problem, {"text": second})
    assert [item["point"] for item in regraded] == ["求出导数", "代入求值"]
    assert state["calls"] == 2
    updated = db.get("problem_rubrics", problem_id="test-q2")
    assert updated["answer_hash"] == hashlib.sha256(second.encode("utf-8")).hexdigest()
    assert updated["answer_hash"] != cached["answer_hash"]
    assert ai["http"] == []


def test_grade_endpoint_degrades_without_ai(ai):
    db, client = ai["db"], ai["one"]
    set_answer_text(db, "test-q2", "参考详解：先求导，再代入。")
    state = submit(client, pid="test-q2", text="我的解答").json()["progress"]
    assert state["result"] == "pending"
    response = grade(client, "test-q2", revision=state["revision"])
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["reason"] == "AI 判分暂不可用，请自检"
    assert body["problem"]["progress"]["result"] == "pending"
    assert body["problem"]["progress"]["ai_grading"] is None
    assert db.get("problem_grading", problem_id="test-q2") is None
    missing = submit(client, pid="test-q3", text="我的解答").json()["progress"]
    blank_answer = grade(client, "test-q3", revision=missing["revision"]).json()
    assert blank_answer["available"] is False
    assert blank_answer["reason"] == "题目缺少文字详解，请自检"
    assert blank_answer["problem"]["progress"]["result"] == "pending"
    assert ai["http"] == []


def test_grade_endpoint_records_ai_snapshot(ai, monkeypatch):
    db, client = ai["db"], ai["one"]
    set_answer_text(db, "test-q2", "参考详解：先求导，再代入。")
    state = submit(client, pid="test-q2", text="我的解答").json()["progress"]
    fixed = {
        "result": "correct",
        "score": 2,
        "max_score": 2,
        "points": [
            {"point": "先求导", "hit": True, "note": "正确"},
            {"point": "再代入", "hit": True, "note": "正确"},
        ],
        "model": "stub-model",
    }
    monkeypatch.setattr(grading, "judge", lambda *args, **kwargs: fixed)
    response = grade(client, "test-q2", revision=state["revision"])
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["reason"] == ""
    progress = body["problem"]["progress"]
    assert progress["result"] == "correct"
    assert progress["result_source"] == "ai"
    assert progress["revision"] == state["revision"]
    snapshot = progress["ai_grading"]
    assert set(snapshot) == {
        "result",
        "score",
        "max_score",
        "points",
        "model",
        "created_at",
    }
    assert snapshot["result"] == "correct"
    assert snapshot["score"] == 2
    assert snapshot["max_score"] == 2
    assert snapshot["points"] == fixed["points"]
    assert snapshot["model"] == "stub-model"
    assert isinstance(snapshot["created_at"], str) and snapshot["created_at"]
    row = db.get("problem_grading", problem_id="test-q2")
    assert row["user_id"] == progress["user_id"]
    assert row["result"] == "correct"
    assert row["score"] == 2
    assert row["max_score"] == 2
    assert row["points"] == fixed["points"]
    assert row["problem_version"] == 1


def test_grade_endpoint_refuses_second_attempt(ai, monkeypatch):
    db, client = ai["db"], ai["one"]
    set_answer_text(db, "test-q2", "参考详解：先求导，再代入。")
    state = submit(client, pid="test-q2", text="我的解答").json()["progress"]
    monkeypatch.setattr(
        grading,
        "judge",
        lambda *args, **kwargs: {
            "result": "correct",
            "score": 1,
            "max_score": 1,
            "points": [{"point": "先求导", "hit": True, "note": ""}],
            "model": "stub-model",
        },
    )
    assert grade(client, "test-q2", revision=state["revision"]).status_code == 200
    repeated = grade(client, "test-q2", revision=state["revision"])
    assert repeated.status_code == 409
    assert repeated.json()["detail"] == "本次作答已判分或已自检，请刷新后重试。"
    assert db.get("problem_grading", problem_id="test-q2")["score"] == 1


def test_self_assessment_after_ai_keeps_snapshot(ai, monkeypatch):
    db, client = ai["db"], ai["one"]
    set_answer_text(db, "test-q2", "参考详解：先求导，再代入。")
    state = submit(client, pid="test-q2", text="我的解答").json()["progress"]
    monkeypatch.setattr(
        grading,
        "judge",
        lambda *args, **kwargs: {
            "result": "correct",
            "score": 1,
            "max_score": 1,
            "points": [{"point": "先求导", "hit": True, "note": "正确"}],
            "model": "stub-model",
        },
    )
    assert grade(client, "test-q2", revision=state["revision"]).status_code == 200
    snapshot = db.get("problem_grading", problem_id="test-q2")
    response = client.patch(
        "/api/v1/me/problems/test-q2/self-assessment",
        json={"result": "incorrect", "revision": state["revision"]},
    )
    assert response.status_code == 200
    progress = response.json()["progress"]
    assert progress["result"] == "incorrect"
    assert progress["result_source"] == "self_assessment"
    assert progress["ever_correct"] is True
    assert progress["ever_incorrect"] is True
    assert progress["ai_grading"]["result"] == "correct"
    assert db.get("problem_grading", problem_id="test-q2") == snapshot


def test_grade_endpoint_rejects_choice_problem(ai):
    db, client = ai["db"], ai["one"]
    problem = db.get("problems", id="test-q2")
    problem.update(type="choice", grading_mode="choice", choice_options=["A", "B"])
    db.put("problems", problem)
    answer = db.get("problem_answers", problem_id="test-q2")
    answer["correct_options"] = ["A"]
    db.put("problem_answers", answer)
    response = grade(client, "test-q2")
    assert response.status_code == 400
    assert response.json()["detail"] == "选择题已自动判分。"
    assert db.get("problem_grading", problem_id="test-q2") is None
