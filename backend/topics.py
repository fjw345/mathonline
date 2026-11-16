"""知识点标签体系：统一编码、学科映射、启发式识别与难度估计。

知识点使用稳定的 ASCII 编码存库（problems.knowledge_points），中文名称只由
本模块输出，前端与接口不各存一份中文。编码一经发布不要改名；新增知识点只
追加，避免历史统计与筛选链接失效。
"""

DOMAIN_NAMES = {
    "calculus": "高等数学",
    "linear_algebra": "线性代数",
    "probability": "概率论",
}

# code: (中文名, 所属领域, 关键词)
KNOWLEDGE = {
    "limit": (
        "极限与连续",
        "calculus",
        ("极限", "lim", "连续", "间断点", "无穷小", "无穷大", "洛必达", "夹逼", "单调有界"),
    ),
    "derivative": (
        "导数与微分",
        "calculus",
        ("导数", "求导", "微分", "切线", "法线", "高阶导数", "隐函数", "参数方程"),
    ),
    "mean_value": (
        "微分中值定理与导数应用",
        "calculus",
        ("中值定理", "罗尔", "拉格朗日", "柯西", "单调", "极值", "最值", "拐点", "凹凸", "渐近线"),
    ),
    "indefinite_integral": (
        "不定积分",
        "calculus",
        ("不定积分", "原函数", "换元积分", "分部积分"),
    ),
    "definite_integral": (
        "定积分及其应用",
        "calculus",
        ("定积分", "变限积分", "积分上限", "面积", "旋转体", "弧长", "体积", "平均值"),
    ),
    "improper_integral": (
        "反常积分",
        "calculus",
        ("反常积分", "广义积分", "瑕点", "瑕积分"),
    ),
    "ode": (
        "微分方程",
        "calculus",
        ("微分方程", "通解", "特解", "初值", "齐次", "积分因子"),
    ),
    "series": (
        "无穷级数",
        "calculus",
        ("级数", "幂级数", "收敛半径", "傅里叶", "泰勒", "麦克劳林", "数项级数"),
    ),
    "multivariable_derivative": (
        "多元函数微分学",
        "calculus",
        ("偏导", "全微分", "梯度", "方向导数", "拉格朗日乘数", "二元函数"),
    ),
    "multiple_integral": (
        "重积分",
        "calculus",
        ("二重积分", "三重积分", "重积分", "极坐标", "柱坐标", "球坐标", "积分区域"),
    ),
    "line_surface_integral": (
        "曲线与曲面积分",
        "calculus",
        ("曲线积分", "曲面积分", "格林公式", "高斯公式", "斯托克斯", "散度", "旋度"),
    ),
    "vector_geometry": (
        "空间解析几何",
        "calculus",
        ("向量", "平面方程", "直线方程", "曲面", "切平面", "法线", "距离"),
    ),
    "inequality_proof": (
        "不等式与综合证明",
        "calculus",
        ("证明", "不等式", "存在", "至少", "构造"),
    ),
    "determinant": (
        "行列式",
        "linear_algebra",
        ("行列式", "代数余子式", "范德蒙德"),
    ),
    "matrix": (
        "矩阵与初等变换",
        "linear_algebra",
        ("矩阵", "秩", "逆矩阵", "初等变换", "分块", "伴随"),
    ),
    "vector_space": (
        "向量组与线性方程组",
        "linear_algebra",
        ("向量组", "线性相关", "线性无关", "线性方程组", "基础解系", "极大无关组", "通解"),
    ),
    "eigenvalue": (
        "特征值与特征向量",
        "linear_algebra",
        ("特征值", "特征向量", "相似", "对角化", "若尔当"),
    ),
    "quadratic_form": (
        "二次型",
        "linear_algebra",
        ("二次型", "正定", "合同", "惯性指数", "标准形"),
    ),
    "probability_basic": (
        "随机事件与概率",
        "probability",
        ("概率", "古典概型", "条件概率", "全概率", "贝叶斯", "独立", "互斥"),
    ),
    "random_variable": (
        "随机变量及其分布",
        "probability",
        ("分布函数", "概率密度", "分布律", "正态分布", "二项分布", "泊松", "均匀分布", "指数分布"),
    ),
    "multivariate_probability": (
        "多维随机变量",
        "probability",
        ("二维", "联合分布", "边缘分布", "条件分布", "独立性", "卷积"),
    ),
    "expectation_variance": (
        "数字特征",
        "probability",
        ("期望", "方差", "协方差", "相关系数", "矩", "标准差"),
    ),
    "limit_theorem": (
        "大数定律与中心极限定理",
        "probability",
        ("大数定律", "中心极限定理", "切比雪夫", "辛钦", "伯努利"),
    ),
    "statistics": (
        "数理统计初步",
        "probability",
        ("统计量", "样本", "点估计", "矩估计", "极大似然", "置信区间", "假设检验"),
    ),
}

