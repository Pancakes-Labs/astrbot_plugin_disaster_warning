"""
事件接入分发服务。
负责根据 source 特征与策略配置决定事件应同步处理还是转后台任务分发，
从 SourceMessageRouter 中迁出运行时分发策略判断。
"""

from __future__ import annotations

import asyncio

from astrbot.api import logger

from ..sources.source_catalog import get_source_entry


class EventIngressDispatchService:
    """
    事件接入分发服务。

    核心职责：
    1. 评估指定数据源事件是否应当在后台异步协程中分发，以避免阻塞底层的 WebSocket/HTTP 接收套接字。
    2. 执行事件分发，对于需要异步的事件创建后台 Task 托管；对于普通事件则在当前协程直接同步执行。
    """

    def __init__(self, service):
        """
        保存灾害服务引用，供分发阶段访问配置与主处理链。

        Args:
            service: 灾害预警核心服务 (DisasterWarningService) 实例。
        """
        self.service = service

    def should_dispatch_in_background(self, source_id: str) -> bool:
        """
        判断指定数据源是否应转为后台异步分发。

        通过解析数据源目录(Source Catalog)中的分发特征与策略配置，来决定是否应该
        通过后台任务异步调用主链路逻辑，防止如高频且计算复杂的融合逻辑等阻塞接入线程。

        Args:
            source_id: 数据源的唯一标识符。

        Returns:
            bool: 如果需要异步分发返回 True，否则返回 False。
        """
        # 从数据源静态名录中检索对应的源定义
        entry = get_source_entry(source_id)
        if entry is None:
            return False

        dispatch_family = (entry.dispatch_family or "").strip()

        # FAN 台风路径：富化查询可能同步阻塞较久（EQSC 重试链最长约 5 分钟），
        # 必须转为后台异步分发，避免阻塞 FAN Studio WS 接收线程。
        # 注意：EQSC 独立轮询路径不经过此分发，直接由轮询协程驱动。
        if dispatch_family == "fan_studio_typhoon":
            return True

        # 针对具有特定的推送协议族(例如 fan_studio_eew) 的源进行异步判断
        if dispatch_family != "fan_studio_eew":
            return False

        # 必须是需要参与融合处理的组(fusion_group)才支持异步分发
        if not entry.fusion_group:
            return False

        # 只有在主配置中启用了对应融合策略的快报源，才值得改为后台执行，避免阻塞接入线程
        message_manager = getattr(self.service, "message_manager", None)
        config = getattr(message_manager, "config", {}) if message_manager else {}
        strategies_cfg = (
            config.get("strategies", {}) if isinstance(config, dict) else {}
        )

        # 融合组与配置键的对应映射关系
        strategy_key_map = {
            "cenc_intensity": "cenc_fusion",
            "cwa_scale": "cwa_eew_fusion",
        }
        strategy_key = strategy_key_map.get(entry.fusion_group)
        if not strategy_key:
            return False

        # 获取具体的融合策略配置字典
        strategy_config = strategies_cfg.get(strategy_key, {})
        return bool(
            isinstance(strategy_config, dict) and strategy_config.get("enabled", False)
        )

    async def dispatch_event(self, event, *, source_id: str, source_label: str) -> None:
        """
        按策略把事件直接送入主链或转为后台任务。

        根据判断结果，派发事件到主处理链路中。

        Args:
            event: 已解析的 EventEnvelope 统一事件包裹实例。
            source_id: 来源数据源唯一标识符。
            source_label: 来源数据源可读标签。
        """
        if self.should_dispatch_in_background(source_id):

            async def _dispatch_event_non_blocking(disaster_event):
                # 后台任务只负责调用主事件处理链，并把异常留在日志中，不回抛到接入层。
                try:
                    await self.service._handle_disaster_event(disaster_event)
                except Exception as dispatch_err:
                    logger.error(
                        f"[灾害预警] {source_label} 异步分发失败: {dispatch_err}"
                    )

            # 创建非阻塞异步协程任务，避免阻塞主接收套接字
            task = asyncio.create_task(
                _dispatch_event_non_blocking(event),
                name=f"dw_{source_id}_dispatch_{event.id}",
            )
            # 在主服务中注册后台生命周期托管，防止任务被 Python GC 提前回收
            if hasattr(self.service, "register_background_task"):
                self.service.register_background_task(task)
            return

        # 普通源直接在当前协程同步执行主事件处理链
        await self.service._handle_disaster_event(event)


__all__ = ["EventIngressDispatchService"]
