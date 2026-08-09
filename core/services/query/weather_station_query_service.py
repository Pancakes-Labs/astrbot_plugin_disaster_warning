"""
气象站查询服务。

统一承接命令侧（/实况 /气象站 /气象站历史 /实况历史 /气象站列表）的查询编排：
- 站点解析：支持五位站号（59270）或中文站名（怀集/广东怀集）
- 映射链路：FAN station_all（站号->站名） + NMC 省份城市表（站名->NMC城市码）
- 实况查询：NMC /rest/weather 返回的 real（温度/24h变温/气压/湿度/风向/风速/降水/体感）
- 历史查询：NMC /rest/weather 返回的 passedchart（近24h逐小时观测），可选指定时次
- 列表查询：NMC /rest/province/all + /rest/province/{pcode} 精确按省过滤，附五位数码

数据源：
- FAN Studio：https://api.fanstudio.tech/we/station_all.php?type=temperature
- NMC 中央气象台：https://www.nmc.cn/rest/weather?stationid={code}

缺测值 9999 统一显示为「-」。
"""

from __future__ import annotations

import re
from typing import Any

from ...network.http.fan_studio_station_client import FanStudioStationClient
from ...network.http.nmc_weather_client import NmcWeatherClient

# 缺测标记值：NMC 接口用 9999 表示缺测。
MISSING_VALUE = 9999.0

# 风向角度 -> 16 方位中文（用于历史数据的 windDirection 角度转中文风向）。
_DIRECTIONS_16 = [
    "北",
    "北东北",
    "东北",
    "东东北",
    "东",
    "东东南",
    "东南",
    "南东南",
    "南",
    "南西南",
    "西南",
    "西西南",
    "西",
    "西西北",
    "西北",
    "北西北",
]

# 风向角度 -> 16 方位英文缩写（与 _DIRECTIONS_16 一一对应，用于历史输出）。
_DIRECTIONS_16_EN = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
]

# 实况要素定义：输出行前缀、取值键、单位
_REAL_FIELD_DEFS: list[tuple[str, str, str]] = [
    ("瞬时温度", "temperature", "℃"),
    ("24小时变温", "temperatureDiff", "℃"),
    ("体感温度", "feelst", "℃"),
    ("地面气压", "airpressure", "hPa"),
    ("相对湿度", "humidity", "%"),
    ("1小时降水", "rain", "mm"),
    ("天气现象", "info", ""),
]


def _norm_value(v: Any, unit: str = "", decimals: int | None = None) -> str:
    """把数值格式化为可读文本；缺测/异常显示「-」。

    Args:
        v: 原始数值。
        unit: 单位后缀。
        decimals: 固定小数位；None 时温度（℃）保留 1 位、其余整数不带小数。
    """
    if v is None or v == "" or v == "9999" or v == "9999.0":
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == MISSING_VALUE:
        return "-"
    if decimals is None:
        # 温度统一保留 1 位小数（如 29.5 ℃、36.0 ℃），其余整数不带小数
        if unit == "℃":
            decimals = 1
        else:
            decimals = 0 if f == int(f) else 1
    text = f"{f:.{decimals}f}"
    return f"{text} {unit}".strip() if unit else text


# 省级行政区全称 -> 简称（用于「省份+城市」展示，避免过长）。
_PROVINCE_FULL_TO_SHORT: dict[str, str] = {
    "北京市": "北京",
    "天津市": "天津",
    "上海市": "上海",
    "重庆市": "重庆",
    "河北省": "河北",
    "山西省": "山西",
    "内蒙古自治区": "内蒙古",
    "辽宁省": "辽宁",
    "吉林省": "吉林",
    "黑龙江省": "黑龙江",
    "江苏省": "江苏",
    "浙江省": "浙江",
    "安徽省": "安徽",
    "福建省": "福建",
    "江西省": "江西",
    "山东省": "山东",
    "河南省": "河南",
    "湖北省": "湖北",
    "湖南省": "湖南",
    "广东省": "广东",
    "广西壮族自治区": "广西",
    "海南省": "海南",
    "四川省": "四川",
    "贵州省": "贵州",
    "云南省": "云南",
    "西藏自治区": "西藏",
    "陕西省": "陕西",
    "甘肃省": "甘肃",
    "青海省": "青海",
    "宁夏回族自治区": "宁夏",
    "新疆维吾尔自治区": "新疆",
    "香港特别行政区": "香港",
    "澳门特别行政区": "澳门",
    "台湾省": "台湾",
}