SUBJECT_DOMAINS = {
    "微积分上": "calculus",
    "微积分下": "calculus",
    "高等数学": "calculus",
    "线性代数": "linear_algebra",
    "概率论": "probability",
}

_CALCULUS_UPPER = (
    "limit",
    "derivative",
    "mean_value",
    "indefinite_integral",
    "definite_integral",
    "improper_integral",
    "ode",
    "inequality_proof",
)
_CALCULUS_LOWER = (
    "vector_geometry",
    "multivariable_derivative",
    "multiple_integral",
    "line_surface_integral",
    "series",
    "ode",
    "inequality_proof",
)
SUBJECT_KNOWLEDGE = {
    "微积分上": _CALCULUS_UPPER,
    "微积分下": _CALCULUS_LOWER,
    "高等数学": tuple(code for code in KNOWLEDGE if KNOWLEDGE[code][1] == "calculus"),
    "线性代数": tuple(
        code for code in KNOWLEDGE if KNOWLEDGE[code][1] == "linear_algebra"
    ),
    "概率论": tuple(code for code in KNOWLEDGE if KNOWLEDGE[code][1] == "probability"),
}

_MAX_POINTS = 4
_DIFFICULTY_PROOF = ("证明", "讨论", "参数", "取值范围", "至少", "构造")


def domain_for_subject(subject):
    return SUBJECT_DOMAINS.get(str(subject or "").strip(), "")


def allowed_codes(subject):
    """该学科允许的知识点编码；未知学科放开全部，避免丢标签。"""
    return SUBJECT_KNOWLEDGE.get(str(subject or "").strip(), tuple(KNOWLEDGE))


def name_for(code):
    entry = KNOWLEDGE.get(code)
    return entry[0] if entry else ""


def domain_for(code):
    entry = KNOWLEDGE.get(code)
    return entry[1] if entry else ""


def resolve(codes, subject=None):
    """把库里的编码解析成前端对象，保持顺序、去重、丢弃越界编码。"""
    allowed = set(allowed_codes(subject)) if subject else set(KNOWLEDGE)
    result = []
    seen = set()
    for code in codes or []:
        if not isinstance(code, str) or code not in KNOWLEDGE:
            continue
        if code in seen or code not in allowed:
            continue
        seen.add(code)
        result.append(
            {
                "code": code,
                "name": KNOWLEDGE[code][0],
                "domain": KNOWLEDGE[code][1],
            }
        )
    return result


def normalize_codes(codes, subject=None):
    """模型或表单输入 → 合法编码列表；同时接受中文名。"""
    allowed = set(allowed_codes(subject)) if subject else set(KNOWLEDGE)
    by_name = {entry[0]: code for code, entry in KNOWLEDGE.items()}
    result = []
    for item in codes or []:
        value = item.get("code") if isinstance(item, dict) else item
        if not isinstance(value, str):
            continue
        value = value.strip()
        code = value if value in KNOWLEDGE else by_name.get(value, "")
        if code and code in allowed and code not in result:
            result.append(code)
    return result[:_MAX_POINTS]


