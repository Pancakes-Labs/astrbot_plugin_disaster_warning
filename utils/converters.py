"""
数据转换工具。
提供震度、烈度与数值转换等通用能力。
"""

import math
import re
from typing import Any

from .severity_emoji import INTENSITY_CIRCLE_EMOJIS


def safe_float_convert(value: Any) -> float | None:
    """安全地将输入值转换为浮点数。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, TypeError):
            return None
    return None


class ScaleConverter:
    """震度与烈度转换工具类。"""

    # 罗马数字到阿拉伯数字的映射，用于兼容 Global Quake 等数据源的烈度表示。
    ROMAN_TO_INT = {
        "I": 1,
        "II": 2,
        "III": 3,
        "IV": 4,
        "V": 5,
        "VI": 6,
        "VII": 7,
        "VIII": 8,
        "IX": 9,
        "X": 10,
        "XI": 11,
        "XII": 12,
    }

    @staticmethod
    def parse_jma_cwa_scale(scale_str: str | int | float) -> float | None:
        """
        解析日本或台湾震度字符串。
        支持格式：'5-'、'5+'、'5弱'、'5強'、'5强'、'5' 等。

        映射规则与项目内 P2P/展示层规范值对齐：
        X弱 / X- -> X - 0.5
        X強 / X强 / X+ -> X
        X        -> X.0

        例如：
        5弱 -> 4.5
        5強 -> 5.0
        6弱 -> 5.5
        6強 -> 6.0
        """
        if scale_str is None:
            return None

        # 若输入本身已经是数值，则直接返回。
        if isinstance(scale_str, (int, float)):
            return float(scale_str)

        scale_str = str(scale_str).strip()
        if not scale_str:
            return None

        # 先走显式字典，避免简繁体与符号写法产生歧义。
        normalized = scale_str.replace("強", "强").replace("＋", "+").replace("－", "-")
        explicit_mapping = {
            "5-": 4.5,
            "5弱": 4.5,
            "5+": 5.0,
            "5强": 5.0,
            "6-": 5.5,
            "6弱": 5.5,
            "6+": 6.0,
            "6强": 6.0,
            "7": 7.0,
            "4": 4.0,
            "3": 3.0,
            "2": 2.0,
            "1": 1.0,
            "0": 0.0,
        }
        if normalized in explicit_mapping:
            return explicit_mapping[normalized]

        # 支持 5+、5-、5弱、5強/5强 等多种格式。
        # 「強/强/+」映射为整数档（5.0/6.0），与 convert_p2p_scale 及展示层一致。
        match = re.search(r"(\d+)(弱|強|强|\+|\-)?", normalized)
        if match:
            base = int(match.group(1))
            suffix = match.group(2)

            if suffix in ["弱", "-"]:
                return base - 0.5
            if suffix in ["強", "强", "+"]:
                return float(base)
            return float(base)

        return None

    @staticmethod
    def convert_p2p_scale(p2p_scale: int) -> float | None:
        """
        将 P2P 震度值转换为标准震度。

        映射表：
        10 -> 1.0
        20 -> 2.0
        30 -> 3.0
        40 -> 4.0
        45 -> 4.5 (5弱)
        46 -> 4.6 (5弱以上推测)
        50 -> 5.0 (5強)
        55 -> 5.5 (6弱)
        60 -> 6.0 (6強)
        70 -> 7.0 (7)
        """
        scale_mapping = {
            -1: None,  # 震度信息不存在
            0: 0.0,  # 震度0
            10: 1.0,  # 震度1
            20: 2.0,  # 震度2
            30: 3.0,  # 震度3
            40: 4.0,  # 震度4
            45: 4.5,  # 震度5弱
            46: 4.6,  # 震度5弱以上と推定されるが震度情報を入手していない
            50: 5.0,  # 震度5強
            55: 5.5,  # 震度6弱
            60: 6.0,  # 震度6強
            70: 7.0,  # 震度7
        }
        return scale_mapping.get(p2p_scale)

    @staticmethod
    def normalize_p2p_scale_value(value: Any) -> int | None:
        """把 P2P 震度业务值安全规整为整数。"""
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def format_p2p_scale_display(value: Any) -> str:
        """把 P2P 震度业务值转换为用户可读展示值。"""
        raw_value = ScaleConverter.normalize_p2p_scale_value(value)
        if raw_value is None:
            return ""
        if raw_value == 99:
            return "以上"
        converted = ScaleConverter.convert_p2p_scale(raw_value)
        if converted is not None:
            return ScaleConverter.format_jma_cwa_scale_display(converted)
        return ScaleConverter.format_jma_cwa_scale_display(raw_value)

    @staticmethod
    def format_p2p_scale_range(scale_from: Any, scale_to: Any) -> str:
        """格式化 P2P 预估震度范围。"""
        from_value = ScaleConverter.normalize_p2p_scale_value(scale_from)
        to_value = ScaleConverter.normalize_p2p_scale_value(scale_to)
        if from_value is None and to_value is None:
            return ""
        if from_value is None:
            return ScaleConverter.format_p2p_scale_display(to_value)
        if to_value is None or to_value == from_value:
            return ScaleConverter.format_p2p_scale_display(from_value)
        from_display = ScaleConverter.format_p2p_scale_display(from_value)
        to_display = ScaleConverter.format_p2p_scale_display(to_value)
        if not from_display:
            return to_display
        if not to_display:
            return from_display
        if to_value == 99:
            return f"{from_display}{to_display}"
        return f"{from_display} ～ {to_display}"

    @staticmethod
    def get_p2p_scale_emoji(scale_from: Any, scale_to: Any) -> str:
        """根据 P2P 震度业务值选择展示 emoji（色板复用统一震度色序）。"""
        candidates: list[float] = []
        for value in (scale_from, scale_to):
            raw_value = ScaleConverter.normalize_p2p_scale_value(value)
            if raw_value is None:
                continue
            converted = ScaleConverter.convert_p2p_scale(raw_value)
            if converted is not None:
                candidates.append(converted)
        if not candidates:
            return INTENSITY_CIRCLE_EMOJIS[0]
        max_scale = max(candidates)
        # 档位索引 → 统一圆形色序（白→蓝→绿→黄→橙→红→紫）
        if max_scale >= 6.5:
            idx = 6
        elif max_scale >= 5.5:
            idx = 5
        elif max_scale >= 4.5:
            idx = 4
        elif max_scale >= 3.5:
            idx = 3
        elif max_scale >= 2.5:
            idx = 2
        elif max_scale >= 1.5:
            idx = 1
        else:
            idx = 0
        return INTENSITY_CIRCLE_EMOJIS[idx]

    # 計測震度中“0以下”的阈值：与 S-Net/C0 图标区间（shindo < -0.5）对齐。
    MEASURED_INTENSITY_BELOW_ZERO = -0.5

    @staticmethod
    def classify_measured_intensity(value: float | int | None) -> float | None:
        """把连续計測震度归类为日本震度阶级对应的规范浮点值。

        阈值与项目内既有展示逻辑一致：
        ≥6.5→7, ≥6.0→6.0(6强), ≥5.5→5.5(6弱), ≥5.0→5.0(5强),
        ≥4.5→4.5(5弱), ≥3.5→4, ≥2.5→3, ≥1.5→2, ≥0.5→1,
        ≥-0.5→0, < -0.5→None（表示“0以下”）。
        """
        if value is None:
            return None
        try:
            num = float(value)
        except (TypeError, ValueError):
            return None
        if num >= 6.5:
            return 7.0
        if num >= 6.0:
            return 6.0
        if num >= 5.5:
            return 5.5
        if num >= 5.0:
            return 5.0
        if num >= 4.5:
            return 4.5
        if num >= 3.5:
            return 4.0
        if num >= 2.5:
            return 3.0
        if num >= 1.5:
            return 2.0
        if num >= 0.5:
            return 1.0
        # 0 与轻微负值（MSIL 色阶）统一按震度 0 展示
        if num >= ScaleConverter.MEASURED_INTENSITY_BELOW_ZERO:
            return 0.0
        # 更低的负值不映射到 0，交由 format_measured_intensity_display 显示“0以下”
        return None

    @staticmethod
    def format_measured_intensity_display(value: float | int | None) -> str:
        """连续計測震度 → 震度阶级展示文本。

        复用 format_jma_cwa_scale_display；低于 -0.5 时返回“0以下”。
        """
        if value is None:
            return ""
        try:
            num = float(value)
        except (TypeError, ValueError):
            return ""
        if num < ScaleConverter.MEASURED_INTENSITY_BELOW_ZERO:
            return "0以下"
        classified = ScaleConverter.classify_measured_intensity(num)
        if classified is None:
            return "0以下"
        return ScaleConverter.format_jma_cwa_scale_display(classified)

    @staticmethod
    def format_jma_cwa_scale_display(scale_value: str | int | float | None) -> str:
        """
        将日本/台湾震度值转换为展示文本。

        支持输入：
        - 原始字符串: "5-", "5+", "5弱", "5強", "6弱"
        - 解析后的浮点值: 4.5, 5.0, 5.5, 6.0
        - P2P 原始整数: 45, 50, 55, 60, 70
        """
        if scale_value is None:
            return ""

        if isinstance(scale_value, str):
            scale_str = scale_value.strip()
            if not scale_str:
                return ""

            normalized = (
                scale_str.replace("強", "强").replace("＋", "+").replace("－", "-")
            )
            display_mapping = {
                "5-": "5弱",
                "5+": "5强",
                "6-": "6弱",
                "6+": "6强",
                "5弱": "5弱",
                "5强": "5强",
                "5強": "5强",
                "6弱": "6弱",
                "6强": "6强",
                "6強": "6强",
                "7": "7",
                "4": "4",
                "3": "3",
                "2": "2",
                "1": "1",
                "0": "0",
            }
            if normalized in display_mapping:
                return display_mapping[normalized]

            parsed = ScaleConverter.parse_jma_cwa_scale(normalized)
            if parsed is None:
                return scale_str
            scale_value = parsed

        if isinstance(scale_value, int) and scale_value in {
            10,
            20,
            30,
            40,
            45,
            46,
            50,
            55,
            60,
            70,
        }:
            if scale_value == 45 or scale_value == 46:
                return "5弱"
            if scale_value == 50:
                return "5强"
            if scale_value == 55:
                return "6弱"
            if scale_value == 60:
                return "6强"
            if scale_value == 70:
                return "7"
            return str(scale_value // 10)

        if isinstance(scale_value, (int, float)):
            num = float(scale_value)
            if math.isclose(num, 4.5, abs_tol=0.01):
                return "5弱"
            if math.isclose(num, 5.0, abs_tol=0.01):
                return "5强"
            if math.isclose(num, 5.5, abs_tol=0.01):
                return "6弱"
            if math.isclose(num, 6.0, abs_tol=0.01):
                return "6强"
            if math.isclose(num, round(num), abs_tol=0.01):
                return str(int(round(num)))
            return f"{num:.1f}".rstrip("0").rstrip(".")

        return str(scale_value)

    @classmethod
    def convert_roman_intensity(cls, intensity_str: str) -> float | None:
        """将罗马数字烈度转换为浮点数。"""
        if not intensity_str:
            return None

        if intensity_str in cls.ROMAN_TO_INT:
            return float(cls.ROMAN_TO_INT[intensity_str])

        return None
