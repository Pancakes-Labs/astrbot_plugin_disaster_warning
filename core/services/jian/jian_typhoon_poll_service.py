"""
Jian Project 台风独立轮询服务。

周期性拉取 GET /get/typhoon.php，用 TyphoonParser 构建事件并进入统一流水线。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger

from ....utils.plugin_logger import plugin_logger
from ...domain.event_models import TyphoonEvent
from ...domain.typhoon import clean_text, normalize_typhoon_id
from ...network.http.jian_typhoon_client import (
    LOOKUP_ERROR,
    JianTyphoonClient,
)
from ...parsers.typhoon_parser import TyphoonParser
from ..query.source_runtime_query_service import SourceRuntimeQueryService


class JianTyphoonPollService:
    """Jian Project 台风 HTTP 轮询服务。"""

    SOURCE_ID = "typhoon_jianproject"
    DEFAULT_INTERVAL_SECONDS = 60
    MIN_INTERVAL_SECONDS = 15
    MAX_INTERVAL_SECONDS = 600
    POLL_INTERVAL_CONFIG_KEY = "typhoon_poll_interval_seconds"

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
        self._disabled_logged = False
        self._no_change_log_rounds = 0
        self._no_change_log_interval = 30
        self._parser = TyphoonParser(
            message_logger=getattr(service, "message_logger", None),
            source_id=self.SOURCE_ID,
        )

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def is_enabled(self) -> bool:
        return self._source_runtime_query.is_source_active(self.SOURCE_ID)

    def _jian_config(self) -> dict[str, Any]:
        data_sources = self.service.config.get("data_sources", {})
        if not isinstance(data_sources, dict):
            return {}
        raw = data_sources.get("jian_project", {})
        return raw if isinstance(raw, dict) else {}

    def _resolve_interval(self) -> int:
        cfg = self._jian_config()
        raw = cfg.get(self.POLL_INTERVAL_CONFIG_KEY, self.DEFAULT_INTERVAL_SECONDS)
        if isinstance(raw, bool) or not isinstance(raw, int):
            interval = self.DEFAULT_INTERVAL_SECONDS
        else:
            interval = raw
        return max(self.MIN_INTERVAL_SECONDS, min(interval, self.MAX_INTERVAL_SECONDS))

    def _ensure_client(self) -> JianTyphoonClient:
        if self._client is None:
            self._client = JianTyphoonClient(
                message_logger=getattr(self.service, "message_logger", None),
            )
        return self._client

    def get_runtime_status(self) -> dict[str, Any]:
        deduplicator = getattr(
            getattr(self.service, "message_manager", None), "deduplicator", None
        )
        typhoon_cache = getattr(deduplicator, "_typhoon_cache", None)
        tracked_typhoons = len(typhoon_cache) if isinstance(typhoon_cache, dict) else 0
        return {
            "running": self.running,
            "enabled": self.is_enabled(),
            "last_success_at": self._last_success_at,
            "consecutive_failures": int(self._consecutive_failures),
            "tracked_typhoons": tracked_typhoons,
            "poll_interval_seconds": self._resolve_interval(),
        }

    async def start(self) -> None:
        if self.running:
            return
        if not self.is_enabled():
            logger.info("[灾害预警] Jian 台风数据源未启用，跳过轮询启动")
            return
        self._task = asyncio.create_task(
            self._poll_loop(), name="dw_jian_typhoon_poll"
        )
        self.service.scheduled_tasks.append(self._task)
        logger.debug("[灾害预警] Jian 台风轮询任务已启动")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._client is not None:
            await self._client.close()
        self._client = None

    async def _poll_loop(self) -> None:
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is not None:
            try:
                coordinator.note_poll_fetch_started("jian_typhoon")
            except Exception as exc:
                logger.debug(
                    f"[灾害预警] Jian 台风轮询通知静默协调器抓取开始失败: {exc}"
                )
        try:
            await self.fetch_once(emit_event=True)
        except Exception as exc:
            logger.error(f"[灾害预警] Jian 台风首次抓取失败: {exc}")
            if coordinator is not None:
                try:
                    coordinator.note_poll_fetch_completed(
                        "jian_typhoon", success=False
                    )
                except Exception:
                    pass

        while getattr(self.service, "running", False):
            try:
                await asyncio.sleep(self._resolve_interval())
                if not getattr(self.service, "running", False):
                    break
                if not self.is_enabled():
                    if not self._disabled_logged:
                        logger.debug("[灾害预警] Jian 台风已禁用，跳过本轮轮询")
                        self._disabled_logged = True
                    continue
                if self._disabled_logged:
                    self._no_change_log_rounds = 0
                self._disabled_logged = False
                await self.fetch_once(emit_event=True)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[灾害预警] Jian 台风轮询异常: {exc}")

    def _filter_active_items(self, typhoon_list: list[Any]) -> list[dict[str, Any]]:
        """筛出本轮可处理台风：当前列表项 + 刚从列表消失需停编通知的项。

        Jian GET 仅返回活跃台风；上一轮有、本轮无的 ID 需要合成停编通知。
        """
        current: dict[str, dict[str, Any]] = {}
        for item in typhoon_list:
            if not isinstance(item, dict):
                continue
            raw_id = clean_text(item.get("id"))
            if not raw_id:
                continue
            tid = normalize_typhoon_id(raw_id) or raw_id
            current[tid] = item

        candidates: list[dict[str, Any]] = list(current.values())

        # 上一轮活跃、本轮消失 → 停编通知（复制上一轮缓存无法拿到，用最小字段）
        disappeared = (self._last_active_ids | self._pending_deactivate_ids) - set(
            current.keys()
        )
        for tid in disappeared:
            candidates.append(
                {
                    "id": tid,
                    "is_active": False,
                    "_jian_deactivate": True,
                }
            )

        for tid in current:
            self._pending_deactivate_ids.discard(tid)

        self._last_active_ids = set(current.keys())
        return candidates

    def _build_envelope(self, raw: dict[str, Any]):
        if raw.get("_jian_deactivate"):
            # 停编：构造最小活跃=False 事件
            typhoon_id = clean_text(raw.get("id"))
            if not typhoon_id:
                return None
            envelope = self._parser._build_envelope(
                {
                    "id": typhoon_id,
                    "name": typhoon_id,
                    "type": "停编",
                    "updateTime": "",
                }
            )
            if envelope is None or not isinstance(envelope.event, TyphoonEvent):
                return None
            envelope.event.is_active = False
            return envelope

        envelope = self._parser._build_envelope(raw)
        if envelope is None:
            return None
        if isinstance(envelope.event, TyphoonEvent):
            envelope.event.is_active = True
        return envelope

    async def _process_typhoon_updates(
        self, active_items: list[dict[str, Any]]
    ) -> tuple[int, int]:
        emitted = 0
        filtered = 0
        for raw in active_items:
            envelope = self._build_envelope(raw)
            if envelope is None or not isinstance(envelope.event, TyphoonEvent):
                continue

            typhoon = envelope.event
            typhoon_id = normalize_typhoon_id(typhoon.typhoon_id) or typhoon.typhoon_id
            if not typhoon_id:
                continue
            deactivated = not bool(getattr(typhoon, "is_active", True))

            is_silence = getattr(self.service, "is_silencing", None)
            if callable(is_silence) and is_silence():
                try:
                    handled = await self.service._handle_disaster_event(envelope)
                except Exception as exc:
                    logger.debug(
                        f"[灾害预警] Jian 台风静默期播种失败（已忽略）: "
                        f"ID={typhoon_id}, err={exc}"
                    )
                    handled = False
                if handled:
                    self._pending_deactivate_ids.discard(typhoon_id)
                elif deactivated:
                    self._pending_deactivate_ids.add(typhoon_id)
                continue

            deduplicator = getattr(
                getattr(self.service, "message_manager", None), "deduplicator", None
            )
            peek = getattr(deduplicator, "peek_typhoon_should_push", None)
            if callable(peek) and not peek(envelope):
                self._pending_deactivate_ids.discard(typhoon_id)
                filtered += 1
                continue

            try:
                handled = await self.service._handle_disaster_event(envelope)
            except Exception as exc:
                plugin_logger.error(
                    f"[灾害预警] Jian 台风事件推送失败: ID={typhoon_id}, err={exc}",
                    is_event_linked=True,
                    event_stream="typhoon",
                )
                if deactivated:
                    self._pending_deactivate_ids.add(typhoon_id)
                continue
            if not handled:
                if deactivated:
                    self._pending_deactivate_ids.add(typhoon_id)
                continue
            self._pending_deactivate_ids.discard(typhoon_id)
            emitted += 1
        return emitted, filtered

    def _notify_silence_fetch_completed(self, *, success: bool) -> None:
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is None:
            return
        try:
            coordinator.note_poll_fetch_completed("jian_typhoon", success=success)
        except Exception as exc:
            logger.debug(f"[灾害预警] Jian 台风轮询通知静默协调器失败: {exc}")

    async def fetch_once(self, *, emit_event: bool = True) -> list[dict[str, Any]]:
        client = self._ensure_client()
        typhoon_list, status = await client.fetch_typhoon_list(use_cache=False)
        if status == LOOKUP_ERROR:
            self._consecutive_failures += 1
            self._notify_silence_fetch_completed(success=False)
            return []

        # empty 也是成功（当前无活跃台风）
        self._consecutive_failures = 0
        self._last_success_at = time.time()
        active_items = self._filter_active_items(
            typhoon_list if isinstance(typhoon_list, list) else []
        )

        if not emit_event:
            self._notify_silence_fetch_completed(success=True)
            return active_items

        emitted, filtered = await self._process_typhoon_updates(active_items)
        self._notify_silence_fetch_completed(success=True)
        if emitted == 0 and filtered == 0:
            self._no_change_log_rounds += 1
            if self._no_change_log_rounds >= self._no_change_log_interval:
                logger.debug(
                    f"[灾害预警] Jian 台风连续 {self._no_change_log_rounds} 轮无变化"
                )
                self._no_change_log_rounds = 0
        else:
            self._no_change_log_rounds = 0
            logger.info(
                f"[灾害预警] Jian 台风本轮推送 {emitted} 条，去重过滤 {filtered} 条"
            )
        return active_items


__all__ = ["JianTyphoonPollService"]
