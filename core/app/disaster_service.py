"""
灾害预警核心服务。

该模块承担应用层总装配职责：
1. 持有配置、上下文与共享运行状态；
2. 装配消息、统计、缓存、查询等基础能力；
3. 协调生命周期、运行时调度、通知与事件流水线；
4. 对外暴露统一的启动、停止、状态查询与事件入口。
"""

import asyncio
import os
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from astrbot.api import logger
from astrbot.api.star import StarTools

if TYPE_CHECKING:
    from ..services.telemetry.telemetry_service import TelemetryManager

from ..domain.event_models import EventEnvelope
from ..message.message_logger import MessageLogger
from ..message.message_manager import MessagePushManager
from ..message.presenters.presenter_registry import (
    get_presenter,
    get_text_presenter_keys,
)
from ..network.event_ingress_dispatch_service import EventIngressDispatchService
from ..network.source_ingress_side_effect_service import SourceIngressSideEffectService
from ..network.source_message_router import SourceMessageRouter
from ..network.websocket.websocket_manager import HTTPDataFetcher, WebSocketManager
from ..parsers.parser_registry import (
    create_parser_for_source,
    validate_catalog_parser_names,
)
from ..services.config.config_service import ConfigAccessor
from ..services.config.connection_plan_builder import ConnectionPlanBuilder
from ..services.eqsc.eqsc_tsunami_poll_service import EqscTsunamiPollService
from ..services.eqsc.eqsc_typhoon_poll_service import EqscTyphoonPollService
from ..services.geo.cn_seis_int_loc_loader import get_district_points
from ..services.geo.jma_seis_int_loc_loader import get_sect_map
from ..services.geo.region_service import region_service
from ..services.geo.travel_time_loader import get_travel_times
from ..services.notification import NotificationCenter
from ..services.query.earthquake_list_service import EarthquakeListService
from ..services.query.eew_query_state_service import EEWQueryStateService
from ..services.query.source_runtime_query_service import SourceRuntimeQueryService
from ..services.snet.snet_poll_service import SnetPollService
from ..sources.source_catalog import SOURCE_CATALOG
from ..sources.source_institution_catalog import get_institution_catalog
from ..storage.session_config_manager import SessionConfigManager
from ..storage.statistics_manager import StatisticsManager
from .pipeline.event_pipeline import EventPipeline
from .runtime.disaster_service_cache import DisasterServiceCacheService
from .runtime.disaster_service_lifecycle import DisasterServiceLifecycleService
from .runtime.disaster_service_notice import DisasterServiceNoticeService
from .runtime.disaster_service_reconnect import DisasterServiceReconnectService
from .runtime.disaster_service_runtime import DisasterServiceRuntimeService
from .runtime.disaster_service_status import DisasterServiceStatusService
from .services.typhoon_enrichment_service import TyphoonEnrichmentService
from .services.typhoon_history_rebuild_service import TyphoonHistoryRebuildService


def _is_source_enabled_by_catalog(source_id: str, data_sources: dict[str, Any]) -> bool:
    """根据统一的数据源目录判断某个数据源是否启用。

    与 SourceRuntimeQueryService.is_source_enabled / SourceEnabledRule 对齐：
    缺省为 False（opt-in），避免新源（如 S-Net）在配置缺失时被误判为开启。
    """
    if not isinstance(data_sources, dict):
        return False

    source_entry = SOURCE_CATALOG.get(source_id)
    if source_entry is None:
        return False

    # 获取数据源组配置，如果组被禁用，则该数据源禁用
    group_cfg = data_sources.get(source_entry.config_group, {})
    if not isinstance(group_cfg, dict):
        return False

    if not bool(group_cfg.get("enabled", False)):
        return False

    # 返回具体数据源开关的布尔值（缺省 False）
    return bool(group_cfg.get(source_entry.config_key, False))


