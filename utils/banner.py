"""
启动横幅与汇总大屏生成器。

负责三件事：
1. 插件重载时打印组织名 Pancakes-Labs 的 ASCII art 横幅（bold_cyan 配色，
   带框线），艺术字下方的插件名、版本与简介水平居中；
2. 启动静默真正结束时（_force_ready）打印一张启动汇总大屏，把散落的初始化
   信息一次性汇总展示，替代原先“车间流水账”式的逐行 INFO 日志；
3. 服务停止时打印一张停止汇总大屏，汇总资源回收与停机耗时。

排版说明：
- 面板统一使用窄宽度（默认 60 列）框线，连接/轮询明细按列对齐，减少留白；
- 使用 _display_width() 基于 unicodedata.east_asian_width() 精确估算显示宽度，
  中英/emoji 混排时右侧框线也能严格对齐；
- 使用 shutil.get_terminal_size() 探测终端宽度，宽度不足时 Pancakes 与 Labs
  自动换行显示，避免被终端折行破坏等宽效果。

颜色说明：
- 仅当终端支持 ANSI 颜色（stdout 为 TTY 且非 NO_COLOR 环境）时启用 bold_cyan；
- 否则回退为纯文本，保证日志文件与不支持彩色的终端下依然可读。
"""

from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from datetime import datetime, timezone
from typing import Any

from astrbot.api import logger

from .version import get_plugin_version

# ---------------------------------------------------------------------------
# Pancakes-Labs ASCII art（等宽字符，bold_cyan 配色）
# ---------------------------------------------------------------------------
_ASCII_ART_PANCAKES = r"""██████╗  █████╗ ███╗   ██╗ ██████╗ █████╗ ██╗  ██╗███████╗███████╗
██╔══██╗██╔══██╗████╗  ██║██╔════╝██╔══██╗██║ ██╔╝██╔════╝██╔════╝
██████╔╝███████║██╔██╗ ██║██║     ███████║█████╔╝ █████╗  ███████╗█████╗
██╔═══╝ ██╔══██║██║╚██╗██║██║     ██╔══██║██╔═██╗ ██╔══╝  ╚════██║╚════╝
██║     ██║  ██║██║ ╚████║╚██████╗██║  ██║██║  ██╗███████╗███████║
╚═╝     ╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝"""

_ASCII_ART_LABS = r"""██╗      █████╗ ██████╗ ███████╗
██║     ██╔══██╗██╔══██╗██╔════╝
██║     ███████║██████╔╝███████╗
██║     ██╔══██║██╔══██╗╚════██║
███████╗██║  ██║██████╔╝███████║
╚══════╝╚═╝  ╚═╝╚═════╝ ╚══════╝"""


# ---------------------------------------------------------------------------
# ANSI 颜色工具
# ---------------------------------------------------------------------------
_ANSI_BOLD_CYAN = "\x1b[1;36m"
_ANSI_RESET = "\x1b[0m"


def _supports_color() -> bool:
    """是否启用 ANSI 颜色：stdout 为 TTY 且未显式禁用颜色。"""
    if os.environ.get("NO_COLOR"):
        return False
    try:
        stream = getattr(sys, "stdout", None)
        return bool(stream is not None and stream.isatty())
    except Exception:
        return False


def _paint_bold_cyan(text: str) -> str:
    """用 bold_cyan 着色（若终端不支持颜色则原样返回）。"""
    if not _supports_color():
        return text
    return f"{_ANSI_BOLD_CYAN}{text}{_ANSI_RESET}"


# ---------------------------------------------------------------------------
# 终端宽度探测
# ---------------------------------------------------------------------------
def _terminal_width() -> int:
    try:
        size = shutil.get_terminal_size(fallback=(80, 24))
        return int(size.columns)
    except Exception:
        return 80


