"""
CENC 融合策略服务。
负责处理 Fan CENC 等待 Wolfx 烈度补充与 Wolfx 到达后的缓存/唤醒流程。
"""

from __future__ import annotations

import asyncio
import time

from astrbot.api import logger

from ....utils.plugin_logger import plugin_logger
from ...domain.event_models import EarthquakeEvent, EventEnvelope
from ...domain.event_payload import SourcePayload


class CENCFusionService:
    """CENC 融合策略服务。"""

    def __init__(self, manager, execute_push):
        # 融合服务通过主消息管理器访问融合状态仓储，并复用统一推送执行入口。
        self.manager = manager
        self._execute_push = execute_push
        # 启动静默兜底判定回调：await future 之后、真正推送之前再检查一次，
        # 防止启动快照跨静默期被融合唤醒后泄漏成真实推送。
        self._silence_checker = None
        # 静默期吸收回调（由主服务注入，统一执行播种与计数，避免依赖反向引用）
        self._silence_absorb_handler = None

    def set_silence_checker(self, checker) -> None:
        """注入启动静默判定回调（复用主服务 is_silencing）。"""
        self._silence_checker = checker

    def set_silence_absorb_handler(self, handler) -> None:
        """注入静默期吸收回调（播种去重指纹并计数）。"""
        self._silence_absorb_handler = handler

    def _absorb_if_silencing(self, event) -> bool:
        """静默期吸收事件并返回 True（不真正推送）。

        Returns:
            True: 事件处于启动静默期，已吸收（播种与计数由上层处理）。
            False: 未处于静默期，可继续推送。
        """
        checker = self._silence_checker
        if checker is None:
            return False
        try:
            if checker():
                handler = self._silence_absorb_handler
                if callable(handler):
                    handler(event)
                plugin_logger.debug(f"[灾害预警] 融合链静默兜底吸收事件: {event.id}")
                return True
        except Exception as exc:
            plugin_logger.debug(f"[灾害预警] 融合链静默兜底判定异常（已忽略）: {exc}")
        return False

    @staticmethod
    def _get_earthquake_data(
        event: EventEnvelope,
    ) -> EarthquakeEvent | None:
        data = getattr(event, "event", None)
        if isinstance(data, EarthquakeEvent):
            return data
        return None

    @staticmethod
    def _ensure_source_payload(event: EventEnvelope) -> SourcePayload:
        """确保事件上挂载统一原始载荷对象。"""
        payload = event.payload
        if isinstance(payload, SourcePayload):
            return payload
        source_payload = SourcePayload(
            source_id=event.source_id or "",
            raw=dict(payload) if isinstance(payload, dict) else {},
        )
        event.payload = source_payload
        return source_payload

    @classmethod
    def _apply_intensity(
        cls,
        event: EventEnvelope,
        earthquake: EarthquakeEvent,
        intensity: float,
    ) -> None:
        """把融合得到的烈度写回载荷、事件元数据与领域对象。"""
        source_payload = cls._ensure_source_payload(event)
        source_payload.raw["intensity"] = intensity
        source_payload.attributes["intensity"] = intensity
        if isinstance(event.metadata, dict):
            event.metadata["intensity"] = intensity
        if isinstance(getattr(earthquake, "metadata", None), dict):
            earthquake.metadata["intensity"] = intensity
        earthquake.intensity = intensity

    def _resolve_measurement_type(self, event: EventEnvelope) -> str:
        """解析 CENC 测定类型，统一为 automatic / reviewed / unknown。"""
        source_payload = type(self)._ensure_source_payload(event)
        raw = source_payload.raw if isinstance(source_payload.raw, dict) else {}
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        earthquake = type(self)._get_earthquake_data(event)
        earthquake_metadata = (
            getattr(earthquake, "metadata", None)
            if isinstance(getattr(earthquake, "metadata", None), dict)
            else {}
        )

        candidates = [
            metadata.get("info_type"),
            earthquake_metadata.get("info_type"),
            source_payload.attributes.get("info_type"),
            raw.get("infoTypeName"),
            raw.get("type"),
        ]
        for candidate in candidates:
            normalized = (
                self.manager._fusion_state_store.normalize_cenc_measurement_type(
                    candidate
                )
            )
            if normalized != "unknown":
                return normalized
        return "unknown"

    async def intercept_fan_event(
        self,
        event: EventEnvelope,
        timeout: int,
        *,
        target_sessions: list[str] | None = None,
        session_config_getter=None,
    ) -> bool:
        """拦截 Fan 侧事件并等待 Wolfx 烈度补充。"""
        earthquake = self._get_earthquake_data(event)
        if earthquake is None:
            if self._absorb_if_silencing(event):
                return False
            return await self._execute_push(
                event,
                target_sessions=target_sessions,
                session_config_getter=session_config_getter,
            )

        store = self.manager._fusion_state_store
        store.prune()

        event_key = store.get_fusion_event_key(event)
        report_num = store.get_fusion_report_num(event)
        measurement_type = self._resolve_measurement_type(event)
        similarity_profile = store.build_cenc_similarity_profile(event)
        cached_payload = store.select_cenc_cached_payload(
            store.cenc_wolfx_cache.get(event_key, {}), report_num, measurement_type
        )
        if cached_payload is None:
            cached_payload = store.select_cenc_cached_payload_from_all(
                store.cenc_wolfx_cache,
                report_num,
                measurement_type,
                similarity_profile,
            )
        if (
            isinstance(cached_payload, dict)
            and cached_payload.get("intensity") is not None
        ):
            intensity = cached_payload["intensity"]
            type(self)._apply_intensity(event, earthquake, intensity)
            plugin_logger.info(
                f"[灾害预警] 融合策略: Fan CENC 事件 {event.id} 命中 Wolfx 缓存并补充烈度: {earthquake.intensity}",
                is_event_linked=True,
                event_stream="earthquake",
            )
            if self._absorb_if_silencing(event):
                return False
            return await self._execute_push(
                event,
                target_sessions=target_sessions,
                session_config_getter=session_config_getter,
            )

        plugin_logger.info(
            f"[灾害预警] 融合策略: 拦截 Fan CENC 事件 {event.id}，事件标识为 {event_key}，兼容槽位序号为 {report_num}，测定类型为 {measurement_type}，等待 Wolfx 补充（{timeout} 秒）...",
            is_event_linked=True,
            event_stream="earthquake",
        )

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        # pending_key 需要足够唯一；这里保留 report_num 作为槽位片段，仅用于兼容旧结构与同事件并发隔离。
        pending_key = f"{event_key}#{report_num}#{event.id}#{int(time.time() * 1000)}"

        store.cenc_pending[pending_key] = {
            "event": event,
            "future": future,
            "event_key": event_key,
            "report_num": report_num,
            "measurement_type": measurement_type,
            "similarity_profile": similarity_profile,
            "created_at": time.time(),
        }

        async def wait_timeout():
            try:
                await asyncio.sleep(timeout)
                if not future.done():
                    future.set_result("timeout")
            except Exception as e:
                if not future.done():
                    future.set_exception(e)

        asyncio.create_task(wait_timeout())

        try:
            result = await future
            store.cenc_pending.pop(pending_key, None)

            if result == "timeout":
                plugin_logger.info(
                    "[灾害预警] 融合策略: CENC 等待超时，推送原始 Fan 事件",
                    is_event_linked=True,
                    event_stream="earthquake",
                )
                if self._absorb_if_silencing(event):
                    return False
                return await self._execute_push(
                    event,
                    target_sessions=target_sessions,
                    session_config_getter=session_config_getter,
                )
            if result == "fused":
                plugin_logger.info(
                    "[灾害预警] 融合策略: CENC 融合完成，推送补充后的 Fan 事件",
                    is_event_linked=True,
                    event_stream="earthquake",
                )
                if self._absorb_if_silencing(event):
                    return False
                return await self._execute_push(
                    event,
                    target_sessions=target_sessions,
                    session_config_getter=session_config_getter,
                )
        except Exception as e:
            logger.error(f"[灾害预警] CENC 融合策略处理异常: {e}")
            if self._absorb_if_silencing(event):
                return False
            return await self._execute_push(
                event,
                target_sessions=target_sessions,
                session_config_getter=session_config_getter,
            )

        return False

    def handle_wolfx_event(self, wolfx_event: EventEnvelope):
        """处理 Wolfx 到达事件，并尝试唤醒等待中的 Fan CENC 事件。"""
        earthquake = self._get_earthquake_data(wolfx_event)
        if earthquake is None:
            logger.warning(
                "[灾害预警] CENC 融合策略收到了一条 Wolfx 消息，但里面没有可用的地震事件对象，因此这次无法参与融合。"
            )
            return

        intensity = getattr(earthquake, "intensity", None)
        if intensity is None:
            logger.warning(
                f"[灾害预警] 这条 Wolfx CENC 消息已经进入融合流程，但烈度信息仍然缺失。"
                f"当前震中是 {getattr(earthquake, 'place_name', '未知地点') or '未知地点'}，"
                f"发震时间是 {getattr(earthquake, 'occurred_at', None)}，因此这次无法补充 Fan 的烈度。"
            )
            return

        store = self.manager._fusion_state_store
        store.prune()

        event_key = store.get_fusion_event_key(wolfx_event)
        report_num = store.get_fusion_report_num(wolfx_event)
        measurement_type = self._resolve_measurement_type(wolfx_event)
        similarity_profile = store.build_cenc_similarity_profile(wolfx_event)
        if not event_key:
            logger.warning(
                f"[灾害预警] 这条 Wolfx CENC 消息虽然带有烈度，但没有生成出可用于融合的事件标识。"
                f"震中是 {getattr(earthquake, 'place_name', '未知地点') or '未知地点'}，"
                f"发震时间是 {getattr(earthquake, 'occurred_at', None)}，这次只能放弃写入融合缓存。"
            )
            return

        event_cache = store.cenc_wolfx_cache.setdefault(event_key, {})
        event_cache[report_num] = {
            "intensity": intensity,
            "measurement_type": measurement_type,
            "occurred_at": similarity_profile.get("occurred_at"),
            "latitude": similarity_profile.get("latitude"),
            "longitude": similarity_profile.get("longitude"),
            "magnitude": similarity_profile.get("magnitude"),
            "created_at": time.time(),
        }

        pending_key = store.find_best_cenc_pending_key(
            store.cenc_pending, event_key, measurement_type
        )
        if not pending_key:
            pending_key = store.find_best_cenc_pending_key_by_profile(
                store.cenc_pending, measurement_type, similarity_profile
            )
        if not pending_key:
            return

        try:
            item = store.cenc_pending.get(pending_key)
            if not isinstance(item, dict):
                return

            fan_event = item.get("event")
            future = item.get("future")
            fan_earthquake = self._get_earthquake_data(fan_event)
            if fan_event is None or fan_earthquake is None:
                return

            type(self)._apply_intensity(fan_event, fan_earthquake, intensity)
            plugin_logger.info(
                f"[灾害预警] 融合策略: 成功用 Wolfx 补充 Fan CENC 事件 {pending_key} 的烈度: {intensity}",
                is_event_linked=True,
                event_stream="earthquake",
            )

            if future is not None and hasattr(future, "done") and not future.done():
                future.set_result("fused")

            store.cenc_pending.pop(pending_key, None)
        except Exception as e:
            logger.error(f"[灾害预警] CENC 融合操作失败: {e}")
