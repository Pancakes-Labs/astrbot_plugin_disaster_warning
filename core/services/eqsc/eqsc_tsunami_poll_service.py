"""
EQSC JMA 海啸情报轮询服务。

独立于 WebSocket 接入：周期性拉取 /jma_tsunami.json，
经同源内容指纹去重后进入统一事件流水线，作为 P2P 津波予報的高优先级补充源。
各源独立推送，不再与 P2P 做跨源互斥。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from astrbot.api import logger

from ...domain.tsunami.jma_tsunami_normalize import (
    build_jma_tsunami_content_fingerprint,
    coerce_bool,
    normalize_jma_tsunami_areas,
    resolve_jma_tsunami_max_grade,
)
from ...network.http.eqsc_token_manager import EqscTokenManager
from ...network.http.eqsc_tsunami_client import EqscTsunamiClient
from ..query.source_runtime_query_service import SourceRuntimeQueryService


class EqscTsunamiPollService:
    """EQSC JMA 海啸轮询服务。"""

    SOURCE_ID = "jma_tsunami_eqsc"
    DEFAULT_INTERVAL_SECONDS = 60
    MIN_INTERVAL_SECONDS = 15
    MAX_INTERVAL_SECONDS = 300

    def __init__(self, service):
        self.service = service
        self._source_runtime_query = SourceRuntimeQueryService(service.config)
        self._task: asyncio.Task | None = None
        self._last_payload_fingerprint: str | None = None
        self._last_event_id: str | None = None
        self._last_success_at: float | None = None
        self._consecutive_failures = 0
        self._client: EqscTsunamiClient | None = None
        self._owns_token_manager = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def is_enabled(self) -> bool:
        """数据源是否启用（组总闸 + jma_tsunami 子开关）。"""
        return self._source_runtime_query.is_source_enabled(self.SOURCE_ID)

    def _eqsc_config(self) -> dict[str, Any]:
        data_sources = self.service.config.get("data_sources", {})
        if not isinstance(data_sources, dict):
            return {}
        eqsc = data_sources.get("eqsc", {})
        return eqsc if isinstance(eqsc, dict) else {}

    def _resolve_interval(self) -> int:
        cfg = self._eqsc_config()
        try:
            interval = int(
                cfg.get(
                    "jma_tsunami_poll_interval_seconds", self.DEFAULT_INTERVAL_SECONDS
                )
            )
        except (TypeError, ValueError):
            interval = self.DEFAULT_INTERVAL_SECONDS
        return max(self.MIN_INTERVAL_SECONDS, min(interval, self.MAX_INTERVAL_SECONDS))

    def _resolve_include_training(self) -> bool:
        return bool(self._eqsc_config().get("jma_tsunami_include_training", False))

    def _get_shared_token_manager(self) -> EqscTokenManager | None:
        """优先复用 EQSC 通道服务的 token_manager，避免双份鉴权状态。"""
        channel = getattr(self.service, "eqsc_channel_service", None)
        if channel is None:
            return None
        token_manager = getattr(channel, "token_manager", None)
        if isinstance(token_manager, EqscTokenManager):
            return token_manager
        return None

    def _ensure_client(self) -> EqscTsunamiClient | None:
        """懒创建海啸客户端；共享 token_manager 时不接管其生命周期。"""
        if self._client is not None:
            return self._client

        eqsc_config = self._eqsc_config()
        message_logger = getattr(self.service, "message_logger", None)
        shared_tm = self._get_shared_token_manager()
        if shared_tm is not None:
            self._client = EqscTsunamiClient(
                shared_tm,
                eqsc_config,
                message_logger=message_logger,
                owns_token_manager=False,
            )
            self._owns_token_manager = False
            return self._client

        token_manager = EqscTokenManager(eqsc_config)
        if not token_manager.is_configured:
            logger.debug("[灾害预警] EQSC 海啸轮询：token 未配置，跳过客户端创建")
            return None
        self._client = EqscTsunamiClient(
            token_manager,
            eqsc_config,
            message_logger=message_logger,
            owns_token_manager=True,
        )
        self._owns_token_manager = True
        return self._client

    def get_runtime_status(self) -> dict[str, Any]:
        """供健康面板读取的轻量运行态。"""
        return {
            "running": self.running,
            "enabled": self.is_enabled(),
            "last_success_at": self._last_success_at,
            "consecutive_failures": int(self._consecutive_failures),
            "last_event_id": self._last_event_id,
            "poll_interval_seconds": self._resolve_interval(),
        }

    async def start(self) -> None:
        """启动后台轮询任务。"""
        if self.running:
            return
        if not self.is_enabled():
            logger.info("[灾害预警] EQSC 海啸数据源未启用，跳过轮询启动")
            return
        # 通道 token 未配置时仍启动循环，便于配置热更新后自动生效；
        # 每轮会自行判断并跳过。
        self._task = asyncio.create_task(self._poll_loop(), name="dw_eqsc_tsunami_poll")
        self.service.scheduled_tasks.append(self._task)
        logger.info("[灾害预警] EQSC 海啸轮询任务已启动")

    async def stop(self) -> None:
        """停止后台轮询并释放客户端。"""
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
        """后台轮询循环。"""
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is not None:
            try:
                coordinator.note_poll_fetch_started("eqsc_tsunami")
            except Exception as exc:
                logger.debug(
                    f"[灾害预警] EQSC 海啸轮询通知静默协调器抓取开始失败: {exc}"
                )
        try:
            await self.fetch_once(emit_event=True)
        except Exception as exc:
            logger.error(f"[灾害预警] EQSC 海啸首次抓取失败: {exc}")
            if coordinator is not None:
                try:
                    coordinator.note_poll_fetch_completed("eqsc_tsunami", success=False)
                except Exception:
                    pass

        while getattr(self.service, "running", False):
            try:
                interval = self._resolve_interval()
                await asyncio.sleep(interval)
                if not getattr(self.service, "running", False):
                    break
                if not self.is_enabled():
                    logger.debug("[灾害预警] EQSC 海啸已禁用，跳过本轮轮询")
                    continue
                await self.fetch_once(emit_event=True)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[灾害预警] EQSC 海啸轮询异常: {exc}")

    def _build_snapshot_fingerprint(self, raw: dict[str, Any]) -> str:
        """基于原始快照构建轮询侧内容指纹。"""
        cancelled = coerce_bool(raw.get("cancelled"), default=False)
        is_training = coerce_bool(raw.get("isTraining"), default=False)
        areas = normalize_jma_tsunami_areas(raw.get("areas"), cancelled=cancelled)
        max_grade = resolve_jma_tsunami_max_grade(areas, cancelled=cancelled)
        event_id = str(
            raw.get("eventID") or raw.get("eventId") or raw.get("id") or ""
        ).strip()
        return build_jma_tsunami_content_fingerprint(
            event_id=event_id,
            cancelled=cancelled,
            max_grade=max_grade,
            areas=areas,
            is_training=is_training,
        )

    def _notify_silence_fetch_completed(self, *, success: bool) -> None:
        """通知静默协调器本轮抓取已结束（成功或失败）。"""
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is None:
            return
        try:
            coordinator.note_poll_fetch_completed("eqsc_tsunami", success=success)
        except Exception as exc:
            logger.debug(f"[灾害预警] EQSC 海啸轮询通知静默协调器失败: {exc}")

    async def fetch_once(self, *, emit_event: bool = True) -> dict[str, Any] | None:
        """抓取一轮 EQSC 海啸快照，可选投递事件。"""
        client = self._ensure_client()
        if client is None:
            self._notify_silence_fetch_completed(success=False)
            return None

        # 轮询侧强制绕过短缓存，确保按间隔拿到最新快照；
        # 客户端缓存仍可用于同间隔内的并发查询复用。
        raw = await client.fetch_latest_tsunami(use_cache=False)
        if not isinstance(raw, dict) or not raw:
            self._consecutive_failures += 1
            self._notify_silence_fetch_completed(success=False)
            return None

        self._consecutive_failures = 0
        self._last_success_at = time.time()

        fingerprint = self._build_snapshot_fingerprint(raw)

        is_training = coerce_bool(raw.get("isTraining"), default=False)
        if is_training and not self._resolve_include_training():
            # 主动忽略训练报时仍提交指纹，避免每轮重复解析同一训练快照。
            self._last_payload_fingerprint = fingerprint
            logger.debug("[灾害预警] EQSC 海啸训练报已忽略")
            self._notify_silence_fetch_completed(success=True)
            return raw

        if fingerprint == self._last_payload_fingerprint:
            logger.debug("[灾害预警] EQSC 海啸快照内容未变化，跳过推送")
            self._notify_silence_fetch_completed(success=True)
            return raw

        if not emit_event:
            self._last_payload_fingerprint = fingerprint
            self._notify_silence_fetch_completed(success=True)
            return raw

        message = json.dumps(raw, ensure_ascii=False)
        event = self.service.parse_event(self.SOURCE_ID, message)
        if event is None:
            # 解析失败不提交指纹，下一轮可重试（例如短暂脏数据）。
            self._notify_silence_fetch_completed(success=True)
            return raw

        event_id = getattr(event, "id", None)
        # 仅在成功处理后提交指纹；_handle_disaster_event 吞异常并返回 False。
        # 成功通知放在处理（含静默播种）完成之后，避免静默协调器提前 READY。
        handled = await self.service._handle_disaster_event(event)
        if not handled:
            self._notify_silence_fetch_completed(success=True)
            return raw
        self._last_payload_fingerprint = fingerprint
        self._last_event_id = str(event_id) if event_id else None
        self._notify_silence_fetch_completed(success=True)
        return raw


__all__ = ["EqscTsunamiPollService"]
