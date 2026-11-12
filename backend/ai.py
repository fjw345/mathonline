"""OpenAI 兼容模型的统一调用入口。

判分引擎（backend/grading.py）有自己的一份调用实现，那里要保持既有行为与
测试替身不变；本模块服务学情分析、考试预测与 AI 生题，三者都要求模型输出
可解析的 JSON，失败一律抛 AIUnavailable，由上层降级而不是编造结果。
"""

import json
import logging
import re
import time

import httpx

from . import config

_FENCE = re.compile(r"^" + chr(96) * 3 + r"[A-Za-z0-9_-]*\s*")
_JSON_MODE = True


class AIUnavailable(Exception):
    """模型未配置、请求失败或输出不可解析。"""


class AIOutputError(AIUnavailable):
    """模型有响应但不是期望的 JSON。"""


def enabled():
    return bool(config.AI_ENABLED)


def chat(
    messages,
    *,
    temperature=0,
    timeout=None,
    max_tokens=1800,
    retries=2,
    model=None,
):
    """单次对话补全；失败按退避重试，仍失败抛 AIUnavailable。"""
    global _JSON_MODE
    if not config.AI_ENABLED:
        raise AIUnavailable("AI 未配置")
    body = {
        "model": model or config.AI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if _JSON_MODE:
        body["response_format"] = {"type": "json_object"}
    last_error = ""
    for attempt in range(retries + 1):
        try:
            response = httpx.post(
                config.AI_BASE_URL + "/chat/completions",
                headers={
                    "Authorization": "Bearer " + config.AI_API_KEY,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=timeout or config.AI_TIMEOUT,
            )
        except Exception as exc:  # 网络错误与超时
            last_error = type(exc).__name__
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise AIUnavailable("模型服务请求失败：" + last_error) from exc
        if response.status_code == 400 and "response_format" in body:
            # 并非所有兼容实现都支持 JSON 模式；去掉后重试一次，不改变提示词。
            _JSON_MODE = False
            body.pop("response_format", None)
            continue
        if response.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            last_error = "模型服务返回状态码 " + str(response.status_code)
            time.sleep(1.2 * (attempt + 1))
            continue
        if response.status_code >= 400:
            logging.getLogger("mathonline").error(
                "AI request failed with %s", response.status_code
            )
            raise AIUnavailable("模型服务返回状态码 " + str(response.status_code))
        try:
            payload = response.json()
            message = payload["choices"][0]["message"]
            content = message.get("content")
            reasoning = message.get("reasoning_content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIUnavailable("模型服务响应缺少正文") from exc
        if not content and reasoning and attempt < retries and max_tokens < 4000:
            # 思考型模型可能把预算全花在推理上；加预算重试一次，而不是当作不可用。
            body["max_tokens"] = min(4000, max(1200, max_tokens * 3))
            last_error = "模型只返回了推理内容"
            continue
        if not isinstance(content, str) or not content.strip():
            raise AIUnavailable("模型返回内容为空")
        return content
    raise AIUnavailable(last_error or "模型服务暂不可用")


def json_object(raw):
    """从模型输出中取出 JSON 对象；允许 Markdown 围栏与前后缀说明。"""
    text = (raw or "").strip()
    if text.startswith(chr(96) * 3):
        text = _FENCE.sub("", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise AIOutputError("模型输出不是 JSON 对象")
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise AIOutputError("模型输出不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise AIOutputError("模型输出不是 JSON 对象")
    return payload


def complete_json(messages, **kwargs):
    return json_object(chat(messages, **kwargs))


def parse_list(value):
    """模型偶尔把数组写成 JSON 字符串；统一转成 list。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def clean_text(value, limit):
    text = value.strip() if isinstance(value, str) else ""
    return text[:limit]


def clip(text, limit):
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "……（已截断）"
