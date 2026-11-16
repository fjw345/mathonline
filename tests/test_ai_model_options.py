"""公共/个人模型隔离和明确选择判题的离线回归；不访问真实模型。"""

import base64

import httpx
import pytest

from backend import ai, config, grading, main, user_ai
from conftest import submit

PERSONAL_KEY = "sk-personal-test-1234567890"


@pytest.fixture
def models(env, monkeypatch):
    monkeypatch.setattr(config, "AI_KEY_SECRET", base64.urlsafe_b64encode(b"a" * 32).decode())
    monkeypatch.setattr(config, "AI_ENABLED", True)
    monkeypatch.setattr(config, "AI_ALLOW_SERVER_KEY", True)
    monkeypatch.setattr(config, "AI_VISION_BASE_URL", "https://public-vision.invalid/v1")
    monkeypatch.setattr(config, "AI_VISION_API_KEY", "public-vision-key")
    monkeypatch.setattr(config, "AI_VISION_MODEL", "public-vision")
    return env


def uid(env, email="one@test.local"):
    return env["db"].get("profiles", email=email)["id"]


def save_personal(env, model="gpt-5-test", vision_model=""):
    settings = user_ai.normalize_settings("openai", model, vision_model)
    user_ai.save(uid(env), PERSONAL_KEY, settings)
    ai.bind_user(uid(env))


def test_public_model_requires_no_personal_key_or_encryption_secret(models, monkeypatch):
    monkeypatch.setattr(config, "AI_KEY_SECRET", "")
    ai.bind_user(uid(models))
    assert ai.enabled()
    assert ai.resolve_key() == "offline-placeholder"
    status = models["one"].get("/api/v1/me/ai-key").json()
    assert status["mode"] == "public"
    assert status["available"] is True
    assert status["public_available"] is True
    assert "offline-placeholder" not in str(status)


def test_personal_model_is_isolated_and_can_restore_public(models, monkeypatch):
    monkeypatch.setattr(user_ai, "verify", lambda *args: None)
    client = models["one"]
    saved = client.put("/api/v1/me/ai-key", json={
        "api_key": PERSONAL_KEY, "provider": "openai", "model": "gpt-5-test",
    })
    assert saved.status_code == 200
    assert saved.json()["mode"] == "personal"
    assert saved.json()["model"] == "gpt-5-test"
    assert PERSONAL_KEY not in saved.text
    row = models["db"].get("user_ai_keys", user_id=uid(models))
    assert PERSONAL_KEY not in row["ciphertext"]
    ai.bind_user(uid(models))
    assert ai.base_endpoint() == "https://api.openai.com/v1"
    assert ai.resolve_key() == PERSONAL_KEY
    assert ai.model_name("variant") == "gpt-5-test"
    ai.bind_user(uid(models, "two@test.local"))
    assert ai.resolve_key() == "offline-placeholder"
    assert models["two"].get("/api/v1/me/ai-key").json()["mode"] == "public"
    removed = client.delete("/api/v1/me/ai-key")
    assert removed.status_code == 200
    assert removed.json()["mode"] == "public"
    ai.bind_user(uid(models))
    assert ai.resolve_key() == "offline-placeholder"


def test_personal_model_works_without_public_configuration(models, monkeypatch):
    save_personal(models)
    monkeypatch.setattr(config, "AI_ENABLED", False)
    monkeypatch.setattr(config, "AI_API_KEY", "")
    monkeypatch.setattr(config, "AI_BASE_URL", "")
    monkeypatch.setattr(config, "AI_MODEL", "")
    assert ai.enabled()
    assert ai.model_name() == "gpt-5-test"
    assert ai.resolve_key() == PERSONAL_KEY


def test_analysis_cache_is_invalidated_when_switching_model(models):
    ai.bind_user(uid(models))
    row = {"model": ai.model_name(), "fingerprint": "same-data", "report": {"summary": "旧报告"}}
    stats = {"fingerprint": "same-data"}
    assert main.cached_report(row, stats)["summary"] == "旧报告"
    save_personal(models)
    assert main.cached_report(row, stats) is None


