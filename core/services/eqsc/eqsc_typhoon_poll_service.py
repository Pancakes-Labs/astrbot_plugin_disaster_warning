"""
EQSC 台风独立轮询服务。

不依赖 FAN Studio 触发：周期性拉取 /typhoonNMC.json，
对活跃台风按核心参数指纹去重后进入统一事件流水线。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from astrbot.api import logger

from ....utils.plugin_logger import plugin_logger
from ....utils.time_converter import TimeConverter
from ...app.services.eqsc_channel_service import EqscChannelService
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
    # 统一轮询间隔配置键（与海啸、烈度速报共用同一配置项）
    POLL_INTERVAL_CONFIG_KEY = "poll_interval_seconds"

    def __init__(self, service):
        self.service = service
        self._source_runtime_query = SourceRuntimeQueryService(service.config)
        self._task: asyncio.Task | None = None
        # 上一轮轮询中处于活跃态的台风 ID 集合。
        # 用于识别"上一轮活跃 → 本轮停编"的台风，放行一次以推送停编通知；
        # 早已停编的历史台风不在集合中，不会进入投递，避免每轮刷屏。
        self._last_active_ids: set[str] = set()
        # 刚停编但尚未确认投递成功的台风 ID 集合。
        # 若 _handle_disaster_event 返回失败，停编通知仍保留在此集合，
        # 下一轮轮询会继续放行重试，直到投递成功才移除，避免停编通知永久丢失。
        self._pending_deactivate_ids: set[str] = set()
        self._last_success_at: float | None = None
        self._consecutive_failures = 0
        self._client: EqscTyphoonClient | None = None
        # 是否由本服务创建并拥有 client（借用 enrichment 的 client 时为 False）
        self._owns_client = False
        # 仅在自建 client 且自建 token 时有意义；close 时由 client 内部决定是否关 token
        self._owns_token_manager = False
        # 禁用态日志：仅首次跳过本轮时打一次
        self._disabled_logged = False
        # 无变化汇总日志节流：连续 N 轮才打一次，避免无灾害时每轮刷屏
        self._no_change_log_rounds = 0
        self._no_change_log_interval = 30

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def is_enabled(self) -> bool:
        """数据源是否启用。"""
        return self._source_runtime_query.is_source_enabled(self.SOURCE_ID)

    def _eqsc_config(self) -> dict[str, Any]:
        data_sources = self.service.config.get("data_sources", {})
        if not isinstance(data_sources, dict):
            return {}
        eqsc = data_sources.get("eqsc", {})
        return eqsc if isinstance(eqsc, dict) else {}

    def _resolve_interval(self) -> int:
        cfg = self._eqsc_config()
        raw = cfg.get(self.POLL_INTERVAL_CONFIG_KEY, self.DEFAULT_INTERVAL_SECONDS)
        # bool 是 int 子类，不能当作合法间隔。
        if isinstance(raw, bool) or not isinstance(raw, int):
            interval = self.DEFAULT_INTERVAL_SECONDS
        else:
            interval = raw
        return max(self.MIN_INTERVAL_SECONDS, min(interval, self.MAX_INTERVAL_SECONDS))

    def _get_shared_token_manager(self) -> EqscTokenManager | None:
        """优先复用 EQSC 通道服务的 token_manager，避免双份鉴权状态。"""
        return EqscChannelService.resolve_shared_token_manager(self.service)

    def _get_shared_typhoon_client(self) -> EqscTyphoonClient | None:
        """优先复用台风富化服务内的台风客户端。"""
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
            # 借用富化服务的 client 对象：stop 时绝不能 close
            self._client = shared_client
            self._owns_client = False
            self._owns_token_manager = False
            return self._client

        eqsc_config = self._eqsc_config()
        message_logger = getattr(self.service, "message_logger", None)
        shared_tm = self._get_shared_token_manager()
        if shared_tm is not None:
            # 自建 client，仅共享 token：stop 时 close client（不关 token）
            self._client = EqscTyphoonClient(
                shared_tm,
                eqsc_config,
                message_logger=message_logger,
                owns_token_manager=False,
            )
            self._owns_client = True
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
        self._owns_client = True
        self._owns_token_manager = True
        return self._client

    def get_runtime_status(self) -> dict[str, Any]:
        """供健康面板读取的轻量运行态。"""
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
        """启动后台轮询任务。"""
        if self.running:
            return
        if not self.is_enabled():
            logger.info("[灾害预警] EQSC 台风数据源未启用，跳过轮询启动")
            return
        self._task = asyncio.create_task(self._poll_loop(), name="dw_eqsc_typhoon_poll")
        self.service.scheduled_tasks.append(self._task)
        logger.debug("[灾害预警] EQSC 台风轮询任务已启动")

    async def stop(self) -> None:
        """停止后台轮询；仅关闭本服务自己创建的客户端。

        借用 TyphoonEnrichmentService 的 client 时不得 close，否则会拆掉富化侧会话。
        自建 client 时始终 close（session 由 client 管理；token 是否关闭看 owns_token_manager）。
        """
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._client is not None and self._owns_client:
            await self._client.close()
        self._client = None
        self._owns_client = False

    async def _poll_loop(self) -> None:
        """后台轮询循环。"""
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is not None:
            try:
                coordinator.note_poll_fetch_started("eqsc_typhoon")
            except Exception as exc:
                logger.debug(
                    f"[灾害预警] EQSC 台风轮询通知静默协调器抓取开始失败: {exc}"
                )
        try:
            await self.fetch_once(emit_event=True)
        except Exception as exc:
            logger.error(f"[灾害预警] EQSC 台风首次抓取失败: {exc}")
            if coordinator is not None:
                try:
                    coordinator.note_poll_fetch_completed("eqsc_typhoon", success=False)
                except Exception:
                    pass

        while getattr(self.service, "running", False):
            try:
                interval = self._resolve_interval()
                await asyncio.sleep(interval)
                if not getattr(self.service, "running", False):
                    break
                if not self.is_enabled():
                    # 仅首次禁用时打一次，避免配置禁用后每轮刷屏
                    if not self._disabled_logged:
                        logger.debug("[灾害预警] EQSC 台风已禁用，跳过本轮轮询")
                        self._disabled_logged = True
                    continue
                if self._disabled_logged:
                    # 从禁用状态恢复：重置无变化计数器，避免禁用期的旧累计值
                    # 导致恢复后立即打印"无变化"汇总日志
                    self._no_change_log_rounds = 0
                self._disabled_logged = False
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
    def _parse_track_time(value: Any) -> datetime | None:
        """把 EQSC 时间字符串解析为带时区的 datetime。

        与 typhoon_event_adapter._normalize_time 保持一致的时区处理规则：
        无时区信息的时间按北京时间（UTC+8）解释，避免混合时区导致排序错误。
        无法解析时返回 None。
        """
        if not value:
            return None
        # 直接传原始值给 TimeConverter.parse_datetime，不先转 str：
        # 该方法已支持 str/int/float/datetime，先转 str 会使数值时间戳无法识别。
        parsed = TimeConverter.parse_datetime(value)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        return parsed

    @staticmethod
    def _latest_track_timestamp(raw: dict[str, Any]) -> float:
        """提取台风历史轨迹中最新的观测时间戳，用于同源去重排序。

        EQSC historyTrack 顺序不保证，取所有节点时间解析后的最大 timestamp。
        使用带时区解析而非原始字符串字典序比较，避免混合时区
        （如 +08:00 与 Z）导致错误选出非最新条目。
        无法解析的时间视为 0（排序时排在最前），确保不会因个别坏数据
        导致整个条目被丢弃。
        """
        history = raw.get("historyTrack") or raw.get("history_track") or []
        if not isinstance(history, list):
            return 0.0
        timestamps: list[float] = []
        for node in history:
            if not isinstance(node, dict):
                continue
            parsed = EqscTyphoonPollService._parse_track_time(node.get("time"))
            if parsed is not None:
                timestamps.append(parsed.timestamp())
        return max(timestamps) if timestamps else 0.0

    @staticmethod
    def _track_node_fingerprints(raw: dict[str, Any]) -> set[str]:
        """提取历史轨迹所有观测节点的 (timestamp, lat, lon) 指纹集合。

        用于检测两个台风是否为同一物理台风的不同编报阶段条目：
        EQSC 对同一台风在未编号/已编号/占位阶段会返回不同 id 的条目，
        但它们的轨迹节点完全重叠。

        跳过缺失坐标（lat/lon 为 None）的节点，避免 None 值参与指纹
        导致不同数据源间的虚假不匹配。

        时间键使用解析后的 timestamp（秒级整数），而非原始时间字符串：
        相同瞬间的 Z 和 +08:00 表示会生成相同指纹，避免不同编报阶段
        条目因时区表示差异而被误判为不同源。
        """
        history = raw.get("historyTrack") or raw.get("history_track") or []
        if not isinstance(history, list):
            return set()
        fingerprints: set[str] = set()
        for node in history:
            if not isinstance(node, dict):
                continue
            parsed = EqscTyphoonPollService._parse_track_time(node.get("time"))
            if parsed is None:
                continue
            lat = to_float(node.get("latitude"))
            lon = to_float(node.get("longitude"))
            # 跳过缺失坐标的节点：None 值参与指纹会导致不同数据源间
            # 的虚假不匹配（如某源缺坐标而另一源有坐标时指纹不同）。
            if lat is None or lon is None:
                continue
            # 用秒级整数 timestamp 作时间键，消除时区表示差异。
            fingerprints.add(f"{int(parsed.timestamp())}|{lat:.1f}|{lon:.1f}")
        return fingerprints

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
        """筛出本轮可处理的台风对象：活跃台风 + 刚停编台风。

        EQSC 台风列表同时包含活跃与历史台风（客户端文档已注明）：
        - 活跃台风（isActive=True）始终进入投递；
        - 上一轮活跃、本轮停编（isActive=False）的台风放行一次，
          用于推送"停编通知"（停编时即使核心参数未变化也应推一条）；
        - 上一轮停编但投递失败的台风（在 _pending_deactivate_ids 中）继续放行重试，
          直到投递成功后才从该集合移除，避免停编通知因瞬时失败永久丢失；
        - 早已停编的历史台风不在上述两个集合中，不进入投递，
          由 time_rule 兜底过滤，避免冷启动/每轮刷屏。

        同源去重：EQSC 对同一物理台风在不同编报阶段会返回多个条目
        （如 NAMELESS_07 停编 + 2619 活跃 + 26XX 占位活跃），
        它们原始 id 不同且归一化键也不同（TD07 / 2619 / TD_26XX），
        但轨迹节点完全重叠。此处用轨迹节点指纹集合检测同源关系：
        若两个台风的轨迹节点有显著重叠（交集 >= 较小集合的 60%），
        视为同一物理台风，只保留最新观测时间的条目，
        避免同一台风推多条且停编/活跃状态反复横跳。
        """
        # 第一阶段：同源去重。
        # 先按归一化 id 去重（处理同 id 的重复条目），
        # 再按轨迹重叠检测跨 id 的同源条目。
        valid_items: list[dict[str, Any]] = []
        for item in typhoon_list:
            if not isinstance(item, dict):
                continue
            raw_id = clean_text(item.get("id"))
            if not raw_id:
                continue
            valid_items.append(item)

        # 按最新观测时间降序排列，确保同源去重时保留最新条目
        valid_items.sort(
            key=lambda it: self._latest_track_timestamp(it),
            reverse=True,
        )

        deduped: list[dict[str, Any]] = []
        kept_fingerprints: list[set[str]] = []
        for item in valid_items:
            item_fps = self._track_node_fingerprints(item)
            is_duplicate = False
            for idx, kept_fps in enumerate(kept_fingerprints):
                if not item_fps or not kept_fps:
                    continue
                # 轨迹节点重叠率 >= 60% 视为同源
                overlap = len(item_fps & kept_fps)
                smaller = min(len(item_fps), len(kept_fps))
                if smaller > 0 and overlap / smaller >= 0.6:
                    # 同源：当前条目已按时间降序排列，
                    # 先加入的（deduped 中）观测时间更晚，跳过当前条目。
                    is_duplicate = True
                    break
            if not is_duplicate:
                deduped.append(item)
                kept_fingerprints.append(item_fps)

        # 第二阶段：从去重后的条目中筛出活跃 + 刚停编台风。
        # ID 口径统一：使用归一化 ID 检查 _last_active_ids / _pending_deactivate_ids，
        # 与 _process_typhoon_updates 写入这两个集合时使用的归一化 ID 保持一致，
        # 避免 26XX 停编投递失败后集合保存 TD_26XX，下一轮却用 26XX 检查导致漏重试。
        current_active_ids: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for item in deduped:
            raw_id = clean_text(item.get("id"))
            if not raw_id:
                continue
            typhoon_id = normalize_typhoon_id(raw_id)
            if not typhoon_id:
                continue
            if self._is_active_typhoon(item):
                current_active_ids.add(typhoon_id)
                # 台风重新活跃：若之前处于待确认停编集合中则清理，避免残留导致误放行。
                self._pending_deactivate_ids.discard(typhoon_id)
                candidates.append(item)
            elif (
                typhoon_id in self._last_active_ids
                or typhoon_id in self._pending_deactivate_ids
            ):
                # 刚停编 或 停编投递失败待重试：放行以推送/重试停编通知
                candidates.append(item)

        # 记录本轮活跃集合，供下一轮识别"刚停编"台风。
        # 注意：此处覆盖的是活跃集合，不影响 _pending_deactivate_ids 的待重试保留。
        self._last_active_ids = current_active_ids
        return candidates

    async def _process_typhoon_updates(
        self, active_items: list[dict[str, Any]]
    ) -> tuple[int, int]:
        """投递活跃台风事件；单事件失败不中断整批。

        去重判定与 FAN 触发路径及事件流水线完全统一：
        统一使用去重服务的只读判定 peek_typhoon_should_push
        （基于 _typhoon_cache 与 _generate_typhoon_fingerprint），
        不再在轮询侧自建指纹缓存，避免「去重器判定过滤、
        轮询侧却统计为推送」的口径矛盾。

        Returns:
            (emitted, filtered)：成功推送条数与被去重过滤条数。
        """
        emitted = 0
        filtered = 0
        for raw in active_items:
            envelope = self._build_live_envelope(raw)
            if envelope is None or not isinstance(envelope.event, TyphoonEvent):
                continue

            typhoon = envelope.event
            typhoon_id = normalize_typhoon_id(typhoon.typhoon_id)
            if not typhoon_id:
                continue

            # 停编标记：用于决定投递失败时是否保留待重试集合。
            deactivated = not bool(getattr(typhoon, "is_active", True))

            # 启动静默期：不推送、不统计，仅调用 _handle_disaster_event
            # 让其内部的 _seed_event_for_silence 播种去重服务的 _typhoon_cache，
            # 避免静默结束后首次推送因去重缓存为空而被误放行（重载后重复推送的根因）。
            is_silence = getattr(self.service, "is_silencing", None)
            if callable(is_silence) and is_silence():
                try:
                    handled = await self.service._handle_disaster_event(envelope)
                except Exception as exc:
                    logger.debug(
                        f"[灾害预警] EQSC 台风静默期播种去重指纹失败（已忽略）: "
                        f"ID 为 {typhoon_id}, 错误信息：{exc}"
                    )
                    handled = False
                # 仅播种成功（返回 True）时才视为已处理并清理待重试停编 ID；
                # 返回 False 或抛出异常时保留（停编台风重新加入）待重试集合，
                # 避免先前投递失败的停编通知在静默期被误清理而永久丢失。
                if handled:
                    self._pending_deactivate_ids.discard(typhoon_id)
                elif deactivated:
                    self._pending_deactivate_ids.add(typhoon_id)
                continue

            # 统一去重判定：与 _handle_disaster_event 内 FAN 触发路径一致，
            # 使用去重服务的只读判定（不写缓存；真正写入由流水线
            # should_push_event 在放行时完成）。被过滤者不计入本轮推送统计。
            deduplicator = getattr(
                getattr(self.service, "message_manager", None), "deduplicator", None
            )
            peek = getattr(deduplicator, "peek_typhoon_should_push", None)
            if callable(peek) and not peek(envelope):
                # 去重判定已通过（内容有变化）才会走到这里；被过滤意味着
                # 该停编通知此前已成功推送过，无需再保留待重试。
                self._pending_deactivate_ids.discard(typhoon_id)
                filtered += 1
                continue

            try:
                handled = await self.service._handle_disaster_event(envelope)
            except Exception as exc:
                # 单台风软失败：记录后继续处理其余活跃台风；
                # 去重缓存未被写入，下一轮可重试该台风。
                logger.error(
                    f"[灾害预警] EQSC 台风事件推送失败，已跳过并继续本轮: "
                    f"ID 为 {typhoon_id}, 错误信息：{exc}"
                )
                if deactivated:
                    # 停编通知投递失败：保留 ID，下一轮继续放行重试，
                    # 避免该停编通知永久丢失。
                    self._pending_deactivate_ids.add(typhoon_id)
                continue
            if not handled:
                logger.error(
                    f"[灾害预警] EQSC 台风事件处理未成功，已跳过并继续本轮: "
                    f"ID 为 {typhoon_id}"
                )
                if deactivated:
                    self._pending_deactivate_ids.add(typhoon_id)
                continue
            # 投递成功：停编通知已送达，从待重试集合中移除。
            self._pending_deactivate_ids.discard(typhoon_id)
            emitted += 1

        return emitted, filtered

    def _notify_silence_fetch_completed(self, *, success: bool) -> None:
        """通知静默协调器本轮抓取已结束（成功或失败）。"""
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is None:
            return
        try:
            coordinator.note_poll_fetch_completed("eqsc_typhoon", success=success)
        except Exception as exc:
            logger.debug(f"[灾害预警] EQSC 台风轮询通知静默协调器失败: {exc}")

    async def fetch_once(self, *, emit_event: bool = True) -> list[dict[str, Any]]:
        """抓取一轮 EQSC 台风列表，可选投递变化事件。"""
        client = self._ensure_client()
        if client is None:
            self._notify_silence_fetch_completed(success=False)
            return []

        # 轮询侧强制绕过短缓存，确保按间隔拿到最新列表。
        typhoon_list = await client.fetch_typhoon_list(use_cache=False)
        if not isinstance(typhoon_list, list):
            self._consecutive_failures += 1
            self._notify_silence_fetch_completed(success=False)
            return []

        active_items = self._filter_active_items(typhoon_list)

        # 客户端失败时常返回空列表；与"确实无台风"无法严格区分，
        # 这里仅在拿到可解析对象时记成功。
        self._consecutive_failures = 0
        self._last_success_at = time.time()

        if not emit_event:
            # 非投递轮（如预热）无播种需求，直接通知抓取成功。
            self._notify_silence_fetch_completed(success=True)
            return active_items

        # 先完成事件投递（含静默期指纹播种），再通知抓取完成，
        # 避免静默协调器提前 READY 导致首批台风指纹未播种而重复推送。
        emitted, filtered = await self._process_typhoon_updates(active_items)
        self._notify_silence_fetch_completed(success=True)
        # 轮询汇总默认 DEBUG，避免每轮轮询都刷 INFO；
        # 有推送价值的事件进入流水线后，会由事件级"会话筛选/推送完成"汇总
        # （INFO 级）提供可观测性。需要查看轮询明细时开启 DEBUG 级别。
        if emitted or filtered:
            self._no_change_log_rounds = 0
            plugin_logger.debug(
                f"[灾害预警] EQSC 台风轮询汇总：推送 {emitted} 条更新，"
                f"跳过 {filtered} 条未变化",
                is_event_linked=True,
                event_stream="typhoon",
                is_silent_window=True,
            )
        else:
            # 无变化为常态，连续 N 轮才打一次，避免无灾害时每轮刷屏
            self._no_change_log_rounds += 1
            if self._no_change_log_rounds >= self._no_change_log_interval:
                self._no_change_log_rounds = 0
                plugin_logger.debug(
                    "[灾害预警] EQSC 台风轮询本轮无变化，跳过推送",
                    is_event_linked=True,
                    event_stream="typhoon",
                    is_silent_window=True,
                )
        return active_items


__all__ = ["EqscTyphoonPollService"]