# ---------------------------------------------------------------------------
# ASCII art 生成（含宽度自适应）
# ---------------------------------------------------------------------------
def build_banner_lines() -> list[str]:
    """生成带边框的横幅文本行（未着色），供打印与测试使用。"""
    width = _terminal_width()
    pancakes_lines = _ASCII_ART_PANCAKES.splitlines()
    labs_lines = _ASCII_ART_LABS.splitlines()

    # 单行并排所需的最小列数：取 Pancakes 行最大宽 + 1 空格 + Labs 行最大宽
    max_pancakes_width = max(len(line) for line in pancakes_lines)
    max_labs_width = max(len(line) for line in labs_lines)
    single_line_width = max_pancakes_width + 1 + max_labs_width

    # 宽度充足（>= 单行所需）时单行并排，否则 Labs 换行。
    # 注意：Pancakes 各行宽度不一（第 3/4 行含额外块字符），
    # 必须按最大宽度 ljust 补齐后再拼接，否则 Labs 列会对不齐。
    if width >= single_line_width:
        art_lines: list[str] = []
        for p, lab in zip(pancakes_lines, labs_lines):
            art_lines.append(f"{p.ljust(max_pancakes_width)} {lab}")
    else:
        art_lines = list(pancakes_lines) + list(labs_lines)

    # 计算整个横幅的显示宽度（取 art 行最大宽 + 两侧留白 + 边框竖线）
    art_width = max(_display_width(line) for line in art_lines)
    inner_width = art_width + 4  # 左右各留 2 空格

    version = (get_plugin_version() or "").strip()
    caption_lines = [
        f"Disaster Warning Plugin  ·  {version}",
        "多数据源灾害预警插件 · 地震 / 海啸 / 气象 / 台风",
    ]

    lines: list[str] = []
    lines.append(_PANEL_TOP + _PANEL_HORIZONTAL * inner_width + _PANEL_TOP_RIGHT)
    for art in art_lines:
        lines.append(
            f"{_PANEL_VERTICAL}  {_pad(art, inner_width - 4)}  {_PANEL_VERTICAL}"
        )
    lines.append(
        _PANEL_TITLE_RIGHT + _PANEL_HORIZONTAL * inner_width + _PANEL_TITLE_LEFT
    )
    for cap in caption_lines:
        lines.append(
            f"{_PANEL_VERTICAL} {_center(cap, inner_width - 2)} {_PANEL_VERTICAL}"
        )
    lines.append(_PANEL_BOTTOM + _PANEL_HORIZONTAL * inner_width + _PANEL_BOTTOM_RIGHT)
    return lines


def print_banner() -> None:
    """打印 Pancakes-Labs ASCII art 横幅（bold_cyan 配色，带边框，说明居中）。"""
    for line in build_banner_lines():
        logger.info(_paint_bold_cyan(line))


# ---------------------------------------------------------------------------
# 启动汇总大屏生成
# ---------------------------------------------------------------------------
# 连接分组名 -> 展示名（大屏连接明细用）
# 场景投影：启动大屏为排版对齐做了微调（追加"数据源"后缀、空格分隔），
# 与 display_registry.CONNECTION_DISPLAY_NAMES（管理端口径）存在有意差异，
# 此处保留大屏专用文案，修改展示名时请同时检查两处。
_CONNECTION_LABELS: dict[str, str] = {
    "fan_studio_all": "FAN Studio 数据源",
    "fan_studio_cenc_ir": "FAN Studio 烈度速报",
    "p2p_main": "P2P 地震情报",
    "wolfx_all": "Wolfx 数据源",
    "openquake_api": "OpenQuake API",
}

# 轮询服务属性名 -> 展示名（大屏轮询明细用）
_POLL_SERVICE_ATTRS: list[tuple[str, str]] = [
    ("snet_poll_service", "S-Net 测站分布"),
    ("eqsc_tsunami_poll_service", "EQSC 海啸轮询"),
    ("eqsc_typhoon_poll_service", "EQSC 台风轮询"),
    ("eqsc_cenc_intensity_poll_service", "EQSC 烈度速报"),
]