def test_long_openai_project_key_and_official_verification_endpoint(models, monkeypatch):
    key = "sk-proj-" + "x" * 240
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs["headers"]["Authorization"]))
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(httpx, "get", get)
    response = models["one"].put("/api/v1/me/ai-key", json={
        "api_key": key, "provider": "openai", "model": "gpt-5-test",
    })
    assert response.status_code == 200
    assert calls == [("https://api.openai.com/v1/models", "Bearer " + key)]
    assert key not in response.text


@pytest.mark.parametrize("settings", [
    {"provider": "other", "model": "test"},
    {"provider": "openai", "model": "a\nheader"},
    {"provider": "openai", "model": ""},
    {"provider": "deepseek", "model": "test", "vision_model": "../ invalid"},
])
def test_invalid_model_config_is_never_saved(models, monkeypatch, settings):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid settings must not send a verification request")

    monkeypatch.setattr(httpx, "get", unexpected)
    response = models["one"].put("/api/v1/me/ai-key", json={"api_key": PERSONAL_KEY, **settings})
    assert response.status_code in (400, 422)
    assert user_ai.load_settings(uid(models)) is None


def test_public_and_personal_vision_credentials_do_not_mix(models, monkeypatch):
    save_personal(models)
    assert ai.vision_settings() == (
        "https://public-vision.invalid/v1", "public-vision-key", "public-vision",
    )
    save_personal(models, vision_model="my-vision")
    assert ai.vision_settings() == (
        "https://api.openai.com/v1", PERSONAL_KEY, "my-vision",
    )
    monkeypatch.setattr(config, "AI_ALLOW_SERVER_KEY", False)
    save_personal(models)
    assert ai.vision_settings() == ("", "", "")


def test_openai_request_does_not_inherit_public_reasoning_options(models, monkeypatch):
    save_personal(models)
    monkeypatch.setattr(config, "AI_REASONING_EFFORT", "none")
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(httpx, "post", post)
    assert ai.chat([{"role": "user", "content": "test"}]) == "{}"
    url, kwargs = calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer " + PERSONAL_KEY
    assert kwargs["json"]["model"] == "gpt-5-test"
    assert "temperature" not in kwargs["json"]
    assert "reasoning_effort" not in kwargs["json"]
    assert "max_completion_tokens" in kwargs["json"]


def test_json_fallback_does_not_affect_other_users(models, monkeypatch):
    ai.bind_user(uid(models))
    calls = []

    def post(url, **kwargs):
        calls.append(dict(kwargs["json"]))
        if len(calls) == 1:
            return httpx.Response(400)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(httpx, "post", post)
    assert ai.chat([], retries=0) == "{}"
    assert "response_format" not in calls[1]
    save_personal(models)
    assert ai.chat([], retries=0) == "{}"
    assert "response_format" in calls[2]


def test_submission_unlocks_reference_but_only_explicit_grade_calls_ai(models, monkeypatch):
    calls = []
    answer = models["db"].get("problem_answers", problem_id="test-q2")
    answer["text"] = "参考详解"
    models["db"].put("problem_answers", answer)

    def judge(*args, **kwargs):
        calls.append(True)
        return {"result": "correct", "score": 1, "max_score": 1, "points": [], "model": "test"}

    monkeypatch.setattr(grading, "judge", judge)
    client = models["one"]
    submitted = submit(client, "test-q2", "我的作答").json()
    assert submitted["answer_unlocked"] is True
    assert submitted["progress"]["result"] == "pending"
    assert calls == []
    assert client.get("/api/v1/problems/test-q2/answer").status_code == 200
    assert calls == []
    response = client.post("/api/v1/me/problems/test-q2/grade", json={
        "problem_version": 1, "revision": submitted["progress"]["revision"],
    })
    assert response.status_code == 200
    assert response.json()["problem"]["progress"]["result"] == "correct"
    assert calls == [True]


def test_project_key_length_is_supported():
    value = "sk-proj-" + "x" * 210
    assert user_ai.normalize(value) == value
