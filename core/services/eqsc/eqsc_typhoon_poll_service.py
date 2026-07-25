"""
EQSC 台风独立轮询服务。

不依赖 FAN Studio 触发：周期性拉取 /typhoonNMC.json，
对活跃台风按核心参数指纹去重后进入统一事件流水线。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from astrbot.api import logger

from ....utils.plugin_logger import plugin_logger
from ...domain.event_models import TyphoonEvent
from ...domain.typhoon import (
    build_typhoon_event_envelope,
    clean_text,
    normalize_typhoon_id,
    to_float,
)
from ...network.http.eqsc_token_manager import EqscTokenManager
from ...network.http.eqsc_typhoon_client import EqscTyphoonClient
from ..query.source_runtime_query_service import SourceRuntimeQueryService


class EqscTyphoonPollService:
    """EQSC 台风 HTTP 轮询服务。"""

    SOURCE_ID = "typhoon_eqsc"
    DEFAULT_INTERVAL_SECONDS = 120
    MIN_INTERVAL_SECONDS = 30
    MAX_INTERVAL_SECONDS = 600

    def __init__(self, service):
        self.service = service
        self._source_runtime_query = SourceRuntimeQueryService(service.config)
        self._task: asyncio.Task | None = None
        self._last_fingerprints: dict[str, str] = {}
        self._last_success_at: float | None = None
        self._consecutive_failures = 0
        self._client: EqscTyphoonClient | None = None
        self._owns_token_manager = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def is_enabled(self) -> bool:
        """数据源是否启用（组总闸 + typhoon_enrichment 子开关）。"""
        return self._source_runtime_query.is_source_enabled(self.SOURCE_ID)

    def _eqsc_config(self) -> dict[str, Any]:
        data_sources = self.service.config.get("data_sources", {})
        if not isinstance(data_sources, dict):
            return {}
        eqsc = data_sources.get("eqsc", {})
        return eqsc if isinstance(eqsc, dict) else {}

    def _resolve_interval(self) -> int:
        cfg = self._eqsc_config()
        raw = cfg.get("typhoon_poll_interval_seconds", self.DEFAULT_INTERVAL_SECONDS)
        # bool 是 int 子类，不能当作合法间隔。
        if isinstance(raw, bool) or not isinstance(raw, int):
            interval = self.DEFAULT_INTERVAL_SECONDS
        else:
            interval = raw
        return max(self.MIN_INTERVAL_SECONDS, min(interval, self.MAX_INTERVAL_SECONDS))

    def _get_shared_token_manager(self) -> EqscTokenManager | None:
        """优先复用台风富化服务的 token_manager，避免双份鉴权状态。"""
        enrichment = getattr(self.service, "typhoon_enrichment_service", None)
        if enrichment is None:
            return None
        token_manager = getattr(enrichment, "_token_manager", None)
        if isinstance(token_manager, EqscTokenManager):
            return token_manager
        return None

    def _get_shared_typhoon_client(self) -> EqscTyphoonClient | None:
        """优先复用富化服务内的台风客户端。"""
        enrichment = getattr(self.service, "typhoon_enrichment_service", None)
        if enrichment is None:
            return None
        client = getattr(enrichment, "_typhoon_client", None)
        if isinstance(client, EqscTyphoonClient):
            return client
        return None

    def _ensure_client(self) -> EqscTyphoonClient | None:
        """懒创建台风客户端；共享 token/client 时不接管其生命周期。"""
        if self._client is not None:
            return self._client

        shared_client = self._get_shared_typhoon_client()
        if shared_client is not None:
            self._client = shared_client
            self._owns_token_manager = False
            return self._client

        eqsc_config = self._eqsc_config()
        message_logger = getattr(self.service, "message_logger", None)
        shared_tm = self._get_shared_token_manager()
        if shared_tm is not None:
            self._client = EqscTyphoonClient(
                shared_tm,
                eqsc_config,
                message_logger=message_logger,
                owns_token_manager=False,
            )
            self._owns_token_manager = False
            return self._client

        token_manager = EqscTokenManager(eqsc_config)
        if not token_manager.is_configured:
            logger.debug("[灾害预警] EQSC 台风轮询：token 未配置，跳过客户端创建")
            return None
        self._client = EqscTyphoonClient(
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
            "tracked_typhoons": len(self._last_fingerprints),
            "poll_interval_seconds": self._resolve_interval(),
        }

    async def start(self) -> None:
        """启动后台轮询任务。"""
        if self.running:
            return
        if not self.is_enabled():
            logger.info("[灾害预警] EQSC 台风数据源未启用，跳过轮询启动")
            return
        self._task = asyncio.create_task(self._poll_loop(), name="dw_eqsc_typhoon_poll")
        self.service.scheduled_tasks.append(self._task)
        logger.info("[灾害预警] EQSC 台风轮询任务已启动")

    async def stop(self) -> None:
        """停止后台轮询并按需释放客户端。"""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._client is not None and self._owns_token_manager:
            await self._client.close()
        self._client = None

    async def _poll_loop(self) -> None:
        """后台轮询循环。"""
        try:
            await self.fetch_once(emit_event=True)
        except Exception as exc:
            logger.error(f"[灾害预警] EQSC 台风首次抓取失败: {exc}")

        while getattr(self.service, "running", False):
            try:
                interval = self._resolve_interval()
                await asyncio.sleep(interval)
                if not getattr(self.service, "running", False):
                    break
                if not self.is_enabled():
                    logger.debug("[灾害预警] EQSC 台风已禁用，跳过本轮轮询")
                    continue
                await self.fetch_once(emit_event=True)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[灾害预警] EQSC 台风轮询异常: {exc}")

    @staticmethod
    def _is_active_typhoon(raw: dict[str, Any]) -> bool:
        """判断 EQSC 台风对象是否为活跃态。"""
        if "isActive" in raw:
            return bool(raw.get("isActive"))
        # 缺省字段时保守视为活跃，避免漏推
        return True

    @staticmethod
    def _build_fingerprint_from_event(typhoon: TyphoonEvent) -> str:
        """与去重服务一致的核心参数指纹。"""
        return "|".join(
            [
                str(typhoon.typhoon_type or "").strip(),
                EqscTyphoonPollService._normalize_value(typhoon.latitude),
                EqscTyphoonPollService._normalize_value(typhoon.longitude),
                EqscTyphoonPollService._normalize_value(typhoon.wind_speed),
                EqscTyphoonPollService._normalize_value(typhoon.pressure),
                str(typhoon.move_direction or "").strip(),
                EqscTyphoonPollService._normalize_value(typhoon.move_speed),
                EqscTyphoonPollService._normalize_value(typhoon.radius7),
                EqscTyphoonPollService._normalize_value(typhoon.radius10),
                "1" if bool(typhoon.is_active) else "0",
            ]
        )

    @staticmethod
    def _normalize_value(value: Any) -> str:
        number = to_float(value)
        if number is None:
            return ""
        return f"{number:.4f}"

    def _build_live_envelope(self, raw: dict[str, Any]):
        """从 EQSC 原始对象构建实时推送事件。"""
        envelope = build_typhoon_event_envelope(
            raw,
            source_id=self.SOURCE_ID,
            data_mode="eqsc",
        )
        if envelope is None:
            return None

        domain = envelope.event
        if isinstance(domain, TyphoonEvent):
            # 列表接口 isActive 优先；缺省时保持适配器结果。
            if "isActive" in raw:
                domain.is_active = bool(raw.get("isActive"))
        return envelope

    def _filter_active_items(self, typhoon_list: list[Any]) -> list[dict[str, Any]]:
        """从列表中筛出可处理的活跃台风对象。"""
        active_items: list[dict[str, Any]] = []
        for item in typhoon_list:
            if not isinstance(item, dict):
                continue
            if not clean_text(item.get("id")):
                continue
            if not self._is_active_typhoon(item):
                continue
            active_items.append(item)
        return active_items

    async def _process_typhoon_updates(self, active_items: list[dict[str, Any]]) -> int:
        """按指纹去重后投递变化事件；单事件失败不中断整批。"""
        seen_ids: set[str] = set()
        emitted = 0
        for raw in active_items:
            envelope = self._build_live_envelope(raw)
            if envelope is None or not isinstance(envelope.event, TyphoonEvent):
                continue

            typhoon = envelope.event
            typhoon_id = normalize_typhoon_id(typhoon.typhoon_id)
            if not typhoon_id:
                continue
            seen_ids.add(typhoon_id)

            fingerprint = self._build_fingerprint_from_event(typhoon)
            if self._last_fingerprints.get(typhoon_id) == fingerprint:
                continue

            # 启动静默期：不进入推送链路，但仍提交指纹，避免静默结束后整批误报“推送”。
            is_silence = getattr(self.service, "is_in_silence_period", None)
            if callable(is_silence) and is_silence():
                self._last_fingerprints[typhoon_id] = fingerprint
                continue

            try:
                await self.service._handle_disaster_event(envelope)
            except Exception as exc:
                # 单台风软失败：记录后继续处理其余活跃台风；
                # 不提交指纹，下一轮可重试该台风。
                logger.error(
                    f"[灾害预警] EQSC 台风事件推送失败，已跳过并继续本轮: "
                    f"ID 为 {typhoon_id}, 错误信息：{exc}"
                )
                continue
            self._last_fingerprints[typhoon_id] = fingerprint
            emitted += 1

        # 清理已消亡台风的指纹缓存，避免无限增长。
        stale_ids = [key for key in self._last_fingerprints if key not in seen_ids]
        for key in stale_ids:
            self._last_fingerprints.pop(key, None)
        return emitted

    async def fetch_once(self, *, emit_event: bool = True) -> list[dict[str, Any]]:
        """抓取一轮 EQSC 台风列表，可选投递变化事件。"""
        client = self._ensure_client()
        if client is None:
            return []

        # 轮询侧强制绕过短缓存，确保按间隔拿到最新列表。
        typhoon_list = await client.fetch_typhoon_list(use_cache=False)
        if not isinstance(typhoon_list, list):
            self._consecutive_failures += 1
            return []

        active_items = self._filter_active_items(typhoon_list)

        # 客户端失败时常返回空列表；与“确实无台风”无法严格区分，
        # 这里仅在拿到可解析对象时记成功。
        self._consecutive_failures = 0
        self._last_success_at = time.time()

        if not emit_event:
            return active_items

        emitted = await self._process_typhoon_updates(active_items)
        if emitted:
            plugin_logger.info(
                f"[灾害预警] EQSC 台风轮询本轮推送 {emitted} 条更新",
                is_event_linked=True,
            )
        else:
            plugin_logger.debug("[灾害预警] EQSC 台风轮询本轮无变化，跳过推送")
        return active_items


__all__ = ["EqscTyphoonPollService"]
