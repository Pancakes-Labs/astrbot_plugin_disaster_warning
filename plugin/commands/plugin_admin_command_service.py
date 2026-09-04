"""
插件后台管理命令服务。
负责灾害预警插件中面向管理员的状态、日志、统计、推送开关与配置查看命令逻辑，
减少 main.DisasterWarningPlugin 中的命令实现体积。
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.core import DEMO_MODE
from astrbot.core.desktop_runtime import is_desktop_managed_backend

from ...core.app.services import quoted_plain_result
from ...core.app.services.eqsc_channel_service import EqscChannelService
from ...utils.version import get_plugin_name, get_plugin_version
from ..astrbot_restart import restart_astrbot_in_background
from .forward_helper import build_forward_nodes, send_forward_blocks
from .telemetry_mixin import CommandTelemetryMixin


class PluginAdminCommandService(CommandTelemetryMixin):
    """后台管理命令服务。"""

    def __init__(self, plugin):
        self.plugin = plugin

    def _get_session_config_manager(self):
        """安全获取会话配置管理器实例，不可用时返回 None。"""
        service = getattr(self.plugin, "disaster_service", None)
        if service and hasattr(service, "session_config_manager"):
            return service.session_config_manager
        return None

    def _get_session_log_str(self, session_umo: str) -> str:
        """获取统一格式的会话日志字符串（私聊/群聊 ID (备注名)）。

        当会话配置管理器不可用时回退到原始 UMO。
        """
        mgr = self._get_session_config_manager()
        if mgr:
            return mgr.get_session_log_str(session_umo)
        return session_umo

    async def handle_disaster_restart(self, event):
        """处理插件重载命令，等价于 AstrBot WebUI 中的重载插件操作。

        重载时会依次触发：
        1. 终止旧插件实例（调用 terminate()，回收服务/WebServer/浏览器资源）
        2. 解绑旧插件注册表
        3. 重新导入模块并创建新实例（调用 initialize()）
        4. 新实例自动完成数据源装配与 WebAdmin 启动

        重载成功后旧实例被销毁，因此最终结果通过 context.send_message 直接发送，
        不再通过 yield 返回。
        """
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        # 向用户发送"正在重载"提示（此时旧实例尚未销毁，yield 安全可用）
        yield event.plain_result("🔄 正在重载灾害预警插件，请稍候…")

        try:
            # 通过 Context._star_manager 获取 PluginManager 实例
            plugin_manager = getattr(self.plugin.context, "_star_manager", None)
            if plugin_manager is None:
                # 上报失败（管理器不可用，命令未真正执行）
                await self._track_command_feature(
                    "command_admin_action",
                    {
                        "action": "reload_plugin",
                        "success": False,
                        "reason": "no_plugin_manager",
                    },
                )
                yield event.plain_result("❌ 无法获取 AstrBot 插件管理器")
                return

            # 插件名取自 metadata.yaml 的 name 字段，避免硬编码与元数据配置漂移；
            # 读取失败时回退到插件目录名，保证兼容旧逻辑。
            plugin_name = get_plugin_name()
            # 触发前上报请求状态（重载成功会销毁当前实例，无法在完成后上报，故先记录 requested）。
            # 注意：使用独立 action（reload_plugin_requested）避免与失败事件
            # （reload_plugin/success=False）共享节流键，防止 10 秒节流吞掉紧随其后的失败事件。
            await self._track_command_feature(
                "command_admin_action",
                {
                    "action": "reload_plugin_requested",
                    "success": True,
                    "plugin": plugin_name,
                },
            )
            success, message = await plugin_manager.reload(plugin_name)

            # 重载后旧实例已销毁，改用 context.send_message 直接发送结果
            if success:
                await self.plugin.context.send_message(
                    event.unified_msg_origin,
                    MessageChain([Comp.Plain("✅ 灾害预警插件重载完成，正在重新启动")]),
                )
            else:
                # 上报失败（reload 返回失败结果）。
                # reason 仅用固定失败码，不上报原始 message 文本，避免插件管理器
                # 返回的 URL 凭据/令牌/内部路径等敏感信息随遥测外泄。
                await self._track_command_feature(
                    "command_admin_action",
                    {
                        "action": "reload_plugin",
                        "success": False,
                        "reason": "reload_failed",
                    },
                )
                await self.plugin.context.send_message(
                    event.unified_msg_origin,
                    MessageChain([Comp.Plain(f"❌ 插件重载失败: {message}")]),
                )
        except Exception as e:
            logger.error(f"[灾害预警] 插件重载失败: {e}")
            # 上报失败（异常路径）。reason 仅用固定失败码 + 异常类型名，
            # 不上报原始异常消息文本，避免敏感信息随遥测外泄。
            await self._track_command_feature(
                "command_admin_action",
                {
                    "action": "reload_plugin",
                    "success": False,
                    "reason": "exception",
                    "error_type": type(e).__name__,
                },
            )
            try:
                await self.plugin.context.send_message(
                    event.unified_msg_origin,
                    MessageChain([Comp.Plain(f"❌ 插件重载失败: {e}")]),
                )
            except Exception:
                pass

    async def handle_restart_astrbot(self, event):
        """处理重启 AstrBot 命令，等价于 AstrBot WebUI 中「设置 → 维护 → 重启 AstrBot」。

        重启是进程级的 os.exec* 替换，不依赖插件实例存活，因此：
        1. 先通过 yield 发送「即将重启」提示（此时管道仍可用）
        2. 检查桌面版托管 / DEMO 模式守卫，拒绝时直接提示
        3. 通过 context.send_message 发送最终提示（进程即将替换，yield 管道已不可靠）
        4. 通过重启适配层在后台 daemon 线程触发进程重启（内部 sleep 3 秒后
           终止全部子进程并 os.exec* 替换当前进程完成重启），适配层会
           按 AstrBot 版本自动选择新版 process_restart 或旧版 updator 策略
        """
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        # 桌面版 Launcher 托管后端时不允许核心直接重启，重启策略内部也会抛错，
        # 这里提前拦截并给出更友好的提示。
        if is_desktop_managed_backend():
            yield event.plain_result(
                "❌ 当前由 AstrBot Desktop 托管运行，无法通过核心命令重启。\n"
                "请从桌面客户端执行重启或更新。"
            )
            return

        if DEMO_MODE:
            yield event.plain_result("❌ 演示模式（DEMO_MODE）下不允许执行此操作。")
            return

        # 告知用户即将重启（此时事件管道仍可用，yield 安全）
        yield event.plain_result("🔄 即将重启 AstrBot，约 3 秒后进程将重新启动…")

        try:
            # 触发前上报请求状态（成功路径进程将被替换，无法在完成后上报，故先记录 requested）。
            # 注意：使用独立 action（restart_astrbot_requested）避免与失败事件共享节流键，
            # 防止 10 秒节流吞掉线程内随后上报的失败事件。
            await self._track_command_feature(
                "command_admin_action",
                {
                    "action": "restart_astrbot_requested",
                    "success": True,
                },
            )
            # 重启动作由适配层在后台 daemon 线程执行：
            # 先确认派发成功，再发送成功提示，避免"线程已启动但实际失败"
            # 时用户误收到成功提示（重启失败无法通过回滚插件恢复）。
            loop = asyncio.get_running_loop()
            unified_msg_origin = event.unified_msg_origin

            def _on_thread_failure(restart_err: Exception) -> None:
                """重启线程内失败回调：向原会话推送失败通知并上报失败遥测。"""
                logger.error(f"[灾害预警] AstrBot 重启线程执行失败: {restart_err}")
                try:
                    # 线程内无法直接 await，用 run_coroutine_threadsafe 将失败
                    # 通知与失败遥测调度回事件循环执行（线程安全）。
                    asyncio.run_coroutine_threadsafe(
                        self._notify_restart_astrbot_failure(
                            unified_msg_origin, restart_err
                        ),
                        loop,
                    )
                except Exception:
                    pass

            ok, error = restart_astrbot_in_background(on_failure=_on_thread_failure)
            if not ok:
                raise RuntimeError(f"无法触发 AstrBot 重启: {error or '未知原因'}")

            # 派发成功后才发送最终提示：
            # 进程级替换不销毁任何插件对象，但事件总线管道可能在进程退出瞬间被中断，
            # 因此最终提示不依赖 yield，直接用 send_message 发送，重启线程内部会
            # sleep 3 秒，足以让消息送达平台。
            # 注意：成功提示的发送失败只记录日志，绝不能进入下方重启失败遥测路径
            # —— 此时重启线程已派发、进程仍会重启，误报失败会让用户与运维误判。
            try:
                await self.plugin.context.send_message(
                    unified_msg_origin,
                    MessageChain(
                        [
                            Comp.Plain(
                                "✅ 已触发 AstrBot 重启，进程将在数秒内重新启动，请稍候…"
                            )
                        ]
                    ),
                )
            except Exception as notify_err:
                logger.warning(
                    f"[灾害预警] 重启成功提示发送失败（已忽略）: {notify_err}"
                )
        except Exception as e:
            logger.error(f"[灾害预警] 触发 AstrBot 重启失败: {e}")
            # 上报失败（启动线程前的异常路径）。
            # reason 仅用固定失败码 + 异常类型名，不上报原始异常消息文本。
            await self._track_command_feature(
                "command_admin_action",
                {
                    "action": "restart_astrbot",
                    "success": False,
                    "reason": "exception",
                    "error_type": type(e).__name__,
                },
            )
            try:
                await self.plugin.context.send_message(
                    event.unified_msg_origin,
                    MessageChain(
                        [Comp.Plain("❌ 触发 AstrBot 重启失败，请查看服务端日志")]
                    ),
                )
            except Exception:
                pass

    async def _notify_restart_astrbot_failure(
        self, unified_msg_origin: str, error: Exception
    ) -> None:
        """AstrBot 重启线程失败时，向原会话推送失败通知并上报失败遥测。

        注意：异常原文只在服务端日志记录；发送给用户的必须是固定文案，
        避免向（可能是群聊的）会话泄露内部路径/运行环境/上游实现细节。
        """
        try:
            await self.plugin.context.send_message(
                unified_msg_origin,
                MessageChain([Comp.Plain("❌ AstrBot 重启失败，请查看服务端日志")]),
            )
        except Exception:
            pass
        try:
            # reason 仅用固定失败码 + 异常类型名，不上报原始异常消息文本。
            await self._track_command_feature(
                "command_admin_action",
                {
                    "action": "restart_astrbot",
                    "success": False,
                    "reason": "exception",
                    "error_type": type(error).__name__,
                },
            )
        except Exception:
            pass

    async def web_reload_plugin(self) -> tuple[bool, str]:
        """Web 管理端重载插件（等价于 /灾害预警重启 指令）。

        与指令路径共享同一套守卫、遥测与插件管理器调用，保证行为等价。
        注意：重载会销毁旧插件实例并重建 Web 管理端，若在 HTTP 请求内
        同步等待重载完成，旧 uvicorn 服务器会因本请求仍处于优雅关闭窗口
        而取消在途请求（打断 reload 中途），且新实例绑定端口可能冲突。
        因此这里只完成守卫与遥测后立即返回，实际重载在后台 asyncio
        任务中执行，与指令路径触发的底层操作完全一致。
        """
        plugin_manager = getattr(self.plugin.context, "_star_manager", None)
        if plugin_manager is None:
            await self._track_command_feature(
                "command_admin_action",
                {
                    "action": "reload_plugin",
                    "success": False,
                    "reason": "no_plugin_manager",
                },
            )
            return False, "无法获取 AstrBot 插件管理器"

        plugin_name = get_plugin_name()
        await self._track_command_feature(
            "command_admin_action",
            {
                "action": "reload_plugin_requested",
                "success": True,
                "plugin": plugin_name,
            },
        )

        loop = asyncio.get_running_loop()

        async def _run_reload() -> None:
            try:
                success, message = await plugin_manager.reload(plugin_name)
                if not success:
                    logger.warning(f"[灾害预警] Web 端重载插件操作失败: {message}")
                    await self._track_command_feature(
                        "command_admin_action",
                        {
                            "action": "reload_plugin",
                            "success": False,
                            "reason": "reload_failed",
                        },
                    )
            except Exception as exc:  # noqa: BLE001 - 后台任务边界兜底
                logger.error(f"[灾害预警] Web 端重载插件操作失败: {exc}")
                await self._track_command_feature(
                    "command_admin_action",
                    {
                        "action": "reload_plugin",
                        "success": False,
                        "reason": "exception",
                        "error_type": type(exc).__name__,
                    },
                )

        try:
            loop.create_task(_run_reload())
        except RuntimeError:
            # 事件循环已关闭等极端场景下无法派发任务，视为失败。
            return False, "无法派发插件重载任务，请查看服务端日志"

        return True, "正在重载灾害预警插件，请稍候…"

    async def web_restart_astrbot(self) -> tuple[bool, str]:
        """Web 管理端重启 AstrBot 进程（等价于 /重启AstrBot 指令）。

        与指令路径共享同一套桌面托管/DEMO 守卫、遥测与重启适配层调用。
        重启由适配层在后台 daemon 线程执行（内部 sleep 3 秒后 os.exec*
        替换进程），因此本方法在派发成功后立即返回，响应可安全送达。
        """
        if is_desktop_managed_backend():
            return False, (
                "当前由 AstrBot Desktop 托管运行，无法通过核心命令重启。\n"
                "请从桌面客户端执行重启或更新。"
            )

        if DEMO_MODE:
            return False, "演示模式（DEMO_MODE）下不允许执行此操作。"

        await self._track_command_feature(
            "command_admin_action",
            {
                "action": "restart_astrbot_requested",
                "success": True,
            },
        )

        loop = asyncio.get_running_loop()

        def _on_thread_failure(restart_err: Exception) -> None:
            """重启线程内失败回调：仅记录日志并上报失败遥测（Web 无会话可推送）。"""
            logger.error(f"[灾害预警] AstrBot 重启线程执行失败: {restart_err}")
            try:
                asyncio.run_coroutine_threadsafe(
                    self._track_command_feature(
                        "command_admin_action",
                        {
                            "action": "restart_astrbot",
                            "success": False,
                            "reason": "exception",
                            "error_type": type(restart_err).__name__,
                        },
                    ),
                    loop,
                )
            except Exception:
                pass

        try:
            ok, error = restart_astrbot_in_background(on_failure=_on_thread_failure)
            if not ok:
                raise RuntimeError(f"无法触发 AstrBot 重启: {error or '未知原因'}")
        except Exception as e:
            logger.error(f"[灾害预警] Web 端触发 AstrBot 重启失败: {e}")
            await self._track_command_feature(
                "command_admin_action",
                {
                    "action": "restart_astrbot",
                    "success": False,
                    "reason": "exception",
                    "error_type": type(e).__name__,
                },
            )
            return False, "触发 AstrBot 重启失败，请查看服务端日志"

        return True, "已触发 AstrBot 重启，进程将在数秒内重新启动，请稍候…"

    async def handle_disaster_reconnect(self, event):
        """处理强制重连命令，尝试对所有离线或异常的数据源触发重连尝试。

        触发后立即返回"已触发"概览，并注册底层手动重连结果回执回调，
        待各连接的真实建连结果（成功/失败/超时）到达后，异步推送到当前会话，
        避免指令只反馈"已触发"却没有后续真实结果。
        """
        # 管理类命令统一在入口先做管理员校验，避免内部逻辑重复散落权限判断。
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if not self.plugin.disaster_service:
            yield event.plain_result("❌ 灾害预警服务未启动")
            return

        yield event.plain_result("🔄 正在尝试重连所有离线数据源...")

        try:
            reconnect_service = self.plugin.disaster_service.reconnect_service
            # 记录触发指令的会话，作为异步回执的推送目标。
            target_session = getattr(event, "unified_msg_origin", None)

            # 注册本批次回执回调，并拿到请求批次标识；
            # 重连服务会在本轮结果全部消费完后自动清理该批次。
            request_id, _unregister = reconnect_service.register_reconnect_callback(
                self._build_reconnect_receipt_sender(target_session)
            )
            results = await reconnect_service.reconnect_all_sources(
                request_id=request_id
            )

            lines = ["🔄 重连操作结果："]
            success_count = 0
            fail_count = 0
            skip_count = 0

            for name, status in results.items():
                # 展示名统一由重连服务按连接配置解析，避免向用户暴露内部字段名。
                display_name = reconnect_service.resolve_display_name(name)
                if "已触发" in status:
                    success_count += 1
                    icon = "✅"
                elif "失败" in status:
                    fail_count += 1
                    icon = "❌"
                else:
                    skip_count += 1
                    icon = "⏩"
                lines.append(f"  {icon} {display_name}: {status}")

            lines.append("")
            lines.append(
                f"📊 统计: 触发 {success_count}, 跳过 {skip_count}, 失败 {fail_count}"
            )
            if success_count > 0:
                lines.append("⏳ 重连结果将稍后推送，请留意后续消息。")
            # 匿名上报功能执行遥测（管理类命令统一归并到 command_admin_action）
            await self._track_command_feature(
                "command_admin_action",
                {
                    "action": "force_reconnect",
                    "success": True,
                    "triggered_count": success_count,
                    "failed_count": fail_count,
                    "skipped_count": skip_count,
                },
            )
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            await self._track_command_feature(
                "command_admin_action",
                {"action": "force_reconnect", "success": False},
            )
            logger.error(f"[灾害预警] 重连操作失败: {e}")
            yield event.plain_result(f"❌ 重连操作失败: {str(e)}")

    def _build_reconnect_receipt_sender(self, target_session: str | None):
        """构造重连结果回执的异步发送器。

        回执载荷包含展示名与结果描述，由重连服务在真实建连结果到达后调用。
        订阅者的清理由重连服务在"本轮所有等待结果消费完毕"时统一完成。
        """
        if not target_session:
            # 无有效目标会话时直接返回异步空操作，避免回调链断裂。
            # _noop_reconnect_receipt 已具备正确异步签名，无需额外 lambda 包裹。
            return self._noop_reconnect_receipt

        async def _send(payload: dict) -> None:
            display_name = str(payload.get("display_name") or "未知连接")
            success = bool(payload.get("success"))
            stage = str(payload.get("stage") or "result")
            message = str(payload.get("message") or "")

            if stage == "timeout":
                line = f"⏳ {display_name}：{message}"
            elif success:
                line = f"✅ {display_name}：重连成功"
            else:
                line = f"❌ {display_name}：{message}"
            await self._send_plain_to_session(target_session, line)

        return _send

    async def _noop_reconnect_receipt(self, payload: dict) -> None:
        """无目标会话时的空回执处理（仅保留接口一致性）。"""
        pass

    async def _send_plain_to_session(self, session: str, text: str) -> None:
        """向指定会话发送纯文本消息（复用消息管理器的会话发送能力）。"""
        try:
            message_manager = getattr(
                self.plugin.disaster_service, "message_manager", None
            )
            if message_manager is None:
                logger.warning("[灾害预警] 消息管理器不可用，无法发送重连回执")
                return
            session_sender = getattr(message_manager, "session_sender", None)
            if session_sender is None:
                logger.warning("[灾害预警] 会话发送器不可用，无法发送重连回执")
                return
            await session_sender.send(session, MessageChain([Comp.Plain(text)]))
        except Exception as e:
            logger.error(f"[灾害预警] 重连回执发送到 {session} 失败: {e}")

    async def handle_disaster_status(self, event):
        """处理运行状态查询命令，以合并转发多节点消息形式展示各个连接状态与子数据源情况。"""
        if not self.plugin.disaster_service:
            yield event.plain_result("❌ 灾害预警服务未启动")
            return

        try:
            status = self.plugin.disaster_service.get_service_status()
            running_state = "🟢 运行中" if status["running"] else "🔴 已停止"
            uptime = status.get("uptime", "未知")
            plugin_version = get_plugin_version()

            # bot_id 由公共 forward_helper 内部从 event 获取；此处仅需显示名。
            bot_name = "灾害预警"

            # 对应展示名称映射（命令文本场景投影）。
            # connection_label_map / source_group_label_map 的多数值与
            # display_registry.CONNECTION_DISPLAY_NAMES 一致，但命令文本保留了两处
            # 有意差异：fan_studio_cenc_ir 用空格（"FAN Studio 烈度速报"）而非括号；
            # snet_msil 用"日本海沟 S-Net 海底震度计"。
            # 修改展示名时请同时核对 display_registry.py 与下方各投影表。
            connection_label_map = OrderedDict(
                [
                    ("fan_studio_all", "FAN Studio"),
                    ("fan_studio_cenc_ir", "FAN Studio 烈度速报"),
                    ("p2p_main", "P2P地震情報"),
                    ("wolfx_all", "Wolfx"),
                    ("openquake_api", "OpenQuakeAPI"),
                ]
            )
            source_group_label_map = OrderedDict(
                [
                    ("fan_studio", "FAN Studio"),
                    ("p2p_earthquake", "P2P地震情報"),
                    ("wolfx", "Wolfx"),
                    ("openquake_api", "OpenQuakeAPI"),
                    ("eqsc", "EQSC API"),
                    ("snet", "NIED S-Net"),
                ]
            )
            source_label_map = {
                "china": "中国地震预警网",
                "china_pr": "中国地震预警网（省级）",
                "japan": "日本气象厅",
                "taiwan": "台湾中央气象署",
                "weather": "中国气象局",
                "earthquake": "中国地震台网",
                "usgs": "USGS",
                "eew": "紧急地震速报",
                "earthquake_info": "地震情报",
                "global_quake": "Global Quake",
                "china_typhoon": "中国气象局：实时活跃台风",
                "snet_msil": "日本海沟 S-Net 海底震度计",
            }
            scoped_sub_source_label_map = {
                "FAN Studio": {
                    "china_earthquake_warning": "中国地震预警网 (CEA)",
                    "china_earthquake_warning_provincial": "中国地震预警网 (省级)",
                    "taiwan_cwa_earthquake": "台湾中央气象署: 强震即时警报",
                    "taiwan_cwa_report": "台湾中央气象署: 地震报告",
                    "china_cenc_earthquake": "中国地震台网 (CENC)",
                    "china_cenc_intensity_report": "中国地震台网 (CENC) 烈度速报",
                    "usgs_earthquake": "美国地质调查局 (USGS)",
                    "fssn_cmt": "FSSN 矩心矩张量解 (CMT)",
                    "usa_shakealert": "美国 ShakeAlert 地震预警",
                    "china_weather_alarm": "中国气象局: 气象预警",
                    "china_tsunami": "自然资源部海啸预警中心",
                    "china_typhoon": "中国气象局：实时活跃台风",
                    "japan_jma_eew": "日本气象厅: 紧急地震速报",
                },
                "P2P地震情報": {
                    "japan_jma_eew": "日本气象厅: 紧急地震速报",
                    "japan_jma_earthquake": "日本气象厅: 地震情报",
                    "japan_jma_tsunami": "日本气象厅: 海啸予报",
                },
                "Wolfx": {
                    "japan_jma_eew": "日本气象厅: 紧急地震速报",
                    "china_cenc_eew": "中国地震预警网 (CEA)",
                    "taiwan_cwa_eew": "台湾中央气象署: 强震即时警报",
                    "japan_jma_earthquake": "日本气象厅地震情报",
                    "china_cenc_earthquake": "中国地震台网地震测定",
                },
                "OpenQuakeAPI": {
                    "global_quake": "Global Quake",
                    "china_weather_alarm": "中国气象局: 气象预警",
                },
                "EQSC API": {
                    "typhoon": "中国气象局：实时活跃台风",
                    "jma_tsunami": "日本气象厅: 海啸予报",
                    "china_cenc_intensity_report": "中国地震台网 (CENC) 烈度速报",
                },
                "NIED S-Net": {
                    "enabled": "日本海沟 S-Net 海底震度计",
                },
            }

            def _build_forward_nodes(blocks: list[str]) -> Comp.Nodes | None:
                """生成便于客户端折叠阅读的合并转发节点（复用公共实现）。"""
                return build_forward_nodes(
                    blocks,
                    event=event,
                    quote_first=True,
                    plugin=self.plugin,
                    name=bot_name,
                )

            def _map_sub_source_name(group_display_name: str, raw_key: str) -> str:
                """将原始的子源键名映射为好看的展示名称。"""
                normalized_key = str(raw_key or "").strip()
                if not normalized_key:
                    return normalized_key
                scoped_map = scoped_sub_source_label_map.get(group_display_name, {})
                return scoped_map.get(
                    normalized_key,
                    source_label_map.get(normalized_key, normalized_key),
                )

            # EQSC / S-Net 为 HTTP 通道，不在 ws_manager 连接表中，单独补充
            # 注意：get_service_status() 已把 EQSC、S-Net 计入 active/total，这里只负责展示详情
            eqsc_health: dict = {}
            eqsc_channel = getattr(
                self.plugin.disaster_service, "eqsc_channel_service", None
            )
            if eqsc_channel is not None:
                getter = getattr(eqsc_channel, "get_health_status", None)
                if callable(getter):
                    try:
                        maybe_health = getter()
                        if isinstance(maybe_health, dict):
                            eqsc_health = maybe_health
                    except Exception:
                        eqsc_health = {}

            if not eqsc_health:
                # 通道服务不可用时，回退到配置层判定
                eqsc_cfg = (
                    (self.plugin.config.get("data_sources", {}) or {}).get("eqsc", {})
                    if isinstance(self.plugin.config, dict)
                    else {}
                )
                if not isinstance(eqsc_cfg, dict):
                    eqsc_cfg = {}
                config_enabled, typhoon_enrichment = (
                    EqscChannelService.resolve_eqsc_flags(eqsc_cfg)
                )
                token_configured = bool(
                    str(eqsc_cfg.get("refresh_token", "") or "").strip()
                )
                eqsc_health = {
                    "enabled": config_enabled and token_configured,
                    "config_enabled": config_enabled,
                    "typhoon": typhoon_enrichment,
                    "token_configured": token_configured,
                    "access_token_valid": False,
                    "circuit_open": False,
                    # 子数据源展示只看子开关本身
                    "sub_sources": {
                        "china_typhoon": typhoon_enrichment,
                    },
                }

            eqsc_enabled = bool(eqsc_health.get("enabled"))
            eqsc_circuit_open = bool(eqsc_health.get("circuit_open", False))
            eqsc_token_valid = bool(eqsc_health.get("access_token_valid", False))
            if not eqsc_enabled:
                eqsc_state_text = "⚪ 未启用"
            elif eqsc_circuit_open:
                eqsc_state_text = "🟠 熔断中"
            elif eqsc_token_valid:
                # AccessToken 有效 = 活跃连接
                eqsc_state_text = "🟢 可用"
            else:
                eqsc_state_text = "🔴 鉴权失效"

            # S-Net 轮询状态
            snet_poll = getattr(self.plugin.disaster_service, "snet_poll_service", None)
            snet_cfg = (
                (self.plugin.config.get("data_sources", {}) or {}).get("snet", {})
                if isinstance(self.plugin.config, dict)
                else {}
            )
            if not isinstance(snet_cfg, dict):
                snet_cfg = {}
            snet_config_enabled = bool(snet_cfg.get("enabled", False))
            try:
                runtime_query = getattr(
                    self.plugin.disaster_service, "source_runtime_query", None
                )
                if runtime_query is not None:
                    snet_enabled = bool(runtime_query.is_source_enabled("snet_msil"))
                else:
                    snet_enabled = snet_config_enabled
            except Exception:
                snet_enabled = snet_config_enabled
            snet_poll_running = bool(
                snet_poll is not None and getattr(snet_poll, "running", False)
            )
            if not snet_enabled:
                snet_state_text = "⚪ 未启用"
            elif snet_poll_running:
                snet_state_text = "🟢 轮询中"
            else:
                snet_state_text = "🔴 未启动"

            # 解析 FAN Studio 服务器信息
            fan_server_info = status.get("fan_server_info", {})

            # 1. 总体概览行（active/total 已由 status 服务计入 EQSC + S-Net）
            overview_lines = [
                "📊 灾害预警服务状态",
                "",
                f"🔧 插件版本：{plugin_version}",
                f"🔄 运行状态：{running_state} (已运行 {uptime})",
                f"🔗 活跃连接：{status['active_websocket_connections']} / {status['total_connections']}",
            ]

            # 2. 连接状态详情行
            connection_lines = ["📡 连接详情"]
            conn_details = status.get("connection_details", {})
            # 从 connections 视图提取各连接分组的配置启用状态，
            # 用于区分"未启用 / 异常 / 正常"三种状态，避免将未启用的
            # 连接（如 FAN Studio 烈度速报）误显示为"异常"。
            connections_view = status.get("connections", {})
            group_enabled_map: dict[str, bool] = {}
            for conn_info in connections_view.values():
                if isinstance(conn_info, dict):
                    gk = str(conn_info.get("group_key") or "")
                    if gk:
                        group_enabled_map[gk] = bool(conn_info.get("enabled", False))

            for conn_name, display_name in connection_label_map.items():
                detail = conn_details.get(conn_name, {})
                connected = bool(detail.get("connected", False))
                is_enabled = group_enabled_map.get(conn_name, False)
                if not is_enabled:
                    state_text = "⚪ 未启用"
                elif connected:
                    state_text = "🟢 正常"
                else:
                    state_text = "🔴 异常"
                # FAN Studio 连接附加主备服务器信息
                server_label = fan_server_info.get(conn_name)
                if server_label:
                    state_text += f"（{server_label}）"
                connection_lines.append(f"• {display_name}：{state_text}")

            connection_lines.append(f"• EQSC API：{eqsc_state_text}")
            connection_lines.append(f"• NIED S-Net：{snet_state_text}")

            # 3. 各子数据源的细化开关状况行
            data_source_lines = ["📚 子数据源启用状况"]
            active_sources = status.get("data_sources", [])
            grouped_sources: dict[str, list[str]] = {}
            for source in active_sources:
                service_name, _, source_name = source.partition(".")
                grouped_sources.setdefault(service_name, [])
                if source_name:
                    grouped_sources[service_name].append(source_name)

            sub_source_status = dict(status.get("sub_source_status", {}) or {})
            # EQSC / S-Net 为 HTTP 通道，SOURCE_CATALOG 已注册其子源，
            # build_sub_source_status() 会按 config_key 正确归组，
            # 无需再硬编码注入，避免 key 名与 grouped_sources 漂移导致计数错误。

            for service_name, display_name in source_group_label_map.items():
                if (
                    service_name not in grouped_sources
                    and service_name not in sub_source_status
                ):
                    continue

                # 分组之间增加空行，提升可读性
                if len(data_source_lines) > 1:
                    data_source_lines.append("")

                raw_sources = grouped_sources.get(service_name, [])
                group_status = sub_source_status.get(service_name, {})
                if raw_sources:
                    enabled_count = len(raw_sources)
                    total_count = 0
                    if isinstance(group_status, dict) and group_status:
                        total_count = len(group_status)
                    suffix = (
                        f"（已启用 {enabled_count}/{total_count}）"
                        if total_count > 0
                        else f"（已启用 {enabled_count} 项）"
                    )
                    data_source_lines.append(f"• {display_name}{suffix}")
                elif isinstance(group_status, dict) and group_status:
                    # 组未计入 active_sources 时，仍按子源开关汇总（避免未启用却显示「已启用」）
                    enabled_count = sum(1 for v in group_status.values() if bool(v))
                    total_count = len(group_status)
                    data_source_lines.append(
                        f"• {display_name}（已启用 {enabled_count}/{total_count}）"
                    )
                else:
                    data_source_lines.append(f"• {display_name}：已启用")
                if isinstance(group_status, dict) and group_status:
                    sorted_items = sorted(
                        group_status.items(),
                        key=lambda item: (
                            not bool(item[1]),
                            _map_sub_source_name(display_name, item[0]),
                        ),
                    )
                    for raw_key, enabled in sorted_items:
                        sub_name = _map_sub_source_name(display_name, raw_key)
                        state_icon = "🟢" if enabled else "⚪"
                        data_source_lines.append(f"  {state_icon} {sub_name}")

            nodes = _build_forward_nodes(
                [
                    "\n".join(overview_lines),
                    "\n".join(connection_lines),
                    "\n".join(data_source_lines),
                ]
            )
            if nodes:
                await self._track_command_feature(
                    "command_admin_action",
                    {
                        "action": "status_query",
                        "success": True,
                        "running": bool(status.get("running")),
                    },
                )
                yield event.chain_result([nodes])
                return

            await self._track_command_feature(
                "command_admin_action",
                {
                    "action": "status_query",
                    "success": True,
                    "running": bool(status.get("running")),
                },
            )
            yield quoted_plain_result(self.plugin, event, "\n".join(overview_lines))
        except Exception as e:
            logger.error(f"[灾害预警] 获取服务状态失败: {e}")
            yield quoted_plain_result(
                self.plugin, event, f"❌ 获取服务状态失败: {str(e)}"
            )

    async def handle_disaster_stats(self, event):
        """处理统计详情命令，聚合展示本地内存中的去重与过滤指标。"""

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        if not self.plugin.disaster_service:
            yield _quoted_plain_result("❌ 灾害预警服务未启动")
            return

        try:
            status = self.plugin.disaster_service.get_service_status()
            stats_summary = status.get("statistics_summary", "❌ 暂无统计数据")
            if (
                self.plugin.disaster_service
                and self.plugin.disaster_service.message_logger
            ):
                filter_stats = self.plugin.disaster_service.message_logger.filter_stats
                if filter_stats and filter_stats["total_filtered"] > 0:
                    stats_summary += "\n\n🛡️ 日志过滤拦截统计:\n"
                    stats_summary += f"• 重复数据拦截: {filter_stats.get('duplicate_events_filtered', 0)}\n"
                    stats_summary += (
                        f"• 心跳包过滤: {filter_stats.get('heartbeat_filtered', 0)}\n"
                    )
                    stats_summary += (
                        f"• P2P节点状态: {filter_stats.get('p2p_areas_filtered', 0)}\n"
                    )
                    stats_summary += f"• 连接状态过滤: {filter_stats.get('connection_status_filtered', 0)}\n"
                    stats_summary += (
                        f"📊 总计拦截: {filter_stats.get('total_filtered', 0)}"
                    )
            await self._track_command_feature(
                "command_admin_action",
                {"action": "stats_query", "success": True},
            )
            # 统计报告显式走合并转发，失败则回退普通引用回复
            ok = await send_forward_blocks(
                self.plugin, event, [stats_summary], name="灾害预警"
            )
            if not ok:
                yield _quoted_plain_result(stats_summary)
        except Exception as e:
            logger.error(f"[灾害预警] 获取统计信息失败: {e}")
            yield _quoted_plain_result(f"❌ 获取统计信息失败: {str(e)}")

    async def handle_disaster_logs(self, event):
        """查看原始日志记录文件的体积、条目数与起止时间（需管理员权限）。"""
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if (
            not self.plugin.disaster_service
            or not self.plugin.disaster_service.message_logger
        ):
            yield event.plain_result("❌ 日志功能不可用")
            return

        try:
            log_summary = self.plugin.disaster_service.message_logger.get_log_summary()
            if not log_summary["enabled"]:
                yield event.plain_result(
                    "📋 原始消息日志功能未启用\n\n使用 /灾害预警日志开关 启用日志记录"
                )
                return

            if not log_summary["log_exists"]:
                yield event.plain_result(
                    "📋 暂无日志记录\n\n当日志功能启用后，所有接收到的原始消息将被记录。"
                )
                return

            usage_percent = log_summary.get("usage_percent", 0)
            max_capacity = log_summary.get("max_total_capacity_mb", 0)
            file_count = log_summary.get("file_count", 1)
            bar_length = 15
            filled_length = int(bar_length * usage_percent / 100)
            filled_length = max(0, min(filled_length, bar_length))
            bar = "█" * filled_length + "░" * (bar_length - filled_length)

            status_icon = "🟢"
            if usage_percent > 90:
                status_icon = "🔴"
            elif usage_percent > 70:
                status_icon = "🟡"

            log_info = f"""📊 原始消息日志统计

