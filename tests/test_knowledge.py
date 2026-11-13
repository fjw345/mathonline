"""知识点标签、公开筛选与人工校正的离线测试（不请求真实模型）。"""

import httpx
import pytest

from backend import ai, config, tagging, topics


def set_knowledge(db, pid, codes, difficulty=3, source="manual"):
    problem = db.get("problems", id=pid)
    problem["knowledge_points"] = codes
    problem["difficulty"] = difficulty
    problem["knowledge_source"] = source
    db.put("problems", problem)
    return problem


def test_taxonomy_maps_subject_to_allowed_codes():
    upper = topics.allowed_codes("微积分上")
    lower = topics.allowed_codes("微积分下")
    linear = topics.allowed_codes("线性代数")
    probability = topics.allowed_codes("概率论")
    assert "limit" in upper and "series" not in upper
    assert "series" in lower and "limit" not in lower
    assert "eigenvalue" in linear and "limit" not in linear
    assert "expectation_variance" in probability
    assert topics.domain_for_subject("微积分下") == "calculus"
    assert topics.name_for("limit") == "极限与连续"
    assert topics.domain_for("limit") == "calculus"


def test_normalize_accepts_codes_and_chinese_names():
    assert topics.normalize_codes(["极限与连续", "limit", "bogus"], "高等数学") == [
        "limit"
    ]
    assert topics.normalize_codes(["派生编码"], "高等数学") == []
    assert topics.invalid_codes(["bogus", "limit"], "高等数学") == ["bogus"]
    assert topics.invalid_codes(["limit"], "线性代数") == ["limit"]
    resolved = topics.resolve(["limit", "derivative", "bogus"], "高等数学")
    assert [item["code"] for item in resolved] == ["limit", "derivative"]
    assert resolved[0]["name"] == "极限与连续"
    assert topics.parse_filter("limit,derivative,nope") == ["limit", "derivative"]


def test_heuristic_tagging_uses_keywords_and_clamps_difficulty():
    problem = {
        "subject": "高等数学",
        "type": "blank",
        "title": "测试",
        "question_text": "求极限 $\\lim_{x\\to 0}\\frac{\\sin x}{x}$，并讨论连续性。",
    }
    codes = topics.heuristic_codes(problem)
    assert codes[0] == "limit"
    assert 1 <= topics.heuristic_difficulty(problem) <= 5
    proof = {
        "subject": "高等数学",
        "type": "solution",
        "question_text": "证明不等式 $e^x>1+x$（$x\\neq 0$），并说明等号成立条件。",
    }
    assert "inequality_proof" in topics.heuristic_codes(proof)
    matrix = {
        "subject": "线性代数",
        "type": "solution",
        "question_text": "求矩阵 $A$ 的特征值与特征向量，并判断是否可对角化。",
    }
    assert "eigenvalue" in topics.heuristic_codes(matrix)


def test_tag_problem_prefers_ai_and_falls_back(monkeypatch):
    problem = {
        "id": "p1",
        "subject": "高等数学",
        "type": "solution",
        "title": "测试题",
        "question_text": "计算定积分 $\\int_0^1 x^2 dx$。",
    }
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "AI_MODEL", "stub")
    monkeypatch.setattr(
        ai, "chat", lambda *args, **kwargs: '{"knowledge":["definite_integral"],"difficulty":4}'
    )
    tagged = tagging.tag_problem(problem, "答案 1/3")
    assert tagged["knowledge_points"] == ["definite_integral"]
    assert tagged["difficulty"] == 4
    assert tagged["source"] == "ai"
    assert tagged["model"] == "stub"

    monkeypatch.setattr(
        ai, "chat", lambda *args, **kwargs: '{"knowledge":["not-a-code"],"difficulty":9}'
    )
    fallback = tagging.tag_problem(problem, "答案 1/3")
    assert fallback["source"] == "heuristic"
    assert "definite_integral" in fallback["knowledge_points"]
    assert 1 <= fallback["difficulty"] <= 5


