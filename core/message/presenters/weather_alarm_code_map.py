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
# ⚠️ 0016~0044 段为插件私有扩展码（11B73~11B99），并非任何官方气象预警编码：
# 按官方 p 码类型号正序递增分配，仅为本插件内部约定，泛用性无法保证。
# 若官方未来分配正式码段，仅需替换映射值与本地图标文件名即可平滑升级。
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
    # --- 插件私有扩展段 ---
    "0016": "11B73",  # 雪灾
    "0017": "11B74",  # 严寒
    "0018": "11B75",  # 低温
    "0019": "11B76",  # 低温冻害
    "0020": "11B77",  # 内涝
    "0021": "11B78",  # 地质灾害
    "0022": "11B79",  # 大雪
    "0023": "11B80",  # 寒冷
    "0024": "11B81",  # 山洪灾害
    "0025": "11B82",  # 干热风
    "0026": "11B83",  # 强对流
    "0027": "11B84",  # 强降温
    "0028": "11B85",  # 强降雨
    "0029": "11B86",  # 持续低温
    "0030": "11B87",  # 森林火险
    "0031": "11B88",  # 沙尘
    "0032": "11B89",  # 泥石流
    "0033": "11B90",  # 海上大雾
    "0034": "11B91",  # 海上大风
    "0035": "11B92",  # 滑坡
    "0036": "11B93",  # 空气污染
    "0037": "11B94",  # 草原火险
    "0038": "11B95",  # 道路结雪
    "0039": "11B96",  # 重污染
    "0040": "11B97",  # 降温
    "0041": "11B98",  # 雷暴
    "0042": "11B99",  # 雷暴大风
    # 官方 0043/0044 与 0039/0030 重复，复用相同私有码
    "0043": "11B96",  # 重污染（与 0039 重复）
    "0044": "11B87",  # 森林火险（与 0030 重复）
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
# （标题兜底现已覆盖强对流/雷暴大风/地质灾害/山洪灾害 → 11B83/11B99/11B78/11B81）
_P_CODE_SKIP_GENERIC: frozenset[str] = frozenset(
    {
        "p0000001",  # 大雾/海区大雾 红色 — 通用规则会误判
        "p0000003",  # 道路冰雪/强对流/雷暴大风/地质灾害/山洪灾害 — 通用规则会误判为高温
        "p0000004",  # 道路冰雪/雷暴大风/山洪灾害 — 通用规则会误判为高温
    }
)

# ---------------------------------------------------------------------------
# 标题兜底：从标题文本提取灾害类型 → 11B 基础码
# 仅保留有明确 11B 图标对应的灾害类型，按关键词长度倒序排列。
# ---------------------------------------------------------------------------