📁 文件路径：{log_summary["log_file"]}
📄 文件数量：{file_count}
📈 总条目数：{log_summary["total_entries"]}
📦 占用空间：{log_summary.get("file_size_mb", 0):.2f} MB / {max_capacity:.0f} MB
💾 存储占用：{bar} {usage_percent:.1f}% {status_icon}
📅 时间范围：{log_summary["date_range"]["start"]} 至 {log_summary["date_range"]["end"]}

📡 数据源统计："""
            for source in log_summary["data_sources"]:
                log_info += f"\n  • {source}"
            log_info += "\n\n💡 提示：使用 /灾害预警日志开关 可以关闭日志记录"
            yield event.plain_result(log_info)
        except Exception as e:
            logger.error(f"[灾害预警] 获取日志信息失败: {e}")
            yield event.plain_result(f"❌ 获取日志信息失败: {str(e)}")

    async def handle_toggle_message_logging(self, event):
        """开启或关闭原始 WebSocket 日志记录器，切换运行配置。"""
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if (
            not self.plugin.disaster_service
            or not self.plugin.disaster_service.message_logger
        ):
            yield event.plain_result("❌ 日志功能不可用")
            return

        try:
            current_state = self.plugin.disaster_service.message_logger.enabled
            new_state = not current_state
            self.plugin.config["debug_config"]["enable_raw_message_logging"] = new_state
            self.plugin.disaster_service.message_logger.enabled = new_state
            self.plugin.config.save_config()

            status = "启用" if new_state else "禁用"
            action = "开始" if new_state else "停止"
            await self._track_command_feature(
                "command_admin_action",
                {
                    "action": "toggle_raw_logging",
                    "success": True,
                    "enabled": bool(new_state),
                },
            )
            yield event.plain_result(
                f"✅ 原始消息日志记录已{status}\n\n插件将{action}记录所有数据源的原始消息格式。"
            )
        except Exception as e:
            logger.error(f"[灾害预警] 切换日志状态失败: {e}")
            yield event.plain_result(f"❌ 切换日志状态失败: {str(e)}")

    async def handle_clear_message_logs(self, event):
        """清空本地生成的原始 JSON 消息日志文件。"""
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if (
            not self.plugin.disaster_service
            or not self.plugin.disaster_service.message_logger
        ):
            yield event.plain_result("❌ 日志功能不可用")
            return

        try:
            self.plugin.disaster_service.message_logger.clear_logs()
            yield event.plain_result(
                "✅ 所有原始消息日志已清除\n\n日志文件已被删除，新的消息记录将重新开始。"
            )
        except Exception as e:
            logger.error(f"[灾害预警] 清除日志失败: {e}")
            yield event.plain_result(f"❌ 清除日志失败: {str(e)}")

    async def handle_clear_statistics(self, event):
        """重置本地 SQLite 数据库与统计 JSON 快照（需管理员权限）。"""
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if (
            not self.plugin.disaster_service
            or not self.plugin.disaster_service.statistics_manager
        ):
            yield event.plain_result("❌ 统计功能不可用")
            return

        try:
            await self.plugin.disaster_service.statistics_manager.reset_stats()
            await self._track_command_feature(
                "command_admin_action",
                {"action": "clear_statistics", "success": True},
            )
            yield event.plain_result(
                "✅ 统计数据已重置\n\n所有历史统计记录已被清除，新的统计将重新开始。"
            )
        except Exception as e:
            logger.error(f"[灾害预警] 清除统计失败: {e}")
            yield event.plain_result(f"❌ 清除统计失败: {str(e)}")

    async def handle_toggle_push(self, event):
        """快速切换当前会话的推送名单启用状态。"""
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        try:
            session_umo = event.unified_msg_origin
            if not session_umo:
                yield event.plain_result("❌ 无法获取当前会话的 UMO")
                return

            # 统一使用 私聊/群聊 ID (备注名) 格式展示
            session_log_str = self._get_session_log_str(session_umo)

            target_sessions = self.plugin.config.get("target_sessions", [])
            if target_sessions is None:
                target_sessions = []

            if session_umo in target_sessions:
                target_sessions.remove(session_umo)
                self.plugin.config["target_sessions"] = target_sessions
                self.plugin.config.save_config()
                await self._track_command_feature(
                    "command_admin_action",
                    {
                        "action": "toggle_push",
                        "success": True,
                        "enabled": False,
                        "target_session_count": len(target_sessions),
                    },
                )
                yield event.plain_result(
                    f"✅ 推送已关闭\n\n{session_log_str} 已从推送列表中移除。"
                )
                logger.info(f"[灾害预警] {session_log_str} 已关闭推送")
            else:
                target_sessions.append(session_umo)
                self.plugin.config["target_sessions"] = target_sessions
                self.plugin.config.save_config()
                await self._track_command_feature(
                    "command_admin_action",
                    {
                        "action": "toggle_push",
                        "success": True,
                        "enabled": True,
                        "target_session_count": len(target_sessions),
                    },
                )
                yield event.plain_result(
                    f"✅ 推送已开启\n\n{session_log_str} 已添加到推送列表。"
                )
                logger.info(f"[灾害预警] {session_log_str} 已开启推送")
        except Exception as e:
            logger.error(f"[灾害预警] 切换推送状态失败: {e}")
            yield event.plain_result(f"❌ 切换推送状态失败: {str(e)}")

    async def handle_disaster_config(
        self, event, action: str = None, target: str = None
    ):
        """查看指定会话的覆写配置与合并后生效配置（需管理员权限）。"""
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if action != "查看":
            yield event.plain_result(
                "❓ 请使用格式：\n"
                "• /灾害预警配置 查看\n"
                "• /灾害预警配置 查看 全局\n"
                "• /灾害预警配置 查看 当前\n"
                "• /灾害预警配置 查看 <会话UMO>"
            )
            return

        try:
            schema = self.plugin._command_support_service.get_config_schema()
            target_mode = (target or "全局").strip()
            if target_mode.lower() == "global":
                target_mode = "全局"

            if target_mode == "全局":
                config_data = dict(self.plugin.config)
                translated_config = (
                    self.plugin._command_support_service.translate_config_recursive(
                        config_data, schema
                    )
                )
                config_str = json.dumps(translated_config, indent=2, ensure_ascii=False)
                await send_forward_blocks(
                    self.plugin, event, [config_str], name="灾害预警"
                )
                return

            session_umo = (
                event.unified_msg_origin
                if target_mode in ["当前", "本会话", "this", "current"]
                else target_mode
            )
            if not session_umo:
                yield event.plain_result("❌ 无法解析目标会话 UMO")
                return

            if not self.plugin.disaster_service or not hasattr(
                self.plugin.disaster_service, "session_config_manager"
            ):
                yield event.plain_result("❌ 会话配置管理器不可用")
                return

            mgr = self.plugin.disaster_service.session_config_manager
            override = mgr.get_override(session_umo)
            effective = mgr.get_effective_config(session_umo)
            session_log_str = mgr.get_session_log_str(session_umo)
            translated_override = (
                self.plugin._command_support_service.translate_config_recursive(
                    override, schema
                )
            )
            translated_effective = (
                self.plugin._command_support_service.translate_config_recursive(
                    effective, schema
                )
            )

            override_str = json.dumps(translated_override, indent=2, ensure_ascii=False)
            effective_str = json.dumps(
                translated_effective, indent=2, ensure_ascii=False
            )
            await send_forward_blocks(
                self.plugin,
                event,
                [
                    f"📌 差异覆写 (override)：\n{override_str}",
                    f"📘 合并后配置 (effective)：\n{effective_str}",
                ],
                header=f"🔧 会话配置详情 ({session_log_str})",
                name="灾害预警",
            )
        except Exception as e:
            logger.error(f"[灾害预警] 获取配置详情失败: {e}")
            yield event.plain_result(f"❌ 获取配置详情失败: {str(e)}")

    async def handle_server_switch(
        self, event, data_source: str = None, preference: str = None
    ):
        """处理 /服务器切换 指令。"""
        # 管理类命令统一在入口先做管理员校验（与同文件其余管理命令保持一致）
        if not await self.plugin.is_plugin_admin(event):
            yield event.plain_result("🚫 权限不足：此命令仅限管理员使用。")
            return

        if not self.plugin.disaster_service:
            yield event.plain_result("❌ 灾害预警服务未启动")
            return

        # 无参数时展示当前服务器状态
        if not data_source:
            status = self.plugin.disaster_service.get_service_status()
            fan_info = status.get("fan_server_info", {})
            lines = [
                "🖥️ 数据源服务器状态",
                "",
            ]
            for conn_name, display_name in [
                ("fan_studio_all", "FAN Studio"),
                ("fan_studio_cenc_ir", "FAN Studio（烈度速报）"),
            ]:
                label = fan_info.get(conn_name, "未知")
                lines.append(f"  • {display_name}：{label}")
            lines.append("")
            lines.append("📌 用法：/服务器切换 <数据源> <主服务器|备用服务器>")
            lines.append("💡 示例：/服务器切换 FAN 主服务器")
            yield event.plain_result("\n".join(lines))
            return

        # 如果 data_source 实际上是 preference（兼容单参数用法）
        data_source_lower = str(data_source or "").strip().lower()
        pref_candidates = ("主服务器", "备用服务器", "主", "备")
        if data_source_lower in {c.lower() for c in pref_candidates}:
            source = "FAN"
            raw_pref = str(data_source).strip()
        else:
            source = str(data_source).strip()
            raw_pref = str(preference or "").strip()

        # 规范化偏好值
        raw_pref_lower = raw_pref.lower()
        if raw_pref_lower in ("主服务器", "主"):
            preference_full = "主服务器优先"
            preference_short = "主服务器"
        elif raw_pref_lower in ("备用服务器", "备"):
            preference_full = "备用服务器优先"
            preference_short = "备用服务器"
        else:
            yield event.plain_result(
                "❌ 无效参数。\n\n"
                "📌 用法：/服务器切换 <数据源> <主服务器|备用服务器>\n"
                "💡 示例：/服务器切换 FAN 主服务器\n"
                "  或：/服务器切换 FAN 备用服务器"
            )
            return

        # 判断数据源
        source_lower = source.lower()
        if source_lower not in ("fan", "fan studio", "fan_studio", "fanstudio"):
            yield event.plain_result(f"❌ 暂不支持数据源「{source}」的服务器切换。")
            return

        yield event.plain_result(f"🔄 正在切换 FAN Studio 至 {preference_short}...")

        try:
            results = await self.plugin.disaster_service.switch_fan_server_preference(
                preference_full
            )
            lines = ["🖥️ FAN Studio 服务器切换结果："]
            for conn_name, status in results.items():
                display_name = self._resolve_fan_conn_display(conn_name)
                if "失败" in status:
                    lines.append(f"  ❌ {display_name}：{status}")
                else:
                    # 只保留首个 ✅，去掉结果中的重复 emoji
                    clean_status = status.replace("✅ ", "")
                    lines.append(f"  ✅ {display_name}：{clean_status}")
            yield event.plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"[灾害预警] 切换 FAN Studio 服务器失败: {e}")
            yield event.plain_result(f"❌ 切换失败: {str(e)}")

    @staticmethod
    def _resolve_fan_conn_display(conn_name: str) -> str:
        """解析 FAN 连接展示名。"""
        names = {
            "fan_studio_all": "FAN Studio",
            "fan_studio_cenc_ir": "FAN Studio（烈度速报）",
        }
        return names.get(conn_name, conn_name)
