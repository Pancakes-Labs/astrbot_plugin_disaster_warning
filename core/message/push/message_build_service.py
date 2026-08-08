"""
消息构建服务。
负责文本消息、卡片、地图、远程媒体图件等消息内容拼装，
进一步收敛 MessagePushManager 中的构建职责。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
from datetime import timezone
from typing import Any
from urllib.parse import urlparse

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import MessageChain

from ....utils.time_converter import TimeConverter
from ...domain.event_models import (
    EarthquakeEvent,
    EventEnvelope,
    TsunamiEvent,
    TyphoonEvent,
    WeatherEvent,
)
from ...services.identity.event_identity import resolve_report_num
from ...sources.source_catalog import get_source_ids_by_type
from ...sources.source_entry import SourceType
from ...storage.source_compat import is_cenc_intensity_report
from ..presenters.weather_alarm_code_map import (
    build_weather_icon_url,
    resolve_local_weather_icon_abs_path,
    resolve_p_code_color,
    resolve_weather_icon_code,
)
from ..render.beachball_renderer import BeachballRenderer


class MessageBuildService:
    """消息构建服务。"""

    TSUNAMI_MEDIA_KEYS: tuple[str, ...] = ("earthquake", "amplitude", "coastal")

    # 气象预警颜色 → 本地回退图标路径映射。
    # 当 Fan Studio 官方图标接口无法返回有效图标时，根据编码中的颜色后缀回退到通用图标。
    _WEATHER_ICON_FALLBACK_MAP: dict[str, str] = {
        "blue": "resources/weatheralarm_logo/fallback_blue.png",
        "yellow": "resources/weatheralarm_logo/fallback_yellow.png",
        "orange": "resources/weatheralarm_logo/fallback_orange.png",
        "red": "resources/weatheralarm_logo/fallback_red.png",
    }

    # 紧凑 11B 编码末两位颜色映射（如 11B3102 / 11B2002）：
    # 01=蓝, 02=黄, 03=橙, 04=红。与事件 ID 尾部编码保持一致。
    _COMPACT_11B_COLOR_MAP: dict[str, str] = {
        "01": "blue",
        "02": "yellow",
        "03": "orange",
        "04": "red",
    }

    # 中文/英文颜色关键词 → 英文颜色键。
    # 仅保留双字中文颜色词，避免“黄/蓝/红”等单字命中地名（黄河/蓝田/红河）。
    _CN_COLOR_HINT_MAP: dict[str, str] = {
        "蓝色": "blue",
        "黄色": "yellow",
        "橙色": "orange",
        "红色": "red",
        "blue": "blue",
        "yellow": "yellow",
        "orange": "orange",
        "red": "red",
    }

    def __init__(self, manager):
        # 通过主消息管理器复用构建器、发送器、缓存和配置能力。
        self.manager = manager  # 主消息管理器 MessagePushManager 实例

    @staticmethod
    def _get_envelope(event: EventEnvelope):
        """统一获取领域 envelope。"""
        return event

    @staticmethod
    def _get_domain_event(event: EventEnvelope):
        """统一获取领域事件。"""
        return event.event

    @staticmethod
    def _get_event_metadata(event: EventEnvelope) -> dict[str, Any]:
        """统一获取事件 metadata 视图。"""
        envelope = event
        domain_event = event.event

        merged: dict[str, Any] = {}
        domain_metadata = getattr(domain_event, "metadata", None)
        if isinstance(domain_metadata, dict):
            merged.update(domain_metadata)

        metadata = getattr(envelope, "metadata", None)
        if isinstance(metadata, dict):
            merged.update(metadata)

        return merged

    @staticmethod
    def _get_split_map_source_ids() -> set[str]:
        """返回需要分离发送地图的地震预警来源集合（如 EEW 数据源排除 global_quake）。"""
        return set(get_source_ids_by_type(SourceType.EARTHQUAKE_WARNING)) - {
            "global_quake"
        }

    @staticmethod
    def _resolve_source_id(event: EventEnvelope) -> str:
        """统一解析执行路径中的 source_id。"""
        return (getattr(event, "source_id", "") or "").strip()

    @staticmethod
    def _build_snet_map_cache_key(
        stations: list[Any],
        timestamp: str,
    ) -> str:
        """构建 S-Net 测站图缓存键（timestamp + 测站震度指纹）。"""
        rows: list[tuple[str, float]] = []
        for item in stations:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            try:
                shindo = round(float(item.get("shindo", -999.0)), 3)
            except (TypeError, ValueError):
                shindo = -999.0
            rows.append((name, shindo))
        rows.sort(key=lambda x: x[0])
        # 全量排序指纹：同分钟同分布可跨推送/查询复用渲染结果
        fingerprint = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        return f"snet_map|{timestamp}|{fingerprint}"

    @staticmethod
    def _build_typhoon_map_cache_key(
        data: dict[str, Any] | TyphoonEvent,
        *,
        map_source: str,
        playwright_mode: str,
    ) -> str:
        """构建台风路径图缓存键（编号 + 轨迹指纹 + 地图源）。"""
        if isinstance(data, TyphoonEvent):
            typhoon_id = str(data.typhoon_id or "")
            history = list(data.history_track or [])
            future = list(data.future_track or [])
            updated_at = str(data.updated_at or "")
        else:
            typhoon_id = str(data.get("typhoon_id") or data.get("eqsc_id") or "")
            history = list(data.get("history_track") or [])
            future = list(data.get("future_track") or [])
            updated_at = str(
                data.get("updated_at") or data.get("updated_at_text") or ""
            )

        def _track_fingerprint(nodes: list[Any]) -> list[tuple[Any, ...]]:
            rows: list[tuple[Any, ...]] = []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                rows.append(
                    (
                        str(node.get("time") or ""),
                        node.get("latitude")
                        if node.get("latitude") is not None
                        else node.get("lat"),
                        node.get("longitude")
                        if node.get("longitude") is not None
                        else node.get("lon"),
                        node.get("windSpeed")
                        if node.get("windSpeed") is not None
                        else node.get("wind_speed"),
                        node.get("pressure"),
                        str(
                            node.get("typeNameCN")
                            or node.get("type")
                            or node.get("level")
                            or ""
                        ),
                    )
                )
            return rows

        key_obj = {
            "type": "typhoon_map",
            "typhoon_id": typhoon_id,
            "updated_at": updated_at,
            "history": _track_fingerprint(history),
            "future": _track_fingerprint(future),
            "map_source": map_source or "PetalMap矢量图暗",
            "playwright_mode": playwright_mode or "local",
        }
        return json.dumps(key_obj, sort_keys=True, ensure_ascii=False, default=str)

    @staticmethod
    def _build_map_cache_key(
        lat: float,
        lon: float,
        config: dict[str, Any],
        event_caption: str = "",
    ) -> str:
        """构建地图渲染缓存键。"""
        key_obj = {
            "type": "map",
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "map_source": config.get("map_source", "PetalMap矢量图亮"),
            "map_zoom_level": config.get("map_zoom_level", 5),
            "playwright_mode": config.get("playwright_mode", "local"),
            "event_caption": event_caption or "",
        }
        return json.dumps(key_obj, sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _build_event_map_caption(event: EventEnvelope) -> str:
        """构建通用地图左上角的事件基础描述文字胶囊。

        格式：时间 震中 震级
        - 普通地震：如 "05时29分 岩手県冲 M 3.7"
        - 日本震度速报（缺震级/震中）：如 "02时41分 震源 調查中"
        - 中国烈度速报：时间更详细，如 "2026-08-07 13:08 四川宜宾市高县 M 4.9"
        """
        domain_event = getattr(event, "event", None)
        if not isinstance(domain_event, EarthquakeEvent):
            return ""

        source_id = (getattr(event, "source_id", "") or "").strip()
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        domain_metadata = (
            domain_event.metadata if isinstance(domain_event.metadata, dict) else {}
        )
        merged_metadata = dict(domain_metadata)
        merged_metadata.update(metadata)
        info_type = str(merged_metadata.get("info_type") or "").strip()
        # 烈度速报：时间格式更详细
        is_cenc_ir = is_cenc_intensity_report(source_id, info_type=info_type)
        # 震度速报：日本 P2P ScalePrompt 或 info_type 含"震度速报"，缺震级/震中
        is_shindo_report = info_type in ("ScalePrompt", "震度速报") or (
            "震度速报" in info_type
        )

        # 时间格式化：烈度速报显示完整日期时间，其余显示 "HH时MM分"
        occurred_at = getattr(domain_event, "occurred_at", None)
        if occurred_at is not None:
            try:
                # naive datetime 按 UTC 兜底处理，避免 astimezone 误用服务器本地时区
                local_dt = occurred_at
                if local_dt.tzinfo is None:
                    local_dt = local_dt.replace(tzinfo=timezone.utc)
                fmt = "%Y-%m-%d %H:%M" if is_cenc_ir else "%H时%M分"
                time_part = TimeConverter._safe_strftime(
                    local_dt.astimezone(TimeConverter._get_timezone("UTC+8")),
                    fmt,
                )
            except Exception:
                time_part = ""
        else:
            time_part = ""

        # 震中：有真实地名优先；震度速报用"震源 調查中"，其余兜底"未知地点"
        place_name = str(getattr(domain_event, "place_name", "") or "").strip()
        has_place = bool(place_name) and place_name not in ("未知地点", "未知位置")
        if has_place:
            place_text = place_name
        elif is_shindo_report:
            place_text = "震源 調查中"
        else:
            place_text = "未知地点"

        # 震级：有值保留一位小数；震度速报缺震级时不重复输出（"震源 調查中"已统一表达）；
        # 其余兜底用通用文案"未知震级"
        magnitude = getattr(domain_event, "magnitude", None)
        magnitude_text = ""
        try:
            mag_float = float(magnitude) if magnitude is not None else None
        except (TypeError, ValueError):
            mag_float = None
        if mag_float is not None and mag_float > 0:
            # 用窄空格（U+2009 THIN SPACE）连接 M 与震级：
            # 保留可读留白但比普通空格紧凑，避免视觉空隙过大。
            magnitude_text = f"M\u2009{mag_float:.1f}"
        elif is_shindo_report:
            magnitude_text = ""
        else:
            magnitude_text = "未知震级"

        parts = [p for p in (time_part, place_text, magnitude_text) if p]
        return " ".join(parts)

    @staticmethod
    def _build_global_quake_card_cache_key(
        earthquake: EventEnvelope,
        message_format_config: dict[str, Any],
        display_timezone: str,
    ) -> str:
        """构建 Global Quake 卡片缓存键。"""
        payload = earthquake.payload
        metadata = earthquake.metadata if isinstance(earthquake.metadata, dict) else {}
        identity = earthquake.identity
        domain_event = earthquake.event
        # 只有地震事件才能生成这类卡片缓存键，其他事件类型直接视为调用错误。
        if not isinstance(domain_event, EarthquakeEvent):
            raise TypeError("Global Quake card cache key requires EarthquakeEvent")

        report_num = resolve_report_num(earthquake) or 1
        payload_marker = (
            payload.raw.get("id")
            if isinstance(payload, object)
            and hasattr(payload, "raw")
            and isinstance(payload.raw, dict)
            else None
        )
        key_obj = {
            "type": "global_quake_card",
            "event_id": getattr(identity, "event_id", "")
            or getattr(domain_event, "place_name", "")
            or "unknown_event",
            "report_num": report_num,
            "occurred_at": domain_event.occurred_at.isoformat()
            if getattr(domain_event, "occurred_at", None)
            else None,
            "latitude": domain_event.latitude,
            "longitude": domain_event.longitude,
            "magnitude": domain_event.magnitude,
            "depth": domain_event.depth,
            "intensity": domain_event.intensity,
            "place_name": domain_event.place_name,
            "max_pga": metadata.get("max_pga"),
            "stations": metadata.get("stations"),
            "payload_marker": payload_marker,
            "template": message_format_config.get("global_quake_template", "Aurora"),
            "map_source": message_format_config.get("map_source", "PetalMap矢量图亮"),
            "map_zoom_level": message_format_config.get("map_zoom_level", 5),
            "playwright_mode": message_format_config.get("playwright_mode", "local"),
            "timezone": display_timezone,
        }
        return json.dumps(key_obj, sort_keys=True, ensure_ascii=False)

    # 气象预警图标接口返回伪图片（HTTP 200 + content_type=image/png 但实际为 HTML）的特征关键词。
    # 上游 Fan Studio 图标接口对不存在的编码会返回 HTML 错误页而非真实图片，
    # 这类失败稳定触发，降级为 DEBUG 避免控制台刷屏。
    _PSEUDO_IMAGE_ERROR_MARKERS: tuple[str, ...] = (
        "响应体不是有效图片",
        "响应类型不是图片",
    )

    async def _append_remote_image_component(
        self,
        chain: MessageChain,
        image_url: str,
        *,
        media_label: str,
        allow_url_fallback: bool = True,
    ) -> bool:
        """将远程图片优先转为 Base64 附加到消息链，失败时可回退 URL。"""
        normalized_url = image_url.strip()
        # 这里只接受标准网络图片地址，避免把本地路径或其他协议误当成远程图件抓取。
        if not (
            isinstance(normalized_url, str)
            and (
                normalized_url.startswith("http://")
                or normalized_url.startswith("https://")
            )
        ):
            logger.debug(
                f"[灾害预警] 跳过非 HTTP 图片链接 ({media_label}): {normalized_url}"
            )
            return False

        # 调用抓取服务拉取远程图片二进制数据
        fetch_result = await self.manager.fetch_remote_media(
            normalized_url,
            expected_kind="image",
        )
        if fetch_result and fetch_result.get("data"):
            try:
                # 转换为 Base64 结构插入消息链
                b64_data = base64.b64encode(fetch_result["data"]).decode()
                chain.chain.append(Comp.Image.fromBase64(b64_data))
                return True
            except Exception as e:
                logger.warning(
                    "[灾害预警] 远程图片转Base64失败 "
                    f"({media_label}): source={fetch_result.get('source_url')}, final={fetch_result.get('final_url')}, "
                    f"content_type={fetch_result.get('content_type')}, bytes={fetch_result.get('bytes')}, error={type(e).__name__}: {e}"
                )

        if fetch_result:
            error_msg = str(fetch_result.get("error") or "")
            # 特征识别：气象预警图标接口返回伪图片（HTML 错误页伪装为 image/png），
            # 这类失败稳定触发，降级为 DEBUG 避免控制台刷屏。
            is_pseudo_image = media_label == "气象预警图标" and any(
                marker in error_msg for marker in self._PSEUDO_IMAGE_ERROR_MARKERS
            )
            if is_pseudo_image:
                logger.debug(
                    f"[灾害预警] 气象预警图标接口返回伪图片，将走本地回退: "
                    f"编码相关 URL：{normalized_url}, 错误信息：{error_msg}"
                )
            else:
                logger.warning(
                    "[灾害预警] 远程图片抓取失败 "
                    f"({media_label}): source={fetch_result.get('source_url')}, final={fetch_result.get('final_url')}, "
                    f"status={fetch_result.get('status')}, content_type={fetch_result.get('content_type')}, "
                    f"content_length={fetch_result.get('content_length')}, bytes={fetch_result.get('bytes')}, "
                    f"error={fetch_result.get('exception_type') or 'FetchError'}: {fetch_result.get('error')}"
                )

        # 抓取或 Base64 转换失败后，若开启 URL 回退，则尝试利用 URL 方式插入图片组件
        if allow_url_fallback:
            try:
                chain.chain.append(Comp.Image.fromURL(normalized_url))
                return True
            except Exception as e:
                parsed = urlparse(normalized_url)
                logger.warning(
                    "[灾害预警] 远程图片URL回退发送失败 "
                    f"({media_label}): scheme={parsed.scheme}, host={parsed.netloc}, url={normalized_url}, error={type(e).__name__}: {e}"
                )
        return False

    def build_message(self, event: EventEnvelope) -> MessageChain:
        """构建消息（同步单文本）。"""
        # 同步构建路径只生成文本消息，适用于不需要额外附件的轻量场景。
        source_id = self._resolve_source_id(event)
        message_format_config = self.manager.config.get("message_format", {})
        return self.manager.text_message_builder.build(
            event,
            source_id,
            message_format_config,
        )

    async def build_message_async(
        self,
        event: EventEnvelope,
        runtime_config: dict[str, Any] | None = None,
    ) -> MessageChain:
        """构建异步消息，支持卡片、地图和图件附件。"""
        active_config = runtime_config or self.manager.config
        source_id = self._resolve_source_id(event)
        message_format_config = active_config.get("message_format", {})

        # 若当前事件满足 Global Quake 卡片条件，则优先直接返回整张卡片消息（包含内置地图和指标图）。
        global_quake_card = await self._try_build_global_quake_card(
            event,
            source_id=source_id,
            active_config=active_config,
            message_format_config=message_format_config,
        )
        if global_quake_card is not None:
            return global_quake_card

        # 否则常规构建普通文本消息
        chain = self.manager.text_message_builder.build(
            event,
            source_id,
            message_format_config,
            full_config=active_config,
        )

        # S-Net 测站分布图（替代通用地图）
        await self._append_snet_map_if_needed(chain, event, source_id=source_id)
        # 台风路径图（EQSC 富化轨迹）
        await self._append_typhoon_map_if_needed(
            chain,
            event,
            message_format_config=message_format_config,
        )
        # 地图渲染与插入（S-Net、CMT 跳过，避免重复或误附加通用地图）
        if source_id not in ("snet_msil", "fssn_cmt_fanstudio"):
            await self._append_map_if_needed(
                chain,
                event,
                source_id=source_id,
                message_format_config=message_format_config,
            )
        # 附加 CMT 震源球 (Beachball)
        await self._append_fssn_cmt_beachball_if_needed(
            chain, event, source_id=source_id
        )
        # 气象警报图标附加
        await self._append_weather_icon_if_needed(chain, event, active_config)
        # 海啸观测与预报图附加
        await self._append_tsunami_media_if_needed(chain, event)
        # 台湾正式地震报告图片与等震度图附加
        await self._append_cwa_report_media_if_needed(chain, event, source_id)
        return chain

    async def push_split_map(
        self,
        event: EventEnvelope,
        target_sessions: list[str],
        config: dict[str, Any],
    ) -> None:
        """后台渲染并发送分离的地图图片。"""
        try:
            domain_event = self._get_domain_event(event)
            if not isinstance(domain_event, EarthquakeEvent):
                return

            lat, lon = domain_event.latitude, domain_event.longitude
            # 地图渲染前先校验经纬度，避免无效坐标导致浏览器渲染报错。
            if (
                lat is None
                or lon is None
                or not (-90 <= lat <= 90)
                or not (-180 <= lon <= 180)
            ):
                return

            # 调用 Playwright 动态渲染地图图片（带事件描述文字胶囊）
            event_caption = self._build_event_map_caption(event)
            map_image_path = await self.render_map_image(
                lat, lon, config, event_caption=event_caption
            )
            if not map_image_path:
                return

            # 以 Base64 读取渲染后的图片数据并封装为消息链
            with open(map_image_path, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode()

            map_message = MessageChain([Comp.Image.fromBase64(b64_data)])
            # 循环向各个订阅了地图分离的会话推送地图图片消息
            for session in target_sessions:
                session_log = self.manager._get_session_log_str(session)
                try:
                    await self.manager.session_sender.send(session, map_message)
                    logger.debug(f"[灾害预警] 分离地图已发送到 {session_log}")
                except Exception as e:
                    logger.error(f"[灾害预警] 分离地图发送到 {session_log} 失败: {e}")
        except Exception as e:
            logger.error(f"[灾害预警] 异步地图渲染任务失败: {e}")

    async def render_map_image(
        self,
        lat: float,
        lon: float,
        config: dict,
        event_caption: str = "",
    ) -> str | None:
        """渲染通用地图图片（带缓存复用）。"""

        async def render_map() -> str | None:
            return await self.manager.map_attachment_builder.render_map_image(
                lat,
                lon,
                config,
                event_caption=event_caption,
            )

        map_cache_key = self._build_map_cache_key(
            lat, lon, config, event_caption=event_caption
        )
        return await self.manager._render_with_cache(map_cache_key, render_map)

    async def _try_build_global_quake_card(
        self,
        event: EventEnvelope,
        *,
        source_id: str,
        active_config: dict[str, Any],
        message_format_config: dict[str, Any],
    ) -> MessageChain | None:
        """尝试构建 Global Quake 信息展示卡片。"""
        use_gq_card = message_format_config.get("use_global_quake_card", False)
        domain_event = self._get_domain_event(event)
        if not (
            source_id == "global_quake"
            and use_gq_card
            and isinstance(domain_event, EarthquakeEvent)
        ):
            return None

        try:
            return await self.manager.global_quake_card_builder.build(
                event,
                active_config=active_config,
                message_format_config=message_format_config,
                cache_key_builder=self._build_global_quake_card_cache_key,
                render_with_cache=self.manager._render_with_cache,
            )
        except Exception as e:
            logger.error(f"[灾害预警] Global Quake 卡片渲染失败: {e}，回退到文本模式")
            return None

    async def _append_snet_map_if_needed(
        self,
        chain: MessageChain,
        event: EventEnvelope,
        *,
        source_id: str,
    ) -> None:
        """为 S-Net 事件附加测站分布图（走 RenderImageCache，避免重复截图）。"""
        if source_id != "snet_msil":
            return
        renderer = getattr(self.manager, "snet_map_renderer", None)
        if renderer is None:
            return

        metadata = self._get_event_metadata(event)
        stations = metadata.get("stations")
        if not isinstance(stations, list) or not stations:
            return

        timestamp = str(metadata.get("timestamp") or "").strip()
        cache_key = self._build_snet_map_cache_key(stations, timestamp)
        try:

            async def _render_snet() -> str | None:
                # 缓存键稳定时固定文件名，便于命中磁盘复用
                safe_ts = timestamp or "unknown"
                img_path = os.path.join(
                    str(self.manager.temp_dir),
                    f"snet_map_{safe_ts}.png",
                )
                return await renderer.render(stations, img_path, timestamp)

            out = await self.manager._render_with_cache(cache_key, _render_snet)
            if not out or not os.path.exists(out):
                return
            with open(out, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode()
            chain.chain.append(Comp.Image.fromBase64(b64_data))
            logger.debug("[灾害预警] 已附加 S-Net 测站分布图")
            # 不删除 out：交给 RenderImageCache / 临时目录清理服务回收
        except Exception as e:
            logger.error(f"[灾害预警] S-Net 测站图渲染失败: {e}")

    async def _append_typhoon_map_if_needed(
        self,
        chain: MessageChain,
        event: EventEnvelope,
        *,
        message_format_config: dict[str, Any],
    ) -> None:
        """为台风事件附加路径图（需 history_track，走 RenderImageCache）。"""
        domain_event = self._get_domain_event(event)
        if not isinstance(domain_event, TyphoonEvent):
            return

        renderer = getattr(self.manager, "typhoon_map_renderer", None)
        if renderer is None:
            return
        can_render = getattr(renderer, "can_render", None)
        if callable(can_render) and not can_render(domain_event):
            return

        # 台风路径图使用独立瓦片配置，不与地震通用地图 map_source 混用。
        map_source = (
            message_format_config.get("typhoon_map_source") or "PetalMap矢量图暗"
        )
        if not str(map_source).strip():
            map_source = "PetalMap矢量图暗"
        playwright_mode = message_format_config.get("playwright_mode") or (
            self.manager.config.get("message_format", {}) or {}
        ).get("playwright_mode", "local")
        cache_key = self._build_typhoon_map_cache_key(
            domain_event,
            map_source=str(map_source),
            playwright_mode=str(playwright_mode),
        )
        try:

            async def _render_typhoon() -> str | None:
                safe_id = str(domain_event.typhoon_id or "unknown").replace("/", "_")
                # 文件名纳入 map_source / playwright_mode 指纹，避免多会话差异化配置
                # 并发渲染时写入同一路径导致串图或文件损坏。
                cfg_token = hashlib.sha1(
                    f"{map_source}|{playwright_mode}".encode()
                ).hexdigest()[:10]
                img_path = os.path.join(
                    str(self.manager.temp_dir),
                    f"typhoon_map_{safe_id}_{cfg_token}.png",
                )
                return await renderer.render(
                    domain_event,
                    img_path,
                    map_source=str(map_source),
                    playwright_mode=str(playwright_mode),
                )

            out = await self.manager._render_with_cache(cache_key, _render_typhoon)
            if not out or not os.path.exists(out):
                return
            with open(out, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode()
            chain.chain.append(Comp.Image.fromBase64(b64_data))
            logger.debug("[灾害预警] 已附加台风路径图")
        except Exception as e:
            logger.error(f"[灾害预警] 台风路径图渲染失败: {e}")

    async def _append_map_if_needed(
        self,
        chain: MessageChain,
        event: EventEnvelope,
        *,
        source_id: str,
        message_format_config: dict[str, Any],
    ) -> None:
        """按配置决定是否在主消息链中附加地图图片。"""
        include_map = message_format_config.get("include_map", False)
        split_map_sources = self._get_split_map_source_ids()
        domain_event = self._get_domain_event(event)
        if not include_map or not isinstance(domain_event, EarthquakeEvent):
            return

        if source_id in split_map_sources:
            logger.debug(
                f"[灾害预警] 数据源 {source_id} 属于分离地图发送类型，跳过同步附加"
            )
            return

        lat_valid = (
            domain_event.latitude is not None and -90 <= domain_event.latitude <= 90
        )
        lon_valid = (
            domain_event.longitude is not None and -180 <= domain_event.longitude <= 180
        )
        if not (lat_valid and lon_valid):
            return

        try:
            event_caption = self._build_event_map_caption(event)
            map_image_path = await self.render_map_image(
                domain_event.latitude,
                domain_event.longitude,
                message_format_config,
                event_caption=event_caption,
            )
            if not map_image_path:
                return
            try:
                with open(map_image_path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode()
                chain.chain.append(Comp.Image.fromBase64(b64_data))
                logger.debug("[灾害预警] 已附加地图图片 (Base64模式)")
            except Exception as b64_err:
                logger.error(f"[灾害预警] 地图图片转Base64失败: {b64_err}")
        except Exception as e:
            logger.error(f"[灾害预警] 地图图片生成失败: {e}")

    @classmethod
    def _resolve_weather_color_key(
        cls,
        weather_type_code: str,
        *,
        color_hint: str | None = None,
    ) -> str | None:
        """根据气象预警编码/颜色提示解析颜色关键词。

        支持：
        - 新格式 11B20_yellow：下划线后即为颜色关键词
        - 旧格式 p0002002：最后一位数字表示颜色（1=红, 2=橙, 3=黄, 4=蓝）
        - 紧凑 11B 格式 11B3102 / 11B2002：末两位 01/02/03/04 表示蓝/黄/橙/红
        - 中文/英文颜色提示（标题、级别字段）

        注意：编码解析失败时继续尝试 color_hint 兜底（不直接 return），
        确保地质灾害/山洪等无专属图标、编码颜色无法识别的类型也能回退到通用颜色图标。
        """
        code = (weather_type_code or "").strip()
        color_key = None
        if code:
            if "_" in code:
                # 新格式：11B20_yellow。命中下划线格式后提取颜色关键词。
                candidate = code.rsplit("_", 1)[-1].lower()
                if candidate in cls._WEATHER_ICON_FALLBACK_MAP:
                    color_key = candidate
            elif code.startswith("p"):
                # 旧格式：p0002002，最后一位数字映射颜色。
                # 与 weather_alarm_code_map.resolve_p_code_color 共用同一套映射，
                # 避免跨文件重复维护且颜色判定口径不一致。
                # 命中 SKIP 短码（如强对流 p0000003）时返回 None，继续走 color_hint 兜底。
                color_key = resolve_p_code_color(code)
            # 紧凑 11B 编码：仅当编码本身是 11B/11E 格式且末两位明确是
            # 01/02/03/04 时才认作颜色。必须限定前缀，否则 p 码（如 p0000003）
            # 的末两位 03 会被误判成紧凑颜色码 03=orange。
            if (
                not color_key
                and code
                and (code.startswith("11B") or code.startswith("11E"))
                and len(code) >= 2
                and code[-2:] in cls._COMPACT_11B_COLOR_MAP
            ):
                color_key = cls._COMPACT_11B_COLOR_MAP[code[-2:]]

        if color_key:
            return color_key

        raw_hint = (color_hint or "").strip()
        if raw_hint:
            hint = raw_hint.lower()
            # 直接英文键
            if hint in cls._WEATHER_ICON_FALLBACK_MAP:
                return hint
            # 中文颜色提示：仅匹配双字颜色词，避免地名误伤。
            for token, color_key in cls._CN_COLOR_HINT_MAP.items():
                if token.lower() in hint or token in raw_hint:
                    return color_key
        return None

    @classmethod
    def _resolve_weather_fallback_icon_path(
        cls,
        weather_type_code: str,
        *,
        icon_code: str | None = None,
        color_hint: str | None = None,
    ) -> str | None:
        """根据气象预警编码解析本地回退图标路径。

        优先使用标准化后的 11B 完整码（icon_code）解析颜色：
        - 下划线格式（11B20_yellow）颜色后缀最可靠；
        - 原始编码（如 p 码）解析失败时由 color_hint 兜底。
        """
        # icon_code 是标准化后的 11B 码（含下划线颜色后缀），解析颜色最可靠
        color_key = cls._resolve_weather_color_key(
            icon_code or weather_type_code,
            color_hint=color_hint,
        )
        relative_path = (
            cls._WEATHER_ICON_FALLBACK_MAP.get(color_key) if color_key else None
        )
        if not relative_path:
            return None
        # 以插件根目录为基准拼接绝对路径
        plugin_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        abs_path = os.path.join(plugin_root, relative_path)
        return abs_path if os.path.isfile(abs_path) else None

    async def _append_weather_icon_if_needed(
        self,
        chain: MessageChain,
        event: EventEnvelope,
        active_config: dict[str, Any],
    ) -> None:
        """按配置为气象事件附加预警级别图标组件。"""
        weather_config = active_config.get("weather_config", {})
        enable_weather_icon = weather_config.get("enable_weather_icon", True)
        domain_event = self._get_domain_event(event)
        if not (enable_weather_icon and isinstance(domain_event, WeatherEvent)):
            return

        metadata = self._get_event_metadata(event)
        # 从多层元数据中提取气象预警类型编码。
        # Fan Studio 格式如 11B20_yellow；OpenQuakeAPI CMA 格式如 p0002003。
        raw_weather_code = (
            metadata.get("weather_code")
            or metadata.get("type")
            or metadata.get("alert_code")
            or metadata.get("code")
            or getattr(domain_event, "alert_type", "")
        )
        if not isinstance(raw_weather_code, str) or not raw_weather_code.strip():
            return

        raw_weather_code = raw_weather_code.strip()

        # 统一解析为 Fan Studio 图标接口兼容的 11B 完整码。
        # p 编码（OpenQuakeAPI CMA）会通过映射表转换为 11B 码；
        # 已有 11B 码直接使用；无法映射的返回 None 走本地回退。
        title_text = getattr(domain_event, "title", "") or metadata.get("title", "")
        headline_text = getattr(domain_event, "headline", "") or metadata.get(
            "headline", ""
        )
        icon_code = resolve_weather_icon_code(
            raw_weather_code,
            title=title_text,
            headline=headline_text,
        )

        # 1. 本地优先：优先读取本地具体预警图标（11B 码对应文件）转 Base64 附加。
        #    本地缺失时才走后续回退通道，避免依赖外部服务与网络。
        local_icon_path = (
            resolve_local_weather_icon_abs_path(icon_code) if icon_code else None
        )
        if local_icon_path:
            try:
                with open(local_icon_path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode()
                chain.chain.append(Comp.Image.fromBase64(b64_data))
                logger.debug(f"[灾害预警] 已附加本地气象预警图标: {local_icon_path}")
                return
            except Exception as e:
                logger.warning(
                    f"[灾害预警] 本地气象预警图标读取失败: "
                    f"{local_icon_path}, 错误信息: {e}"
                )

        # 2. 本地颜色回退：本地具体图标缺失时，优先按颜色后缀/紧凑编码/标题颜色提示
        #    回退到本地通用图标（fallback_{color}.png）。这样即使 Fan Studio 接口
        #    对某些编码返回"伪成功"（真实占位图）也不会吞掉颜色回退。
        color_hint_candidates = [
            metadata.get("severity_color"),
            metadata.get("level"),
            metadata.get("headline"),
            metadata.get("title"),
            getattr(domain_event, "headline", None),
            getattr(domain_event, "title", None),
        ]
        color_hint = " ".join(
            str(item).strip()
            for item in color_hint_candidates
            if isinstance(item, str) and item.strip()
        )
        fallback_path = self._resolve_weather_fallback_icon_path(
            raw_weather_code,
            icon_code=icon_code,
            color_hint=color_hint or None,
        )
        if fallback_path:
            try:
                with open(fallback_path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode()
                chain.chain.append(Comp.Image.fromBase64(b64_data))
                logger.debug(
                    f"[灾害预警] 本地具体图标缺失，已回退到本地通用颜色图标: "
                    f"预警编码为 {raw_weather_code}, 解析码为 {icon_code}"
                )
                return
            except Exception as e:
                logger.warning(
                    f"[灾害预警] 本地回退图标读取失败: {fallback_path}, 错误信息: {e}"
                )

        # 3. Fan Studio 官方接口兜底：本地图标与本地颜色回退均不可用时，
        #    才请求外部图标接口（如 11B20_blue 这类本地与颜色回退均无匹配的类型）。
        if icon_code:
            icon_url = build_weather_icon_url(icon_code)
            appended = await self._append_remote_image_component(
                chain,
                icon_url,
                media_label="气象预警图标",
                allow_url_fallback=False,
            )
            if appended:
                logger.debug(f"[灾害预警] 已附加气象预警图标: {icon_url}")
                return

        logger.debug(
            f"[灾害预警] 气象预警图标下载失败且无可用回退图标，已跳过: "
            f"原始编码={raw_weather_code}, 解析码={icon_code}"
        )

    async def _append_tsunami_media_if_needed(
        self,
        chain: MessageChain,
        event: EventEnvelope,
    ) -> None:
        """按需把海啸图件（如等震度、波幅观测点等）附加到消息链。"""
        domain_event = self._get_domain_event(event)
        if not isinstance(domain_event, TsunamiEvent):
            return

        metadata = self._get_event_metadata(event)

        map_urls = metadata.get("map_urls")
        if not isinstance(map_urls, dict):
            return

        # 遍历可能存在的海啸媒体图片列表
        for map_key in self.TSUNAMI_MEDIA_KEYS:
            map_url = map_urls.get(map_key)
            if isinstance(map_url, str) and map_url.strip():
                await self._append_remote_image_component(
                    chain,
                    map_url.strip(),
                    media_label=f"海啸图件/{map_key}",
                    allow_url_fallback=True,
                )

    async def _append_cwa_report_media_if_needed(
        self,
        chain: MessageChain,
        event: EventEnvelope,
        source_id: str,
    ) -> None:
        """按需附加台湾地震报告相关等震度报告图件。"""
        if source_id != "cwa_fanstudio_report":
            return

        metadata = self._get_event_metadata(event)

        report_image_urls: list[str] = []
        candidate_urls = [
            metadata.get("image_uri"),
            metadata.get("shakemap_uri"),
        ]
        for image_url in candidate_urls:
            if isinstance(image_url, str):
                normalized_url = image_url.strip()
                if normalized_url and normalized_url not in report_image_urls:
                    report_image_urls.append(normalized_url)

        # 将筛选出的有效地震报告图片转 Base64/回退 URL 发送并附加至消息链中
        for idx, image_url in enumerate(report_image_urls, start=1):
            await self._append_remote_image_component(
                chain,
                image_url,
                media_label=f"CWA地震报告图件/{idx}",
                allow_url_fallback=True,
            )

    async def _append_fssn_cmt_beachball_if_needed(
        self,
        chain: MessageChain,
        event: EventEnvelope,
        *,
        source_id: str,
    ) -> None:
        """为 FSSN CMT 地震事件动态渲染并附加沙滩球图片。"""
        if source_id != "fssn_cmt_fanstudio":
            return

        domain_event = self._get_domain_event(event)
        if not isinstance(domain_event, EarthquakeEvent):
            return

        meta = domain_event.metadata if isinstance(domain_event.metadata, dict) else {}
        plane1 = meta.get("nodal_plane1")
        if not plane1:
            logger.debug("[灾害预警] FSSN CMT 事件缺失节面信息，跳过沙滩球渲染")
            return

        strike = plane1.get("strike")
        dip = plane1.get("dip")
        rake = plane1.get("rake")
        cmt_id = str(meta.get("cmt_id") or event.id).strip()
        # 清理路径中潜在的非法/目录穿越字符
        safe_cmt_id = re.sub(r"[^\w\.-]", "_", cmt_id)

        if strike is None or dip is None or rake is None:
            return

        try:
            # 引入 beachball 渲染器并设定本地缓存机制
            async def _render_ball() -> str | None:
                img_filename = f"fssn_cmt_ball_{safe_cmt_id}.png"
                img_path = os.path.join(str(self.manager.temp_dir), img_filename)
                renderer = BeachballRenderer(size=360)  # 一期渲染360px大小
                return await asyncio.to_thread(
                    renderer.render,
                    strike=float(strike),
                    dip=float(dip),
                    rake=float(rake),
                    output_path=img_path,
                )

            cache_key = f"cmt_beachball_{safe_cmt_id}_{strike}_{dip}_{rake}"
            out = await self.manager._render_with_cache(cache_key, _render_ball)

            if not out or not os.path.exists(out):
                logger.warning(f"[灾害预警] FSSN CMT 沙滩球生成结果不存在: {out}")
                return

            with open(out, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode()

            chain.chain.append(Comp.Image.fromBase64(b64_data))
            logger.info(f"[灾害预警] 已成功附加 FSSN CMT 沙滩球图片 ({safe_cmt_id})")

        except Exception as exc:
            logger.error(f"[灾害预警] FSSN CMT 附加沙滩球失败: {exc}", exc_info=True)
