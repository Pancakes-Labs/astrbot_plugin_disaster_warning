"""AstrBot 加载完成标记，用于区分「插件重载」与「AstrBot 首次启动/进程重启」。

背景：
- AstrBot 首次启动 / 进程重启时，on_astrbot_loaded 钩子会在 AstrBot 加载完成后触发，
  静默武装可以安全推迟到该时刻，避免 30 秒硬超时被 AstrBot 加载耗时提前耗尽。
- 插件单独重载时，该钩子不会再次触发（PluginManager.reload 只走 terminate + load），
  但此时 AstrBot 早已就绪，应跳过等待直接武装。

由于插件重载时会重新执行模块代码（模块级变量丢失），无法用内存标志区分，
这里借助持久化标记 + 进程 PID 校验：
- 标记不存在，或标记 PID 与当前进程不一致 → 首次启动/进程重启 → 等待钩子；
- 标记存在且 PID 与当前进程一致 → 本次进程内已完成加载 → 插件重载 → 立即武装。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from astrbot.api.star import StarTools

_MARKER_FILENAME = "astrbot_loaded_marker.json"


def _marker_path() -> Path:
    return StarTools.get_data_dir("astrbot_plugin_disaster_warning") / _MARKER_FILENAME


def mark_astrbot_loaded() -> None:
    """记录 AstrBot 已完成一次加载（携带当前进程 PID）。

    仅在 on_astrbot_loaded 钩子中调用，作为「本次进程内已加载完成」的凭证。
    """
    try:
        data: dict[str, Any] = {"pid": os.getpid()}
        path = _marker_path()
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        # 标记写入失败只影响场景区分精度，不阻断主流程。
        pass


def is_first_boot_in_process() -> bool:
    """判断当前是否为 AstrBot 首次启动 / 进程重启。

    Returns:
        True: 需要等待 on_astrbot_loaded 钩子再武装静默；
        False: 本次进程内已加载完成（插件重载场景），应跳过等待立即武装。
    """
    try:
        path = _marker_path()
        if not path.exists():
            return True
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("pid", -1)) != os.getpid()
    except Exception:
        # 读取失败时保守按首次启动处理，静默仍可被钩子正常武装。
        return True


__all__ = ["mark_astrbot_loaded", "is_first_boot_in_process"]
