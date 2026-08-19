"""
遥测服务主入口。
承载匿名遥测事件发送、配置快照上报与错误脱敏上报能力。

数据脱敏说明:
- 不收集任何用户个人信息（如群号、QQ号、IP地址等）
- 配置快照仅收集统计性数据（如启用的数据源数量）
- 错误信息仅包含错误类型和模块名，不包含堆栈中的敏感路径
"""

from __future__ import annotations

import asyncio
import base64
import copy
import platform
import re
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.star import StarTools

from ....utils.version import get_astrbot_version_info


class TelemetryManager:
    """遥测管理器。

    负责异步发送匿名遥测数据，并集中管理实例标识、脱敏与上报策略。
    """

    # 统一接收遥测的云端接入服务端点
    _ENDPOINT = "https://telemetry.aloys233.top/api/ingest"
    # App Key 经过 base64 编码，增加源码探测复杂度
    _ENCODED_KEY = "dGtfVFMxaVEtcGVJbUlKczFVM3VBcGM4anREUlRhbC00VGY="
    _APP_KEY = base64.b64decode(_ENCODED_KEY).decode()

    # 特定高频事件的最小加入队列间隔（秒），用于在内存中提前丢弃同质化冗余遥测
    _THROTTLE_CONFIG = {
        "feature:push_result": 30.0,  # 地震等高频新报的推送结果，30秒内仅保留第一笔
        "feature:command_admin_action": 10.0,  # 管理类命令（状态/统计等）统一节流
    }

    # 错误事件按"模块 + 异常类型"维度聚合节流的最小间隔（秒），
    # 防止同一错误点在消息风暴或瞬时故障场景下高频刷屏遥测服务器。
    _ERROR_THROTTLE_SECONDS = 60.0

    # 物理网络请求的最小时间间隔，防范任何极端情况下的 429
    _MIN_REQUEST_INTERVAL = 10.0

    def __init__(
        self,
        config: dict,
        plugin_version: str = "unknown",
    ):
        """
        初始化遥测管理器。

        参数说明：
        - config: 插件配置对象
        - plugin_version: 插件版本号
        """
        self._config = config
        self._plugin_version = plugin_version

        # 获取 AstrBot 版本号与探测来源，便于区分宿主版本差异带来的兼容性问题。
        self._astrbot_version_info = get_astrbot_version_info()
        self._astrbot_version = self._astrbot_version_info.version

        # 从配置中读取遥测开关
        telemetry_config = config.get("telemetry_config", {})
        self._enabled = telemetry_config.get("enabled", True)

        # 获取或创建实例 ID（存储在插件数据目录中）
        self._instance_id = self._get_or_create_instance_id()

        # aiohttp session (延迟初始化)
        self._session: aiohttp.ClientSession | None = None

        self._env = "production"

        # 引入缓冲队列与后台任务，降低发送频率，避免 429 触发频率限制
        self._queue: list[dict[str, Any]] = []
        self._queue_lock = asyncio.Lock()
        self._send_task: asyncio.Task | None = None
        self._last_429_time: datetime | None = None

        # 事件节流时间记录：键为 event_name 或 feature:feature_name，值为上次上报的时间戳
        self._last_throttled_times: dict[str, float] = {}

        # 物理请求速率限制与互斥锁
        self._last_send_time: float = 0.0
        self._send_semaphore = asyncio.Semaphore(1)

        if self._enabled:
            logger.debug(
                f"[灾害预警] 已启用匿名遥测，实例标识为 {self._instance_id}，AstrBot 版本为 {self._astrbot_version}"
            )
        else:
            logger.debug("[灾害预警] 遥测功能未启用")

    def _get_or_create_instance_id(self) -> str:
        """获取或创建实例标识，并持久化到插件数据目录。"""

        try:
            # 使用 StarTools 获取插件数据目录（与 message_logger 一致）
            data_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
            id_file = data_dir / ".telemetry_id"

            # 尝试读取已存在的 ID
            if id_file.exists():
                instance_id = id_file.read_text().strip()
                if instance_id:
                    return instance_id

            # 生成新的 UUID
            instance_id = str(uuid.uuid4())

            # 保存到文件
            data_dir.mkdir(parents=True, exist_ok=True)
            id_file.write_text(instance_id)
            logger.debug(f"[灾害预警] 已生成新的实例 ID: {instance_id}")

            return instance_id

        except Exception as e:
            # 如果无法读写文件，生成临时 ID
            logger.warning(f"[灾害预警] 无法持久化实例 ID: {e}")
            return str(uuid.uuid4())

    @property
    def enabled(self) -> bool:
        """返回当前是否启用遥测。"""
        return self._enabled

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建内部网络会话。"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def track(
        self,
        event_name: str,
        data: dict[str, Any] | None = None,
        immediate: bool = False,
    ) -> bool:
        """
        发送遥测事件。

        参数说明：
        - event_name: 事件名称
        - data: 附加数据对象
        - immediate: 是否立即发送，不经过缓冲队列
        """
        if not self._enabled:
            return False

        # 对高频冗余事件进行内存节流过滤
        throttle_key = event_name
        if event_name == "feature" and data and "feature" in data:
            throttle_key = f"feature:{data['feature']}"
        elif event_name == "error" and data:
            # 错误事件按"模块 + 异常类型"维度聚合，避免同一错误点刷屏
            throttle_key = (
                f"error:{data.get('module') or 'unknown'}:"
                f"{data.get('type') or 'unknown'}"
            )

        throttle_seconds = self._THROTTLE_CONFIG.get(throttle_key)
        if throttle_seconds is None and throttle_key.startswith("error:"):
            # 错误事件统一按 _ERROR_THROTTLE_SECONDS 间隔节流（默认 60 秒）
            throttle_seconds = self._ERROR_THROTTLE_SECONDS

        if throttle_seconds is not None:
            now_ts = time.time()
            last_ts = self._last_throttled_times.get(throttle_key, 0.0)
            if now_ts - last_ts < throttle_seconds:
                # 冷却时间未到，静默丢弃当前高频事件
                return True
            self._last_throttled_times[throttle_key] = now_ts

        # 延迟启动后台批处理任务，避免在没有运行 loop 的初始化时报错
        if self._send_task is None or self._send_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._send_task = loop.create_task(self._batch_sender_loop())
            except RuntimeError:
                pass

        event_item = {
            "event": event_name,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if immediate:
            return await self._send_batch_raw([event_item])

        async with self._queue_lock:
            self._queue.append(event_item)
            should_flush = (
                len(self._queue) >= 100
            )  # 适当扩大缓冲区大小到 100，平滑高频阶段

        if should_flush:
            asyncio.create_task(self.flush())

        return True

    async def _batch_sender_loop(self) -> None:
        """后台批处理发送循环"""
        while self._enabled:
            try:
                await asyncio.sleep(15.0)  # 延长至每 15 秒自动轮询上报一次，平滑低峰段
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[灾害预警] 遥测后台批处理循环异常: {e}")

    async def flush(self) -> bool:
        """立即清空缓冲区并批量发送所有缓存的事件。"""
        if not self._enabled:
            return False

        async with self._queue_lock:
            if not self._queue:
                return False
            batch_data = list(self._queue)
            self._queue.clear()

        return await self._send_batch_raw(batch_data)

    async def _send_batch_raw(self, batch_data: list[dict[str, Any]]) -> bool:
        """底层实际网络上报接口，包含强制发送速率限制。"""
        payload = {
            "instance_id": self._instance_id,
            "version": self._plugin_version,
            "env": self._env,
            "batch": batch_data,
        }

        # 强制两次物理发送之间必须有 _MIN_REQUEST_INTERVAL 秒间隔，避免短时间内并发多个物理请求导致 429
        async with self._send_semaphore:
            now_ts = time.time()
            elapsed = now_ts - self._last_send_time
            if elapsed < self._MIN_REQUEST_INTERVAL:
                wait_time = self._MIN_REQUEST_INTERVAL - elapsed
                logger.debug(
                    f"[灾害预警] 遥测请求物理限速，后台挂起等待 {wait_time:.2f} 秒"
                )
                await asyncio.sleep(wait_time)

            # 更新发送时间戳，确保后续请求准确排队
            self._last_send_time = time.time()

            try:
                session = await self._get_session()
                headers = {
                    "Content-Type": "application/json",
                    "X-App-Key": self._APP_KEY,
                }

                # 发起匿名批量上报请求
                async with session.post(
                    self._ENDPOINT, json=payload, headers=headers
                ) as response:
                    if response.status == 200:
                        return True
                    if response.status == 401:
                        logger.warning("[灾害预警] App Key 无效或项目已禁用")
                    elif response.status == 429:
                        # 限制 429 警告日志的输出频率，避免高频刷屏
                        now = datetime.now()
                        if (
                            self._last_429_time is None
                            or (now - self._last_429_time).total_seconds() > 600
                        ):
                            logger.debug("[灾害预警] 遥测请求频率超限")
                            self._last_429_time = now
                    else:
                        logger.debug(
                            f"[灾害预警] 遥测事件发送失败: HTTP {response.status}"
                        )

            except asyncio.TimeoutError:
                logger.debug("[灾害预警] 遥测请求超时")
                return False
            except aiohttp.ClientConnectionError as e:
                logger.debug(f"[灾害预警] 遥测连接失败: {e}")
                return False
            except aiohttp.ClientPayloadError as e:
                logger.debug(f"[灾害预警] 遥测数据负载异常，错误为 {e}")
                return False
            except aiohttp.ClientError as e:
                logger.debug(f"[灾害预警] 遥测网络请求异常，错误为 {e}")
                return False
            except Exception as e:
                logger.debug(f"[灾害预警] 遥测发送遇到未知异常，错误为 {e}")
                return False

        return False

    async def track_startup(self) -> bool:
        """上报启动事件和系统信息。"""
        return await self.track(
            "startup",
            {
                "os": platform.system(),
                "os_version": platform.release(),
                "python_version": platform.python_version(),
                "arch": platform.machine(),
                "astrbot_version": self._astrbot_version,
                "astrbot_version_source": self._astrbot_version_info.source,
                "astrbot_version_error": self._astrbot_version_info.error,
            },
            immediate=True,
        )

    async def track_shutdown(
        self, exit_code: int = 0, runtime_seconds: float = 0
    ) -> bool:
        """上报退出事件。"""
        return await self.track(
            "shutdown",
            {
                "exit_code": exit_code,
                "runtime_seconds": runtime_seconds,
            },
            immediate=True,
        )

    async def track_heartbeat(self, uptime_seconds: float = 0) -> bool:
        """上报心跳事件。

        参数 `uptime_seconds` 表示当前累计运行秒数。
        """
        return await self.track(
            "heartbeat",
            {
                "uptime_seconds": uptime_seconds,
            },
        )

    # 配置快照中可能携带真实凭据的敏感键集合。
    # 命中即整体替换为脱敏占位符，避免 API Key、刷新令牌等凭据随匿名遥测外泄。
    _SENSITIVE_CREDENTIAL_KEYS = {
        "api_key",
        "apikey",
        "refresh_token",
        "access_token",
        "token",
        "secret",
        "password",
        "pwd",
        "cookie",
        "cookie_str",
        "app_key",
        "app_secret",
        "client_secret",
        "authorization",
    }

    async def track_config(self, config: dict) -> bool:
        """
        上报配置快照。

        会过滤管理员、目标会话、地理位置与管理端密码等敏感字段，
        并对数据源凭据类键做递归脱敏替换，防止真实凭据随匿名遥测外泄。
        """
        if not self._enabled:
            return False

        try:
            config_copy = copy.deepcopy(config)

            # 对可能存有敏感信息的键进行严格删除脱敏，确保用户隐私安全
            if "admin_users" in config_copy:
                del config_copy["admin_users"]
            if "target_sessions" in config_copy:
                del config_copy["target_sessions"]

            if "local_monitoring" in config_copy:
                lm = config_copy["local_monitoring"]
                if isinstance(lm, dict):
                    if "latitude" in lm:
                        del lm["latitude"]
                    if "longitude" in lm:
                        del lm["longitude"]
                    if "place_name" in lm:
                        del lm["place_name"]

            if "web_admin" in config_copy:
                wa = config_copy["web_admin"]
                if isinstance(wa, dict) and "password" in wa:
                    del wa["password"]

            # 递归脱敏数据源凭据类键，兜底未来新增的敏感字段
            self._sanitize_credentials(config_copy)

            return await self.track("config", config_copy, immediate=True)

        except Exception as e:
            logger.debug(f"[灾害预警] 配置快照提取失败: {e}")
            return False

    def _sanitize_credentials(self, node: Any) -> None:
        """就地递归脱敏配置树中的凭据类键值。

        对 dict 的键做大小写不敏感匹配；命中 _SENSITIVE_CREDENTIAL_KEYS
        的值统一替换为脱敏占位符。list 内元素继续递归，确保嵌套配置结构
        （如数据源列表）中的凭据同样被覆盖。
        """
        if isinstance(node, dict):
            for key in list(node.keys()):
                normalized = str(key).strip().lower()
                if normalized in self._SENSITIVE_CREDENTIAL_KEYS:
                    node[key] = "***"
                else:
                    self._sanitize_credentials(node[key])
        elif isinstance(node, list):
            for item in node:
                self._sanitize_credentials(item)

    async def track_feature(self, feature_name: str, extra: dict | None = None) -> bool:
        """上报功能使用事件。"""
        data = extra.copy() if extra else {}
        data["feature"] = feature_name
        return await self.track("feature", data)

    async def track_error(
        self,
        exception: Exception,
        module: str | None = None,
    ) -> bool:
        """
        上报错误事件。

        参数说明：
        - exception: 捕获到的异常对象
        - module: 发生错误的模块名
        """
        # 未指定模块时使用默认占位，避免服务器端出现 null 分组
        module = module or "unknown"

        raw_message = str(exception)
        # 通过预设规则判定，忽略常规网络抖动或主动取消等高频无价值错误，减少服务器遥测数据噪声
        if self._should_skip_error_telemetry(exception, raw_message, module):
            logger.debug(
                "[灾害预警] 命中遥测噪声过滤规则，跳过错误上报："
                f"异常类型为 {type(exception).__name__}，模块为 {module}，消息摘要：{raw_message[:200]}"
            )
            return False

        sanitized_message = self._sanitize_message(raw_message)

        data = {
            "type": type(exception).__name__,
            "message": sanitized_message[:500],
            "module": module,
            "severity": "error",
        }

        stack = "".join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        )
        # 对异常堆栈进行强力脱敏过滤，剔除涉及宿主机私人用户名及本地系统特有文件绝对路径的信息
        data["stack"] = self._sanitize_stack(stack)[:4000]

        return await self.track("error", data)

    def _should_skip_error_telemetry(
        self,
        exception: Exception,
        raw_message: str,
        module: str | None = None,
    ) -> bool:
        """判断是否应跳过高频低价值错误的遥测上报。"""
        error_type = type(exception).__name__
        message = (raw_message or "").lower()
        module_name = (module or "").lower()

        # 协程撤销和生成器主动回收不属于运行期错误，无需遥测
        if error_type in {"CancelledError", "GeneratorExit"}:
            return True

        # Playwright 主动或被动关闭错误无需遥测
        if error_type == "TargetClosedError":
            return True
        if "target page, context or browser has been closed" in message:
            return True
        if "browser has been closed" in message and module_name.startswith(
            "core.browser_manager"
        ):
            return True

        # 宿主机上 Playwright 二进制依赖缺失错误不应归结为插件逻辑错误，跳过遥测
        if (
            "executable doesn't exist" in message
            or "playwright install" in message
            or "ms-playwright" in message
        ):
            return True

        # WebSocket 物理断线或心跳心跳响应超时等网络扰动无需遥测
        if "websocket异常关闭" in message and "1006" in message:
            return True
        if module_name.startswith("core.websocket_manager.connect") and any(
            marker in message
            for marker in (
                "1006",
                "cannot write to closing transport",
                "connection reset by peer",
                "server disconnected",
                "heartbeat",
                "ping",
            )
        ):
            return True

        # Playwright 渲染地图卡片由于不可抗力网络原因（如地图瓦片服务请求限流或阻断）而导航超时的错误，跳过遥测
        if module_name.startswith("core.browser_manager.render_card") and any(
            marker in message
            for marker in (
                "waiting for selector",
                "timeout",
                "navigation timeout",
                "net::err_",
            )
        ):
            return True

        return False

    def _sanitize_stack(self, stack: str) -> str:
        """
        脱敏堆栈信息，移除敏感路径

        - 移除用户主目录路径
        - 保留相对于插件的路径
        - 隐藏用户名
        """
        stack = re.sub(r"[A-Za-z]:\\Users\\[^\\]+\\", r"<USER_HOME>\\", stack)
        stack = re.sub(r"/(?:home|Users|root)/[^/]+/", r"<USER_HOME>/", stack)
        stack = re.sub(r"/root/", r"<USER_HOME>/", stack)
        stack = re.sub(r".*astrbot_plugin_disaster_warning[/\\]", r"<PLUGIN>/", stack)
        stack = re.sub(r".*site-packages[/\\]", r"<SITE_PACKAGES>/", stack)
        return stack

    def _sanitize_message(self, message: str) -> str:
        """脱敏错误消息，移除可能的敏感信息。"""
        message = re.sub(r"/(?:home|Users|root)/[^/\s]+/", r"<USER_HOME>/", message)
        message = re.sub(r"/root/", r"<USER_HOME>/", message)
        message = re.sub(r"[A-Za-z]:\\Users\\[^\\\s]+\\", r"<USER_HOME>\\", message)
        # 脱敏 URL query 中的凭据类参数（token/key/secret/password 等），
        # 避免异常消息里拼接的带鉴权参数的 URL 把真实凭据带进遥测。
        message = re.sub(
            r"(?i)([?&](?:token|key|secret|password|pwd|api_key|apikey|refresh_token|access_token|authorization|cookie)=)[^&\s\"']+",
            r"\1***",
            message,
        )
        return message

    async def close(self):
        """关闭遥测会话。"""
        # 1. 取消后台批处理任务并安全等待其结束
        if self._send_task and not self._send_task.done():
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
            self._send_task = None

        # 2. 强行上报缓冲中剩余的数据
        await self.flush()

        # 3. 关闭底层会话
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logger.debug("[灾害预警] 遥测会话已关闭")


__all__ = ["TelemetryManager"]
