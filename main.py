import asyncio
import time
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from .core.app.disaster_service import get_disaster_service
from .core.app.runtime.boot_marker import (
    is_first_boot_in_process,
    mark_astrbot_loaded,
)
from .core.network.admin.host.web_server import WebAdminServer
from .core.services.telemetry.telemetry_service import TelemetryManager
from .core.services.telemetry.telemetry_utils import track_error_safely
from .plugin.commands.forward_helper import send_forward_blocks
from .plugin.commands.plugin_admin_command_service import PluginAdminCommandService
from .plugin.commands.plugin_query_command_service import PluginQueryCommandService
from .plugin.plugin_command_support_service import PluginCommandSupportService
from .plugin.plugin_lifecycle_service import PluginLifecycleService
from .utils.banner import print_banner
from .utils.plugin_logger import plugin_logger


class DisasterWarningPlugin(Star):
    """多数据源灾害预警插件，支持地震、海啸、气象预警"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        # main.py 现在主要承担 AstrBot 插件入口壳职责，
        # 具体生命周期和命令实现分别下沉到 plugin/ 子服务中。
        self.config: AstrBotConfig = config
        self.disaster_service: Any = None  # DisasterService 类型，避免循环导入
        self._service_task: asyncio.Task[None] | None = None
        self.telemetry: TelemetryManager | None = None
        self._config_schema: dict[str, Any] | None = None  # JSON Schema 缓存
        self._original_exception_handler: Any = None  # asyncio 异常处理器
        self._telemetry_tasks: set[asyncio.Task[None]] = set()  # 遥测任务引用集合
        self._heartbeat_task: asyncio.Task[None] | None = None  # 心跳定时任务
        self._start_time: float = 0.0  # 插件启动时间
        self.web_server = None
        self._lifecycle_service = PluginLifecycleService(self)
        self._command_support_service = PluginCommandSupportService(self)
        self._admin_command_service = PluginAdminCommandService(self)
        self._query_command_service = PluginQueryCommandService(self)

    async def initialize(self):
        """初始化插件"""
        try:
            # 插件一重载即打印组织 ASCII art 横幅（bold_cyan 配色，终端不支持颜色时回退纯文本）。
            print_banner()

            plugin_logger.set_config(self.config)

            # 初始化期先处理配置与管理员同步，再装配 disaster_service / telemetry / web_admin。
            self._lifecycle_service.sync_admin_users_from_global()
            self._lifecycle_service.validate_and_fix_config()

            # 检查插件是否启用
            if not self.config.get("enabled", True):
                logger.info("[灾害预警] 插件已禁用，跳过初始化")
                return

            # 获取灾害预警服务
            self.disaster_service = await get_disaster_service(
                self.config, self.context
            )

            # 区分首次启动/进程重启与插件重载：
            # - 首次启动/进程重启：AstrBot 尚未加载完成，推迟静默武装，
            #   等 on_astrbot_loaded 钩子触发后再武装，避免硬超时被加载耗时耗尽；
            # - 插件重载：AstrBot 已就绪，立即武装（仍保留 30 秒兜底）。
            first_boot = is_first_boot_in_process()
            if first_boot:
                logger.info(
                    "[灾害预警] 检测到 AstrBot 首次启动/进程重启，"
                    "静默启动将等待 AstrBot 加载完成钩子触发"
                )
            # 启动服务使用后台 task 承载，这样插件 initialize() 不会长期阻塞 AstrBot 的启动流程。
            self._service_task = asyncio.create_task(
                self.disaster_service.start(defer_silence_arm=first_boot)
            )

            # 遥测相关初始化放在 disaster_service 创建之后，确保能把 telemetry 引用回注到服务层。
            self._lifecycle_service.setup_telemetry()
            self._lifecycle_service.install_asyncio_exception_handler()
            self._lifecycle_service.start_telemetry_tasks()

            if self.config.get("web_admin", {}).get("enabled", False):
                self.web_server = WebAdminServer(self.disaster_service, self.config)
                # 注入引用以支持事件驱动的实时推送
                self.disaster_service.web_admin_server = self.web_server
                await self.web_server.start()

        except Exception as e:
            logger.error(f"[灾害预警] 插件初始化失败: {e}")
            # 上报初始化失败错误到遥测。
            # 若异常已内部上报过（内部上报点会在异常对象上设置 _telemetry_reported 标记），
            # 此处跳过，避免同一异常产生两条遥测记录。
            # 使用显式标记而非遍历 traceback 识别内部上报，避免对函数名/文件名产生脆弱依赖。
            if not getattr(e, "_telemetry_reported", False):
                await track_error_safely(
                    self.telemetry,
                    e,
                    module="main.initialize",
                    log_context="初始化错误遥测",
                )

            # 发生异常时，确保清理已启动的任务和资源，防止任务泄露；
            # 初始化失败路径以非 0 退出码上报，避免失败启动被错误记录为成功退出。
            await self.terminate(exit_code=1)
            raise

    async def _cleanup_telemetry_tasks(self) -> None:
        """清理并终止所有未完成的遥测任务，避免任务泄漏"""
        await self._lifecycle_service.cleanup_telemetry_tasks()

    async def terminate(self, exit_code: int = 0):
        """插件销毁时调用。

        Args:
            exit_code: 退出码。正常销毁为 0；初始化失败等异常路径传入非 0，
                避免失败启动被错误记录为成功退出。
        """
        # 上报退出事件（统计实例运行时长与退出码）。
        # 必须在 shutdown_plugin_resources()（内部会关闭遥测会话）之前执行，
        # 否则 track_shutdown 会重新拉起已关闭的发送链路，退出事件大概率丢失。
        # 退出事件单独 try 包裹：任何失败都不阻塞后续资源清理。
        try:
            await self._lifecycle_service.stop_heartbeat_task()
        except Exception as e:
            logger.debug(f"[灾害预警] 停止心跳任务失败（已忽略）: {e}")
        try:
            self._lifecycle_service.restore_asyncio_exception_handler()
        except Exception as e:
            logger.debug(f"[灾害预警] 恢复异常处理器失败（已忽略）: {e}")

        if hasattr(self, "telemetry") and self.telemetry and self.telemetry.enabled:
            try:
                runtime_seconds = time.monotonic() - getattr(
                    self, "_start_time", time.monotonic()
                )
                await self.telemetry.track_shutdown(
                    exit_code=exit_code, runtime_seconds=max(0.0, runtime_seconds)
                )
            except Exception as e:
                logger.debug(f"[灾害预警] 退出事件上报失败（已忽略）: {e}")

        # 清理遥测任务与插件资源各自独立 try：任一步骤失败都不中断后续回收，
        # 确保服务任务、网络会话与 Web 资源在退出遥测异常时仍被清理。
        cleanup_error: Exception | None = None
        try:
            await self._cleanup_telemetry_tasks()
        except Exception as e:
            logger.debug(f"[灾害预警] 清理遥测任务失败（已忽略）: {e}")
        try:
            await self._lifecycle_service.shutdown_plugin_resources()
        except Exception as e:
            logger.error(f"[灾害预警] 插件停止时出错: {e}")
            cleanup_error = e

        # 上报停止错误到遥测（best-effort，遥测自身故障不影响停机流程）
        if (
            cleanup_error is not None
            and hasattr(self, "telemetry")
            and self.telemetry
            and self.telemetry.enabled
        ):
            await track_error_safely(
                self.telemetry,
                cleanup_error,
                module="main.terminate",
                log_context="停机错误遥测",
            )

    def _handle_asyncio_exception(self, loop, context):
        """
        全局 asyncio 异常处理器
        捕获未被处理的 asyncio task 异常并上报到遥测
        """
        self._lifecycle_service.handle_asyncio_exception(loop, context)

    async def _heartbeat_loop(self):
        """心跳循环任务 - 启动时立即发送一次，之后每12小时发送一次"""
        await self._lifecycle_service.heartbeat_loop()

    # ======================================================================
    # 1. 帮助入口
    # ======================================================================

    @filter.command("灾害预警")
    async def disaster_warning_help(self, event: AstrMessageEvent):
        """灾害预警插件帮助"""
        header = (
            "🚨 灾害预警插件使用指南\n"
            "──────────────\n"
            "📌 参数约定：<必填> [可选]\n"
            "💡 输入 /灾害预警 可随时查看本指南\n"
            "📍 各指令详情与完整示例请查阅 README"
        )
        blocks = [
            # 1. 地震速查
            (
                "🌐 地震速查\n"
                "• /地震列表查询 [数据源] [数量] [格式]\n"
                "   数据源：cenc/jma；格式：card/text\n"
                "   例：/地震列表查询 jma 10 card\n"
                "• /地震预警查询（别名 /地震预警）\n"
                "   查询各机构 EEW 状态与无 EEW 计时"
            ),
            # 2. 地震专业分析
            (
                "🔬 地震专业分析\n"
                "• /地震动预测 <纬度> <经度> <震级> <深度> <预测点纬度> <预测点经度> [Vs30]\n"
                "   可引用地震消息自动提取震中参数；Vs30 可选（缺省 600 m/s）\n"
                "• /本地地震动预测 [纬度] [经度]（别名 /本地预测 /卧槽）\n"
                "   引用地震消息按本地监控坐标预测\n"
                "• /JMA震央分布 [开始日期] [结束日期]\n"
                "• /JMA震央分布绘图 [投影] [开始日期] [结束日期]\n"
                "   投影：经度纬度/经度深度/纬度深度/经度时间/纬度时间/深度时间\n"
                "• /snet - NIED 海底震度分布\n"
                "• /生成沙滩球 <走向> <倾角> <滑动角> [大小] [线宽]\n"
                "• /节面解析 <走向> <倾角> <滑动角>"
            ),
            # 3. 气象预警与雷达
            (
                "⚡ 气象预警与雷达\n"
                "• /气象预警查询 <省份|全国> [类型] [颜色] [全部|全日期]\n"
                "   或 /气象预警查询 <预警ID>\n"
                "   默认近 72 小时，全部/全日期查全量历史\n"
                "• /雷达 <站点名>（如 /雷达 北京、/雷达 全国）\n"
                "• /雷达动图 <站点名>\n"
                "• /雷达列表"
            ),
            # 4. 降水量预报
            (
                "🌧️ 降水量预报\n"
                "• /降水量预报 [24h|6h] [时次]\n"
                "   例：/降水量预报 24h、/降水量预报 6h 08时\n"
                "• /降水量预报动图 [24h|6h]\n"
                "   全时次循环动图"
            ),
            # 5. 实况排行
            (
                "📊 实况排行（全国 Top10）\n"
                "• /气温排行 [跨度] [时次]\n"
                "• /最低气温排行 [跨度] [时次]\n"
                "• /降水排行 [跨度] [时次]\n"
                "• /风速排行 [跨度] [时次]\n"
                "   跨度：6小时/24小时；时次：MM月DD日HH时 等\n"
                "   例：/降水排行 6小时 昨天20时"
            ),
            # 6. 气象站与空气质量
            (
                "🏙️ 气象站与空气质量\n"
                "• /气象站实况 <站点代码|站名>（别名 /实况 /气象站）\n"
                "• /气象站历史 <站点> [时次]\n"
                "• /气象站列表 [省份]\n"
                "• /空气质量 <城市|省份|全国> [等级]（别名 /AQI）\n"
                "• /空气质量排行 [最好|最差]\n"
                "• /空气质量列表 [省份]"
            ),
            # 7. 台风信息
            (
                "🌀 台风信息查询（别名 /台风查询 /台风信息）\n"
                "参数任意顺序：台风ID | 名称 | 数量 | 完整|简要 | 活跃\n"
                "• /台风信息查询 - 活跃台风列表\n"
                "• /台风信息查询 2609 完整 - 指定台风完整路径\n"
                "• /台风信息查询 5 活跃 - 最近 5 个活跃台风"
            ),
            # 8. 模拟预警
            (
                "🧪 灾害预警模拟（数据源置末尾，决定灾种）\n"
                "• 地震：/灾害预警模拟 <纬度> <经度> <震级> [深度] [源]\n"
                "• 海啸：/灾害预警模拟 <标题> <等级> <位置> [源震级] [源]\n"
                "• 气象：/灾害预警模拟 <标题> <正文> [预警编码] [源]\n"
                "• 台风：/灾害预警模拟 <编号> <名称> [强度] [源]\n"
                "例：/灾害预警模拟 30.6 103.0 5.2 10 cea_fanstudio"
            ),
            # 9. 运维管理
            (
                "🛠️ 运维管理\n"
                "• /灾害预警状态 - 服务运行状态\n"
                "• /灾害预警重启 - 重载插件\n"
                "• /灾害预警重连 - 强制重连离线数据源\n"
                "• /灾害预警统计 / 灾害预警统计清除\n"
                "• /灾害预警推送开关 - 会话推送开关\n"
                "• /灾害预警配置 查看 [全局|当前|<会话UMO>]\n"
                "• /灾害预警日志 / 日志开关 / 日志清除\n"
                "• /服务器切换 - 查看/切换数据源主备服务器\n"
                "• /重启AstrBot - 重启整个 AstrBot 进程\n"
                "──────────────\n"
                "📚 更多信息请查阅插件 README 文档"
            ),
        ]
        try:
            await send_forward_blocks(
                self,
                event,
                blocks,
                header=header,
                name="灾害预警",
            )
        except Exception as exc:
            # 平台拒绝合并转发或发送暂时失败时，回退为普通文本回复，
            # 保证帮助内容仍能送达用户。
            logger.warning(f"[灾害预警] 帮助命令合并转发失败，回退为普通文本: {exc}")
            fallback_text = header + "\n" + "\n\n".join(blocks)
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain([Comp.Plain(fallback_text)]),
            )

    # ======================================================================
    # 2. 地震类指令
    # ======================================================================

    @filter.command("地震列表查询", alias={"地震列表"})
    async def query_earthquake_list(
        self,
        event: AstrMessageEvent,
        source: str = "cenc",
        count: int = 9,
        mode: str = "card",
    ):
        """查询最新的地震列表"""
        async for result in self._query_command_service.handle_query_earthquake_list(
            event,
            source=source,
            count=count,
            mode=mode,
        ):
            yield result

    @filter.command("地震预警查询", alias={"地震预警"})
    async def query_earthquake_warning(self, event: AstrMessageEvent):
        """查询各机构地震预警（EEW）状态"""
        async for result in self._query_command_service.handle_query_earthquake_warning(
            event
        ):
            yield result

    @filter.command("地震动预测", alias={"地震动"})
    async def ground_motion_predict(
        self,
        event: AstrMessageEvent,
        lat: str = None,
        lon: str = None,
        magnitude: str = None,
        depth: str = None,
        point_lat: str = None,
        point_lon: str = None,
        vs30: str = None,
    ):
        """地震动预测（可引用地震消息自动提取震中参数）"""
        async for result in self._query_command_service.handle_ground_motion_predict(
            event,
            lat_str=lat,
            lon_str=lon,
            mag_str=magnitude,
            depth_str=depth,
            point_lat_str=point_lat,
            point_lon_str=point_lon,
            vs30_str=vs30,
        ):
            yield result

    @filter.command(
        "本地地震动预测",
        alias={"本地预测", "卧槽", "卧槽大大大", "本地地震动"},
    )
    async def local_ground_motion_predict(
        self,
        event: AstrMessageEvent,
        lat: str = None,
        lon: str = None,
    ):
        """本地地震动预测（引用地震消息，按本地监控坐标预测）"""
        async for (
            result
        ) in self._query_command_service.handle_local_ground_motion_predict(
            event, lat_str=lat, lon_str=lon
        ):
            yield result

    @filter.command(
        "JMA震央分布",
        alias={
            "JMA震中分布",
            "JMA震源分布",
            "jma震央分布",
            "jma震中分布",
            "jma震源分布",
        },
    )
    async def query_jma_hypo_list(
        self,
        event: AstrMessageEvent,
        arg1: str = None,
        arg2: str = None,
    ):
        """查询 JMA 震央分布统计（纯文本）"""
        async for result in self._query_command_service.handle_query_jma_hypo_list(
            event,
            arg1=arg1,
            arg2=arg2,
        ):
            yield result

    @filter.command(
        "JMA震央分布绘图",
        alias={
            "JMA震中分布绘图",
            "JMA震源分布绘图",
            "jma震央分布绘图",
            "jma震中分布绘图",
            "jma震源分布绘图",
        },
    )
    async def query_jma_hypo_plot(
        self,
        event: AstrMessageEvent,
        arg1: str = None,
        arg2: str = None,
        arg3: str = None,
    ):
        """绘制 JMA 震央分布图（支持 6 种投影）"""
        async for result in self._query_command_service.handle_query_jma_hypo_plot(
            event,
            arg1=arg1,
            arg2=arg2,
            arg3=arg3,
        ):
            yield result

    @filter.command("snet", alias={"S-Net", "s-net", "Snet", "SNET"})
    async def query_snet(self, event: AstrMessageEvent, arg: str = None):
        """查询 NIED S-Net 海底震度分布（可调试：random/7/6+/...）"""
        async for result in self._query_command_service.handle_query_snet(
            event,
            arg=arg,
        ):
            yield result

    @filter.command("生成沙滩球", alias={"沙滩球", "beachball", "球"})
    async def generate_beachball(
        self,
        event: AstrMessageEvent,
        strike: str,
        dip: str,
        rake: str,
        size: str = None,
        line_width: str = None,
    ):
        """根据走向、倾角、滑动角生成沙滩球图片"""
        async for result in self._query_command_service.handle_generate_beachball(
            event,
            strike=strike,
            dip=dip,
            rake=rake,
            size_str=size,
            line_width_str=line_width,
        ):
            yield result

    @filter.command("节面解析", alias={"节面成分解析"})
    async def parse_nodal_plane(
        self,
        event: AstrMessageEvent,
        strike: str,
        dip: str,
        rake: str,
    ):
        """根据走向、倾角、滑动角解析节面断层破裂分量"""
        async for result in self._query_command_service.handle_parse_nodal_plane(
            event,
            strike=strike,
            dip=dip,
            rake=rake,
        ):
            yield result

    # ======================================================================
    # 3. 气象类指令
    # ======================================================================

    @filter.command("气象预警查询", alias={"气象预警"})
    async def query_weather_alarm(
        self,
        event: AstrMessageEvent,
        keyword: str = None,
        optional_a: str = None,
        optional_b: str = None,
        optional_c: str = None,
    ):
        """气象预警查询（支持 [全部|全日期] 和关闭 72 小时过滤）"""
        async for result in self._query_command_service.handle_query_weather_alarm(
            event,
            keyword=keyword,
            optional_a=optional_a,
            optional_b=optional_b,
            optional_c=optional_c,
        ):
            yield result

    @filter.command("雷达")
    async def radar_image(self, event: AstrMessageEvent, name: str = None):
        """查询最新一帧气象雷达图"""
        async for result in self._query_command_service.handle_query_radar(
            event, name=name
        ):
            yield result

    @filter.command("雷达动图")
    async def radar_gif(self, event: AstrMessageEvent, name: str = None):
        """查询最近多帧合成循环动图"""
        async for result in self._query_command_service.handle_query_radar_gif(
            event, name=name
        ):
            yield result

    @filter.command("雷达列表")
    async def radar_list(self, event: AstrMessageEvent):
        """查看全部气象雷达站点列表"""
        async for result in self._query_command_service.handle_query_radar_list(event):
            yield result

    @filter.command("降水量预报", alias={"降水量预报图", "降水预报图", "降水预报"})
    async def precipitation_image(
        self,
        event: AstrMessageEvent,
        product_keyword: str = None,
        hour_keyword: str = None,
    ):
        """查询单张降水量预报图"""
        async for result in self._query_command_service.handle_query_precipitation(
            event,
            product_keyword=product_keyword,
            hour_keyword=hour_keyword,
        ):
            yield result

    @filter.command("降水量预报动图", alias={"降水预报动图"})
    async def precipitation_gif(
        self,
        event: AstrMessageEvent,
        product_keyword: str = None,
    ):
        """查询降水量预报全时次循环动图"""
        async for result in self._query_command_service.handle_query_precipitation_gif(
            event,
            product_keyword=product_keyword,
        ):
            yield result

    @filter.command("气温排行", alias={"温度排行", "气温榜", "温度榜"})
    async def temperature_rank(self, event: AstrMessageEvent, time_arg: str = None):
        """查询全国实况气温排行 Top10"""
        async for result in self._query_command_service.handle_query_rank(
            event, rank_keyword="气温", time_arg=time_arg
        ):
            yield result

    @filter.command(
        "最低气温排行",
        alias={"最低温排行", "最低气温榜", "低温排行", "低温榜"},
    )
    async def mintemperature_rank(self, event: AstrMessageEvent, time_arg: str = None):
        """查询全国实况最低气温排行 Top10"""
        async for result in self._query_command_service.handle_query_rank(
            event, rank_keyword="最低气温", time_arg=time_arg
        ):
            yield result

    @filter.command("降水排行", alias={"降水榜", "降水量排行", "降水量榜"})
    async def rain_rank(self, event: AstrMessageEvent, time_arg: str = None):
        """查询全国实况降水排行 Top10"""
        async for result in self._query_command_service.handle_query_rank(
            event, rank_keyword="降水", time_arg=time_arg
        ):
            yield result

    @filter.command("风速排行", alias={"风速榜", "风速排行榜"})
    async def wind_rank(self, event: AstrMessageEvent, time_arg: str = None):
        """查询全国实况风速排行 Top10"""
        async for result in self._query_command_service.handle_query_rank(
            event, rank_keyword="风速", time_arg=time_arg
        ):
            yield result

    @filter.command("气象站实况", alias={"实况", "气象站"})
    async def query_weather_station_real(
        self,
        event: AstrMessageEvent,
        keyword: str = None,
    ):
        """查询气象站实况（支持站点代码或站名）"""
        async for result in self._query_command_service.handle_query_weather_real(
            event, keyword=keyword
        ):
            yield result

    @filter.command("气象站历史", alias={"实况历史"})
    async def query_weather_station_history(
        self,
        event: AstrMessageEvent,
        keyword: str = None,
        time_arg: str = None,
    ):
        """查询气象站近24小时逐小时历史数据（可选指定时次）"""
        async for result in self._query_command_service.handle_query_weather_history(
            event, keyword=keyword, time_arg=time_arg
        ):
            yield result

    @filter.command("气象站列表")
    async def query_weather_station_list(
        self,
        event: AstrMessageEvent,
        province: str = None,
    ):
        """查询气象站列表（可按省份过滤）"""
        async for (
            result
        ) in self._query_command_service.handle_query_weather_station_list(
            event, province=province
        ):
            yield result

    @filter.command("空气质量", alias={"AQI", "aqi", "空气质量指数"})
    async def query_aqi(
        self,
        event: AstrMessageEvent,
        keyword: str = None,
        optional_a: str = None,
    ):
        """查询城市/省份/全国空气质量（FAN Studio AQI）"""
        async for result in self._query_command_service.handle_query_aqi(
            event, keyword=keyword, optional_a=optional_a
        ):
            yield result

    @filter.command(
        "空气质量排行", alias={"AQI排行", "aqi排行", "空气质量排行榜", "空气榜"}
    )
    async def query_aqi_rank(
        self,
        event: AstrMessageEvent,
        direction: str = None,
    ):
        """查询空气质量排行榜（最好/最差 Top10）"""
        async for result in self._query_command_service.handle_query_aqi_rank(
            event, direction=direction
        ):
            yield result

    @filter.command("空气质量列表", alias={"AQI列表", "aqi列表", "空气质量城市列表"})
    async def query_aqi_city_list(
        self,
        event: AstrMessageEvent,
        province: str = None,
    ):
        """查询空气质量支持的城市列表（可按省份过滤）"""
        async for result in self._query_command_service.handle_query_aqi_city_list(
            event, province=province
        ):
            yield result

    # ======================================================================
    # 4. 台风类指令
    # ======================================================================

    @filter.command("台风信息查询", alias={"台风查询", "台风信息"})
    async def query_typhoon_info(
        self,
        event: AstrMessageEvent,
        arg1: str = None,
        arg2: str = None,
        arg3: str = None,
    ):
        """台风信息查询（优先 EQSC，失败回退本地数据库）"""
        async for result in self._query_command_service.handle_query_typhoon(
            event,
            arg1=arg1,
            arg2=arg2,
            arg3=arg3,
        ):
            yield result

    # ======================================================================
    # 5. 模拟测试指令
    # ======================================================================

    @filter.command("灾害预警模拟")
    async def simulate_disaster(
        self,
        event: AstrMessageEvent,
        arg1: str = None,
        arg2: str = None,
        arg3: str = None,
        arg4: str = None,
        arg5: str = None,
    ):
        """模拟灾害事件测试预警响应。"""
        async for result in self._query_command_service.handle_simulate_disaster(
            event,
            arg1=arg1,
            arg2=arg2,
            arg3=arg3,
            arg4=arg4,
            arg5=arg5,
        ):
            yield result

    # ======================================================================
    # 6. 运维管理指令（仅管理员）
    # ======================================================================

    @filter.command("灾害预警状态")
    async def disaster_status(self, event: AstrMessageEvent):
        """查看灾害预警服务状态"""
        async for result in self._admin_command_service.handle_disaster_status(event):
            yield result

    @filter.command("灾害预警重启", alias={"灾害预警重载"})
    async def disaster_restart(self, event: AstrMessageEvent):
        """重载插件（等价于 AstrBot WebUI 中的重载插件操作）"""
        async for result in self._admin_command_service.handle_disaster_restart(event):
            yield result

    @filter.command(
        "重启AstrBot",
        alias={
            "重启 AstrBot",
            "重启astrbot",
            "重启 astrbot",
            "重载 AstrBot",
            "重载AstrBot",
            "重载 astrbot",
            "重载astrbot",
        },
    )
    async def restart_astrbot(self, event: AstrMessageEvent):
        """重启整个 AstrBot 进程（等价于 AstrBot WebUI 中的「设置 → 维护 → 重启 AstrBot」）"""
        async for result in self._admin_command_service.handle_restart_astrbot(event):
            yield result

    @filter.command("灾害预警重连")
    async def disaster_reconnect(self, event: AstrMessageEvent):
        """强制对所有已启用但离线的数据源发起重连"""
        async for result in self._admin_command_service.handle_disaster_reconnect(
            event
        ):
            yield result

    @filter.command("灾害预警统计")
    async def disaster_stats(self, event: AstrMessageEvent):
        """查看灾害预警详细统计"""
        async for result in self._admin_command_service.handle_disaster_stats(event):
            yield result

    @filter.command("灾害预警统计清除")
    async def clear_statistics(self, event: AstrMessageEvent):
        """清除统计数据"""
        async for result in self._admin_command_service.handle_clear_statistics(event):
            yield result

    @filter.command("灾害预警推送开关")
    async def toggle_push(self, event: AstrMessageEvent):
        """开关当前会话的推送"""
        async for result in self._admin_command_service.handle_toggle_push(event):
            yield result

    @filter.command("灾害预警配置")
    async def disaster_config(
        self,
        event: AstrMessageEvent,
        action: str = None,
        target: str = None,
    ):
        """查看当前配置信息（支持按会话查看差异覆写）"""
        async for result in self._admin_command_service.handle_disaster_config(
            event, action=action, target=target
        ):
            yield result

    @filter.command("灾害预警日志")
    async def disaster_logs(self, event: AstrMessageEvent):
        """查看原始消息日志信息"""
        async for result in self._admin_command_service.handle_disaster_logs(event):
            yield result

    @filter.command("灾害预警日志开关")
    async def toggle_message_logging(self, event: AstrMessageEvent):
        """开关原始消息日志记录"""
        async for result in self._admin_command_service.handle_toggle_message_logging(
            event
        ):
            yield result

    @filter.command("灾害预警日志清除")
    async def clear_message_logs(self, event: AstrMessageEvent):
        """清除所有原始消息日志"""
        async for result in self._admin_command_service.handle_clear_message_logs(
            event
        ):
            yield result

    @filter.command("服务器切换")
    async def server_switch(
        self, event: AstrMessageEvent, data_source: str = None, preference: str = None
    ):
        """切换数据源主备服务器。"""
        async for result in self._admin_command_service.handle_server_switch(
            event, data_source, preference
        ):
            yield result

    # ======================================================================
    # 命令辅助
    # ======================================================================

    async def is_plugin_admin(self, event: AstrMessageEvent) -> bool:
        """检查用户是否为插件管理员或Bot管理员"""
        return await self._command_support_service.is_plugin_admin(event)

    @staticmethod
    def _with_quote_reply(
        event: AstrMessageEvent,
        chain: list[Any],
    ) -> list[Any]:
        """为消息链添加引用回复段（若可用）。"""
        return PluginCommandSupportService.with_quote_reply(event, chain)

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """AstrBot加载完成时的钩子"""
        # 记录本次进程内已完成 AstrBot 加载，供后续插件重载场景区分使用。
        mark_astrbot_loaded()

        service = getattr(self, "disaster_service", None)
        if service is None:
            logger.debug("[灾害预警] AstrBot 已加载完成，灾害预警服务尚未初始化")
            return