_TITLE_TYPE_TO_11B_BASE: list[tuple[str, str]] = [
    # ------------------------------------------------------------------
    # ⚠️ 插件私有扩展码（11B73~11B99）：
    # 0016~0044 段类型在 Fan Studio 无对应 11B 码；其 p 码虽有官方定义，
    # 但部分类型（如强对流/雷暴大风/地质灾害/山洪灾害）在数据源中实际只出现
    # 短 p 码（p0000001~p0000004，仅编码颜色无法区分类型），因此这里通过
    # 标题兜底映射到自研扩展码段。注意：11B73~11B99 并非任何官方气象预警
    # 编码，仅为本插件内部约定，泛用性无法保证；若官方未来分配正式码段，
    # 仅需替换映射值与本地图标文件名即可平滑升级。
    # ------------------------------------------------------------------
    # 复合类型优先（更长的关键词先匹配）
    ("强季风", "11E99"),  # 插件私有扩展码
    ("道路结冰", "11B21"),
    ("道路冰雪", "11B21"),
    ("道路积雪", "11B21"),
    ("道路结雪", "11B95"),  # 插件私有扩展码（p0038）
    ("海上大风", "11B91"),  # 插件私有扩展码（p0034）
    ("海上大雾", "11B90"),  # 插件私有扩展码（p0033）
    ("森林火险", "11B87"),  # 插件私有扩展码（p0030/p0044）
    ("草原火险", "11B94"),  # 插件私有扩展码（p0037）
    ("空气污染", "11B93"),  # 插件私有扩展码（p0036）
    ("重污染天气", "11B96"),  # 插件私有扩展码（p0039/p0043）
    ("重污染", "11B96"),  # 插件私有扩展码（p0039/p0043，标题单独出现"重污染"时）
    ("雷雨大风", "11B20"),
    ("雷暴大风", "11B99"),  # 插件私有扩展码（p0042）
    ("低温冻害", "11B76"),  # 插件私有扩展码（p0019）
    ("持续低温", "11B86"),  # 插件私有扩展码（p0029）
    ("地质灾害", "11B78"),  # 插件私有扩展码（p0021）
    ("山洪灾害", "11B81"),  # 插件私有扩展码（p0024）
    ("泥石流", "11B89"),  # 插件私有扩展码（p0032）
    ("干热风", "11B82"),  # 插件私有扩展码（p0025）
    ("强降雨", "11B85"),  # 插件私有扩展码（p0028）
    ("强降温", "11B84"),  # 插件私有扩展码（p0027）
    ("强对流", "11B83"),  # 插件私有扩展码（p0026）
    ("沙尘暴", "11B07"),
    ("风暴潮", "11E02"),
    ("海浪", "11E06"),
    ("雪灾", "11B73"),  # 插件私有扩展码（p0016）
    ("严寒", "11B74"),  # 插件私有扩展码（p0017）
    ("低温", "11B75"),  # 插件私有扩展码（p0018）
    ("内涝", "11B77"),  # 插件私有扩展码（p0020）
    ("大雪", "11B79"),  # 插件私有扩展码（p0022）
    ("寒冷", "11B80"),  # 插件私有扩展码（p0023）
    ("沙尘", "11B88"),  # 插件私有扩展码（p0031）
    ("滑坡", "11B92"),  # 插件私有扩展码（p0035）
    ("降温", "11B97"),  # 插件私有扩展码（p0040）
    ("雷暴", "11B98"),  # 插件私有扩展码（p0041）
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

# 标题关键词 → 11B 基础码 的字典视图，供编码错标纠偏时
# 判断"标题类型与编码类型是否指向同一图标"，指向同一基础码时结果等价，无需纠偏。
_TITLE_TYPE_TO_11B_BASE_DICT: dict[str, str] = dict(_TITLE_TYPE_TO_11B_BASE)

# 11B 基础码 → 规范类型名。用于"编码类型 vs 标题字面类型"一致性校验（编码错标兜底）
# 二者不一致且不属于同一语义族（同义/父子类型）时，判定上游编码类型位错标，
# 改以标题字面类型为准。只收录有明确 11B 图标的类型，与
# _TITLE_TYPE_TO_11B_BASE / WEATHER_EMOJI_MAP 口径对齐。
_CANONICAL_TYPE_BY_11B_BASE: dict[str, str] = {
    "11B01": "台风",
    "11B03": "暴雨",
    "11B09": "高温",
    "11B05": "寒潮",
    "11B17": "大雾",
    "11B04": "暴雪",
    "11B06": "大风",
    "11B07": "沙尘暴",
    "11B15": "冰雹",
    "11B22": "干旱",
    "11B21": "道路结冰",
    "11B14": "雷电",
    "11B16": "霜冻",
    "11B19": "霾",
    "11B20": "雷雨大风",
    "11E02": "风暴潮",
    "11E06": "海浪",
    "11B73": "雪灾",
    "11B74": "严寒",
    "11B75": "低温",
    "11B76": "低温冻害",
    "11B77": "内涝",
    "11B78": "地质灾害",
    "11B79": "大雪",
    "11B80": "寒冷",
    "11B81": "山洪灾害",
    "11B82": "干热风",
    "11B83": "强对流",
    "11B84": "强降温",
    "11B85": "强降雨",
    "11B86": "持续低温",
    "11B87": "森林火险",
    "11B88": "沙尘",
    "11B89": "泥石流",
    "11B90": "海上大雾",
    "11B91": "海上大风",
    "11B92": "滑坡",
    "11B93": "空气污染",
    "11B94": "草原火险",
    "11B95": "道路结雪",
    "11B96": "重污染",
    "11B97": "降温",
    "11B98": "雷暴",
    "11B99": "雷暴大风",
    "11E99": "强季风",
}

# 标题关键词匹配排除表：当标题命中某关键词时，若同时包含其复合排除词，跳过该关键词。
# 解决"雷暴大风/雷雨大风/海上大风"被"大风"误匹配的问题：
# - "雷暴大风"已通过 _TITLE_TYPE_TO_11B_BASE 映射到私有扩展码 11B99，
#   "海上大风"映射到 11B91，这里仍保留排除项，避免标题命中这些复合类型
#   后被后续"大风"关键词二次匹配。
# - 例如标题"发布雷暴大风黄色预警"命中"雷暴大风"（11B99）后即返回，
#   不会继续落到"大风"（11B06）。
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
    3. 编码错标纠偏：编码类型与标题字面类型"完全不相干且非同义"时，改以标题为准
    4. 标题文本兜底（灾害类型 + 颜色）

    返回 None 表示无法映射，调用方应走本地颜色回退。
    """
    # 先做编码错标纠偏：上游类型位错标时，
    # 编码类型与标题字面完全不沾边，此时以标题为准重建 11B 码。
    authoritative = resolve_title_authoritative_icon_code(
        weather_type_code, title, headline
    )
    if authoritative is not None:
        return authoritative
    return _resolve_pure_icon_code(weather_type_code, title, headline)


def _resolve_pure_icon_code(
    weather_type_code: str,
    title: str = "",
    headline: str = "",
) -> str | None:
    """纯按编码解析 11B 完整码（不触发标题类型纠偏）。

    解析优先级：
    1. 已有 11B 编码：下划线格式直接使用，紧凑格式标准化后返回
    2. p 编码通用规则（4位类型码 + 末位颜色码），失败时走标题兜底
    3. 标题文本兜底（灾害类型 + 颜色）
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


# 颜色后缀 → 紧凑 11B 末两位颜色码（01=蓝 02=黄 03=橙 04=红）
# 与 _COMPACT_11B_COLOR_TO_SUFFIX 为互逆映射（同源同语义）。
_COLOR_SUFFIX_TO_COMPACT_DIGITS: dict[str, str] = {
    "blue": "01",
    "yellow": "02",
    "orange": "03",
    "red": "04",
}


def suggest_compact_weather_code(title: str = "", headline: str = "") -> str:
    """从预警标题/副标题提取灾害类型与颜色，生成紧凑 11B 编码（如 11B2002）。

    与 _resolve_from_title 共用同一套关键词表与排除规则（同源同语义），
    仅输出形态不同：这里是紧凑编码（11B2002），供模拟表单默认值与
    schema 图标资源命名使用；无法识别类型或颜色时返回空字符串。

    Args:
        title: 预警标题（如 靖远县气象台继续发布雷雨大风黄色预警信号）
        headline: 副标题（可空，用于补充匹配）

    Returns:
        紧凑 11B 编码（如 "11B2002"）；无法识别时返回空字符串。
    """
    code = _resolve_from_title(title, headline)
    if not code:
        return ""
    base, _, color_suffix = code.partition("_")
    digits = _COLOR_SUFFIX_TO_COMPACT_DIGITS.get(color_suffix)
    if not base or not digits:
        return ""
    return f"{base}{digits}"


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


# 同义类型族：族内类型可互相覆盖（不触发纠偏），仅收录「基础码相同、
# 图标等价」的类型组，避免因分类粒度差异误切图标。
# 注意：仅语义相近但图标基础码不同的类型不收录于此
# 父子/包含关系由 is_same_weather_family 的包含判定覆盖。
_SYNONYM_TYPE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"霾", "灰霾"}),  # 同为 11B19
    frozenset({"大雾", "浓雾"}),  # 同为 11B17
)


