"""
消息推送执行服务。
负责会话级筛选、消息构建缓存、并发发送与推送结果汇总，
进一步减少 MessagePushManager 中的过程式编排代码。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain

from ....utils.emoji_filter import EMOJI_FILTER_MODE_DEFAULT
from ...domain.event_models import EventEnvelope


class PushExecutionService:
    """消息推送执行服务。"""

    def __init__(self, manager):
        # 执行服务通过主消息管理器获取会话发送、消息构建与规则评估能力。
        self.manager = manager  # 主消息管理器 MessagePushManager 实例

    @staticmethod
    def _collect_exception_texts(exc: BaseException) -> list[str]:
        """收集异常中可用于判定失败语义的文本片段。"""
        texts: list[str] = [str(exc)]
        for attr_name in ("message", "wording", "msg", "errMsg", "errmsg"):
            value = getattr(exc, attr_name, None)
            if isinstance(value, str) and value.strip():
                texts.append(value)
        return texts

    @classmethod
    def _is_rich_media_transfer_failure(cls, exc: BaseException) -> bool:
        """判断是否为明确的富媒体传输失败（可安全降级重发）。"""
        markers = (
            "rich media transfer failed",
            "rich media",
            "transfer failed",
            "media transfer",
        )
        for text in cls._collect_exception_texts(exc):
            lowered = text.lower()
            if any(marker in lowered for marker in markers):
                return True
        return False

    @classmethod
    def _is_ambiguous_timeout_failure(cls, exc: BaseException) -> bool:
        """判断是否为“可能已送达”的超时类失败。

        仅在失败语义明确像超时/事件检查超时时跳过降级重发，
        避免把 retcode=1200 的富媒体传输失败误判为超时，导致缺失图标等场景无法降级。
        """
        # 明确的富媒体失败应优先走降级，而不是按超时吞掉。
        if cls._is_rich_media_transfer_failure(exc):
            return False

        error_name = type(exc).__name__.lower()
        if "timeout" in error_name:
            return True

        for text in cls._collect_exception_texts(exc):
            lowered = text.lower()
            # 仅在文案明确包含超时语义时跳过降级；
            # EventChecker Failed 本身不等于超时（也可能是 rich media 失败）。
            if "timeout" in lowered or "timed out" in lowered or "time out" in lowered:
                return True

        # 不再仅凭 retcode=1200/1400 判定超时：
        # 平台适配器会把 rich media transfer failed 也包装成 retcode=1200。
        return False

    @staticmethod
    def _build_plaintext_fallback_message(message: MessageChain) -> MessageChain | None:
        """构建发送失败后的降级消息，保留文本与安全的本地图片组件。"""
        if not isinstance(message, MessageChain):
            return None

        fallback_components: list[Any] = []
        text_parts: list[str] = []
        for component in getattr(message, "chain", []) or []:
            text = getattr(component, "text", None)
            if isinstance(text, str) and text.strip():
                text_parts.append(text)
                continue

            component_type = type(component).__name__.lower()
            if "image" not in component_type:
                continue

            file_attr = getattr(component, "file", None)
            path_attr = getattr(component, "path", None)
            url_attr = getattr(component, "url", None)
            data_attr = getattr(component, "data", None)
            base64_attr = getattr(component, "base64", None)

            # 只保留非 HTTP 外部网络地址的安全本地物理路径图片及 Base64 字符图片做降级发送，过滤危险的不在线网络大图
            if (
                isinstance(file_attr, str)
                and file_attr.strip()
                and not str(file_attr).startswith(("http://", "https://"))
            ):
                fallback_components.append(component)
                continue
            if (
                isinstance(path_attr, str)
                and path_attr.strip()
                and not str(path_attr).startswith(("http://", "https://"))
            ):
                fallback_components.append(component)
                continue
            if data_attr:
                fallback_components.append(component)
                continue
            if isinstance(base64_attr, str) and base64_attr.strip():
                fallback_components.append(component)
                continue
            if isinstance(url_attr, str) and url_attr.strip().startswith(
                ("http://", "https://")
            ):
                continue

        merged_text = "\n".join(
            part.rstrip() for part in text_parts if part.strip()
        ).strip()
        if merged_text:
            fallback_components.insert(0, Plain(merged_text))

        if not fallback_components:
            return None
        return MessageChain(fallback_components)

    async def execute(
        self,
        event: EventEnvelope,
        *,
        target_sessions: list[str] | None = None,
        session_config_getter=None,
        commit_state: bool = True,
    ) -> dict[str, Any]:
        """执行会话级消息过滤评估、动态并发渲染与最终投递派发。"""
        # 每次执行前重置成功会话列表，避免旧批次结果污染当前事件。
        self.manager.last_success_sessions = []

        sessions = (
            target_sessions
            if target_sessions is not None
            else self.manager.config.get("target_sessions", [])
        )
        if not sessions:
            logger.warning("[灾害预警] 没有配置目标会话，无法推送消息")
            return {
                "success": False,
                "push_success_count": 0,
                "passed_sessions": [],
                "session_message_format_config": {},
                "filter_reason_stats": {},
                "source_id": "",
            }

        source_id = (getattr(event, "source_id", "") or "").strip()
        push_success_count = 0
        passed_sessions: list[str] = []
        # 记录每个会话最终使用的 message_format，供后续地图拆分发送时按配置分组复用。
        session_message_format_config: dict[str, dict[str, Any]] = {}
        # 统计预筛阶段的拦截原因，便于输出汇总日志。
        filter_reason_stats: dict[str, int] = {}
        # 保留更细粒度的拦截原因明细，避免不同数据源/分组被压扁成同一句日志。
        filter_reason_detail_stats: dict[str, int] = {}
        # 统计实际发送阶段的失败原因，避免与规则拦截混淆。
        send_failure_stats: dict[str, int] = {}

        # 收集预筛选通过的所有会话名单与配置
        push_candidates = self._collect_push_candidates(
            event,
            sessions,
            session_config_getter=session_config_getter,
            filter_reason_stats=filter_reason_stats,
            filter_reason_detail_stats=filter_reason_detail_stats,
        )
        # 会话级展示时区映射：供分离地图渲染时对齐各会话文本时间。
        session_display_timezone_map: dict[str, str] = {
            session: str((runtime_config or {}).get("display_timezone") or "UTC+8")
            for session, runtime_config in push_candidates
        }

        # 同一事件在不同会话下若渲染参数一致，则共享同一个消息构建任务，
        # 避免并发下重复渲染文本/地图/卡片。
        message_task_cache: dict[str, asyncio.Task[MessageChain]] = {}
        message_task_lock = asyncio.Lock()

        async def get_or_build_message(runtime_config: dict[str, Any]) -> MessageChain:
            # 构建缓存键时纳入所有会影响展示结果的关键配置，避免不同配置误复用。
            message_format_config = runtime_config.get("message_format", {})
            weather_config = runtime_config.get("weather_config", {})
            typhoon_config = runtime_config.get("typhoon_config", {})
            if not isinstance(typhoon_config, dict):
                typhoon_config = {}
            # 本地监控开关与地点直接影响地震正文的本地预估展示，必须纳入缓存键，
            # 否则本地监控开启的会话渲染出的含本地预估消息会被未开启的会话误复用。
            local_monitoring_cfg = runtime_config.get("local_monitoring", {})
            if not isinstance(local_monitoring_cfg, dict):
                local_monitoring_cfg = {}
            data_sources = runtime_config.get("data_sources", {})
            if not isinstance(data_sources, dict):
                data_sources = {}
            eqsc_cfg = data_sources.get("eqsc", {})
            if not isinstance(eqsc_cfg, dict):
                eqsc_cfg = {}
            # 会话级 typhoon 开关影响台风正文是否展示 EQSC 富化字段。
            if "typhoon" in eqsc_cfg:
                typhoon_enrichment = bool(eqsc_cfg.get("typhoon"))
            else:
                typhoon_enrichment = bool(eqsc_cfg.get("enabled", True))
            cache_key = json.dumps(
                {
                    "event_id": event.id,
                    "source": event.source_id,
                    "event_type": event.event_type,
                    "display_timezone": runtime_config.get("display_timezone", "UTC+8"),
                    "message_format": {
                        "include_map": message_format_config.get("include_map", False),
                        "map_source": message_format_config.get(
                            "map_source", "PetalMap矢量图亮"
                        ),
                        "typhoon_map_source": message_format_config.get(
                            "typhoon_map_source", "PetalMap矢量图暗"
                        ),
                        "map_zoom_level": message_format_config.get(
                            "map_zoom_level", 5
                        ),
                        "playwright_mode": message_format_config.get(
                            "playwright_mode", "local"
                        ),
                        # 是否忽略 HTTPS 证书错误直接影响地图底图能否加载，
                        # 纳入缓存键避免切换开关后误复用旧渲染结果。
                        "browser_ignore_https_errors": bool(
                            message_format_config.get(
                                "browser_ignore_https_errors", False
                            )
                        ),
                        "use_global_quake_card": message_format_config.get(
                            "use_global_quake_card", False
                        ),
                        "global_quake_template": message_format_config.get(
                            "global_quake_template", "Aurora"
                        ),
                        "detailed_jma_intensity": message_format_config.get(
                            "detailed_jma_intensity", False
                        ),
                        "jma_region_intensity": message_format_config.get(
                            "jma_region_intensity", True
                        ),
                        "emoji_filter_mode": message_format_config.get(
                            "emoji_filter_mode", EMOJI_FILTER_MODE_DEFAULT
                        ),
                    },
                    "weather": {
                        "enable_weather_icon": weather_config.get(
                            "enable_weather_icon", True
                        ),
                        "max_description_length": weather_config.get(
                            "max_description_length", 384
                        ),
                    },
                    "typhoon": {
                        "show_local_estimation": typhoon_config.get(
                            "show_local_estimation", False
                        ),
                        # 台风路径图附件开关影响消息是否附加路径图卡片，
                        # 必须纳入缓存键，否则不同会话会误复用彼此的渲染结果。
                        "include_track_map": bool(
                            typhoon_config.get("include_track_map", True)
                        ),
                        "typhoon": typhoon_enrichment,
                    },
                    # S-Net 测站分布图附件开关同理，纳入缓存键避免跨会话误复用。
                    "snet": {
                        "include_station_map": bool(
                            data_sources.get("snet", {}).get(
                                "include_station_map", True
                            )
                        ),
                    },
                    # 本地监控配置差异会导致地震正文附带不同的本地预估，
                    # 缺失时会使不同会话误共享同一份渲染结果。
                    "local_monitoring": {
                        "enabled": bool(local_monitoring_cfg.get("enabled", False)),
                        "place_name": str(local_monitoring_cfg.get("place_name", "")),
                        "latitude": local_monitoring_cfg.get("latitude", 0.0),
                        "longitude": local_monitoring_cfg.get("longitude", 0.0),
                        "strict_mode": bool(
                            local_monitoring_cfg.get("strict_mode", False)
                        ),
                        "intensity_threshold": local_monitoring_cfg.get(
                            "intensity_threshold", 2.0
                        ),
                    },
                },
                sort_keys=True,
                ensure_ascii=False,
            )
            task = message_task_cache.get(cache_key)
            if task is None:
                async with message_task_lock:
                    task = message_task_cache.get(cache_key)
                    if task is None:
                        # 触发异步消息渲染任务 (包含文本和地图卡片渲染)
                        task = asyncio.create_task(
                            self.manager.message_build_service.build_message_async(
                                event,
                                runtime_config=runtime_config,
                            )
                        )
                        message_task_cache[cache_key] = task
            return await task

        async def push_to_session(
            session: str,
            runtime_config: dict[str, Any],
        ) -> tuple[bool, str, dict[str, Any] | None, str | None]:
            # 获取统一格式的会话日志字符串（私聊/群聊 ID (备注名)）
            session_log = self.manager._get_session_log_str(session)
            try:
                # 预筛通过后，在真正发送前再次复核；真实推送提交报数状态，
                # 模拟推送只复用筛选与渲染链路，不污染运行时规则状态。
                decision = self.manager.evaluate_push_decision(
                    event,
                    runtime_config=runtime_config,
                    session_id=session,
                    emit_filter_log=False,
                    commit_state=commit_state,
                )
                if not decision.accepted:
                    if decision.detail:
                        logger.debug(
                            f"[灾害预警] 事件 {event.id} 在 {session_log} 发送前复核未通过，原因：{decision.reason}（{decision.detail}）"
                        )
                    else:
                        logger.debug(
                            f"[灾害预警] 事件 {event.id} 在 {session_log} 发送前复核未通过，原因：{decision.reason}"
                        )
                    return False, session, None, "发送前复核未通过"

                logger.debug(
                    f"[灾害预警] 事件 {event.id} 通过 {session_log} 的发送前复核，准备发送消息"
                )
                # 获取复用或动态渲染的图片/卡片消息链
                message = await get_or_build_message(runtime_config)
                # 调用底座 Session 发送器下发消息
                await self.manager.session_sender.send(session, message)
                logger.debug(f"[灾害预警] 事件 {event.id} 已推送到 {session_log}")
                return True, session, runtime_config.get("message_format", {}), None
            except Exception as e:
                error_name = type(e).__name__

                # 仅对“可能已送达”的超时类失败跳过降级，避免重复推送。
                # 注意：retcode=1200 也会出现在 rich media transfer failed 场景，
                # 这类失败应继续走纯文本/本地图降级，而不是直接吞掉。
                if self._is_ambiguous_timeout_failure(e):
                    logger.warning(
                        f"[灾害预警] 推送到 {session_log} 时疑似超时，但实际推送成功却返回失败，为防止重复，跳过降级重发: {e}"
                    )
                    # 遇到超时时，为了避免被外层统计为彻底失败，可认为其成功送达了，或者至少不能再降级重发。
                    # 这里返回 True，认为该会话已处理完毕，不再次投递。
                    return True, session, runtime_config.get("message_format", {}), None

                logger.error(f"[灾害预警] 推送到 {session_log} 失败: {e}")

                # 如果富媒体发送失败，则尝试从原消息链中提取纯文本进行降级重发。
                fallback_message = self._build_plaintext_fallback_message(
                    locals().get("message")
                )
                if fallback_message is not None:
                    try:
                        await self.manager.session_sender.send(
                            session, fallback_message
                        )
                        if self._is_rich_media_transfer_failure(e):
                            logger.warning(
                                f"[灾害预警] {session_log} 富媒体传输失败，已自动降级重发: {error_name}"
                            )
                        else:
                            logger.warning(
                                f"[灾害预警] {session_log} 富媒体发送失败，已自动降级重发: {error_name}"
                            )
                        return (
                            True,
                            session,
                            runtime_config.get("message_format", {}),
                            None,
                        )
                    except Exception as fallback_error:
                        fallback_error_name = type(fallback_error).__name__
                        logger.error(
                            f"[灾害预警] {session_log} 纯文本降级重发失败: {fallback_error}"
                        )
                        return (
                            False,
                            session,
                            None,
                            f"富媒体发送失败({error_name})，纯文本降级失败({fallback_error_name})",
                        )

                logger.warning(
                    f"[灾害预警] {session_log} 富媒体发送失败，且消息中无可用纯文本可降级: {error_name}"
                )
                return False, session, None, f"富媒体发送失败({error_name})"

        if push_candidates:
            # 通过并发发送缩短整批会话推送耗时，但单个会话的发送前复核仍各自独立执行。
            push_tasks = [
                asyncio.create_task(push_to_session(session, runtime_config))
                for session, runtime_config in push_candidates
            ]
            push_results = await asyncio.gather(*push_tasks, return_exceptions=True)

            for result in push_results:
                if isinstance(result, Exception):
                    logger.error(f"[灾害预警] 会话推送任务异常: {result}")
                    continue

                ok, session, msg_cfg, failure_reason = result
                if ok:
                    push_success_count += 1
                    passed_sessions.append(session)
                    session_message_format_config[session] = msg_cfg or {}
                elif failure_reason:
                    send_failure_stats[failure_reason] = (
                        send_failure_stats.get(failure_reason, 0) + 1
                    )

        self.manager.last_success_sessions = passed_sessions
        return {
            "success": push_success_count > 0,
            "push_success_count": push_success_count,
            "passed_sessions": passed_sessions,
            "session_message_format_config": session_message_format_config,
            # 成功会话的展示时区映射，供分离地图渲染对齐文本时间。
            "session_display_timezone_map": {
                s: tz
                for s, tz in session_display_timezone_map.items()
                if s in passed_sessions
            },
            "filter_reason_stats": filter_reason_stats,
            "filter_reason_detail_stats": filter_reason_detail_stats,
            "send_failure_stats": send_failure_stats,
            "source_id": source_id,
        }

    def _collect_push_candidates(
        self,
        event: EventEnvelope,
        sessions: list[str],
        *,
        session_config_getter=None,
        filter_reason_stats: dict[str, int] | None = None,
        filter_reason_detail_stats: dict[str, int] | None = None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """收集所有通过初筛的待发送目标会话名单（commit_state为False，在此阶段不污染报数状态）。"""
        # 这里仅做“预筛”，所以 commit_state=False，避免在真正发送前就提前消耗报数状态。
        candidates: list[tuple[str, dict[str, Any]]] = []
        if filter_reason_stats is None:
            filter_reason_stats = {}
        if filter_reason_detail_stats is None:
            filter_reason_detail_stats = {}

        simulation_bypass = bool(
            getattr(event, "metadata", {}).get(
                "simulation_bypass_regular_filters", False
            )
        )

        for session in sessions:
            # 会话级配置允许不同会话使用不同推送规则与展示参数。
            runtime_config = (
                session_config_getter(session)
                if callable(session_config_getter)
                else self.manager.config
            )
            if not isinstance(runtime_config, dict):
                runtime_config = self.manager.config
            else:
                runtime_config = dict(runtime_config)

            if simulation_bypass:
                runtime_config["__simulation_bypass_regular_filters"] = True

            if runtime_config.get("push_enabled", True) is False:
                if simulation_bypass:
                    runtime_config["push_enabled"] = True
                else:
                    session_log = self.manager._get_session_log_str(session)
                    logger.debug(f"[灾害预警] {session_log} 推送开关关闭，跳过")
                    continue

            # 过滤判定评估
            decision = self.manager.evaluate_push_decision(
                event,
                runtime_config=runtime_config,
                session_id=session,
                emit_filter_log=False,
                commit_state=False,
            )
            if not decision.accepted:
                reason = decision.reason or "未通过推送条件"
                reason_detail = decision.detail or ""
                filter_reason_stats[reason] = filter_reason_stats.get(reason, 0) + 1
                detail_key = f"{reason}（{reason_detail}）" if reason_detail else reason
                filter_reason_detail_stats[detail_key] = (
                    filter_reason_detail_stats.get(detail_key, 0) + 1
                )
                session_log = self.manager._get_session_log_str(session)
                if reason_detail:
                    logger.debug(
                        f"[灾害预警] 事件 {event.id} 在 {session_log} 的预筛选阶段被拦截，原因：{reason}（{reason_detail}）"
                    )
                else:
                    logger.debug(
                        f"[灾害预警] 事件 {event.id} 在 {session_log} 的预筛选阶段被拦截，原因：{reason}"
                    )
                continue

            candidates.append((session, runtime_config))

        return candidates
