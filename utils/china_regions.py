"""
中国省级行政区划公共常量与解析工具。

集中维护全国 34 个省级行政区的：
- 简称列表（CHINA_PROVINCES）
- 全称 <-> 简称映射（PROVINCE_FULL_TO_SHORT / PROVINCE_SHORT_TO_FULL）
- 「省+地名」前缀剥离与省份关键词解析

供气象站查询、AQI 查询、气象地区解析、统计事件等模块复用，
避免多处维护重复的省份集合导致遗漏/漂移。
"""

from __future__ import annotations

import re

# 中国 34 个省级行政区简称（含港澳台），顺序参考常见行政区划排布。
CHINA_PROVINCES: list[str] = [
    "北京",
    "天津",
    "上海",
    "重庆",
    "河北",
    "山西",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "海南",
    "四川",
    "贵州",
    "云南",
    "陕西",
    "甘肃",
    "青海",
    "台湾",
    "内蒙古",
    "广西",
    "西藏",
    "宁夏",
    "新疆",
    "香港",
    "澳门",
]

# 省级行政区全称 -> 简称（用于「省份+城市」展示，避免过长）。
PROVINCE_FULL_TO_SHORT: dict[str, str] = {
    "北京市": "北京",
    "天津市": "天津",
    "上海市": "上海",
    "重庆市": "重庆",
    "河北省": "河北",
    "山西省": "山西",
    "内蒙古自治区": "内蒙古",
    "辽宁省": "辽宁",
    "吉林省": "吉林",
    "黑龙江省": "黑龙江",
    "江苏省": "江苏",
    "浙江省": "浙江",
    "安徽省": "安徽",
    "福建省": "福建",
    "江西省": "江西",
    "山东省": "山东",
    "河南省": "河南",
    "湖北省": "湖北",
    "湖南省": "湖南",
    "广东省": "广东",
    "广西壮族自治区": "广西",
    "海南省": "海南",
    "四川省": "四川",
    "贵州省": "贵州",
    "云南省": "云南",
    "西藏自治区": "西藏",
    "陕西省": "陕西",
    "甘肃省": "甘肃",
    "青海省": "青海",
    "宁夏回族自治区": "宁夏",
    "新疆维吾尔自治区": "新疆",
    "香港特别行政区": "香港",
    "澳门特别行政区": "澳门",
    "台湾省": "台湾",
}

# 省级行政区简称 -> 全称（用于把用户关键词解析为官方全称）。
PROVINCE_SHORT_TO_FULL: dict[str, str] = {
    short: full for full, short in PROVINCE_FULL_TO_SHORT.items()
}

# 常见省名（先全称后简称，用于「省+地名」前缀剥离）。
# 注意顺序：全称在前，避免「北京」抢先命中「北京市」导致剥离出错。
COMMON_PROVINCES: list[str] = [
    *PROVINCE_FULL_TO_SHORT.keys(),
    *PROVINCE_SHORT_TO_FULL.keys(),
]


def province_short(province: str) -> str:
    """把省级行政区全称转为简称（如「广东省」->「广东」）。

    Args:
        province: 省级行政区名称（全称或简称）。

    Returns:
        简称；未知时原样返回；空串返回空串。
    """
    name = str(province or "").strip()
    if not name:
        return ""
    return PROVINCE_FULL_TO_SHORT.get(name, name)


def resolve_province_full(keyword: str) -> str | None:
    """把用户省份关键词解析为「省/市/自治区」全称。

    Args:
        keyword: 用户输入，如「广东」「广东省」「新疆」「内蒙古」。

    Returns:
        省份全称（如「广东省」「新疆维吾尔自治区」）；无法识别返回 None。
    """
    k = str(keyword or "").strip().replace(" ", "")
    if not k:
        return None
    k = k.removesuffix("省").removesuffix("市").strip()
    if not k:
        return None
    # 先精确匹配简称表
    if k in PROVINCE_SHORT_TO_FULL:
        return PROVINCE_SHORT_TO_FULL[k]
    # 直接是全称（如「广东省」）
    if k in PROVINCE_FULL_TO_SHORT:
        return k
    # 长简称（3 字及以上，如「内蒙古」「黑龙江」）允许包含匹配；
    # 2 字简称只做精确匹配（上面已处理），避免「海南州」这类
    # 含省份简称子串的城市名被误判为省份。
    for short, full in PROVINCE_SHORT_TO_FULL.items():
        if len(short) >= 3 and short in k:
            return full
    return None


def strip_province_prefix(raw: str) -> tuple[str | None, str]:
    """从「省+地名」中剥离省份前缀。

    仅当 raw 比省名更长（确实带了地名后缀）才剥离；
    若 raw 恰好等于省名（如「上海」「北京」等直辖市名即站名），
    整串原样返回，避免剥成空串。

    Args:
        raw: 用户输入的原始地名（如「广东怀集」「北京」）。

    Returns:
        (province_hint, rest)：
        - province_hint: 命中的省份（简称），未命中为 None。
        - rest: 剥离前缀后的地名；未剥离时为原串。
    """
    s = str(raw or "").strip()
    if not s:
        return None, s
    for pname in COMMON_PROVINCES:
        if s.startswith(pname) and len(s) > len(pname):
            return pname, s[len(pname) :]
    return None, s


