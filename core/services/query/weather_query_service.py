"""
气象预警查询服务。
承接旧实现中的查询与文案整理职责，统一供命令侧与接口层复用。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from ....utils.time_converter import TimeConverter
from ...message.presenters.weather_constants import (
    COLOR_LEVEL_EMOJI,
    SORTED_WEATHER_TYPES,
    extract_final_weather_color,
)


def normalize_weather_color(color_token: str | None) -> str | None:
    """规范化预警颜色关键词。"""
    if not color_token:
        return None

    token = color_token.strip()
    if not token:
        return None

    # 对输入的中文字符缩写进行对齐转换
    color_map = {
        "红": "红色",
        "橙": "橙色",
        "黄": "黄色",
        "蓝": "蓝色",
        "白": "白色",
        "红色": "红色",
        "橙色": "橙色",
        "黄色": "黄色",
        "蓝色": "蓝色",
        "白色": "白色",
    }
    return color_map.get(token)


def parse_weather_query_filters(
    token_a: str | None,
    token_b: str | None,
) -> tuple[str | None, str | None]:
    """解析可选参数中的预警类型与预警颜色。

    两个可选参数的位置不固定，因此这里按内容判断其语义。
    """
    weather_type = None
    weather_color = None

    for token in (token_a, token_b):
        if not token:
            continue

        normalized_color = normalize_weather_color(token)
        if normalized_color:
            weather_color = normalized_color
            continue

        # 排除颜色后的普通字符串，默认视作气象灾害类型关键字（如暴雨、台风）
        if weather_type is None:
            weather_type = token.strip()

    return weather_type, weather_color


def parse_event_time_to_utc(time_value: Any) -> datetime | None:
    """将事件时间解析并转换为 UTC。

    若原始时间没有显式时区，则按北京时间处理。
    """
    parsed = TimeConverter.parse_datetime(time_value)
    if parsed is None:
        return None

    # 中国气象预警中心下发的数据默认不带时区，使用北京时间（UTC+8）强制修饰
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TimeConverter._get_timezone("UTC+8"))

    return parsed.astimezone(timezone.utc)


def format_cn_time(dt_utc: datetime | None) -> str:
    """将 UTC 时间格式化为北京时间中文样式。"""
    if dt_utc is None:
        return "未知时间"

    cn_dt = dt_utc.astimezone(TimeConverter._get_timezone("UTC+8"))
    return TimeConverter._safe_strftime(cn_dt, "%Y年%m月%d日 %H时%M分%S秒")


def extract_weather_org(title_text: str, headline_text: str) -> str:
    """提取发布机构。"""
    candidate = (headline_text or title_text or "").strip()
    if not candidate:
        return "未知发布机构"

    # 正则提取发布/更新前缀的单位机构名称（如“北京市气象台”）
    match = re.search(r"^(.+?)(?:发布|更新)", candidate)
    if match:
        return match.group(1)

    # 提取以气象台/气象局/气象站/气象中心结尾的机构名（如"康县气象台"）
    # 覆盖"升级/降级"类预警 headline 无"发布"关键词的场景
    org_match = re.search(
        r"^(.+?(?:气象台|气象局|气象站|气象中心|气象分局))", candidate
    )
    if org_match:
        return org_match.group(1)

    time_match = re.search(
        r"^(.*?)(?:\d{4}年\d{1,2}月\d{1,2}日\d{1,2}时\d{1,2}分(?:\d{1,2}秒)?)$",
        candidate,
    )
    if time_match:
        return time_match.group(1)

    return candidate


def detect_weather_type(title_text: str, weather_type_code: str | None) -> str:
    """识别预警类型。"""
    text = title_text or ""
    # 按预设的 24 种常见气象大类匹配（如雷雨大风、寒潮、道路结冰）
    for weather_type in SORTED_WEATHER_TYPES:
        if weather_type in text:
            return weather_type

    code_text = (weather_type_code or "").strip()
    for weather_type in SORTED_WEATHER_TYPES:
        if weather_type in code_text:
            return weather_type

    return "未知类型"


def detect_weather_color(level_text: str, title_text: str) -> str:
    """识别预警颜色。

    对于"升级为/降级为"类预警，优先取变更后的最终颜色，
    避免因红色优先匹配而返回变更前的旧颜色。
    """
    final_color = extract_final_weather_color(level_text, title_text)
    return final_color if final_color else "未知颜色"


def extract_weather_warning_core(title_text: str) -> str | None:
    """从完整标题中提取“类型+颜色+预警”核心短语。"""
    text = (title_text or "").strip()
    if not text:
        return None

    # 正则截取预警核心段落，用于精简大段啰嗦的标题，如“北京市气象台发布暴雨黄色预警信号” -> “暴雨黄色预警信号”
    tail_match = re.search(
        r"([\u4e00-\u9fffA-Za-z0-9]{1,12}(?:红色|橙色|黄色|蓝色|白色)?预警(?:信号)?)$",
        text,
    )
    if tail_match:
        return tail_match.group(1)

    publish_match = re.search(
        r"发布([\u4e00-\u9fffA-Za-z0-9]{1,12}(?:红色|橙色|黄色|蓝色|白色)?预警(?:信号)?)",
        text,
    )
    if publish_match:
        return publish_match.group(1)

    return None


def build_weather_type_line(
    weather_type: str,
    weather_color: str,
    title_text: str,
) -> str:
    """构建“预警类型”展示文案（仅保留类型信息，不含地区前缀）。"""
    color_emoji = COLOR_LEVEL_EMOJI.get(weather_color, "")

    if weather_type != "未知类型":
        if weather_color != "未知颜色":
            return f"{weather_type}{weather_color}预警{color_emoji}"

        short_title = extract_weather_warning_core(title_text)
        if short_title:
            return f"{short_title}{color_emoji}"

        return f"{weather_type}预警{color_emoji}"

    short_title = extract_weather_warning_core(title_text)
    if short_title:
        return f"{short_title}{color_emoji}"

    return f"未知类型预警{color_emoji}"


def build_weather_list_blocks(items: list[dict[str, Any]]) -> list[str]:
    """将列表项整理为独立文本块（用于合并转发或分段发送）。"""
    blocks: list[str] = []
    for item in items:
        lines = [
            f"发布时间：{item.get('issue_time') or '未知时间'}",
            f"ID：{item.get('alarm_id') or '未知ID'}",
            f"发布机构：{item.get('publish_org') or '未知发布机构'}",
            f"预警类型：{item.get('weather_type_line') or '未知类型预警'}",
        ]
        blocks.append("\n".join(lines))
    return blocks


def chunk_weather_blocks(blocks: list[str], max_chars: int = 1024) -> list[str]:
    """将文本块按长度分组，避免单段过长。"""
    if not blocks:
        return []

    chunks: list[str] = []
    bucket: list[str] = []
    bucket_len = 0

    for block in blocks:
        block_len = len(block)
        # 单块超出预设的最大分片字数限制时，进行切分封装，规避超过 QQ 等通信软件单次字数发送上限而失败
        if bucket and (bucket_len + block_len + 2 > max_chars):
            chunks.append("\n\n".join(bucket))
            bucket = [block]
            bucket_len = block_len
        else:
            bucket.append(block)
            bucket_len += block_len + 2

    if bucket:
        chunks.append("\n\n".join(bucket))

    return chunks


async def query_weather_alarm_data(
    db,
    keyword: str,
    optional_a: str | None = None,
    optional_b: str | None = None,
) -> dict[str, Any]:
    """查询气象预警。

    同时支持按预警标识精确查询，以及按地区、类型、颜色组合筛选近时段记录。
    """
    normalized_keyword = (keyword or "").strip()
    if not normalized_keyword:
        return {
            "success": False,
            "error": "参数不足",
            "usage": [
                "/气象预警查询 <省份/地名> [<预警类型>] [<预警颜色>]",
                "/气象预警查询 全国 [<预警类型>] [<预警颜色>]",
                "/气象预警查询 <预警ID>",
            ],
        }

    # 先识别是否为预警标识查询，命中后直接走精确查找分支。
    id_query = bool(re.match(r"^\d+_\d{12,14}$", normalized_keyword))
    if id_query:
        target_id = normalized_keyword
        # 从本地数据库快速按主键拉取
        matched = await db.find_weather_event_by_alarm_id(target_id)
        if not matched:
            return {
                "success": False,
                "query_mode": "id",
                "error": f"未在本地数据库中找到预警ID为 {target_id} 的气象预警记录。可尝试通过其他官方渠道进行查询",
            }

        title_text = str(matched.get("description") or "").strip()
        headline_text = str(matched.get("subtitle") or "").strip()
        body_text = str(
            matched.get("weather_detail") or matched.get("description") or ""
        ).strip()
        level_text = str(matched.get("level") or "").strip()
        weather_type_code = str(matched.get("weather_type_code") or "").strip()

        detected_type = detect_weather_type(title_text, weather_type_code)
        detected_color = detect_weather_color(level_text, title_text)
        color_emoji = COLOR_LEVEL_EMOJI.get(detected_color, "")

        guideline_text = None
        # 裁剪说明字段提取官方防灾指南部分
        if "防御指南" in body_text:
            guideline_idx = body_text.find("防御指南")
            guideline_text = body_text[guideline_idx:].strip()

        return {
            "success": True,
            "query_mode": "id",
            "data": {
                "alarm_id": target_id,
                "title_text": title_text,
                "headline_text": headline_text,
                "body_text": body_text,
                "level_text": level_text,
                "weather_type_code": weather_type_code,
                "detected_type": detected_type,
                "detected_color": detected_color,
                "color_emoji": color_emoji,
                "guideline_text": guideline_text,
                "icon_url": (
                    f"https://api.fanstudio.tech/we/img/alarm_icon.php?type={weather_type_code}"
                    if weather_type_code
                    else None
                ),
            },
        }

    # 模糊条件搜索分支，最大加载 5000 条事件以限制开销
    weather_events = await db.get_recent_weather_events(limit=5000)
    if not weather_events:
        return {
            "success": False,
            "query_mode": "search",
            "error": "本地数据库中暂无可查询的气象预警历史数据，请稍后重试。也可尝试通过其他官方渠道进行查询",
        }

    location_keyword = normalized_keyword
    query_type, query_color = parse_weather_query_filters(optional_a, optional_b)
    is_nationwide = normalized_keyword in {"全国", "全國"}
    if is_nationwide:
        location_keyword = None

    now_utc = datetime.now(timezone.utc)
    # 普通检索仅保留近 72 小时数据，避免结果过旧且数量过大。
    threshold_utc = now_utc - timedelta(hours=72)

    matched_items = []
    for item in weather_events:
        event_time_utc = parse_event_time_to_utc(item.get("time"))
        # 过滤掉 72 小时前的预警
        if event_time_utc is None or event_time_utc < threshold_utc:
            continue

        title_text = str(item.get("description") or "").strip()
        headline_text = str(item.get("subtitle") or "").strip()
        level_text = str(item.get("level") or "").strip()
        weather_type_code = str(item.get("weather_type_code") or "").strip()
        haystack = f"{title_text} {headline_text}"

        # 行政区域地名碰撞过滤
        if location_keyword and location_keyword not in haystack:
            continue

        detected_type = detect_weather_type(title_text, weather_type_code)
        detected_color = detect_weather_color(level_text, title_text)

        # 气象警报种类过滤
        if query_type and query_type not in haystack and query_type != detected_type:
            continue

        # 警报色彩级别过滤
        if (
            query_color
            and query_color != detected_color
            and query_color not in haystack
        ):
            continue

        matched_items.append(
            {
                "raw": item,
                "event_time_utc": event_time_utc,
                "title_text": title_text,
                "headline_text": headline_text,
                "weather_type": detected_type,
                "weather_color": detected_color,
            }
        )

    # 排序使最新的气象预警记录最先展示
    matched_items.sort(key=lambda entry: entry["event_time_utc"], reverse=True)

    if not matched_items:
        return {
            "success": False,
            "query_mode": "search",
            "error": "未在本地数据库中查询到符合条件的气象预警（仅检索近72小时内数据）。可尝试通过其他官方渠道进行查询",
            "filters": {
                "location": location_keyword or "全国",
                "type": query_type,
                "color": query_color,
            },
        }

    items: list[dict[str, Any]] = []
    for entry in matched_items:
        item = entry["raw"]
        title_text = entry["title_text"]
        headline_text = entry["headline_text"]
        weather_type = entry["weather_type"]
        weather_color = entry["weather_color"]
        weather_type_code = str(item.get("weather_type_code") or "").strip()

        raw_unique_id = str(item.get("unique_id") or "").strip()
        raw_real_event_id = str(item.get("real_event_id") or "").strip()
        display_alarm_id = raw_real_event_id or raw_unique_id
        if not raw_real_event_id and "|" in raw_unique_id:
            display_alarm_id = raw_unique_id.split("|")[-1].strip()

        items.append(
            {
                "issue_time": format_cn_time(entry["event_time_utc"]),
                "alarm_id": display_alarm_id or "未知ID",
                "publish_org": extract_weather_org(title_text, headline_text),
                "weather_type_line": build_weather_type_line(
                    weather_type,
                    weather_color,
                    title_text,
                ),
                "weather_type": weather_type,
                "weather_color": weather_color,
                "title_text": title_text,
                "headline_text": headline_text,
                "weather_type_code": weather_type_code,
                "icon_url": (
                    f"https://api.fanstudio.tech/we/img/alarm_icon.php?type={weather_type_code}"
                    if weather_type_code
                    else None
                ),
            }
        )

    blocks = build_weather_list_blocks(items)
    chunked_blocks = chunk_weather_blocks(blocks, max_chars=900)

    return {
        "success": True,
        "query_mode": "search",
        "filters": {
            "location": location_keyword or "全国",
            "type": query_type,
            "color": query_color,
            "time_window_hours": 72,
        },
        "items": items,
        "text_blocks": chunked_blocks,
        "total": len(items),
        "is_nationwide": is_nationwide,
    }
