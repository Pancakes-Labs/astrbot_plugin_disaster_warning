"""台风数据形态解析。"""

from __future__ import annotations

MODE_ALIASES: dict[str, str] = {
    "enriched": "enriched",
    "fan+eqsc": "enriched",
    "fan_eqsc": "enriched",
    "eqsc_enriched": "enriched",
    # 实时 EQSC 轮询
    "eqsc": "eqsc",
    "eqsc_live": "eqsc",
    "eqsc_poll": "eqsc",
    # 冷启动历史重建
    "eqsc_rebuild": "eqsc_rebuild",
    "eqsc_history": "eqsc_rebuild",
    "history_rebuild": "eqsc_rebuild",
    "rebuild": "eqsc_rebuild",
    "fan": "fan",
    "fan_studio": "fan",
    "fanstudio": "fan",
}


def resolve_data_mode(value: object = "", *, default: str = "fan") -> str:
    """解析台风数据形态，未知或缺失值按指定默认值返回。"""
    text = str(value or "").strip().lower()
    return MODE_ALIASES.get(text, default)