def clamp_difficulty(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return min(5, max(1, number))


def invalid_codes(items, subject=None):
    """返回无法映射到该学科允许范围的输入，供管理端拒绝脏数据。"""
    allowed = set(allowed_codes(subject)) if subject else set(KNOWLEDGE)
    by_name = {entry[0]: code for code, entry in KNOWLEDGE.items()}
    bad = []
    for item in items or []:
        value = item.get("code") if isinstance(item, dict) else item
        value = value.strip() if isinstance(value, str) else ""
        code = value if value in KNOWLEDGE else by_name.get(value, "")
        if not code or code not in allowed:
            bad.append(value)
    return bad


def heuristic_codes(problem, limit=3):
    """离线兜底：按关键词命中数排序，保证无模型时也有标签。"""
    subject = (problem or {}).get("subject")
    allowed = allowed_codes(subject)
    text = " ".join(
        str((problem or {}).get(key) or "")
        for key in ("question_text", "title", "group_name")
    )
    scored = []
    for code in allowed:
        score = 0
        for keyword in KNOWLEDGE[code][2]:
            if keyword and keyword in text:
                score += 1 if keyword.isascii() else 2
        if code == "inequality_proof" and "证明" not in text:
            score = 0
        if score:
            scored.append((score, code))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored:
        return []
    top = scored[0][0]
    return [code for score, code in scored if score >= max(1, top - 2)][:limit]


def heuristic_difficulty(problem):
    kind = (problem or {}).get("type")
    base = {"choice": 2, "blank": 3, "solution": 4}.get(kind, 3)
    text = str((problem or {}).get("question_text") or "")
    if len(text) > 400:
        base += 1
    if any(keyword in text for keyword in _DIFFICULTY_PROOF):
        base += 1
    if len(text) < 120:
        base -= 1
    return clamp_difficulty(base)


def classify(problem):
    """离线标注：返回 (codes, difficulty)，两处都为保守估计。"""
    return heuristic_codes(problem), heuristic_difficulty(problem)


def catalog(codes=None):
    """知识点目录，按领域 + 固定顺序输出，供筛选下拉使用。"""
    selected = set(codes) if codes is not None else None
    result = []
    for code, (label, domain, _keywords) in KNOWLEDGE.items():
        if selected is not None and code not in selected:
            continue
        result.append({"code": code, "name": label, "domain": domain})
    return result


def knows(code):
    return code in KNOWLEDGE


def guess_subject_domain(subject):
    """未知学科的中文名里带有关键词时也能落到正确领域。"""
    text = str(subject or "")
    if any(keyword in text for keyword in ("线代", "矩阵")):
        return "linear_algebra"
    if any(keyword in text for keyword in ("概率", "统计")):
        return "probability"
    if any(keyword in text for keyword in ("微积分", "高数", "数学分析", "高等数学")):
        return "calculus"
    return ""


def parse_filter(raw):
    """筛选参数 knowledge=limit,derivative；非法值直接忽略。"""
    if not raw:
        return []
    values = raw.split(",") if isinstance(raw, str) else list(raw)
    result = []
    for value in values:
        code = str(value).strip()
        if code in KNOWLEDGE and code not in result:
            result.append(code)
    return result


__all__ = [
    "DOMAIN_NAMES",
    "KNOWLEDGE",
    "SUBJECT_DOMAINS",
    "SUBJECT_KNOWLEDGE",
    "allowed_codes",
    "catalog",
    "classify",
    "clamp_difficulty",
    "domain_for",
    "domain_for_subject",
    "guess_subject_domain",
    "heuristic_codes",
    "heuristic_difficulty",
    "invalid_codes",
    "knows",
    "name_for",
    "normalize_codes",
    "parse_filter",
    "resolve",
]
