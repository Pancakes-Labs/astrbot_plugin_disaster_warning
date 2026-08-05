"""
灾害服务运行时编排服务。
负责 WebSocket 连接建立、定时 HTTP 拉取与清理任务调度，
减少 DisasterWarningService 中的运行期过程式逻辑。
"""

from __future__ import annotations

import asyncio
import json

from astrbot.api import logger

from ...network.websocket.fan_studio_connection_policy import attach_fan_auth_from_plan
from ...services.query.source_runtime_query_service import SourceRuntimeQueryService


class DisasterServiceRuntimeService:
    """灾害服务运行时编排服务。

    该类负责承接“服务启动后持续发生”的运行期行为：
    例如 WebSocket 建连、Wolfx HTTP 轮询、日常清理任务等。
    这类逻辑通常都带有异步循环或后台任务特征，单独拆出后更便于维护与停机回收。
    """

    def __init__(self, service):
        # 保留主服务引用，运行期任务需要读取连接计划、消息管理器、解析器与运行标志位。
        self.service = service
        # 查询服务用于判断某个数据源是否启用，避免对已禁用源继续做解析与事件投递。
        self._source_runtime_query = SourceRuntimeQueryService(service.config)

    async def establish_websocket_connections(self) -> None:
        """建立 WebSocket 连接。"""
        logger.debug(
            f"[灾害预警] 开始建立WebSocket连接，当前任务数: {len(self.service.connection_tasks)}"
        )

        async def _connect_with_timeout(name, uri, info):
            # 这里封装成局部协程，是为了让每个连接都能作为独立任务运行，
            # 并在日志中清晰标识是哪一个连接任务异常终止。
            try:
                # 建立底层连接
                await self.service.ws_manager.connect(
                    name=name,
                    uri=uri,
                    connection_info=info,
                )
            except Exception as e:
                logger.error(f"[灾害预警] WebSocket 连接任务 {name} 异常终止: {e}")

        # FAN Studio 先主后次：优先启动 /all，再启动独立次要通道。
        ordered_items = list(self.service.connections.items())
        ordered_items.sort(
            key=lambda item: (
                0
                if item[0] == "fan_studio_all"
                else 1
                if str(item[0]).startswith("fan_studio_")
                else 2,
                item[0],
            )
        )

        for conn_name, conn_config in ordered_items:
            # 这里只处理由连接计划生成的 WebSocket 连接；
            # 具体断线重连、备用地址切换等细节由连接管理器内部负责。
            if conn_config["handler"] in [
                "fan_studio",
                "p2p",
                "wolfx",
                "openquake_api",
            ]:
                # 这份连接附加信息会一路传入连接管理器，作为连接状态展示、重连通知、
                # 管理端查询等场景的上下文信息。
                connection_info = {
                    "connection_name": conn_name,
                    "handler_type": conn_config["handler"],
                    "data_source": conn_config.get("data_source", conn_name),
                    "established_time": None,
                    "backup_url": conn_config.get("backup_url"),
                }
                # FAN Studio 鉴权凭证随连接上下文传递，供建连后发送 auth 包。
                attach_fan_auth_from_plan(connection_info, conn_config)

                # 异步建连后台任务
                task = asyncio.create_task(
                    _connect_with_timeout(
                        conn_name, conn_config["url"], connection_info
                    ),
                    name=f"dw_ws_connect_{conn_name}",
                )
                self.service.connection_tasks.append(task)  # 记录任务便于生命周期回收

                backup_info = (
                    f", 备用: {conn_config.get('backup_url')}"
                    if conn_config.get("backup_url")
                    else ""
                )
                logger.debug(
                    f"[灾害预警] 已启动WebSocket连接任务: {conn_name} (数据源: {connection_info['data_source']}{backup_info})"
                )

        logger.debug(
            f"[灾害预警] WebSocket连接建立完成，总任务数: {len(self.service.connection_tasks)}"
        )

    async def start_scheduled_http_fetch(self) -> None:
        """启动定时 HTTP 数据获取。"""

        async def fetch_wolfx_data():
            # Wolfx 列表属于低频补偿型数据：
            # 一方面用于补齐地震列表缓存，另一方面在对应数据源启用时也可转成事件，
            # 为未通过实时流捕获到的列表更新提供兜底入口。
            while self.service.running:
                try:
                    # 固定 5 分钟轮询 Wolfx 列表接口，用于补充列表缓存与低频事件补偿。
                    await asyncio.sleep(300)

                    # 抓取器由主服务统一创建，这里通过上下文协议复用其会话资源。
                    async with self.service.http_fetcher as fetcher:
                        try:
                            # 获取并解析中国地震局最新记录
                            cenc_data = await asyncio.wait_for(
                                fetcher.fetch_json(
                                    "https://api.wolfx.jp/cenc_eqlist.json"
                                ),
                                timeout=60,
                            )
                            if cenc_data:
                                # 无论后续是否转成事件，都先刷新本地列表缓存，
                                # 这样查询接口总能拿到尽可能新的列表内容。
                                self.service.earthquake_list_service.update_earthquake_list(
                                    "cenc", cenc_data
                                )
                                if self._source_runtime_query.is_source_enabled(
                                    "cenc_wolfx"
                                ):
                                    # 这里将 HTTP 返回结果重新整理为解析器可接受的消息内容，
                                    # 复用既有解析链路，避免在运行时服务中重复实现业务转换逻辑。
                                    event = self.service.parse_event(
                                        "cenc_wolfx", json.dumps(cenc_data)
                                    )
                                    if event:
                                        await self.service._handle_disaster_event(event)
                        except asyncio.TimeoutError:
                            logger.warning(
                                "[灾害预警] 定时获取 CENC 地震列表超时，保留原有缓存"
                            )
                        except Exception as e:
                            logger.error(f"[灾害预警] 获取 CENC 数据出错: {e}")

                        try:
                            # 获取并解析日本气象厅最新记录
                            jma_data = await asyncio.wait_for(
                                fetcher.fetch_json(
                                    "https://api.wolfx.jp/jma_eqlist.json"
                                ),
                                timeout=60,
                            )
                            if jma_data:
                                self.service.earthquake_list_service.update_earthquake_list(
                                    "jma", jma_data
                                )
                                if self._source_runtime_query.is_source_enabled(
                                    "jma_wolfx_info"
                                ):
                                    event = self.service.parse_event(
                                        "jma_wolfx_info", json.dumps(jma_data)
                                    )
                                    if event:
                                        await self.service._handle_disaster_event(event)
                        except asyncio.TimeoutError:
                            logger.warning(
                                "[灾害预警] 定时获取 JMA 地震列表超时，保留原有缓存"
                            )
                        except Exception as e:
                            logger.error(f"[灾害预警] 获取 JMA 数据出错: {e}")

                except Exception as e:
                    # 外层兜底保证后台循环不会因单次异常直接退出。
                    logger.error(f"[灾害预警] 定时 HTTP 数据获取失败: {e}")

        # 启动定时拉取后台任务
        task = asyncio.create_task(fetch_wolfx_data(), name="dw_http_fetch_wolfx")
        self.service.scheduled_tasks.append(task)

    async def start_cleanup_task(self) -> None:
        """启动清理任务。"""

        async def cleanup():
            while self.service.running:
                try:
                    # 每日一次清理消息侧历史记录与临时渲染文件，避免长期运行后磁盘膨胀。
                    await asyncio.sleep(86400)
                    self.service.message_manager.cleanup_old_records()  # 清理本地过期磁盘缓存和历史记录
                except Exception as e:
                    logger.error(f"[灾害预警] 清理任务失败: {e}")

        # 启动定时清理任务
        task = asyncio.create_task(cleanup(), name="dw_cleanup")
        self.service.scheduled_tasks.append(task)