_PANEL_TOP = "┌"
_PANEL_TOP_RIGHT = "┐"
_PANEL_BOTTOM = "└"
_PANEL_BOTTOM_RIGHT = "┘"
_PANEL_HORIZONTAL = "─"
_PANEL_VERTICAL = "│"
_PANEL_TITLE_LEFT = "┤"
_PANEL_TITLE_RIGHT = "├"


def _ellipsize(text: str, max_width: int) -> str:
    """按显示宽度做中段省略：保留头部与尾部，中间用 … 连接。

    用于数据库路径等超长值，避免撑破面板右侧框线。
    """
    if max_width < 6:
        max_width = 6
    if _display_width(text) <= max_width:
        return text
    # 预留省略号宽度
    ellipsis = "…"
    keep = max_width - _display_width(ellipsis)
    head_w = (keep * 2) // 3
    tail_w = keep - head_w
    # 按显示宽度裁剪 head/tail（避免半截宽字符）
    head: list[str] = []
    acc = 0
    for ch in text:
        w = _display_width(ch)
        if acc + w > head_w:
            break
        acc += w
        head.append(ch)
    tail: list[str] = []
    acc = 0
    for ch in reversed(text):
        w = _display_width(ch)
        if acc + w > tail_w:
            break
        acc += w
        tail.append(ch)
    return "".join(head) + ellipsis + "".join(reversed(tail))


def _display_width(text: str) -> int:
    """估算字符串在终端的显示宽度（中英混排右线对齐的正解）。

    使用 unicodedata.east_asian_width() 判断：
    - "W" / "F"（全角/宽）→ 2 列
    - 其余（含 "A" 模糊宽度，如块字符 █╗、· 等）→ 1 列
      在等宽终端里这些字符均占 1 列，按 2 计会高估宽度导致右线错开。
    - 零宽字符（ZWJ U+200D、变体选择符 U+FE00~FE0F）→ 0 列
    - 后随变体选择符 U+FE0F 的字符按 emoji 呈现、占 2 列，
      避免 EAW 为 "N" 的基础字符（如时钟类符号）被低估为 1 列。

    提示：为彻底规避终端 emoji 宽度差异，大屏中优先选用 EAW=W 的
    确定性 emoji，FE0F 处理仅作为兜底。
    """
    width = 0
    for i, ch in enumerate(text):
        code = ord(ch)
        # 零宽字符（ZWJ U+200D、变体选择符 U+FE00~FE0F）不占列宽
        if code == 0x200D or 0xFE00 <= code <= 0xFE0F:
            continue
        base = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        # 后随 FE0F 的字符按 emoji 渲染（2 列）
        if base == 1 and i + 1 < len(text) and ord(text[i + 1]) == 0xFE0F:
            base = 2
        width += base
    return width


def _pad(text: str, width: int) -> str:
    """按显示宽度补齐到指定宽度（半角占 1，全角/emoji 占 2）。"""
    return text + " " * max(0, width - _display_width(text))


def _center(text: str, width: int) -> str:
    """按显示宽度水平居中，左补 1 格保持视觉平衡。"""
    pad_total = max(0, width - _display_width(text))
    left = pad_total // 2
    right = pad_total - left
    return " " * left + text + " " * right


