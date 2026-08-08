"""
气象展示常量。

该模块集中维护气象预警文本展示中会用到的天气类型图标、颜色等级图标和默认描述长度，
避免展示器内部散落硬编码常量。
"""

from __future__ import annotations

WEATHER_EMOJI_MAP = {
    # 一、国家级标准预警（14类）
    "台风": "🌀",
    "暴雨": "⛈️",
    "强对流": "⛈️⚡",
    "暴雪": "❄️",
    "寒潮": "🥶",
    "大风": "🍃",
    "沙尘暴": "🏜️🌪️",
    "低温": "🌡️📉",
    "高温": "🌡️🔥",
    "干旱": "☀️🌵",
    "霜冻": "❄️🌡️",
    "冰冻": "🧊",
    "大雾": "🌫️",
    "霾": "🌫️😷",
    # 二、地方特色及专项预警
    # 海洋气象预警
    "海上大风": "🌊🍃",
    "海区大风": "🌊🍃",
    "海上台风": "🌊🌀",
    "海上大雾": "🌊🌫️",
    "海上雷雨大风": "🌊⛈️🍃",
    "海上雷电": "🌊⚡",
    "风暴潮": "🌊⬆️",
    "海浪": "🌊",
    "海啸": "🌊⚠️",
    "强季风": "🌬️🍃",
    # 地域性天气预警
    "道路冰雪": "🛣️🧊",
    "雪灾": "❄️⚠️",
    "大雪": "🌨️",
    "持续低温": "🌡️📉⏳",
    "严寒": "🥶",
    "低温冻害": "🥶🌱",
    "低温雨雪冰冻": "🌨️🧊",
    # 环境与火险预警
    "森林（草原）火险": "🌲🔥",
    "森林火险": "🌲🔥",
    "草原火险": "🌱🔥",
    "空气重污染": "🏭😷",
    "臭氧": "🧪",
    "浓浮尘": "🏜️🌫️",
    "沙尘": "🏜️💨",
    "重污染天气": "🌫️😷",
    # 强对流细分预警
    "雷电": "⚡",
    "雷暴大风": "⛈️🍃",
    "雷雨大风": "⛈️🍃",
    "雷雨强风": "⛈️🍃",
    "短时强降水": "🌧️🚤",
    "强降雨": "🌧️",
    "龙卷风": "🌪️",
    "冰雹": "🌨️🧊",
    # 能见度类细分预警
    "轻雾": "🌫️",
    "重雾": "🌫️🌫️",
    "浓雾": "🌫️🌫️🌫️",
    "特强浓雾": "🌫️🌫️⚠️",
    # 温度类补充预警
    "寒冷": "🧥",
    "低温冷害": "🌡️📉🍂",
    "高温中暑": "☀️🤢",
    "干热风": "🔥🍃",
    "强降温": "📉🥶",
    # 城市与环境专项
    "灰霾": "🌫️",
    "臭氧污染": "🧪⚠️",
    "光化学烟雾": "🌫️🧪",
    # 农业气象预警
    "农业干旱": "🚜🌵",
    "农田渍涝": "🚜🌊",
    "作物霜冻": "🌱❄️",
    "倒春寒": "🌱🥶",
    "寒露风": "🍂🍃",
    "农业气象": "🚜🌾",
    # 水文与地质灾害预警
    "中小河流洪水": "🌊🏘️",
    "洪涝灾害": "🌊🏠",
    "渍涝": "🌊💧",
    "山洪灾害": "⛰️🌊",
    "地质灾害": "⛰️⚠️",
    # 交通气象预警
    "道路结冰": "🛣️🧊",
    "道路积雪": "🛣️❄️",
    "路面高温": "🛣️🔥",
    "航道结冰": "🚢🧊",
    # 特殊天气预警
    "飑线": "🌩️💨",
    "尘卷风": "🌪️",
    # 城市定制预警
    "城市内涝": "🏙️🌊",
    "内涝": "🏘️🌊",
    "城市暴雨积涝": "🏙️🌧️💧",
    "建筑工地": "🏗️⚠️",
    "旅游景区": "🏕️⚠️",
    # 健康与卫生气象预警
    "中暑": "🥵",
    # 通用兜底预警
    "其它气象灾害": "🌦️⚠️",
    # 科研与作业预警
    "人工影响天气": "🚀☁️",
    "飞机积冰": "✈️🧊",
}

# 按名称长度倒序匹配，避免“暴雨”先于“短时强降水”等更具体类型误命中。
SORTED_WEATHER_TYPES = sorted(WEATHER_EMOJI_MAP.keys(), key=len, reverse=True)

