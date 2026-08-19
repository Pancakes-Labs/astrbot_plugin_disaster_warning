"""
WebSocket 重连与离线通知服务。
负责处理连接错误后的重连调度、关键错误判定、离线通知发送与状态清理，
减少 WebSocketManager 中的重连过程式逻辑。
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

from .fan_studio_connection_policy import (
    is_fan_secondary_connection,
    is_fan_studio_connection,
    is_primary_fan_connected,
    is_tls_blocked_signal,
    resolve_secondary_reconnect_interval,
)


class WebSocketReconnectService:
    """WebSocket 重连与离线通知服务。"""

    def __init__(self, manager):
        """保存管理器引用，供重连流程读写连接状态。"""
        self.manager = manager

    def handle_connection_error(
        self, name: str, uri: str, headers: dict | None, error: Exception
    ) -> None:
        """统一处理连接错误。"""
        # 先摘掉活跃连接映射，并异步关闭底层句柄，避免旧连接继续占用上游配额
        existing = self.manager.connections.pop(name, None)
        if existing is not None:
            asyncio.create_task(
                self.manager._close_websocket_quietly(
                    existing,
                    reason=f"{name} 连接错误清理",
                )
            )

        heartbeat_task = self.manager.heartbeat_tasks.pop(name, None)
        if heartbeat_task is not None and not heartbeat_task.done():
            heartbeat_task.cancel()
        self.manager.last_heartbeat_time.pop(name, None)

        # 记录首次断开连接的时间戳，为后续判断“已持续离线多长时间”做数据支撑
        connection_info = self.manager.connection_info.get(name, {})
        offline_since = connection_info.get("offline_since")
        if offline_since is None:
            offline_since = asyncio.get_running_loop().time()
            connection_info["offline_since"] = offline_since
            self.manager.connection_info[name] = connection_info

        # 若管理器本身正处于 stop 阶段，则不再生成新的重连定时任务
        if not self.manager.running:
            return

        # 若属于 SSL 或 证书配置错误这类不可自行恢复的问题，直接终止重试，并发送系统离线通知
        # 注意：仅把「证书配置错误」视为永久性停止重连信号；
        # "tls handshake failed"/"tls error"/"ssl: default" 属于网络层 TLS 阻断，
        # 应交给下方 is_tls_blocked_signal 触发主备切换，而不是提前停止重连。
        error_msg = str(error).lower()
        ssl_keywords = (
            "ssl certificate",
            "certificate verify failed",
            "certificate_verify_failed",
            "unable to get local issuer certificate",
            "self signed certificate",
        )
        if any(kw in error_msg for kw in ssl_keywords) or "certificate" in error_msg:
            logger.warning(f"[灾害预警] {name} 遇到 SSL 配置错误，停止重连: {error}")
            self.emit_offline_notification(
                connection_name=name,
                stage="stop",
                reason=f"SSL/证书错误: {error}",
                retry_count=self.manager.connection_retry_counts.get(name, 0),
                fallback_count=self.manager.fallback_retry_counts.get(name, 0),
            )
            return

        # 判断是否需要强行跳过短时重连阶段（如 401, 403 授权失败，大概率需要管理员修改 Token，短时爆破重试无意义）
        force_fallback = self.is_critical_error(error)
        if force_fallback:
            logger.warning(
                f"[灾害预警] {name} 遇到关键错误，将直接进入兜底重连阶段: {error}"
            )

        # FAN Studio TLS 阻断信号：目标地址 TCP 可达但 TLS 握手（携带 SNI）被
        # 中间设备 RST（如 ws.fanstudio.hk 在部分网络被 SNI 阻断）。继续在同一
        # 地址上短时重试没有意义，应尽快切换到另一台服务器（主/备）。
        if is_fan_studio_connection(name) and is_tls_blocked_signal(error):
            max_retries = self.manager.config.get("max_reconnect_retries", 3)
            current = self.manager.connection_retry_counts.get(name, 0)
            # 已切换过一次（current 已越过第一优先地址的计数段）仍被阻断，
            # 说明主备两个地址在当前网络下都不可达，直接拉满进入长兜底周期，
            # 避免在两台服务器之间高频无限交替重试。
            if current >= max_retries:
                total = max_retries * 2
                self.manager.connection_retry_counts[name] = total
                logger.warning(
                    f"[灾害预警] {name} 检测到 TLS 阻断信号且主备地址均不可达，"
                    f"将直接进入长兜底重试周期: {error}"
                )
            else:
                # 首次在同一地址上命中阻断：跳过剩余重试，让下一轮调度切换到另一台
                self.manager.connection_retry_counts[name] = max_retries
                logger.warning(
                    f"[灾害预警] {name} 检测到 TLS 阻断信号（当前地址 TLS 握手被重置），"
                    f"将跳过同一地址的剩余重试并切换服务器: {error}"
                )

        # 若该连接已存在活跃的挂起重连任务，避免重复拉起重试任务
        existing_task = self.manager.reconnect_tasks.get(name)
        if existing_task and not existing_task.done():
            logger.debug(f"[灾害预警] {name} 已有正在运行的重连任务，跳过重复创建")
            return

        # 创建并挂载对应的异步重连调度协程
        reconnect_task = asyncio.create_task(
            self.schedule_reconnect(
                name, uri, headers, connection_info, force_fallback=force_fallback
            ),
            name=f"dw_reconnect_{name}",
        )
        self.manager.reconnect_tasks[name] = reconnect_task

    def is_critical_error(self, error: Exception) -> bool:
        """判断是否为需要直接进入兜底重连的关键错误。"""
        error_msg = str(error).lower()
        # 授权拒绝错误，或由业务层主动认定的不可瞬时重试的关闭帧
        if "401" in error_msg or "403" in error_msg:
            return True
        if "协议错误关闭（不重连）" in error_msg:
            return True
        return False

    async def schedule_reconnect(
        self,
        name: str,
        uri: str,
        headers: dict | None = None,
        connection_info: dict[str, Any] | None = None,
        force_fallback: bool = False,
    ) -> None:
        """计划重连。"""
        # 再次确认管理器依然处于运转状态
        if not self.manager.running:
            return

        try:
            connection_info = connection_info or {}
            offline_since = connection_info.get("offline_since")
            if offline_since is None:
                offline_since = asyncio.get_running_loop().time()
                connection_info["offline_since"] = offline_since
                self.manager.connection_info[name] = connection_info

            # 读取管理器配置文件中关于短重试、兜底长周期重试的详细策略
            max_retries = self.manager.config.get("max_reconnect_retries", 3)
            reconnect_interval = self.manager.config.get("reconnect_interval", 10)
            # FAN 次要通道：主通道离线或命中配额时拉长退避，避免与 /all 抢连接。
            if is_fan_secondary_connection(name):
                reconnect_interval = resolve_secondary_reconnect_interval(
                    default_interval=int(reconnect_interval or 10),
                    quota_hit=bool(connection_info.get("quota_hit")),
                    primary_online=is_primary_fan_connected(self.manager),
                )
            fallback_enabled = self.manager.config.get("fallback_retry_enabled", True)
            fallback_interval = self.manager.config.get("fallback_retry_interval", 1800)
            fallback_max_count = self.manager.config.get("fallback_retry_max_count", -1)

            # 获取该连接当前的重试情况
            current_retry = self.manager.connection_retry_counts.get(name, 0)
            current_fallback = self.manager.fallback_retry_counts.get(name, 0)
            has_backup = connection_info and connection_info.get("backup_url")
            # 存在备用地址时，短时最大重试次数翻倍（主地址试完后在备用地址再试同等次数）
            total_max_retries = max_retries * 2 if has_backup else max_retries
            # 读取 FAN Studio 服务器偏好，按偏好顺序重试
            conn_config = (
                connection_info.get("connection_config", {})
                if isinstance(connection_info, dict)
                else {}
            )
            server_pref_str = conn_config.get("server_preference", "")
            if server_pref_str and is_fan_secondary_connection(name):
                # 对于 FAN 次要通道，优先跟随主通道的偏好
                primary_info = self.manager.connection_info.get("fan_studio_all", {})
                primary_config = (
                    primary_info.get("connection_config", {})
                    if isinstance(primary_info, dict)
                    else {}
                )
                server_pref_str = primary_config.get(
                    "server_preference", server_pref_str
                )

            # 如果触发了关键错误需要强行降级，直接把当前短时重试计数拉满以切入长兜底周期
            if force_fallback:
                current_retry = total_max_retries
                self.manager.connection_retry_counts[name] = total_max_retries

            # 短时尝试彻底耗尽，准备转长周期兜底
            if current_retry >= total_max_retries:
                # 若配置中压根未启用长周期兜底，宣告彻底离线并发送停止重连通知
                if not fallback_enabled:
                    logger.error(
                        f"[灾害预警] {name} 重连失败，已达到最大重试次数 ({total_max_retries})，停止重连"
                    )
                    self.emit_offline_notification(
                        connection_name=name,
                        stage="stop",
                        reason="短时重连次数已达上限且未启用兜底重试",
                        retry_count=current_retry,
                        fallback_count=current_fallback,
                    )
                    return

                # 若配置了兜底最大次数，且已超限，同样宣告彻底离线
                if fallback_max_count != -1 and current_fallback >= fallback_max_count:
                    logger.error(
                        f"[灾害预警] {name} 兜底重试失败，已达到最大兜底重试次数 ({fallback_max_count})，停止重连"
                    )
                    self.emit_offline_notification(
                        connection_name=name,
                        stage="stop",
                        reason="兜底重试次数已达上限",
                        retry_count=current_retry,
                        fallback_count=current_fallback,
                    )
                    return

                # 累加长周期重试计数并整理时间显示格式
                self.manager.fallback_retry_counts[name] = current_fallback + 1
                fallback_display = current_fallback + 1
                fallback_max_display = (
                    "无限" if fallback_max_count == -1 else str(fallback_max_count)
                )

                if fallback_interval < 60:
                    fallback_interval_display = f"{fallback_interval} 秒"
                else:
                    minutes = fallback_interval // 60
                    seconds = fallback_interval % 60
                    if seconds == 0:
                        fallback_interval_display = f"{minutes} 分钟"
                    else:
                        fallback_interval_display = f"{minutes} 分钟 {seconds} 秒"

                logger.warning(
                    f"[灾害预警] {name} 短时重连失败，将在 {fallback_interval_display} 后进行兜底重试 "
                    f"({fallback_display}/{fallback_max_display})"
                )

                # 发送离线降级到长兜底阶段的通知，通知群组和管理员
                self.emit_offline_notification(
                    connection_name=name,
                    stage="fallback",
                    reason="短时重连失败，进入兜底重试",
                    next_retry_in=fallback_interval_display,
                    retry_count=current_retry,
                    fallback_count=self.manager.fallback_retry_counts.get(name, 0),
                )

                # 睡眠指定长周期
                await asyncio.sleep(fallback_interval)
                if not self.manager.running:
                    return

                # 释放占位 task，并在重连前清理可能残留的旧句柄
                self.manager.reconnect_tasks.pop(name, None)
                await self.manager._release_existing_connection(
                    name,
                    reason="兜底重连前清理旧连接",
                    keep_connection_info=True,
                )
                await self.manager.connect(
                    name,
                    uri,
                    headers,
                    is_retry=True,
                    connection_info=connection_info,
                )
                return

            # 短时重连仍在进行中，判定下一轮连第一优先还是第二优先地址
            target_uri = uri
            # 根据原始地址（未受偏好交换影响）判断目标 URI 是主还是备
            conn_config = (
                connection_info.get("connection_config", {})
                if isinstance(connection_info, dict)
                else {}
            )
            original_url = str(conn_config.get("original_url") or "").strip()
            original_backup = str(conn_config.get("original_backup") or "").strip()

            # 前 max_retries 次按第一优先地址（uri 已按服务器偏好交换），
            # 之后切换到第二优先地址（backup_url 也已按偏好交换）。
            # 注意：在"备用服务器优先"偏好下，第一优先是物理备用、第二优先是
            # 物理主服务器，因此不能凭"阶段"推断服务器类型，必须与原始地址比对。
            if has_backup and current_retry >= max_retries:
                backup_url = connection_info.get("backup_url")
                if backup_url:
                    target_uri = backup_url

            # 用原始地址（未受偏好交换影响）判定目标 URI 对应的物理服务器类型
            if target_uri and original_backup and target_uri == original_backup:
                server_type = "备用服务器"
            elif target_uri and original_url and target_uri == original_url:
                server_type = "主服务器"
            else:
                # 原始地址缺失等异常兜底：按重试阶段推断（前半段第一优先，后半段第二优先）
                server_type = (
                    "备用服务器" if current_retry >= max_retries else "主服务器"
                )

            # 记录当前连接的服务器类型
            if isinstance(connection_info, dict):
                connection_info["active_server"] = (
                    "backup" if server_type == "备用服务器" else "primary"
                )

            # 整理日志展示用的第几次重试：按物理服务器各自归零，
            # 避免出现 (6/5) 这类跨阶段越界计数
            if current_retry >= max_retries:
                display_retry = current_retry - max_retries + 1
            else:
                display_retry = current_retry + 1

            logger.info(
                f"[灾害预警] {name} 将在 {reconnect_interval} 秒后尝试重连{server_type} ({display_retry}/{max_retries})"
            )

            # 评估是否需要对“短暂离线了较长时间，但仍未耗尽短时次数”的中间状态发送警报通知
            offline_elapsed = asyncio.get_running_loop().time() - offline_since
            # 阈值取“重连间隔 × 3”与 30 秒兜底的较大者：
            # 避免重连间隔配置过小的情况下，离线仅数秒就触发“离线时间过长”通知。
            short_retry_notify_threshold = max(reconnect_interval * 3, 30)
            short_retry_notified = bool(
                connection_info.get("short_retry_notified", False)
            )
            if (
                short_retry_notify_threshold > 0
                and offline_elapsed >= short_retry_notify_threshold
                and not short_retry_notified
            ):
                self.emit_offline_notification(
                    connection_name=name,
                    stage="short_retry",
                    reason=(
                        f"离线已持续至少 {int(short_retry_notify_threshold)} 秒，"
                        "仍处于短时重连阶段"
                    ),
                    next_retry_in=f"{reconnect_interval} 秒",
                    retry_count=current_retry,
                    fallback_count=current_fallback,
                )
                connection_info["short_retry_notified"] = True
                self.manager.connection_info[name] = connection_info

            # 睡眠指定的短周期
            await asyncio.sleep(reconnect_interval)
            if not self.manager.running:
                return

            # 释放占位 task，并在重连前清理可能残留的旧句柄
            self.manager.reconnect_tasks.pop(name, None)
            await self.manager._release_existing_connection(
                name,
                reason="短时重连前清理旧连接",
                keep_connection_info=True,
            )
            await self.manager.connect(
                name,
                target_uri,
                headers,
                is_retry=True,
                connection_info=connection_info,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[灾害预警] 重连调度失败 {name}: {e}")

    def emit_offline_notification(
        self,
        connection_name: str,
        stage: str,
        reason: str,
        next_retry_in: str | None = None,
        retry_count: int | None = None,
        fallback_count: int | None = None,
    ) -> None:
        """触发离线通知回调（异步安全）。"""
        if not self.manager._offline_notify_callback:
            return

        # 整理离线载荷数据
        info = self.manager.connection_info.get(connection_name, {})
        payload = {
            "connection_name": connection_name,
            "data_source": info.get("data_source")
            or info.get("connection_name")
            or "unknown",
            "stage": stage,
            "reason": reason,
            "next_retry_in": next_retry_in,
            "retry_count": retry_count,
            "fallback_count": fallback_count,
        }
        # 以非阻塞异步任务方式抛出给外层订阅者
        asyncio.create_task(self.manager._offline_notify_callback(payload))
