"""
灾害预警插件 Web 管理服务器。
负责创建管理端宿主应用，并装配路由、实时广播、健康探测与静态页面入口。
"""

import asyncio
import secrets
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .....utils.geolocation import close_geoip_session
from ....services.config.config_service import ConfigAccessor
from ...monitoring.source_health_monitor import SourceHealthMonitor
from ...websocket.websocket_hub import WebSocketHub
from ..payloads.api_response import ApiResponse
from ..payloads.config_payload_builder import ConfigPayloadBuilder
from ..payloads.connections_payload_builder import ConnectionsPayloadBuilder
from ..payloads.realtime_payload_builder import RealtimePayloadBuilder
from .web_server_runtime_service import WebServerRuntimeService

# 动态探测 FastAPI 与 Uvicorn 环境
try:
    import uvicorn
    from fastapi import FastAPI, Request, WebSocket
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    logger.warning(
        "[灾害预警] FastAPI 未安装，Web 管理端功能不可用。请运行: pip install fastapi uvicorn"
    )


class WebAdminServer:
    """Web 管理端服务器。"""

    def __init__(self, disaster_service, config: dict[str, Any]):
        """初始化管理端宿主，并装配运行时依赖。"""
        self.disaster_service = disaster_service
        self.config = config
        self.config_accessor = ConfigAccessor(config)
        self.app = None
        self.server = None
        self._server_task = None
        self._broadcast_task = None
        self._ping_task = None
        self._ws_hub = WebSocketHub()

        # 延迟缓存容器，用于在健康监控与连接面板展示之间共享探测数值
        self._latency_cache: dict[str, float | None] = {}
        eqsc_host = ConnectionsPayloadBuilder.resolve_eqsc_host(self.config)
        self._source_health_monitor = SourceHealthMonitor(
            self._latency_cache,
            host_overrides={"eqsc": eqsc_host},
        )

        # 注入各不同职责的 Payload 生成器
        self._connections_payload_builder = ConnectionsPayloadBuilder(
            disaster_service=self.disaster_service,
            config=self.config,
            latency_cache=self._latency_cache,
        )
        self._config_payload_builder = ConfigPayloadBuilder(self.config)
        self._realtime_payload_builder = RealtimePayloadBuilder(
            disaster_service=self.disaster_service,
            config=self.config,
            latency_cache=self._latency_cache,
        )
        self._auth_enabled = False
        self._auth_token: str | None = None

        # 注入后台运行时调度管理服务
        self._runtime_service = WebServerRuntimeService(self)

        if not FASTAPI_AVAILABLE:
            return

        self._setup_app()

    def _setup_app(self):
        """配置 FastAPI 应用。"""
        self.app = FastAPI(
            title="灾害预警管理端",
            description="灾害预警插件 Web 管理界面",
            version="1.0.0",
        )

        # 鉴权配置先于路由注册完成，确保中间件与 WebSocket 端点复用同一套运行时状态。
        self._runtime_service.configure_auth()

        @self.app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            """拦截管理端 API 请求并执行令牌鉴权。"""
            # 若未设置鉴权密码，直接放行
            if not self._auth_enabled:
                return await call_next(request)

            # 放行非 API 接口及登录鉴权专有端点
            path = request.url.path
            if path in {"/api/login", "/api/auth-info"}:
                return await call_next(request)
            if not path.startswith("/api"):
                return await call_next(request)

            # 从 HTTP query params 或 Authorization Bearer 头部提取 Token 字段
            token = request.query_params.get("token", "")
            if not token:
                auth_header = request.headers.get("Authorization", "")
                auth_parts = auth_header.split(" ", 1)
                token = (
                    auth_parts[1].strip()
                    if len(auth_parts) == 2 and auth_parts[0].lower() == "bearer"
                    else ""
                )

            # 时序安全防爆破校验
            if not self._auth_token or not secrets.compare_digest(
                token, self._auth_token
            ):
                return ApiResponse.error("未授权，请先登录", status_code=401)

            return await call_next(request)

        # 支持跨域访问
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        self._register_routes()

        # 装载气象预警回退图标资源目录，供前端在官方图标不可用时本地回退。
        # 注意：此 mount 必须在 admin 静态目录之前注册，否则 / 的 catch-all
        # 会拦截 /weatheralarm_logo/ 下的所有请求导致图标 404。
        logo_dir = (
            Path(__file__).resolve().parents[4] / "resources" / "weatheralarm_logo"
        )
        if logo_dir.exists():
            self.app.mount(
                "/weatheralarm_logo",
                StaticFiles(directory=logo_dir),
                name="weatheralarm_logo",
            )
        else:
            logger.warning(
                "[灾害预警] 未找到气象预警回退图标目录，跳过注册 /weatheralarm_logo 路由"
            )

        # 装载静态网页资源目录（SPA 入口，catch-all 放最后）
        admin_dir = Path(__file__).resolve().parents[4] / "admin"
        if admin_dir.exists():
            self.app.mount(
                "/", StaticFiles(directory=admin_dir, html=True), name="admin"
            )

    def _register_routes(self):
        """注册 API 路由。"""
        # 装载全部 HTTP 端点路由定义
        self._runtime_service.register_routes()

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """管理端 WebSocket 实时推送端点。"""
            await self._runtime_service.handle_websocket(websocket)

    async def _send_full_update(self, websocket: WebSocket):
        """向单个客户端发送完整数据更新。"""
        await self._runtime_service.send_full_update(websocket)

    async def _broadcast_data(self):
        """向所有已连接客户端广播数据更新。"""
        await self._runtime_service.broadcast_data()

    async def get_realtime_data(self) -> dict:
        """获取 WebSocket 推送所需的实时数据。"""
        return await self._runtime_service.get_realtime_data()

    def get_expected_data_sources(self) -> dict[str, str]:
        """获取所有支持的数据源列表，不区分当前是否启用。"""
        return self._source_health_monitor.get_expected_data_sources()

    async def _broadcast_loop(self):
        """后台广播循环，作为保底同步机制定期推送快照。"""
        await self._runtime_service.run_broadcast_loop(interval_seconds=30)

    async def notify_event(self, event_data: dict = None):
        """当有新灾害事件时，立即向所有客户端推送事件更新。"""
        await self._runtime_service.notify_event(event_data)

    def _get_data_source_host(self, source_name: str) -> str | None:
        """获取数据源的主机名，供延迟探测使用。"""
        return self._source_health_monitor.get_data_source_host(source_name)

    async def _ping_host(
        self, host: str, port: int = 443, timeout: float = 3.0
    ) -> float | None:
        """使用 TCP 连接测试主机延迟。"""
        return await self._source_health_monitor.ping_host(
            host, port=port, timeout=timeout
        )

    async def _background_ping_loop(self):
        """后台定期更新延迟缓存。"""
        await self._source_health_monitor.run_background_ping_loop(interval_seconds=30)

    async def start(self):
        """启动 Web 服务器。"""
        if not FASTAPI_AVAILABLE:
            logger.error("[灾害预警] 无法启动 Web 管理端: FastAPI 未安装")
            return

        web_config = self.config_accessor.web_admin_config()
        host = web_config.get("host", "0.0.0.0")
        port = web_config.get("port", 8089)

        # 记录访问地址，供启动汇总大屏等场景展示。
        self.url = f"http://{host}:{port}"

        # 构造 Uvicorn 运行配置
        config = uvicorn.Config(
            self.app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
            # 优雅关闭时内部最多等待 10 秒（默认 None 会对长连接无限等待）。
            # 超时后 uvicorn 会取消残留后台任务并继续执行 lifespan.shutdown()，
            # 配合 stop() 的外部超时兜底，双保险确保端口最终能释放。
            timeout_graceful_shutdown=10,
        )
        self.server = uvicorn.Server(config)

        logger.debug(f"[灾害预警] Web 管理端已启动: {self.url}")

        async def _serve():
            try:
                await self.server.serve()
            except asyncio.CancelledError:
                # 正常停止时会取消该任务，需放行以保持取消语义。
                raise
            except (SystemExit, Exception):
                # Uvicorn 绑定端口失败会调用 sys.exit() 抛出 SystemExit（属
                # BaseException 而非 Exception）。该任务由 create_task 起、从不
                # 被 await，若不在此拦截，异常会作为未检索的任务异常冒泡到事件
                # 循环根部，拖垮整个 AstrBot 进程。这里显式只拦 SystemExit 与
                # Exception，不波及 KeyboardInterrupt。
                # logger.exception 已含异常类型与堆栈，无需再拼接异常对象。
                logger.exception("[灾害预警] Web 管理端运行异常")

        # 将服务进程、心跳/延迟检测循环与广播事件的协程单独开启 Task 挂载
        self._server_task = asyncio.create_task(_serve())
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())
        self._ping_task = asyncio.create_task(self._background_ping_loop())

    async def stop(self):
        """停止 Web 服务器。"""
        # 1. 终止后台延迟 TCP ping 检测循环
        if self._ping_task:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass

        # 2. 终止定时数据广播推送循环
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

        # 3. 强行断开并清理所有前端 websocket 句柄连接
        await self._runtime_service.close_all_websockets()

        # 4. 释放全局的 GeoIP 会话
        try:
            await close_geoip_session()
        except Exception as e:
            logger.debug(f"[灾害预警] 关闭 GeoIP 会话时出错: {e}")

        # 5. 退出 Uvicorn Web 服务器实例并等待服务 Task 彻底终止
        if self.server:
            # 优雅关闭优先：由 uvicorn 正常执行 lifespan.shutdown()，让
            # LifespanOn.main() 协程收到关闭事件后自行退出，避免其成为孤儿
            # pending 任务（否则事件循环 GC 会打印 "Task was destroyed but it
            # is pending!" 噪音）。内部超时由 timeout_graceful_shutdown=10 兜底。
            #
            # 注意：这里刻意不使用 asyncio.wait_for 直接等待 _server_task——
            # Uvicorn 0.23.0 的 Server.serve() 在 main_loop() 后才调用 shutdown()
            # 且没有 finally 保护；wait_for 超时会取消任务本身，导致协程可能在
            # 关闭监听 socket 之前就被终止，force_exit 也无法恢复已取消的协程。
            # 因此改用 asyncio.wait（不取消任务）：先等优雅关闭窗口，超时后
            # 设置 force_exit 再等一个短窗口，仍未退出才取消并回收任务。
            self.server.should_exit = True
            force_fallback = False
            if self._server_task and not self._server_task.done():
                try:
                    # 第一段等待：优雅关闭窗口（不取消 _server_task）。
                    done, _ = await asyncio.wait({self._server_task}, timeout=15.0)
                    if not done:
                        logger.warning(
                            "[灾害预警] Web 管理端未在 15 秒内优雅停止，降级为强制退出以释放端口。"
                        )
                        force_fallback = True
                except asyncio.CancelledError:
                    # 若是 stop() 协程本身被外部取消，需放行以保持取消语义。
                    raise
                except Exception as e:
                    logger.warning(f"[灾害预警] 停止 Web 管理端时出现异常: {e!r}")

            if force_fallback:
                # 降级路径：先强制退出（不取消任务），让 serve() 自己尽快返回，
                # 再从事件循环层面回收 lifespan 协程，避免其成为孤儿任务挂起
                # 在 receive_queue.get() 上。
                self.server.force_exit = True
                if self._server_task and not self._server_task.done():
                    try:
                        # 第二段等待：强制退出后的短窗口，仍不取消任务，
                        # 让 Uvicorn 完成 socket 关闭流程。
                        done, _ = await asyncio.wait({self._server_task}, timeout=5.0)
                        if not done:
                            # 任务仍未退出：最后才取消并回收，确保 stop 返回前
                            # 监听 socket 尽量已释放，避免热重载时端口冲突。
                            logger.warning(
                                "[灾害预警] Web 管理端强制退出后仍未停止，取消残留任务以释放端口。"
                            )
                            self._server_task.cancel()
                            try:
                                await self._server_task
                            except asyncio.CancelledError:
                                # 区分两种取消：若是 _server_task 自身被上面
                                # cancel（预期内），吞掉即可；若是 stop() 协程
                                # 本身被外部取消，则需放行以保持取消语义。
                                if not self._server_task.cancelled():
                                    raise
                            except Exception as e:
                                logger.debug(
                                    f"[灾害预警] 等待 Web 管理端任务取消时出现异常: {e!r}"
                                )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.debug(
                            f"[灾害预警] 等待 Web 管理端任务退出时出现异常: {e!r}"
                        )
                # 兜底回收 lifespan：主动发送关闭事件，让 LifespanOn.main()
                # 协程正常退出（重复调用 shutdown 由 Starlette 幂等处理，安全）。
                lifespan = getattr(self.server, "lifespan", None)
                if lifespan is not None:
                    try:
                        # 内部有超时保护，最多等待 10 秒。
                        await asyncio.wait_for(lifespan.shutdown(), timeout=10.0)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "[灾害预警] lifespan 关闭未在 10 秒内完成，跳过等待。"
                        )
                    except Exception as e:
                        logger.debug(
                            f"[灾害预警] lifespan 关闭时出现异常（已忽略）: {e!r}"
                        )
            logger.debug("[灾害预警] Web 管理端已停止")
