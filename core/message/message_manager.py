"""
消息推送管理器。

该模块是消息子系统的高层装配入口，
负责串联运行时基础设施、消息构建能力、推送执行链、融合策略与系统通知能力，
对上层仅暴露少量统一接口。
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.star import StarTools

from ..domain.event_models import EventEnvelope
from .fusion.cenc_fusion_service import CENCFusionService
from .fusion.cwa_eew_fusion_service import CWAEewFusionService
from .push import (
    MessageBuildService,
    PushExecutionService,
    PushFlowHandler,
    PushOrchestrator,
    SessionSender,
    evaluate_push_decision_with_components,
)
from .render.render_cache import RenderImageCache
from .runtime.bootstrap_service import MessageManagerBootstrapService
from .runtime.fusion_state_store import FusionStateStore
from .runtime.remote_media_service import MessageRemoteMediaService
from .runtime.resource_cleanup_service import MessageResourceCleanupService
from .runtime.runtime_component_factory import MessageRuntimeComponentFactory
from .system.system_notification_service import MessageSystemNotificationService


class MessagePushManager:
    """消息推送管理器。"""

    def __init__(self, config: dict[str, Any], context, telemetry=None):
        # 管理器只保存消息子系统共享的根依赖和运行期公共状态。
        self.config = config
        self.context = context
        self._telemetry = telemetry
        # 会话发送器是最底层的消息下发执行入口。
        self.session_sender = SessionSender(context)
        self.plugin_root = str(Path(__file__).resolve().parents[2])
        self.storage_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
        self.temp_dir = self.storage_dir / "temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        self.data_dir = self.plugin_root
        # 记录最近一次成功发送的会话，便于后续状态查看或调试。
        self.last_success_sessions: list[str] = []
        # 远程媒体抓取会话由消息子系统统一复用，减少重复建连开销。
        self._remote_media_session = None
        self._remote_media_session_timeout_seconds = 30
        # 会话配置管理器引用，由上层 DisasterService 注入，
        # 供推送执行链与构建服务获取会话备注名等展示信息。
        self.session_config_manager = None

        # 装配顺序遵循“运行时基础设施 -> 消息构建能力 -> 推送执行链”。
        self._setup_runtime_infrastructure(config, telemetry)
        self._setup_message_infrastructure()
        self._setup_push_pipeline()

    def set_session_config_manager(self, mgr) -> None:
        """注入会话配置管理器引用，供下游服务查询备注名等展示信息。"""
        self.session_config_manager = mgr

    def _get_session_log_str(self, session: str) -> str:
        """获取统一格式的会话日志字符串（私聊/群聊 ID (备注名)）。

        当会话配置管理器不可用时回退到原始 UMO。
        """
        mgr = self.session_config_manager
        if mgr:
            return mgr.get_session_log_str(session)
        return session

    def _setup_runtime_infrastructure(
        self,
        config: dict[str, Any],
        telemetry=None,
    ) -> None:
        """装配运行时基础设施。"""
        self._runtime_component_factory = MessageRuntimeComponentFactory()
        self._bootstrap_service = MessageManagerBootstrapService(self)
        self._bootstrap_service.setup_filters(config)  # 载入推送规则过滤器列表
        self._bootstrap_service.setup_browser(
            config, telemetry=telemetry
        )  # 初始化网页截图用浏览器底座
        self._fusion_state_store = FusionStateStore(ttl_seconds=120)  # 事件合并暂存区
        self._render_cache = RenderImageCache(ttl_seconds=180)  # 卡片图片渲染缓存
        self._resource_cleanup_service = MessageResourceCleanupService(
            self
        )  # 优雅清理服务
        self._remote_media_service = MessageRemoteMediaService(self)  # 外部媒体抓取助手
        self.cleanup_old_records()  # 执行一次启动清理

    def _setup_message_infrastructure(self) -> None:
        """装配消息构建基础设施。"""
        self._bootstrap_service.setup_message_components()  # 装载文字和卡片构建组件
        self._message_build_service = MessageBuildService(self)
        self._system_notification_service = MessageSystemNotificationService(self)

    def _setup_push_pipeline(self) -> None:
        """装配推送执行链与合并融合策略。"""
        self._push_execution_service = PushExecutionService(self)
        self._push_flow_handler = PushFlowHandler(self)
        self._cenc_fusion_service = CENCFusionService(
            self,
            execute_push=self._push_flow_handler.execute_push,
        )
        self._cwa_eew_fusion_service = CWAEewFusionService(
            self,
            execute_push=self._push_flow_handler.execute_push,
        )
        self._push_orchestrator = PushOrchestrator(
            config=self.config,
            execute_push=self._push_flow_handler.execute_push,
            cenc_fusion_service=self._cenc_fusion_service,
            cwa_eew_fusion_service=self._cwa_eew_fusion_service,
        )

    def set_silence_callbacks(self, checker, handler) -> None:
        """注入启动静默回调到推送编排器与各融合服务。

        Args:
            checker: 无参回调，返回是否处于启动静默期（复用主服务 is_silencing）。
            handler: 吸收回调，接收事件并完成播种/计数等吸收动作。
        """
        if self._push_orchestrator is not None:
            self._push_orchestrator.set_silence_checker(checker)
            self._push_orchestrator.set_silence_handler(handler)
        for fusion_service in (
            self._cenc_fusion_service,
            self._cwa_eew_fusion_service,
        ):
            if fusion_service is None:
                continue
            set_checker = getattr(fusion_service, "set_silence_checker", None)
            if callable(set_checker):
                set_checker(checker)
            set_handler = getattr(fusion_service, "set_silence_absorb_handler", None)
            if callable(set_handler):
                set_handler(handler)

    def set_telemetry(self, telemetry) -> None:
        """同步更新消息子系统遥测引用。"""
        # 浏览器管理器属于消息层中最容易出现外部依赖异常的组件之一，
        # 因此需要同步注入最新遥测引用。
        self._telemetry = telemetry
        if self.browser_manager:
            self.browser_manager._telemetry = telemetry

    async def get_remote_media_session(self, timeout_seconds: int | None = None):
        """获取可复用的远程媒体抓取 Session。"""
        return await self._remote_media_service.get_session(timeout_seconds)

    async def close_remote_media_session(self) -> None:
        """关闭远程媒体抓取 Session。"""
        await self._remote_media_service.close_session()

    async def fetch_remote_media(
        self,
        url: str,
        *,
        expected_kind: str,
        timeout_seconds: int = 30,
        max_bytes: int = 15 * 1024 * 1024,
    ) -> dict[str, Any] | None:
        """抓取远程媒体文件并返回规范格式。"""
        return await self.remote_media_fetcher.fetch(
            url,
            expected_kind=expected_kind,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )

    @property
    def remote_media_fetcher(self):
        """远程媒体抓取器。"""
        return self._bootstrap_service.remote_media_fetcher

    @property
    def text_message_builder(self):
        """文本消息构建器。"""
        return self._bootstrap_service.text_message_builder

    @property
    def card_message_builder(self):
        """卡片消息构建器。"""
        return self._bootstrap_service.card_message_builder

    @property
    def map_attachment_builder(self):
        """地图附件构建器。"""
        return self._bootstrap_service.map_attachment_builder

    @property
    def global_quake_card_builder(self):
        """Global Quake 卡片构建器。"""
        return self._bootstrap_service.global_quake_card_builder

    @property
    def snet_map_renderer(self):
        """S-Net 测站分布图渲染器。"""
        return self._bootstrap_service.snet_map_renderer

    @property
    def typhoon_map_renderer(self):
        """台风路径图渲染器。"""
        return self._bootstrap_service.typhoon_map_renderer

    @property
    def browser_manager(self):
        """浏览器管理器。"""
        return self.__dict__.get("_browser_manager")

    @browser_manager.setter
    def browser_manager(self, value) -> None:
        """设置浏览器管理器。"""
        self.__dict__["_browser_manager"] = value

    @property
    def system_notification_service(self):
        """系统通知推送服务。"""
        return self._system_notification_service

    @property
    def message_build_service(self):
        """消息构建服务。"""
        return self._message_build_service

    @property
    def push_execution_service(self):
        """推送执行服务。"""
        return self._push_execution_service

    def _build_policy_state(
        self,
        runtime_config: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """基于运行时配置构建推送规则决策所需状态上下文。"""
        # 规则状态对象按会话维度按需构建，
        # 这样既能复用公共规则组件，又能保留会话级差异化配置。
        policy_state = self._runtime_component_factory.build(
            runtime_config,
            session_id=session_id,
        )
        # 注入全局 data_sources，供 SourceEnabledRule 做“全局总闸 AND 会话开关”
        global_config = self.config if isinstance(self.config, dict) else {}
        global_data_sources = global_config.get("data_sources", {})
        policy_state["global_data_sources"] = (
            global_data_sources if isinstance(global_data_sources, dict) else {}
        )
        return policy_state

    async def _render_with_cache(
        self,
        cache_key: str,
        renderer: Callable[[], Awaitable[str | None]],
    ) -> str | None:
        """带去重与缓存的图片/卡片渲染包装器。"""
        return await self._render_cache.render(cache_key, renderer)

    def evaluate_push_decision(
        self,
        event: EventEnvelope,
        runtime_config: dict[str, Any] | None = None,
        session_id: str | None = None,
        emit_filter_log: bool = True,
        commit_state: bool = True,
    ):
        """评估事件推送决策，返回规则判断结果。"""
        # 该方法只负责准备运行时状态和参数，
        # 真正的规则求值逻辑仍委托给独立规则组件完成。
        runtime_config = runtime_config or self.config
        policy_state = self._build_policy_state(runtime_config, session_id)
        return evaluate_push_decision_with_components(
            event,
            runtime_config=runtime_config,
            policy_state=policy_state,
            session_id=session_id,
            emit_filter_log=emit_filter_log,
            commit_state=commit_state,
            logger_instance=logger,
        )

    async def push_event(
        self,
        event: EventEnvelope,
        target_sessions: list[str] | None = None,
        session_config_getter=None,
        *,
        commit_state: bool = True,
        skip_dedup: bool = False,
        bypass_fusion: bool = False,
        return_details: bool = False,
        aggregated_session_count: int = 0,
    ) -> bool | dict[str, Any]:
        """推送事件入口，由推送编排器统一调度。"""
        return await self._push_orchestrator.push_event(
            event,
            target_sessions=target_sessions,
            session_config_getter=session_config_getter,
            commit_state=commit_state,
            skip_dedup=skip_dedup,
            bypass_fusion=bypass_fusion,
            return_details=return_details,
            aggregated_session_count=aggregated_session_count,
        )

    async def render_earthquake_list_card(
        self, events: list[dict], source_name: str
    ) -> str | None:
        """渲染地震列表卡片"""
        return await self.card_message_builder.render_earthquake_list_card(
            events, source_name
        )

    async def cleanup_browser(self):
        """清理浏览器与远程媒体抓取资源。"""
        await self._resource_cleanup_service.cleanup_browser()

    def cleanup_old_records(self):
        """清理历史临时截图与无用记录文件。"""
        self._resource_cleanup_service.cleanup_old_records()
