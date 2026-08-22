"""
插件命令层支持服务。
负责管理员校验、引用回复构造、配置 Schema 缓存与配置展示翻译等横切能力，
减少 main.DisasterWarningPlugin 中重复的命令辅助逻辑。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp


class PluginCommandSupportService:
    """插件命令支持服务。"""

    def __init__(self, plugin):
        """初始化插件命令支持服务。"""
        self.plugin = plugin

    async def is_plugin_admin(self, event) -> bool:
        """检查用户是否为插件管理员或 Bot 管理员。"""
        # 如果是宿主机器人全局管理员，直接拥有权限
        if event.is_admin():
            return True

        # 插件管理员名单来自插件配置，作为宿主管理员权限之外的补充入口
        sender_id = event.get_sender_id()
        plugin_admins = self.plugin.config.get("admin_users", [])
        return sender_id in plugin_admins

    @staticmethod
    def with_quote_reply(event, chain: list[Any]) -> list[Any]:
        """为消息链添加引用回复段（若可用）。"""
        message_obj = getattr(event, "message_obj", None)
        message_id = getattr(message_obj, "message_id", None) if message_obj else None
        # 如果能拿到原始消息的消息ID，则在消息链头部插入回复节点，用于生成“回复”气泡效果
        if not message_id:
            return chain
        return [Comp.Reply(id=str(message_id)), *chain]

    def get_config_schema(self) -> dict[str, Any]:
        """获取并缓存配置 Schema。"""
        # 配置结构只需读取一次，后续命令查询场景直接复用缓存以减少文件读取。
        if self.plugin._config_schema is not None:
            return self.plugin._config_schema

        schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
        if schema_path.exists():
            with schema_path.open(encoding="utf-8") as f:
                self.plugin._config_schema = json.load(f)
        else:
            self.plugin._config_schema = {}
        return self.plugin._config_schema

    # 敏感字段名集合：在配置查看指令中这些字段的值会被脱敏为 ***
    # 包含 schema 中标记了 hidden 的字段，以及虽未标记但含敏感信息的字段
    _SENSITIVE_KEY_NAMES = frozenset(
        {
            "password",
            "refresh_token",
            "secret",
            "token",
            "api_key",
            "apikey",
            "private_key",
            "access_key",
            "refresh_token",
            "playwright_server_url",
        }
    )

    @classmethod
    def _is_sensitive_key(cls, key: str, item_schema: dict[str, Any]) -> bool:
        """判断字段是否为敏感字段。

        判定规则：schema 中标记 hidden=true，或字段名命中敏感字段集合。
        """
        if item_schema.get("hidden") is True:
            return True
        # 去除下划线和中划线后做大小写不敏感匹配
        normalized_key = key.replace("-", "_").lower()
        return normalized_key in cls._SENSITIVE_KEY_NAMES

    @staticmethod
    def _mask_sensitive_value(value: Any) -> Any:
        """对敏感字段值进行脱敏处理。

        非空字符串值替换为 ***，空值保持原样展示，
        非字符串类型（如 bool/int）不脱敏。
        """
        if isinstance(value, str) and value.strip():
            return "***"
        return value

    def translate_config_recursive(
        self,
        config_item: Any,
        schema_item: dict[str, Any] | None,
    ) -> Any:
        """递归将配置键名转换为中文描述，并对敏感字段脱敏。"""
        if isinstance(config_item, list):
            return [
                self.translate_config_recursive(item, schema_item)
                if isinstance(item, dict)
                else item
                for item in config_item
            ]

        if not isinstance(config_item, dict):
            return config_item

        translated: dict[str, Any] = {}
        schema_item = schema_item or {}
        # 兼容旧版本中未注册在 schema 里的局部特殊键名
        legacy_alias_map = {
            "provinces": "省份白名单(旧版兼容)",
            "province": "省份(旧版兼容)",
            "push_enable": "单会话推送开关(旧版字段)",
        }
        for key, value in config_item.items():
            # 每个配置项都优先使用结构定义中的中文说明；schema 外旧字段走兼容别名，避免展示错位
            item_schema = (
                schema_item.get(key, {}) if isinstance(schema_item, dict) else {}
            )
            description = item_schema.get("description", legacy_alias_map.get(key, key))

            # 敏感字段脱敏：schema 标记 hidden 或字段名命中敏感字段集合时替换为 ***
            if self._is_sensitive_key(key, item_schema):
                translated[description] = self._mask_sensitive_value(value)
                continue

            if isinstance(value, dict):
                # 嵌套配置继续按子结构递归翻译，保持整棵配置树的展示风格一致
                sub_schema = item_schema.get("items", {})
                translated[description] = self.translate_config_recursive(
                    value, sub_schema
                )
            elif isinstance(value, list):
                translated[description] = [
                    self.translate_config_recursive(item, item_schema.get("items", {}))
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                translated[description] = value

        return translated
