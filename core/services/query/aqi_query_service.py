"""
城市空气质量指数（AQI）查询服务。

统一承接命令侧（/空气质量 /空气质量排行 /空气质量列表）的查询编排：
- 城市/省份/全国三种查询模式解析
- 按 CityCode 前缀反推省份（无需维护静态城市表）
- AQI 数值解析与缺测（NA）防御
- 等级圆点视觉指示（仅保留严重程度指示器，不堆叠装饰 emoji）
- 文本格式化：单城市详情 / 省份列表 / 全国概览 / 排行榜 / 城市列表

数据源：Jian Project https://api.sismotide.top/get/aqi.php
响应为 UTF-8（可能带 BOM）的 JSON 数组，一次返回全国城市快照。
"""

from __future__ import annotations

import math
from collections import OrderedDict
from typing import Any

from ....utils.china_regions import (
    province_short,
    resolve_province_full,
    strip_province_prefix,
)
from ....utils.severity_emoji import AQI_LEVEL_DOT, aqi_level_emoji
from ....utils.text_format_utils import format_iso_time
from ...network.http.jian_aqi_client import JianAqiClient

# ---- 省份映射：CityCode 前两位 -> 省份 ----
# 参考国家标准行政区划代码（前两位为省级代码）
CODE2PROV: dict[str, str] = {
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
}

# 默认排行条数
DEFAULT_RANK_LIMIT = 10

# 等级过滤词 -> 对应 AQI 数值区间
_QUALITY_FILTER_RANGES: dict[str, tuple[int, int]] = {
    "优": (0, 51),
    "良": (51, 101),
    "轻度污染": (101, 151),
    "中度污染": (151, 201),
    "重度污染": (201, 301),
    "严重污染": (301, 10**9),
}

# 等级过滤词别名（用户常用简称）-> 标准键
_QUALITY_FILTER_ALIASES: dict[str, str] = {
    "轻度": "轻度污染",
    "中度": "中度污染",
    "重度": "重度污染",
    "严重": "严重污染",
}


def _resolve_quality_filter(fk: str) -> str | None:
    """把等级过滤词解析为标准键；无法识别返回 None。"""
    k = str(fk or "").strip()
    if not k:
        return None
    if k in _QUALITY_FILTER_RANGES:
        return k
    return _QUALITY_FILTER_ALIASES.get(k)


