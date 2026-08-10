"""
城市空气质量指数（AQI）查询服务。

统一承接命令侧（/空气质量 /空气质量排行 /空气质量列表）的查询编排：
- 城市/省份/全国三种查询模式解析
- 按 CityCode 前缀反推省份（无需维护静态城市表）
- AQI 数值解析与缺测（NA）防御
- 等级圆点视觉指示（仅保留严重程度指示器，不堆叠装饰 emoji）
- 文本格式化：单城市详情 / 省份列表 / 全国概览 / 排行榜 / 城市列表

数据源：FAN Studio https://api.fanstudio.tech/we/aqi.php
实测响应为 UTF-8 带 BOM 的 JSON 数组，一次返回全国 338 城快照。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from ....utils.china_regions import province_short, resolve_province_full
from ....utils.text_format_utils import format_iso_time
from ...network.http.fan_aqi_client import FanAqiClient

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

# AQI 等级圆点（按 AQI 数值分档，参考 HJ 633-2012）
_AQI_LEVEL_DOT = [
    (0, 51, "🟢"),  # 优 0-50
    (51, 101, "🟡"),  # 良 51-100
    (101, 151, "🟠"),  # 轻度污染 101-150
    (151, 201, "🔴"),  # 中度污染 151-200
    (201, 301, "🟣"),  # 重度污染 201-300
    (301, 10**9, "🟤"),  # 严重污染 301+
]

# 等级过滤词 -> 对应 AQI 数值区间
_QUALITY_FILTER_RANGES: dict[str, tuple[int, int]] = {
    "优": (0, 51),
    "良": (51, 101),
    "轻度污染": (101, 151),
    "中度污染": (151, 201),
    "重度污染": (201, 301),
    "严重污染": (301, 10**9),
}

# 城市名后缀（用于去掉后缀做模糊匹配）
_AREA_SUFFIXES = ("市", "地区", "自治州", "盟", "县")


def code_to_prov(code: int | None) -> str:
    """按 CityCode 前两位反推省份；新疆兵团（659xxx）单列。"""
    if code is None:
        return "未知"
    s = str(code)
    if s.startswith("659"):
        return "新疆兵团"
    return CODE2PROV.get(s[:2], "未知")


def aqi_num(item: dict[str, Any]) -> int | None:
    """提取 AQI 数值；缺测（NA/空/异常）返回 None。"""
    try:
        v = str(item.get("AQI") or "").strip()
        if not v or v.upper() in ("NA", "N/A", "-"):
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def aqi_level_dot(item: dict[str, Any]) -> str:
    """返回 AQI 等级圆点；缺测返回 ⬜。"""
    aqi = aqi_num(item)
    if aqi is None:
        return "⬜"
    for lo, hi, dot in _AQI_LEVEL_DOT:
        if lo <= aqi < hi:
            return dot
    return "⬜"


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


def _area_short(area: str) -> str:
    """压缩城市名：去掉「市」后缀（自治州/地区/盟保留全称）。"""
    s = str(area or "").strip()
    if s.endswith("市"):
        return s[:-1]
    return s


def _match_area(item: dict[str, Any], keyword: str) -> bool:
    """匹配城市关键词（去掉后缀模糊匹配）。"""
    area = str(item.get("Area") or "").strip()
    kw = str(keyword or "").strip().replace(" ", "")
    if not area or not kw:
        return False
    if area == kw or area == kw + "市":
        return True
    for suffix in _AREA_SUFFIXES:
        if area.endswith(suffix) and area[: -len(suffix)] == kw:
            return True
    return kw in area


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
        level = int(item.get("AqiLevel") or 0)
        lines.append(f"{aqi_level_dot(item)} AQI {aqi_text}  {q_label}（{level}级）")
    lines.append(f"首要污染物：{pp_label}")
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
        lines.append(f"{aqi_level_dot(item)} {area} AQI {aqi} {q}{suffix}")
    return "\n".join(lines)


def build_nationwide_text(
    items: list[dict[str, Any]], time_point: str | None
) -> tuple[str, list[str]]:
    """构建全国 AQI 概览文本（按等级分组）。

    Returns:
        (summary_text, blocks)：summary_text 为普通文本；blocks 为合并转发分块。
    """
    tp = format_iso_time(time_point)
    groups: dict[str, list[str]] = {
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
        groups.setdefault(q, []).append(
            f"{_area_short(str(item.get('Area') or '未知'))} {item.get('AQI') or 'NA'}"
        )

    summary = f"全国空气质量概览\n数据时间：{tp} | 覆盖 {len(items)} 个城市"
    blocks: list[str] = []
    for q in ["优", "良", "轻度污染", "中度污染", "重度污染", "严重污染", "数据缺失"]:
        cities = groups.get(q)
        if not cities:
            continue
        dot = {
            "优": "🟢",
            "良": "🟡",
            "轻度污染": "🟠",
            "中度污染": "🔴",
            "重度污染": "🟣",
            "严重污染": "🟤",
            "数据缺失": "⬜",
        }[q]
        lines = [f"{dot} {q}（{len(cities)}城）："]
        for i in range(0, len(cities), 8):
            lines.append("  " + "、".join(cities[i : i + 8]))
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
        lines.append(f"{idx}. {aqi_level_dot(item)} {area} AQI {aqi} {q}{suffix}")
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
            if code_to_prov(int(it.get("CityCode") or 0))
            == province_short(province_name)
        ]
        cities = sorted(_area_short(str(it.get("Area") or "")) for it in prov_items)
        display = province_short(province_name) or province_name
        text = f"{display} 支持 {len(cities)} 城：\n  " + "、".join(cities)
        return text, [text]

    # 全国：按省份分组，每省一块
    grouped: dict[str, list[str]] = OrderedDict()
    for item in items:
        prov = code_to_prov(int(item.get("CityCode") or 0))
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
    """解析查询模式。

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
        return "province", province
    return "city", None


