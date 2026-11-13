"""图片作答转写：读图、缓存、失败降级与判分端点集成。"""

import pytest

from backend import ai, config, grading, vision


def add_submission_asset(env, owner_id):
    asset = {
        "id": "submission-image",
        "owner_id": owner_id,
        "purpose": "submission",
        "path": "uploads/test/submission.webp",
        "content_type": "image/webp",
        "width": 200,
        "height": 100,
        "problem_id": None,
        "source_path": "",
        "created_at": "2026-11-13T00:00:00+00:00",
    }
    env["db"].put("media_assets", asset)
    return asset["id"]


def user_id(client):
    return client.get("/api/v1/me").json()["user"]["id"]


def set_answer_text(db, pid, text):
    answer = db.get("problem_answers", problem_id=pid)
    answer["text"] = text
    db.put("problem_answers", answer)


@pytest.fixture
def vision_env(env, monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "AI_VISION_ENABLED", True)
    monkeypatch.setattr(config, "AI_VISION_MODEL", "stub-vision")
    monkeypatch.setattr(config, "AI_VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr(config, "AI_VISION_API_KEY", "vision-key")
    monkeypatch.setattr(vision, "_asset_bytes", lambda asset: env["raw"])
    calls = []

    def chat(messages, **kwargs):
        calls.append((messages, kwargs))
        return "作答：由 $x=1$ 得结果为 2。"

    monkeypatch.setattr(ai, "chat", chat)
    return {**env, "calls": calls}


def test_transcribe_builds_image_message_and_caches(vision_env):
    db = vision_env["db"]
    owner = user_id(vision_env["one"])
    aid = add_submission_asset(vision_env, owner)
    problem = {"id": "test-q2", "subject": "高等数学", "type": "solution"}
    state = {"image_ids": [aid], "revision": 3}
    result = vision.transcribe({"id": owner}, problem, state, db)
    assert result["text"] == "由 $x=1$ 得结果为 2。"
    assert result["model"] == "stub-vision"
    assert len(vision_env["calls"]) == 1
    messages, kwargs = vision_env["calls"][0]
    content = messages[-1]["content"]
    assert isinstance(content, list) and content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert kwargs["model"] == "stub-vision"
    assert kwargs["base_url"] == "https://vision.example/v1"
    assert kwargs["api_key"] == "vision-key"
    assert kwargs["json_mode"] is False
    # 同一修订号再次转写：直接读缓存
    again = vision.transcribe({"id": owner}, problem, state, db)
    assert again["text"] == result["text"]
    assert len(vision_env["calls"]) == 1
    cached = db.get("submission_transcripts", id=owner + ":test-q2")
    assert cached["revision"] == 3
    assert cached["image_ids"] == [aid]
    # 修订号变化后重新转写
    vision.transcribe({"id": owner}, problem, {"image_ids": [aid], "revision": 4}, db)
    assert len(vision_env["calls"]) == 2


def test_transcribe_rejects_foreign_or_missing_images(vision_env):
    db = vision_env["db"]
    owner = user_id(vision_env["one"])
    other = user_id(vision_env["two"])
    aid = add_submission_asset(vision_env, other)
    problem = {"id": "test-q2", "subject": "高等数学", "type": "solution"}
    with pytest.raises(vision.VisionUnavailable):
        vision.transcribe(
            {"id": owner}, problem, {"image_ids": [aid], "revision": 1}, db
        )
    with pytest.raises(vision.VisionUnavailable):
        vision.transcribe(
            {"id": owner}, problem, {"image_ids": ["missing"], "revision": 1}, db
        )


def test_transcribe_degrades_on_model_error(vision_env, monkeypatch):
    db = vision_env["db"]
    owner = user_id(vision_env["one"])
    aid = add_submission_asset(vision_env, owner)
    problem = {"id": "test-q2", "subject": "高等数学", "type": "solution"}
    monkeypatch.setattr(
        ai,
        "chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(ai.AIUnavailable("down")),
    )
    with pytest.raises(vision.VisionUnavailable):
        vision.transcribe(
            {"id": owner}, problem, {"image_ids": [aid], "revision": 1}, db
        )
    monkeypatch.setattr(ai, "chat", lambda *args, **kwargs: "   ")
    with pytest.raises(vision.VisionUnavailable):
        vision.transcribe(
            {"id": owner}, problem, {"image_ids": [aid], "revision": 2}, db
        )


def test_grade_endpoint_transcribes_image_submission(vision_env, monkeypatch):
    db, client = vision_env["db"], vision_env["one"]
    owner = user_id(client)
    aid = add_submission_asset(vision_env, owner)
    set_answer_text(db, "test-q2", "参考详解：答案为 2。")
    state = client.put(
        "/api/v1/me/problems/test-q2/submission",
        json={"image_ids": [aid], "problem_version": 1},
    ).json()["progress"]
    assert state["text"] == ""
    assert state["image_ids"] == [aid]
    monkeypatch.setattr(
        vision,
        "transcribe",
        lambda *args, **kwargs: {
            "text": "由条件得答案为 2。",
            "model": "stub-vision",
            "images": 1,
        },
    )
    captured = {}

    def judge(problem, answer, submitted):
        captured["submitted"] = submitted
        return {
            "result": "correct",
            "score": 2,
            "max_score": 2,
            "points": [
                {"point": "步骤", "hit": True, "note": ""},
                {"point": "结论", "hit": True, "note": ""},
            ],
            "model": "stub-model",
        }

    monkeypatch.setattr(grading, "judge", judge)
    response = client.post(
        "/api/v1/me/problems/test-q2/grade",
        json={"problem_version": 1, "revision": state["revision"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert captured["submitted"] == "由条件得答案为 2。"
    snapshot = body["problem"]["progress"]["ai_grading"]
    assert snapshot["transcript"] == "由条件得答案为 2。"
    row = db.get("problem_grading", problem_id="test-q2")
    assert row["transcript"] == "由条件得答案为 2。"
    assert body["problem"]["progress"]["result"] == "correct"


def test_grade_endpoint_degrades_when_image_transcription_fails(
    vision_env, monkeypatch
):
    db, client = vision_env["db"], vision_env["one"]
    owner = user_id(client)
    aid = add_submission_asset(vision_env, owner)
    set_answer_text(db, "test-q2", "参考详解：答案为 2。")
    state = client.put(
        "/api/v1/me/problems/test-q2/submission",
        json={"image_ids": [aid], "problem_version": 1},
    ).json()["progress"]

    def unavailable(*args, **kwargs):
        raise vision.VisionUnavailable("图片作答转写暂不可用，请自检")

    monkeypatch.setattr(vision, "transcribe", unavailable)
    response = client.post(
        "/api/v1/me/problems/test-q2/grade",
        json={"problem_version": 1, "revision": state["revision"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["reason"] == "图片作答转写暂不可用，请自检"
    assert body["problem"]["progress"]["result"] == "pending"
    assert db.get("problem_grading", problem_id="test-q2") is None


def test_text_submission_skips_transcription(vision_env, monkeypatch):
    db, client = vision_env["db"], vision_env["one"]
    set_answer_text(db, "test-q2", "参考详解：答案为 2。")
    state = client.put(
        "/api/v1/me/problems/test-q2/submission",
        json={"text": "我的文字作答", "problem_version": 1},
    ).json()["progress"]

    def forbidden(*args, **kwargs):
        raise AssertionError("文字作答不应调用视觉转写")

    monkeypatch.setattr(vision, "transcribe", forbidden)
    captured = {}

    def judge(problem, answer, submitted):
        captured["submitted"] = submitted
        return {
            "result": "incorrect",
            "score": 0,
            "max_score": 1,
            "points": [{"point": "结论", "hit": False, "note": ""}],
            "model": "stub-model",
        }

    monkeypatch.setattr(grading, "judge", judge)
    response = client.post(
        "/api/v1/me/problems/test-q2/grade",
        json={"problem_version": 1, "revision": state["revision"]},
    )
    assert response.status_code == 200
    assert captured["submitted"] == "我的文字作答"
    assert response.json()["problem"]["progress"]["ai_grading"]["transcript"] == ""
