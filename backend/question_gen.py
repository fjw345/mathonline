"""AI 生题：仿照原题生成「同思路、不同数据」的新题，并独立求解校验可解性。

模拟卷与错题变式练习共用本模块。每次生成固定两步：先命题，再由模型独立求解
并核验答案；两步都通过才返回题目。生成结果不写入公开题库，只进入模拟卷或
变式练习记录，避免未经人工审核的题目混入正式题库。
"""

from . import ai, config, grading

MAX_STEM = 4000
MAX_OPTION = 400
MAX_ANSWER = 800
MAX_EXPLANATION = 4000
MAX_SEED_CHARS = 3000
LETTERS = ("A", "B", "C", "D")

_GENERATE_SYSTEM = (
    "你是大学数学命题专家。你要仿照给定原题命制一道新题：考查同样的知识点、"
    "使用相同或相近的解法思路，但必须更换数据、函数、参数或情境，使新题答案"
    "与原题不同。新题必须自洽、有唯一确定的答案、可以手算或常规推导解出。"
    "严格输出一个 JSON 对象，不要输出解释文字或 Markdown 代码围栏。"
)
_VERIFY_SYSTEM = (
    "你是严格的数学阅卷人。只依据题目本身独立求解，不假设参考答案正确。"
    "严格输出一个 JSON 对象，不要输出解释文字或 Markdown 代码围栏。"
)


class GenerationError(Exception):
    """多次生成都未通过结构或可解性校验。"""


def build_generation_messages(seed, answer_text, subject, kind):
    type_name = {"choice": "选择题", "blank": "填空题", "solution": "解答题"}.get(
        kind, "解答题"
    )
    options = {
        "choice": (
            "题干（四个选项必须写在 stem 里，用 (A)(B)(C)(D) 标号）",
            "四个选项文本，长度 4，按 A-D 顺序",
            "正确选项字母，只能是 A/B/C/D",
        ),
        "blank": ("题干（留一个空，用 ____ 表示）", "", "这个空的答案"),
        "solution": ("题干（求解题，结果必须唯一）", "", "最终答案"),
    }[kind]
    body = (
        "【原题】\n" + ai.clip(seed.get("question_text"), MAX_SEED_CHARS) + "\n\n"
        "【原题参考答案】\n" + ai.clip(answer_text, MAX_SEED_CHARS) + "\n\n"
        "【要求】\n"
        "1. 与原题考查同一个知识点、沿用相同或相近的解题思路，但数据、函数、"
        "参数或情境必须不同，答案也要不同。\n"
        "2. 题目必须可解且有唯一确定的答案；避免需要额外假设、超出条件的表述。\n"
        "3. 填空题与选择题的答案尽量是具体数值或简短表达式（如 1/2、3、x+1），"
        "便于按答案核对；解答题也应有明确的最终结果。\n"
        "4. 把新题写成" + type_name + "：" + options[0] + "。\n"
        "5. explanation 不超过 200 字，只写关键步骤、最终答案与原题解法的对应关系，"
        "不要长篇讨论。\n"
        "6. 只返回 JSON："
        '{"stem":"题干","options":["A 选项","B 选项","C 选项","D 选项"],'
        '"correct":"A","answer":"最终答案","explanation":"解题过程"}。'
        "非选择题把 options 写成空数组、correct 写成空字符串。"
    )
    return [
        {"role": "system", "content": _GENERATE_SYSTEM},
        {"role": "user", "content": body},
    ]


def build_verify_messages(item):
    if item["type"] == "choice":
        task = (
            "请独立求解这道选择题，判断题目条件是否充分、答案是否唯一、"
            "解析中的正确选项是否与给出的正确选项一致。只返回 JSON："
            '{"solvable":true,"unique":true,"consistent":true,"correct":"A",'
            '"reason":"不超过60字的理由"}'
        )
        answer = "正确选项：" + "".join(item.get("correct_options") or [])
    else:
        task = (
            "请独立求解这道题，判断题目条件是否充分、答案是否唯一，"
            "并核对给出的参考答案与解析的最终结论是否一致。只返回 JSON："
            '{"solvable":true,"unique":true,"consistent":true,'
            '"answer":"你独立解出的答案","explanation_answer":"解析中的最终答案",'
            '"reason":"不超过60字的理由"}'
        )
        answer = "参考答案：" + (item.get("answer_text") or "")
    body = (
        "【题目】\n" + ai.clip(item["question_text"], MAX_STEM) + "\n\n"
        "【待核验答案】\n" + ai.clip(answer, MAX_ANSWER) + "\n\n"
        "【解析】\n" + ai.clip(item.get("explanation"), MAX_ANSWER) + "\n\n" + task
    )
    return [
        {"role": "system", "content": _VERIFY_SYSTEM},
        {"role": "user", "content": body},
    ]