class DisasterWarningService:
    """灾害预警核心服务。"""

    # 地震预警查询状态的有效时间窗口，超过后会按历史状态处理。
    EEW_VALID_DURATION_SECONDS = 300

    def __init__(self, config: dict[str, Any], context):
        # 主服务负责持有全局依赖、共享状态，并装配生命周期、运行时调度、通知与缓存等子服务。
        self.config = config  # 插件的全局配置字典
        self.context = context  # 框架上下文环境
        self.running = False  # 运行状态标识
        # 启停锁用于避免重复启动或并发停止时出现状态竞争。
        self._start_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._stopping = False  # 正在停止标识

        # 这一组对象属于全局基础能力，基本会被多个子服务共享使用。
        self.message_logger = MessageLogger(
            config, "disaster_warning"
        )  # 消息日志持久化服务
        self.statistics_manager = StatisticsManager(config)  # 灾害事件统计管理器
        self._telemetry: TelemetryManager | None = None  # 遥测服务管理器
        self.session_config_manager = SessionConfigManager(config)  # 临时会话配置管理器
        self.source_runtime_query = SourceRuntimeQueryService(
            config
        )  # 数据源运行时查询辅助服务

        # WebSocket 管理器与消息推送管理器属于核心基础设施，需在初始化阶段提前装配。
        self.ws_manager = WebSocketManager(
            config.get("websocket_config", {}),
            self.message_logger,
            telemetry=self._telemetry,
        )
        # 接口抓取器会在初始化阶段创建，用于 Wolfx 等接口型数据源的定时拉取。
        self.http_fetcher: HTTPDataFetcher | None = None
        self.message_manager = MessagePushManager(
            config, context, telemetry=self._telemetry
        )
        # 注入会话配置管理器引用，使推送链路可查询会话备注名等展示信息。
        self.message_manager.set_session_config_manager(self.session_config_manager)
        # 用于离线通知节流，避免同一连接异常在短时间内反复刷屏。
        self._offline_notification_state: dict[str, dict[str, float]] = {}

        # 解析器按数据源编号注册，后续所有事件入口都通过统一映射表调度。
        self.parsers = {}
        self._initialize_parsers()

        # 这些运行时容器会被生命周期服务与运行时服务共同维护。
        self.connections = {}  # 启用的连接方案配置表
        self.connection_tasks = []  # WebSocket 监听任务句柄列表
        self.scheduled_tasks = []  # 定时轮询拉取任务句柄列表
        self.background_tasks: set[asyncio.Task] = set()  # 通用异步后台任务集合
        self.web_admin_server = None  # Web管理后台服务器实例

        # 地震列表缓存主要服务于命令查询与管理端展示。
        self.earthquake_lists = {
            "cenc": {},
            "jma": {},
        }  # 中国地震局及日本气象厅地震列表数据缓存
        self.earthquake_list_service = EarthquakeListService(self.earthquake_lists)

        # 缓存文件统一落在插件数据目录下，便于跨重启恢复运行状态。
        self.storage_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
        self.cache_file = os.path.join(self.storage_dir, "earthquake_lists_cache.json")
        self.eew_query_cache_file = os.path.join(
            self.storage_dir, "eew_query_cache.json"
        )
        # 地震预警查询状态按机构维度保存，用于命令与管理端复用。
        self.eew_query_state: dict[str, dict[str, Any]] = {}
        self.eew_query_service = EEWQueryStateService(
            institutions=get_institution_catalog("eew"),
            valid_duration_seconds=self.EEW_VALID_DURATION_SECONDS,
            source_enabled_checker=_is_source_enabled_by_catalog,
        )
        # 通知中心独立维护远端通知同步、本地缓存和已读状态，供管理端前端复用。
        self.notification_center = NotificationCenter(self)
        # 台风 EQSC 富化服务，在台风事件进入流水线前按需拉取 EQSC 详细数据。
        # 注入 message_logger，使 EQSC HTTP 响应进入原始消息日志链路。
        self.typhoon_enrichment_service = TyphoonEnrichmentService(
            config,
            message_logger=self.message_logger,
        )
        # 冷启动历史重建编排独立服务，避免主服务继续堆叠台风业务细节。
        self.typhoon_history_rebuild_service = TyphoonHistoryRebuildService(
            enrichment_service=self.typhoon_enrichment_service,
            statistics_manager=self.statistics_manager,
        )
        # S-Net MSIL 瓦片轮询（直连 HTTP，不走 WebSocket）
        self.snet_poll_service = SnetPollService(self)
        # EQSC JMA 海啸 HTTP 轮询（复用 EQSC 令牌，作为 P2P 高优先级补充）
        self.eqsc_tsunami_poll_service = EqscTsunamiPollService(self)
        # EQSC 台风 HTTP 独立轮询（不依赖 FAN 触发）
        self.eqsc_typhoon_poll_service = EqscTyphoonPollService(self)
        self._setup_runtime_services()

    def _setup_runtime_services(self) -> None:
        """装配灾害服务运行时子服务。"""
        # 以下服务分别承接事件流水线、生命周期、运行时调度、缓存、状态整理、通知、重连与接入旁路编排，主服务本身只保留高层协调职责。
        self.event_pipeline = EventPipeline(self)  # 事件流处理流水线
        self.lifecycle_service = DisasterServiceLifecycleService(
            self
        )  # 服务启停生命周期服务
        self.runtime_service = DisasterServiceRuntimeService(
            self
        )  # 运行时网络/轮询驱动服务
        self.cache_service = DisasterServiceCacheService(self)  # 历史缓存管理服务
        self.status_service = DisasterServiceStatusService(self)  # 运行状态统计上报服务
        self.notice_service = DisasterServiceNoticeService(
            self
        )  # 服务通知、下线广播服务
        self.reconnect_service = DisasterServiceReconnectService(
            self
        )  # 重连机制决策服务
        self.source_ingress_side_effect_service = SourceIngressSideEffectService(
            self
        )  # 数据流入的副作用处理服务
        self.event_ingress_dispatch_service = EventIngressDispatchService(
            self
        )  # 数据流入分配服务

    def _initialize_parsers(self):
        """初始化各数据源对应的解析器。"""
        for source_id in SOURCE_CATALOG:
            # 解析器创建逻辑集中在注册表中维护，这里只负责装配与缓存实例。
            parser = create_parser_for_source(source_id, self.message_logger)
            if parser is not None:
                self.parsers[source_id] = parser

    def parse_event(self, source_id: str, message):
        """统一的解析器调度入口。"""
        parser = self.parsers.get(source_id)
        if parser is None:
            return None
        # 主服务不关心具体解析细节，只负责按数据源分发到正确的解析器。
        return parser.parse_message(message)

    def _check_registry_integrity(self):
        """检查数据源目录与展示注册入口的一致性，缺少对应渲染器时输出警告日志。"""
        source_presentation_types = {
            entry.presentation_type for entry in SOURCE_CATALOG.values()
        }
        source_text_presenter_keys = {
            entry.text_presenter_key
            for entry in SOURCE_CATALOG.values()
            if isinstance(entry.text_presenter_key, str)
            and entry.text_presenter_key.strip()
        }
        source_enum_ids = {
            entry.source_id
            for entry in SOURCE_CATALOG.values()
            if isinstance(entry.source_enum, str) and entry.source_enum.strip()
        }

        # 检查未匹配的卡片/文本渲染器
        unresolved_presenters = {
            presentation_type
            for presentation_type in source_presentation_types
            if get_presenter(presentation_type) is None
        }
        missing_text_presenters = source_text_presenter_keys - get_text_presenter_keys()
        missing_source_enum = set(SOURCE_CATALOG.keys()) - source_enum_ids

        if unresolved_presenters:
            logger.warning(
                f"[灾害预警] 以下 presentation_type 无法解析 presenter: {unresolved_presenters}"
            )
        if missing_text_presenters:
            logger.warning(
                f"[灾害预警] 以下 text_presenter_key 缺少 presenter 注册: {missing_text_presenters}"
            )
        if missing_source_enum:
            logger.warning(
                f"[灾害预警] 以下数据源缺少 SOURCE_CATALOG.source_enum 定义: {missing_source_enum}"
            )
        if (
            not unresolved_presenters
            and not missing_text_presenters
            and not missing_source_enum
        ):
            logger.debug("[灾害预警] source catalog / presenter 注册完整性自检通过")

    def set_telemetry(self, telemetry: Optional["TelemetryManager"]):
        """设置遥测管理器引用。"""
        # 遥测能力会同时下发给 WebSocket 管理器与消息推送管理器，
        # 以便基础设施层也能统一上报异常与运行指标。
        self._telemetry = telemetry
        if self.ws_manager:
            self.ws_manager._telemetry = telemetry
        if self.message_manager:
            self.message_manager.set_telemetry(telemetry)

    async def initialize(self):
        """初始化服务。"""
        try:
            logger.info("[灾害预警] 正在初始化灾害预警服务...")
            # 初始化阶段只做“静态装配”：校验注册表、加载基础数据、注册解析器调度、生成连接计划。
            validate_catalog_parser_names()
            self._check_registry_integrity()
            await region_service.load_data_async()  # 加载地理省份数据文件
            # 预加载 JMA 町丁目->地域映射表，避免首条地震情报时延迟加载
            get_sect_map()
            # 预加载走时模型与中国区县采样点，避免首条地震预警时延迟加载
            get_travel_times()
            get_district_points()
            self.http_fetcher = HTTPDataFetcher(self.config)  # 初始化 HTTP 轮询拉取组件
            self._register_handlers()
            self._configure_connections()
            # 就绪日志以真实轮询服务为准（catalog 组总闸 + typhoon_enrichment）
            typhoon_poll = getattr(self, "eqsc_typhoon_poll_service", None)
            poll_enabled = bool(typhoon_poll is not None and typhoon_poll.is_enabled())
            enrichment = self.typhoon_enrichment_service
            if poll_enabled:
                logger.info("[灾害预警] EQSC 台风轮询服务已就绪")
            elif getattr(enrichment, "is_channel_enabled", False):
                logger.info(
                    "[灾害预警] EQSC 通道已启用，但台风轮询子开关关闭；"
                    "实时台风推送将不可用"
                )
            else:
                logger.debug("[灾害预警] EQSC 数据源未启用，相关数据源将不可用")
            logger.info("[灾害预警] 灾害预警服务初始化完成")

            # 注意：EQSC 历史台风重建不得在 initialize() 中同步/阻塞执行。
            # 该阶段会阻塞后续 start()，而 token 网络请求可能长达数十秒。
            # 重建统一放到 start() 完成后由后台任务触发。

        except Exception as e:
            logger.error(f"[灾害预警] 初始化服务失败: {e}")
            if self._telemetry and self._telemetry.enabled:
                await self._telemetry.track_error(
                    e, module="core.disaster_service.initialize"
                )
            raise

    def schedule_eqsc_token_warmup(self) -> None:
        """启动后第一时间后台预热 EQSC AccessToken，并开启保活续期。

        状态面板只读内存 token 有效性；若仅启动预热、无业务请求触发续期，
        AccessToken（约 1 小时）过期后会长期显示“鉴权失效”。
        """
        # 通道级预热：只要组总闸开启且 token 已配置即可，不依赖台风富化子开关
        enrichment = self.typhoon_enrichment_service
        if not getattr(enrichment, "is_channel_enabled", enrichment.is_enabled):
            return

        async def _warmup_and_keepalive() -> None:
            await enrichment.warm_up_access_token()
            # 预热成功与否都启动保活：失败时循环会按重试间隔继续尝试
            start_keepalive = getattr(enrichment, "start_token_keepalive", None)
            if callable(start_keepalive):
                start_keepalive(register_task=self.register_background_task)

        warmup_task = asyncio.create_task(
            _warmup_and_keepalive(),
            name="dw_eqsc_token_warmup",
        )
        self.register_background_task(warmup_task)

    def schedule_typhoon_db_rebuild(self) -> None:
        """在后台调度 EQSC 历史台风数据库重建，避免阻塞启动链路。"""
        if not self.typhoon_enrichment_service.is_enabled:
            return
        # 依赖可能在 initialize 后才完全就绪，调度前再绑定一次。
        self.typhoon_history_rebuild_service.bind(
            enrichment_service=self.typhoon_enrichment_service,
            statistics_manager=self.statistics_manager,
        )
        rebuild_task = asyncio.create_task(
            self.typhoon_history_rebuild_service.try_cold_start_rebuild(),
            name="dw_rebuild_typhoon_db",
        )
        self.register_background_task(rebuild_task)

    def _register_handlers(self):
        """注册消息调度处理器。"""
        # 路由器会按不同数据源的接入类型，把消息分发到统一事件入口或旁路处理逻辑。
        registry = SourceMessageRouter(self)
        registry.register_all(self.ws_manager)
        self.ws_manager.set_offline_notify_callback(self._handle_offline_notification)

    def _configure_connections(self):
        """根据数据源配置生成连接计划。"""
        self.connections = ConnectionPlanBuilder.build(self.config)

    async def start(self):
        """启动服务"""
        await self.lifecycle_service.start()

    async def _cancel_and_wait(self, tasks: list[asyncio.Task]) -> None:
        """取消并等待任务结束。"""
        await self.lifecycle_service.cancel_and_wait(tasks)

    def register_background_task(self, task: asyncio.Task) -> None:
        """注册服务级后台任务，确保停机时可统一回收。"""
        if task is None:
            return
        self.background_tasks.add(task)
        # 任务结束后自动从集合中移除，避免后台任务引用长期堆积。
        task.add_done_callback(self.background_tasks.discard)

    async def stop(self):
        """停止服务"""
        await self.lifecycle_service.stop()

    async def _establish_websocket_connections(self):
        """建立 WebSocket 连接，实际逻辑由运行时服务承接。"""
        await self.runtime_service.establish_websocket_connections()

    async def _start_scheduled_http_fetch(self):
        """启动定时 HTTP 数据获取。"""
        await self.runtime_service.start_scheduled_http_fetch()

    async def _start_cleanup_task(self):
        """启动清理任务。"""
        await self.runtime_service.start_cleanup_task()

    def is_in_silence_period(self) -> bool:
        """检查是否处于启动后的静默期。"""
        if not hasattr(self, "start_time"):
            return False

        debug_config = self.config.get("debug_config", {})
        silence_duration = debug_config.get("startup_silence_duration", 0)

        if silence_duration <= 0:
            return False

        elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
        return elapsed < silence_duration

    async def _handle_disaster_event(self, event: EventEnvelope):
        """处理灾害事件。主链路仅接收解析器产出的统一事件。"""
        try:
            # 地震预警查询状态更新属于轻量级旁路状态维护，即使失败也不阻断主流程。
            self._update_eew_query_state(event)
        except Exception as e:
            logger.debug(f"[灾害预警] 更新 EEW 查询状态失败（已忽略）: {e}")

        # 启动静默期过滤逻辑，防止插件重载后反复造成消息推送
        if self.is_in_silence_period():
            debug_config = self.config.get("debug_config", {})
            silence_duration = debug_config.get("startup_silence_duration", 0)
            elapsed = (datetime.now(timezone.utc) - self.start_time).total_seconds()
            logger.debug(
                f"[灾害预警] 处于启动静默期 (剩余 {silence_duration - elapsed:.1f}s)，忽略事件: {event.id}"
            )
            return

        try:
            # 台风事件：
            # 1) FAN 遗留触发：多台风共舞时会整包重推，富化前先只读去重；
            # 2) EQSC 独立轮询：事件已含完整轨迹，跳过二次富化。
            if event.event_type == "typhoon":
                source_id = str(getattr(event, "source_id", "") or "").strip()
                if source_id != "typhoon_eqsc":
                    deduplicator = getattr(
                        getattr(self, "message_manager", None), "deduplicator", None
                    )
                    peek = getattr(deduplicator, "peek_typhoon_should_push", None)
                    if callable(peek) and not peek(event):
                        logger.debug(
                            f"[灾害预警] 台风事件在富化前被去重过滤，跳过后续推送: {event.id}"
                        )
                        return

                    # 仅对 FAN 触发路径执行 EQSC 富化（遗留兼容）
                    event = await self.typhoon_enrichment_service.enrich(event)

            # 真正的日志记录、推送、统计与 Web 管理端通知由事件流水线统一处理。
            await self.event_pipeline.handle(event)

        except Exception as e:
            logger.error(f"[灾害预警] 处理灾害事件失败: {e}")
            logger.error(
                f"[灾害预警] 失败的事件ID: {event.id if hasattr(event, 'id') else 'unknown'}"
            )
            logger.error(f"[灾害预警] 异常堆栈: {traceback.format_exc()}")
            if self._telemetry and self._telemetry.enabled:
                asyncio.create_task(
                    self._telemetry.track_error(
                        exception=e,
                        module="disaster_service._handle_disaster_event",
                    )
                )

    async def _handle_offline_notification(self, payload: dict[str, Any]) -> None:
        """处理 WebSocket 管理器的离线通知回调。"""
        await self.notice_service.handle_offline_notification(payload)

    async def reconnect_all_sources(self) -> dict[str, str]:
        """
        强制重连所有已启用但离线的数据源。

        返回值为“连接名 -> 处理结果”的对应表。
        """
        return await self.reconnect_service.reconnect_all_sources()

    def get_service_status(self) -> dict[str, Any]:
        """获取服务状态。"""
        return self.status_service.get_service_status()

    def get_uptime(self) -> str:
        """获取服务运行时长。"""
        return self.status_service.get_uptime()

    def _update_eew_query_state(self, event: EventEnvelope) -> None:
        """更新地震预警查询状态（机构级，跨源去重）。"""
        self.eew_query_state = self.eew_query_service.update_state(
            self.eew_query_state,
            event,
        )

    def get_eew_query_status_data(self) -> dict[str, Any]:
        """获取地震预警查询的结构化状态数据，供 Web 管理端与指令复用。"""
        data_sources_cfg = ConfigAccessor(self.config).data_sources_config()
        return self.eew_query_service.build_status_data(
            self.eew_query_state,
            data_sources_cfg,
        )

    def get_eew_query_text(self) -> str:
        """生成 /地震预警查询 命令对应的文本。"""
        return self.notice_service.get_eew_query_text()


_disaster_service: DisasterWarningService | None = None


async def get_disaster_service(
    config: dict[str, Any], context
) -> DisasterWarningService:
    """获取灾害预警服务单例实例。"""
    global _disaster_service

    if _disaster_service is None:
        _disaster_service = DisasterWarningService(config, context)
        await _disaster_service.initialize()

    return _disaster_service


async def stop_disaster_service():
    """停止并注销灾害预警服务。"""
    global _disaster_service

    if _disaster_service:
        await _disaster_service.stop()
        _disaster_service = None
