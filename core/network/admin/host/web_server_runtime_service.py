"""
Web 管理端运行时服务。
负责 WebAdminServer 的应用初始化、通用路由装配、实时广播委托与健康检查循环委托，
减少 WebAdminServer 主类中的过程式样板代码。
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from datetime import datetime
from typing import Any

from astrbot.api import logger

# 引入 FastAPI FileResponse
try:
    from fastapi.responses import FileResponse
except ImportError:  # pragma: no cover
    FileResponse = None

# 导入各类子模块的路由装配器
from ..api.analytics_routes import register_analytics_routes
from ..api.auth_routes import register_auth_routes
from ..api.backup_routes import register_backup_routes
from ..api.config_routes import register_config_routes
from ..api.events_routes import register_events_routes
from ..api.notification_routes import register_notification_routes
from ..api.runtime_admin_routes import register_runtime_admin_routes
from ..api.runtime_routes import register_runtime_routes
from ..api.session_config_routes import register_session_config_routes
from ..api.status_routes import register_status_routes
from ..api.utility_routes import register_utility_routes
from ..payloads.api_response import ApiResponse


class WebServerRuntimeService:
    """Web 管理端运行时服务。"""

    def __init__(self, server):
        self.server = server

    def configure_auth(self) -> None:
        """按当前配置初始化管理端鉴权状态。"""
        # 读取配置中的管理端访问密码
        password = self.server.config_accessor.web_admin_password()
        # 管理端启用密码时，登录成功后依靠随机令牌维持会话，而非反复传输明文密码
        if password:
            self.server._auth_enabled = True
            # 生成 32 字节的安全随机 Hex Token
            self.server._auth_token = secrets.token_hex(32)

    def register_routes(self) -> None:
        """注册管理端全部 HTTP 路由。"""
        app = self.server.app
        # 路由按主题拆分注册，避免主服务类继续膨胀成巨型文件。
        register_auth_routes(
            app,
            auth_enabled=self.server._auth_enabled,
            auth_token=self.server._auth_token,
            password_getter=self.server.config_accessor.web_admin_password,
        )

        @app.get("/logo.png")
        async def get_logo():
            """返回插件管理端使用的 Logo 图片。"""
            # 向上递归查找项目根路径下的 logo.png
            logo_path = os.path.join(
                os.path.dirname(
                    os.path.dirname(
                        os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                    )
                ),
                "logo.png",
            )
            # 若文件存在则以 FileResponse 发送给前端
            if os.path.exists(logo_path) and FileResponse is not None:
                return FileResponse(logo_path)
            return ApiResponse.error("未找到插件 Logo 的图片文件", status_code=404)

        # 依次调用并装配其他子模块的 API 路由端点
        register_status_routes(
            app,
            disaster_service=self.server.disaster_service,
            realtime_payload_builder=self.server._realtime_payload_builder,
            password_getter=self.server.config_accessor.web_admin_password,
        )
        register_runtime_admin_routes(
            app,
            disaster_service=self.server.disaster_service,
            connections_payload_builder=self.server._connections_payload_builder,
            config_payload_builder=self.server._config_payload_builder,
            expected_sources_getter=self.server.get_expected_data_sources,
        )
        register_utility_routes(
            app,
            disaster_service=self.server.disaster_service,
            plugin_root=os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                )
            ),
        )
        register_events_routes(app, disaster_service=self.server.disaster_service)
        register_analytics_routes(app, disaster_service=self.server.disaster_service)
        register_notification_routes(app, disaster_service=self.server.disaster_service)
        register_runtime_routes(
            app,
            disaster_service=self.server.disaster_service,
            config=self.server.config,
        )
        register_config_routes(app, config=self.server.config)
        register_session_config_routes(
            app, disaster_service=self.server.disaster_service
        )
        register_backup_routes(app, disaster_service=self.server.disaster_service)

    async def handle_websocket(self, websocket) -> None:
        """处理单个管理端 WebSocket 客户端连接。"""
        # 若启用了鉴权，则优先从查询参数或 Authorization 头提取令牌。
        if self.server._auth_enabled:
            token = websocket.query_params.get("token", "")
            # 若 query params 中不存在，则在 HTTP Header 的 Authorization Bearer 字段中匹配提取
            if not token:
                auth_header = websocket.headers.get("Authorization", "")
                auth_parts = auth_header.split(" ", 1)
                token = (
                    auth_parts[1].strip()
                    if len(auth_parts) == 2 and auth_parts[0].lower() == "bearer"
                    else ""
                )
            # 安全比对 Token 是否一致，防止时序攻击
            if not self.server._auth_token or not secrets.compare_digest(
                token, self.server._auth_token
            ):
                # 1008 表示策略违规，适合表达“鉴权失败，拒绝建立连接”。
                await websocket.close(code=1008)
                return

        # 接收并开启 websocket 会话
        await websocket.accept()
        # 连接建立后立即纳入 hub，便于统一广播与连接计数
        self.server._ws_hub.add(websocket)
        logger.info(
            f"[灾害预警] 有 WebSocket 客户端已连接，当前连接数: {self.server._ws_hub.count()}"
        )

        try:
            # 新客户端接入后先推送完整快照，再进入增量交互循环
            if not await self.send_full_update(websocket):
                return
            while True:
                try:
                    data = await websocket.receive_text()
                    msg = json.loads(data)
                    # 响应心跳
                    if msg.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                    # 响应前端要求强刷的请求
                    elif msg.get("type") == "refresh":
                        if not await self.send_full_update(websocket):
                            return
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            disconnect_name = type(e).__name__
            # 过滤正常的断连类型，其他未预期网络传输报错打印 debug
            if disconnect_name != "WebSocketDisconnect":
                logger.debug(f"[灾害预警] WebSocket 连接异常: {e}")
        finally:
            # 断连后必须从 Hub 容器中注销，防止内存泄漏和僵尸发包
            self.server._ws_hub.remove(websocket)
            logger.info(
                f"[灾害预警] 有 WebSocket 客户端已断开，当前连接数: {self.server._ws_hub.count()}"
            )

    async def send_full_update(self, websocket) -> bool:
        """向指定客户端发送一份完整实时快照。"""
        return await self.server._ws_hub.send_full_update(
            websocket, self.server.get_realtime_data
        )

    async def broadcast_data(self) -> None:
        """向全部已连接客户端广播最新实时数据。"""
        await self.server._ws_hub.broadcast_update(self.server.get_realtime_data)

    async def get_realtime_data(self) -> dict[str, Any]:
        """构建管理端实时面板所需的数据载荷。"""
        try:
            # 通过构建器构建出当前的实时状态（在线状态、延迟、最近事件等）
            return await self.server._realtime_payload_builder.build(
                self.server.get_expected_data_sources()
            )
        except Exception as e:
            logger.debug(f"[灾害预警] 构建实时数据失败: {e}")
            return {"timestamp": datetime.now().isoformat()}

    async def notify_event(self, event_data: dict[str, Any] | None = None) -> None:
        """向前端广播一条事件通知。"""
        await self.server._ws_hub.broadcast_event(
            self.server.get_realtime_data, event_data
        )

    async def run_broadcast_loop(self, interval_seconds: int = 30) -> None:
        """后台广播循环。"""
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                # 周期性定时广播最新数据，保证前端界面状态最终一致
                await self.broadcast_data()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[灾害预警] 管理端广播循环执行异常，错误为 {e}")

    async def close_all_websockets(self) -> None:
        """关闭所有 WebSocket 连接。"""
        await self.server._ws_hub.close_all()