def test_tag_problem_without_ai_never_calls_http(monkeypatch):
    attempts = []

    def guard(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("未配置模型时不应发起 HTTP 请求")

    monkeypatch.setattr(httpx, "post", guard)
    monkeypatch.setattr(config, "AI_ENABLED", False)
    tagged = tagging.tag_problem(
        {"id": "p1", "subject": "概率论", "type": "blank", "question_text": ""}
    )
    assert tagged["source"] == "heuristic"
    assert tagged["knowledge_points"] == ["probability_basic"]
    assert attempts == []


def test_select_targets_protects_manual_and_existing():
    problems = [
        {"id": "a", "knowledge_points": [], "knowledge_source": ""},
        {"id": "b", "knowledge_points": ["limit"], "knowledge_source": "ai"},
        {"id": "c", "knowledge_points": ["limit"], "knowledge_source": "manual"},
        {"id": "d", "knowledge_points": ["limit"], "knowledge_source": "heuristic"},
    ]
    assert [p["id"] for p in tagging.select_targets(problems)] == ["a"]
    assert [p["id"] for p in tagging.select_targets(problems, retag=True)] == [
        "a",
        "b",
        "d",
    ]
    assert [p["id"] for p in tagging.select_targets(problems, override_manual=True)] == [
        "a",
        "c",
    ]
    assert [
        p["id"]
        for p in tagging.select_targets(
            problems, retag=True, override_manual=True
        )
    ] == ["a", "b", "c", "d"]
    heuristic_only = tagging.select_targets(problems, retag_heuristic=True)
    assert [p["id"] for p in heuristic_only] == ["a", "d"]


def test_public_catalog_and_knowledge_filter(env):
    db = env["db"]
    set_knowledge(db, "test-q1", ["limit"], 2)
    set_knowledge(db, "test-q2", ["definite_integral"], 3)
    response = env["guest"].get("/api/v1/catalog")
    assert response.status_code == 200
    catalog = {item["code"]: item for item in response.json()["knowledge"]}
    assert catalog["limit"]["count"] == 1
    listed = env["guest"].get("/api/v1/problems?knowledge=limit")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    problem = body["items"][0]
    assert problem["id"] == "test-q1"
    assert problem["knowledge_points"] == [
        {"code": "limit", "name": "极限与连续", "domain": "calculus"}
    ]
    assert "difficulty" not in problem
    empty = env["guest"].get("/api/v1/problems?knowledge=not-a-code").json()
    assert empty["total"] == 0
    detail = env["guest"].get("/api/v1/problems/test-q1").json()
    assert detail["knowledge_points"][0]["name"] == "极限与连续"


def test_admin_updates_knowledge_without_bumping_version(env):
    db = env["db"]
    before = db.get("problems", id="test-q1")
    response = env["admin"].put(
        "/api/v1/admin/problems/test-q1/knowledge",
        json={"knowledge_points": ["derivative"], "difficulty": 4},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["knowledge_points"][0]["code"] == "derivative"
    assert body["difficulty"] == 4
    after = db.get("problems", id="test-q1")
    assert after["knowledge_points"] == ["derivative"]
    assert after["knowledge_source"] == "manual"
    assert after["difficulty"] == 4
    assert after["version"] == before["version"]
    assert after["question_text"] == before["question_text"]
    forbidden = env["one"].put(
        "/api/v1/admin/problems/test-q1/knowledge",
        json={"knowledge_points": ["limit"]},
    )
    assert forbidden.status_code == 403
    invalid = env["admin"].put(
        "/api/v1/admin/problems/test-q1/knowledge",
        json={"knowledge_points": ["not-a-code"]},
    )
    assert invalid.status_code == 400
    cross_subject = env["admin"].put(
        "/api/v1/admin/problems/test-q1/knowledge",
        json={"knowledge_points": ["eigenvalue"]},
    )
    assert cross_subject.status_code == 400


def test_admin_lists_knowledge_names(env):
    set_knowledge(env["db"], "test-q2", ["definite_integral"], 3)
    response = env["admin"].get("/api/v1/admin/problems?page=1&pid=test-q2")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["knowledge_codes"] == ["definite_integral"]
    assert item["knowledge_points"][0]["name"] == "定积分及其应用"
    assert item["difficulty"] == 3


def test_keyword_search_matches_knowledge_names(env):
    set_knowledge(env["db"], "test-q1", ["limit"], 2)
    body = env["guest"].get("/api/v1/problems?q=极限").json()
    assert [item["id"] for item in body["items"]] == ["test-q1"]


def test_reimport_keeps_knowledge_and_internal_difficulty(env):
    db = env["db"]
    set_knowledge(db, "test-q1", ["limit"], 4)
    before = db.get("problems", id="test-q1")
    answer = db.get("problem_answers", problem_id="test-q1")
    record = {
        k: v
        for k, v in before.items()
        if k not in ("knowledge_points", "knowledge_source")
    }
    record["answer"] = {k: v for k, v in answer.items() if k != "problem_id"}
    manifest = {
        "schema_version": 1,
        "sources": db.list("problem_sources"),
        "assets": [],
        "problems": [record],
    }
    from backend.importer import import_manifest

    result = import_manifest(manifest)
    after = db.get("problems", id="test-q1")
    assert result["unchanged"] == 1
    assert after["version"] == before["version"]
    assert after["knowledge_points"] == ["limit"]
    assert after["difficulty"] == 4
    assert after["updated_at"] == before["updated_at"]