# 11B/11E 基础码 → Emoji 精确反查表。
# 与 weather_alarm_code_map 的编码体系对齐（p 码/紧凑码会先归一化为 11B 完整码），
# 用于按 weather_type_code 精确匹配，避免纯文本匹配被标题/正文中的歧义字眼带偏。
# 这里只收录有明确 11B 图标对应的类型；无对应码的类型回退到文本匹配。
_WEATHER_11B_BASE_TO_EMOJI: dict[str, str] = {
    "11B01": "🌀",  # 台风
    "11B03": "⛈️",  # 暴雨
    "11B09": "🌡️🔥",  # 高温
    "11B05": "🥶",  # 寒潮
    "11B17": "🌫️",  # 大雾
    "11B04": "❄️",  # 暴雪
    "11B06": "🍃",  # 大风
    "11B07": "🏜️🌪️",  # 沙尘暴
    "11B15": "🌨️🧊",  # 冰雹
    "11B22": "☀️🌵",  # 干旱
    "11B21": "🛣️🧊",  # 道路结冰
    "11B14": "⚡",  # 雷电
    "11B16": "❄️🌡️",  # 霜冻
    "11B19": "🌫️😷",  # 霾
    "11B20": "⛈️🍃",  # 雷雨大风
    "11E02": "🌊⬆️",  # 风暴潮
    "11E06": "🌊",  # 海浪
}

# 颜色等级图标映射，用于在标题后补充直观颜色提示。
COLOR_LEVEL_EMOJI = {
    "红色": "🔴",
    "橙色": "🟠",
    "黄色": "🟡",
    "蓝色": "🔵",
    "白色": "⚪",
}

# 默认描述最大长度，避免长篇气象正文把消息刷得过长。
DEFAULT_MAX_DESCRIPTION_LENGTH = 384

# 升级/降级等变更关键词，用于识别预警变更后的最终颜色
_COLOR_CHANGE_KEYWORDS = ("升级为", "降级为", "变更为")


def extract_final_weather_color(*texts: str) -> str | None:
    """从文本中提取预警最终颜色，正确处理升级/降级场景。

    优先从包含"升级为/降级为"的文本中提取变更关键词之后的颜色；
    若无变更场景，则按红色→白色优先级返回首个匹配的颜色。

    Args:
        texts: 待检索的文本（level、title、headline 等），按优先级传入。

    Returns:
        颜色名称（如"红色"），无匹配时返回 None。
    """
    # 第一轮：优先处理包含变更关键词的文本，取变更后的最终颜色
    for text in texts:
        if not text:
            continue
        text = str(text)
        for keyword in _COLOR_CHANGE_KEYWORDS:
            idx = text.find(keyword)
            if idx >= 0:
                tail = text[idx + len(keyword) :]
                for color in COLOR_LEVEL_EMOJI:
                    if color in tail:
                        return color
    # 第二轮：无变更场景，按红色优先返回首个匹配的颜色
    for text in texts:
        if not text:
            continue
        text = str(text)
        for color in COLOR_LEVEL_EMOJI:
            if color in text:
                return color
    return None


def _resolve_emoji_from_code(texts) -> str | None:
    """从编码类文本（p 码 / 11B/11E 码）精确反查 Emoji。

    与 weather_alarm_code_map.resolve_weather_icon_code 共用编码归一化逻辑，
    把 p 码/紧凑码统一转换为 11B 完整码后按基础码反查，命中即返回。

    Args:
        texts: 待匹配文本序列。

    Returns:
        精确匹配到的 Emoji；无编码或未命中返回 None。
    """
    from .weather_alarm_code_map import resolve_weather_icon_code

    for item in texts:
        text = str(item or "").strip()
        if not text:
            continue
        # 仅处理编码形态，避免把普通中文标题误判为编码
        if not (
            text.startswith("p") or text.startswith("11B") or text.startswith("11E")
        ):
            continue
        icon_code = resolve_weather_icon_code(text)
        if not icon_code:
            continue
        base = icon_code.split("_", 1)[0]
        emoji = _WEATHER_11B_BASE_TO_EMOJI.get(base)
        if emoji:
            return emoji
    return None


def resolve_weather_emoji(*texts: str, default: str = "⛈️") -> str:
    """解析气象类型并返回对应 Emoji。

    匹配优先级：
    1. 编码精确反查：文本中含 p 码 / 11B 码时，经 weather_alarm_code_map
       归一化为 11B 基础码后精确匹配，杜绝文本歧义（如"大雾"正文含"暴雨"字样）。
    2. 文本兜底：复用 WEATHER_EMOJI_MAP / SORTED_WEATHER_TYPES 按名称长度
       倒序匹配标题/副标题等文本。

    供推送展示器、管理端事件摘要与 WebSocket 实时广播共用，
    避免各模块各自维护一套 Emoji 映射导致展示口径漂移。

    Args:
        texts: 待匹配的文本（标题、副标题、描述、类型编码等），可混入编码。
        default: 无任何类型命中时的兜底 Emoji。

    Returns:
        匹配到的气象类型 Emoji；无匹配时返回 default。
    """
    code_emoji = _resolve_emoji_from_code(texts)
    if code_emoji:
        return code_emoji

    match_text = " ".join(str(item).strip() for item in texts if str(item).strip())
    if not match_text:
        return default
    for name in SORTED_WEATHER_TYPES:
        if name in match_text:
            return WEATHER_EMOJI_MAP[name]
    return default