def generate_verified(seed, answer_text, subject, kind, attempts=4):
    """生成并核验一道题；全部尝试失败抛 GenerationError，模型不可用抛 AIUnavailable。"""
    if not ai.enabled():
        raise ai.AIUnavailable("AI 生题未配置")
    if kind not in ("choice", "blank", "solution"):
        raise GenerationError("不支持的题型")
    last_reason = ""
    for _attempt in range(max(1, attempts)):
        try:
            payload = ai.complete_json(
                build_generation_messages(seed, answer_text, subject, kind),
                max_tokens=4000,
            )
            item = normalize_generated(payload, kind)
            if not item:
                last_reason = "结构不完整"
                continue
            if same_as_seed(item, seed):
                last_reason = "与原题过于相似"
                continue
            if not verify_item(item):
                last_reason = "独立求解未通过"
                continue
            return item
        except ai.AIUnavailable as exc:
            last_reason = str(exc)
            if not ai.enabled():
                raise
    raise GenerationError("多次生成均未通过校验：" + last_reason)


def normalize_generated(payload, kind):
    stem = ai.clean_text(payload.get("stem"), MAX_STEM).strip()
    if not stem:
        return None
    explanation = ai.clean_text(payload.get("explanation"), MAX_EXPLANATION).strip()
    if kind == "choice":
        raw_options = ai.parse_list(payload.get("options"))
        options = [
            ai.clean_text(option, MAX_OPTION).strip()
            for option in raw_options
            if isinstance(option, str) and option.strip()
        ]
        if len(options) < 3:
            return None
        correct = str(payload.get("correct") or "").strip().upper()
        if correct not in LETTERS[: len(options)]:
            correct = ""
        if not correct:
            # 模型偶尔把正确选项写成文本：只接受能唯一对应的那一个。
            matches = [
                LETTERS[index]
                for index, option in enumerate(options)
                if _option_matches(option, payload.get("answer"))
            ]
            if len(matches) != 1:
                return None
            correct = matches[0]
        options = [
            _strip_letter(option, index) for index, option in enumerate(options)
        ]
        answer_text = options[LETTERS.index(correct)]
        stem = stem + "\n\n" + "\n".join(
            "(%s) %s" % (LETTERS[index], option)
            for index, option in enumerate(options)
        )
        return {
            "type": "choice",
            "question_text": stem,
            "choice_options": list(LETTERS[: len(options)]),
            "multiple": False,
            "correct_options": [correct],
            "answer_text": answer_text,
            "explanation": explanation,
            "model": config.AI_MODEL,
        }
    answer = ai.clean_text(payload.get("answer"), MAX_ANSWER).strip()
    if not answer:
        return None
    return {
        "type": kind,
        "question_text": stem,
        "choice_options": [],
        "multiple": False,
        "correct_options": [],
        "answer_text": answer,
        "explanation": explanation,
        "model": config.AI_MODEL,
    }


def verify_item(item):
    try:
        payload = ai.complete_json(build_verify_messages(item), max_tokens=1400)
    except ai.AIUnavailable:
        return False
    if not all(_as_bool(payload.get(key)) for key in ("solvable", "unique")):
        return False
    if not _as_bool(payload.get("consistent")):
        return False
    if item["type"] == "choice":
        correct = str(payload.get("correct") or "").strip().upper()
        return correct == "".join(item.get("correct_options") or [])
    independent = str(payload.get("answer") or "").strip()
    if not answers_match(item.get("answer_text"), independent):
        return False
    # 解析里的最终结论必须与 answer 字段一致，挡住“答案与解析各写一个数”的题。
    explanation_answer = str(payload.get("explanation_answer") or "").strip()
    return bool(explanation_answer) and answers_match(
        item.get("answer_text"), explanation_answer
    )


def answers_match(given, independent):
    """数值容差优先；非数值答案要求归一化后一致。"""
    if not given or not independent:
        return False
    if grading.algorithmic_verdict(given, independent) == "correct":
        return True
    left = grading.normalize(given)
    right = grading.normalize(independent)
    return bool(left) and left == right


def same_as_seed(item, seed):
    left = grading.normalize(item.get("question_text"))
    right = grading.normalize(seed.get("question_text"))
    return bool(left and right) and left == right


def _strip_letter(option, index):
    text = option.strip()
    prefix = LETTERS[index] if index < len(LETTERS) else ""
    for mark in (prefix + ".", prefix + "、", prefix + ")", prefix + "．"):
        if mark and text.startswith(mark):
            return text[len(mark) :].strip()
    return text


def _option_matches(option, answer):
    if not isinstance(answer, str) or not answer.strip():
        return False
    target = answer.strip()
    return target in option or grading.normalize(option) == grading.normalize(target)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return False


def seed_payload(problem, answer_text):
    """把题库题目整理成生成用的 seed（不改变原题）。"""
    return {
        "id": problem.get("id"),
        "type": problem.get("type"),
        "subject": problem.get("subject"),
        "title": problem.get("title"),
        "question_text": problem.get("question_text") or "",
        "knowledge_points": problem.get("knowledge_points") or [],
        "difficulty": problem.get("difficulty"),
        "answer_text": answer_text or "",
    }