def _filter_by_quality(
    items: list[dict[str, Any]], quality_filter: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """按等级过滤词过滤城市列表（全国/省份概览模式通用）。

    Args:
        items: 待过滤城市列表。
        quality_filter: 等级过滤词（优/良/轻度污染等，支持简称别名）。

    Returns:
        (filtered, error)：过滤后的列表；过滤词无效时 error 非 None。
    """
    if not quality_filter:
        return items, None
    fk = _resolve_quality_filter(quality_filter)
    if fk is None:
        return items, (
            f"无效的等级过滤词「{quality_filter}」，"
            "可用：优/良/轻度(污染)/中度(污染)/重度(污染)/严重(污染)"
        )
    lo, hi = _QUALITY_FILTER_RANGES[fk]
    return [it for it in items if (n := aqi_num(it)) is not None and lo <= n < hi], None


# 城市名后缀（用于去掉后缀做模糊匹配）
_AREA_SUFFIXES = (
    "自治州",
    "自治县",
    "地区",
    "林区",
    "特区",
    "市",
    "县",
    "旗",
    "盟",
    "区",
)


def _safe_int(v: Any, default: int | None = None) -> int | None:
    """安全整数解析：非数字/缺测/无穷大返回 default。"""
    if v is None or v == "":
        return default
    try:
        num = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return int(num)


def code_to_prov(code: Any) -> str:
    """按 CityCode 前两位反推省份；新疆兵团（659xxx）单列；无效代码归类「未知」。"""
    code = _safe_int(code)
    if code is None:
        return "未知"
    s = str(code)
    if s.startswith("659"):
        return "新疆兵团"
    return CODE2PROV.get(s[:2], "未知")


def aqi_num(item: dict[str, Any]) -> int | None:
    """提取 AQI 数值；缺测（NA/空/异常/无穷大）返回 None。"""
    try:
        v = str(item.get("AQI") or "").strip()
        if not v or v.upper() in ("NA", "N/A", "-"):
            return None
        num = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return int(num)


def quality_label(item: dict[str, Any]) -> str:
    """返回空气质量等级描述；缺测返回「数据缺失」。"""
    q = str(item.get("Quality") or "").strip()
    if not q or q.upper() == "NA":
        return "数据缺失"
    return q


def primary_pollutant_label(item: dict[str, Any]) -> str:
    """返回首要污染物展示文本；「—」表示无，返回「无」；缺测返回「-」。"""
    pp = str(item.get("PrimaryPollutant") or "").strip()
    if not pp or pp.upper() == "NA":
        return "-"
    if pp == "—":
        return "无"
    return pp


# 直辖市简称：用户输入省名即城市名，应走单城详情而非「1城」省级摘要。
_MUNICIPALITY_SHORTS = frozenset({"北京", "天津", "上海", "重庆", "香港", "澳门"})


def _conc_text(value: Any) -> str | None:
    """浓度字段展示：缺测/NA/空省略（不补 0）。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.upper() in ("NA", "N/A", "-", "—"):
        return None
    return s


def _format_conc_lines(item: dict[str, Any]) -> list[str]:
    """拼装上游已有浓度字段；缺失字段直接跳过。"""
    hour_specs = (
        ("PM2.5", "PM2_5", "μg/m³"),
        ("PM10", "PM10", "μg/m³"),
        ("SO2", "SO2", "μg/m³"),
        ("NO2", "NO2", "μg/m³"),
        ("CO", "CO", "mg/m³"),
        ("O3", "O3", "μg/m³"),
    )
    hour_parts: list[str] = []
    for label, key, unit in hour_specs:
        v = _conc_text(item.get(key))
        if v is not None:
            hour_parts.append(f"{label} {v} {unit}")

    extra_specs = (
        ("PM2.5(24h)", "PM2_5_24h", "μg/m³"),
        ("PM10(24h)", "PM10_24h", "μg/m³"),
        ("SO2(24h)", "SO2_24h", "μg/m³"),
        ("NO2(24h)", "NO2_24h", "μg/m³"),
        ("CO(24h)", "CO_24h", "mg/m³"),
        ("O3(8h)", "O3_8h", "μg/m³"),
        ("O3(8h,24h)", "O3_8h_24h", "μg/m³"),
        ("O3(24h)", "O3_24h", "μg/m³"),
    )
    extra_parts: list[str] = []
    for label, key, unit in extra_specs:
        v = _conc_text(item.get(key))
        if v is not None:
            extra_parts.append(f"{label} {v} {unit}")

    lines: list[str] = []
    if hour_parts or extra_parts:
        lines.append("浓度：")
        # 每行最多 3 项，避免单行过长
        for parts in (hour_parts, extra_parts):
            for i in range(0, len(parts), 3):
                lines.append(" | ".join(parts[i : i + 3]))
    return lines


def _area_short(area: str) -> str:
    """压缩城市名：去掉「市」后缀（自治州/地区/盟保留全称）。"""
    s = str(area or "").strip()
    if s.endswith("市"):
        return s[:-1]
    return s


def _strip_area_suffix(name: str) -> str:
    """去掉行政区划后缀，便于市/县关键词对齐。"""
    s = str(name or "").strip()
    for suffix in _AREA_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s


def _match_area(item: dict[str, Any], keyword: str) -> bool:
    """匹配城市/县级关键词（去掉后缀模糊匹配）。"""
    return _area_match_score(item, keyword) > 0


def _area_match_score(item: dict[str, Any], keyword: str) -> int:
    """地名匹配分：精确 > 去后缀相等 > 前缀 > 包含；0 表示不匹配。"""
    area = str(item.get("Area") or "").strip()
    kw = str(keyword or "").strip().replace(" ", "")
    if not area or not kw:
        return 0
    area_core = _strip_area_suffix(area)
    kw_core = _strip_area_suffix(kw)
    if area == kw:
        return 100
    if area_core == kw or area == kw_core or area_core == kw_core:
        return 90
    if area.startswith(kw) or area_core.startswith(kw_core):
        return 70
    # 包含匹配：要求关键词足够长，降低「安」这类短串误伤
    if len(kw_core) >= 2 and (kw_core in area or kw_core in area_core):
        return 40
    return 0


def _pick_best_area_matches(
    items: list[dict[str, Any]], keyword: str
) -> list[dict[str, Any]]:
    """按匹配分挑选最佳市/县级结果；同分保留并列。"""
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in items:
        score = _area_match_score(item, keyword)
        if score > 0:
            scored.append((score, item))
    if not scored:
        return []
    best = max(score for score, _ in scored)
    # 仅保留最高分；同名并列时再按 AQI 有效性与名称稳定性排序
    top = [it for score, it in scored if score == best]
    top.sort(
        key=lambda x: (
            0 if aqi_num(x) is not None else 1,
            len(str(x.get("Area") or "")),
            str(x.get("Area") or ""),
        )
    )
    return top


def _is_plain_province_keyword(keyword: str) -> bool:
    """是否为纯省份查询词（不含市/县等后缀）。"""
    k = str(keyword or "").strip().replace(" ", "")
    if not k:
        return False
    province = resolve_province_full(k)
    if not province:
        return False
    short = province_short(province)
    # 显式带市/县/州等后缀时，优先按地名查（如「吉林市」）
    for suffix in _AREA_SUFFIXES:
        if k.endswith(suffix):
            return False
    k_norm = (
        k.removesuffix("特别行政区")
        .removesuffix("维吾尔自治区")
        .removesuffix("壮族自治区")
        .removesuffix("回族自治区")
        .removesuffix("自治区")
        .removesuffix("省")
        .strip()
    )
    return k_norm in {short, k} or k in {short, province}


def build_city_detail(item: dict[str, Any]) -> str:
    """构建单城市 AQI 详情文本。"""
    area = str(item.get("Area") or "未知城市")
    tp = format_iso_time(item.get("TimePoint"))
    aqi = aqi_num(item)
    aqi_text = str(item.get("AQI") or "NA")
    q_label = quality_label(item)
    pp_label = primary_pollutant_label(item)

    lines = [f"{area}空气质量"]
    if tp:
        lines.append(f"更新时间：{tp}")
    lines.append("")
    if aqi is None:
        lines.append(f"⬜ AQI {aqi_text}  {q_label}")
    else:
        level = _safe_int(item.get("AqiLevel"), default=0) or 0
        lines.append(
            f"{aqi_level_emoji(item.get('AQI'))} AQI {aqi_text}  {q_label}（{level}级）"
        )
    lines.append(f"首要污染物：{pp_label}")
    conc_lines = _format_conc_lines(item)
    if conc_lines:
        lines.append("")
        lines.extend(conc_lines)
    lines.append("")
    # 分指数：前缀独立一行，CO/NO2/O3 与 PM10/PM2.5/SO2 各占一行，不与前缀同行
    lines.append("分指数：")
    lines.append(
        f"CO {item.get('COLevel') or '-'} 级 | NO2 {item.get('NO2Level') or '-'} 级 | O3 {item.get('O3Level') or '-'} 级"
    )
    lines.append(
        f"PM10 {item.get('PM10Level') or '-'} 级 | PM2.5 {item.get('PM2_5Level') or '-'} 级 | SO2 {item.get('SO2Level') or '-'} 级"
    )
    unheal = str(item.get("Unheathful") or "").strip()
    measure = str(item.get("Measure") or "").strip()
    if unheal:
        lines.append("")
        lines.append(f"健康影响：{unheal}")
    if measure:
        lines.append(f"建议措施：{measure}")
    return "\n".join(lines)


def build_province_text(
    province_name: str, items: list[dict[str, Any]], time_point: str | None
) -> str:
    """构建某省全部城市 AQI 文本（按 AQI 升序）。"""
    tp = format_iso_time(time_point)
    display = province_short(province_name) or province_name
    lines = [f"{display}空气质量（{len(items)}城）"]
    if tp:
        lines.append(f"数据时间：{tp}")
    lines.append("")
    ordered = sorted(
        items,
        key=lambda x: (
            aqi_num(x) if aqi_num(x) is not None else 10**9,
            str(x.get("Area") or ""),
        ),
    )
    for item in ordered:
        area = _area_short(str(item.get("Area") or "未知"))
        aqi = item.get("AQI") or "NA"
        q = quality_label(item)
        pp = primary_pollutant_label(item)
        suffix = "" if pp in ("无", "-") else f" | {pp}"
        lines.append(f"{aqi_level_emoji(item.get('AQI'))} {area} AQI {aqi} {q}{suffix}")
    return "\n".join(lines)


def build_nationwide_text(
    items: list[dict[str, Any]], time_point: str | None
) -> tuple[str, list[str]]:
    """构建全国 AQI 概览文本（按等级分块，块内按省份分组排序）。

    展示结构：
    - 先按空气质量等级分块（优/良/轻度污染…），每块一段；
    - 等级内部按省份分组，省份按「省内最优 AQI」升序排列，
      每省独占一行（含省份名、最优 AQI、城市数）；
    - 同一省份内的城市按 AQI 升序排列，切行只在省内进行，
      避免省份之间混行。

    Returns:
        (summary_text, blocks)：summary_text 为普通文本；blocks 为合并转发分块。
    """
    tp = format_iso_time(time_point)
    groups: dict[str, list[dict[str, Any]]] = {
        q: []
        for q in [
            "优",
            "良",
            "轻度污染",
            "中度污染",
            "重度污染",
            "严重污染",
            "数据缺失",
        ]
    }
    for item in items:
        q = quality_label(item)
        groups.setdefault(q, []).append(item)

    summary = f"全国空气质量概览\n数据时间：{tp} | 覆盖 {len(items)} 个城市"
    blocks: list[str] = []
    for q in ["优", "良", "轻度污染", "中度污染", "重度污染", "严重污染", "数据缺失"]:
        city_items = groups.get(q)
        if not city_items:
            continue
        # 按等级名映射圆点：复用统一模块区间表（数据缺失固定 ⬜）
        dot = {
            "优": AQI_LEVEL_DOT[0][2],
            "良": AQI_LEVEL_DOT[1][2],
            "轻度污染": AQI_LEVEL_DOT[2][2],
            "中度污染": AQI_LEVEL_DOT[3][2],
            "重度污染": AQI_LEVEL_DOT[4][2],
            "严重污染": AQI_LEVEL_DOT[5][2],
            "数据缺失": "⬜",
        }[q]

        # 等级内按省份聚合
        prov_groups: dict[str, list[dict[str, Any]]] = {}
        for item in city_items:
            prov_groups.setdefault(code_to_prov(item.get("CityCode")), []).append(item)

        # 省份排序：按省内最优 AQI 升序；省内城市按 AQI 升序（缺测排末位）
        def _prov_best_key(pair: tuple[str, list[dict[str, Any]]]) -> tuple[int, str]:
            prov, cities = pair
            best = 10**9
            for city in cities:
                aqi = aqi_num(city)
                if aqi is not None and aqi < best:
                    best = aqi
            return (best, prov)

        def _city_sort_key(item: dict[str, Any]) -> tuple[int, str]:
            aqi = aqi_num(item)
            return (aqi if aqi is not None else 10**9, str(item.get("Area") or ""))

        ordered_provs = sorted(prov_groups.items(), key=_prov_best_key)

        lines = [f"{dot} {q}（{len(city_items)}城）："]
        for prov, cities in ordered_provs:
            valid = [a for a in (aqi_num(c) for c in cities) if a is not None]
            best_text = f"最优 {min(valid)}" if valid else "无有效数据"
            # 省份独占一行标题
            lines.append(f"  【{prov}】{best_text}（{len(cities)}城）：")
            city_texts = [
                f"{_area_short(str(item.get('Area') or '未知'))} {item.get('AQI') or 'NA'}"
                for item in sorted(cities, key=_city_sort_key)
            ]
            # 省内每行最多 6 个城市，切行只在省内进行（不跨省混行）
            for i in range(0, len(city_texts), 6):
                lines.append("    " + "、".join(city_texts[i : i + 6]))
        blocks.append("\n".join(lines))
    return summary, blocks


def _build_rank_block(
    items: list[dict[str, Any]],
    *,
    direction: str,
    time_point: str | None,
    limit: int,
) -> str:
    """构建单个方向（最好/最差）的排行榜文本块。"""
    tp = format_iso_time(time_point)
    title = "空气质量最差" if direction == "worst" else "空气质量最好"
    lines = [f"{title} Top{min(limit, len(items)) or 0}"]
    if tp:
        lines[0] += f"（{tp}）"
    if not items:
        lines.append("暂无有效数据")
        return "\n".join(lines)

    ordered = sorted(
        items,
        key=lambda x: (aqi_num(x), str(x.get("Area") or "")),
        reverse=(direction == "worst"),
    )
    for idx, item in enumerate(ordered[:limit], 1):
        area = _area_short(str(item.get("Area") or "未知"))
        aqi = item.get("AQI") or "NA"
        q = quality_label(item)
        pp = primary_pollutant_label(item)
        suffix = "" if pp in ("无", "-") else f" | {pp}"
        lines.append(
            f"{idx}. {aqi_level_emoji(item.get('AQI'))} {area} AQI {aqi} {q}{suffix}"
        )
    return "\n".join(lines)


def build_rank_text(
    items: list[dict[str, Any]],
    *,
    direction: str | None = None,
    time_point: str | None = None,
    limit: int = DEFAULT_RANK_LIMIT,
) -> tuple[str, list[str]]:
    """构建 AQI 排行榜文本。

    Args:
        items: 全量城市列表（含缺测，会自动剔除缺测再排行）。
        direction: "best" 最好 / "worst" 最差 / None 两者都输出（最好在前）。
        time_point: 数据时间。
        limit: 每个方向最多条数。

    Returns:
        (summary_text, blocks)：
        - direction 指定时返回单块文本；
        - direction 为 None 时返回两块（最好在前）。
    """
    valid = [it for it in items if aqi_num(it) is not None]

    if direction is None:
        best_block = _build_rank_block(
            valid, direction="best", time_point=time_point, limit=limit
        )
        worst_block = _build_rank_block(
            valid, direction="worst", time_point=time_point, limit=limit
        )
        summary = f"空气质量排行（{format_iso_time(time_point)}）"
        return summary, [best_block, worst_block]

    block = _build_rank_block(
        valid, direction=direction, time_point=time_point, limit=limit
    )
    return block, [block]


def build_city_list_text(
    items: list[dict[str, Any]], province_name: str | None = None
) -> tuple[str, list[str]]:
    """构建支持城市列表文本（按省份分组）。

    Returns:
        (summary_text, blocks)：全国时按省分组多块；单省时单块。
    """
    if province_name:
        prov_items = [
            it
            for it in items
            if code_to_prov(it.get("CityCode")) == province_short(province_name)
        ]
        cities = sorted(_area_short(str(it.get("Area") or "")) for it in prov_items)
        display = province_short(province_name) or province_name
        text = f"{display} 支持 {len(cities)} 城：\n  " + "、".join(cities)
        return text, [text]

    # 全国：按省份分组，每省一块
    grouped: dict[str, list[str]] = OrderedDict()
    for item in items:
        prov = code_to_prov(item.get("CityCode"))
        grouped.setdefault(prov, []).append(_area_short(str(item.get("Area") or "")))
    blocks: list[str] = []
    for prov in sorted(grouped, key=lambda x: -len(grouped[x])):
        cities = sorted(grouped[prov])
        blocks.append(f"【{prov}】{len(cities)} 城：\n  " + "、".join(cities))
    summary = f"AQI 支持城市（共 {len(items)} 城，按省份分组）："
    return summary, blocks


def _resolve_query_mode(
    keyword: str,
) -> tuple[str, str | None]:
    """解析查询模式（不含数据集命中；最终以 query_aqi 内二次判定为准）。

    Returns:
        (mode, province_name)：
        - mode: "help" | "nationwide" | "province" | "city"
        - province_name: province 模式下为省份全称，否则 None。
    """
    k = str(keyword or "").strip()
    if not k or k in ("帮助", "help", "?"):
        return "help", None
    if k in ("全国", "全部", "所有"):
        return "nationwide", None
    province = resolve_province_full(k)
    if province:
        short = province_short(province)
        k_norm = k.removesuffix("市").removesuffix("特别行政区").strip()
        # 直辖市名即城市名：走单城详情
        if short in _MUNICIPALITY_SHORTS and k_norm in {short, province}:
            return "city", None
        # 纯省名才走全省列表；「吉林市」等留给市级匹配
        if _is_plain_province_keyword(k):
            return "province", province
    return "city", None


def _resolve_place_query(
    keyword: str | None,
    optional: str | None = None,
) -> tuple[str, str | None, str | None, str | None]:
    """解析空气质量查询目标。

    Returns:
        (mode, province_name, place_keyword, quality_filter)
        - place_keyword: 市级/县级地名（含「宁夏银川」「宁夏 银川」拆出的市名）
        - quality_filter: 仅当第二段是等级词时有值
    """
    k = str(keyword or "").strip().replace(" ", "")
    opt = str(optional or "").strip()
    if not k or k in ("帮助", "help", "?"):
        return "help", None, None, None
    if k in ("全国", "全部", "所有"):
        quality = opt if _resolve_quality_filter(opt) else None
        if opt and quality is None:
            # /空气质量 全国 银川 → 不当等级，回落为全国；城市应直接查地名
            return "nationwide", None, None, None
        return "nationwide", None, None, quality

    # /空气质量 宁夏 银川：第二段优先当地名；确为等级词才做过滤
    if opt:
        quality = _resolve_quality_filter(opt)
        province = resolve_province_full(k)
        if province and quality is None:
            return "city", province, opt, None
        if province and quality is not None:
            return "province", province, None, quality

    # /空气质量 宁夏银川（省前缀+地名粘连）
    prov_hint, rest = strip_province_prefix(k)
    if prov_hint and rest and rest != k:
        # 前缀是省，后缀当地名
        return "city", resolve_province_full(prov_hint) or prov_hint, rest, None

    mode, province = _resolve_query_mode(k)
    if mode == "province":
        return "province", province, None, None
    if mode == "nationwide":
        return "nationwide", None, None, None
    if mode == "help":
        return "help", None, None, None
    return "city", None, k, None


async def query_aqi(
    keyword: str | None = None,
    *,
    client: JianAqiClient | None = None,
    quality_filter: str | None = None,
) -> dict[str, Any]:
    """查询 AQI 数据。

    Args:
        keyword: 城市/县名 / 省份名 / 「省+地名」 / 全国 / 帮助；None 或空视为帮助。
        client: 复用客户端实例；None 时内部新建并自动关闭。
        quality_filter: 可选第二段参数：等级过滤词，或省内市/县名（如「宁夏 银川」）。

    Returns:
        {
          "success": True,
          "mode": "help"|"nationwide"|"province"|"city",
          "text": "...",           # 主文本
          "blocks": [...],         # 全国/省份长文本合并转发分块（可能为空）
          "time_point": "...",
          "total": int,
        }
        或 {"success": False, "error": "..."}
    """
    mode, province, place_keyword, parsed_quality = _resolve_place_query(
        keyword, quality_filter
    )
    # 兼容旧参数：若调用方仍把等级放在 quality_filter，且解析未识别第二段，则回落原值
    if parsed_quality is None and quality_filter and _resolve_quality_filter(
        str(quality_filter)
    ):
        parsed_quality = _resolve_quality_filter(str(quality_filter))
    quality_filter = parsed_quality

    if mode == "help":
        return {
            "success": True,
            "mode": "help",
            "text": AQI_HELP_TEXT,
            "blocks": [],
            "time_point": "",
            "total": 0,
        }

    owned_client = client is None
    if owned_client:
        client = JianAqiClient()
    try:
        items, error = await client.fetch_aqi()
    finally:
        if owned_client and client is not None:
            await client.close()

    if error is not None:
        return {"success": False, "error": error}

    if not items:
        return {"success": False, "error": "AQI 数据为空"}

    time_point = str(items[0].get("TimePoint") or "")
    total = len(items)

    if mode == "nationwide":
        items, filter_error = _filter_by_quality(items, quality_filter)
        if filter_error:
            return {"success": False, "error": filter_error}
        total = len(items)
        summary, blocks = build_nationwide_text(items, time_point)
        return {
            "success": True,
            "mode": mode,
            "text": summary,
            "blocks": blocks,
            "time_point": time_point,
            "total": total,
        }

    if mode == "province":
        prov_items = [
            it
            for it in items
            if code_to_prov(it.get("CityCode")) == province_short(province)
        ]
        if not prov_items:
            return {
                "success": False,
                "error": f"未找到「{province}」的 AQI 数据",
            }
        prov_items, filter_error = _filter_by_quality(prov_items, quality_filter)
        if filter_error:
            return {"success": False, "error": filter_error}
        if not prov_items:
            return {
                "success": False,
                "error": f"「{province}」暂无符合「{quality_filter}」等级的数据",
            }
        if len(prov_items) == 1:
            only = prov_items[0]
            area = str(only.get("Area") or "").strip()
            short = province_short(province)
            if area in {short, short + "市", str(province)}:
                return {
                    "success": True,
                    "mode": "city",
                    "text": build_city_detail(only),
                    "blocks": [],
                    "time_point": time_point,
                    "total": 1,
                }
        text = build_province_text(province, prov_items, time_point)
        return {
            "success": True,
            "mode": mode,
            "text": text,
            "blocks": [],
            "time_point": time_point,
            "total": len(prov_items),
        }

    # mode == "city"：支持市级/县级单独查询，以及「省 + 地名」限定
    k = str(place_keyword or keyword or "").strip()
    pool = items
    if province:
        short = province_short(province) or str(province)
        pool = [it for it in items if code_to_prov(it.get("CityCode")) == short]
        if not pool:
            return {
                "success": False,
                "error": f"未找到「{short}」的 AQI 数据",
            }
    matches = _pick_best_area_matches(pool, k)
    if not matches:
        scope = province_short(province) if province else ""
        hint = f"「{scope}」内" if scope else ""
        return {
            "success": False,
            "error": f"未找到{hint}城市/县「{k}」的 AQI 数据",
        }
    item = matches[0]
    text = build_city_detail(item)
    return {
        "success": True,
        "mode": "city",
        "text": text,
        "blocks": [],
        "time_point": time_point,
        "total": len(matches),
    }


async def query_aqi_rank(
    direction: str | None = None,
    *,
    client: JianAqiClient | None = None,
    limit: int = DEFAULT_RANK_LIMIT,
) -> dict[str, Any]:
    """查询 AQI 排行榜。

    Args:
        direction: "best" 最好 / "worst" 最差；None 时同时输出最好与最差。
        client: 复用客户端实例；None 时内部新建并自动关闭。
        limit: 每个方向最多条数（默认 10）。

    Returns:
        {
          "success": True,
          "text": "...",          # 主文本（direction 指定时）
          "blocks": [...],        # 合并转发分块（direction 为 None 时两块：最好在前）
          "direction": "best"|"worst"|"both",
          "time_point": "...",
        }
        或 {"success": False, "error": "..."}
    """
    raw = str(direction or "").strip()
    d: str | None = None
    if raw in ("best", "最好", "优"):
        d = "best"
    elif raw in ("worst", "最差", "差"):
        d = "worst"
    # 其它（含 None/空）保持 None -> 同时输出最好+最差

    owned_client = client is None
    if owned_client:
        client = JianAqiClient()
    try:
        items, error = await client.fetch_aqi()
    finally:
        if owned_client and client is not None:
            await client.close()

    if error is not None:
        return {"success": False, "error": error}
    if not items:
        return {"success": False, "error": "AQI 数据为空"}

    time_point = str(items[0].get("TimePoint") or "")
    effective_limit = max(1, min(int(limit or DEFAULT_RANK_LIMIT), 50))
    text, blocks = build_rank_text(
        items,
        direction=d,
        time_point=time_point,
        limit=effective_limit,
    )
    return {
        "success": True,
        "text": text,
        "blocks": blocks,
        "direction": d or "both",
        "time_point": time_point,
    }


async def query_aqi_city_list(
    province_keyword: str | None = None,
    *,
    client: JianAqiClient | None = None,
) -> dict[str, Any]:
    """查询 AQI 支持的城市列表。

    Args:
        province_keyword: 可选省份关键词；None 时返回全国分组列表。

    Returns:
        {"success": True, "text": "...", "blocks": [...], "is_nationwide": bool}
        或 {"success": False, "error": "..."}
    """
    owned_client = client is None
    if owned_client:
        client = JianAqiClient()
    try:
        items, error = await client.fetch_aqi()
    finally:
        if owned_client and client is not None:
            await client.close()

    if error is not None:
        return {"success": False, "error": error}
    if not items:
        return {"success": False, "error": "AQI 数据为空"}

    province_name = None
    if province_keyword and str(province_keyword).strip():
        province_name = resolve_province_full(str(province_keyword).strip())
        if not province_name:
            return {
                "success": False,
                "error": f"未找到省份「{province_keyword}」",
            }

    text, blocks = build_city_list_text(items, province_name)
    return {
        "success": True,
        "text": text,
        "blocks": blocks,
        "is_nationwide": province_name is None,
    }


# 帮助文本（纯文本，不堆叠装饰 emoji）
AQI_HELP_TEXT = (
    "空气质量查询\n\n"
    "用法：\n"
    "  /空气质量 <市/县名>           查询单个城市或县级单位详情（含浓度）\n"
    "  /空气质量 <省份> <市/县名>    在指定省内查询单个城市/县\n"
    "  /空气质量 <省份名> [等级]     查询全省各城市空气质量（可按等级过滤）\n"
    "  /空气质量 全国 [等级]         全国空气质量概览（可按等级过滤）\n"
    "  /空气质量排行 [最好|最差]     空气质量排行榜（无参时同时输出最好与最差）\n"
    "  /空气质量列表 [省份]          查看支持的城市列表\n\n"
    "等级说明：🟢优 0-50 | 🟡良 51-100 | 🟠轻度 101-150 | 🔴中度 151-200 | 🟣重度 201-300 | 🟤严重 301+\n\n"
    "示例：/空气质量 银川 | /空气质量 宁夏 银川 | /空气质量 北京 | /空气质量 广东 优 | /空气质量排行"
)

__all__ = [
    "code_to_prov",
    "aqi_num",
    "quality_label",
    "primary_pollutant_label",
    "build_city_detail",
    "build_province_text",
    "build_nationwide_text",
    "build_rank_text",
    "build_city_list_text",
    "query_aqi",
    "query_aqi_rank",
    "query_aqi_city_list",
    "AQI_HELP_TEXT",
    "DEFAULT_RANK_LIMIT",
]
