"""题目知识点标注：模型优先，关键词兜底。

`tag_problem` 是唯一入口，离线测试与 `scripts/tag_knowledge.py` 共用它。
模型不可用或返回越界编码时自动回落到关键词规则，保证每道题至少有标签；
只有连关键词也命中不了时才给该学科的第一个知识点，并把来源标为 heuristic，
方便管理员在后台按来源筛选后人工校正。
"""

from . import ai, config, topics

MAX_REFERENCE_CHARS = 1200
MAX_QUESTION_CHARS = 2600

_SYSTEM = (
    "你是大学数学教研组的题目标注助手，只依据给出的题目、参考答案与题号信息判断，"
    "不补充题目没有的信息。严格输出一个 JSON 对象，不要输出解释文字，"
    "也不要输出 Markdown 代码围栏。"
)


def build_messages(problem, answer_text=""):
    subject = str(problem.get("subject") or "").strip() or "未知学科"
    options = "\n".join(
        code + "：" + topics.name_for(code) for code in topics.allowed_codes(subject)
    )
    body = (
        "【学科】" + subject + "\n"
        "【题型】" + _type_name(problem.get("type")) + "\n"
        "【题目】\n" + ai.clip(problem.get("question_text"), MAX_QUESTION_CHARS) + "\n\n"
        "【参考答案】\n" + ai.clip(answer_text, MAX_REFERENCE_CHARS) + "\n\n"
        "【可选知识点】（只能从下列编码中选择 1 至 3 个）\n" + options + "\n\n"
        "【要求】\n"
        "1. 选出最能代表本题考点的 1 至 3 个编码，按主次排序，不要凑数；"
        "纯计算题不要选“不等式与综合证明”。\n"
        "2. 估计难度：1 基础（套公式）、2 较易、3 中等、4 较难（多步综合）、"
        "5 难（竞赛或压轴证明）。\n"
        '3. 只返回 JSON：{"knowledge":["limit"],"difficulty":2}，'
        "编码必须来自上面的列表，不要返回中文名。"
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": body},
    ]


def tag_problem(problem, answer_text="", use_ai=True):
    """返回 {knowledge_points, difficulty, source, model}；不写库。"""
    subject = problem.get("subject")
    if use_ai and ai.enabled():
        try:
            payload = ai.complete_json(
                build_messages(problem, answer_text), max_tokens=1000
            )
        except ai.AIUnavailable:
            payload = {}
        if payload:
            codes = topics.normalize_codes(
                payload.get("knowledge") or payload.get("knowledge_points"), subject
            )
            if codes:
                difficulty = topics.clamp_difficulty(payload.get("difficulty"))
                return {
                    "knowledge_points": codes,
                    "difficulty": difficulty or topics.heuristic_difficulty(problem),
                    "source": "ai",
                    "model": config.AI_MODEL,
                }
    codes = topics.heuristic_codes(problem) or default_codes(subject)
    return {
        "knowledge_points": codes,
        "difficulty": topics.heuristic_difficulty(problem),
        "source": "heuristic",
        "model": "",
    }


def default_codes(subject):
    allowed = topics.allowed_codes(subject)
    return [allowed[0]] if allowed else []


def _type_name(kind):
    return {"choice": "选择题", "blank": "填空题", "solution": "解答题"}.get(
        kind, str(kind or "")
    )


def select_targets(
    problems, retag=False, override_manual=False, retag_heuristic=False
):
    """挑选本次要标注的题目：人工标注与已有标签默认跳过。

    - 默认只补缺失标签；
    - retag 重算已有标签，但仍跳过人工标注；
    - retag_heuristic 只重算关键词兜底的题目（AI 失败后补跑时用）；
    - override_manual 允许覆盖人工标注（只处理缺失与人工两类）；
    - 多项同时给出时按并集处理。
    """
    targets = []
    for problem in problems:
        source = problem.get("knowledge_source") or ""
        codes = problem.get("knowledge_points") or []
        if source == "manual":
            if override_manual:
                targets.append(problem)
            continue
        if codes and not retag:
            if not (retag_heuristic and source == "heuristic"):
                continue
        targets.append(problem)
    return targets


def merge(items, existing):
    """保留人工标注；其余按「已有 AI 标签保持、缺失才补」的规则更新。"""
    source = (existing or {}).get("knowledge_source") or ""
    codes = (existing or {}).get("knowledge_points") or []
    if source == "manual":
        return None
    if source == "ai" and codes:
        return None
    return items
