import pytest

from backend import ai, config, question_gen, variant_pool


@pytest.mark.parametrize("text", [
    r"计算 $\frac{x}{2$。",
    r"计算 $x^2。",
    "已知 `sin⁡xx+1` 是原函数。",
    r"计算 $\int x\,dx。",
    r"计算 \(x^2 的导数。",
    "公式 \x0crac{1}{2}",
])
def test_rejects_obviously_damaged_math(text):
    assert not question_gen.valid_math_format(text)
    assert question_gen.normalize_generated({"stem": text, "answer": "2"}, "blank") is None


@pytest.mark.parametrize("text", [
    r"已知 $\frac{\sin x}{x+1}$ 是 $f(x)$ 的一个原函数。",
    r"计算 \(\frac{1}{2}\) 与 \[x^2\]。",
    "计算 1+1 的值。",
])
def test_preserves_valid_math(text):
    assert question_gen.valid_math_format(text)


def test_damaged_generation_retries_without_silently_guessing(env, monkeypatch):
    monkeypatch.setattr(config, "AI_ENABLED", True)
    calls = []

    def complete(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return {"stem": "已知 `sin⁡xx+1` 是原函数。", "answer": "2"}
        if len(calls) == 2:
            return {"stem": r"计算 $\int_0^1 4x\,dx$。", "answer": "2", "explanation": "结果为 2。"}
        return {"solvable": True, "unique": True, "consistent": True, "answer": "2", "explanation_answer": "2"}

    monkeypatch.setattr(ai, "complete_json", complete)
    result = question_gen.generate_verified({"question_text": "原题"}, "1", "高等数学", "blank", attempts=2)
    assert result["question_text"] == r"计算 $\int_0^1 4x\,dx$。"
    assert len(calls) == 3


def test_cached_damaged_formula_is_skipped_without_deleting_it():
    rows = [
        {"id": "bad", "question_text": "已知 sin⁡xx+1", "answer_text": "2"},
        {"id": "good", "question_text": "$x+1$", "answer_text": "2"},
    ]

    class Repository:
        def list(self, *args, **kwargs):
            return rows

    valid = variant_pool._cache_rows({"id": "p"}, "hash", "blank", Repository())
    assert [row["id"] for row in valid] == ["good"]
    assert len(rows) == 2
