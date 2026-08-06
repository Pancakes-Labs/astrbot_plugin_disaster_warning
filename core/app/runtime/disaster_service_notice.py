"""
灾害服务通知与文本展示服务。
负责离线通知编排与 EEW 查询文本生成，
进一步减少 DisasterWarningService 中的展示与通知职责。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...services.config.config_service import ConfigAccessor
from ...services.config.config_validation_service import ConfigValidator


class DisasterServiceNoticeService:
    """灾害服务通知与文本展示服务。"""

    # 离线通知只关心对用户可见的阶段语义；底层阶段代号在这里统一翻译为中文描述。
    _OFFLINE_STAGE_MAP = {
        "short_retry": "离线时间过长",
        "fallback": "进入兜底重试",
        "stop": "停止重连",
    }

    # 连接内部数据源代号 -> 用户可读的展示名称。
    # 与 SourceRuntimeQueryService._CONNECTION_DISPLAY_NAME 的展示口径保持一致，
    # 避免把 openquake_mixed 这类内部代号直接暴露给用户。
    _SOURCE_DISPLAY_MAP: dict[str, str] = {
        "fan_studio_mixed": "FAN Studio",
        "wolfx_mixed": "Wolfx",
        "openquake_mixed": "OpenQuakeAPI",
        "jma_p2p": "P2P地震情報",
        "jma_p2p_info": "P2P地震情報",
        "jma_tsunami_p2p": "P2P地震情報",
        "jma_tsunami_eqsc": "EQSC API",
        "cenc_ir_eqsc": "EQSC API",
        "typhoon_eqsc": "EQSC API",
        "snet_msil": "NIED S-Net",
    }

    def __init__(self, service):
        # 与生命周期服务类似，这里只保留主服务引用，便于共享配置、状态与消息发送能力。
        self.service = service  # 主服务 DisasterWarningService 实例

    async def handle_offline_notification(self, payload: dict[str, Any]) -> None:
        """处理 WebSocket 管理器离线通知回调。"""
        # 这里负责把底层连接层抛出的松散字典整理成显式参数，
        # 这样后续通知逻辑可以保持稳定签名，减少对上游字段结构的耦合。
        await self.notify_data_source_offline(
            connection_name=payload.get("connection_name", "unknown"),
            data_source=payload.get("data_source", "unknown"),
            stage=payload.get("stage", "unknown"),
            reason=payload.get("reason", "未知原因"),
            next_retry_in=payload.get("next_retry_in"),
            retry_count=payload.get("retry_count"),
            fallback_count=payload.get("fallback_count"),
        )

    async def notify_data_source_offline(
        self,
        connection_name: str,
        data_source: str,
        stage: str,
        reason: str,
        next_retry_in: str | None = None,
        retry_count: int | None = None,
        fallback_count: int | None = None,
    ) -> bool:
        """推送数据源离线系统警告通知（支持防刷频节流、重连阶段兜底）。"""
        # 若消息管理器不存在，说明当前运行环境不具备发送系统消息的条件，直接返回即可。
        if not self.service.message_manager:
            return False

        key = f"{connection_name}:{stage}"  # 拼装唯一的连接阶段节流键
        now = asyncio.get_running_loop().time()  # 获取当前系统相对时间
        state = self.service._offline_notification_state.get(key, {})
        last_ts = state.get("last_ts", 0.0)
        # “离线时间过长(short_retry)”不进行节流，“兜底重试”与“停止重连”防刷屏限制半小时
        ttl_seconds = 1800 if stage != "short_retry" else 0
        # 节流粒度为“同一连接 + 同一阶段”。
        # 相同连接在同一 stage 下 30 分钟内最多通知一次，避免离线抖动造成刷屏。
        if now - last_ts < ttl_seconds:
            return False

        # 生成具体推送文本
        message = self.build_offline_notification_message(
            connection_name=connection_name,
            data_source=data_source,
            stage=stage,
            reason=reason,
            next_retry_in=next_retry_in,
            retry_count=retry_count,
            fallback_count=fallback_count,
        )
        offline_sessions = (
            self.resolve_offline_notification_sessions()
        )  # 计算推送会话列表
        # 调用系统通知中心推送提醒
        success = await self.service.message_manager.system_notification_service.push_system_message(
            message,
            target_sessions=offline_sessions,
        )
        # 只在实际发送成功后刷新节流时间，避免因为发送失败而长时间吞掉后续提醒。
        if success:
            self.service._offline_notification_state[key] = {"last_ts": now}
        return bool(success)

    def resolve_offline_notification_sessions(self) -> list[str]:
        """解析离线警告通知的目标群组/私聊会话列表。"""
        # 优先使用独立的离线通知会话，适用于希望把运维告警与普通灾情推送拆分的场景；
        # 若未配置，再回退到通用目标会话，保证通知能力默认可用。
        offline_sessions = ConfigValidator._validate_target_sessions(
            self.service.config.get("offline_notification_sessions", []),
            key_name="offline_notification_sessions",
        )
        if offline_sessions:
            return offline_sessions  # 使用专用运维通知会话
        # 回退至默认的推送目标会话
        return ConfigValidator._validate_target_sessions(
            self.service.config.get("target_sessions", []),
            key_name="target_sessions",
        )

    def build_offline_notification_message(
        self,
        *,
        connection_name: str,
        data_source: str,
        stage: str,
        reason: str,
        next_retry_in: str | None,
        retry_count: int | None,
        fallback_count: int | None,
    ) -> str:
        """构建具体展示的离线通知富文本消息。"""
        # 阶段文案、重试次数和下一次重试时间会一起展示，
        # 目的是让使用者能在一条通知里快速判断当前处于“短时抖动”还是“长期离线”。
        # 数据源代号先映射为展示名，避免把内部标识暴露给用户。
        source_display = self._SOURCE_DISPLAY_MAP.get(data_source, data_source)
        stage_text = self._OFFLINE_STAGE_MAP.get(stage, stage)
        retry_part = (
            f"短时重试: {retry_count}" if retry_count is not None else "短时重试: 未知"
        )
        fallback_part = (
            f"兜底重试: {fallback_count}"
            if fallback_count is not None
            else "兜底重试: 未知"
        )
        next_retry_part = (
            f"下一次重试: {next_retry_in}" if next_retry_in else "下一次重试: 未知"
        )

        message_lines = [
            f"⚠️ {source_display} 离线通知",
            "",
            f"⛔ 状态: {stage_text}",
            f"📝 原因: {reason}",
            f"🔁 {retry_part}",
            f"🛟 {fallback_part}",
        ]
        # “离线时间过长”和“进入兜底重试”都适合展示下一次重试时间，帮助运维判断恢复窗口。
        if stage in {"short_retry", "fallback"}:
            message_lines.append(f"⏳ {next_retry_part}")
        return "\n".join(message_lines)

    def get_eew_query_text(self) -> str:
        """生成 /地震预警查询 主命令返回的结构化统计展示文本。"""
        # 文本生成并不直接读取原始事件，而是消费查询状态服务产出的结构化结果，
        # 这样命令输出与管理端展示可以复用同一份状态基础。
        data_sources_cfg = ConfigAccessor(self.service.config).data_sources_config()
        status_data = self.service.eew_query_service.build_status_data(
            self.service.eew_query_state,
            data_sources_cfg,
        )
        institutions = status_data.get("institutions", [])

        # 先按“正在生效 / 已启用但暂无数据 / 未启用”分组，
        # 最后再统一拼接，以获得更符合阅读顺序的文本结构。
        active_lines: list[str] = []
        inactive_items: list[tuple[int, str]] = []
        no_data_lines: list[str] = []
        unavailable_lines: list[str] = []

        for item in institutions:
            display_name = item.get("display_name", "未知机构")
            active_name = item.get("active_name", display_name)
            status = item.get("status")

            if status == "unavailable":
                # 该机构对应的数据源开关未启用，因此不能把“无预警时长”误导性地展示为正常统计结果。
                unavailable_lines.append(
                    f"- {display_name}：未启用对应数据源开关，无法计算无 EEW 时间"
                )
                continue

            if status == "no_data":
                # 已启用但没有足够历史状态时，需要与“正常无预警”区分开，避免误解为系统已统计过。
                no_data_lines.append(
                    f"- {display_name}：已启用数据源，但暂无可计算历史数据"
                )
                continue

            if status == "active":
                # 正在生效的预警优先展示在最前面，便于用户一眼看到当前最关键的信息。
                magnitude = item.get("magnitude")
                place = item.get("place") or "未知地点"
                mag_text = self._format_magnitude(magnitude)
                active_lines.append(
                    f"[{active_name}] 当前正在发布地震预警：M {mag_text} {place}"
                )
                continue

            # 剩余情况视为“当前无生效预警”，并按无预警时长升序排列，
            # 让最近刚结束预警的机构优先显示在前面。
            elapsed = int(item.get("elapsed_seconds") or 0)
            inactive_items.append(
                (
                    elapsed,
                    f"{self.service.eew_query_service.format_elapsed_seconds(elapsed)} 无 {display_name}",
                )
            )

        inactive_lines = [
            line for _, line in sorted(inactive_items, key=lambda item: item[0])
        ]

        lines: list[str] = []
        if active_lines:
            lines.extend(active_lines)
            if inactive_lines:
                lines.append("")
                lines.extend(inactive_lines)
        else:
            lines.append("当前没有正在生效的地震预警")
            if inactive_lines:
                lines.append("")
                lines.extend(inactive_lines)

        # “暂无历史数据”和“未启用”属于说明性补充信息，统一放在正文末尾，避免打断主信息阅读。
        if no_data_lines:
            lines.append("")
            lines.append("以下机构暂无可计算的历史 EEW 数据：")
            lines.extend(no_data_lines)

        if unavailable_lines:
            lines.append("")
            lines.append("以下机构因数据源开关未启用，无法参与计算：")
            lines.extend(unavailable_lines)

        if not lines:
            lines.append("当前没有正在生效的地震预警")

        return "\n".join(lines)

    @staticmethod
    def _format_magnitude(magnitude: Any) -> str:
        """格式化震级显示文本为保留一位小数。"""
        # 震级来源可能是数字、字符串甚至空值；这里统一转换为适合展示的一位小数字符串。
        if magnitude is None:
            return "?"
        try:
            return f"{float(magnitude):.1f}"
        except Exception:
            return str(magnitude)
