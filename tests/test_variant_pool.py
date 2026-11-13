"""AI 仿题共享题池：跨用户复用、池大小、源哈希失效与降级。"""

import json

import pytest

from backend import ai, config, variant_pool, variants


def set_answer_text(db, pid, text):
    answer = db.get("problem_answers", problem_id=pid)
    answer["text"] = text
    db.put("problem_answers", answer)
    return answer


def set_knowledge(db, pid, codes):
    problem = db.get("problems", id=pid)
    problem["knowledge_points"] = codes
    db.put("problems", problem)
    return problem


def chat_stub(state):
    def chat(messages, **kwargs):
        body = "\n".join(message["content"] for message in messages)
        if "仿照" in body:
            state["generated"] += 1
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
        state["verified"] += 1
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


@pytest.fixture
def shared(env, monkeypatch):
    db = env["db"]
    answer = set_answer_text(db, "test-q2", "参考解答：先求导再代入。")
    problem = set_knowledge(db, "test-q2", ["definite_integral"])
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "AI_MODEL", "stub-model")
    monkeypatch.setattr(config, "AI_VARIANT_SHARE", True)
    monkeypatch.setattr(config, "AI_VARIANT_POOL_SIZE", 3)
    state = {"generated": 0, "verified": 0}
    monkeypatch.setattr(ai, "chat", chat_stub(state))
    profiles = {row["email"]: row["id"] for row in db.list("profiles")}
    users = {
        "user1": {"id": profiles["one@test.local"]},
        "user2": {"id": profiles["two@test.local"]},
        "user3": {"id": profiles["admin@test.local"]},
    }
    return {
        "db": db,
        "problem": problem,
        "answer": answer,
        "state": state,
        **users,
        **env,
    }


def test_pool_reuses_across_users(shared):
    db, problem, answer, state = (
        shared["db"],
        shared["problem"],
        shared["answer"],
        shared["state"],
    )
    first = variants.create_variant(shared["user1"], problem, answer, db)
    assert state["generated"] == 1 and state["verified"] == 1
    assert bool(first["reused"]) is False
    assert len(db.list("variant_cache", problem_id="test-q2")) == 1
    second = variants.create_variant(shared["user2"], problem, answer, db)
    assert state["generated"] == 1  # 直接命中题池，没有再次调用模型
    assert bool(second["reused"]) is True
    assert second["question_text"] == first["question_text"]
    cache = db.list("variant_cache", problem_id="test-q2")[0]
    assert cache["use_count"] == 2
    # 同一用户再要一道：题池未满且已看过唯一一道，会生成第二道
    third = variants.create_variant(shared["user1"], problem, answer, db)
    assert state["generated"] == 2
    assert bool(third["reused"]) is False
    assert len(db.list("variant_cache", problem_id="test-q2")) == 2
    # 另一个用户此时能看到未看过的第二道，仍然不生成
    fourth = variants.create_variant(shared["user2"], problem, answer, db)
    assert state["generated"] == 2
    assert bool(fourth["reused"]) is True
    assert fourth["cache_id"] != second["cache_id"]


def test_pool_size_cap_reuses_instead_of_generating(shared, monkeypatch):
    db, problem, answer, state = (
        shared["db"],
        shared["problem"],
        shared["answer"],
        shared["state"],
    )
    monkeypatch.setattr(config, "AI_VARIANT_POOL_SIZE", 1)
    variants.create_variant(shared["user1"], problem, answer, db)
    variants.create_variant(shared["user2"], problem, answer, db)
    # u1 已看过池内唯一一道，池满后复用而不是再生成
    again = variants.create_variant(shared["user1"], problem, answer, db)
    assert state["generated"] == 1
    assert bool(again["reused"]) is True


def test_source_hash_invalidates_on_answer_or_version_change(shared):
    db, problem, answer, state = (
        shared["db"],
        shared["problem"],
        shared["answer"],
        shared["state"],
    )
    variants.create_variant(shared["user1"], problem, answer, db)
    changed = dict(answer)
    changed["text"] = "参考解答：改用洛必达法则，答案不同。"
    variants.create_variant(shared["user2"], problem, changed, db)
    assert state["generated"] == 2
    assert len(db.list("variant_cache", problem_id="test-q2")) == 2
    bumped = dict(problem)
    bumped["version"] = problem["version"] + 1
    variants.create_variant(shared["user3"], bumped, changed, db)
    assert state["generated"] == 3
    assert len(db.list("variant_cache", problem_id="test-q2")) == 3


def test_share_disabled_generates_every_time(shared, monkeypatch):
    db, problem, answer, state = (
        shared["db"],
        shared["problem"],
        shared["answer"],
        shared["state"],
    )
    monkeypatch.setattr(config, "AI_VARIANT_SHARE", False)
    variants.create_variant(shared["user1"], problem, answer, db)
    variants.create_variant(shared["user2"], problem, answer, db)
    assert state["generated"] == 2
    assert db.list("variant_cache", problem_id="test-q2") == []


def test_cache_serves_when_ai_unavailable(shared, monkeypatch):
    db, problem, answer, state = (
        shared["db"],
        shared["problem"],
        shared["answer"],
        shared["state"],
    )
    variants.create_variant(shared["user1"], problem, answer, db)
    monkeypatch.setattr(config, "AI_ENABLED", False)
    reused = variants.create_variant(shared["user2"], problem, answer, db)
    assert bool(reused["reused"]) is True
    assert state["generated"] == 1
    # 换一道没有缓存的题：既无模型也无题池，必须明确失败
    other = set_knowledge(db, "test-q3", ["definite_integral"])
    other_answer = set_answer_text(db, "test-q3", "参考解答：另一道题。")
    with pytest.raises(ai.AIUnavailable):
        variant_pool.get_or_create(
            other, other_answer["text"], "高等数学", "solution", db
        )


def test_two_users_share_via_api(shared):
    client_one, client_two = shared["one"], shared["two"]
    first = client_one.post("/api/v1/me/problems/test-q2/variants")
    assert first.status_code == 200
    body_one = first.json()
    assert body_one["pooled"] is False
    second = client_two.post("/api/v1/me/problems/test-q2/variants")
    assert second.status_code == 200
    body_two = second.json()
    assert body_two["pooled"] is True
    assert body_two["id"] != body_one["id"]
    assert body_two["question_text"] == body_one["question_text"]
    assert "answer_text" not in body_two
    assert shared["state"]["generated"] == 1
    shared["two"].patch(
        "/api/v1/me/variants/" + body_two["id"], json={"result": "correct"}
    )
    listed_one = client_one.get("/api/v1/me/variants?problem_id=test-q2").json()
    listed_two = client_two.get("/api/v1/me/variants?problem_id=test-q2").json()
    assert listed_one["items"][0]["result"] == ""
    assert listed_two["items"][0]["result"] == "correct"
