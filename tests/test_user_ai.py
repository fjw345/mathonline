"""用户自带 DeepSeek API 密钥：加密存储、掩码、权限与按用户调用。"""

import base64
import os

import pytest

from backend import ai, config, user_ai

KEY = "sk-" + "a" * 24


def make_secret():
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


@pytest.fixture
def key_env(env, monkeypatch):
    monkeypatch.setattr(config, "AI_KEY_SECRET", make_secret())
    monkeypatch.setattr(config, "AI_ALLOW_SERVER_KEY", False)
    monkeypatch.setattr(config, "AI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setattr(config, "AI_MODEL", "stub-model")
    # 服务端没有密钥也要能靠用户密钥工作。
    monkeypatch.setattr(config, "AI_ENABLED", False)
    monkeypatch.setattr(config, "AI_API_KEY", "")
    monkeypatch.setattr(user_ai, "verify", lambda api_key: None)
    return env


def user_id(env, email="one@test.local"):
    return env["db"].get("profiles", email=email)["id"]


def test_key_round_trip_is_encrypted(key_env):
    db = key_env["db"]
    uid = user_id(key_env)
    status = user_ai.save(uid, KEY)
    assert status["configured"] is True
    assert status["hint"] == "••••••••" + KEY[-4:]
    row = db.get("user_ai_keys", user_id=uid)
    assert KEY not in row["ciphertext"]
    assert row["ciphertext"].startswith("v1:")
    assert user_ai.load_key(uid) == KEY
    assert user_ai.status(uid)["updated_at"]


def test_ciphertext_is_bound_to_the_user(key_env):
    uid = user_id(key_env, "one@test.local")
    other = user_id(key_env, "two@test.local")
    user_ai.save(uid, KEY)
    ciphertext = key_env["db"].get("user_ai_keys", user_id=uid)["ciphertext"]
    with pytest.raises(user_ai.KeyUnavailable):
        user_ai.decrypt(other, ciphertext)


def test_missing_encryption_secret_refuses_to_save(env, monkeypatch):
    monkeypatch.setattr(config, "AI_KEY_SECRET", "")
    monkeypatch.setattr(user_ai, "verify", lambda api_key: None)
    with pytest.raises(user_ai.KeyUnavailable):
        user_ai.save(user_id(env), KEY)
    assert env["db"].get("user_ai_keys", user_id=user_id(env)) is None


def test_key_endpoints_only_return_masked_value(key_env):
    client = key_env["one"]
    uid = user_id(key_env)
    saved = client.put("/api/v1/me/ai-key", json={"api_key": KEY})
    assert saved.status_code == 200
    body = saved.json()
    assert body["configured"] is True
    assert body["hint"].endswith(KEY[-4:])
    assert KEY not in saved.text
    assert KEY not in key_env["db"].get("user_ai_keys", user_id=uid)["ciphertext"]

    fetched = client.get("/api/v1/me/ai-key")
    assert fetched.status_code == 200
    assert KEY not in fetched.text
    assert fetched.json()["configured"] is True

    deleted = client.delete("/api/v1/me/ai-key")
    assert deleted.status_code == 200
    assert deleted.json()["configured"] is False
    assert user_ai.load_key(uid) is None


def test_key_endpoints_require_login_and_reject_bad_format(key_env):
    assert key_env["guest"].get("/api/v1/me/ai-key").status_code == 401
    bad = key_env["one"].put(
        "/api/v1/me/ai-key", json={"api_key": "not-a-deepseek-key"}
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]
    assert key_env["db"].list("user_ai_keys") == []


def test_invalid_key_is_not_saved(key_env, monkeypatch):
    def reject(api_key):
        raise ValueError("密钥无效或已被撤销，请检查后重试。")

    monkeypatch.setattr(user_ai, "verify", reject)
    response = key_env["one"].put("/api/v1/me/ai-key", json={"api_key": KEY})
    assert response.status_code == 400
    assert key_env["db"].list("user_ai_keys") == []


def test_ai_context_uses_the_users_own_key(key_env):
    uid = user_id(key_env)
    user_ai.save(uid, KEY)
    ai.clear_user()
    ai.bind_user(uid)
    assert ai.enabled() is True
    assert ai.resolve_key() == KEY

    ai.bind_user(user_id(key_env, "two@test.local"))
    assert ai.enabled() is False
    assert "配置" in ai.reason()
    with pytest.raises(ai.AIUnavailable):
        ai.resolve_key()
    ai.clear_user()