def is_same_weather_family(a: str, b: str) -> bool:
    """判断两个气象类型名是否属于同一语义族（同义或父子类型）。

    用途：编码错标纠偏时，若编码类型与标题字面类型只是"近亲"
    （如"雷雨大风"与"雷暴大风"、"暴雪"与"大雪"、"海上大雾"与"大雾"），
    视为语义一致，保留原编码，避免因分类粒度差异误切图标。

    Args:
        a: 类型名（如编码解析出的规范类型名）。
        b: 类型名（如标题命中的类型关键词）。

    Returns:
        属于同一语义族返回 True，否则 False。
    """
    one = str(a or "").strip()
    other = str(b or "").strip()
    if not one or not other:
        return False
    if one == other:
        return True
    # 父子/包含关系：如"雷暴大风"与"大风"、"海上大雾"与"大雾"
    if one in other or other in one:
        return True
    # 显式同义词族
    for group in _SYNONYM_TYPE_GROUPS:
        if one in group and other in group:
            return True
    return False


def _resolve_title_keyword(text: str) -> str | None:
    """按 _TITLE_TYPE_TO_11B_BASE 提取文本命中的类型关键词。

    与 _resolve_from_title 共用同一份关键词表与排除规则（同源同语义），
    保证"标题类型判定"与"标题兜底图标"口径一致。

    Args:
        text: 待匹配文本（标题或副标题）。

    Returns:
        命中的类型关键词；无命中返回 None。
    """
    if not text:
        return None
    for keyword, _ in _TITLE_TYPE_TO_11B_BASE:
        if keyword not in text:
            continue
        excluded = _TITLE_KEYWORD_EXCLUSIONS.get(keyword)
        if excluded and any(item in text for item in excluded):
            continue
        return keyword
    return None


def _resolve_title_type_base(text: str) -> str | None:
    """提取文本命中的灾害类型基础码（不含颜色）。

    复用 _resolve_title_keyword 的关键词表与排除规则，只返回类型部分，
    便于「类型取自标题、颜色另取或继承编码」的场景复用。

    Args:
        text: 待匹配文本（标题或副标题）。

    Returns:
        命中的 11B 基础码（如 "11B03"）；无命中返回 None。
    """
    keyword = _resolve_title_keyword(text)
    if not keyword:
        return None
    return _TITLE_TYPE_TO_11B_BASE_DICT.get(keyword)


