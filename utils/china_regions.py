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
    # 简称包含匹配（如「内蒙古」->「内蒙古自治区」）
    for short, full in PROVINCE_SHORT_TO_FULL.items():
        if short in k:
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


__all__ = [
    "CHINA_PROVINCES",
    "PROVINCE_FULL_TO_SHORT",
    "PROVINCE_SHORT_TO_FULL",
    "COMMON_PROVINCES",
    "province_short",
    "resolve_province_full",
    "strip_province_prefix",
]
