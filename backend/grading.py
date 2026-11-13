"""AI 判分引擎：填空数值比对、大题得分点生成与逐点判分。

模型统一走 OpenAI 兼容的 /chat/completions 接口；未配置、请求失败或
模型输出不可解析时一律抛 GradingUnavailable，由调用方降级为待自检。
"""

import hashlib
import json
import math
import re
import unicodedata

import httpx

from . import ai, config
from .auth import now
from .store import store

MAX_POINTS = 15
MAX_POINT_CHARS = 200
MAX_REFERENCE_CHARS = 80
MAX_INPUT_CHARS = 4000
REL_TOL = 1e-9

_TRAILING = "。．.，,；;：:！!？?、"
_SPLIT = re.compile(r"[；;，]|或")
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_FRACTION = re.compile(
    r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*/\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))$"
)
_LATEX_FRACTION = re.compile(r"^\\frac\{([^{}]+)\}\{([^{}]+)\}$")
_FENCE = re.compile(r"^" + chr(96) * 3 + r"[A-Za-z0-9_-]*\s*")
_SYSTEM = (
    "你是严谨的数学阅卷助手，只依据给出的参考答案与参考详解判断，"
    "不臆测未给出的信息。严格输出一个 JSON 对象："
    "不要输出解释文字，也不要输出 Markdown 代码围栏。"
)


class GradingUnavailable(Exception):
    """AI 判分不可用：未配置、请求失败、输出不可解析或得分点缺失。"""


def normalize(text):
    """归一化作答：全角转半角、压缩空白、去公式装饰与句末标点。"""
    value = unicodedata.normalize("NFKC", text or "")
    value = value.replace("$", "")
    value = value.replace("\\left", "").replace("\\right", "")
    value = value.replace("\\dfrac", "\\frac")
    value = re.sub(r"\s+", " ", value).strip()
    return value.rstrip(_TRAILING).strip()


def algorithmic_verdict(reference, submitted):
    """算法判分：仅在有把握时给出 incorrect，其余返回 None 交模型。"""
    target = normalize(submitted)
    source = (reference or "").strip()
    if not target or not source:
        return None
    if "\\begin{" in source or "\n" in source or "\r" in source:
        return None
    if len(source) > MAX_REFERENCE_CHARS:
        return None
    comparable = False
    for candidate in _SPLIT.split(source):
        current = normalize(candidate)
        if not current:
            continue
        if current == target:
            return "correct"
        left = _as_number(current)
        right = _as_number(target)
        if left is None or right is None:
            continue
        comparable = True
        if math.isclose(left, right, rel_tol=REL_TOL, abs_tol=REL_TOL):
            return "correct"
    return "incorrect" if comparable else None


def generate_rubric(problem, answer):
    """依据 problem_answers.text 生成得分点，失败抛 GradingUnavailable。"""
    reference = _answer_text(answer)
    if not reference:
        raise GradingUnavailable("题目缺少文字详解，无法生成得分点")
    payload = _json_object(_chat(_rubric_messages(problem, reference)))
    points = _coerce_points(payload.get("points"))
    if not points:
        raise GradingUnavailable("模型未返回可用得分点")
    if len(points) > MAX_POINTS:
        raise GradingUnavailable("模型返回的得分点过多，无法可靠判分")
    return points


def rubric_for(problem, answer):
    """读取缓存得分点；详解变更或未命中则重新生成，存储异常抛 GradingUnavailable。"""
    problem_id = (problem or {}).get("id")
    if not problem_id:
        raise GradingUnavailable("题目缺少 ID，无法读取得分点")
    reference = _answer_text(answer)
    digest = hashlib.sha256(reference.encode("utf-8")).hexdigest()
    try:
        cached = store.get("problem_rubrics", problem_id=problem_id)
    except Exception as exc:
        raise GradingUnavailable("得分点缓存不可用") from exc
    if isinstance(cached, dict) and cached.get("answer_hash") == digest:
        points = _coerce_points(cached.get("points"))
        if points:
            return points
    if not ai.enabled():
        raise GradingUnavailable("AI 判分未配置")
    points = generate_rubric(problem, answer)
    record = {
        "problem_id": problem_id,
        "answer_hash": digest,
        "points": points,
        "model": config.AI_MODEL,
        "created_at": now(),
    }
    try:
        store.put("problem_rubrics", record)
    except Exception as exc:
        raise GradingUnavailable("得分点缓存写入失败") from exc
    return points


