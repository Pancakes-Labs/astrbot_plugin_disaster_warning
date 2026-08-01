"""
气象预警编码映射表。

负责把 CMA 预警地图 API 的 p 编码（如 p0002003）转换为
Fan Studio 图标接口兼容的 11B 编码（如 11B03_yellow），
并提供标题兜底映射能力。

设计原则：有什么图标用什么，不强行映射。
匹配不到的返回 None，由调用方走本地颜色回退。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# p 编码结构：p + 4位类型码 + 1位颜色码（1=红 2=橙 3=黄 4=蓝）
# 示例：p0002003 → 类型 0002=暴雨，颜色 3=黄色 → 11B03_yellow
# ---------------------------------------------------------------------------

# p 编码 4 位类型前缀 → 11B 基础码（仅保留有明确图标对应的类型）
_P_TYPE_TO_11B_BASE: dict[str, str] = {
    "0001": "11B01",  # 台风
    "0002": "11B03",  # 暴雨
    "0003": "11B09",  # 高温
    "0004": "11B05",  # 寒潮
    "0005": "11B17",  # 大雾
    "0006": "11B04",  # 暴雪
    "0007": "11B06",  # 大风
    "0008": "11B07",  # 沙尘暴
    "0009": "11B15",  # 冰雹
    "0010": "11B22",  # 干旱
    "0011": "11B21",  # 道路结冰
    "0012": "11B14",  # 雷电
    "0013": "11B16",  # 霜冻
    "0014": "11B19",  # 霾
    "0015": "11B20",  # 雷雨大风
}

# p 编码末位颜色数字 → 颜色后缀
_P_COLOR_DIGIT_TO_SUFFIX: dict[str, str] = {
    "1": "red",
    "2": "orange",
    "3": "yellow",
    "4": "blue",
}

# 特殊完整 p 编码直射（7位短码不遵循通用规则，需特殊处理）
# 这些码在数据库中实际灾害类型与通用规则不符，走标题兜底
_P_CODE_SKIP_GENERIC: frozenset[str] = frozenset(
    {
        "p0000001",  # 大雾/海区大雾 红色 — 通用规则会误判
        "p0000003",  # 道路冰雪/强对流 — 通用规则会误判为高温
        "p0000004",  # 道路冰雪 — 通用规则会误判为高温
    }
)

# ---------------------------------------------------------------------------
# 标题兜底：从标题文本提取灾害类型 → 11B 基础码
# 仅保留有明确 11B 图标对应的灾害类型，按关键词长度倒序排列。
# ---------------------------------------------------------------------------

_TITLE_TYPE_TO_11B_BASE: list[tuple[str, str]] = [
    # 复合类型优先（更长的关键词先匹配）
    ("雷雨大风", "11B20"),
    ("道路结冰", "11B21"),
    ("道路冰雪", "11B21"),
    ("道路积雪", "11B21"),
    ("沙尘暴", "11B07"),
    ("风暴潮", "11E02"),
    ("海浪", "11E06"),
    # 单类型
    ("台风", "11B01"),
    ("暴雨", "11B03"),
    ("暴雪", "11B04"),
    ("寒潮", "11B05"),
    ("大风", "11B06"),
    ("高温", "11B09"),
    ("雷电", "11B14"),
    ("冰雹", "11B15"),
    ("霜冻", "11B16"),
    ("大雾", "11B17"),
    ("浓雾", "11B17"),
    ("灰霾", "11B19"),
    ("干旱", "11B22"),
    ("霾", "11B19"),
]

# 标题颜色关键词 → 颜色后缀
_TITLE_COLOR_TO_SUFFIX: list[tuple[str, str]] = [
    ("红色", "red"),
    ("橙色", "orange"),
    ("黄色", "yellow"),
    ("蓝色", "blue"),
]
# 颜色后缀 → 中文颜色词（用于日志展示）
_COLOR_SUFFIX_TO_CN: dict[str, str] = {
    "red": "红色",
    "orange": "橙色",
    "yellow": "黄色",
    "blue": "蓝色",
}


def _is_p_code(code: str) -> bool:
    """判断是否为 CMA p 编码格式。"""
    return code.startswith("p") and len(code) >= 7 and code[1:].isdigit()


def _is_11b_code(code: str) -> bool:
    """判断是否为 Fan Studio 11B/11E 编码格式。"""
    return code.startswith("11B") or code.startswith("11E")


def resolve_weather_icon_code(
    weather_type_code: str,
    *,
    title: str = "",
    headline: str = "",
) -> str | None:
    """把气象预警编码解析为 Fan Studio 图标接口兼容的 11B 完整码。

    解析优先级：
    1. 已有 11B 编码（含下划线新格式或紧凑格式）直接返回
    2. p 编码通用规则（4位类型码 + 末位颜色码）
    3. 标题文本兜底（灾害类型 + 颜色）

    返回 None 表示无法映射，调用方应走本地颜色回退。
    """
    code = (weather_type_code or "").strip()

    # 1. 已有 11B 编码直接返回
    if code and _is_11b_code(code):
        return code

    # 2. p 编码通用规则
    if code and _is_p_code(code):
        result = _resolve_p_code_generic(code)
        if result:
            return result
        # p 编码通用规则失败，走标题兜底
        return _resolve_from_title(title, headline)

    # 3. 标题兜底
    return _resolve_from_title(title, headline)


def _resolve_p_code_generic(code: str) -> str | None:
    """按 p 编码通用规则（4位类型 + 末位颜色）解析 11B 完整码。"""
    # 特殊短码跳过通用规则
    if code in _P_CODE_SKIP_GENERIC:
        return None

    digits = code[1:]  # 去掉 p 前缀
    if len(digits) < 5:
        return None

    # 取前 4 位作为类型码，末位作为颜色码
    type_part = digits[:4]
    color_digit = digits[-1]

    base_11b = _P_TYPE_TO_11B_BASE.get(type_part)
    if not base_11b:
        return None

    color_suffix = _P_COLOR_DIGIT_TO_SUFFIX.get(color_digit)
    if not color_suffix:
        return None

    return f"{base_11b}_{color_suffix}"


def resolve_p_code_color(code: str) -> str | None:
    """解析 p 编码的颜色关键词（red/orange/yellow/blue）。

    与图标解析共用同一套颜色映射（_P_COLOR_DIGIT_TO_SUFFIX），
    并感知 _P_CODE_SKIP_GENERIC 特殊短码列表，避免本地回退图标
    与官方图标解析逻辑因各自独立维护而产生分歧。

    Args:
        code: CMA p 编码，如 "p0002003"。

    Returns:
        颜色关键词（"red"/"orange"/"yellow"/"blue"），
        非法编码或命中特殊短码时返回 None。
    """
    code = (code or "").strip()
    if not _is_p_code(code):
        return None
    # 特殊短码颜色与通用规则不符，交给调用方走标题兜底
    if code in _P_CODE_SKIP_GENERIC:
        return None
    color_digit = code[-1]
    return _P_COLOR_DIGIT_TO_SUFFIX.get(color_digit)


def _resolve_from_title(title: str, headline: str) -> str | None:
    """从标题文本中提取灾害类型和颜色，组合成 11B 完整码。"""
    combined = f"{title or ''} {headline or ''}".strip()
    if not combined:
        return None

    # 提取灾害类型
    base_11b = None
    for keyword, code in _TITLE_TYPE_TO_11B_BASE:
        if keyword in combined:
            base_11b = code
            break

    if not base_11b:
        return None

    # 提取颜色
    color_suffix = None
    for keyword, suffix in _TITLE_COLOR_TO_SUFFIX:
        if keyword in combined:
            color_suffix = suffix
            break

    if not color_suffix:
        return None

    return f"{base_11b}_{color_suffix}"


def build_weather_icon_url(icon_code: str) -> str:
    """构建 Fan Studio 官方图标接口 URL。"""
    return f"https://api.fanstudio.tech/we/img/alarm_icon.php?type={icon_code}"
