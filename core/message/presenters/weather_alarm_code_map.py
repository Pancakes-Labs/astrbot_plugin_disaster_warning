"""
气象预警编码映射表。

负责把 CMA 预警地图 API 的 p 编码（如 p0002003）转换为
Fan Studio 图标接口兼容的 11B 编码（如 11B03_yellow），
并提供标题兜底映射能力。

设计原则：有什么图标用什么，不强行映射。
匹配不到的返回 None，由调用方走本地颜色回退。

图标路径策略（本地优先）：
1. 优先使用本地 resources/weatheralarm_logo 目录下的图标文件；
2. 本地文件缺失时再回退到 Fan Studio 官方图标接口。
"""

from __future__ import annotations

import os

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

# 紧凑 11B 编码末两位颜色码 → 颜色后缀（01=蓝 02=黄 03=橙 04=红）
# 与事件 ID 尾部紧凑编码及 message_build_service._COMPACT_11B_COLOR_MAP 保持一致，
# 用于把 11B2002 这类紧凑编码标准化为 11B20_yellow，便于命中本地图标文件。
_COMPACT_11B_COLOR_TO_SUFFIX: dict[str, str] = {
    "01": "blue",
    "02": "yellow",
    "03": "orange",
    "04": "red",
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

# 标题关键词匹配排除表：当标题命中某关键词时，若同时包含其复合排除词，跳过该关键词。
# 解决"雷暴大风"被"大风"误匹配的问题：雷暴大风无专属图标，应走通用颜色 fallback。
# 例如标题"发布雷暴大风黄色预警"会命中"大风"，但含"雷暴"前缀，应跳过"大风"匹配。
_TITLE_KEYWORD_EXCLUSIONS: dict[str, tuple[str, ...]] = {
    # "大风"不应命中"雷暴大风/雷雨大风/雷雨强风/海上大风/海区大风"等复合类型
    "大风": ("雷暴大风", "雷雨大风", "雷雨强风", "海上大风", "海区大风"),
    # "雷电"不应命中"海上雷电"
    "雷电": ("海上雷电",),
    # "大雾"不应命中"海上大雾/特强浓雾"
    "大雾": ("海上大雾",),
}

# 标题颜色关键词 → 颜色后缀
_TITLE_COLOR_TO_SUFFIX: list[tuple[str, str]] = [
    ("红色", "red"),
    ("橙色", "orange"),
    ("黄色", "yellow"),
    ("蓝色", "blue"),
]


def _is_p_code(code: str) -> bool:
    """判断是否为 CMA p 编码格式。"""
    return code.startswith("p") and len(code) >= 7 and code[1:].isdigit()


def _is_11b_code(code: str) -> bool:
    """判断是否为 Fan Studio 11B/11E 编码格式。"""
    return code.startswith("11B") or code.startswith("11E")


def _normalize_compact_11b_code(code: str) -> str | None:
    """把紧凑 11B 编码标准化为下划线颜色格式。

    紧凑格式形如 11B2001（末两位 01/02/03/04 表示蓝/黄/橙/红），
    标准化后为 11B20_blue，便于命中本地图标文件（11B20_blue.png）
    及向 Fan Studio 图标接口传递正确编码。

    仅接受 7 位紧凑格式（11B + 2 位类型码 + 2 位颜色码），
    避免传统完整码（如 11B01）被误拆成 base=11B + 颜色码=01。

    Args:
        code: 紧凑 11B 编码，如 "11B2001"。

    Returns:
        标准化后的 11B 完整码（如 "11B20_blue"）；非紧凑格式返回 None。
    """
    # 长度校验：仅接受 7 位紧凑格式（11Bxxyy，如 11B2001），
    # 排除 11B01 这类无下划线的传统短码，避免 base 被误拆为 "11B"。
    if not (
        code and len(code) == 7 and code[:3] in ("11B", "11E") and code[3:].isdigit()
    ):
        return None
    base = code[:-2]  # 去掉末两位颜色码，如 11B2001 → 11B20
    color_digits = code[-2:]
    color_suffix = _COMPACT_11B_COLOR_TO_SUFFIX.get(color_digits)
    if not color_suffix:
        return None
    return f"{base}_{color_suffix}"


def resolve_weather_icon_code(
    weather_type_code: str,
    *,
    title: str = "",
    headline: str = "",
) -> str | None:
    """把气象预警编码解析为 Fan Studio 图标接口兼容的 11B 完整码。

    解析优先级：
    1. 已有 11B 编码：下划线格式（11B20_yellow）直接使用，
       紧凑格式（11B2001）标准化为 11B20_blue 后返回
    2. p 编码通用规则（4位类型码 + 末位颜色码）
    3. 标题文本兜底（灾害类型 + 颜色）

    返回 None 表示无法映射，调用方应走本地颜色回退。
    """
    code = (weather_type_code or "").strip()

    # 1. 已有 11B 编码：下划线格式直接返回，紧凑格式标准化后返回
    if code and _is_11b_code(code):
        if "_" in code:
            return code
        normalized = _normalize_compact_11b_code(code)
        if normalized:
            return normalized
        # 紧凑格式颜色码无法识别（如 11B20 无颜色），原样返回交上游兜底
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
        if keyword not in combined:
            continue
        # 排除规则：命中关键词但标题同时包含其复合排除词时跳过。
        # 例如"雷暴大风黄色预警"命中"大风"，但含"雷暴"前缀，应跳过"大风"匹配，
        # 让其走通用颜色 fallback（无专属图标不强行归类）。
        excluded_keywords = _TITLE_KEYWORD_EXCLUSIONS.get(keyword)
        if excluded_keywords and any(excl in combined for excl in excluded_keywords):
            continue
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


# ---------------------------------------------------------------------------
# 本地图标目录解析：将 11B 完整码映射为 resources/weatheralarm_logo 下的文件。
# ---------------------------------------------------------------------------

# 插件根目录（weather_alarm_code_map.py 位于 core/message/presenters/ 下，向上 4 层）
_PLUGIN_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
# 本地气象预警图标目录
_WEATHER_LOGO_DIR = os.path.join(_PLUGIN_ROOT, "resources", "weatheralarm_logo")

# 本地图标文件名缓存：code -> (路径, 是否存在)，避免重复 stat
_LOCAL_ICON_CACHE: dict[str, tuple[str, bool]] = {}


def _resolve_local_icon_file(icon_code: str) -> tuple[str, bool]:
    """解析本地图标文件路径，并缓存其是否存在。

    文件名规则：11B 完整码直接作为文件名前缀（如 11B03_yellow.png、11E02_red.png）。
    """
    code = (icon_code or "").strip()
    if not code:
        return "", False

    if code in _LOCAL_ICON_CACHE:
        return _LOCAL_ICON_CACHE[code]

    # 文件名直接使用 11B 完整码 + .png（如 11B03_yellow.png、11E02_red.png）
    filename = f"{code}.png"
    path = os.path.join(_WEATHER_LOGO_DIR, filename)
    exists = os.path.isfile(path)
    _LOCAL_ICON_CACHE[code] = (path, exists)
    return path, exists


def resolve_local_weather_icon_abs_path(icon_code: str) -> str | None:
    """返回本地气象预警图标的绝对路径。

    供推送侧直接读取本地文件转 Base64 使用（推送进程不一定能访问管理端
    静态路由 /weatheralarm_logo/，因此不能依赖本地 URL 下载）。

    Args:
        icon_code: 11B 完整码，如 "11B03_yellow"。

    Returns:
        本地图标绝对路径；文件不存在时返回 None。
    """
    path, exists = _resolve_local_icon_file(icon_code)
    return path if exists else None


def build_local_weather_icon_url(icon_code: str) -> str | None:
    """构建本地气象预警图标的 URL。

    本地图标通过管理端静态路由 /weatheralarm_logo/ 对外提供访问，
    仅当对应文件存在时返回 URL，否则返回 None 交由调用方回退。

    Args:
        icon_code: 11B 完整码，如 "11B03_yellow"。

    Returns:
        本地图标 URL（如 /weatheralarm_logo/11B03_yellow.png），
        文件不存在时返回 None。
    """
    path, exists = _resolve_local_icon_file(icon_code)
    if not exists:
        return None
    return f"/weatheralarm_logo/{os.path.basename(path)}"


def resolve_icon_color_suffix(icon_code: str) -> str | None:
    """从 11B 完整码/紧凑码/p 编码中解析颜色后缀。

    与 resolve_weather_icon_code 共用同一套颜色映射，避免本地回退图标
    与官方图标解析逻辑因各自独立维护而产生分歧。

    Args:
        icon_code: 气象预警编码，如 "11B03_yellow" / "11B2001" / "p0002003"。

    Returns:
        颜色后缀（"red"/"orange"/"yellow"/"blue"），无法解析返回 None。
    """
    code = (icon_code or "").strip()
    if not code:
        return None

    # 1. 下划线完整码（11B03_yellow）：下划线后即颜色
    if "_" in code:
        color = code.split("_")[-1].strip().lower()
        if color in {"red", "orange", "yellow", "blue"}:
            return color

    # 2. 紧凑 11B 码（11B2001）：末两位颜色码
    compact = _normalize_compact_11b_code(code)
    if compact and "_" in compact:
        return compact.split("_")[-1]

    # 3. p 编码：末位颜色数字
    if _is_p_code(code) and code not in _P_CODE_SKIP_GENERIC:
        return _P_COLOR_DIGIT_TO_SUFFIX.get(code[-1])

    return None


def build_local_weather_fallback_url(icon_code: str) -> str | None:
    """构建本地通用颜色回退图标 URL。

    当本地缺少具体 11B 图标文件时，按编码解析出的颜色后缀回退到
    /weatheralarm_logo/fallback_{color}.png（如 fallback_red.png）。

    Args:
        icon_code: 气象预警编码（11B 完整码 / 紧凑码 / p 码均可）。

    Returns:
        本地回退图标 URL（如 /weatheralarm_logo/fallback_red.png）；
        颜色无法解析或文件不存在时返回 None。
    """
    color = resolve_icon_color_suffix(icon_code)
    if not color:
        return None
    path, exists = _resolve_local_icon_file(f"fallback_{color}")
    if not exists:
        return None
    return f"/weatheralarm_logo/{os.path.basename(path)}"


def build_weather_icon_url(icon_code: str) -> str | None:
    """构建气象预警图标 URL（本地优先，缺失时回退 Fan Studio 官方接口）。

    图标使用策略：
    1. 本地 resources/weatheralarm_logo 目录存在对应文件 → 返回本地静态 URL；
    2. 本地文件缺失 → 回退本地通用颜色图标 /weatheralarm_logo/fallback_{color}.png；
    3. 颜色也无法解析 → 返回 Fan Studio 官方图标接口 URL 兜底。

    优先返回本地静态资源可避免远程接口返回“伪图片”（HTTP 200 的 HTML）
    导致浏览器无法触发 img onError 而显示破图的问题。

    Args:
        icon_code: 气象预警编码（11B 完整码 / 紧凑码 / p 码），可为空。

    Returns:
        图标 URL；编码为空时返回 None（调用方应自行决定是否展示图标）。
    """
    code = (icon_code or "").strip()
    if not code:
        return None

    local_url = build_local_weather_icon_url(code)
    if local_url:
        return local_url

    fallback_url = build_local_weather_fallback_url(code)
    if fallback_url:
        return fallback_url

    return f"https://api.fanstudio.tech/we/img/alarm_icon.php?type={code}"
