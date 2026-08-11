"""
实况排行查询服务。

统一承接命令侧（/气温排行 /最低气温排行 /降水排行 /风速排行）的查询编排：
- 排行要素关键词解析（气温/温度、降水、风速）
- 可选历史时次解析（如「08日15时」「2026080815」「今天15时」等）
- 可选时间跨度（1h 逐小时 / 6h / 24h 累计）
- 调用 NmcRealRankClient 抓取 /rest/realrank 接口数据
- 文本格式化：站点名 + 省份右对齐、数值右对齐，输出 Top10

数据源：中央气象台官网首页「实况排行」模块
    https://www.nmc.cn/rest/realrank/{type}/{hour}/{ymdh}

实测接口行为：
- 无需 Referer、无需登录，直接 GET 即可
- 返回 Top10，字段 name（站点）、pname（省份）、value（数值）
- 四要素（maxtemp/mintemp/rain/wind）均支持 1h / 6h / 24h 三档跨度
- 6h/24h 跨度返回「起点时-终点时」滚动区间文本
- 24h 降水按日界整点（如 08时）滚动累计
- 支持按历史时次查询（如 08日15时 的气温排行）
- 无数据/非法时次时 data 为空字符串，需防御处理
- 单位由前端拼接：气温 ℃、降水 mm、风速 m/s
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ....utils.severity_emoji import (
    rank_level_emoji,
)
from ....utils.text_format_utils import (
    MISSING_VALUE,
)
from ....utils.text_format_utils import (
    display_width as _display_width,
)
from ....utils.text_format_utils import (
    pad_display_width as _pad_display_width,
)
from ...network.http.nmc_realrank_client import (
    RANK_HOURS,
    RANK_TYPES,
    NmcRealRankClient,
)

# NMC 接口时次使用北京时间（UTC+8），与服务器本地时区解耦，
# 避免容器部署在 UTC 等非 +8 时区时无参查询取错时次。
_CST = timezone(timedelta(hours=8))

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

# 各要素默认时间跨度（小时）。官网首页「实况排行」三档均可选，
# 四要素（maxtemp/mintemp/rain/wind）接口行为完全一致：
# - 1h：逐小时实时（气温=当前气温、降水=1小时雨量、风速=瞬时风速）
# - 6h：6 小时累计/极值（气温=6h 最高/最低）
# - 24h：24 小时累计/极值（最低气温按日统计取 24h 档）
# 无参查询统一默认 1h（与官网首页默认行为一致）；
# 需要日最低统计时显式指定「24小时」，返回昨 08/20 双日界时段。
_RANK_DEFAULT_HOUR: dict[str, int] = {
    "temperature": 1,
    "mintemperature": 1,
    "rain": 1,
    "wind": 1,
}

# 时间跨度关键词 -> 跨度值（小时）。
# 「24小时/24h/二十四小时/全天」归一为 24；「6小时/6h/六小时」归一为 6。
# 注意：刻意不含纯「时」结尾形式（如「6时/24时」），
# 避免与纯时次（早上6点/次日0点）冲突。
_HOUR_KEYWORDS: dict[str, int] = {
    "6小时": 6,
    "6h": 6,
    "六小时": 6,
    "24小时": 24,
    "24h": 24,
    "二十四小时": 24,
    "全天": 24,
}

# 时间跨度正则：由 _HOUR_KEYWORDS 表动态生成（按长度降序，优先匹配长别名）。
# 左边界 (?<![\d一二三四五六七八九十百千万两]) 同时防止：
# - 「16小时」被截取为「6小时」（半角数字前有 1）
# - 「十六小时」被截取为「六小时」（中文数字前有「十」）
_HOUR_ARG_RE = re.compile(
    r"(?<![\d一二三四五六七八九十百千万两])(?:"
    + "|".join(re.escape(k) for k in sorted(_HOUR_KEYWORDS, key=len, reverse=True))
    + ")"
)

# 时次解析正则
# 1) YYYYMMDDHH（如 2026080815）
_YMDH_RE = re.compile(r"^(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})(?P<h>\d{2})$")
# 2) MM月DD日HH时 / MM月DD日HH点（如 08月15日15时）
_MDH_RE = re.compile(r"^(?P<m>\d{1,2})月(?P<d>\d{1,2})日\s*(?P<h>\d{1,2})[时点]$")
# 3) 今天/昨天 + HH时/HH点（如 今天15时、昨天21点）
_REL_DAY_RE = re.compile(r"^(今天|今日|昨天|昨日)\s*(?P<h>\d{1,2})[时点]$")


def _today_ymdh(hour: int) -> str:
    """按北京时间（UTC+8）生成 YYYYMMDDHH 时次。"""
    now = datetime.now(_CST)
    return f"{now.year:04d}{now.month:02d}{now.day:02d}{hour:02d}"


def _shift_day_ymdh(hour: int, days: int) -> str:
    """生成偏移 days 天的 YYYYMMDDHH 时次（按北京时间）。"""
    target = datetime.now(_CST) + timedelta(days=days)
    return f"{target.year:04d}{target.month:02d}{target.day:02d}{hour:02d}"


def parse_rank_args(text: str | None) -> tuple[int | None, str | None]:
    """从用户输入中解析（时间跨度, 时次）。

    支持两种写法：
    - 跨度优先：如「24小时」「6小时」→ (24, None)
    - 跨度 + 时次：如「24小时 08时」「6h 昨天21时」→ (24, '昨天21时')
    - 仅时次：如「08日15时」→ (None, '08日15时')（跨度取要素默认）

    Args:
        text: 用户输入的命令参数（原始字符串）。

    Returns:
        (hour, time_text)：hour 为 6/24 或 None；time_text 为剩余时次文本。
    """
    if not text or not str(text).strip():
        return None, None
    s = str(text).strip()
    hour: int | None = None

    # 提取跨度关键词：匹配 _HOUR_KEYWORDS 表生成的统一正则
    # （含「6小时/24h/六小时/全天」等别名，带数字/中文数字左边界防误截）。
    m = _HOUR_ARG_RE.search(s)
    if m:
        hour = _HOUR_KEYWORDS[m.group(0)]
        s = (s[: m.start()] + " " + s[m.end() :]).strip()

    # 剩余部分去掉可能残留的空白，作为时次参数
    time_text = s.strip() or None
    if time_text:
        # 跨度关键词与剩余时次之间可能夹了「的/的」等，简单容错
        time_text = re.sub(r"^[\s的]+", "", time_text)
    return hour, time_text


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
    # 包含匹配按关键词长度降序，避免「气温」抢先命中「最低气温」。
    for k in sorted(_RANK_KIND_KEYWORDS, key=len, reverse=True):
        if k in key:
            return _RANK_KIND_KEYWORDS[k]
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
        ymdh = f"{m.group('y')}{m.group('m')}{m.group('d')}{m.group('h')}"
        # 校验日期真实合法（如 2026139925 非法），避免误导用户
        try:
            datetime.strptime(ymdh, "%Y%m%d%H")
        except ValueError:
            return None
        return ymdh

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
            # 缺测标记（9999）或非有限值（NaN/inf）统一显示为「-」
            if not math.isfinite(v) or v >= MISSING_VALUE:
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


def _build_rank_block(
    *,
    rank_type: str,
    title: str,
    unit: str,
    items: list[dict[str, Any]],
    time_text: str,
    limit: int,
    hour: int,
) -> list[str]:
    """构建单个时段（或单块）的排行文本块。

    Args:
        rank_type: 接口 type（maxtemp/mintemp/rain/wind）。
        title: 排行标题（如「气温排行」）。
        unit: 数值单位。
        items: 排行条目列表。
        time_text: 展示用时间文本。
        limit: 最多输出条数。
        hour: 时间跨度（1/6/24），用于降水指示器阈值选择。

    Returns:
        文本行列表（不含外层空行分隔）。
    """
    lines: list[str] = []

    if not items:
        lines.append(f"{title} {_normalize_time_text(time_text)}")
        lines.append("暂无数据")
        return lines

    # 站点名列宽度：复用 _format_station_name（空名会替换为「-」），
    # 保证宽度计算与实际渲染一致，避免空站点名时该行错位。
    limit_items = items[:limit]
    station_width = max(
        (
            _display_width(_format_station_name(it.get("name"), it.get("pname")))
            for it in limit_items
        ),
        default=12,
    )

    # 序号区：最多两位（Top10 只到 10），序号顶格后补位到固定 4 显示宽，
    # 使「序号 + 间隔」总宽恒定，地点列精确对齐：
    #   - 1-9 行：`1.`（2列）+ 全角空格（2列）= 4 列
    #   - 10 行：`10.`（3列）+ 半角空格（1列）= 4 列
    # 10 行用单一半角空格补位（不与别的空格连续，QQ 不会折叠），
    # 从而与 1-9 行地点列起点一致，看起来不会"多一个空格"。
    # 每行结构（显示宽度）：
    #   {序号区:4} {emoji:2} {站点:station_width} {数值:6} {单位}
    # 数值右端所在显示宽度 = 4(序号区) + 2(emoji) + station_width + 1(空格) + 6(数值)
    value_right = station_width + 13

    # 标题行：日期文本规范化（连字符两侧加空格）。
    # 目标：日期右端精确对齐到数值列右端（value_right，与下方数值右对齐）；
    # 若「标题 + 2 空格 + 日期」整体宽度超过数值列右端（标题或日期过宽），
    # 则退化为「标题 + 2 空格 + 日期」保底分隔，日期右端随长度自然延伸。
    display_time = _normalize_time_text(time_text)
    date_width = _display_width(display_time)
    title_width = _display_width(title)
    min_gap = 2
    if title_width + min_gap + date_width <= value_right:
        header = (
            _pad_display_width(title, value_right - date_width, align="left")
            + display_time
        )
    else:
        header = f"{title}  {display_time}"
    lines.append(header)

    for idx, it in enumerate(limit_items, 1):
        station_text = _format_station_name(it.get("name"), it.get("pname"))
        value_text = _format_value(it.get("value"), unit=unit)
        # 颜色圆点指示器（气温冷蓝暖红 / 降水绿蓝黄橙红紫 / 风速台风色板）
        emoji = rank_level_emoji(rank_type, it.get("value"), hour=hour)
        # 序号顶格，序号区固定 4 显示宽左对齐：
        idx_text = _pad_display_width(f"{idx}.", 4, align="left")
        padded_station = _pad_display_width(station_text, station_width, align="left")
        # emoji 后接 1 个半角空格分隔，避免与站点名粘连
        lines.append(f"{idx_text}{emoji} {padded_station} {value_text}")

    return lines


def build_rank_blocks(
    *,
    rank_type: str,
    items: list[dict[str, Any]],
    time_text: str,
    limit: int = DEFAULT_LIMIT,
    hour: int = 1,
) -> list[str]:
    """构建排行文本块列表（每时段一个块，供合并转发分节点展示）。

    Args:
        rank_type: 接口 type（maxtemp/rain/wind）。
        items: 接口返回的排行条目列表（可含多个时段，内部按时间分组）。
        time_text: 展示用时间文本（如「2026年08月08日 21时」）。
        limit: 最多输出条数（默认 10）。
        hour: 时间跨度（1/6/24），用于降水指示器阈值选择。

    Returns:
        文本块列表：多时段时每个时段一个块；单时段只有一个块。
    """
    kind_def = next(
        (v for v in _RANK_KIND_DEFS.values() if v["type"] == rank_type),
        None,
    )
    if kind_def is None:
        kind_def = {"label": "排行", "type": rank_type, "unit": "", "title": "排行"}
    title = kind_def["title"]
    unit = kind_def["unit"]

    # 多时段支持：item 自带 time 字段（单个时段）时按时间分组展示。
    # 单个时段（原有形态）直接作为一组。
    if any(str(it.get("time") or "").strip() for it in items):
        grouped: dict[str, list[dict[str, Any]]] = {}
        for it in items:
            grouped.setdefault(str(it.get("time") or "").strip(), []).append(it)
        blocks: list[str] = []
        for grp_time, grp_items in grouped.items():
            grp_time_text = grp_time or time_text
            block_lines = _build_rank_block(
                rank_type=rank_type,
                title=title,
                unit=unit,
                items=grp_items,
                time_text=grp_time_text,
                limit=limit,
                hour=hour,
            )
            blocks.append("\n".join(block_lines))
        return blocks

    block_lines = _build_rank_block(
        rank_type=rank_type,
        title=title,
        unit=unit,
        items=items,
        time_text=time_text,
        limit=limit,
        hour=hour,
    )
    return ["\n".join(block_lines)]


def build_rank_text(
    *,
    rank_type: str,
    items: list[dict[str, Any]],
    time_text: str,
    limit: int = DEFAULT_LIMIT,
    hour: int = 1,
) -> str:
    """构建排行文本（Top10 右对齐输出，每行带颜色圆点指示器）。

    Args:
        rank_type: 接口 type（maxtemp/rain/wind）。
        items: 接口返回的排行条目列表（可含多个时段，内部按时间分组）。
        time_text: 展示用时间文本（如「2026年08月08日 21时」）。
        limit: 最多输出条数（默认 10）。
        hour: 时间跨度（1/6/24），用于降水指示器阈值选择。

    Returns:
        格式化后的多行文本（多时段以空行分隔拼接）。
    """
    return "\n\n".join(
        build_rank_blocks(
            rank_type=rank_type,
            items=items,
            time_text=time_text,
            limit=limit,
            hour=hour,
        )
    )


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
            （四要素统一默认 1h 逐小时档）。
        client: 复用客户端实例；None 时内部新建并自动关闭。

    Returns:
        {"success": True, "text": "...", "time": "...", "raw_items": [...]}
        {"success": False, "error": "..."}
    """
    # 归一化 rank_type
    if rank_type not in RANK_TYPES:
        return {"success": False, "error": f"不支持的排行类型: {rank_type}"}

    # 归一化 hour：未指定时按要素默认跨度（四要素统一默认 1h）
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

    # 确定要抓取的时次列表：
    # - 显式指定时次：只查该时次
    # - 未指定 + 24h 档：默认抓「昨天 08 时」+「昨天 20 时」两个日界时段
    #   （与官网前端固定时段入口一致），输出两个块
    # - 未指定 + 1h/6h 档：取当前整点
    if ymdh:
        ymdh_list = [ymdh]
    elif hour >= 24:
        # 官网 24h 降水按 08/20 两个日界滚动，各覆盖 12 小时。
        # 默认抓昨天 08 时 + 昨天 20 时（完整一个自然日），两个块都返回。
        candidates = [8, 20]
        ymdh_list = [_shift_day_ymdh(hh, -1) for hh in candidates]
    else:
        now = datetime.now(_CST)
        ymdh_list = [f"{now.year:04d}{now.month:02d}{now.day:02d}{now.hour:02d}"]

    # 无参查询（未显式指定时次）时，允许数据未发布时自动回退前 1 小时。
    # 刚过整点时当前整点数据往往还没发布，接口会返回空数据，此时向前回退
    # 最多 AUTO_RETRY_HOURS 次；仅「暂无排行数据」这类空数据才回退。
    # 注意：24h 档默认按 08/20 两个日界整点滚动，不启用逐小时回退，
    # 避免回退到 07/06 等非日界整点，破坏日界整点语义。
    auto_retry = ymdh is None and hour < 24

    owned_client = client is None
    if owned_client:
        client = NmcRealRankClient()

    # 逐时段抓取，聚合 items（带 time 字段用于分组展示）
    all_items: list[dict[str, Any]] = []
    all_time_text: str | None = None
    errors: list[str] = []
    # 多时段查询（如 24h 无参默认昨 08/20 双日界）：任一必需时段失败即整体失败，
    # 避免发送仅含单时段的残缺排行。
    multi_bucket = len(ymdh_list) > 1
    try:
        for ymdh_i in ymdh_list:
            resolved = ymdh_i
            payload = await client.fetch_rank(
                rank_type=rank_type,
                hour=hour,
                ymdh=resolved,
            )
            # 无参查询且当前时次无数据时，自动回退前 1 小时重试。
            retry_used = 0
            while (
                auto_retry
                and not payload.get("success")
                and "暂无" in (payload.get("error") or "")
                and retry_used < AUTO_RETRY_HOURS
            ):
                retry_used += 1
                prev = datetime.strptime(resolved, "%Y%m%d%H") - timedelta(hours=1)
                resolved = prev.strftime("%Y%m%d%H")
                payload = await client.fetch_rank(
                    rank_type=rank_type,
                    hour=hour,
                    ymdh=resolved,
                )
            if not payload.get("success"):
                errors.append(payload.get("error") or f"{ymdh_i} 无数据")
                # 多时段场景任一必需时段失败即中止，整体返回失败。
                if multi_bucket:
                    break
                continue
            items = payload.get("items") or []
            time_text_i = payload.get("format_time") or payload.get("time") or ""
            if not time_text_i:
                time_text_i = resolve_time_text(resolved)
            for it in items:
                item = dict(it)
                item["time"] = time_text_i
                all_items.append(item)
            if all_time_text is None:
                all_time_text = time_text_i
    finally:
        if owned_client and client is not None:
            await client.close()

    if not all_items or (multi_bucket and errors):
        if errors:
            return {
                "success": False,
                "error": "；".join(dict.fromkeys(errors)) or "排行查询失败",
            }
        return {"success": False, "error": "排行查询失败"}

    time_text = all_time_text or ""
    blocks = build_rank_blocks(
        rank_type=rank_type,
        items=all_items,
        time_text=time_text,
        hour=hour,
    )
    return {
        "success": True,
        "text": "\n\n".join(blocks),
        "blocks": blocks,
        "time": time_text,
        "raw_items": all_items,
        # 生效跨度（归一化后），供命令侧埋点记录实际使用的跨度。
        "hour": hour,
    }


# 时次参数帮助文本：集中定义，减少帮助文本与实际解析行为不一致的风险。
TIME_ARG_HELP = (
    "参数格式：\n"
    "· 时间跨度：6小时 / 24小时（缺省逐小时；24小时档返回昨 08/20 两个日界时段）\n"
    "· 时次：MM月DD日HH时 / YYYYMMDDHH / 今天HH时 / 昨天HH时\n"
    "示例：/气温排行、/气温排行 24小时、/降水排行 24小时 08时、/风速排行 昨天15时"
)

__all__ = [
    "resolve_rank_kind",
    "resolve_rank_type",
    "resolve_time_text",
    "parse_time_arg",
    "parse_rank_args",
    "build_rank_text",
    "build_rank_blocks",
    "query_rank",
    "DEFAULT_LIMIT",
    "AUTO_RETRY_HOURS",
    "TIME_ARG_HELP",
]