def judge(problem, answer, submitted):
    """判分入口；AI 不可用时返回 None，调用方保持待自检。"""
    kind = (problem or {}).get("type")
    if kind == "choice":
        raise ValueError("选择题由现有自动判分处理，不应调用 AI 判分")
    if kind not in ("blank", "solution"):
        raise ValueError("AI 判分不支持该题型")
    text = (submitted or "").strip()
    if kind == "blank":
        verdict = algorithmic_verdict(_answer_text(answer), text)
        if verdict is not None:
            score = 1 if verdict == "correct" else 0
            return _result(verdict, score, 1, [], "algorithm")
        if not text or not ai.enabled():
            return None
        try:
            return _judge_blank(problem, answer, text)
        except GradingUnavailable:
            return None
    if not text or not ai.enabled():
        return None
    try:
        points = rubric_for(problem, answer)
        return _judge_solution(problem, answer, text, points)
    except GradingUnavailable:
        return None


def _judge_blank(problem, answer, submitted):
    reference = _answer_text(answer)
    payload = _json_object(_chat(_blank_messages(problem, reference, submitted)))
    value = payload.get("result")
    if isinstance(value, str):
        value = value.strip().lower()
    if value not in ("correct", "incorrect"):
        raise GradingUnavailable("模型未给出可用判分结论")
    score = 1 if value == "correct" else 0
    return _result(value, score, 1, [], config.AI_MODEL)


def _judge_solution(problem, answer, submitted, rubric):
    reference = _answer_text(answer)
    messages = _solution_messages(problem, reference, submitted, rubric)
    payload = _json_object(_chat(messages))
    raw = payload.get("points")
    if not isinstance(raw, list):
        raise GradingUnavailable("模型未返回得分点判定结果")
    verdicts = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        point_id = item.get("id")
        if isinstance(point_id, str) and point_id.strip().isdigit():
            point_id = int(point_id.strip())
        if isinstance(point_id, bool) or not isinstance(point_id, int):
            continue
        hit = _as_bool(item.get("hit"))
        if hit is None:
            continue
        note = item.get("note")
        verdicts[point_id] = (hit, note.strip() if isinstance(note, str) else "")
    judged = []
    for entry in rubric:
        current = verdicts.get(entry["id"])
        if current is None:
            raise GradingUnavailable("模型未判定全部得分点")
        judged.append({"point": entry["point"], "hit": current[0], "note": current[1]})
    hits = sum(1 for item in judged if item["hit"])
    result = "correct" if hits == len(judged) else "incorrect"
    return _result(result, hits, len(judged), judged, config.AI_MODEL)


def _chat(messages):
    """唯一的模型调用点，测试可替换本函数。"""
    if not ai.enabled():
        raise GradingUnavailable(ai.reason() or "AI 判分未配置")
    try:
        response = httpx.post(
            config.AI_BASE_URL + "/chat/completions",
            headers={
                "Authorization": "Bearer " + ai.resolve_key(),
                "Content-Type": "application/json",
            },
            json={
                "model": config.AI_MODEL,
                "messages": messages,
                "temperature": 0,
            },
            timeout=config.AI_TIMEOUT,
        )
    except Exception as exc:
        raise GradingUnavailable("模型服务请求失败") from exc
    if response.status_code >= 400:
        raise GradingUnavailable("模型服务返回状态码 " + str(response.status_code))
    try:
        payload = response.json()
    except ValueError as exc:
        raise GradingUnavailable("模型服务响应不是 JSON") from exc
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GradingUnavailable("模型服务响应缺少正文") from exc
    if not isinstance(content, str) or not content.strip():
        raise GradingUnavailable("模型返回内容为空")
    return content