def _resolve_title_color_suffix(text: str) -> str | None:
    """提取文本中的颜色后缀（不含类型）。

    Args:
        text: 待匹配文本（标题或副标题）。

    Returns:
        颜色后缀（"red"/"orange"/"yellow"/"blue"）；无命中返回 None。
    """
    if not text:
        return None
    for keyword, suffix in _TITLE_COLOR_TO_SUFFIX:
        if keyword in text:
            return suffix
    return None


def _match_title_keyword(title: str, headline: str) -> str | None:
    """按标题优先、副标题兜底的顺序提取类型关键词。

    标题与副标题分别匹配，避免副标题中的类型关键词（如副标题提到"道路结冰"）
    因关键词表顺序而抢占标题中的真实类型（如"暴雨"）。

    Args:
        title: 预警标题。
        headline: 预警副标题。

    Returns:
        命中的类型关键词；均无命中返回 None。
    """
    return _resolve_title_keyword(title) or _resolve_title_keyword(headline)


def _apply_title_type_fallback(
    weather_type_code: str,
    title: str,
    headline: str,
) -> str | None:
    """按标题字面类型重建 11B 完整码，并继承编码自身颜色。

    颜色优先从标题/副标题提取；标题未给出颜色词时，沿用原编码解析出的颜色，
    确保纠偏只改类型、不丢颜色，使本地图标文件仍能按颜色命中。

    类型与颜色分别解析：_resolve_from_title 在文本缺颜色词时会整体返回
    None，不能用于只取类型的场景，否则「继承编码颜色」分支永远不可达。

    Args:
        weather_type_code: 原始气象预警编码。
        title: 预警标题。
        headline: 预警副标题。

    Returns:
        纠偏后的 11B 完整码；无法从标题识别类型或无法取得颜色时返回 None。
    """
    base_11b = _resolve_title_type_base(title) or _resolve_title_type_base(headline)
    if not base_11b:
        return None

    color_suffix = _resolve_title_color_suffix(title) or _resolve_title_color_suffix(
        headline
    )
    if not color_suffix:
        # 标题未给出颜色词，继承原编码解析出的颜色
        original = _resolve_pure_icon_code(weather_type_code, title, headline)
        if original and "_" in original:
            color_suffix = original.rsplit("_", 1)[-1]
    if not color_suffix:
        return None
    return f"{base_11b}_{color_suffix}"


def resolve_title_authoritative_icon_code(
    weather_type_code: str,
    title: str = "",
    headline: str = "",
) -> str | None:
    """编码错标纠偏：编码与标题类型矛盾时，返回以标题为准的 11B 完整码。

    触发条件（需全部满足）：
    1. 编码能解析出规范类型名（在 _CANONICAL_TYPE_BY_11B_BASE 中）；
    2. 该规范类型名未出现在标题/副标题字面中；
    3. 标题能明确匹配到另一个类型关键词；
    4. 标题类型与编码类型不指向同一 11B 基础码（结果不等价）；
    5. 标题类型与编码类型不属于同一语义族（非同义、非父子）。

    返回 None 表示无需纠偏，调用方沿用编码解析结果（保证不回归）。

    Args:
        weather_type_code: 气象预警编码（p 码 / 11B 码 / 紧凑码）。
        title: 预警标题。
        headline: 预警副标题。

    Returns:
        以标题为准的 11B 完整码；无需纠偏时返回 None。
    """
    code = (weather_type_code or "").strip()
    combined = f"{title or ''} {headline or ''}".strip()
    if not code or not combined:
        return None

    # 1. 编码解析出的规范类型名
    pure = _resolve_pure_icon_code(code, title, headline)
    if not pure:
        return None
    canonical = _CANONICAL_TYPE_BY_11B_BASE.get(pure.split("_", 1)[0])
    if not canonical:
        return None

    # 2. 编码类型必须完全未出现在标题字面中，否则视为一致，不纠偏
    if canonical in combined:
        return None

    # 3. 标题能明确匹配到另一个类型关键词（标题优先，副标题仅兜底）
    title_keyword = _match_title_keyword(title, headline)
    if not title_keyword:
        return None

    # 4. 标题类型与编码类型若指向同一 11B 基础码，结果等价，无需纠偏
    #    （如"道路冰雪"与"道路结冰"同为 11B21，改与不改图标一致）
    title_base = _TITLE_TYPE_TO_11B_BASE_DICT.get(title_keyword)
    if title_base == pure.split("_", 1)[0]:
        return None

    # 5. 非同义/非父子关系才纠偏（父子关系如"雷暴大风"与"大风"保留编码）
    if is_same_weather_family(canonical, title_keyword):
        return None

    return _apply_title_type_fallback(code, title, headline)
