"""
EQSC CENC 烈度速报轮询服务。

独立于 WebSocket 接入：
1. 周期性拉取 /listIntensityReportCENC.json
2. 按 eventID 差分发现新事件
3. 仅对新/变化项拉取 /intensityReportCENC.json 详情
4. 解析后进入统一事件流水线

作为 FAN /cenc-ir 独立 WS 的高优先级替代源（默认启用）。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from astrbot.api import logger

from ....utils.plugin_logger import plugin_logger
from ...app.services.eqsc_channel_service import EqscChannelService
from ...network.http.eqsc_cenc_intensity_client import EqscCencIntensityClient
from ...network.http.eqsc_token_manager import EqscTokenManager
from ..query.source_runtime_query_service import SourceRuntimeQueryService


class EqscCencIntensityPollService:
    """EQSC CENC 烈度速报 HTTP 轮询服务。"""

    SOURCE_ID = "cenc_ir_eqsc"
    DEFAULT_INTERVAL_SECONDS = 90
    MIN_INTERVAL_SECONDS = 30
    MAX_INTERVAL_SECONDS = 600
    DEFAULT_LIST_LIMIT = 5
    MIN_LIST_LIMIT = 1
    MAX_LIST_LIMIT = 20
    # 已处理 eventID 缓存上限，防止长跑内存增长
    MAX_TRACKED_EVENTS = 128

    def __init__(self, service):
        self.service = service
        self._source_runtime_query = SourceRuntimeQueryService(service.config)
        self._task: asyncio.Task | None = None
        # event_id -> list 侧指纹（magnitude|place_name）
        self._last_list_fingerprints: dict[str, str] = {}
        # 已成功投递详情的 event_id 集合（有序近似：dict key 顺序）
        self._detail_done: dict[str, None] = {}
        self._last_success_at: float | None = None
        self._consecutive_failures = 0
        self._client: EqscCencIntensityClient | None = None
        self._owns_token_manager = False
        self._startup_seeded = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def is_enabled(self) -> bool:
        """数据源是否启用（组总闸 + china_cenc_intensity_report 子开关）。"""
        return self._source_runtime_query.is_source_enabled(self.SOURCE_ID)

    def _eqsc_config(self) -> dict[str, Any]:
        data_sources = self.service.config.get("data_sources", {})
        if not isinstance(data_sources, dict):
            return {}
        eqsc = data_sources.get("eqsc", {})
        return eqsc if isinstance(eqsc, dict) else {}

    def _resolve_interval(self) -> int:
        cfg = self._eqsc_config()
        raw = cfg.get("cenc_ir_poll_interval_seconds", self.DEFAULT_INTERVAL_SECONDS)
        if isinstance(raw, bool) or not isinstance(raw, int):
            interval = self.DEFAULT_INTERVAL_SECONDS
        else:
            interval = raw
        return max(self.MIN_INTERVAL_SECONDS, min(interval, self.MAX_INTERVAL_SECONDS))

    def _resolve_list_limit(self) -> int:
        cfg = self._eqsc_config()
        raw = cfg.get("cenc_ir_list_limit", self.DEFAULT_LIST_LIMIT)
        if isinstance(raw, bool) or not isinstance(raw, int):
            limit = self.DEFAULT_LIST_LIMIT
        else:
            limit = raw
        return max(self.MIN_LIST_LIMIT, min(limit, self.MAX_LIST_LIMIT))

    def _get_shared_token_manager(self) -> EqscTokenManager | None:
        """优先复用 EQSC 通道服务的 token_manager，避免双份鉴权状态。"""
        return EqscChannelService.resolve_shared_token_manager(self.service)

    def _ensure_client(self) -> EqscCencIntensityClient | None:
        """懒创建客户端；共享 token_manager 时不接管其生命周期。"""
        if self._client is not None:
            return self._client

        eqsc_config = self._eqsc_config()
        message_logger = getattr(self.service, "message_logger", None)
        shared_tm = self._get_shared_token_manager()
        if shared_tm is not None:
            self._client = EqscCencIntensityClient(
                shared_tm,
                eqsc_config,
                message_logger=message_logger,
                owns_token_manager=False,
            )
            self._owns_token_manager = False
            return self._client

        token_manager = EqscTokenManager(eqsc_config)
        if not token_manager.is_configured:
            logger.debug(
                "[灾害预警] EQSC CENC 烈度速报轮询：token 未配置，跳过客户端创建"
            )
            return None
        self._client = EqscCencIntensityClient(
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
            "tracked_events": len(self._detail_done),
            "poll_interval_seconds": self._resolve_interval(),
            "list_limit": self._resolve_list_limit(),
        }

    async def start(self) -> None:
        """启动后台轮询任务。"""
        if self.running:
            return
        if not self.is_enabled():
            logger.debug("[灾害预警] EQSC CENC 烈度速报数据源未启用，跳过轮询启动")
            return
        self._task = asyncio.create_task(self._poll_loop(), name="dw_eqsc_cenc_ir_poll")
        self.service.scheduled_tasks.append(self._task)
        logger.info("[灾害预警] EQSC CENC 烈度速报轮询任务已启动")

    async def stop(self) -> None:
        """停止后台轮询并释放客户端 HTTP 会话。

        始终 close 客户端：EqscHttpClient.close() 会关闭 aiohttp 会话，
        且仅在 owns_token_manager=True 时关闭 token_manager，共享鉴权时不会误关。
        """
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
                coordinator.note_poll_fetch_started("eqsc_cenc_ir")
            except Exception as exc:
                logger.debug(
                    f"[灾害预警] EQSC CENC 烈度速报轮询通知静默协调器抓取开始失败: {exc}"
                )
        try:
            await self.fetch_once(emit_event=True)
        except Exception as exc:
            logger.error(f"[灾害预警] EQSC CENC 烈度速报首次抓取失败: {exc}")
            if coordinator is not None:
                try:
                    coordinator.note_poll_fetch_completed("eqsc_cenc_ir", success=False)
                except Exception:
                    pass

        while getattr(self.service, "running", False):
            try:
                interval = self._resolve_interval()
                await asyncio.sleep(interval)
                if not getattr(self.service, "running", False):
                    break
                if not self.is_enabled():
                    logger.debug("[灾害预警] EQSC CENC 烈度速报已禁用，跳过本轮轮询")
                    continue
                await self.fetch_once(emit_event=True)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[灾害预警] EQSC CENC 烈度速报轮询异常: {exc}")

    @staticmethod
    def _build_list_fingerprint(item: dict[str, Any]) -> str:
        """列表侧轻量指纹：用于判断是否需要重新拉详情。"""
        event_id = str(item.get("event_id") or "").strip()
        place = str(item.get("place_name") or "").strip()
        magnitude = item.get("magnitude")
        try:
            mag_text = f"{float(magnitude):.1f}" if magnitude is not None else ""
        except (TypeError, ValueError):
            mag_text = str(magnitude or "").strip()
        return f"{event_id}|{mag_text}|{place}"

    def _remember_event(self, event_id: str, list_fp: str) -> None:
        """记录已处理事件，并裁剪过旧条目。"""
        self._last_list_fingerprints[event_id] = list_fp
        self._detail_done[event_id] = None
        # 保持插入顺序：超出上限时淘汰最旧
        overflow = len(self._detail_done) - self.MAX_TRACKED_EVENTS
        if overflow <= 0:
            return
        stale_ids = list(self._detail_done.keys())[:overflow]
        for key in stale_ids:
            self._detail_done.pop(key, None)
            self._last_list_fingerprints.pop(key, None)

    def _seed_list_items(self, items: list[dict[str, Any]]) -> int:
        """启动静默/首轮播种：只记指纹，不拉详情、不推送。"""
        seeded = 0
        for item in items:
            event_id = str(item.get("event_id") or "").strip()
            if not event_id:
                continue
            list_fp = self._build_list_fingerprint(item)
            self._remember_event(event_id, list_fp)
            seeded += 1
        self._startup_seeded = True
        return seeded

    async def _process_list_item(
        self,
        client: EqscCencIntensityClient,
        item: dict[str, Any],
        *,
        emit_event: bool,
        silence: bool,
    ) -> bool:
        """处理单条列表项：按需拉详情并投递。成功返回 True。"""
        event_id = str(item.get("event_id") or "").strip()
        if not event_id:
            return False

        list_fp = self._build_list_fingerprint(item)
        already_done = event_id in self._detail_done
        same_list = self._last_list_fingerprints.get(event_id) == list_fp
        if already_done and same_list:
            return False

        # 静默期：只播种列表指纹，避免冷启动把历史 N 条全推出去
        if silence or not emit_event:
            self._remember_event(event_id, list_fp)
            return False

        detail = await client.fetch_detail(event_id, use_cache=True)
        if not isinstance(detail, dict) or not detail:
            # 不提交指纹，下轮可重试
            return False

        message = json.dumps(detail, ensure_ascii=False)
        event = self.service.parse_event(self.SOURCE_ID, message)
        if event is None:
            return False

        # _handle_disaster_event 内部吞掉异常并返回 False；
        # 仅成功处理后才提交指纹，避免“失败当完成”导致漏重试。
        handled = await self.service._handle_disaster_event(event)
        if not handled:
            return False

        self._remember_event(event_id, list_fp)
        return True

    def _notify_silence_fetch_completed(self, *, success: bool) -> None:
        """通知静默协调器本轮抓取已结束（成功或失败）。"""
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is None:
            return
        try:
            coordinator.note_poll_fetch_completed("eqsc_cenc_ir", success=success)
        except Exception as exc:
            logger.debug(f"[灾害预警] EQSC CENC 烈度速报轮询通知静默协调器失败: {exc}")

    async def fetch_once(self, *, emit_event: bool = True) -> list[dict[str, Any]]:
        """抓取一轮列表，并按差分拉取详情投递。"""
        client = self._ensure_client()
        if client is None:
            self._notify_silence_fetch_completed(success=False)
            return []

        limit = self._resolve_list_limit()
        items = await client.fetch_list(limit=limit, use_cache=False)
        if not isinstance(items, list):
            self._consecutive_failures += 1
            self._notify_silence_fetch_completed(success=False)
            return []

        self._consecutive_failures = 0
        self._last_success_at = time.time()
        self._notify_silence_fetch_completed(success=True)

        is_silence = False
        silence_checker = getattr(self.service, "is_silencing", None)
        if callable(silence_checker):
            try:
                is_silence = bool(silence_checker())
            except Exception:
                is_silence = False

        # 首轮或静默期：播种列表，避免历史回放刷屏
        if not self._startup_seeded or is_silence:
            seeded = self._seed_list_items(items)
            plugin_logger.debug(
                f"[灾害预警] EQSC CENC 烈度速报播种 {seeded} 条列表指纹"
                f"{'（静默期）' if is_silence else '（首轮）'}"
            )
            return items

        if not emit_event:
            return items

        emitted = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                pushed = await self._process_list_item(
                    client,
                    item,
                    emit_event=True,
                    silence=False,
                )
            except Exception as exc:
                event_id = str(item.get("event_id") or "").strip()
                logger.error(
                    f"[灾害预警] EQSC CENC 烈度速报事件推送失败，已跳过并继续本轮: "
                    f"ID={event_id}, 错误={exc}"
                )
                continue
            if pushed:
                emitted += 1

        if emitted:
            plugin_logger.info(
                f"[灾害预警] EQSC CENC 烈度速报轮询本轮推送 {emitted} 条",
                is_event_linked=True,
                event_stream="earthquake",
            )
        else:
            plugin_logger.debug("[灾害预警] EQSC CENC 烈度速报轮询本轮无变化，跳过推送")
        return items


__all__ = ["EqscCencIntensityPollService"]
