"""
配置访问服务。
集中封装常见配置分组的读取逻辑，作为运行时配置读取的主入口。
"""

from __future__ import annotations

from typing import Any


class ConfigAccessor:
    """统一配置访问服务。

    负责把原始配置字典包装成一组稳定访问方法，避免上层重复编写键名和类型兜底逻辑。
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """初始化配置访问器。"""
        # 保存传入的配置字典，若为空则默认使用空字典
        self.config = config or {}

    def web_admin_config(self) -> dict[str, Any]:
        """获取管理端配置分组。"""
        # 读取 web_admin 节点，并确保返回字典类型
        value = self.config.get("web_admin", {})
        return value if isinstance(value, dict) else {}

    def web_admin_password(self) -> str:
        """获取管理端密码字符串。"""
        password = self.web_admin_config().get("password", "")
        return password if isinstance(password, str) else ""

    def message_format_config(self) -> dict[str, Any]:
        """获取消息展示格式配置。"""
        value = self.config.get("message_format", {})
        return value if isinstance(value, dict) else {}

    def event_deduplication_config(self) -> dict[str, Any]:
        """获取事件去重配置。"""
        # 获取事件去重设置，用于在过滤阶段识别重复推送
        value = self.config.get("event_deduplication", {})
        return value if isinstance(value, dict) else {}

    def weather_config(self) -> dict[str, Any]:
        """获取气象相关配置。"""
        value = self.config.get("weather_config", {})
        return value if isinstance(value, dict) else {}

    def typhoon_config(self) -> dict[str, Any]:
        """获取台风相关配置。"""
        value = self.config.get("typhoon_config", {})
        return value if isinstance(value, dict) else {}

    def debug_config(self) -> dict[str, Any]:
        """获取调试相关配置。"""
        value = self.config.get("debug_config", {})
        return value if isinstance(value, dict) else {}

    def local_monitoring_config(self) -> dict[str, Any]:
        """获取本地监控配置。"""
        value = self.config.get("local_monitoring", {})
        return value if isinstance(value, dict) else {}

    def data_sources_config(self) -> dict[str, Any]:
        """获取数据源配置总表。"""
        # 获取全部已启用的数据源详细开关状态及参数
        value = self.config.get("data_sources", {})
        return value if isinstance(value, dict) else {}

    def strategies_config(self) -> dict[str, Any]:
        """获取策略配置分组。"""
        # 包含 CENC 融合、CWA 最大震度融合等多种高级推送策略配置
        value = self.config.get("strategies", {})
        return value if isinstance(value, dict) else {}

    def target_sessions(self) -> list[Any]:
        """获取目标会话列表。"""
        # 获取默认的消息群发或私聊推送会话 ID 列表
        value = self.config.get("target_sessions", [])
        return value if isinstance(value, list) else []


__all__ = ["ConfigAccessor"]