async def query_aqi(
    keyword: str | None = None,
    *,
    client: FanAqiClient | None = None,
    quality_filter: str | None = None,
) -> dict[str, Any]:
    """查询 AQI 数据。

    Args:
        keyword: 城市名 / 省份名 / 全国 / 帮助；None 或空视为帮助。
        client: 复用客户端实例；None 时内部新建并自动关闭。
        quality_filter: 可选等级过滤词（优/良/轻度污染等）。

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
    mode, province = _resolve_query_mode(keyword)
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
        client = FanAqiClient()
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

    # 等级过滤（仅对城市/省份模式生效）
    if quality_filter:
        fk = str(quality_filter).strip()
        if fk in _QUALITY_FILTER_RANGES:
            lo, hi = _QUALITY_FILTER_RANGES[fk]
            items = [
                it for it in items if (n := aqi_num(it)) is not None and lo <= n < hi
            ]
            total = len(items)

    if mode == "nationwide":
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
            if code_to_prov(int(it.get("CityCode") or 0)) == province_short(province)
        ]
        if not prov_items:
            return {
                "success": False,
                "error": f"未找到「{province}」的 AQI 数据",
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

    # mode == "city"
    k = str(keyword or "").strip()
    matches = [it for it in items if _match_area(it, k)]
    if not matches:
        return {
            "success": False,
            "error": f"未找到城市「{k}」的 AQI 数据",
        }
    # 多候选（如「吉林」匹配到省市）时取精确匹配
    if len(matches) > 1:
        exact = [
            it
            for it in matches
            if str(it.get("Area") or "") == k or str(it.get("Area") or "").startswith(k)
        ]
        if exact:
            matches = exact
    item = matches[0]
    text = build_city_detail(item)
    return {
        "success": True,
        "mode": mode,
        "text": text,
        "blocks": [],
        "time_point": time_point,
        "total": len(matches),
    }


async def query_aqi_rank(
    direction: str | None = None,
    *,
    client: FanAqiClient | None = None,
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
        client = FanAqiClient()
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
    client: FanAqiClient | None = None,
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
        client = FanAqiClient()
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
    "  /空气质量 <城市名>        查询指定城市空气质量\n"
    "  /空气质量 <省份名>        查询全省各城市空气质量\n"
    "  /空气质量 全国            全国主要城市空气质量概览\n"
    "  /空气质量排行 [最好|最差]  空气质量排行榜（无参时同时输出最好与最差）\n"
    "  /空气质量列表 [省份]       查看支持的城市列表\n\n"
    "等级说明：🟢优 0-50 | 🟡良 51-100 | 🟠轻度 101-150 | 🔴中度 151-200 | 🟣重度 201-300 | 🟤严重 300+\n\n"
    "示例：/空气质量 北京 | /空气质量 广东 | /空气质量 全国 | /空气质量排行 | /空气质量列表 新疆"
)


__all__ = [
    "code_to_prov",
    "aqi_num",
    "aqi_level_dot",
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
