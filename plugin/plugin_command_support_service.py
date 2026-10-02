"""
插件命令层支持服务。
负责管理员校验、引用回复构造、配置 Schema 缓存与配置展示翻译等横切能力，
减少 main.DisasterWarningPlugin 中重复的命令辅助逻辑。
"""

from __future__ import annotations

import json
import math
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
            "login_key",
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

    # ------------------------------------------------------------------
    # /设置所在地 参数解析
    # ------------------------------------------------------------------

    # 生效范围关键字别名表（统一转小写后匹配）
    LOCATION_SCOPE_GLOBAL = "全局"
    LOCATION_SCOPE_CURRENT = "当前会话"
    LOCATION_SCOPE_SESSION = "指定会话"

    _LOCATION_SCOPE_ALIASES: dict[str, str] = {
        "全局": "全局",
        "全体": "全局",
        "所有": "全局",
        "global": "全局",
        "all": "全局",
        "当前会话": "当前会话",
        "当前": "当前会话",
        "本会话": "当前会话",
        "这个会话": "当前会话",
        "this": "当前会话",
        "current": "当前会话",
    }

    # 显式键值对别名表：用于消解「只填一个裸数字」时的经纬度歧义。
    # 支持 lat=39.9 / 纬度=39.9 / lon=116.4 / 经度=116.4 / 地名=北京 / 范围=当前会话
    _LOCATION_KEY_ALIASES: dict[str, str] = {
        "lat": "latitude",
        "latitude": "latitude",
        "纬度": "latitude",
        "lon": "longitude",
        "lng": "longitude",
        "longitude": "longitude",
        "经度": "longitude",
        "name": "place_name",
        "place": "place_name",
        "地名": "place_name",
        "位置": "place_name",
        "scope": "scope",
        "范围": "scope",
    }

    # 会话 UMO 中已知的 message_type 片段，用于识别“指定会话”。
    _SESSION_UMO_MESSAGE_TYPES: tuple[str, ...] = (
        "FriendMessage",
        "GroupMessage",
        "PrivateMessage",
        "GuildMessage",
    )

    @classmethod
    def _looks_like_session_umo(cls, token: str) -> bool:
        """判断参数是否为会话 UMO（用于区分“指定会话”与自定义地名）。"""
        if not token or ":" not in token:
            return False
        # 形态 1：命中已知 message_type，最可靠。
        for message_type in cls._SESSION_UMO_MESSAGE_TYPES:
            if f":{message_type}:" in token:
                return True
        # 形态 2：至少三段冒号分隔且各段均非空（platform:type:id）。
        parts = token.split(":")
        return len(parts) >= 3 and all(part.strip() for part in parts)

    @staticmethod
    def _try_parse_coordinate(token: str) -> float | None:
        """严格解析坐标数值；非有限值或非法文本返回 None。"""
        try:
            value = float(token)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    @classmethod
    def _split_explicit_kv(cls, token: str) -> tuple[str, str] | None:
        """尝试把 token 拆为显式键值对，返回 (规范键名, 原始值)。

        支持半角 = 与全角 ＝，键名经别名表归一；非键值对或未知键返回 None。
        """
        for separator in ("=", "＝"):
            if separator in token:
                raw_key, _, raw_value = token.partition(separator)
                canonical = cls._LOCATION_KEY_ALIASES.get(raw_key.strip().lower())
                if canonical is not None:
                    return canonical, raw_value.strip()
                return None
        return None

    @staticmethod
    def _looks_like_explicit_kv(token: str) -> bool:
        """判断 token 是否形如键值对（含半角 = 或全角 ＝）。

        与 _split_explicit_kv 互补：后者只对“键名可识别”的键值对返回结果，
        本方法用于识别“形如键值对但键名未知”的输入，避免其被静默当作地名。
        """
        return "=" in token or "＝" in token

    @staticmethod
    def _extract_kv_raw_key(token: str) -> str:
        """提取形如 键=值 的 token 中的原始键名（用于错误提示）。"""
        for separator in ("=", "＝"):
            if separator in token:
                return token.partition(separator)[0].strip()
        return token

    @classmethod
    def parse_set_location_args(
        cls,
        raw_args: list[Any],
        existing_lm: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """解析 /设置所在地 的原始参数。

        解析策略（按“类型嗅探”而非严格位置，以兼容任意参数留空）：

        1. 显式键值对 `lat=` / `纬度=` / `lon=` / `经度=` / `地名=` / `范围=`
           → 直接落到对应字段，语义无歧义（推荐用于只修正单个坐标的场景）；
        2. 含 `=` / `＝` 但键名未识别 → 直接报错。避免 `scop=当前会话`
           这类键名拼写错误被静默当作地名，进而误写为全局地名；
        3. 形如 UMO 的参数 → 生效范围「指定会话」，并记录目标会话；
        4. 命中范围别名的参数 → 生效范围「全局」或「当前会话」；
        5. 可解析为有限浮点数的参数 → 按出现顺序收集为裸坐标；
        6. 剩余参数 → 按顺序拼接为自定义地名。

        裸坐标分配规则（仅当未使用显式键值对时生效）：
        - 2 个裸坐标：第 1 个为纬度、第 2 个为经度，顺序疑似颠倒时自动纠偏；
        - 1 个裸坐标：绝对值大于 90 必为经度；否则结合 existing_lm
          判断用户意图（已有纬度缺经度 → 判为经度，反之判为纬度）；
          若仍无法判定，则默认纬度并置 ambiguous=True，
          由调用方提示用户改用 `lon=` 显式写法。

        Args:
            raw_args: 原始参数列表。
            existing_lm: 当前生效的 local_monitoring 配置（用于消歧，可为 None）。

        Returns:
            含 latitude / longitude / place_name / scope / scope_target /
            swapped / ambiguous / error 的字典；
            error 非空表示解析失败，调用方应回显用法提示。

        约束：纬度、经度、地名三者至少要提供一项。仅提供地名亦为合法用法，
        用于在坐标已配置的前提下单独修改地名（增量更新语义）。
        """
        result: dict[str, Any] = {
            "latitude": None,
            "longitude": None,
            "place_name": "",
            "scope": cls.LOCATION_SCOPE_GLOBAL,
            "scope_target": "",
            "swapped": False,
            "ambiguous": False,
            "error": "",
        }

        tokens = [
            str(arg).strip() for arg in raw_args if arg is not None and str(arg).strip()
        ]
        if not tokens:
            result["error"] = "缺少经纬度参数"
            return result

        explicit: dict[str, Any] = {}
        coordinates: list[float] = []
        place_parts: list[str] = []

        for token in tokens:
            # 1) 显式键值对：优先级最高，直接决定字段归属。
            kv = cls._split_explicit_kv(token)
            if kv is not None:
                key, raw_value = kv
                if key == "scope":
                    if not raw_value:
                        result["error"] = "生效范围的取值不能为空"
                        return result
                    scope = cls._LOCATION_SCOPE_ALIASES.get(raw_value.lower())
                    if scope is not None:
                        result["scope"] = scope
                    else:
                        result["error"] = f"无法识别的生效范围「{raw_value}」"
                        return result
                    continue
                if key == "place_name":
                    # 空地名必须显式报错：若放行空串，place_parts 会变为非空列表，
                    # 从而绕过下方“全部缺失”校验，最终返回空地名 + 空坐标，
                    # 命令侧会谎报更新成功却没有任何字段被写入。
                    if not raw_value:
                        result["error"] = "地名的取值不能为空"
                        return result
                    place_parts.append(raw_value)
                    continue
                if not raw_value:
                    result["error"] = f"{key} 的取值不能为空"
                    return result
                value = cls._try_parse_coordinate(raw_value)
                if value is None:
                    result["error"] = f"无法解析 {key} 的取值「{raw_value}」"
                    return result
                explicit[key] = value
                continue

            # 含等号但键名未识别：必须显式报错，不能作为地名静默接受。
            if cls._looks_like_explicit_kv(token):
                raw_key = cls._extract_kv_raw_key(token)
                if raw_key:
                    result["error"] = (
                        f"未识别的参数键「{raw_key}」，可用键："
                        "lat/latitude/纬度、lon/lng/longitude/经度、"
                        "地名/位置/name、范围/scope"
                    )
                else:
                    result["error"] = "无法识别的参数格式"
                return result

            # 2) 指定会话 UMO：含冒号且形态匹配，不作为地名候选。
            if cls._looks_like_session_umo(token):
                result["scope"] = cls.LOCATION_SCOPE_SESSION
                result["scope_target"] = token
                continue

            # 3) 生效范围关键字。
            scope = cls._LOCATION_SCOPE_ALIASES.get(token.lower())
            if scope is not None:
                result["scope"] = scope
                continue

            # 4) 裸坐标数值。
            value = cls._try_parse_coordinate(token)
            if value is not None:
                coordinates.append(value)
                continue

            # 5) 其余视为地名片段。
            place_parts.append(token)

        if "latitude" in explicit and "longitude" in explicit:
            latitude = explicit["latitude"]
            longitude = explicit["longitude"]
        elif "latitude" in explicit or "longitude" in explicit:
            # 显式指定了其中一个：另一个只能来自裸坐标（最多 1 个）。
            if len(coordinates) > 1:
                result["error"] = "已用键值对指定坐标时，最多再提供一个数值参数"
                return result
            latitude = explicit.get("latitude")
            longitude = explicit.get("longitude")
            if coordinates:
                if longitude is None:
                    longitude = coordinates[0]
                else:
                    latitude = coordinates[0]
        else:
            # 全部走裸坐标推断。
            if len(coordinates) > 2:
                result["error"] = "最多提供两个数值参数（纬度、经度）"
                return result
            latitude = None
            longitude = None
            if len(coordinates) >= 2:
                latitude, longitude = coordinates[0], coordinates[1]
                # 顺序纠偏：纬度绝对值不可能大于 90；若第 1 个越界而第 2 个合法则交换。
                if abs(latitude) > 90.0 and abs(longitude) <= 90.0:
                    latitude, longitude = longitude, latitude
                    result["swapped"] = True
            elif len(coordinates) == 1:
                value = coordinates[0]
                if abs(value) > 90.0:
                    # 绝对值大于 90 不可能是纬度，判定无歧义。
                    longitude = value
                else:
                    # 落地到「只修正其中一个坐标」的核心消歧逻辑：
                    # 优先参考当前生效配置，判断用户想补的是哪一个。
                    existing_lat = (existing_lm or {}).get("latitude")
                    existing_lon = (existing_lm or {}).get("longitude")
                    if existing_lat is not None and existing_lon is None:
                        longitude = value
                    elif existing_lon is not None and existing_lat is None:
                        latitude = value
                    else:
                        # 已有完整坐标或完全没有坐标时无法确定意图，
                        # 默认按纬度处理并标记歧义，由调用方提示显式写法。
                        latitude = value
                        result["ambiguous"] = True

        if latitude is not None and not -90.0 <= latitude <= 90.0:
            result["error"] = f"纬度 {latitude} 超出有效范围 -90 ~ 90"
            return result
        if longitude is not None and not -180.0 <= longitude <= 180.0:
            result["error"] = f"经度 {longitude} 超出有效范围 -180 ~ 180"
            return result
        # 地名先拼接并去除首尾空白后再参与判定：只有“真·非空”的地名
        # 才算提供了有效内容，避免地名片段全为空白时绕过校验。
        place_name = " ".join(place_parts).strip()

        # 仅当坐标与地名“全部缺失”时才算解析失败。
        if latitude is None and longitude is None and not place_name:
            result["error"] = "至少需要提供纬度、经度或地名之一"
            return result

        result["latitude"] = latitude
        result["longitude"] = longitude
        result["place_name"] = place_name
        return result
