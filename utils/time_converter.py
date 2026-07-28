"""
时间处理工具类
统一处理时间的解析、时区转换和格式化
"""

from datetime import datetime, timedelta, timezone

# 时区定义映射
TIMEZONES = {
    "UTC": timezone.utc,
    "UTC+0": timezone.utc,
    "UTC+8": timezone(timedelta(hours=8)),  # 北京时间
    "CST": timezone(timedelta(hours=8)),
    "UTC+9": timezone(timedelta(hours=9)),  # 日本时间
    "JST": timezone(timedelta(hours=9)),
}

# 尝试导入 zoneinfo 以支持 IANA 时区 (Python 3.9+)
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Python 3.8 或更早版本需要 backports.zoneinfo
    # 如果没有安装，降级为仅支持固定偏移
    ZoneInfo = None


class TimeConverter:
    """时间转换工具类"""

    # 对应预定义的时区字典映射，防止类方法无法获取 TIMEZONES 属性
    TIMEZONES = TIMEZONES

    # 时区缓存，避免高频创建 ZoneInfo 时区对象造成性能耗损
    _timezone_cache = {}

    @staticmethod
    def parse_datetime(
        time_input: str | int | float | datetime | None,
    ) -> datetime | None:
        """
        解析各种格式的时间输入为 datetime 对象

        参数说明：
        - time_input：时间字符串、时间戳（秒或毫秒）、datetime 对象或 None

        返回值：
        - datetime 对象（可能是 Naive 或 Aware），解析失败时返回 None
        """
        if time_input is None:
            return None

        # 已经是 datetime 对象
        if isinstance(time_input, datetime):
            return time_input

        # 处理时间戳 (int/float)
        if isinstance(time_input, (int, float)):
            try:
                ts = float(time_input)
                # 智能判断秒还是毫秒 (以 300亿 为界，约公元2920年)
                if ts > 30000000000:
                    ts /= 1000
                return datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                return None

        # 处理字符串
        if not isinstance(time_input, str):
            return None

        time_str = time_input.strip()
        if not time_str:
            return None

        # 1. ISO 8601 格式处理 (包含 Z 或 T)
        if "T" in time_str or "Z" in time_str:
            try:
                # 处理 Python < 3.11 对 Z 的兼容性 (虽然 3.11+ 支持 Z，但为了稳健)
                clean_str = time_str.replace("Z", "+00:00")
                return datetime.fromisoformat(clean_str)
            except ValueError:
                pass

        # 2. 常见日期格式尝试
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y/%m/%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",  # 无时区 ISO
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M",
            # EQSC 等源常见：紧凑日期 + 空格时间，如 20260728 16:04:08
            "%Y%m%d %H:%M:%S",
            "%Y%m%d %H:%M:%S.%f",
            "%Y%m%d %H:%M",
            "%Y%m%d%H%M%S",
            "%Y%m%d%H%M",
        ]

        for fmt in formats:
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue

        return None

    @staticmethod
    def _get_timezone(tz_str: str):
        """
        获取时区对象
        支持 "UTC+8", "JST" 等预定义时区，
        也支持 "Asia/Shanghai" 等 IANA 时区 (需要 zoneinfo 支持)
        """
        # 1. 尝试预定义时区
        if tz_str in TIMEZONES:
            return TIMEZONES[tz_str]

        # 检查缓存
        if tz_str in TimeConverter._timezone_cache:
            return TimeConverter._timezone_cache[tz_str]

        tz_obj = None

        # 2. 尝试解析 UTC+N / UTC-N 格式
        if tz_str.startswith("UTC"):
            try:
                # 提取偏移量，例如 UTC+8 -> +8, UTC-5 -> -5
                offset_str = tz_str[3:]
                offset_hours = float(offset_str)
                tz_obj = timezone(timedelta(hours=offset_hours))
            except ValueError:
                pass

        # 3. 尝试 IANA 时区 (如 "Asia/Shanghai")
        if tz_obj is None and ZoneInfo:
            try:
                tz_obj = ZoneInfo(tz_str)
            except Exception:
                pass

        # 默认返回 UTC+8
        if tz_obj is None:
            tz_obj = TIMEZONES["UTC+8"]

        # 存入缓存
        TimeConverter._timezone_cache[tz_str] = tz_obj
        return tz_obj

    @staticmethod
    def convert_timezone(dt: datetime, target_tz_str: str = "UTC+8") -> datetime:
        """
        将 datetime 对象转换为目标时区
        """
        if dt.tzinfo is None:
            return dt

        target_tz = TimeConverter._get_timezone(target_tz_str)
        return dt.astimezone(target_tz)

    @staticmethod
    def _safe_strftime(dt: datetime, fmt: str) -> str:
        """
        安全的 strftime，解决 Windows 下中文编码错误问题
        """
        try:
            return dt.strftime(fmt)
        except UnicodeEncodeError:
            # 如果包含中文导致编码错误，先替换为占位符，格式化后再填回
            safe_fmt = ""
            replacements = []

            for char in fmt:
                if ord(char) > 127:
                    safe_fmt += "{}"
                    replacements.append(char)
                elif char == "{":
                    safe_fmt += "{{"
                elif char == "}":
                    safe_fmt += "}}"
                else:
                    safe_fmt += char

            return dt.strftime(safe_fmt).format(*replacements)

    @staticmethod
    def format_time(
        dt: datetime | None,
        target_timezone: str = "UTC+8",
        fmt: str = "%Y年%m月%d日 %H时%M分%S秒",
    ) -> str:
        """
        格式化时间显示
        """
        if not dt:
            return "未知时间"

        target_tz = TimeConverter._get_timezone(target_timezone)

        # 如果 datetime 带有时区信息，进行时区转换
        if dt.tzinfo is not None:
            dt = dt.astimezone(target_tz)

        # 返回格式化字符串 + 时区名
        # 使用 _safe_strftime 替代直接调用 strftime，规避 Windows 下 GBK 编码缺陷
        time_str = TimeConverter._safe_strftime(dt, fmt)
        return f"{time_str} ({target_timezone})"