def _province_short(province: str) -> str:
    """把省级行政区全称转为简称；已是简称或未知时原样返回。"""
    name = str(province or "").strip()
    if not name:
        return ""
    return _PROVINCE_FULL_TO_SHORT.get(name, name)


def _degree_to_direction(degree: Any, english: bool = False) -> str:
    """风向角度（度）转中文 16 方位；english=True 时返回英文缩写。"""
    try:
        deg = float(degree)
    except (TypeError, ValueError):
        return "-"
    if deg == MISSING_VALUE:
        return "-"
    idx = int((deg % 360 + 360) % 360 / 22.5 + 0.5) % 16
    return _DIRECTIONS_16_EN[idx] if english else _DIRECTIONS_16[idx]


def _parse_station_query(keyword: str) -> str:
    """标准化站点查询关键词（去空格、去省/市后缀）。"""
    s = str(keyword or "").strip()
    s = s.replace(" ", "")
    return s


def _looks_like_station_code(text: str) -> bool:
    """判断是否为站点代码（5 位数字国家站，或 G/Q/S 等字母开头的区域站号）。"""
    s = str(text or "").strip()
    if re.fullmatch(r"\d{5}", s):
        return True
    # 区域站：字母(1-2位) + 数字(3-5位)，如 G8419 / Q6404 / S3648
    if re.fullmatch(r"[A-Za-z]{1,2}\d{3,5}", s):
        return True
    return False


def _match_city_by_name(
    cities: list[dict[str, Any]], name: str
) -> list[dict[str, Any]]:
    """在城市列表中精确匹配站点名（去掉省/市后缀后的精确匹配，次选包含匹配）。"""
    if not cities or not name:
        return []
    exact = [c for c in cities if c.get("city") == name]
    if exact:
        return exact
    # 包含匹配：城市名包含站名，或站名包含城市名（如「怀集」在「怀集」中）
    contains = [
        c for c in cities if name in c.get("city", "") or c.get("city", "") in name
    ]
    return contains


# ----------------------------------------------------------------------
# 历史时次解析辅助
# ----------------------------------------------------------------------

# 历史时次正则：支持「10时」「23时」「2026-08-09 23:00」「08-09 10时」等
_HOUR_RE = re.compile(r"^(?P<h>\d{1,2})[时点]$")
_FULL_TS_RE = re.compile(
    r"^(?P<y>\d{4})[-年/](?P<m>\d{1,2})[-月/](?P<d>\d{1,2})[日]?\s*"
    r"(?P<h>\d{1,2})(?:[:：](?P<min>\d{1,2}))?[时点]?$"
)


def _parse_history_time(time_arg: str | None) -> tuple[str, int] | None:
    """把用户时次参数解析为 (YYYY-MM-DD, hour) 元组。

    支持：
    - 「10时」「23点」：近 24h 中匹配最近一天的该整点
    - 「2026-08-09 23:00」「2026年8月9日23时」：精确日期时次
    - 「08-09 10时」：月-日 + 时

    返回 None 表示未提供或无法解析（不筛选）。
    """
    if not time_arg or not str(time_arg).strip():
        return None
    s = str(time_arg).strip()
    m = _HOUR_RE.match(s)
    if m:
        h = int(m.group("h"))
        if 0 <= h <= 23:
            return ("*", h)
        return None
    m = _FULL_TS_RE.match(s)
    if m:
        y = int(m.group("y"))
        mo = int(m.group("m"))
        d = int(m.group("d"))
        h = int(m.group("h"))
        if 0 <= h <= 23:
            return (f"{y:04d}-{mo:02d}-{d:02d}", h)
    # 简写「08-09 10时」
    m = re.match(r"^(?P<m>\d{1,2})[-/](?P<d>\d{1,2})\s*(?P<h>\d{1,2})[时点]$", s)
    if m:
        mo = int(m.group("m"))
        d = int(m.group("d"))
        h = int(m.group("h"))
        if 0 <= h <= 23:
            return (f"*-{mo:02d}-{d:02d}", h)
    return None


