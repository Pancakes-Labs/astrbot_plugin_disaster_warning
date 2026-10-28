"""
ExtensionAPI is the only object that setup(api) receives.

An extension must not import host modules. The host package name changes
with the install method. The host classes are in api.models.
Each registration records an owner. When setup fails, the host removes
all registrations of that owner.
"""

from __future__ import annotations

import pathlib
import types
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..domain.event_identity import EventIdentity
from ..domain.event_models import EventEnvelope, WeatherEvent
from ..domain.event_payload import SourcePayload
from ..rules.rule_result import RuleDecision
from ..sources.source_entry import FusionRole, ProviderFamily, SourceEntry, SourceType
from .i18n import Translator
from .manifest import Manifest

MODELS = types.SimpleNamespace(
    SourceEntry=SourceEntry,
    SourceType=SourceType,
    ProviderFamily=ProviderFamily,
    FusionRole=FusionRole,
    WeatherEvent=WeatherEvent,
    EventEnvelope=EventEnvelope,
    EventIdentity=EventIdentity,
    SourcePayload=SourcePayload,
    RuleDecision=RuleDecision,
)


class _PrefixedLogger:
    """Add the prefix [扩展:<id>] to each log message."""

    def __init__(self, base, ext_id: str):
        self._base = base
        self._prefix = f"[扩展:{ext_id}] "

    def __getattr__(self, level: str):
        log = getattr(self._base, level)
        return lambda msg, *a, **kw: log(self._prefix + str(msg), *a, **kw)


@dataclass(slots=True)
class PollerSpec:
    """One polling task that an extension registers."""

    source_id: str
    fetch: Callable[..., Awaitable[list]]
    interval_seconds: float
    timeout_seconds: float


class ExtensionAPI:
    """See section 4 of docs/extensions/design.md."""

    MIN_INTERVAL_SECONDS = (
        60  # Lower limit of the polling interval. Tests can patch it.
    )

    models = MODELS

    def __init__(
        self,
        manifest: Manifest,
        host_registry,
        *,
        config: dict[str, Any],
        host_version: str,
        data_dir: pathlib.Path,
    ):
        from astrbot.api import logger

        self.id = manifest.id
        self.version = manifest.version
        self.config = config
        self.host_version = host_version
        self.logger = _PrefixedLogger(logger, manifest.id)
        self._manifest = manifest
        self._registry = host_registry
        self._data_dir = data_dir
        self._translator = Translator(
            manifest.path / "locale", config.get("language") or None
        )
        self.sources: list[
            str
        ] = []  # The source_id values that this extension registers
        self.pollers: list[
            PollerSpec
        ] = []  # ExtensionManager.start() schedules these tasks

    @property
    def data_dir(self) -> pathlib.Path:
        """Private data directory. The first access creates it."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir

    def t(self, key: str, **fmt) -> str:
        return self._translator.t(key, **fmt)

    def register_source(self, entry: SourceEntry) -> None:
        if entry.config_group != "extensions":
            raise ValueError("扩展数据源的 config_group 必须为 'extensions'")
        if entry.provider_family != ProviderFamily.EXTENSION:
            raise ValueError(
                "扩展数据源的 provider_family 必须为 ProviderFamily.EXTENSION"
            )
        self._registry.register_source_entry(entry, owner=self.id)
        self.sources.append(entry.source_id)

    def register_parser(self, name: str, parser_class) -> None:
        self._registry.register_parser(name, parser_class, owner=self.id)

    def register_presenter(self, key: str, presenter_class) -> None:
        self._registry.register_text_presenter(key, presenter_class, owner=self.id)

    def register_rule(self, rule, *, after: str = "local_rule") -> None:
        self._registry.register_rule(rule, after=after, owner=self.id)

    def register_poller(
        self,
        source_id: str,
        fetch,
        *,
        interval_seconds: float,
        timeout_seconds: float = 30,
    ) -> None:
        if "network" not in self._manifest.permissions:
            raise PermissionError("register_poller 需要在 permissions 中声明 network")
        if source_id not in self.sources:
            raise ValueError(f"source_id 未由本扩展注册: {source_id}")
        self.pollers.append(
            PollerSpec(
                source_id,
                fetch,
                max(interval_seconds, self.MIN_INTERVAL_SECONDS),
                timeout_seconds,
            )
        )
