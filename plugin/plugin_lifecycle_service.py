"""
插件运行时与遥测生命周期服务。
负责初始化期的配置修正、遥测注入、异常处理器安装、心跳任务管理与终止清理，
减少主插件类中的生命周期细节堆积。
"""

from __future__ import annotations

import asyncio
import copy
import re
import time

from astrbot.api import logger

from ..core.app.disaster_service import stop_disaster_service
from ..core.services.config.config_validation_service import ConfigValidator
from ..core.services.telemetry.telemetry_service import TelemetryManager
from ..utils.banner import print_stop_summary
from ..utils.geolocation import close_geoip_session
from ..utils.version import get_plugin_version


class PluginLifecycleService:
    """插件生命周期编排服务。"""

    def __init__(self, plugin):
        """初始化插件生命周期服务。"""
        self.plugin = plugin

    def sync_admin_users_from_global(self) -> None:
        """从 AstrBot 全局管理员配置自动同步管理员身份到插件配置中。"""
        config = self.plugin.config
        # 仅当插件未显式配置 admin_users 时，才回退同步 AstrBot 全局管理员，避免覆盖用户自定义设置。
        if "admin_users" not in config or config.get("admin_users") is None:
            global_admins = self.plugin.context.get_config().get("admins_id", [])
            if global_admins:
                config["admin_users"] = list(global_admins)
                config.save_config()
                logger.info(
                    f"[灾害预警] 已自动同步全局管理员到插件配置: {global_admins}"
                )

    def validate_and_fix_config(self) -> None:
        """调用 ConfigValidator 对配置文件进行校验与自动类型纠偏。"""
        try:
            # 校验时先复制一份配置快照，避免在校验过程中直接污染运行中的原始配置对象。
            config_copy = copy.deepcopy(dict(self.plugin.config))
            validated_config = ConfigValidator.validate(config_copy)
            config_changed = False
            for key, value in validated_config.items():
                # 仅在校验结果与当前值不一致时才写回，减少无意义保存。
                if self.plugin.config.get(key) != value:
                    self.plugin.config[key] = value
                    config_changed = True
            if config_changed:
                self.plugin.config.save_config()
                logger.info("[灾害预警] 配置已自动修正并保存")
        except Exception as e:
            logger.error(f"[灾害预警] 配置校验失败: {e}")

    def setup_telemetry(self) -> None:
        """初始化并注入遥测上报管理器。"""
        # 遥测初始化与主服务解耦，便于在生命周期阶段统一注入和关闭。
        self.plugin.telemetry = TelemetryManager(
            config=dict(self.plugin.config),
            plugin_version=get_plugin_version(),
        )
        if self.plugin.disaster_service:
            self.plugin.disaster_service.set_telemetry(self.plugin.telemetry)

    def install_asyncio_exception_handler(self) -> None:
        """为事件循环注册未处理异步异常处理器，将插件后台任务错误上报到遥测服务器。"""
        if not self.plugin.telemetry or not self.plugin.telemetry.enabled:
            return
        loop = asyncio.get_running_loop()
        # 先保存原异常处理器，停机时再恢复，避免影响宿主其他模块。
        self.plugin._original_exception_handler = loop.get_exception_handler()
        loop.set_exception_handler(self.handle_asyncio_exception)
        logger.debug("[灾害预警] 已设置全局异常处理器")

    def start_telemetry_tasks(self) -> None:
        """启动阶段上报启动与配置遥测事件，并初始化心跳循环。"""
        if not self.plugin.telemetry or not self.plugin.telemetry.enabled:
            return

        self.plugin._start_time = time.monotonic()
        # 启动阶段同时上报启动事件与当前配置快照，并统一纳入遥测任务集合管理。
        startup_task = asyncio.create_task(self.plugin.telemetry.track_startup())
        config_task = asyncio.create_task(
            self.plugin.telemetry.track_config(dict(self.plugin.config))
        )
        self.plugin._telemetry_tasks.add(startup_task)
        self.plugin._telemetry_tasks.add(config_task)
        startup_task.add_done_callback(self.plugin._telemetry_tasks.discard)
        config_task.add_done_callback(self.plugin._telemetry_tasks.discard)

        self.plugin._heartbeat_task = asyncio.create_task(self.heartbeat_loop())
        logger.debug("[灾害预警] 已启动遥测心跳任务 (间隔: 12小时)")

    async def cleanup_telemetry_tasks(self) -> None:
        """等待并回收未完成的后台遥测任务协程。"""
        if not self.plugin._telemetry_tasks:
            return
        pending_tasks = list(self.plugin._telemetry_tasks)
        # 停机时先取消未完成任务，再集中等待回收，避免遗留后台协程。
        for task in pending_tasks:
            if not task.done():
                task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        self.plugin._telemetry_tasks.clear()

    async def stop_heartbeat_task(self) -> None:
        """停止遥测心跳任务定时循环。"""
        if self.plugin._heartbeat_task:
            self.plugin._heartbeat_task.cancel()
            try:
                await self.plugin._heartbeat_task
            except asyncio.CancelledError:
                pass
            logger.debug("[灾害预警] 已停止心跳任务")

    def restore_asyncio_exception_handler(self) -> None:
        """卸载未处理异步异常处理器，还原原有事件循环异常处理器。"""
        if self.plugin._original_exception_handler is not None:
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(self.plugin._original_exception_handler)
            self.plugin._original_exception_handler = None
            logger.debug("[灾害预警] 已恢复全局异常处理器")

    async def shutdown_plugin_resources(self) -> None:
        """在插件卸载/关闭时，依次安全终止后台任务、连接会话与资源引用。"""
        # 插件停机时按“服务任务 -> 主服务 -> 子资源”顺序清理，尽量降低悬挂任务与残留连接风险。
        if self.plugin._service_task:
            self.plugin._service_task.cancel()
            try:
                await self.plugin._service_task
            except asyncio.CancelledError:
                pass

        await stop_disaster_service()

        if (
            self.plugin.disaster_service
            and self.plugin.disaster_service.message_manager
        ):
            if hasattr(self.plugin.disaster_service.message_manager, "browser_manager"):
                # 浏览器资源通常最重，优先回收，避免宿主退出后仍残留外部进程。
                try:
                    await self.plugin.disaster_service.message_manager.cleanup_browser()
                except Exception as be:
                    logger.debug(f"[灾害预警] 浏览器清理时出错（已忽略）: {be}")
            try:
                # 消息管理器内部的气象筛选器可能持有网络会话，需要显式关闭。
                await (
                    self.plugin.disaster_service.message_manager.weather_filter.close()
                )
            except Exception as wfe:
                logger.debug(
                    f"[灾害预警] 气象过滤器 session 关闭时出错（已忽略）: {wfe}"
                )

        if (
            self.plugin.disaster_service
            and self.plugin.disaster_service.statistics_manager
        ):
            try:
                # 统计模块中的地区解析器也可能缓存网络会话，停机时一并回收。
                await self.plugin.disaster_service.statistics_manager._weather_region_resolver.close()
            except Exception as wfe:
                logger.debug(
                    f"[灾害预警] 统计模块气象 session 关闭时出错（已忽略）: {wfe}"
                )

        # 气象站查询服务（NMC + FAN 客户端）若已懒加载过，需显式关闭其 aiohttp 会话，
        # 避免插件重载时残留未关闭连接导致会话泄漏。
        weather_station_service = getattr(
            self.plugin, "_weather_station_query_service", None
        )
        if weather_station_service is not None:
            try:
                await weather_station_service.close()
            except Exception as wse:
                logger.debug(
                    f"[灾害预警] 气象站查询服务会话关闭时出错（已忽略）: {wse}"
                )
            finally:
                # 会话已关闭，清除懒加载引用，避免停机后残留已失效的服务实例
                self.plugin._weather_station_query_service = None

        # 降水量预报客户端若已懒加载过，需显式关闭其 aiohttp 会话，
        # 避免插件重载时残留未关闭连接导致会话泄漏。
        precipitation_client = getattr(self.plugin, "_precipitation_client", None)
        if precipitation_client is not None:
            try:
                await precipitation_client.close()
            except Exception as pce:
                logger.debug(
                    f"[灾害预警] 降水量预报客户端会话关闭时出错（已忽略）: {pce}"
                )
            finally:
                # 会话已关闭，清除懒加载引用，避免停机后残留已失效的客户端实例
                self.plugin._precipitation_client = None

        if self.plugin.telemetry:
            try:
                await self.plugin.telemetry.close()
            except Exception as te:
                logger.debug(f"[灾害预警] 遥测会话关闭时出错（已忽略）: {te}")

        if self.plugin.web_server:
            # 最后停止管理端 Web 服务器，避免外部仍尝试进行网络交互
            await self.plugin.web_server.stop()

        # 兜底关闭 GeoIP 共享会话：close_geoip_session() 目前由 web_server.stop() 触发，
        # 但为避免 web_admin 未启用或未来其他路径使用 GeoIP 时残留模块级会话，此处再兜底一次。
        try:
            await close_geoip_session()
        except Exception as geoip_err:
            logger.debug(f"[灾害预警] 关闭 GeoIP 会话时出错（已忽略）: {geoip_err}")

        # 所有资源（含浏览器、后台延迟检测与 Web 管理端）均已完成回收后，
        # 才打印停止汇总大屏，确保面板上的回收状态与实际运行态一致。
        # 该大屏原先在 DisasterServiceLifecycle.stop() 内打印，彼时浏览器与
        # Web 管理端尚未回收（由本方法在 stop() 之后执行），会显示 ⚠️ 未完成。
        try:
            print_stop_summary(self.plugin.disaster_service)
        except Exception as banner_err:
            logger.debug(f"[灾害预警] 停止汇总大屏打印失败（已忽略）: {banner_err}")

    def handle_asyncio_exception(self, loop, context) -> None:
        """事件循环未处理异步异常拦截入口，判断来源若为本插件则执行遥测收集上报。"""
        exception = context.get("exception")
        message = context.get("message", "未知异常")

        is_plugin_exception = False
        # 只接管本插件抛出的异步异常，其他异常继续交还宿主原处理器。
        if exception:
            tb = exception.__traceback__
            while tb is not None:
                frame = tb.tb_frame
                filename = frame.f_code.co_filename
                if "astrbot_plugin_disaster_warning" in filename:
                    is_plugin_exception = True
                    break
                tb = tb.tb_next

        if not is_plugin_exception:
            if self.plugin._original_exception_handler:
                self.plugin._original_exception_handler(loop, context)
            else:
                loop.default_exception_handler(context)
            return

        if exception:
            logger.error(f"[灾害预警] 捕获未处理的异步异常: {exception}")
            logger.error(f"[灾害预警] 异常上下文: {message}")
        else:
            logger.error(f"[灾害预警] 捕获未处理的异步错误: {message}")

        if self.plugin.telemetry and self.plugin.telemetry.enabled:
            # 仅在遥测启用时补充异常上报，避免在禁用状态下继续创建额外任务。
            if exception:
                task = context.get("future")
                # 尽量提取任务名称，便于后续在遥测中定位是哪一类后台任务出了问题。
                task_name = "unknown"
                if task:
                    task_name = getattr(task, "get_name", lambda: str(task))()
                    if not task_name or task_name == str(task):
                        task_repr = repr(task)
                        if "name=" in task_repr:
                            match = re.search(r"name='([^']+)'", task_repr)
                            if match:
                                task_name = match.group(1)
                error_task = asyncio.create_task(
                    self.plugin.telemetry.track_error(
                        exception,
                        module=f"main.unhandled_async.{task_name}",
                    )
                )
            else:
                runtime_error = RuntimeError(message)
                error_task = asyncio.create_task(
                    self.plugin.telemetry.track_error(
                        runtime_error,
                        module="main.unhandled_async",
                    )
                )
            self.plugin._telemetry_tasks.add(error_task)
            error_task.add_done_callback(self.plugin._telemetry_tasks.discard)

    async def heartbeat_loop(self) -> None:
        """遥测心跳定时上报循环。"""
        heartbeat_interval = 43200
        # 心跳间隔固定为 12 小时，用于上报插件长期运行状态。
        try:
            while True:
                if not self.plugin.telemetry or not self.plugin.telemetry.enabled:
                    logger.debug("[灾害预警] 遥测已禁用，跳过心跳发送")
                    await asyncio.sleep(heartbeat_interval)
                    continue

                uptime = time.monotonic() - self.plugin._start_time
                try:
                    await self.plugin.telemetry.track_heartbeat(uptime_seconds=uptime)
                    logger.debug(
                        f"[灾害预警] 心跳数据已发送 (运行时长: {uptime:.0f}秒)"
                    )
                except Exception as e:
                    logger.debug(f"[灾害预警] 心跳发送失败: {e}")

                await asyncio.sleep(heartbeat_interval)
        except asyncio.CancelledError:
            logger.debug("[灾害预警] 心跳任务已取消")
            raise
        except Exception as e:
            logger.error(f"[灾害预警] 心跳循环异常: {e}")