def _find_history_item(
    chart: list[dict[str, Any]], target: tuple[str, int]
) -> dict[str, Any] | None:
    """在近 24h 观测记录中匹配目标 (日期前缀, 小时)。

    target[0] 为 "*" 时忽略日期（匹配最近一天的该整点）。
    """
    prefix, hour = target
    hour_str = f"{hour:02d}"
    best: dict[str, Any] | None = None
    for item in chart:
        t = str(item.get("time") or "").strip()  # 形如 "2026-08-09 23:00"
        if " " not in t:
            continue
        day_part, hhmm = t.split(" ", 1)
        if hhmm[:2] != hour_str:
            continue
        if prefix != "*" and prefix != day_part:
            continue
        # 记录最早匹配（近24h列表按时间倒序，首个匹配即最近）
        if best is None:
            best = item
    return best


class WeatherStationQueryService:
    """气象站查询服务（实况/历史/列表）。"""

    def __init__(
        self,
        *,
        nmc_client: NmcWeatherClient | None = None,
        fan_client: FanStudioStationClient | None = None,
    ) -> None:
        self._nmc = nmc_client or NmcWeatherClient()
        self._fan = fan_client or FanStudioStationClient()

    async def close(self) -> None:
        """关闭底层 HTTP 会话。"""
        await self._nmc.close()
        await self._fan.close()

    # ------------------------------------------------------------------
    # 站点解析
    # ------------------------------------------------------------------

    async def resolve_station(self, keyword: str) -> dict[str, Any]:
        """解析用户输入的站点关键词为 NMC 可查询的城市信息。

        支持：
        - 五位站号（59270）        -> FAN 映射站名 -> NMC 城市匹配
        - 中文站名（怀集）          -> 直接 NMC 城市匹配（需省份上下文）
        - 「省+站名」（广东怀集）    -> 精确按省过滤后匹配

        Returns:
            {
              "success": True,
              "station": {"code", "city", "province", "url"},
              "display_name": "广东怀集",
              "matched_by": "station_code" | "city_name",
            }
            或 {"success": False, "error": "...", "candidates": [...]}
        """
        raw = _parse_station_query(keyword)
        if not raw:
            return {"success": False, "error": "请输入站点代码或站名"}

        # ---- 分支1：五位站号 ----
        if _looks_like_station_code(raw):
            fan_station = await self._fan.find_station(raw)
            if not fan_station:
                return {"success": False, "error": f"未找到站号 {raw} 对应的站点"}
            sta_name = fan_station["sta_name"]
            # 用站名在 NMC 全量城市中匹配（可能跨省，返回候选）
            matched = await self._match_nmc_city(sta_name)
            if not matched.get("success"):
                matched["error"] = (
                    f"站号 {raw}（{sta_name}）未能在中央气象台站点表中找到对应城市"
                )
                return matched
            matched["station"]["fan_stationid"] = raw
            matched["display_name"] = (
                f"{matched['station'].get('province', '')}{sta_name}"
            )
            matched["matched_by"] = "station_code"
            return matched

        # ---- 分支2：站名（可能带省份前缀）----
        province_hint: str | None = None
        city_name = raw
        # 提取省份前缀：常见省名 + 简称。
        # 注意：仅当 raw 比省名更长（确实带了站名后缀）才剥离前缀；
        # 若 raw 恰好等于省名（如「上海」「北京」等直辖市名即站名），
        # 整串直接作为城市名匹配，否则会剥成空串导致「请输入有效的站点名称」。
        for pname in _COMMON_PROVINCES:
            if raw.startswith(pname) and len(raw) > len(pname):
                province_hint = pname
                city_name = raw[len(pname) :]
                break
        # 去掉「省」「市」后缀再匹配
        city_name = city_name.removesuffix("省").removesuffix("市").strip()

        matched = await self._match_nmc_city(city_name, province_hint=province_hint)
        if not matched.get("success"):
            return matched
        matched["matched_by"] = "city_name"
        province_full = matched["station"].get("province", "")
        province_short = _province_short(province_full)
        # 直辖市（北京/上海/天津/重庆）省名与城市名相同，展示去重避免「北京市北京」
        display_city = city_name
        if province_hint:
            display_city = f"{_province_short(province_hint)}{city_name}"
        elif province_short and province_short != city_name:
            display_city = f"{province_short}{city_name}"
        matched["display_name"] = display_city
        # 补查 FAN 站号，让站名查询的历史/实况输出也能带五位数码
        fan_sid = await self._find_fan_sid_by_name(city_name)
        if fan_sid:
            matched["station"]["fan_stationid"] = fan_sid
        return matched

    async def _find_fan_sid_by_name(self, city_name: str) -> str | None:
        """按站名在 FAN 全站表中反查站点代码（5 位数字或 G/Q/S 区域站号）。"""
        stations, error = await self._fan.fetch_stations()
        if error is not None:
            return None
        for s in stations:
            name = str(s.get("sta_name") or "").strip()
            if name == city_name:
                sid = str(s.get("stationid") or "").strip()
                if sid and _looks_like_station_code(sid):
                    return sid
        return None

    async def _match_nmc_city(
        self, city_name: str, province_hint: str | None = None
    ) -> dict[str, Any]:
        """在城市表中匹配 NMC 城市信息。

        带省份提示时只在该省城市列表中匹配；否则全量匹配。
        """
        if not city_name:
            return {"success": False, "error": "请输入有效的站点名称"}

        if province_hint:
            pcode = await self._find_province_code(province_hint)
            if not pcode:
                return {"success": False, "error": f"未找到省份「{province_hint}」"}
            cities = await self._nmc.fetch_cities(pcode)
            hits = _match_city_by_name(cities, city_name)
            if not hits:
                return {
                    "success": False,
                    "error": f"在{province_hint}未找到城市「{city_name}」",
                }
            first = hits[0]
            return {
                "success": True,
                "station": {
                    "code": first["code"],
                    "city": first["city"],
                    "province": first.get("province") or province_hint,
                    "url": first.get("url", ""),
                },
            }

        # 无省份提示：全量拉取所有省份城市匹配
        provinces = await self._nmc.fetch_provinces()
        all_hits: list[dict[str, Any]] = []
        for p in provinces:
            cities = await self._nmc.fetch_cities(p["code"])
            hits = _match_city_by_name(cities, city_name)
            for h in hits:
                all_hits.append(h)
                if len(all_hits) >= 10:
                    break
            if len(all_hits) >= 10:
                break

        if not all_hits:
            return {"success": False, "error": f"未找到城市「{city_name}」"}
        if len(all_hits) > 1:
            # 多候选：去重城市名后提示用户加省份前缀
            names = sorted({h.get("city", "") for h in all_hits})
            return {
                "success": False,
                "error": f"「{city_name}」匹配到多个站点，请补充省份前缀",
                "candidates": names[:10],
            }
        first = all_hits[0]
        return {
            "success": True,
            "station": {
                "code": first["code"],
                "city": first["city"],
                "province": first.get("province", ""),
                "url": first.get("url", ""),
            },
        }

    async def _find_province_code(self, province_name: str) -> str | None:
        """按省份名（含简称）查 NMC 省份码。"""
        provinces = await self._nmc.fetch_provinces()
        for p in provinces:
            if p["name"] == province_name or province_name in p["name"]:
                return p["code"]
        # 简称映射兜底
        return _PROVINCE_SHORT.get(province_name)

    # ------------------------------------------------------------------
    # 实况
    # ------------------------------------------------------------------

    async def query_real(self, keyword: str) -> dict[str, Any]:
        """查询指定站点实况。

        Returns:
            {"success": True, "text": "..."} 或 {"success": False, "error": "..."}
        """
        resolved = await self.resolve_station(keyword)
        if not resolved.get("success"):
            return {"success": False, "error": resolved.get("error", "站点解析失败")}
        station = resolved["station"]
        data = await self._nmc.fetch_weather(station["code"])
        if not data.get("success"):
            return {
                "success": False,
                "error": f"实况查询失败: {data.get('error', '未知错误')}",
            }
        text = self._format_real(resolved, data)
        return {"success": True, "text": text, "station": station}

    @staticmethod
    def _format_display_city(station: dict[str, Any], code: str) -> str:
        """格式化站点展示名：省份简称 + 城市名（可选附站点代码）。

        直辖市的省简称与城市名相同，拼接时去重。
        """
        city = station.get("city") or ""
        province = station.get("province") or ""
        fan_sid = station.get("fan_stationid") or ""
        province_short = _province_short(province)
        if province_short and city:
            if province_short == city:
                base = city
            else:
                base = f"{province_short}{city}"
        else:
            base = city or code
        if fan_sid:
            return f"{base}（{fan_sid}）"
        return base

    def _format_real(self, resolved: dict[str, Any], data: dict[str, Any]) -> str:
        """格式化实况文本。"""
        station = resolved["station"]
        code = station.get("code") or ""
        publish_time = data.get("publish_time") or ""

        display_city = self._format_display_city(station, code)

        real = data.get("real") or {}
        weather = real.get("weather") if isinstance(real.get("weather"), dict) else {}
        wind = real.get("wind") if isinstance(real.get("wind"), dict) else {}

        lines = [f"🌦️ 站点：{display_city}"]
        if publish_time:
            lines.append(f"🕐 更新时间：{publish_time}")

        for label, key, unit in _REAL_FIELD_DEFS:
            lines.append(f"• {label}：{_norm_value(weather.get(key), unit)}")

        # 风向/风速
        wind_direct = str(wind.get("direct") or "").strip()
        wind_speed = wind.get("speed")
        wind_power = str(wind.get("power") or "").strip()
        # NMC 缺测标记 9999 统一显示为「-」
        if wind_direct and wind_direct != "9999":
            lines.append(f"• 风向：{wind_direct}")
        else:
            lines.append("• 风向：-")
        if wind_speed is not None and _norm_value(wind_speed, "m/s") != "-":
            wind_power_text = wind_power if wind_power and wind_power != "9999" else ""
            suffix = f"（{wind_power_text}）" if wind_power_text else ""
            lines.append(f"• 风速：{_norm_value(wind_speed, 'm/s')}{suffix}")
        elif wind_power and wind_power != "9999":
            lines.append(f"• 风力：{wind_power}")
        else:
            lines.append("• 风速：-")

        # 预警
        warn = data.get("warn") or {}
        if warn.get("alert") and warn.get("alert") != "9999":
            lines.append(f"🚨 预警：{warn.get('alert')}")
            if warn.get("issuecontent"):
                lines.append(f"  {warn['issuecontent']}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 历史（近 24h 逐小时）
    # ------------------------------------------------------------------

    async def query_history(
        self, keyword: str, time_arg: str | None = None
    ) -> dict[str, Any]:
        """查询指定站点近 24 小时逐小时观测，可选指定时次。

        Args:
            keyword: 站点代码或站名。
            time_arg: 可选时次，如「10时」「2026-08-09 23:00」，精确匹配某时次；
                      缺省时输出最近 8 条逐小时记录。

        Returns:
            {"success": True, "text": "..."} 或 {"success": False, "error": "..."}
        """
        resolved = await self.resolve_station(keyword)
        if not resolved.get("success"):
            return {"success": False, "error": resolved.get("error", "站点解析失败")}
        station = resolved["station"]
        data = await self._nmc.fetch_weather(station["code"])
        if not data.get("success"):
            return {
                "success": False,
                "error": f"历史查询失败: {data.get('error', '未知错误')}",
            }
        chart = data.get("passedchart") or []
        if not chart:
            return {"success": False, "error": "暂无近24小时逐小时观测数据"}

        # 指定时次：在近24h中精确匹配
        target = _parse_history_time(time_arg)
        if target is not None:
            matched = _find_history_item(chart, target)
            if not matched:
                return {
                    "success": False,
                    "error": (
                        f"近24小时内未找到 {time_arg} 的观测数据"
                        "（数据源仅保留近24小时）"
                    ),
                }
            text = self._format_history_single(resolved, data, matched)
            return {
                "success": True,
                "text": text,
                "station": station,
                "is_single": True,
            }

        text = self._format_history(resolved, data, chart)
        return {"success": True, "text": text, "station": station}

    @staticmethod
    def _format_history(
        resolved: dict[str, Any],
        data: dict[str, Any],
        chart: list[dict[str, Any]],
    ) -> str:
        """格式化近 24h 逐小时观测文本（倒序展示最新在前）。"""
        station = resolved["station"]
        code = station.get("code") or ""
        publish_time = data.get("publish_time") or ""

        display_city = WeatherStationQueryService._format_display_city(station, code)

        lines = [f"🕐 气象站历史（近24小时）：{display_city}"]
        if publish_time:
            lines.append(f"🕐 数据时间：{publish_time}")

        # 只展示最近 8 条，避免过长
        for item in chart[:8]:
            t = str(item.get("time") or "").strip()
            temp = _norm_value(item.get("temperature"), "℃")
            humidity = _norm_value(item.get("humidity"), "%")
            pressure = _norm_value(item.get("pressure"), "hPa")
            rain1h = _norm_value(item.get("rain1h"), "mm")
            wind_dir = _degree_to_direction(item.get("windDirection"))
            wind_speed = _norm_value(item.get("windSpeed"), "m/s")
            lines.append(
                f"• {t}  {temp} / 湿度{humidity} / 气压{pressure} / "
                f"{wind_dir}{wind_speed} / 降水{rain1h}"
            )

        return "\n".join(lines)

    @staticmethod
    def _format_history_single(
        resolved: dict[str, Any],
        data: dict[str, Any],
        item: dict[str, Any],
    ) -> str:
        """格式化单个时次的历史观测。

        NMC passedchart 字段与示例对照：
            temperature  -> 瞬时温度
            pressure     -> 地面气压
            humidity     -> 相对湿度
            windDirection-> 瞬时风向（中文 + 英文缩写）
            windSpeed    -> 瞬时风速
            rain1h       -> 1小时降水
        """
        station = resolved["station"]
        code = station.get("code") or ""
        t = str(item.get("time") or "").strip()

        display_city = WeatherStationQueryService._format_display_city(station, code)

        # 风向：中文(英文缩写)
        wind_dir_cn = _degree_to_direction(item.get("windDirection"), english=False)
        wind_dir_en = _degree_to_direction(item.get("windDirection"), english=True)
        wind_dir_text = (
            f"{wind_dir_cn}({wind_dir_en})"
            if wind_dir_cn != "-" and wind_dir_en != "-"
            else "-"
        )

        lines = [
            f"站点：{display_city}",
            f"时次：{t}",
            "实况：",
            f"瞬时温度：{_norm_value(item.get('temperature'), '℃')}",
            f"地面气压：{_norm_value(item.get('pressure'), 'hPa')}",
            f"相对湿度：{_norm_value(item.get('humidity'), '%')}",
            f"瞬时风向：{wind_dir_text}",
            f"瞬时风速：{_norm_value(item.get('windSpeed'), 'm/s')}",
            f"1小时降水：{_norm_value(item.get('rain1h'), 'mm')}",
        ]
        # 补充 rain6h/rain12h/rain24h（NMC 有提供时展示，缺测自动为 -）
        r6 = _norm_value(item.get("rain6h"), "mm")
        r12 = _norm_value(item.get("rain12h"), "mm")
        r24 = _norm_value(item.get("rain24h"), "mm")
        if r6 != "-" or r12 != "-" or r24 != "-":
            lines.append(f"累计降水：6h {r6} / 12h {r12} / 24h {r24}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 列表
    # ------------------------------------------------------------------

    async def _build_fan_name_to_sid(
        self, nmc_city_names: set[str] | None = None
    ) -> tuple[dict[str, str], str | None]:
        """构建「FAN 站名 -> 站点代码」映射（含去除「站」后缀的键）。

        站点代码兼容：5 位数字国家站号（59270）与 G/Q/S 等字母开头的区域站号
        （如 G8419 阳山、Q6404 南海、S3648 和平），全部保留。

        若传入 nmc_city_names（NMC 城市名集合），则只保留「站名能匹配 NMC 城市」
        的站：FAN 全量 2.1w+ 站中混有大量台湾站（繁体名）、街镇级自动站（* 开头），
        这些站 NMC 城市表里不存在，保留只会污染映射表，还可能用繁体同名站错误覆盖大陆站号。

        Returns:
            (mapping, error)：网络失败时 mapping 为空、error 为明确错误描述。
        """
        stations, error = await self._fan.fetch_stations()
        mapping: dict[str, str] = {}
        if error is not None:
            return mapping, error
        for s in stations:
            name = str(s.get("sta_name") or "").strip()
            sid = str(s.get("stationid") or "").strip()
            if not name or not sid:
                continue
            # 过滤街镇级自动站（* 开头）
            if name.startswith("*"):
                continue
            # 只保留合法站号形态（5 位数字，或字母+数字）
            if not _looks_like_station_code(sid):
                continue
            # 若提供 NMC 城市名集合，只保留能匹配的站
            if nmc_city_names is not None:
                base = re.split(r"[（(]", name)[0].strip()
                if name not in nmc_city_names and base not in nmc_city_names:
                    continue
            mapping[name] = sid
            # 兼容「怀集站」->「怀集」
            if name.endswith("站") and len(name) > 1:
                mapping.setdefault(name[:-1], sid)
        return mapping, None

    async def query_list(self, province_keyword: str | None = None) -> dict[str, Any]:
        """查询气象站列表，可选按省份过滤，每个站点附五位站号。

        返回结构：
            {
              "success": True,
              "text": "...",       # 完整文本（供普通发送兜底）
              "blocks": [...],     # 分块文本（每省一块 / 每省一个节点，供合并转发）
              "is_nationwide": bool,
            }
            或 {"success": False, "error": "..."}
        """
        # 构建站名 -> 站号映射（全国列表与省份列表共用）。
        # FAN 全站数据接口超时/失败时，明确提示稍后重试，而不是输出无站号的残缺列表。
        # 反向用 NMC 城市名过滤：只保留能匹配 NMC 城市的站，剔除台湾/街镇自动站污染。
        nmc_city_names: set[str] = set()
        provinces_all = await self._nmc.fetch_provinces()
        for p in provinces_all:
            cities = await self._nmc.fetch_cities(p["code"])
            for c in cities:
                nmc_city_names.add(c["city"])
        fan_map, fan_error = await self._build_fan_name_to_sid(
            nmc_city_names=nmc_city_names
        )
        if fan_error is not None:
            return {
                "success": False,
                "error": f"获取全国站点编码失败：{fan_error}",
            }

        if not province_keyword:
            # 无省份参数：全国全量，每省一块（复用上方已拉取的省份/城市数据）
            if not provinces_all:
                return {"success": False, "error": "获取省份列表失败"}
            blocks: list[str] = []
            for p in provinces_all:
                cities = await self._nmc.fetch_cities(p["code"])
                if not cities:
                    continue
                display_pname = cities[0].get("province") or p["name"]
                pname_short = _province_short(display_pname)
                lines = [f"📌 {pname_short}气象站列表（共 {len(cities)} 站）："]
                chunk = []
                for c in cities:
                    cname = c.get("city") or ""
                    sid = fan_map.get(cname) or ""
                    if not sid:
                        base = re.split(r"[（(]", cname)[0].strip()
                        sid = fan_map.get(base) or ""
                    # 与省份分支一致：始终带括号，无站号显示 (-)
                    chunk.append(f"{cname}({sid})" if sid else f"{cname}(-)")
                    if len(chunk) == 8:
                        lines.append("  " + "、".join(chunk))
                        chunk = []
                if chunk:
                    lines.append("  " + "、".join(chunk))
                blocks.append("\n".join(lines))
            text = "📋 全国气象站列表：\n" + "\n\n".join(blocks)
            text += "\n\n💡 发送「气象站列表 <省份名>」查看该省全部站点"
            return {
                "success": True,
                "text": text,
                "blocks": blocks,
                "is_nationwide": True,
            }

        pname = str(province_keyword).strip().removesuffix("省").removesuffix("市")
        pcode = await self._find_province_code(pname)
        if not pcode:
            return {"success": False, "error": f"未找到省份「{province_keyword}」"}
        cities = await self._nmc.fetch_cities(pcode)
        if not cities:
            return {"success": False, "error": f"获取{province_keyword}站点列表失败"}

        display_pname = cities[0].get("province") or pname
        pname_short = _province_short(display_pname)
        lines = [f"📋 {pname_short}气象站列表（共 {len(cities)} 站）："]

        # 每行 8 个站点：站名(站号)
        chunk = []
        for c in cities:
            cname = c.get("city") or ""
            sid = fan_map.get(cname) or ""
            if not sid:
                base = re.split(r"[（(]", cname)[0].strip()
                sid = fan_map.get(base) or ""
            if not sid:
                sid = "-"
            chunk.append(f"{cname}({sid})")
            if len(chunk) == 8:
                lines.append("  " + "、".join(chunk))
                chunk = []
        if chunk:
            lines.append("  " + "、".join(chunk))
        lines.append("\n💡 发送「气象站 <站点名>」或「实况 <站号>」查询实况")
        text = "\n".join(lines)
        # 省份列表整体作为一个块
        return {
            "success": True,
            "text": text,
            "blocks": [text],
            "is_nationwide": False,
        }


# 常见省名（含简称）用于解析「省+站名」前缀
_COMMON_PROVINCES = [
    "北京市",
    "天津市",
    "河北省",
    "山西省",
    "内蒙古自治区",
    "辽宁省",
    "吉林省",
    "黑龙江省",
    "上海市",
    "江苏省",
    "浙江省",
    "安徽省",
    "福建省",
    "江西省",
    "山东省",
    "河南省",
    "湖北省",
    "湖南省",
    "广东省",
    "广西壮族自治区",
    "海南省",
    "重庆市",
    "四川省",
    "贵州省",
    "云南省",
    "西藏自治区",
    "陕西省",
    "甘肃省",
    "青海省",
    "宁夏回族自治区",
    "新疆维吾尔自治区",
    "香港特别行政区",
    "澳门特别行政区",
    "台湾省",
    "北京",
    "天津",
    "上海",
    "重庆",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
    "香港",
    "澳门",
    "台湾",
]

# 省份简称 -> NMC 省份码（兜底映射）
_PROVINCE_SHORT: dict[str, str] = {
    "北京": "ABJ",
    "天津": "ATJ",
    "河北": "AHE",
    "山西": "ASX",
    "内蒙古": "ANM",
    "辽宁": "ALN",
    "吉林": "AJL",
    "黑龙江": "AHL",
    "上海": "ASH",
    "江苏": "AJS",
    "浙江": "AZJ",
    "安徽": "AAH",
    "福建": "AFJ",
    "江西": "AJX",
    "山东": "ASD",
    "河南": "AHA",
    "湖北": "AHB",
    "湖南": "AHN",
    "广东": "AGD",
    "广西": "AGX",
    "海南": "AHI",
    "重庆": "ACQ",
    "四川": "ASC",
    "贵州": "AGZ",
    "云南": "AYN",
    "西藏": "AXZ",
    "陕西": "ASN",
    "甘肃": "AGS",
    "青海": "AQH",
    "宁夏": "ANX",
    "新疆": "AXJ",
    "香港": "AXG",
    "澳门": "AAM",
    "台湾": "ATW",
}


__all__ = [
    "WeatherStationQueryService",
    "MISSING_VALUE",
]