# GB/T 2260 省级行政区划代码前缀（前2位） -> 省份简称
ADCODE_PROVINCE_MAP: dict[str, str] = {
    "11": "北京",
    "12": "天津",
    "13": "河北",
    "14": "山西",
    "15": "内蒙古",
    "21": "辽宁",
    "22": "吉林",
    "23": "黑龙江",
    "31": "上海",
    "32": "江苏",
    "33": "浙江",
    "34": "安徽",
    "35": "福建",
    "36": "江西",
    "37": "山东",
    "41": "河南",
    "42": "湖北",
    "43": "湖南",
    "44": "广东",
    "45": "广西",
    "46": "海南",
    "50": "重庆",
    "51": "四川",
    "52": "贵州",
    "53": "云南",
    "54": "西藏",
    "61": "陕西",
    "62": "甘肃",
    "63": "青海",
    "64": "宁夏",
    "65": "新疆",
    "71": "台湾",
    "81": "香港",
    "82": "澳门",
    "00": "全国",
}


def extract_province_from_adcode(adcode_or_id: str | int | None) -> str | None:
    """从行政区划代码（adcode）或预警 ID 中提取省级简称。

    支持纯 adcode（如 "320312"）、带后缀 ID（如 "32031241600000_2026..."、
    "620421-2026..."）等各类格式。

    Args:
        adcode_or_id: 行政区划代码字符串/整数或预警 ID。

    Returns:
        省份简称（如 "江苏", "甘肃", "全国"）；未命中时返回 None。
    """
    if not adcode_or_id:
        return None
    s = str(adcode_or_id).strip()
    if not s:
        return None
    m = re.match(r"^(\d{2})", s)
    if m:
        return ADCODE_PROVINCE_MAP.get(m.group(1))
    return None


# 全国级发布机构关键词
_NATIONWIDE_KEYWORDS = (
    "中央气象台",
    "中国气象局",
    "国家预警信息发布中心",
    "国家气象中心",
    "国家海洋环境预报中心",
)

# 按全称长度降序排序的全称列表，用于优先长串精确匹配
_SORTED_FULL_PROVINCES: list[tuple[str, str]] = sorted(
    PROVINCE_FULL_TO_SHORT.items(),
    key=lambda x: len(x[0]),
    reverse=True,
)


def resolve_province_from_text(text: str) -> str | None:
    """从标题、副标题或正文文本中提取省级行政区简称。

    匹配规则：
    1. 全国级发布主体（如中央气象台）优先返回「全国」；
    2. 省级行政区全称优先匹配（如「江苏省」「内蒙古自治区」），避免同名区县误判；
    3. 省级行政区简称匹配，排除「河北区」「海南区」「海淀区」等带后缀的非省份地名误判；
    4. 最终归一化为省份简称。

    Args:
        text: 标题、发布机构或正文文本。

    Returns:
        省级行政区简称（如 "江苏", "北京", "全国"）；未识别时返回 None。
    """
    s = str(text or "").strip()
    if not s:
        return None

    # 1. 全国级发布机构优先
    for kw in _NATIONWIDE_KEYWORDS:
        if kw in s:
            return "全国"

    # 2. 全称优先匹配（如「江苏省徐州市...」命中「江苏省」->「江苏」）
    for full_name, short_name in _SORTED_FULL_PROVINCES:
        if full_name in s:
            return short_name

    # 3. 常见非省份的歧义地名排除规则
    ambiguous_places = {
        "河北区": "天津",  # 天津市河北区
        "海南区": "内蒙古",  # 内蒙古乌海市海南区
        "海南州": "青海",  # 青海省海南藏族自治州
        "海南藏族自治州": "青海",
        "海北州": "青海",
        "海北藏族自治州": "青海",
        "海西州": "青海",
        "海西蒙古族藏族自治州": "青海",
    }
    for amb_place, target_prov in ambiguous_places.items():
        if amb_place in s:
            return target_prov

    # 4. 简称匹配（长简称优先，避免误判）
    for p in ("内蒙古", "黑龙江"):
        if p in s:
            return p

    for p in CHINA_PROVINCES:
        if p in s:
            return p

    return None


__all__ = [
    "CHINA_PROVINCES",
    "PROVINCE_FULL_TO_SHORT",
    "PROVINCE_SHORT_TO_FULL",
    "COMMON_PROVINCES",
    "ADCODE_PROVINCE_MAP",
    "province_short",
    "resolve_province_full",
    "strip_province_prefix",
    "extract_province_from_adcode",
    "resolve_province_from_text",
]
