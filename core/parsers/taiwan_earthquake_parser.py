"""
台湾地震报告解析器。
负责把台湾中央气象署地震报告数据统一转换为领域事件。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from ...utils.converters import safe_float_convert
from ...utils.plugin_logger import plugin_logger
from ..domain.event_identity import EventIdentity
from ..domain.event_models import EarthquakeEvent, EventEnvelope
from ..domain.event_payload import SourcePayload
from ..sources.source_catalog import get_source_entry
from .base_parser import BaseParser

# 上游曾长期复用的占位/污染 id，不可单独作为稳定事件键
_POISON_REPORT_IDS = frozenset(
    {
        "",
        "0",
        "115000",
        "null",
        "none",
        "undefined",
        "unknown",
    }
)


class CwaReportParser(BaseParser):
    """台湾中央气象署地震报告解析器，处理 FAN Studio 来源数据。"""

    def __init__(self, message_logger=None):
        """初始化台湾地震报告解析器。"""
        super().__init__("cwa_fanstudio_report", message_logger)

    @staticmethod
    def _normalize_shock_key(shock_time: Any) -> str:
        text = str(shock_time or "").strip()
        if not text:
            return ""
        # 压缩空白与常见分隔，保证同一发震时刻生成稳定键
        text = text.replace("T", " ")
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _extract_image_token(image_uri: Any) -> str:
        """从报告图 URL 提取尽量稳定的文件名 token（常含时间+编号）。"""
        raw = str(image_uri or "").strip()
        if not raw:
            return ""
        try:
            path = unquote(urlparse(raw).path or "")
        except Exception:
            path = raw
        name = path.rsplit("/", 1)[-1].strip()
        if not name:
            return ""
        # 去掉扩展名与常见后缀
        name = re.sub(r"\.(png|jpg|jpeg|gif|webp)$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"(_H|_h|_M|_m)$", "", name)
        # 仅保留安全字符
        name = re.sub(r"[^0-9A-Za-z._-]+", "", name)
        return name[:80]

    @classmethod
    def _is_poison_report_id(cls, report_id: str) -> bool:
        value = str(report_id or "").strip().lower()
        return value in _POISON_REPORT_IDS

    def _build_stable_event_id(
        self, msg_data: dict[str, Any]
    ) -> tuple[str, tuple[str, ...]]:
        """构建 CWA 地震报告稳定事件 ID。

        CWA 报告是「一场地震一份报告」，不是 EEW 多报。
        上游 id 偶发复用（如 115000）时，必须叠加发震时间/位置/图片 token，
        避免多场地震被错误合并。
        """
        raw_id = str(msg_data.get("id") or msg_data.get("eventId") or "").strip()
        shock_key = self._normalize_shock_key(msg_data.get("shockTime"))
        latitude = safe_float_convert(msg_data.get("latitude"))
        longitude = safe_float_convert(msg_data.get("longitude"))
        magnitude = safe_float_convert(msg_data.get("magnitude"))
        place_name = str(msg_data.get("placeName") or "").strip()
        image_token = self._extract_image_token(msg_data.get("imageURI"))

        lat_part = f"{latitude:.3f}" if latitude is not None else "na"
        lon_part = f"{longitude:.3f}" if longitude is not None else "na"
        mag_part = f"{magnitude:.1f}" if magnitude is not None else "na"
        place_part = re.sub(r"\s+", "", place_name)[:40] if place_name else ""

        aliases: list[str] = []
        if raw_id:
            aliases.append(raw_id)
        if image_token:
            aliases.append(image_token)

        # 1) 图片 token 通常最接近官方单次报告唯一码
        if image_token and len(image_token) >= 8:
            event_id = f"cwa_report_{image_token}"
            if shock_key:
                event_id = f"{event_id}_{shock_key}"
            return event_id, tuple(dict.fromkeys(aliases))

        # 2) 原始 id 可用时，仍叠加发震时间，防止 id 复用再次串味
        if raw_id and not self._is_poison_report_id(raw_id):
            if shock_key:
                event_id = f"{raw_id}_{shock_key}"
            else:
                event_id = f"{raw_id}_{lat_part}_{lon_part}_{mag_part}"
            return event_id, tuple(dict.fromkeys(aliases))

        # 3) 毒 id / 缺失 id：纯物理指纹回退
        parts = ["cwa_report"]
        if shock_key:
            parts.append(shock_key)
        parts.extend([lat_part, lon_part, mag_part])
        if place_part:
            parts.append(place_part)
        event_id = "_".join(parts)
        if raw_id:
            # 保留毒 id 仅作别名，便于排查，不作为主键
            aliases.append(raw_id)
        return event_id, tuple(dict.fromkeys(item for item in aliases if item))

    def _build_envelope(self, msg_data: dict[str, object]) -> EventEnvelope:
        """把台湾地震报告原始字典封装为统一事件包裹体。"""
        # 读取静态数据源配置
        source_entry = get_source_entry(self.source_id)

        # 报告图与震度图链接保留在元数据中，供后续媒体展示链复用。
        metadata = {
            "source_family": "fan_studio",
            "source_enum": source_entry.source_enum if source_entry else "",
            "source_type": source_entry.source_type.value
            if source_entry
            else "earthquake_info",
            "image_uri": msg_data.get("imageURI"),
            "shakemap_uri": msg_data.get("shakemapURI"),
        }
        event_id, aliases = self._build_stable_event_id(msg_data)
        raw_upstream_id = str(msg_data.get("id") or "").strip()
        if raw_upstream_id:
            metadata["upstream_report_id"] = raw_upstream_id
        metadata["stable_event_id"] = event_id

        # 实例化统一的地震数据模型，提取震级、深度、震中地点及发震时间
        domain_event = EarthquakeEvent(
            occurred_at=self._parse_datetime(msg_data.get("shockTime", "")),
            latitude=safe_float_convert(msg_data.get("latitude")),
            longitude=safe_float_convert(msg_data.get("longitude")),
            place_name=str(msg_data.get("placeName", "") or ""),
            magnitude=safe_float_convert(msg_data.get("magnitude")),
            depth=safe_float_convert(msg_data.get("depth")),
            metadata=dict(metadata),
        )

        # 构造并注入事件全局唯一的身份模型
        identity = EventIdentity(
            event_id=event_id,
            source_id=self.source_id,
            event_type="earthquake",
            provider_family=source_entry.provider_family.value
            if source_entry
            else "fan_studio",
            source_enum=source_entry.source_enum if source_entry else "",
            published_at=domain_event.occurred_at,
            aliases=aliases,
            attributes={
                "parser_name": self.source_entry.parser_name
                if self.source_entry
                else "",
                "config_key": source_entry.config_key if source_entry else "",
                "upstream_report_id": raw_upstream_id,
            },
        )

        # 最终组装为 EventEnvelope 返回给 Ingress 消息路由层
        return EventEnvelope(
            identity=identity,
            event=domain_event,
            received_at=datetime.now(timezone.utc),
            payload=SourcePayload(
                source_id=self.source_id,
                provider_family=source_entry.provider_family.value
                if source_entry
                else "fan_studio",
                message_type=str(msg_data.get("type") or "update").strip(),
                raw=dict(msg_data),
                attributes=dict(metadata),
            ),
            metadata=metadata,
        )

    def _parse_data(self, data: dict[str, object]) -> EventEnvelope | None:
        """解析台湾中央气象署地震报告数据。"""
        try:
            msg_data = self._extract_data(data)
            if not msg_data:
                plugin_logger.warning(f"[灾害预警] {self.source_id} 消息中没有有效数据")
                return None

            # 台湾地震报告类消息至少应具备发震时间与报告图片地址，否则通常不是来自 CWA 的地震报告
            if "shockTime" not in msg_data or "imageURI" not in msg_data:
                plugin_logger.debug(
                    f"[灾害预警] {self.source_id} 非 CWA 地震报告数据，跳过"
                )
                return None

            envelope = self._build_envelope(msg_data)
            # 在外层元数据中附加媒体字段，保证主流水线取值正常
            envelope.metadata.update(
                {
                    "image_uri": msg_data.get("imageURI"),
                    "shakemap_uri": msg_data.get("shakemapURI"),
                }
            )

            domain_event = envelope.event
            plugin_logger.info(
                f"[灾害预警] CWA 地震报告解析成功: {getattr(domain_event, 'place_name', '')} "
                f"(M {getattr(domain_event, 'magnitude', None)}), "
                f"时间: {getattr(domain_event, 'occurred_at', None)}, "
                f"ID: {getattr(getattr(envelope, 'identity', None), 'event_id', '')}",
                is_event_linked=True,
            )
            return envelope
        except Exception as exc:
            plugin_logger.error(f"[灾害预警] {self.source_id} 解析数据失败: {exc}")
            return None
