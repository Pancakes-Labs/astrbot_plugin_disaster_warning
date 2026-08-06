"""
CWA EEW 融合策略服务。
负责处理 Fan CWA EEW 等待 Wolfx 最大震度补充与 Wolfx 到达后的缓存/唤醒流程。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger

from ....utils.converters import ScaleConverter
from ....utils.plugin_logger import plugin_logger
from ...domain.event_models import EarthquakeEvent, EventEnvelope
from ...domain.event_payload import SourcePayload


class CWAEewFusionService:
    """CWA EEW 最大震度融合策略服务。"""

    def __init__(self, manager, execute_push):
        # 通过管理器访问融合状态，并复用统一推送执行入口。
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
        envelope = event
        payload = envelope.payload
        if isinstance(payload, SourcePayload):
            return payload
        source_payload = SourcePayload(
            source_id=envelope.source_id or "",
            raw=dict(payload) if isinstance(payload, dict) else {},
        )
        envelope.payload = source_payload
        return source_payload

    @classmethod
    def _apply_scale(
        cls,
        event: EventEnvelope,
        earthquake: EarthquakeEvent,
        scale: float,
    ) -> None:
        """把 Wolfx 提供的最大震度写回事件载荷、事件元数据与领域事件对象。"""
        source_payload = cls._ensure_source_payload(event)
        source_payload.raw["wolfx_scale"] = scale
        source_payload.raw["MaxIntensity"] = scale
        source_payload.attributes["scale"] = scale
        source_payload.attributes["wolfx_scale"] = scale
        if isinstance(event.metadata, dict):
            event.metadata["scale"] = scale
            event.metadata["wolfx_scale"] = scale
        if isinstance(getattr(earthquake, "metadata", None), dict):
            earthquake.metadata["scale"] = scale
            earthquake.metadata["wolfx_scale"] = scale
        earthquake.scale = scale

    async def intercept_fan_event(
        self,
        event: EventEnvelope,
        timeout: int,
        *,
        target_sessions: list[str] | None = None,
        session_config_getter=None,
    ) -> bool:
        """拦截 Fan CWA EEW 事件并等待 Wolfx 最大震度补充。"""
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
        cached_payload = store.select_cached_report_payload(
            store.cwa_eew_wolfx_cache.get(event_key, {}), report_num
        )
        if cached_payload is None:
            cached_payload = store.select_cached_payload_from_all(
                store.cwa_eew_wolfx_cache, report_num, "scale"
            )
        if (
            isinstance(cached_payload, dict)
            and cached_payload.get("scale") is not None
            and earthquake.scale is None
        ):
            scale = cached_payload["scale"]
            cls = type(self)
            cls._apply_scale(event, earthquake, scale)
            plugin_logger.info(
                f"[灾害预警] 融合策略：Fan CWA EEW 事件 {event.id} 已命中 Wolfx 缓存，补充的最大震度为 {scale}",
                is_event_linked=True,
                event_stream="earthquake",
                is_silent_window=True,
            )
            if self._absorb_if_silencing(event):
                return False
            return await self._execute_push(
                event,
                target_sessions=target_sessions,
                session_config_getter=session_config_getter,
            )

        plugin_logger.info(
            f"[灾害预警] 融合策略：已拦截 Fan CWA EEW 事件 {event.id}，事件标识为 {event_key}，报数为 {report_num}，等待 Wolfx 在 {timeout} 秒内补充最大震度",
            is_event_linked=True,
            event_stream="earthquake",
            is_silent_window=True,
        )

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        pending_key = f"{event_key}#{report_num}#{event.id}#{int(time.time() * 1000)}"

        store.cwa_eew_pending[pending_key] = {
            "event": event,
            "future": future,
            "event_key": event_key,
            "report_num": report_num,
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
            store.cwa_eew_pending.pop(pending_key, None)

            if result == "timeout":
                plugin_logger.info(
                    "[灾害预警] 融合策略：CWA EEW 等待超时，推送原始 Fan 事件",
                    is_event_linked=True,
                    event_stream="earthquake",
                    is_silent_window=True,
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
                    "[灾害预警] 融合策略：CWA EEW 融合完成，推送补充最大震度后的 Fan 事件",
                    is_event_linked=True,
                    event_stream="earthquake",
                    is_silent_window=True,
                )
                if self._absorb_if_silencing(event):
                    return False
                return await self._execute_push(
                    event,
                    target_sessions=target_sessions,
                    session_config_getter=session_config_getter,
                )
        except Exception as e:
            logger.error(f"[灾害预警] CWA EEW 融合策略处理异常: {e}")
            if self._absorb_if_silencing(event):
                return False
            return await self._execute_push(
                event,
                target_sessions=target_sessions,
                session_config_getter=session_config_getter,
            )

        return False

    @staticmethod
    def _extract_wolfx_scale(
        payload_raw: dict[str, Any],
        fallback: Any = None,
    ) -> float | None:
        """从 Wolfx 载荷中尽量提取最大震度数值。"""
        candidates: list[Any] = []
        if fallback is not None:
            candidates.append(fallback)
        candidates.extend(
            [
                payload_raw.get("wolfx_scale"),
                payload_raw.get("MaxIntensity"),
                payload_raw.get("maxIntensity"),
                payload_raw.get("scale"),
            ]
        )

        for candidate in candidates:
            if candidate is None:
                continue
            parsed = ScaleConverter.parse_jma_cwa_scale(candidate)
            if parsed is not None:
                return parsed
        return None

    def extract_wolfx_scale(
        self,
        wolfx_event: EventEnvelope,
        wolfx_earthquake: EarthquakeEvent,
    ) -> float | None:
        source_payload = type(self)._ensure_source_payload(wolfx_event)
        return type(self)._extract_wolfx_scale(
            source_payload.to_dict(),
            getattr(wolfx_earthquake, "scale", None),
        )

    def handle_wolfx_event(self, wolfx_event: EventEnvelope):
        """处理 Wolfx 到达事件，并尝试唤醒等待中的 Fan CWA EEW 事件。"""
        earthquake = self._get_earthquake_data(wolfx_event)
        if earthquake is None:
            return

        scale = self.extract_wolfx_scale(wolfx_event, earthquake)
        if scale is None:
            return

        store = self.manager._fusion_state_store
        store.prune()

        event_key = store.get_fusion_event_key(wolfx_event)
        report_num = store.get_fusion_report_num(wolfx_event)
        if not event_key:
            return

        event_cache = store.cwa_eew_wolfx_cache.setdefault(event_key, {})
        event_cache[report_num] = {
            "scale": scale,
            "created_at": time.time(),
        }

        pending_key = store.find_best_pending_key(
            store.cwa_eew_pending, event_key, report_num
        )
        if not pending_key:
            return

        try:
            item = store.cwa_eew_pending.get(pending_key)
            if not isinstance(item, dict):
                return

            fan_event = item.get("event")
            future = item.get("future")
            fan_earthquake = self._get_earthquake_data(fan_event)
            if fan_event is None or fan_earthquake is None:
                return

            if fan_earthquake.scale is None:
                type(self)._apply_scale(fan_event, fan_earthquake, scale)
                plugin_logger.info(
                    f"[灾害预警] 融合策略：已使用 Wolfx 为 Fan CWA EEW 事件 {pending_key} 补充最大震度，数值为 {scale}",
                    is_event_linked=True,
                    event_stream="earthquake",
                    is_silent_window=True,
                )
            else:
                plugin_logger.info(
                    f"[灾害预警] 融合策略：Fan CWA EEW 事件 {pending_key} 已自带最大震度，保留 Fan 的数值 ({fan_earthquake.scale})",
                    is_event_linked=True,
                    event_stream="earthquake",
                    is_silent_window=True,
                )

            if future is not None and hasattr(future, "done") and not future.done():
                future.set_result("fused")

            store.cwa_eew_pending.pop(pending_key, None)
        except Exception as e:
            logger.error(f"[灾害预警] CWA EEW 融合操作失败: {e}")
