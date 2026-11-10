"""
Jian Project 台风轮询服务。

周期性拉取活跃台风数据，转为领域事件后进入统一事件流水线。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger

from ....utils.plugin_logger import plugin_logger
from ...domain.typhoon import (
    build_jian_typhoon_event_envelope,
    normalize_typhoon_id,
)
from ...network.http.jian_typhoon_client import JianTyphoonClient
from ..query.source_runtime_query_service import SourceRuntimeQueryService


class JianTyphoonPollService:
    """Jian Project 台风 HTTP 轮询服务。"""

    SOURCE_ID = "typhoon_jianproject"
    DEFAULT_INTERVAL_SECONDS = 120
    MIN_INTERVAL_SECONDS = 30
    MAX_INTERVAL_SECONDS = 600

    def __init__(self, service):
        self.service = service
        self._source_runtime_query = SourceRuntimeQueryService(
            service.config,
            session_config_manager=getattr(service, "session_config_manager", None),
        )
        self._task: asyncio.Task | None = None
        self._last_active_ids: set[str] = set()
        self._pending_deactivate_ids: set[str] = set()
        self._last_success_at: float | None = None
        self._consecutive_failures = 0
        self._client: JianTyphoonClient | None = None
        self._owns_client = False
        self._disabled_logged = False
        self._no_change_log_rounds = 0
        self._no_change_log_interval = 30
        self._last_fingerprint: dict[str, str] = {}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def is_enabled(self) -> bool:
        return self._source_runtime_query.is_source_active(self.SOURCE_ID)

    def _jian_config(self) -> dict[str, Any]:
        data_sources = self.service.config.get("data_sources", {})
        if not isinstance(data_sources, dict):
            return {}
        cfg = data_sources.get("jian_project", {})
        return cfg if isinstance(cfg, dict) else {}

    def _resolve_interval(self) -> int:
        cfg = self._jian_config()
        raw = cfg.get("typhoon_poll_interval_seconds", self.DEFAULT_INTERVAL_SECONDS)
        if isinstance(raw, bool) or not isinstance(raw, int):
            interval = self.DEFAULT_INTERVAL_SECONDS
        else:
            interval = raw
        return max(self.MIN_INTERVAL_SECONDS, min(interval, self.MAX_INTERVAL_SECONDS))

    def _ensure_client(self) -> JianTyphoonClient:
        if self._client is None:
            self._client = JianTyphoonClient()
            self._owns_client = True
        return self._client

    async def start(self) -> None:
        if self.running:
            return
        if not self.is_enabled():
            if not self._disabled_logged:
                logger.debug("[灾害预警] Jian 台风轮询未启用，跳过启动")
                self._disabled_logged = True
            return
        self._disabled_logged = False
        self._task = asyncio.create_task(
            self._poll_loop(), name="dw_jian_typhoon_poll"
        )
        logger.info("[灾害预警] Jian Project 台风轮询已启动")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._owns_client and self._client is not None:
            await self._client.close()
            self._client = None
            self._owns_client = False

    @staticmethod
    def _fingerprint(item: dict[str, Any]) -> str:
        keys = (
            "id",
            "updateTime",
            "latitude",
            "longitude",
            "windSpeed",
            "pressure",
            "type",
            "power",
            "radius7",
            "radius10",
            "moveDirection",
            "moveSpeed",
        )
        parts = [f"{k}={item.get(k)}" for k in keys]
        return "|".join(parts)

    def _notify_silence_fetch_completed(self, *, success: bool) -> None:
        """通知静默协调器本轮抓取已结束（成功或失败）。"""
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is None:
            return
        try:
            coordinator.note_poll_fetch_completed("jian_typhoon", success=success)
        except Exception as exc:
            logger.debug(f"[灾害预警] Jian 台风轮询通知静默协调器失败: {exc}")

    async def _poll_loop(self) -> None:
        """后台轮询循环。"""
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is not None:
            try:
                coordinator.note_poll_fetch_started("jian_typhoon")
            except Exception as exc:
                logger.debug(
                    f"[灾害预警] Jian 台风轮询通知静默协调器抓取开始失败: {exc}"
                )
        try:
            await self._poll_once()
        except Exception as exc:
            logger.error(f"[灾害预警] Jian 台风首次抓取失败: {exc}")
            self._notify_silence_fetch_completed(success=False)

        while getattr(self.service, "running", False):
            try:
                interval = self._resolve_interval()
                await asyncio.sleep(interval)
                if not getattr(self.service, "running", False):
                    break
                if not self.is_enabled():
                    if not self._disabled_logged:
                        logger.debug("[灾害预警] Jian 台风轮询已禁用，本轮跳过")
                        self._disabled_logged = True
                    continue
                self._disabled_logged = False
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._consecutive_failures += 1
                logger.warning(
                    f"[灾害预警] Jian 台风轮询异常: {type(exc).__name__}: {exc}"
                )
                plugin_logger(
                    f"Jian 台风轮询异常: {exc}",
                    level="warning",
                )

    async def _poll_once(self) -> None:
        client = self._ensure_client()
        items, error = await client.fetch_active_typhoons(use_cache=False)
        if error is not None:
            self._consecutive_failures += 1
            logger.warning(f"[灾害预警] Jian 台风拉取失败: {error}")
            self._notify_silence_fetch_completed(success=False)
            return

        self._consecutive_failures = 0
        self._last_success_at = time.time()
        active_ids: set[str] = set()
        delivered = 0
        unchanged = 0

        for item in items:
            if not isinstance(item, dict):
                continue
            envelope = build_jian_typhoon_event_envelope(item)
            if envelope is None:
                continue
            tid = normalize_typhoon_id(getattr(envelope.event, "typhoon_id", "") or "")
            if not tid:
                continue
            active_ids.add(tid)
            fp = self._fingerprint(item)
            if self._last_fingerprint.get(tid) == fp:
                unchanged += 1
                continue
            self._last_fingerprint[tid] = fp
            ok = await self.service._handle_disaster_event(envelope)
            if ok:
                delivered += 1
                self._pending_deactivate_ids.discard(tid)

        # 上一轮活跃、本轮消失：放行一次停编（is_active=False）通知
        disappeared = (self._last_active_ids | self._pending_deactivate_ids) - active_ids
        for tid in sorted(disappeared):
            # 无上游停编载荷时不伪造事件字段，仅跳过；停编通知依赖 EQSC 等有完整轨迹的源。
            self._pending_deactivate_ids.discard(tid)
            self._last_fingerprint.pop(tid, None)

        self._last_active_ids = active_ids
        if delivered == 0 and unchanged > 0:
            self._no_change_log_rounds += 1
            if self._no_change_log_rounds % self._no_change_log_interval == 1:
                logger.debug(
                    f"[灾害预警] Jian 台风轮询无变化: 活跃 {len(active_ids)}，未变 {unchanged}"
                )
        else:
            self._no_change_log_rounds = 0
            if delivered:
                logger.info(
                    f"[灾害预警] Jian 台风轮询投递 {delivered} 条，活跃 {len(active_ids)}"
                )
        self._notify_silence_fetch_completed(success=True)


__all__ = ["JianTyphoonPollService"]
