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

为避免操作系统复用 PID 导致的误判（新进程被误认为插件重载而跳过等待），
同时记录进程启动时间戳（boot_id / 进程创建时刻）作为第二重校验。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.star import StarTools

_MARKER_FILENAME = "astrbot_loaded_marker.json"


def _marker_path() -> Path:
    return StarTools.get_data_dir("astrbot_plugin_disaster_warning") / _MARKER_FILENAME


def _current_process_boot_id() -> str | None:
    """获取当前进程的启动标识（Linux boot_id，避免 PID 复用误判）。

    优先读取 /proc/sys/kernel/random/boot_id（Linux 特有、进程重启后变化）；
    其他平台或读取失败时返回 None，交由调用方仅以 PID 作保守判断。
    """
    try:
        boot_id = (
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        )
        if boot_id:
            return boot_id
    except Exception:
        pass
    return None


def _current_process_start_time() -> float | None:
    """获取当前进程的启动时间（秒级时间戳，尽力而为）。

    平台差异：
    - Linux：从 /proc/self/stat 的 starttime（第 22 字段，时钟节拍）换算；
    - Windows/macOS：退化为进程创建时间（psutil 不可用时返回 None）。
    """
    # Linux：/proc/self/stat 第 22 字段为进程启动时钟节拍数
    try:
        stat_text = Path("/proc/self/stat").read_text(encoding="utf-8")
        # comm 字段可能含空格/括号，从最后一个 ')' 之后开始解析
        after_comm = stat_text.rsplit(")", 1)[-1].split()
        # after_comm 索引：state(3) ppid(4) ... starttime(22)，
        # 相对 comm 之后依次为 state(0) ppid(1) pgrp(2) session(3) tty_nr(4)
        # tpgid(5) flags(6) minflt(7) cminflt(8) majflt(9) cmajflt(10)
        # utime(11) stime(12) cutime(13) cstime(14) priority(15) nice(16)
        # num_threads(17) itrealvalue(18) starttime(19)
        if len(after_comm) > 19:
            start_ticks = float(after_comm[19])
            # /proc/stat 中 btime（第 22 字段）为系统启动的墙钟时间戳
            proc_stat = Path("/proc/stat").read_text(encoding="utf-8")
            btime = None
            for line in proc_stat.splitlines():
                if line.startswith("btime "):
                    try:
                        btime = float(line.split()[1])
                    except (IndexError, ValueError):
                        btime = None
                    break
            if btime is not None:
                # 时钟节拍数换算为秒（通常 100 HZ）
                hertz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
                if hertz and hertz > 0:
                    return btime + start_ticks / float(hertz)
    except Exception:
        pass
    return None


def _current_process_identity() -> dict[str, Any]:
    """收集当前进程的识别信息（PID + 可选启动时间/boot_id）。"""
    identity: dict[str, Any] = {"pid": os.getpid()}
    boot_id = _current_process_boot_id()
    if boot_id:
        identity["boot_id"] = boot_id
    start_time = _current_process_start_time()
    if start_time is not None:
        identity["start_time"] = start_time
    return identity


def _marker_matches_current(identity: dict[str, Any]) -> bool:
    """标记中的进程身份与当前进程是否一致。"""
    if int(identity.get("pid", -1)) != os.getpid():
        return False
    # PID 一致时，若双方都有 boot_id，则必须一致（boot_id 跨重启必然变化）；
    # 只有记录方缺少 boot_id 时才退回纯 PID 判断。
    current_boot_id = _current_process_boot_id()
    marker_boot_id = identity.get("boot_id")
    if marker_boot_id and current_boot_id:
        return marker_boot_id == current_boot_id
    # 双方都有启动时间戳时做近似比较（容差 5 秒），避免时钟精度差异误判。
    current_start = _current_process_start_time()
    marker_start = identity.get("start_time")
    if current_start is not None and isinstance(marker_start, (int, float)):
        return abs(current_start - float(marker_start)) < 5.0
    return True


def mark_astrbot_loaded() -> None:
    """记录 AstrBot 已完成一次加载（携带当前进程 PID 与启动标识）。

    仅在 on_astrbot_loaded 钩子中调用，作为「本次进程内已加载完成」的凭证。
    写入失败只影响场景区分精度，不阻断主流程，因此失败时记录 debug 日志。
    """
    try:
        data: dict[str, Any] = _current_process_identity()
        path = _marker_path()
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception as exc:
        logger.debug(f"[灾害预警] 写入 AstrBot 加载完成标记失败: {exc}")


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
        return not _marker_matches_current(data)
    except Exception:
        # 读取失败时保守按首次启动处理，静默仍可被钩子正常武装。
        return True


__all__ = ["mark_astrbot_loaded", "is_first_boot_in_process"]
