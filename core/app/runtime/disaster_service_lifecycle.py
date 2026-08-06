"""
灾害服务生命周期编排服务。
负责 DisasterWarningService 的启动、停止、连接任务管理与后台任务回收，
减少主服务类中的生命周期过程式代码。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from astrbot.api import logger


class DisasterServiceLifecycleService:
    """灾害服务生命周期编排服务。"""

    # 插件重载场景的静默硬超时：AstrBot 已就绪，但仍保留与默认一致的 30 秒兜底
    # 确保数据源有充足时间完成建连/首包/首轮同步后再正常放行。
    RELOAD_HARD_TIMEOUT_SECONDS = 30.0

    def __init__(self, service):
        # 这里只保存主服务引用；真正的状态与资源都仍由主服务统一持有。
        self.service = service  # 主服务 DisasterWarningService 实例
        # 是否推迟静默武装（首次启动/进程重启时等待 AstrBot 加载完成钩子）
        self._defer_arm = False

    async def start(self, *, defer_silence_arm: bool = False) -> None:
        """异步启动灾害预警服务。

        Args:
            defer_silence_arm: 为 True 时推迟静默武装，等待 AstrBot 加载完成
                钩子显式调用 arm_startup_silence()；用于首次启动/进程重启，
                避免 30 秒硬超时被 AstrBot 加载耗时提前耗尽。
        """
        # 启动过程必须串行化，避免重复启动导致连接、定时任务或缓存恢复被执行多次。
        async with self.service._start_lock:
            if self.service.running:
                logger.debug("[灾害预警] 服务已在运行中，跳过重复启动")
                return

            try:
                # 一旦进入启动流程，先切换运行标记并记录启动时间，
                # 后续静默期判断、运行时长统计等逻辑都会依赖该时间戳。
                self.service.running = True
                self.service._stopping = False
                self.service.start_time = datetime.now(
                    timezone.utc
                )  # 服务启动UTC时间戳
                logger.debug("[灾害预警] 正在启动灾害预警服务...")

                # 武装启动静默：在真正建连/轮询前注册门闩。
                # 首次启动/进程重启时 AstrBot 尚未加载完成，静默武装推迟到
                # on_astrbot_loaded 钩子触发，确保硬超时从 AstrBot 就绪时刻起算；
                # 插件重载时 AstrBot 已就绪，立即武装（保留 30 秒兜底）。
                if defer_silence_arm:
                    self._defer_arm = True
                    # 待武装期间协调器进入 PENDING：AstrBot 加载窗口内的事件
                    # 仍被吸收播种，但不会开始计时/就绪判定。
                    self._begin_deferred_silence()
                    logger.debug(
                        "[灾害预警] 静默启动已推迟，等待 AstrBot 加载完成钩子触发"
                    )
                else:
                    self._arm_startup_silence(
                        hard_timeout_seconds=self.RELOAD_HARD_TIMEOUT_SECONDS
                    )

                # 启动顺序刻意遵循“先恢复状态，再开放接入”的原则：
                # 1. 初始化统计存储；
                # 2. 恢复地震列表缓存；
                # 3. 恢复地震预警查询缓存。
                # 这样首批接入事件在进入主链路时，就能获得较完整的历史上下文。
                await (
                    self.service.statistics_manager.initialize()
                )  # 初始化数据库连接与加载内存近期推送
                self.service.cache_service.load_earthquake_lists_cache()  # 载入本地地震列表缓存
                self.service.cache_service.load_eew_query_cache()  # 载入本地地震预警状态缓存

                # 运行时任务按“WebSocket 管理器 -> 建立连接 -> 定时 HTTP 拉取 -> 清理任务”启动。
                # 这个顺序可以确保底层接入设施先就绪，再逐层开启依赖它们的上层任务。
                await self.service.ws_manager.start()  # 开启 WebSocket 底层支持
                await (
                    self.service._establish_websocket_connections()
                )  # 开启 WebSocket 连接监听协程
                await (
                    self.service._start_scheduled_http_fetch()
                )  # 开启定时拉取 HTTP 接口协程
                await self.service._start_cleanup_task()  # 开启过期缓存定时清理协程
                # S-Net 专用轮询（MSIL 瓦片，不同于 Wolfx 列表补偿）
                snet_poll = getattr(self.service, "snet_poll_service", None)
                if snet_poll is not None:
                    await snet_poll.start()

                # EQSC AccessToken 必须先于海啸/台风轮询预热：
                # 两路轮询共享同一 token_manager；若首轮并发请求时 token 尚未就绪，
                # 会各自 force_refresh 造成重复 createAccessToken 与 401 噪声。
                if hasattr(self.service, "schedule_eqsc_token_warmup"):
                    self.service.schedule_eqsc_token_warmup()

                # EQSC 海啸 HTTP 轮询（依赖 AccessToken；预热已调度，可并行等待）
                eqsc_tsunami_poll = getattr(
                    self.service, "eqsc_tsunami_poll_service", None
                )
                if eqsc_tsunami_poll is not None:
                    await eqsc_tsunami_poll.start()
                # EQSC 台风 HTTP 独立轮询（不依赖 FAN 触发）
                eqsc_typhoon_poll = getattr(
                    self.service, "eqsc_typhoon_poll_service", None
                )
                if eqsc_typhoon_poll is not None:
                    await eqsc_typhoon_poll.start()
                eqsc_cenc_ir_poll = getattr(
                    self.service, "eqsc_cenc_intensity_poll_service", None
                )
                if eqsc_cenc_ir_poll is not None:
                    await eqsc_cenc_ir_poll.start()
                # 连接健康采样：依赖 statistics_manager 已 initialize
                health_service = getattr(
                    self.service, "connection_health_service", None
                )
                if health_service is not None:
                    await health_service.start()
                if getattr(self.service, "notification_center", None):
                    await (
                        self.service.notification_center.start()
                    )  # 开启网页控制台通知轮询与拉取

                # 原始消息日志属于排障辅助能力，是否启用只影响调试体验，不影响主流程可用性。
                if self.service.message_logger.enabled:
                    logger.debug(
                        f"[灾害预警] 原始消息日志记录已启用，日志文件: {self.service.message_logger.log_file_path}"
                    )
                else:
                    logger.debug(
                        "[灾害预警] 原始消息日志记录未启用。如需调试或记录原始数据，请使用命令 '/灾害预警日志开关' 启用。"
                    )

                # EQSC 历史台风重建放到启动完成后的后台任务：
                # 此时数据库已就绪，且不会阻塞 WebSocket/管理端启动。
                if hasattr(self.service, "schedule_typhoon_db_rebuild"):
                    self.service.schedule_typhoon_db_rebuild()

                logger.debug("[灾害预警] 灾害预警服务已启动")
            except Exception as e:
                # 启动失败时必须回滚运行标记，避免外部误判服务已可用。
                logger.error(f"[灾害预警] 启动服务失败: {e}")
                self.service.running = False
                coordinator = getattr(self.service, "startup_silence", None)
                if coordinator is not None:
                    coordinator.disarm()
                if self.service._telemetry and self.service._telemetry.enabled:
                    await self.service._telemetry.track_error(
                        e, module="core.disaster_service.start"
                    )
                raise

    def arm_startup_silence(self, *, hard_timeout_seconds: float | None = None) -> None:
        """正式武装启动静默（供 AstrBot 加载完成钩子调用）。

        首次启动/进程重启时静默武装被推迟，需要等 AstrBot 真正加载完成后
        由 on_astrbot_loaded 钩子显式触发，保证 30 秒硬超时从该时刻起算。

        若钩子在后台 start() 任务真正执行前触发（running 仍为 False），
        会调度延迟重试，避免静默永久停在 PENDING。
        """
        if not getattr(self.service, "running", False):
            # 服务后台任务尚未真正开始：延迟重试，避免时序竞态导致静默永久停在 PENDING。
            logger.warning("[灾害预警] 服务尚未运行，静默启动推迟重试")
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.call_later(
                1.0,
                lambda: self.arm_startup_silence(
                    hard_timeout_seconds=hard_timeout_seconds
                ),
            )
            return
        self._defer_arm = False
        self._arm_startup_silence(hard_timeout_seconds=hard_timeout_seconds)

    def _begin_deferred_silence(self) -> None:
        """让协调器进入待武装吸收模式（PENDING）。"""
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is None:
            return
        begin = getattr(coordinator, "begin_deferred", None)
        if not callable(begin):
            return
        debug_config: dict = {}
        config = getattr(self.service, "config", {}) or {}
        if isinstance(config, dict):
            raw_debug = config.get("debug_config", {})
            if isinstance(raw_debug, dict):
                debug_config = raw_debug
        enabled = coordinator.resolve_enabled(debug_config)
        try:
            begin(enabled=enabled)
        except Exception as exc:
            logger.debug(f"[灾害预警] 进入待武装静默失败（已忽略）: {exc}")

    def _arm_startup_silence(
        self, *, hard_timeout_seconds: float | None = None
    ) -> None:
        """根据配置与连接计划武装启动静默协调器。"""
        coordinator = getattr(self.service, "startup_silence", None)
        if coordinator is None:
            return
        debug_config: dict = {}
        config = getattr(self.service, "config", {}) or {}
        if isinstance(config, dict):
            raw_debug = config.get("debug_config", {})
            if isinstance(raw_debug, dict):
                debug_config = raw_debug
        enabled = coordinator.resolve_enabled(debug_config)

        # connections 由 ConnectionPlanBuilder 产出，本身即为 WebSocket 连接计划，
        # 无需再按 handler 名称白名单过滤，以免遗漏新增数据源。
        expected_ws: list[str] = []
        connections = getattr(self.service, "connections", None) or {}
        if isinstance(connections, dict):
            for name, plan in connections.items():
                if not isinstance(plan, dict):
                    continue
                conn_name = str(name or "").strip()
                if conn_name:
                    expected_ws.append(conn_name)

        expected_polls: list[str] = []
        snet_poll = getattr(self.service, "snet_poll_service", None)
        if snet_poll is not None and getattr(snet_poll, "is_enabled", lambda: False)():
            expected_polls.append("snet_msil")
        eqsc_tsunami = getattr(self.service, "eqsc_tsunami_poll_service", None)
        if (
            eqsc_tsunami is not None
            and getattr(eqsc_tsunami, "is_enabled", lambda: False)()
        ):
            expected_polls.append("eqsc_tsunami")
        eqsc_typhoon = getattr(self.service, "eqsc_typhoon_poll_service", None)
        if (
            eqsc_typhoon is not None
            and getattr(eqsc_typhoon, "is_enabled", lambda: False)()
        ):
            expected_polls.append("eqsc_typhoon")
        eqsc_cenc_ir = getattr(self.service, "eqsc_cenc_intensity_poll_service", None)
        if (
            eqsc_cenc_ir is not None
            and getattr(eqsc_cenc_ir, "is_enabled", lambda: False)()
        ):
            expected_polls.append("eqsc_cenc_ir")

        coordinator.arm(
            enabled=enabled,
            expected_ws=expected_ws,
            expected_polls=expected_polls,
            hard_timeout_seconds=hard_timeout_seconds,
        )

    async def cancel_and_wait(self, tasks: list[asyncio.Task]) -> None:
        """
        取消并等待指定的 asyncio.Task 任务列表结束。

        Args:
            tasks (list[asyncio.Task]): 需要强制回收的任务句柄列表
        """
        # 这里刻意不区分任务类型，统一采用“先发取消，再集中等待”的回收方式，
        # 以便在停机链路中复用同一套任务收尾逻辑。
        for task in tasks:
            task.cancel()  # 触发任务取消
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)  # 并发等待任务安全退出

    async def stop(self) -> None:
        """异步停止灾害服务，回收后台协程任务并保存缓存文件。"""
        # 停止过程同样需要串行化；此外还用 _stopping 标记抵御递归或重入调用。
        async with self.service._stop_lock:
            if self.service._stopping:
                logger.debug("[灾害预警] 停止流程已在执行中，跳过重复调用")
                return
            self.service._stopping = True
            try:
                # 记录停止流程起始时间，供停止汇总大屏统计停机耗时。
                self.service.stop_started_at = datetime.now(timezone.utc)
                logger.debug("[灾害预警] 正在停止灾害预警服务...")
                was_running = self.service.running
                # 提前将运行标记切为 False，阻止新任务继续按“服务运行中”路径工作。
                self.service.running = False
                coordinator = getattr(self.service, "startup_silence", None)
                if coordinator is not None:
                    coordinator.disarm()

                # 立刻停连接健康采样：避免 running=False 后仍采样，把通道误记为
                # major_outage 并触发错误事故开单。
                health_service = getattr(
                    self.service, "connection_health_service", None
                )
                if health_service is not None:
                    await health_service.stop()

                # 只有服务曾实际运行过，缓存状态才有落盘意义；
                # 若初始化后从未成功启动，则无需写出这些状态文件。
                if was_running:
                    self.service.cache_service.save_earthquake_lists_cache()  # 保存地震列表到本地
                    self.service.cache_service.save_eew_query_cache()  # 保存地震预警状态到本地

                # 停机顺序遵循“先停上层任务，再关底层资源”：
                # 这样可以避免任务还在执行时，其依赖的连接、抓取器或数据库已被提前关闭。
                connection_tasks = list(self.service.connection_tasks)
                await self.cancel_and_wait(
                    connection_tasks
                )  # 终止并回收 WebSocket 连接协程
                self.service.connection_tasks.clear()

                scheduled_tasks = list(self.service.scheduled_tasks)
                await self.cancel_and_wait(
                    scheduled_tasks
                )  # 终止并回收 HTTP 轮询定时任务
                self.service.scheduled_tasks.clear()

                # 后台任务集合中可能混入已完成任务，因此先过滤，减少无意义取消。
                background_tasks = [
                    task
                    for task in self.service.background_tasks
                    if task and not task.done()
                ]
                await self.cancel_and_wait(background_tasks)  # 终止并回收通用后台任务
                self.service.background_tasks.clear()

                # 任务回收完成后，再逐项释放底层基础设施资源。
                if getattr(self.service, "notification_center", None):
                    await self.service.notification_center.stop()  # 停止网页端通知服务

                # 停机前推送气象预警聚合缓冲区中尚未发送的事件，避免丢失。
                # 必须放在 ws_manager.stop() 之前：此时底座连接仍然可用，
                # 合并转发能成功；若等连接销毁后再推送，发送必然失败。
                # 此时 WebSocket 连接协程已被取消，不会再有新事件进入缓冲，安全。
                weather_agg = getattr(
                    self.service, "_weather_aggregation_service", None
                )
                if weather_agg is not None:
                    try:
                        await weather_agg.flush_all()
                    except Exception as flush_err:
                        logger.debug(
                            f"[灾害预警] 停机时推送气象预警聚合缓冲区失败（已忽略）: {flush_err}"
                        )

                await self.service.ws_manager.stop()  # 关闭并断开所有活跃的底座网络连接

                if self.service.http_fetcher:
                    await (
                        self.service.http_fetcher.close()
                    )  # 关闭 HTTP 客户端 Session 连接池

                # 先停 EQSC 海啸/台风轮询客户端（共享 token 时不会关闭 token_manager）
                eqsc_tsunami_poll = getattr(
                    self.service, "eqsc_tsunami_poll_service", None
                )
                if eqsc_tsunami_poll is not None:
                    await eqsc_tsunami_poll.stop()
                eqsc_typhoon_poll = getattr(
                    self.service, "eqsc_typhoon_poll_service", None
                )
                if eqsc_typhoon_poll is not None:
                    await eqsc_typhoon_poll.stop()
                eqsc_cenc_ir_poll = getattr(
                    self.service, "eqsc_cenc_intensity_poll_service", None
                )
                if eqsc_cenc_ir_poll is not None:
                    await eqsc_cenc_ir_poll.stop()

                # 先关闭台风富化服务，最后关闭 EQSC 通道服务（停止保活并释放令牌管理器资源）。
                typhoon_enrichment = getattr(
                    self.service, "typhoon_enrichment_service", None
                )
                if typhoon_enrichment:
                    await typhoon_enrichment.close()
                eqsc_channel = getattr(self.service, "eqsc_channel_service", None)
                if eqsc_channel:
                    await eqsc_channel.close()

                # 统计数据库只在已初始化时关闭，避免访问尚未建立的数据库句柄。
                if (
                    self.service.statistics_manager
                    and self.service.statistics_manager._db_initialized
                ):
                    await (
                        self.service.statistics_manager.db.close()
                    )  # 关闭 SQLite 数据库连接句柄
                    # 重载插件后需要允许统计管理器重新建库/重载，否则会保留“已初始化”假状态。
                    self.service.statistics_manager._db_initialized = False

                logger.debug("[灾害预警] 灾害预警服务已停止")
            except Exception as e:
                logger.error(f"[灾害预警] 停止服务时出错: {e}")
                if self.service._telemetry and self.service._telemetry.enabled:
                    await self.service._telemetry.track_error(
                        e, module="core.disaster_service.stop"
                    )
            finally:
                # 无论停止是否成功，都要清除“正在停止”标记，防止后续流程被永久阻塞。
                self.service._stopping = False
