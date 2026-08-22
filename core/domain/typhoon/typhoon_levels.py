"""台风强度等级领域规则。"""

from __future__ import annotations

from typing import Any

# 顺序必须从长关键词到短关键词，避免"强台风"先命中"台风"。
LEVEL_WEIGHTS: dict[str, int] = {
    "超强台风": 6,
    "强台风": 5,
    "强热带风暴": 3,
    "热带风暴": 2,
    "热带低压": 1,
    "台风": 4,
}

# 中文等级 → 英文缩写映射，供英文名称回退等场景使用，
# 确保英文字段不混入中文内容。
LEVEL_EN_ABBR: dict[str, str] = {
    "热带低压": "TD",
    "热带风暴": "TS",
    "强热带风暴": "STS",
    "台风": "TY",
    "强台风": "STY",
    "超强台风": "Super TY",
}


def normalize_level(level: Any) -> str:
    """清理等级文本；空值返回空字符串。"""
    return str(level or "").strip()


def level_weight(level: Any) -> int:
    """返回台风等级权重，支持包含匹配，未知等级返回 0。"""
    text = normalize_level(level)
    if not text:
        return 0
    exact = LEVEL_WEIGHTS.get(text)
    if exact is not None:
        return exact
    for level_name, weight in LEVEL_WEIGHTS.items():
        if level_name in text:
            return weight
    return 0


def compare_levels(left: Any, right: Any) -> int:
    """比较两个等级；左高返回 1，相等返回 0，右高返回 -1。"""
    left_weight = level_weight(left)
    right_weight = level_weight(right)
    return (left_weight > right_weight) - (left_weight < right_weight)
