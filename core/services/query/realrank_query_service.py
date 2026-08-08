"""
实况排行查询服务。

统一承接命令侧（/气温排行 /降水排行 /风速排行）的查询编排：
- 排行要素关键词解析（气温/温度、降水、风速）
- 可选历史时次解析（如「08日15时」「2026080815」「今天15时」等）
- 调用 NmcRealRankClient 抓取 /rest/realrank 接口数据
- 文本格式化：站点名 + 省份右对齐、数值右对齐，输出 Top10

数据源：中央气象台官网首页「实况排行」模块
    https://www.nmc.cn/rest/realrank/{type}/{hour}/{ymdh}

实测接口行为：
- 无需 Referer、无需登录，直接 GET 即可
- 返回 Top10，字段 name（站点）、pname（省份）、value（数值）
- 支持按历史时次查询（如 08日15时 的气温排行）
- 无数据/非法时次时 data 为空字符串，需防御处理
- 单位由前端拼接：气温 ℃、降水 mm、风速 m/s
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from ...network.http.nmc_realrank_client import (
    RANK_HOURS,
    RANK_TYPES,
    NmcRealRankClient,
)

# 默认查询条数（接口本身返回 Top10）
DEFAULT_LIMIT = 10

# 无参查询时，当前整点数据未发布时自动回退的小时数上限（每次回退 1 小时）
AUTO_RETRY_HOURS = 3

# 排行要素定义：展示名、接口 type、数值单位、输出标题、默认时间跨度
# mintemp（最低气温）与 maxtemp 共用「气温」大类，但走不同接口 type。
_RANK_KIND_DEFS: dict[str, dict[str, str]] = {
    "temperature": {
        "label": "气温",
        "type": "maxtemp",
        "unit": "℃",
        "title": "气温排行",
    },
    "mintemperature": {
        "label": "最低气温",
        "type": "mintemp",
        "unit": "℃",
        "title": "最低气温排行",
    },
    "rain": {
        "label": "降水",
        "type": "rain",
        "unit": "mm",
        "title": "降水排行",
    },
    "wind": {
        "label": "风速",
        "type": "wind",
        "unit": "m/s",
        "title": "风速排行",
    },
}

# 要素关键词 -> 要素键
_RANK_KIND_KEYWORDS: dict[str, str] = {
    "气温": "temperature",
    "温度": "temperature",
    "气温排行": "temperature",
    "温度排行": "temperature",
    "气温榜": "temperature",
    "温度榜": "temperature",
    "最高气温": "temperature",
    "最高温": "temperature",
    "最低气温": "mintemperature",
    "最低温": "mintemperature",
    "低温": "mintemperature",
    "低温排行": "mintemperature",
    "低温榜": "mintemperature",
    "降水": "rain",
    "降水排行": "rain",
    "降水榜": "rain",
    "降水量排行": "rain",
    "降水量榜": "rain",
    "风速": "wind",
    "风速排行": "wind",
    "风速榜": "wind",
    "风速排行榜": "wind",
}

# 各要素默认时间跨度（小时）。mintemp 走 24h 档（最低气温按日统计），
# 其余走逐小时（1h）。maxtemp 逐小时即「当前最高气温」。
_RANK_DEFAULT_HOUR: dict[str, int] = {
    "temperature": 1,
    "mintemperature": 24,
    "rain": 1,
    "wind": 1,
}

# 时次解析正则
# 1) YYYYMMDDHH（如 2026080815）
_YMDH_RE = re.compile(r"^(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})(?P<h>\d{2})$")
# 2) MM月DD日HH时 / MM月DD日HH点（如 08月15日15时）
_MDH_RE = re.compile(r"^(?P<m>\d{1,2})月(?P<d>\d{1,2})日\s*(?P<h>\d{1,2})[时点]$")
# 3) 今天/昨天 + HH时/HH点（如 今天15时、昨天21点）
_REL_DAY_RE = re.compile(r"^(今天|今日|昨天|昨日)\s*(?P<h>\d{1,2})[时点]$")


def _today_ymdh(hour: int) -> str:
    """按当前时间生成 YYYYMMDDHH 时次。"""
    now = datetime.now()
    return f"{now.year:04d}{now.month:02d}{now.day:02d}{hour:02d}"


def _shift_day_ymdh(hour: int, days: int) -> str:
    """生成偏移 days 天的 YYYYMMDDHH 时次。"""
    from datetime import timedelta

    target = datetime.now() + timedelta(days=days)
    return f"{target.year:04d}{target.month:02d}{target.day:02d}{hour:02d}"


def resolve_rank_kind(keyword: str) -> str | None:
    """解析排行要素关键词，返回要素键（temperature/rain/wind）。

    Args:
        keyword: 用户输入的关键词，如「气温」「降水」「风速」。

    Returns:
        要素键；无法识别时返回 None。
    """
    key = str(keyword or "").strip()
    if not key:
        return None
    # 先精确匹配，再按包含关系匹配
    if key in _RANK_KIND_KEYWORDS:
        return _RANK_KIND_KEYWORDS[key]
    for k, v in _RANK_KIND_KEYWORDS.items():
        if k in key:
            return v
    return None


def resolve_rank_type(keyword: str) -> str | None:
    """解析排行要素关键词，返回接口 type（maxtemp/rain/wind）。

    Args:
        keyword: 用户输入的关键词。

    Returns:
        接口 type；无法识别时返回 None。
    """
    kind = resolve_rank_kind(keyword)
    if kind is None:
        return None
    return _RANK_KIND_DEFS[kind]["type"]


def resolve_time_text(ymdh: str) -> str:
    """把 YYYYMMDDHH 时次格式化为「YYYY年MM月DD日 HH时」。

    Args:
        ymdh: 时次，格式 YYYYMMDDHH。

    Returns:
        格式化后的时间文本。
    """
    return NmcRealRankClient.parse_time_text(ymdh)


def parse_time_arg(text: str) -> str | None:
    """解析用户输入的时间参数，返回接口使用的 YYYYMMDDHH 时次。

    支持的格式（都是当天/当天附近，接口只保留近期时次）：
    - YYYYMMDDHH：2026080815
    - MM月DD日HH时 / HH点：08月15日15时、8月15日15点
    - 今天/今日/昨天/昨日 + HH时/HH点：今天15时、昨天21点
    - 纯 HH时/HH点：15时（视为今天）

    Args:
        text: 用户输入的时间文本；None/空串返回 None。

    Returns:
        YYYYMMDDHH 时次字符串；无法解析时返回 None。
    """
    if not text:
        return None
    s = str(text).strip()

    # 1) YYYYMMDDHH
    m = _YMDH_RE.match(s)
    if m:
        return f"{m.group('y')}{m.group('m')}{m.group('d')}{m.group('h')}"

    # 2) MM月DD日HH时 / 点
    m = _MDH_RE.match(s)
    if m:
        mm = int(m.group("m"))
        dd = int(m.group("d"))
        hh = int(m.group("h"))
        if not (1 <= mm <= 12 and 1 <= dd <= 31 and 0 <= hh <= 23):
            return None
        # 用当前年份拼装；若日期晚于今天则视为去年？这里简单用今年，
        # 因为接口只保留近期数据，查询太早的时次会返回空数据。
        now = datetime.now()
        return f"{now.year:04d}{mm:02d}{dd:02d}{hh:02d}"

    # 3) 今天/昨天 + HH时
    m = _REL_DAY_RE.match(s)
    if m:
        hh = int(m.group("h"))
        if not (0 <= hh <= 23):
            return None
        days = -1 if m.group(1) in ("昨天", "昨日") else 0
        return _shift_day_ymdh(hh, days)

    # 4) 纯 HH时/HH点（视为今天）
    m = re.match(r"^(?P<h>\d{1,2})[时点]$", s)
    if m:
        hh = int(m.group("h"))
        if not (0 <= hh <= 23):
            return None
        return _today_ymdh(hh)

    return None


def _display_width(s: str) -> int:
    """估算字符串显示宽度：中文按 2 字符宽、ASCII 按 1 字符宽。"""
    w = 0
    for ch in str(s):
        w += 2 if ord(ch) > 127 else 1
    return w


# 全角空格（U+3000）：聊天平台会压缩连续半角空格导致对齐失效，
# 全角空格不会被压缩，且显示宽度固定为 2 列，适合用来做列对齐。
_FULLWIDTH_SPACE = "\u3000"


def _pad_display_width(s: str, width: int, align: str = "left") -> str:
    """按显示宽度填充/截断字符串到指定宽度。

    终端等宽字体下中文字符占 2 列，直接用 str.ljust/rjust 会因
    字符数与显示宽度不一致导致列错位；且聊天平台会压缩连续半角空格，
    因此这里用「全角空格为主、半角空格兜奇数」的方式补齐显示宽度：
    - 全角空格占 2 显示宽，不会被平台压缩；
    - 若需要补奇数列宽，用 1 个半角空格兜底（夹在全角空格之间，
      不会触发平台连续空格折叠）。

    Args:
        s: 原始字符串。
        width: 目标显示宽度。
        align: 对齐方式，left/right。

    Returns:
        填充后的字符串（按显示宽度对齐）。
    """
    s = str(s)
    cur = _display_width(s)
    if cur >= width:
        return s
    pad_count = width - cur
    full = pad_count // 2
    half = pad_count % 2
    pad = _FULLWIDTH_SPACE * full + " " * half
    return s + pad if align == "left" else pad + s


def _format_value(value: Any, *, unit: str) -> str:
    """格式化排行数值（右对齐到固定宽度）。

    Args:
        value: 接口原始数值（float）。
        unit: 单位文本（℃/mm/m/s）。

    Returns:
        格式化后的数值文本，如「  42.3 ℃」。
    """
    if value is None:
        num = "-"
    else:
        try:
            v = float(value)
        except (TypeError, ValueError):
            num = "-"
        else:
            if v >= 9999:
                num = "-"
            else:
                num = f"{v:.1f}"
    # 数值右对齐到 6 显示宽（含负号和小数点），单位紧随其后
    return _pad_display_width(num, 6, align="right") + " " + unit


def _format_station_name(name: str, pname: str) -> str:
    """格式化站点名 + 省份（站点名居左、省份右对齐）。

    Args:
        name: 站点名。
        pname: 省份名。

    Returns:
        格式化后的文本，如「吐鲁番 - 新疆」。
    """
    name = str(name or "").strip() or "-"
    pname = str(pname or "").strip() or "-"
    return f"{name} - {pname}"


def _normalize_time_text(time_text: str) -> str:
    """规范化排行日期文本：把连字符「-」规范为「 - 」（两侧空格分隔）。

    接口返回的 format_time 形如「08月07日15时-08日14时」，
    显示时把连字符改成空格分隔的短横线，视觉更清晰。
    """
    s = str(time_text or "").strip()
    return re.sub(r"\s*-\s*", " - ", s)


def build_rank_text(
    *,
    rank_type: str,
    items: list[dict[str, Any]],
    time_text: str,
    limit: int = DEFAULT_LIMIT,
) -> str:
    """构建排行文本（Top10 右对齐输出）。

    Args:
        rank_type: 接口 type（maxtemp/rain/wind）。
        items: 接口返回的排行条目列表。
        time_text: 展示用时间文本（如「2026年08月08日 21时」）。
        limit: 最多输出条数（默认 10）。

    Returns:
        格式化后的多行文本。
    """
    kind_def = next(
        (v for v in _RANK_KIND_DEFS.values() if v["type"] == rank_type),
        None,
    )
    if kind_def is None:
        kind_def = {"label": "排行", "type": rank_type, "unit": "", "title": "排行"}
    title = kind_def["title"]
    unit = kind_def["unit"]

    lines: list[str] = []

    if not items:
        lines.append(f"{title} {time_text}")
        lines.append("暂无数据")
        return "\n".join(lines)

    # 站点名列宽度：取前 limit 条中「站点 - 省份」的最大显示宽度，最小 12
    limit_items = items[:limit]
    station_width = max(
        (_display_width(f"{it['name']} - {it['pname']}") for it in limit_items),
        default=12,
    )

    # 序号区：最多两位（Top10 只到 10），序号顶格后补位到固定 4 显示宽，
    # 使「序号 + 间隔」总宽恒定，地点列精确对齐：
    #   - 1-9 行：`1.`（2列）+ 全角空格（2列）= 4 列
    #   - 10 行：`10.`（3列）+ 半角空格（1列）= 4 列
    # 10 行用单一半角空格补位（不与别的空格连续，QQ 不会折叠），
    # 从而与 1-9 行地点列起点一致，看起来不会"多一个空格"。
    # 每行结构（显示宽度）：
    #   {序号区:4} {站点:station_width} {数值:6} {单位}
    # 数值右端所在显示宽度 = 4(序号区) + station_width + 1(空格) + 6(数值)
    value_right = station_width + 11

    # 标题行：日期文本规范化（连字符两侧加空格）。
    # 日期比数值列短时，标题左对齐、日期右对齐到数值列右端；
    # 日期比数值列长（超宽）时，标题与日期之间至少保留 2 个空格。
    display_time = _normalize_time_text(time_text)
    date_width = _display_width(display_time)
    title_pad = value_right - date_width
    if title_pad >= 2:
        header = _pad_display_width(title, title_pad, align="left") + display_time
    else:
        # 日期超宽：标题 + 2 空格 + 日期（不再强行右对齐）
        header = f"{title}  {display_time}"
    lines.append(header)

    for idx, it in enumerate(limit_items, 1):
        station_text = _format_station_name(it["name"], it["pname"])
        value_text = _format_value(it.get("value"), unit=unit)
        # 序号顶格，序号区固定 4 显示宽左对齐：
        # "1." 补 1 个全角空格成 "1.　"、"10." 补 1 个半角空格成 "10. "
        idx_text = _pad_display_width(f"{idx}.", 4, align="left")
        padded_station = _pad_display_width(station_text, station_width, align="left")
        lines.append(f"{idx_text}{padded_station} {value_text}")

    return "\n".join(lines)


async def query_rank(
    *,
    rank_type: str,
    ymdh: str | None = None,
    hour: int | None = None,
    client: NmcRealRankClient | None = None,
) -> dict[str, Any]:
    """查询指定要素的实况排行。

    Args:
        rank_type: 接口 type（maxtemp/mintemp/rain/wind）。
        ymdh: 时次，格式 YYYYMMDDHH；None 时使用当前整点。
        hour: 时间跨度（1/6/24）。None 时按要素选择默认跨度
            （mintemp 为 24h 最低气温档，其余为 1h 逐小时档）。
        client: 复用客户端实例；None 时内部新建并自动关闭。

    Returns:
        {"success": True, "text": "...", "time": "...", "raw_items": [...]}
        {"success": False, "error": "..."}
    """
    # 归一化 rank_type
    if rank_type not in RANK_TYPES:
        return {"success": False, "error": f"不支持的排行类型: {rank_type}"}

    # 归一化 hour：未指定时按要素默认跨度（mintemp=24h，其余=1h）
    if hour is None:
        hour = _RANK_DEFAULT_HOUR.get(
            next((k for k, v in _RANK_KIND_DEFS.items() if v["type"] == rank_type), ""),
            1,
        )
    try:
        hour = int(hour)
    except (TypeError, ValueError):
        hour = 1
    if hour not in RANK_HOURS:
        hour = 1

    # 解析时次：未提供时取当前整点，并允许数据未发布时自动回退前 1 小时。
    # 无参查询（ymdh 为 None）在刚过整点时当前整点数据往往还没发布，
    # 接口会返回空数据，此时自动向前回退最多 AUTO_RETRY_HOURS 次。
    auto_retry = ymdh is None
    resolved_ymdh = ymdh
    if not resolved_ymdh:
        now = datetime.now()
        resolved_ymdh = f"{now.year:04d}{now.month:02d}{now.day:02d}{now.hour:02d}"

    owned_client = client is None
    if owned_client:
        client = NmcRealRankClient()
    try:
        payload = await client.fetch_rank(
            rank_type=rank_type,
            hour=hour,
            ymdh=resolved_ymdh,
        )
        # 无参查询且当前时次无数据时，自动回退前 1 小时重试。
        # 仅「暂无排行数据」这类空数据才回退，网络/解析错误直接返回。
        retry_used = 0
        while (
            auto_retry
            and not payload.get("success")
            and "暂无" in (payload.get("error") or "")
            and retry_used < AUTO_RETRY_HOURS
        ):
            retry_used += 1
            prev = datetime.strptime(resolved_ymdh, "%Y%m%d%H") - timedelta(hours=1)
            resolved_ymdh = prev.strftime("%Y%m%d%H")
            payload = await client.fetch_rank(
                rank_type=rank_type,
                hour=hour,
                ymdh=resolved_ymdh,
            )
    finally:
        if owned_client and client is not None:
            await client.close()

    if not payload.get("success"):
        return {
            "success": False,
            "error": payload.get("error") or "排行查询失败",
        }

    items = payload.get("items") or []
    time_text = payload.get("format_time") or payload.get("time") or ""
    # 若接口没给时间文本，用本地格式化兜底
    if not time_text:
        time_text = resolve_time_text(resolved_ymdh)

    text = build_rank_text(
        rank_type=rank_type,
        items=items,
        time_text=time_text,
    )
    return {
        "success": True,
        "text": text,
        "time": time_text,
        "raw_items": items,
    }


__all__ = [
    "resolve_rank_kind",
    "resolve_rank_type",
    "resolve_time_text",
    "parse_time_arg",
    "build_rank_text",
    "_normalize_time_text",
    "query_rank",
    "DEFAULT_LIMIT",
    "AUTO_RETRY_HOURS",
]
