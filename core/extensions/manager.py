"""
Extension manager. It finds and loads extensions, isolates them, and runs
their polling tasks.

A failure in one extension sets only that extension to the error state and
removes its registrations. The host and the other extensions do not change.
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
    """Use the certifi root certificates when they are available.

    Some system stores do not have new roots, for example the GlobalSign
    Root R46 of CEMADEN. The result is CERTIFICATE_VERIFY_FAILED.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class ExtensionManager:
    """See section 5 of docs/extensions/design.md."""

    MAX_BACKOFF_FACTOR = 10  # Maximum backoff, as a multiple of the polling interval
    LOG_THROTTLE_SECONDS = 600  # Minimum seconds between error logs of one polling task

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
        self._records: list[dict[str, Any]] = []  # Data for status()
        self._apis: dict[str, ExtensionAPI] = {}  # Extensions that loaded
        self._tasks: list[asyncio.Task] = []
        self._session: aiohttp.ClientSession | None = None

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
        """Return the settings dict. The value is JSON text or a dict. Return {} if it is not valid."""
        raw = cfg.get("settings") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "{}")
            except ValueError as e:
                logger.warning(f"[灾害预警] 扩展 settings 不是合法 JSON，已忽略: {e}")
                return {}
        return raw if isinstance(raw, dict) else {}

    def _record(
        self, ext_id: str, state: str, error: str = "", *, name: str = "", version=""
    ) -> dict:
        rec = {
            "id": ext_id,
            "name": name or ext_id,
            "version": version,
            "state": state,
            "error": error,
            "sources": [],
        }
        self._records.append(rec)
        return rec

    def _unregister_all(self, ext_id: str) -> None:
        """Remove all registrations of an extension. A failed item does not stop the others."""
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
        """Load the extensions in enabled_ids one by one. A failure does not stop the others."""
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
        # Remove the modules of an earlier load. A plugin reload then uses the new code.
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
            # Import with a private package name. The extension can use relative imports
            # and the global namespace stays clean.
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

    async def start(self) -> None:
        """Start one asyncio task for each registered polling task."""
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
        """Cancel all polling tasks and close the shared HTTP session."""
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _run_poller(self, ext_id: str, spec: PollerSpec) -> None:
        """Poll in a loop. Log each exception except CancelledError, then retry with backoff."""
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

    def status(self) -> list[dict]:
        """Return the load state of each extension for the admin command."""
        return [dict(r, sources=list(r["sources"])) for r in self._records]
