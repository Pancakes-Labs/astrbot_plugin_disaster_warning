"""台风查询参数解析。

承接命令可选参数与 API 入参规范化，不依赖展示器或数据适配逻辑。
"""

from __future__ import annotations

import re
from typing import Any

# 详细程度：current 仅输出当前状态；full 额外附带历史/预报路径摘要
DETAIL_CURRENT = "current"
DETAIL_FULL = "full"

# 命令参数与前端下拉值的别名映射（中英文均可）
_DETAIL_ALIASES = {
    "current": DETAIL_CURRENT,
    "summary": DETAIL_CURRENT,
    "简要": DETAIL_CURRENT,
    "当前": DETAIL_CURRENT,
    "摘要": DETAIL_CURRENT,
    "full": DETAIL_FULL,
    "track": DETAIL_FULL,
    "path": DETAIL_FULL,
    "完整": DETAIL_FULL,
    "路径": DETAIL_FULL,
    "轨迹": DETAIL_FULL,
}

# “仅查询活跃台风”语义别名
_ACTIVE_ALIASES = {"活跃", "active", "live", "进行中"}

# 列表默认条数与硬上限（与 EQSC 无参查询至多 20 条对齐）
DEFAULT_COUNT = 1
MAX_COUNT = 20


def normalize_typhoon_detail(value: str | None) -> str:
    """规范化详细程度参数。

    未识别或空值时回退为 current，避免上游传入脏值导致分支失效。
    """
    token = str(value or "").strip().lower()
    if not token:
        return DETAIL_CURRENT
    if token in _DETAIL_ALIASES:
        return _DETAIL_ALIASES[token]
    raw = str(value or "").strip()
    return _DETAIL_ALIASES.get(raw, DETAIL_CURRENT)


def normalize_typhoon_count(value: Any, default: int = DEFAULT_COUNT) -> int:
    """规范化返回数量，限制在 1..20。"""
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = default
    if count < 1:
        return 1
    if count > MAX_COUNT:
        return MAX_COUNT
    return count


def is_typhoon_id_token(token: str | None) -> bool:
    """判断是否为台风编号（4 位 EQSC 或 6 位 Fan）。"""
    text = str(token or "").strip()
    return bool(re.fullmatch(r"\d{4}|\d{6}", text))


def is_count_token(token: str | None) -> bool:
    """判断是否为数量参数。

    故意排除 4 位及以上纯数字，避免把台风编号误判成数量。
    """
    text = str(token or "").strip()
    if not re.fullmatch(r"\d{1,2}", text):
        return False
    if len(text) >= 4:
        return False
    value = int(text)
    return 1 <= value <= MAX_COUNT


def is_active_token(token: str | None) -> bool:
    """判断是否为“仅活跃”语义。"""
    text = str(token or "").strip().lower()
    raw = str(token or "").strip()
    return text in _ACTIVE_ALIASES or raw in _ACTIVE_ALIASES


def is_detail_token(token: str | None) -> bool:
    """判断是否为详细程度语义。"""
    text = str(token or "").strip().lower()
    raw = str(token or "").strip()
    return text in _DETAIL_ALIASES or raw in _DETAIL_ALIASES


def parse_typhoon_query_args(
    arg1: str | None = None,
    arg2: str | None = None,
    arg3: str | None = None,
) -> dict[str, Any]:
    """解析命令可选参数，兼容位置不固定的写法。

    支持示例：
    - /台风信息查询
    - /台风信息查询 5
    - /台风信息查询 2609
    - /台风信息查询 202609 完整
    - /台风信息查询 巴威 完整
    - /台风信息查询 5 活跃
    - /台风信息查询 活跃 完整 8

    解析优先级（按 token 语义，而非位置）：
    详细程度 > 活跃过滤 > 台风 ID > 数量 > 名称关键词
    """
    typhoon_id: str | None = None
    keyword: str | None = None
    count = DEFAULT_COUNT
    detail = DETAIL_CURRENT
    active_only = False
    count_explicit = False

    for token in (arg1, arg2, arg3):
        if token is None:
            continue
        text = str(token).strip()
        if not text:
            continue

        if is_detail_token(text):
            detail = normalize_typhoon_detail(text)
            continue
        if is_active_token(text):
            active_only = True
            continue
        if is_typhoon_id_token(text):
            typhoon_id = text
            continue
        if is_count_token(text):
            count = normalize_typhoon_count(text)
            count_explicit = True
            continue
        if keyword is None:
            keyword = text

    return {
        "typhoon_id": typhoon_id,
        "keyword": keyword,
        "count": count,
        "detail": detail,
        "active_only": active_only,
        "count_explicit": count_explicit,
    }


__all__ = [
    "DEFAULT_COUNT",
    "DETAIL_CURRENT",
    "DETAIL_FULL",
    "MAX_COUNT",
    "is_active_token",
    "is_count_token",
    "is_detail_token",
    "is_typhoon_id_token",
    "normalize_typhoon_count",
    "normalize_typhoon_detail",
    "parse_typhoon_query_args",
]
