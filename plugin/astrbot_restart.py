"""AstrBot 进程重启适配层。

背景
----
AstrBot 在 v4.27.0 中对更新子系统做了破坏性重构（commit 21f41c239,
refactor: simplify updater architecture #9493）：

- 旧版: astrbot/core/updator.py 提供 AstrBotUpdator()._reboot()
- 新版: 模块更名为 astrbot/core/updater.py，类更名为 AstrBotUpdater，
  且 _reboot() 被拆分为独立的 astrbot/core/process_restart.py: restart_process()

插件若在模块顶层直接 from astrbot.core.updator import AstrBotUpdator，
在 v4.27.0+ 上会因 ModuleNotFoundError 导致整个插件加载失败。

因此这里把"重启 AstrBot 进程"这一动作从 AstrBot 内部实现上解耦：
- 使用延迟导入（函数内 import），避免模块顶层触发导入错误；
- 按多策略顺序尝试（新版优先、旧版回退），任一可用即成功；
- 所有异常在适配层内消化，绝不让重启能力缺失影响插件主流程。

对外只暴露一个纯函数 restart_astrbot_in_background()，返回
(ok: bool, error: Exception | None)，调用方无需关心 AstrBot 内部细节。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from astrbot.api import logger

__all__ = ["restart_astrbot_in_background"]


def _probe_process_restart() -> None:
    """新版路径（v4.27.0+）探测：仅触发延迟导入，不执行重启。"""
    from astrbot.core.process_restart import restart_process  # noqa: F401


def _run_process_restart() -> None:
    """新版路径（v4.27.0+）执行：process_restart.restart_process(delay=3)。

    该函数与旧版 _reboot(delay=3) 行为完全等价：
    sleep(delay) -> 终止子进程 -> PyInstaller 环境清理 -> os.exec* 替换进程。
    """
    from astrbot.core.process_restart import restart_process

    restart_process(delay=3)


def _probe_updator() -> None:
    """旧版路径（v4.26.x 及更早）探测：仅触发延迟导入，不执行重启。"""
    from astrbot.core.updator import AstrBotUpdator  # noqa: F401


def _run_updator() -> None:
    """旧版路径（v4.26.x 及更早）执行：AstrBotUpdator()._reboot()。"""
    from astrbot.core.updator import AstrBotUpdator

    AstrBotUpdator()._reboot()


# 按优先级排列的重启策略：(探测函数, 执行函数)。
# 探测函数只负责延迟导入，判断该 AstrBot 版本是否存在对应模块；
# 执行函数才真正触发进程重启。
_RESTART_STRATEGIES: tuple[tuple[Callable[[], None], Callable[[], None]], ...] = (
    (_probe_process_restart, _run_process_restart),
    (_probe_updator, _run_updator),
)


def restart_astrbot_in_background(
    on_failure: Callable[[Exception], None] | None = None,
) -> tuple[bool, Exception | None]:
    """在后台 daemon 线程中触发 AstrBot 进程重启。

    重启动作是同步阻塞的（内部 sleep 3s + 杀子进程 + os.exec*），因此必须
    放入 daemon 线程执行，避免阻塞插件事件循环。

    Args:
        on_failure: 可选失败回调。重启线程内部执行失败（如桌面托管守卫抛错、
            exec 替换失败）时，会在线程内以该回调通知调用方；回调应尽量轻量，
            且自行消化自身异常，避免线程内二次抛错。若为 None，失败仅记录日志。

    Returns:
        (ok, error)：ok 表示是否成功派发重启线程；
        error 为失败原因（ok=False 时），否则为 None。
        调用方无需关心 AstrBot 内部细节，只需据 ok 决定后续提示/遥测。
    """
    # 桌面版 Launcher 托管后端时不允许核心直接重启（新旧版核心均会抛错）。
    # 提前拦截并返回明确错误，避免把内部异常细节带上用户提示。
    try:
        from astrbot.core.desktop_runtime import is_desktop_managed_backend

        if is_desktop_managed_backend():
            return False, RuntimeError(
                "当前由 AstrBot Desktop 托管运行，无法通过核心命令重启"
            )
    except (ImportError, ModuleNotFoundError):
        # 极老的版本可能没有 desktop_runtime；继续尝试重启策略即可。
        pass

    # 先同步探测可用的重启策略（探测函数内仅延迟导入，无副作用），
    # 再只启动探测成功的执行线程。
    # 不能把 Thread.start() 放进 try 里指望捕获 ImportError —— 延迟导入
    # 在线程函数内部，start() 本身不会抛导入错误，那样会导致策略回退
    # 永远不触发、导入失败在线程内静默吞掉。
    for probe, run in _RESTART_STRATEGIES:
        try:
            probe()
        except (ImportError, ModuleNotFoundError):
            # 模块/类不存在（如 v4.27.0+ 没有 updator）→ 换下一个策略。
            continue
        except Exception as exc:  # noqa: BLE001 - 探测阶段异常，换策略
            logger.error(f"[灾害预警] AstrBot 重启策略探测失败: {exc}")
            continue

        # 探测成功：在后台 daemon 线程真正执行重启。
        # 线程内必须捕获异常并回调 on_failure，否则重启失败只会产生未处理的
        # 线程异常，而调用方早已报告成功，用户无法感知失败。
        def _run_with_guard() -> None:
            try:
                run()
            except Exception as exc:  # noqa: BLE001 - 线程边界兜底
                logger.error(f"[灾害预警] AstrBot 重启线程执行失败: {exc}")
                if on_failure is not None:
                    try:
                        on_failure(exc)
                    except Exception:  # noqa: BLE001 - 回调自身异常不允许二次抛错
                        logger.error(f"[灾害预警] AstrBot 重启失败回调执行出错: {exc}")

        threading.Thread(
            target=_run_with_guard,
            name="astrbot-core-restart",
            daemon=True,
        ).start()
        return True, None

    logger.warning("[灾害预警] 当前 AstrBot 版本无可用重启策略，无法重启进程。")
    return False, RuntimeError("当前 AstrBot 版本无可用重启策略")
