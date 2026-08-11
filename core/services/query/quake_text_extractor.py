"""
地震动预测文本参数提取器。

负责从用户引用的地震文本中提取震级、震源深度、震中经纬度与发震时间，供「地震动预测」引用模式复用。

实现说明：
- 仅依赖 AstrBot 公开 API（astrbot.api.message_components.Reply），
  不触碰任何 astrbot.core 内部实现，保证跨版本兼容；
- Reply 组件由各平台适配器统一产出，自带 chain（被引用消息段）与
  message_str（解析后的纯文本），插件侧零维护；
- 提取规则：多组正则覆盖 CENC 速报、插件自身 CencEarthquakePresenter
  输出、JMA/台湾情报等常见格式；
- 容错：任一字段缺失都允许（调用方按可选项处理），但至少需要震级或震中之一。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# 仅使用公开 API：Reply 组件（各平台适配器统一产出，含引用内容）
from astrbot.api.message_components import Plain, Reply

# 引用消息通常为北京时间（UTC+8）
_BEIJING_TZ = timezone(timedelta(hours=8))


@dataclass(slots=True)
class QuotedQuakeParams:
    """从引用文本提取到的地震参数。"""

    magnitude: float | None = None
    depth_km: float | None = None
    lat: float | None = None
    lon: float | None = None
    occurred_at: datetime | None = None
    place_name: str = ""
    # 原始命中字段列表，便于调试与提示
    matched_fields: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """是否具备可预测的最小参数（至少震级 + 震中）。"""
        if self.magnitude is None or self.lat is None or self.lon is None:
            return False
        return True


# ---------------------------------------------------------------------------
# 正则规则
# ---------------------------------------------------------------------------

# 震级：M4.2 / M 4.2 / Mw6.1 / 震级：4.2 / 震级 M 4.2
_RE_MAGNITUDE = re.compile(r"(?:M|M\s?w|Ms|ML|Mw|震级)\s*[:：]?\s*(\d+(?:\.\d+)?)")

# 深度：8.00km / 8 km / 深度：8km / 深度 10 千米
_RE_DEPTH = re.compile(
    r"(?:深度|深さ)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:km|KM|千米|公里)?"
)

# 经纬度（N/S/E/W 后缀）：39.32°N, 114.39°E / 39.32N 114.39E / 39.32 N, 114.39 E
_RE_COORDS = re.compile(
    r"(?P<lat>-?\d+(?:\.\d+)?)\s*°?\s*(?P<lat_dir>[NS])\s*[,，]?\s*"
    r"(?P<lon>-?\d+(?:\.\d+)?)\s*°?\s*(?P<lon_dir>[EW])"
)

# 发震时间：2025年12月22日 20时31分18秒
_RE_TIME_CN = re.compile(
    r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})时(\d{1,2})分(?:(\d{1,2}))?秒?"
)
# 发震时间：2025-12-22 20:31:18 / 2025/12/22 20:31
_RE_TIME_ISO = re.compile(
    r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?"
)

# 地点名：山西大同市灵丘县（在"震中："或"区划："后）
_RE_PLACE = re.compile(r"(?:震中|区划|地点|震源)\s*[:：]?\s*([^\n（(]+)")


def _match_time(m: re.Match[str]) -> datetime:
    """将正则匹配组转换为带 UTC+8 时区的时间。"""
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour, minute = int(m.group(4)), int(m.group(5))
    second = int(m.group(6)) if m.group(6) else 0
    return datetime(year, month, day, hour, minute, second, tzinfo=_BEIJING_TZ)


def extract_params_from_text(text: str) -> QuotedQuakeParams | None:
    """从文本提取地震参数。

    Args:
        text: 文本（如 bot 推送的内容）。

    Returns:
        提取到的参数；文本为空或完全不匹配时返回 None。
    """
    if not text or not str(text).strip():
        return None

    raw = str(text)
    params = QuotedQuakeParams()

    # 震级
    m = _RE_MAGNITUDE.search(raw)
    if m:
        params.magnitude = float(m.group(1))
        params.matched_fields.append("magnitude")

    # 深度
    m = _RE_DEPTH.search(raw)
    if m:
        params.depth_km = float(m.group(1))
        params.matched_fields.append("depth")

    # 经纬度
    m = _RE_COORDS.search(raw)
    if m:
        lat = float(m.group("lat"))
        lon = float(m.group("lon"))
        if m.group("lat_dir").upper() == "S":
            lat = -lat
        if m.group("lon_dir").upper() == "W":
            lon = -lon
        params.lat = lat
        params.lon = lon
        params.matched_fields.append("coords")

    # 发震时间（中文优先，ISO 次之）
    m = _RE_TIME_CN.search(raw)
    if m:
        params.occurred_at = _match_time(m)
        params.matched_fields.append("time_cn")
    else:
        m = _RE_TIME_ISO.search(raw)
        if m:
            params.occurred_at = _match_time(m)
            params.matched_fields.append("time_iso")

    # 地点名
    m = _RE_PLACE.search(raw)
    if m:
        params.place_name = m.group(1).strip()
        params.matched_fields.append("place")

    if not params.matched_fields:
        return None
    return params


def _chain_to_text(chain: list[Any] | None) -> str | None:
    """将被引用消息段列表拼接为纯文本（仅取 Plain 文本）。"""
    if not chain:
        return None
    parts: list[str] = []
    for comp in chain:
        if isinstance(comp, Plain):
            text = getattr(comp, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip() or None


async def extract_quoted_quake_params(event: Any) -> QuotedQuakeParams | None:
    """从事件（含引用消息）提取地震参数。

    仅依赖公开 API：
    1. 在事件消息链中查找 Reply 组件；
    2. 优先读 Reply.message_str（平台适配器已解析好的纯文本）；
    3. 回退遍历 Reply.chain 中的 Plain 段拼接文本；
    4. 再回退到事件自身消息链文本（无引用时）。

    Args:
        event: AstrMessageEvent。

    Returns:
        提取到的参数；无法提取时返回 None。
    """
    text = _extract_reply_text(event)
    if not text:
        # 无引用时回退事件自身文本（如直接粘贴速报内容）
        text = _extract_event_text(event)
    if not text:
        return None
    return extract_params_from_text(text)


def _extract_reply_text(event: Any) -> str | None:
    """从事件消息链中提取被引用消息的纯文本（仅公开 API）。"""
    message_obj = getattr(event, "message_obj", None)
    message = getattr(message_obj, "message", None)
    if not message:
        return None
    for comp in message:
        if isinstance(comp, Reply):
            # 平台已解析好的纯文本优先
            message_str = getattr(comp, "message_str", None)
            if message_str:
                return str(message_str).strip() or None
            # 回退遍历被引用消息段
            chain_text = _chain_to_text(getattr(comp, "chain", None))
            if chain_text:
                return chain_text
    return None


def _extract_event_text(event: Any) -> str | None:
    """从事件自身消息链中拼接纯文本（兜底，无引用时用）。"""
    message_obj = getattr(event, "message_obj", None)
    message = getattr(message_obj, "message", None)
    if not message:
        return None
    parts: list[str] = []
    for comp in message:
        if isinstance(comp, Plain):
            text = getattr(comp, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip() or None


__all__ = [
    "QuotedQuakeParams",
    "extract_params_from_text",
    "extract_quoted_quake_params",
]