class StartupSummaryPanel:
    """启动汇总大屏：收集服务启动关键信息并排版为框线面板。"""

    def __init__(self, service: Any) -> None:
        self.service = service

    # -- 字段采集 ----------------------------------------------------------
    def _collect(self) -> dict[str, Any]:
        service = self.service
        data: dict[str, Any] = {}

        data["version"] = get_plugin_version()

        # 启动耗时：从服务初始化开始（init_started_at）到静默启动结束（ready_at）。
        coordinator = getattr(service, "startup_silence", None)
        ready_at = getattr(coordinator, "ready_at", None)
        init_started = getattr(service, "init_started_at", None)
        elapsed: float | None = None
        if ready_at is not None and init_started is not None:
            try:
                start = (
                    init_started.replace(tzinfo=None)
                    if getattr(init_started, "tzinfo", None)
                    else init_started
                )
                end = (
                    ready_at.replace(tzinfo=None)
                    if getattr(ready_at, "tzinfo", None)
                    else ready_at
                )
                elapsed = (end - start).total_seconds()
            except Exception:
                elapsed = None
        data["elapsed"] = elapsed

        # WebSocket 连接
        ws_manager = getattr(service, "ws_manager", None)
        connected = set(getattr(ws_manager, "connections", {}) or {})
        plan = getattr(service, "connections", {}) or {}
        data["ws_connected"] = sorted(connected)
        data["ws_expected"] = sorted(str(k) for k in plan.keys())
        data["ws_skipped"] = sorted(
            str(k) for k in data["ws_expected"] if k not in connected
        )

        # 轮询服务
        poll_states: list[tuple[str, bool]] = []
        for attr, label in _POLL_SERVICE_ATTRS:
            svc = getattr(service, attr, None)
            if svc is None:
                continue
            enabled = bool(getattr(svc, "is_enabled", lambda: False)())
            poll_states.append((label, enabled))
        data["polls"] = poll_states

        # 历史记录
        stats_mgr = getattr(service, "statistics_manager", None)
        stats = getattr(stats_mgr, "stats", None) if stats_mgr else None
        if isinstance(stats, dict):
            data["history_count"] = len(stats.get("recent_pushes") or [])
        else:
            data["history_count"] = None

        # 静默吸收快照
        data["absorbed"] = getattr(coordinator, "absorbed_events", None)

        # 数据库信息：完整路径太长（固定前缀就超宽），大屏只展示文件名 + 大小。
        # 主服务无 database_manager 属性，实际挂在 statistics_manager.db。
        db_path = None
        db_manager = getattr(service, "database_manager", None)
        if db_manager is not None:
            db_path = getattr(db_manager, "db_path", None)
        if db_path is None:
            stats_mgr_for_db = getattr(service, "statistics_manager", None)
            if stats_mgr_for_db is not None:
                db_obj = getattr(stats_mgr_for_db, "db", None)
                if db_obj is not None:
                    db_path = getattr(db_obj, "db_path", None)
        data["db_path"] = str(db_path) if db_path else None
        if db_path:
            db_file = os.path.basename(str(db_path))
            try:
                db_size = os.path.getsize(str(db_path))
            except Exception:
                db_size = None
            if db_size is not None:
                if db_size >= 1024 * 1024:
                    db_size_text = f"{db_size / (1024 * 1024):.1f} MB"
                elif db_size >= 1024:
                    db_size_text = f"{db_size / 1024:.1f} KB"
                else:
                    db_size_text = f"{db_size} B"
                data["db_path"] = f"{db_file} · {db_size_text}"
            else:
                data["db_path"] = db_file

        # Web 管理端地址（web_server.start() 时已记录 self.url）
        web_server = getattr(service, "web_admin_server", None)
        web_url = getattr(web_server, "url", None) or getattr(
            web_server, "base_url", None
        )
        data["web_url"] = str(web_url) if web_url else None

        # 浏览器渲染服务：本地模式 / 远程模式。
        browser: Any = None
        message_manager = getattr(service, "message_manager", None)
        if message_manager is not None:
            browser = getattr(message_manager, "browser_manager", None)
        browser_info: str | None = None
        if browser is not None:
            mode = getattr(browser, "_mode", "") or ""
            server_url = getattr(browser, "_server_url", "") or ""
            if mode == "remote":
                browser_info = f"远程模式 · {server_url}" if server_url else "远程模式"
            else:
                browser_info = "本地模式"
        data["browser"] = browser_info

        # 配置校验结果：主服务校验通过后记录校验状态与耗时。
        validator = getattr(service, "_config_validator", None)
        if validator is None:
            validator = getattr(service, "config_validator", None)
        validate_ok = None
        if validator is not None:
            validate_ok = getattr(validator, "last_validation_ok", None)
        data["config_valid"] = validate_ok

        return data

    # -- 排版 ---------------------------------------------------------------
    def build(self, width: int = 60) -> list[str]:
        """生成大屏文本行（不含边框颜色，纯框线）。"""
        data = self._collect()

        lines: list[str] = []
        inner = max(10, width - 2)

        def top(title: str = "") -> str:
            if title:
                left = title + " "
                pad_len = inner - _display_width(left) - 1
                return (
                    f"{_PANEL_TOP}{_PANEL_HORIZONTAL * pad_len}"
                    f"{_PANEL_TITLE_LEFT} {left}{_PANEL_TITLE_RIGHT}"
                )
            return _PANEL_TOP + _PANEL_HORIZONTAL * inner + _PANEL_TOP_RIGHT

        def bottom() -> str:
            return _PANEL_BOTTOM + _PANEL_HORIZONTAL * inner + _PANEL_BOTTOM_RIGHT

        def row(text: str = "") -> str:
            return f"{_PANEL_VERTICAL} {_pad(text, inner - 1)}{_PANEL_VERTICAL}"

        def divider() -> str:
            # 与 top()/bottom() 保持一致的 inner 个横线，避免右竖线少一格。
            return f"{_PANEL_TITLE_RIGHT}{_PANEL_HORIZONTAL * inner}{_PANEL_TITLE_LEFT}"

        lines.append(top())
        lines.append(row(""))
        lines.append(
            row(
                _pad(
                    f"🌋 Disaster Warning Plugin · {data.get('version') or '?'}",
                    inner - 1,
                )
            )
        )
        lines.append(row(""))
        lines.append(divider())

        # 运行信息（双栏/单栏自适应）
        elapsed = data.get("elapsed")
        elapsed_text = f"{elapsed:.2f} 秒" if elapsed is not None else "N/A"
        ws_text = f"{len(data['ws_connected'])}/{len(data['ws_expected'])} 已连接"
        poll_ok = sum(1 for _, ok in data["polls"] if ok)
        poll_total = len(data["polls"])
        poll_text = f"{poll_ok}/{poll_total} 已就绪"
        history_text = (
            f"{data['history_count']} 条"
            if data.get("history_count") is not None
            else "N/A"
        )
        absorbed_text = (
            f"{data['absorbed']} 条" if data.get("absorbed") is not None else "N/A"
        )

        info_rows: list[tuple[str, str]] = [
            ("✨ 启动耗时", elapsed_text),
            ("📡 WebSocket", ws_text),
            ("🔄 轮询服务", poll_text),
            ("📚 加载历史记录", history_text),
            ("🧹 静默吸收快照", absorbed_text),
        ]
        db_path = data.get("db_path")
        if db_path:
            # 数据库路径可能很长，单栏整行展示
            info_rows.append(("💾 数据库", db_path))
        browser_info = data.get("browser")
        if browser_info:
            info_rows.append(("🌍 浏览器渲染", browser_info))
        config_valid = data.get("config_valid")
        if config_valid is not None:
            info_rows.append(("📋 配置校验", "✅ 通过" if config_valid else "❌ 异常"))
        web_url = data.get("web_url")
        if web_url:
            info_rows.append(("🌐 Web 管理端", web_url))

        # 运行信息采用单栏整行展示，键列与值列分别按固定宽度对齐，观感更整洁。
        # 键列宽 = 各键最大显示宽度（emoji 宽度差异由 _pad 吸收）；
        # 值列宽 = 内容区(inner-1) - 键列宽(key_width) - 分隔符("  "占2) - 值前空格
        key_width = max(_display_width(k) for k, _ in info_rows)
        value_width = inner - 1 - key_width - 2 - 1
        for k, v in info_rows:
            # 超长值（如数据库路径）中段省略，避免撑破右线。
            short_v = _ellipsize(str(v), value_width)
            lines.append(row(f"{_pad(k, key_width)}  {_pad(short_v, value_width)}"))

        lines.append(divider())

        # 连接明细：展示名前置（隐藏内部连接名），状态列左对齐，右侧对齐说明。
        lines.append(row("连接明细"))
        if data["ws_expected"]:
            # 展示名（_CONNECTION_LABELS 映射）统一按最大宽度对齐
            display_names = [
                _CONNECTION_LABELS.get(name, name) for name in data["ws_expected"]
            ]
            name_width = max(_display_width(n) for n in display_names)
            status_width = max(_display_width("✅ 已连接"), _display_width("⚠️ 未连接"))
            for name in data["ws_expected"]:
                ok = name in data["ws_connected"]
                status = "✅ 已连接" if ok else "⚠️ 未连接"
                label = _CONNECTION_LABELS.get(name, name)
                # 展示名 + 状态固定宽，说明靠左（展示名宽度已覆盖最长项）
                lines.append(
                    row(
                        _pad(
                            f"{_pad(label, name_width)}  {_pad(status, status_width)}",
                            inner - 1,
                        )
                    )
                )
        else:
            lines.append(row(_pad("（无 WebSocket 连接）", inner - 1)))

        # 轮询明细：展示名 / 状态 两列对齐
        lines.append(row("轮询明细"))
        if data["polls"]:
            poll_name_width = max(_display_width(label) for label, _ in data["polls"])
            status_width = max(_display_width("✅ 已就绪"), _display_width("⚪ 未启用"))
            for label, ok in data["polls"]:
                status = "✅ 已就绪" if ok else "⚪ 未启用"
                lines.append(
                    row(
                        _pad(
                            f"{_pad(label, poll_name_width)}  {_pad(status, status_width)}",
                            inner - 1,
                        )
                    )
                )
        else:
            lines.append(row(_pad("（无轮询服务）", inner - 1)))

        lines.append(divider())

        # 状态行
        all_ok = len(data["ws_skipped"]) == 0 and poll_ok == poll_total
        status_text = (
            "所有数据源已就绪，静默结束，开始正常推送"
            if all_ok
            else "存在未就绪数据源，已结束静默（见上方状态）"
        )
        lines.append(row(f"状态：{status_text}"))
        lines.append(bottom())
        return lines

    def print_summary(self) -> None:
        """打印启动汇总大屏（INFO 级）。"""
        for line in self.build():
            logger.info(line)


