"""
扩展管理器：发现、加载、隔离并驱动扩展的轮询任务。

任何扩展的任何失败都只会让该扩展进入 error 状态，并回收它已注册的内容，
绝不影响宿主和其他扩展。
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import ssl
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import yaml

from astrbot.api import logger
from astrbot.api.star import StarTools

from .api import ExtensionAPI, PollerSpec
from .manifest import check_host_version, load_manifest

PLUGIN_DIR = Path(__file__).resolve().parents[2]
PLUGIN_NAME = "astrbot_plugin_disaster_warning"


def _build_ssl_context() -> ssl.SSLContext:
    """优先使用 certifi 根证书。

    部分系统证书库缺少较新的根证书（如 CEMADEN 使用的 GlobalSign Root R46），
    导致 CERTIFICATE_VERIFY_FAILED。
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class ExtensionManager:
    """见 docs/extensions/design.md 第 5 节。"""

    MAX_BACKOFF_FACTOR = 10  # 退避上限：轮询间隔的倍数
    LOG_THROTTLE_SECONDS = 600  # 同一轮询任务的错误日志最小间隔

    def __init__(
        self,
        service,
        host_registry,
        *,
        roots: list[Path] | None = None,
        host_version: str | None = None,
        data_dir: Path | None = None,
    ):
        self.service = service
        self.host_registry = host_registry
        self._data_dir = data_dir
        self._roots = roots
        self.host_version = host_version or self._read_host_version()
        self._records: list[dict[str, Any]] = []  # status() 的数据来源
        self._apis: dict[str, ExtensionAPI] = {}  # 加载成功的扩展
        self._tasks: list[asyncio.Task] = []
        self._session: aiohttp.ClientSession | None = None

    # ---------------- 加载 ----------------

    @staticmethod
    def _read_host_version() -> str:
        try:
            meta = yaml.safe_load((PLUGIN_DIR / "metadata.yaml").read_text("utf-8"))
            return str(meta.get("version") or "0")
        except Exception:
            return "0"

    def _base_data_dir(self) -> Path:
        return self._data_dir or StarTools.get_data_dir(PLUGIN_NAME)

    def _get_roots(self) -> list[Path]:
        if self._roots is not None:
            return list(self._roots)
        return [PLUGIN_DIR / "extensions", self._base_data_dir() / "extensions"]

    def _settings(self, cfg: dict) -> dict:
        """settings 可能是 JSON 文本或已解析的 dict；非法时视为空。"""
        raw = cfg.get("settings") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "{}")
            except ValueError as e:
                logger.warning(f"[灾害预警] 扩展 settings 不是合法 JSON，已忽略: {e}")
                return {}
        return raw if isinstance(raw, dict) else {}

    def _record(self, ext_id: str, state: str, error: str = "", **extra) -> dict:
        rec = {
            "id": ext_id,
            "name": extra.pop("name", ext_id),
            "version": extra.pop("version", ""),
            "state": state,
            "error": error,
            "sources": extra.pop("sources", []),
        }
        self._records.append(rec)
        return rec

    def _unregister_all(self, ext_id: str) -> None:
        """回收某扩展已注册的全部内容；单项失败不影响其余项。"""
        reg = self.host_registry
        for name in (
            "unregister_source_entries",
            "unregister_parsers",
            "unregister_presenters",
            "unregister_rules",
        ):
            try:
                getattr(reg, name)(owner=ext_id)
            except Exception as e:
                logger.warning(f"[灾害预警] 扩展 {ext_id} 回收 {name} 失败: {e}")

    def load_all(self) -> None:
        """按 enabled_ids 逐个加载扩展，单个失败不中断。"""
        self._records.clear()
        self._apis.clear()
        cfg = self.service.config.get("extensions", {}) or {}
        enabled = [str(i) for i in (cfg.get("enabled_ids") or [])]
        settings = self._settings(cfg)
        for ext_id in dict.fromkeys(enabled):
            found = [
                r / ext_id
                for r in self._get_roots()
                if (r / ext_id / "extension.yaml").is_file()
            ]
            if not found:
                self._record(ext_id, "not_found", "未在任何扩展根目录中找到")
                logger.warning(f"[灾害预警] 扩展 {ext_id} 未找到")
                continue
            for dup in found[1:]:
                self._record(ext_id, "error", f"重复的扩展 id，已忽略: {dup}")
                logger.warning(f"[灾害预警] 扩展 {ext_id} 重复，已忽略 {dup}")
            self._load_one(ext_id, found[0], settings.get(ext_id))

    @staticmethod
    def _purge_modules(pkg: str) -> None:
        for name in [n for n in sys.modules if n == pkg or n.startswith(pkg + ".")]:
            del sys.modules[name]

    def _load_one(self, ext_id: str, ext_dir: Path, ext_settings: Any) -> None:
        rec = None
        pkg = f"_dw_ext_{ext_id}"
        # 插件重载时清理上次导入的模块，确保扩展代码改动生效
        self._purge_modules(pkg)
        try:
            manifest = load_manifest(ext_dir)
            rec = self._record(
                ext_id, "error", name=manifest.name, version=manifest.version
            )
            check_host_version(manifest, self.host_version)
            api = ExtensionAPI(
                manifest,
                self.host_registry,
                config=ext_settings if isinstance(ext_settings, dict) else {},
                host_version=self.host_version,
                data_dir=self._base_data_dir() / "extensions_data" / ext_id,
            )
            # 以私有包名导入，扩展内部可使用相对导入且不污染全局命名空间
            spec = importlib.util.spec_from_file_location(
                pkg,
                ext_dir / "__init__.py",
                submodule_search_locations=[str(ext_dir)],
            )
            if spec is None or spec.loader is None:
                raise ImportError("扩展缺少 __init__.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[pkg] = module
            spec.loader.exec_module(module)
            mod_name, _, func_name = manifest.entry.partition(":")
            entry_mod = importlib.import_module(f"{pkg}.{mod_name}")
            getattr(entry_mod, func_name)(api)
        except Exception as e:
            self._purge_modules(pkg)
            self._unregister_all(ext_id)
            msg = f"{type(e).__name__}: {e}"
            if rec is None:
                rec = self._record(ext_id, "error")
            rec.update(state="error", error=msg)
            logger.warning(f"[灾害预警] 扩展 {ext_id} 加载失败: {msg}")
            return
        self._apis[ext_id] = api
        rec.update(state="loaded", error="", sources=list(api.sources))
        logger.info(f"[灾害预警] 扩展 {ext_id} v{manifest.version} 已加载")

    # ---------------- 运行 ----------------

    async def start(self) -> None:
        """为每个已登记的轮询任务启动一个 asyncio 任务。"""
        specs = [(i, p) for i, api in self._apis.items() for p in api.pollers]
        if not specs or self._tasks:
            return
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=_build_ssl_context())
        )
        for ext_id, spec in specs:
            self._tasks.append(
                asyncio.create_task(
                    self._run_poller(ext_id, spec),
                    name=f"ext-poller-{ext_id}-{spec.source_id}",
                )
            )

    async def stop(self) -> None:
        """取消所有轮询任务并关闭共享的 HTTP 会话。"""
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _run_poller(self, ext_id: str, spec: PollerSpec) -> None:
        """轮询循环：除 CancelledError 外的异常一律吞掉并退避重试。"""
        failures = 0
        last_log = float("-inf")
        label = f"{ext_id}/{spec.source_id}"

        def log_throttled(msg: str) -> None:
            nonlocal last_log
            now = time.monotonic()
            if now - last_log >= self.LOG_THROTTLE_SECONDS:
                last_log = now
                logger.warning(f"[灾害预警] 扩展轮询 {label} {msg}")

        while True:
            try:
                envelopes = await asyncio.wait_for(
                    spec.fetch(self._session), timeout=spec.timeout_seconds
                )
                for env in envelopes or []:
                    try:
                        await self.service._handle_disaster_event(env)
                    except Exception as e:
                        log_throttled(f"处理事件失败: {type(e).__name__}: {e}")
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                failures += 1
                log_throttled(f"拉取失败: {type(e).__name__}: {e}")
            factor = min(2**failures, self.MAX_BACKOFF_FACTOR) if failures else 1
            await asyncio.sleep(spec.interval_seconds * factor)

    # ---------------- 状态 ----------------

    def status(self) -> list[dict]:
        """返回各扩展的加载状态，供管理指令展示。"""
        return [dict(r, sources=list(r["sources"])) for r in self._records]
