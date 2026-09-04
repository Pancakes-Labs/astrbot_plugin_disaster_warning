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
    _ENDPOINT = "https://plugincenter.aloys23.link/api/ingest"
    # App Key 经过 base64 编码，增加源码探测复杂度
    _ENCODED_KEY = "dGtfbEJ6a2k0eDhBZE40ZFVCVVhRVnpmYnRGT3NWdVYyTmE="
    _APP_KEY = base64.b64decode(_ENCODED_KEY).decode()

    # 特定高频事件的最小加入队列间隔（秒），用于在内存中提前丢弃同质化冗余遥测
    # 节流键规则：
    # - feature 事件默认键为 feature:{feature}；
    # - 若事件携带 action 字段，则键为 feature:{feature}:{action}，未命中时回退到
    #   feature:{feature}，从而允许同一 feature 下按 action 维度分别节流（如管理类命令）。
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

        # 后台批处理循环唤醒事件：close() 置位 _closed 后 set 该事件，
        # 使循环从可中断等待中立即唤醒退出，而不是取消在途发送批次。
        self._wake_event = asyncio.Event()

        # 在途发送标志：flush() 取出 batch_data 到 _send_batch_raw 完成期间置位，
        # 供关闭复查放行"close 前已取出、正在排队发送"的批次，避免数据丢失。
        self._sending = False

        # 事件节流时间记录：键为 event_name 或 feature:feature_name，值为上次上报的时间戳
        self._last_throttled_times: dict[str, float] = {}

        # 物理请求速率限制与互斥锁
        self._last_send_time: float = 0.0
        self._send_semaphore = asyncio.Semaphore(1)

        # 关闭标志：close() 置位后拒绝任何新事件入队/发送，
        # 防止插件重载后残留的 track_* 调用重建 aiohttp 会话或后台批处理任务。
        self._closed = False

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
        bypass_rate_limit: bool = False,
    ) -> bool:
        """
        发送遥测事件。

        参数说明：
        - event_name: 事件名称
        - data: 附加数据对象
        - immediate: 是否立即发送，不经过缓冲队列
        - bypass_rate_limit: 是否绕过物理发送最小间隔。仅用于关机/退出等关键路径
            （如 track_shutdown），避免被 _MIN_REQUEST_INTERVAL 限速等待阻塞资源清理。
        """
        if not self._enabled or self._closed:
            return False

        # 对高频冗余事件进行内存节流过滤
        throttle_key = event_name
        if event_name == "feature" and data and "feature" in data:
            feature_name = data["feature"]
            action = data.get("action")
            # 携带 action 字段时按 action 维度细化节流键，避免同一 feature 下
            # 多个操作互相挤占节流配额（如管理类命令各自独立计数）。
            throttle_key = (
                f"feature:{feature_name}:{action}"
                if action
                else f"feature:{feature_name}"
            )
        elif event_name == "error" and data:
            # 错误事件按"模块 + 异常类型"维度聚合，避免同一错误点刷屏
            throttle_key = (
                f"error:{data.get('module') or 'unknown'}:"
                f"{data.get('type') or 'unknown'}"
            )

        throttle_seconds = self._THROTTLE_CONFIG.get(throttle_key)
        if throttle_seconds is None and throttle_key.startswith("feature:"):
            # 细化后的 feature:{feature}:{action} 未命中时回退到 feature:{feature} 基础节流
            base_key = throttle_key.rsplit(":", 1)[0]
            throttle_seconds = self._THROTTLE_CONFIG.get(base_key)
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
            return await self._send_batch_raw(
                [event_item], bypass_rate_limit=bypass_rate_limit
            )

        async with self._queue_lock:
            # 获取锁后复查关闭状态：等待锁期间 close() 可能已完成，
            # 此时禁止再入队，避免关闭后的队列残留。
            if self._closed:
                return False
            self._queue.append(event_item)
            should_flush = (
                len(self._queue) >= 100
            )  # 适当扩大缓冲区大小到 100，平滑高频阶段

        if should_flush:
            asyncio.create_task(self.flush())

        return True

    async def _batch_sender_loop(self) -> None:
        """后台批处理发送循环"""
        # close() 置位 _closed 并通过 _wake_event 唤醒后，循环立即退出，
        # 避免残留后台任务继续轮询。等待可中断：close() 会 set _wake_event
        # 立即唤醒，无需等到 15 秒超时。
        while self._enabled and not self._closed:
            try:
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(), timeout=15.0
                    )  # 每 15 秒自动轮询上报一次，平滑低峰段
                    self._wake_event.clear()
                except asyncio.TimeoutError:
                    pass
                await self.flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[灾害预警] 遥测后台批处理循环异常: {e}")

    async def flush(
        self,
        bypass_rate_limit: bool = False,
        *,
        _allow_after_close: bool = False,
    ) -> bool:
        """立即清空缓冲区并批量发送所有缓存的事件。

        Args:
            bypass_rate_limit: 是否绕过物理发送最小间隔（只控制 _MIN_REQUEST_INTERVAL）。
            _allow_after_close: 私有标记，仅 close() 的兜底 flush 传入 True，
                允许在 _closed 置位后仍完成最后一次发送；其余调用一律拒绝。
        """
        if not self._enabled or (self._closed and not _allow_after_close):
            return False

        async with self._queue_lock:
            # 持锁后复查关闭状态：等待锁期间 close() 可能已完成。
            # 仅允许 close() 的兜底发送（_allow_after_close=True）绕过该检查。
            if self._closed and not _allow_after_close:
                return False
            if not self._queue:
                return False
            batch_data = list(self._queue)
            self._queue.clear()

        # 标记在途发送：close() 等待 _send_task 自然退出期间，
        # 此标志让 _send_batch_raw 的关闭复查放行"close 前已取出"的批次，
        # 避免取消在途发送导致 batch_data 丢失。
        self._sending = True
        try:
            return await self._send_batch_raw(
                batch_data,
                bypass_rate_limit=bypass_rate_limit,
                _allow_after_close=_allow_after_close,
            )
        finally:
            self._sending = False

    async def _send_batch_raw(
        self,
        batch_data: list[dict[str, Any]],
        *,
        bypass_rate_limit: bool = False,
        _allow_after_close: bool = False,
    ) -> bool:
        """底层实际网络上报接口，包含强制发送速率限制。

        Args:
            batch_data: 待上报的事件批。
            bypass_rate_limit: 是否绕过物理发送最小间隔（只控制 _MIN_REQUEST_INTERVAL）。
                用于关机/退出等关键路径（如 track_shutdown 与 close 兜底 flush），
                避免限速等待阻塞资源清理流程；其余日常发送一律保持限速，防范 429。
            _allow_after_close: 私有标记，仅 close() 的兜底发送传入 True，
                允许在 _closed 置位后仍执行发送（会话尚未关闭）；
                track_shutdown 等其它路径不传，关闭后一律拒绝。
        """
        payload = {
            "instance_id": self._instance_id,
            "version": self._plugin_version,
            "env": self._env,
            "batch": batch_data,
        }

        # 强制两次物理发送之间必须有 _MIN_REQUEST_INTERVAL 秒间隔，
        # 避免短时间内并发多个物理请求导致 429（关机路径可显式绕过）。
        async with self._send_semaphore:
            now_ts = time.time()
            elapsed = now_ts - self._last_send_time
            if not bypass_rate_limit and elapsed < self._MIN_REQUEST_INTERVAL:
                wait_time = self._MIN_REQUEST_INTERVAL - elapsed
                logger.debug(
                    f"[灾害预警] 遥测请求物理限速，后台挂起等待 {wait_time:.2f} 秒"
                )
                await asyncio.sleep(wait_time)

            # 获取信号量并完成限速等待后复查关闭状态：
            # 等待期间 close() 可能已完成，此时禁止继续发送，
            # 避免 _get_session() 重新创建已关闭的 aiohttp 会话。
            # 放行条件（满足其一）：
            # - close() 的兜底发送（_allow_after_close=True 由 close 传入）；
            # - 本批次在 close 前置位了 _sending（flush 已取出、在途发送中），
            #   close() 正等待其完成，不允许丢弃。
            # track_shutdown 等其它路径不传该标记且非在途，关闭后一律拒绝。
            if self._closed and not (_allow_after_close or self._sending):
                return False

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
                            logger.warning("[灾害预警] 遥测请求频率超限")
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
                logger.warning(f"[灾害预警] 遥测网络请求异常，错误为 {e}")
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
        """上报退出事件。

        退出事件走立即发送，并绕过物理最小间隔限速，
        避免关机/重载路径被 _MIN_REQUEST_INTERVAL 限速等待阻塞资源清理。
        """
        return await self.track(
            "shutdown",
            {
                "exit_code": exit_code,
                "runtime_seconds": runtime_seconds,
            },
            immediate=True,
            bypass_rate_limit=True,
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
    # 集合内统一存"去空白、转小写、去除分隔符"后的规范化键名，
    # 匹配时经 _normalize_credential_key 规范化，从而同时覆盖 snake_case /
    # camelCase / kebab-case 等命名风格（如 refresh_token / refreshToken）。
    # 命中即整体替换为脱敏占位符，避免 API Key、刷新令牌等凭据随匿名遥测外泄。
    _SENSITIVE_CREDENTIAL_KEYS = {
        "apikey",
        "refreshtoken",
        "accesstoken",
        "token",
        "secret",
        "password",
        "pwd",
        "cookie",
        "cookiestr",
        "appkey",
        "appsecret",
        "clientsecret",
        "authorization",
    }

    # 配置快照中携带用户/会话标识的敏感键集合（存储规范化的键名）。
    # 命中即**整体删除**，不做统计聚合，避免群号、会话 ID、管理员标识等
    # 个人可识别信息随匿名遥测外泄。
    # 注意：这些键不属于凭据类，_sanitize_credentials 的占位符替换无法覆盖，
    # 因此统一在 _sanitize_credentials 的递归遍历中识别并删除（含嵌套结构）。
    _SENSITIVE_IDENTITY_KEYS = {
        "adminusers",
        "targetsessions",
        "offlinenotificationsessions",
    }

    # URL query 中凭据类参数的键名模式（允许 -/_ 分隔符，大小写不敏感），
    # 用于脱敏异常消息与堆栈中拼接的带鉴权参数 URL，
    # 同时覆盖 snake_case / camelCase / kebab-case 变体（如 refreshToken、apiKey）。
    _CREDENTIAL_URL_KEY_PATTERN = (
        r"(?:token|key|secret|password|pwd|cookie|authorization|"
        r"api[-_]?key|refresh[-_]?token|access[-_]?token|app[-_]?key|"
        r"app[-_]?secret|client[-_]?secret|cookie[-_]?str)"
    )

    @staticmethod
    def _normalize_credential_key(key: str) -> str:
        """规范化凭据键：去空白、转小写、去除 _ - 等分隔符，实现命名风格无关匹配。"""
        return re.sub(r"[\s_\-]+", "", str(key).strip().lower())

    async def track_config(self, config: dict) -> bool:
        """
        上报配置快照。

        会过滤敏感字段并对数据源凭据类键做递归脱敏替换，防止真实凭据随匿名遥测外泄。
        """
        if not self._enabled:
            return False

        try:
            config_copy = copy.deepcopy(config)

            # 先删除地理位置与管理端密码等顶层敏感字段
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

            # 递归脱敏：删除用户/会话标识键（含嵌套结构），
            # 并对数据源凭据类键做占位符替换，兜底未来新增的敏感字段。
            self._sanitize_credentials(config_copy)

            return await self.track("config", config_copy, immediate=True)

        except Exception as e:
            logger.debug(f"[灾害预警] 配置快照提取失败: {e}")
            return False

    def _sanitize_credentials(self, node: Any) -> None:
        """就地递归脱敏配置树中的敏感键值。

        注意：本方法会**就地修改**传入的配置节点。调用方若需保留原始凭据值
        （例如后续仍会复用同一配置字典），请先自行 copy.deepcopy 再传入，
        以免原始凭据在内存中被覆盖为脱敏占位符。

        脱敏规则：
        1. dict 的键做"去分隔符 + 大小写不敏感"匹配；
        2. 命中 _SENSITIVE_CREDENTIAL_KEYS 的值统一替换为脱敏占位符；
        3. dict 中的字符串值（即使键本身不敏感）应用 URL 查询凭据脱敏正则并
           回写，覆盖形如 {"endpoint": "https://api.example/?refreshToken=secret"}
           这类在普通键下携带带鉴权 URL 的场景；
        4. list 内元素继续递归，确保嵌套配置结构（如数据源列表）同样被覆盖。
        """
        if isinstance(node, dict):
            for key in list(node.keys()):
                normalized = self._normalize_credential_key(key)
                if normalized in self._SENSITIVE_IDENTITY_KEYS:
                    # 用户/会话标识键整体删除，防止群号、会话 ID 等随遥测外泄
                    del node[key]
                elif normalized in self._SENSITIVE_CREDENTIAL_KEYS:
                    node[key] = "***"
                elif isinstance(node[key], str):
                    # 字符串值应用 URL 查询凭据脱敏（与 _sanitize_message 同一规则），
                    # 并回写到原位置，确保就地修改生效。
                    if "=" in node[key] and ("?" in node[key] or "&" in node[key]):
                        node[key] = re.sub(
                            rf"(?i)([?&](?:{self._CREDENTIAL_URL_KEY_PATTERN})=)[^&\s\"']+",
                            r"\1***",
                            node[key],
                        )
                    else:
                        self._sanitize_credentials(node[key])
                else:
                    self._sanitize_credentials(node[key])
        elif isinstance(node, list):
            # 用索引遍历，使 list 中的字符串元素也能就地脱敏回写。
            for idx, item in enumerate(node):
                if isinstance(item, str):
                    if "=" in item and ("?" in item or "&" in item):
                        node[idx] = re.sub(
                            rf"(?i)([?&](?:{self._CREDENTIAL_URL_KEY_PATTERN})=)[^&\s\"']+",
                            r"\1***",
                            item,
                        )
                else:
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
        # 对异常堆栈进行强力脱敏过滤：
        # 1. 先剔除宿主机私人用户名及本地系统特有文件绝对路径信息（_sanitize_stack）；
        # 2. 再复用 _sanitize_message 的 URL 凭据正则——traceback.format_exception
        #    会把原始异常消息（可能拼接了带鉴权参数的 URL）重新嵌入 stack，
        #    仅靠 _sanitize_stack 无法覆盖，必须再做一次 URL 凭据脱敏。
        data["stack"] = self._sanitize_message(self._sanitize_stack(stack))[:4000]

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
        # 键名模式允许 -/_ 分隔符且大小写不敏感，同时覆盖 snake_case /
        # camelCase / kebab-case 变体（如 refreshToken、accessToken、apiKey）。
        message = re.sub(
            rf"(?i)([?&](?:{self._CREDENTIAL_URL_KEY_PATTERN})=)[^&\s\"']+",
            r"\1***",
            message,
        )
        return message

    async def close(self):
        """关闭遥测会话（幂等：重复调用直接返回）。

        关闭开始后：
        - _closed 置位，track() 会拒绝任何新事件，避免重建 aiohttp 会话与后台任务；
        - 通过 _wake_event 唤醒后台批处理循环并等待其**自然退出**，
          不取消在途发送批次——若取消恰逢 flush() 已取出 batch_data，
          会中断 _send_batch_raw() 导致该批次事件丢失；
        - 缓冲中剩余数据仍做最后一次兜底 flush（绕过物理限速，避免阻塞会话关闭）。
        """
        # 幂等保护：重复关闭直接返回，防止插件重载期间并发调用重复清理。
        if self._closed:
            return
        self._closed = True

        # 1. 唤醒后台批处理循环并等待其自然退出。
        #    循环在 _closed 置位后通过 _wake_event 立即唤醒并退出；
        #    若循环正在发送在途批次（_sending=True），则等待该批次发送完成，
        #    而不是 cancel 中断（避免已取出未确认的 batch_data 丢失）。
        self._wake_event.set()
        if self._send_task and not self._send_task.done():
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
            self._send_task = None

        # 2. 强行上报缓冲中剩余的数据（关机路径绕过物理限速并允许关闭后发送，
        #    避免阻塞会话关闭；仅在 close() 内部使用 _allow_after_close 标记）。
        try:
            await self.flush(bypass_rate_limit=True, _allow_after_close=True)
        except Exception as flush_err:
            logger.debug(f"[灾害预警] 关闭时兜底发送遥测失败（已忽略）: {flush_err}")

        # 3. 关闭底层会话
        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception as session_err:
                logger.debug(f"[灾害预警] 关闭遥测会话失败（已忽略）: {session_err}")
            finally:
                self._session = None
            logger.debug("[灾害预警] 遥测会话已关闭")


__all__ = ["TelemetryManager"]
