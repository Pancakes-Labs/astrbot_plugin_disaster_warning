"""
消息推送编排器。
负责根据事件来源、融合分组与策略配置决定进入哪条推送路径，
把主管理器中的推送分流逻辑外提出来，从而让消息管理器更专注于高层装配职责。
"""

from __future__ import annotations

from astrbot.api import logger

from ...domain.event_models import EventEnvelope
from ...services.config.config_service import ConfigAccessor
from ...sources.source_catalog import SOURCE_CATALOG
from ...sources.source_entry import FusionRole


class PushOrchestrator:
    """消息推送编排器。"""

    def __init__(
        self,
        config: dict,
        execute_push,
        cenc_fusion_service,
        cwa_eew_fusion_service,
        silence_checker=None,
        silence_handler=None,
    ):
        # 这里保存的都是编排阶段需要查询的静态配置与推送分支执行器。
        self.config = config
        self._config_accessor = ConfigAccessor(config)
        self._execute_push = execute_push
        self._cenc_fusion_service = cenc_fusion_service
        self._cwa_eew_fusion_service = cwa_eew_fusion_service
        # 启动静默回调（由主服务注入）：
        # - silence_checker: 无参回调，返回是否处于启动静默期（纯判定，无副作用）
        # - silence_handler: 吸收回调，接收事件并完成播种/计数等吸收动作
        self._silence_checker = silence_checker
        self._silence_handler = silence_handler

    def set_silence_checker(self, checker) -> None:
        """注入启动静默判定回调（复用主服务 is_silencing）。"""
        self._silence_checker = checker

    def set_silence_handler(self, handler) -> None:
        """注入启动静默吸收回调（播种去重指纹并计数）。"""
        self._silence_handler = handler

    def _resolve_fusion_plan(
        self, source_id: str
    ) -> tuple[object | None, dict, str] | None:
        """从数据源目录与策略配置解析融合计划。"""
        entry = SOURCE_CATALOG.get(source_id)
        # 如果当前数据源没有配置融合分组，就按普通推送路径处理。
        if entry is None or not entry.fusion_group:
            return None

        strategies_cfg = self._config_accessor.strategies_config()
        fusion_group = entry.fusion_group.strip()
        # 分组名称与策略键、执行服务之间的映射统一在这里维护，
        # 方便后续扩展新的融合策略类型。
        strategy_key_map = {
            "cenc_intensity": "cenc_fusion",
            "cwa_scale": "cwa_eew_fusion",
        }
        service_map = {
            "cenc_intensity": self._cenc_fusion_service,
            "cwa_scale": self._cwa_eew_fusion_service,
        }
        strategy_key = strategy_key_map.get(fusion_group)
        fusion_service = service_map.get(fusion_group)
        if not strategy_key or fusion_service is None:
            return None

        strategy_config = strategies_cfg.get(strategy_key, {})
        if not isinstance(strategy_config, dict) or not strategy_config.get(
            "enabled", False
        ):
            return None
        return fusion_service, strategy_config, fusion_group

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
    ) -> bool | dict:
        """根据策略配置编排事件推送流程。

        启动静默期的事件统一在此吸收（播种去重指纹 + 计数），
        不进入融合拦截或普通推送链，避免融合分流路径绕过静默闸口。
        """
        # 第一道静默闸口：覆盖所有进入推送链的事件（含融合分流路径）。
        # 静默期事件不创建融合 pending、不发送，与主入口吸收语义保持一致。
        if self._silence_checker is not None:
            try:
                if self._silence_checker():
                    if self._silence_handler is not None:
                        try:
                            self._silence_handler(event)
                        except Exception as exc:
                            # 吸收回调失败不应中断推送管线：记录日志后按非静默继续推送，
                            # 避免静默协同配置损坏时所有灾害推送都被吞掉。
                            logger.warning(
                                f"[灾害预警] 静默吸收回调执行失败（已放行推送）: {exc}"
                            )
                            return False
                    return False
            except Exception as exc:
                # 判定异常时按不静默处理，避免静默判定故障导致推送完全中断。
                logger.warning(f"[灾害预警] 静默判定异常（已按非静默放行推送）: {exc}")

        source_id = event.source_id
        entry = SOURCE_CATALOG.get(source_id)
        fusion_plan = None if bypass_fusion else self._resolve_fusion_plan(source_id)
        if fusion_plan is not None and entry is not None:
            fusion_service, strategy_config, fusion_group = fusion_plan
            # 不同融合策略对等待另一侧消息的默认超时时间不同。
            timeout_default_map = {
                "cenc_intensity": 10,
                "cwa_scale": 6,
            }
            if entry.fusion_role == FusionRole.SECONDARY:
                # 次级来源事件先进入融合拦截流程，
                # 只有满足融合条件时才真正触发下游推送。
                return await fusion_service.intercept_fan_event(
                    event,
                    strategy_config.get(
                        "timeout", timeout_default_map.get(fusion_group, 6)
                    ),
                    target_sessions=target_sessions,
                    session_config_getter=session_config_getter,
                )
            if entry.fusion_role == FusionRole.PRIMARY:
                # 主来源事件只负责更新融合状态，不立即独立推送。
                fusion_service.handle_wolfx_event(event)
                return False

        # 不参与融合的事件，或融合未启用时，直接走普通推送执行链。
        return await self._execute_push(
            event,
            target_sessions=target_sessions,
            session_config_getter=session_config_getter,
            commit_state=commit_state,
            skip_dedup=skip_dedup,
            return_details=return_details,
            aggregated_session_count=aggregated_session_count,
        )
