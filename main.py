import asyncio
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.app.disaster_service import get_disaster_service
from .core.app.runtime.boot_marker import (
    is_first_boot_in_process,
    mark_astrbot_loaded,
)
from .core.network.admin.host.web_server import WebAdminServer
from .core.services.telemetry.telemetry_service import TelemetryManager
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
            # 上报初始化失败错误到遥测
            if hasattr(self, "telemetry") and self.telemetry and self.telemetry.enabled:
                try:
                    await self.telemetry.track_error(e, module="main.initialize")
                except Exception:
                    pass

            # 发生异常时，确保清理已启动的任务和资源，防止任务泄露
            await self.terminate()
            raise

    async def _cleanup_telemetry_tasks(self) -> None:
        """清理并终止所有未完成的遥测任务，避免任务泄漏"""
        await self._lifecycle_service.cleanup_telemetry_tasks()

    async def terminate(self):
        """插件销毁时调用"""
        try:
            await self._lifecycle_service.stop_heartbeat_task()
            self._lifecycle_service.restore_asyncio_exception_handler()
            await self._cleanup_telemetry_tasks()
            await self._lifecycle_service.shutdown_plugin_resources()

        except Exception as e:
            logger.error(f"[灾害预警] 插件停止时出错: {e}")
            # 上报停止错误到遥测
            if hasattr(self, "telemetry") and self.telemetry and self.telemetry.enabled:
                await self.telemetry.track_error(e, module="main.terminate")

    def _handle_asyncio_exception(self, loop, context):
        """
        全局 asyncio 异常处理器
        捕获未被处理的 asyncio task 异常并上报到遥测
        """
        self._lifecycle_service.handle_asyncio_exception(loop, context)

    async def _heartbeat_loop(self):
        """心跳循环任务 - 启动时立即发送一次，之后每12小时发送一次"""
        await self._lifecycle_service.heartbeat_loop()

    @filter.command("灾害预警")
    async def disaster_warning_help(self, event: AstrMessageEvent):
        """灾害预警插件帮助"""
        help_text = """🚨 灾害预警插件使用说明

📋 可用命令：
• /灾害预警 - 显示此帮助信息
• /灾害预警状态 - 查看服务运行状态
• /灾害预警重连 - 强制重连所有数据源 (仅管理员)
• /地震列表查询 或 /地震列表 [数据源] [数量] [格式] - 查询最新地震列表
• /地震预警查询 或 /地震预警 - 查询各机构 EEW 状态与无 EEW 计时
• /气象预警查询 或 /气象预警 <省份/地名|全国> [预警类型] [预警颜色] [全部|全日期] 或 <预警ID>（默认近72小时，全日期查询全部历史）
• /台风信息查询 或 /台风查询 [台风ID|名称|数量] [完整|简要] [活跃] - 查询台风信息（优先EQSC，失败回退本地）
• /JMA震央分布 [开始日期] [结束日期] - 查询 JMA 震央分布统计（默认今天）
• /JMA震央分布绘图 [投影类型] [开始日期] [结束日期] - 绘制 JMA 震央分布图
• /生成沙滩球 或 /沙滩球 <走向> <倾角> <滑动角> [大小] [线宽] - 生成震源机制沙滩球图片
• /节面解析 <走向> <倾角> <滑动角> - 解析断层节面参数与运动分量
• /地震动预测 <震中纬度> <震中经度> <震级> <震源深度> <预测点纬度> <预测点经度> （或引用地震消息自动提取参数）
• /本地地震动预测 或 /本地预测 或 /卧槽 [<本地纬度>] [<本地经度>] - 按引用地震消息预测本地地震动（坐标默认取本地监控配置）
• /灾害预警统计 - 查看详细的事件统计报告
• /灾害预警统计清除 - 清除所有统计信息 (仅管理员)
• /灾害预警推送开关 - 开启或关闭当前会话的推送 (仅管理员)
• /雷达 <名称> - 查询最新一帧气象雷达图（如：/雷达 北京、/雷达 全国）
• /雷达动图 <名称> - 查询最近多帧合成循环动图（如：/雷达动图 北京）
• /雷达列表 - 查看全部雷达站点列表
• /降水量预报 [24h|6h] [时次] - 查询单张降水量预报图
• /降水量预报动图 [24h|6h] - 查询降水量预报全时次循环动图
• /气温排行 [跨度] [时次] - 查询全国实况气温排行 Top10（如：/气温排行、/气温排行 24小时）
• /最低气温排行 [跨度] [时次] - 查询全国实况最低气温排行 Top10（缺省逐小时；如：/最低气温排行、/最低气温排行 24小时）
• /降水排行 [跨度] [时次] - 查询全国实况降水排行 Top10（如：/降水排行、/降水排行 24小时、/降水排行 6h 08时）
• /风速排行 [跨度] [时次] - 查询全国实况风速排行 Top10（如：/风速排行、/风速排行 昨天15时）
• /气象站实况 或 /实况 或 /气象站 <站点代码或站名> - 查询气象站实况（如：/实况 59270、/气象站 怀集）
• /气象站历史 或 /实况历史 <站点代码或站名> [时次] - 查询气象站近24小时逐小时历史（如：/气象站历史 59270 10时）
• /气象站列表 [省份] - 查询气象站列表（如：/气象站列表、/气象站列表 广东）
• /空气质量 或 /AQI <城市|省份|全国> [等级] - 查询空气质量（如：/空气质量 北京、/空气质量 全国 优）
• /空气质量排行 或 /空气榜 [最好|最差] - 查询空气质量排行榜 Top10
• /空气质量列表 或 /AQI列表 [省份] - 查看空气质量支持的城市列表（如：/空气质量列表 新疆）
• /灾害预警模拟 <参数...> [数据源] - 模拟灾害事件
   · 地震源: /灾害预警模拟 <纬度> <经度> <震级> [深度] [数据源]
   · 海啸源: /灾害预警模拟 [标题] [等级] [位置] [源震级] [数据源]
   · 气象源: /灾害预警模拟 [标题] [正文] [预警编码] [数据源]
   · 台风源: /灾害预警模拟 [编号] [名称] [强度] [数据源]
• /灾害预警配置 查看 [全局|当前|会话UMO] - 查看配置（会话模式返回差异覆写）(仅管理员)
• /灾害预警日志 - 查看原始消息日志统计摘要 (仅管理员)
• /灾害预警日志开关 - 开关原始消息日志记录 (仅管理员)
• /灾害预警日志清除 - 清除所有原始消息日志 (仅管理员)

更多信息可参考 README 文档"""

        yield event.plain_result(help_text)

    @filter.command("灾害预警重连")
    async def disaster_reconnect(self, event: AstrMessageEvent):
        """强制对所有已启用但离线的数据源发起重连"""
        async for result in self._admin_command_service.handle_disaster_reconnect(
            event
        ):
            yield result

    @filter.command("灾害预警状态")
    async def disaster_status(self, event: AstrMessageEvent):
        """查看灾害预警服务状态"""
        async for result in self._admin_command_service.handle_disaster_status(event):
            yield result

    @filter.command("灾害预警统计")
    async def disaster_stats(self, event: AstrMessageEvent):
        """查看灾害预警详细统计"""
        async for result in self._admin_command_service.handle_disaster_stats(event):
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

    @filter.command("气象预警查询", alias={"气象预警"})
    async def query_weather_alarm(
        self,
        event: AstrMessageEvent,
        keyword: str = None,
        optional_a: str = None,
        optional_b: str = None,
        optional_c: str = None,
    ):
        """气象预警查询（支持 [全部|全日期] 关闭 72 小时过滤）"""
        async for result in self._query_command_service.handle_query_weather_alarm(
            event,
            keyword=keyword,
            optional_a=optional_a,
            optional_b=optional_b,
            optional_c=optional_c,
        ):
            yield result

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

    @filter.command("地震预警查询", alias={"地震预警"})
    async def query_earthquake_warning(self, event: AstrMessageEvent):
        """查询各机构地震预警（EEW）状态"""
        async for result in self._query_command_service.handle_query_earthquake_warning(
            event
        ):
            yield result

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

    @filter.command("雷达列表")
    async def radar_list(self, event: AstrMessageEvent):
        """查看全部气象雷达站点列表"""
        async for result in self._query_command_service.handle_query_radar_list(event):
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
        """模拟灾害事件测试预警响应。

        用法（数据源置于末尾，灾种由数据源自动决定）：
        - /灾害预警模拟 纬度 经度 震级 [深度] [数据源]   (地震源，默认)
        - /灾害预警模拟 标题 等级 位置 [源震级] [数据源] (海啸源)
        - /灾害预警模拟 标题 正文 [预警编码] [数据源]   (气象源)
        - /灾害预警模拟 编号 名称 [强度] [数据源]       (台风源)
        """
        async for result in self._query_command_service.handle_simulate_disaster(
            event,
            arg1=arg1,
            arg2=arg2,
            arg3=arg3,
            arg4=arg4,
            arg5=arg5,
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

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """AstrBot加载完成时的钩子"""
        # 记录本次进程内已完成 AstrBot 加载，供后续插件重载场景区分使用。
        mark_astrbot_loaded()

        service = getattr(self, "disaster_service", None)
        if service is None:
            logger.debug("[灾害预警] AstrBot 已加载完成，灾害预警服务尚未初始化")
            return

        # 首次启动/进程重启时静默武装被推迟，此刻正式武装：
        # 静默硬超时从 AstrBot 真正加载完成时刻起算，避免被加载耗时提前耗尽。
        arm = getattr(service, "arm_startup_silence", None)
        if callable(arm):
            arm()
        logger.debug("[灾害预警] AstrBot 已加载完成，静默启动已正式武装")