def print_startup_summary(service: Any) -> None:
    """便捷入口：从主服务实例打印启动汇总大屏。"""
    StartupSummaryPanel(service).print_summary()


# ---------------------------------------------------------------------------
# 停止汇总大屏生成
# ---------------------------------------------------------------------------
class StopSummaryPanel:
    """停止汇总大屏：收集服务停止关键信息并排版为框线面板。

    与 StartupSummaryPanel 保持一致的框线工具与双栏/单栏自适应排版，
    但聚焦"资源回收 + 停机耗时"维度，替代停止流程中散落的逐行 INFO 流水，
    让停机阶段只剩一张干净的汇总大屏。
    """

    def __init__(self, service: Any) -> None:
        self.service = service

    # -- 字段采集 ----------------------------------------------------------
    def _collect(self) -> dict[str, Any]:
        service = self.service
        data: dict[str, Any] = {}

        data["version"] = get_plugin_version()

        # 停止耗时：从停止流程开始（stop_started_at）到汇总大屏生成时刻。
        stop_started = getattr(service, "stop_started_at", None)
        elapsed: float | None = None
        if stop_started is not None:
            try:
                start = (
                    stop_started.replace(tzinfo=None)
                    if getattr(stop_started, "tzinfo", None)
                    else stop_started
                )
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                elapsed = max(0.0, (now - start).total_seconds())
            except Exception:
                elapsed = None
        data["elapsed"] = elapsed

        # WebSocket 连接：按连接计划（connections）统计启用数量，停止时全部回收。
        plan = getattr(service, "connections", {}) or {}
        data["ws_total"] = len(plan)
        data["ws_disconnected"] = len(plan)

        # 停机前气象预警聚合转发的最后一批条数与目标会话（flush_all 发送成功后记录）。
        agg = getattr(service, "_weather_aggregation_service", None)
        data["last_forward_count"] = getattr(agg, "last_flushed_count", None)
        last_session = getattr(agg, "last_flushed_session", None)
        if last_session:
            # 复用会话配置管理器格式化会话日志字符串（私聊/群聊 ID (备注名)）。
            session_mgr = getattr(service, "session_config_manager", None)
            formatter = (
                getattr(session_mgr, "get_session_log_str", None)
                if session_mgr is not None
                else None
            )
            data["last_forward_session"] = (
                formatter(last_session) if callable(formatter) else last_session
            )
        else:
            data["last_forward_session"] = None

        # 停止流程中各资源的真实回收状态（基于运行态推导，避免无条件显示已停止）。
        # 注意：浏览器、后台延迟检测与 Web 管理端并不在本停止流程内回收，
        # 它们由 main.terminate() 在生命周期 stop() 之后执行，此处如实反映运行态。
        health_service = getattr(service, "connection_health_service", None)
        if health_service is not None:
            data["health_stopped"] = not bool(
                getattr(health_service, "_running", False)
            )
        else:
            data["health_stopped"] = None

        notification_center = getattr(service, "notification_center", None)
        if notification_center is not None:
            poll_task = getattr(notification_center, "_poll_task", None)
            data["notification_stopped"] = poll_task is None or bool(
                getattr(poll_task, "done", lambda: True)()
            )
        else:
            data["notification_stopped"] = None

        stats_mgr = getattr(service, "statistics_manager", None)
        if stats_mgr is not None:
            data["db_closed"] = not bool(getattr(stats_mgr, "_db_initialized", False))
        else:
            data["db_closed"] = None

        message_manager = getattr(service, "message_manager", None)
        browser = (
            getattr(message_manager, "browser_manager", None)
            if message_manager is not None
            else None
        )
        if browser is not None:
            data["browser_closed"] = bool(getattr(browser, "_closed", False))
        else:
            data["browser_closed"] = None

        web_server = getattr(service, "web_admin_server", None)
        if web_server is not None and getattr(web_server, "server", None) is not None:
            ping_task = getattr(web_server, "_ping_task", None)
            data["monitor_stopped"] = ping_task is None or bool(
                getattr(ping_task, "done", lambda: True)()
            )
            server_task = getattr(web_server, "_server_task", None)
            data["web_stopped"] = server_task is None or bool(
                getattr(server_task, "done", lambda: True)()
            )
        else:
            data["monitor_stopped"] = None
            data["web_stopped"] = None

        # 缓存保存：停止流程仅在服务曾真正运行（was_running）时执行落盘，
        # 用 start_time 是否存在近似推导 was_running。
        was_running = getattr(service, "start_time", None) is not None
        data["cache_saved"] = was_running

        return data

    # -- 排版 ---------------------------------------------------------------
    def build(self, width: int = 60) -> list[str]:
        """生成停止汇总大屏文本行（不含边框颜色，纯框线）。"""
        data = self._collect()

        lines: list[str] = []
        inner = max(10, width - 2)

        def top(title: str = "") -> str:
            if title:
                left = title + " "
                pad_len = inner - _display_width(left) - 1
                return (
                    f"{_PANEL_TOP}{_PANEL_HORIZONTAL * pad_len}"
                    f"{_PANEL_TITLE_LEFT} {left}{_PANEL_TITLE_RIGHT}"
                )
            return _PANEL_TOP + _PANEL_HORIZONTAL * inner + _PANEL_TOP_RIGHT

        def bottom() -> str:
            return _PANEL_BOTTOM + _PANEL_HORIZONTAL * inner + _PANEL_BOTTOM_RIGHT

        def row(text: str = "") -> str:
            return f"{_PANEL_VERTICAL} {_pad(text, inner - 1)}{_PANEL_VERTICAL}"

        def divider() -> str:
            # 与 top()/bottom() 保持一致的 inner 个横线，避免右竖线少一格。
            return f"{_PANEL_TITLE_RIGHT}{_PANEL_HORIZONTAL * inner}{_PANEL_TITLE_LEFT}"

        lines.append(top())
        lines.append(row(""))
        lines.append(
            row(
                _pad(
                    f"🌋 Disaster Warning Plugin · {data.get('version') or '?'} — 已停止",
                    inner - 1,
                )
            )
        )
        lines.append(row(""))
        lines.append(divider())

        # 停止明细：以 emoji 清单形式逐项展示资源回收状态。
        # 键列（emoji + 名称）按显示宽度统一对齐，✅ 状态列与右竖线随之对齐，
        # 避免不同 emoji 基础字符宽度差异导致硬编码空格错位。
        lines.append(row("停止明细"))

        detail_rows: list[tuple[str, str]] = []

        elapsed_text = (
            f"{data['elapsed']:.2f} 秒" if data.get("elapsed") is not None else "N/A"
        )
        detail_rows.append(("⏳ 停止耗时", elapsed_text))

        last_forward = data.get("last_forward_count")
        forward_text = f"{last_forward} 条预警" if last_forward is not None else "无"
        last_session = data.get("last_forward_session")
        if last_session:
            forward_text = f"{forward_text} · {last_session}"
        detail_rows.append(("📨 最后转发", forward_text))

        ws_total = data.get("ws_total", 0)
        detail_rows.append(
            (
                "🔌 WebSocket 管理器",
                f"✅ 已停止 · {data.get('ws_disconnected', 0)}/{ws_total} 已断开",
            )
        )

        def state_text(stopped: bool | None) -> str:
            """把资源回收状态渲染为面板文案（None 表示未启用/无该组件）。"""
            if stopped is None:
                return "⚪ 未启用"
            return "✅ 已完成" if stopped else "⚠️ 未完成"

        detail_rows.append(("🩺 连接健康采样", state_text(data.get("health_stopped"))))
        detail_rows.append(
            ("🔔 通知系统", state_text(data.get("notification_stopped")))
        )
        detail_rows.append(("💾 数据库", state_text(data.get("db_closed"))))
        detail_rows.append(("🌍 浏览器", state_text(data.get("browser_closed"))))
        detail_rows.append(("🔍 后台延迟检测", state_text(data.get("monitor_stopped"))))
        detail_rows.append(("🌐 Web 管理端", state_text(data.get("web_stopped"))))
        detail_rows.append(
            ("📦 缓存已保存", "✅ 已保存" if data.get("cache_saved") else "⚪ 未保存")
        )

        key_width = max(_display_width(k) for k, _ in detail_rows)
        value_width = inner - 1 - key_width - 2 - 1
        for k, v in detail_rows:
            # 超长值（如带会话备注名的最后转发）中段省略，避免撑破右线。
            short_v = _ellipsize(str(v), value_width)
            lines.append(row(f"{_pad(k, key_width)}  {_pad(short_v, value_width)}"))

        lines.append(divider())

        # 状态行
        lines.append(row("状态：所有服务已安全停止，退出流程完成 ✨"))
        lines.append(bottom())
        return lines

    def print_summary(self) -> None:
        """打印停止汇总大屏（INFO 级）。"""
        for line in self.build():
            logger.info(line)


def print_stop_summary(service: Any) -> None:
    """便捷入口：从主服务实例打印停止汇总大屏。"""
    StopSummaryPanel(service).print_summary()


__all__ = [
    "build_banner_lines",
    "print_banner",
    "StartupSummaryPanel",
    "print_startup_summary",
    "StopSummaryPanel",
    "print_stop_summary",
]