def _json_object(raw):
    text = raw.strip()
    if text.startswith(chr(96) * 3):
        text = _FENCE.sub("", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise GradingUnavailable("模型输出不是 JSON 对象")
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise GradingUnavailable("模型输出不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise GradingUnavailable("模型输出不是 JSON 对象")
    return payload


def _coerce_points(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if not isinstance(value, list):
        return []
    points = []
    for item in value:
        if isinstance(item, dict):
            text = item.get("point")
        elif isinstance(item, str):
            text = item
        else:
            text = None
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            continue
        points.append({"id": len(points) + 1, "point": text[:MAX_POINT_CHARS]})
    return points


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes"):
            return True
        if lowered in ("false", "no"):
            return False
    return None


def _as_number(text):
    value = (text or "").strip()
    match = _LATEX_FRACTION.match(value) or _FRACTION.match(value)
    if match:
        top = _as_number(match.group(1))
        bottom = _as_number(match.group(2))
        if top is None or bottom is None or bottom == 0:
            return None
        return top / bottom
    if _NUMBER.match(value):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _answer_text(answer):
    if not isinstance(answer, dict):
        return ""
    text = answer.get("text")
    return text.strip() if isinstance(text, str) else ""


def _clip(text, limit):
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "……（已截断）"


def _result(result, score, max_score, points, model):
    return {
        "result": result,
        "score": int(score),
        "max_score": int(max_score),
        "points": points,
        "model": model,
    }


def _messages(body):
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": body},
    ]


def _problem_block(problem, reference):
    question = str((problem or {}).get("question_text") or "")
    return (
        "【题目】\n"
        + _clip(question, MAX_INPUT_CHARS)
        + "\n\n【参考答案与详解】\n"
        + _clip(reference, MAX_INPUT_CHARS)
    )


def _blank_messages(problem, reference, submitted):
    instruction = (
        "【要求】判断学生作答与参考答案是否数学等价（允许写法或等价变形不同）。"
        '只返回 JSON：{"result": "correct", "note": "不超过 40 字的理由"}；'
        'result 只能取 "correct" 或 "incorrect"。'
    )
    body = (
        _problem_block(problem, reference)
        + "\n\n【学生作答】\n"
        + _clip(submitted, MAX_INPUT_CHARS)
        + "\n\n"
        + instruction
    )
    return _messages(body)


def _rubric_messages(problem, reference):
    instruction = (
        "【要求】依据参考详解概括该题的关键得分点，3 到 6 条，"
        "每条一句简短中文，按解题先后排列，只写可判定的步骤或结论。"
        '只返回 JSON：{"points": ["得分点一", "得分点二"]}。'
    )
    return _messages(_problem_block(problem, reference) + "\n\n" + instruction)


def _solution_messages(problem, reference, submitted, rubric):
    lines = ["得分点列表："]
    for entry in rubric:
        lines.append(str(entry["id"]) + ". " + entry["point"])
    instruction = (
        "【要求】逐条判断学生作答是否命中每个得分点："
        "写出该得分点的关键步骤或结论才算命中。"
        '只返回 JSON：{"points": [{"id": 1, "hit": true, '
        '"note": "不超过 40 字的理由"}]}；必须覆盖上面全部得分点，'
        "id 使用上面的编号，hit 只能是 true 或 false。"
    )
    body = "\n".join(
        [
            _problem_block(problem, reference),
            "",
            *lines,
            "",
            "【学生作答】",
            _clip(submitted, MAX_INPUT_CHARS),
            "",
            instruction,
        ]
    )
    return _messages(body)
