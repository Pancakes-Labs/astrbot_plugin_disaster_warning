"""
Parse and validate the extension manifest (extension.yaml).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SUPPORTED_API_VERSION = 1  # The only extension API version that the host supports
DEFAULT_ENTRY = "extension:setup"
_ID_RE = re.compile(r"[a-z0-9_]+")


class ManifestError(ValueError):
    """The manifest is missing, malformed, or not compatible with the host."""


@dataclass(frozen=True, slots=True)
class Manifest:
    """Read-only view of extension.yaml."""

    id: str
    name: str
    version: str
    api_version: int
    min_host_version: str  # An empty string means no limit
    entry: str  # "<module>:<function>". The module is relative to the extension package
    description: str
    author: str
    permissions: tuple[str, ...]
    path: Path  # Extension directory


def parse_version(text: str) -> tuple[int, ...]:
    """Convert a version such as "v1.6.5" to a tuple of integers."""
    return tuple(int(n) for n in re.findall(r"\d+", str(text).lstrip("vV")))


def load_manifest(ext_dir: Path) -> Manifest:
    """Read and validate ext_dir/extension.yaml. Raise ManifestError on failure."""
    file = ext_dir / "extension.yaml"
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ManifestError(f"无法读取 extension.yaml: {e}") from e
    if not isinstance(raw, dict):
        raise ManifestError("extension.yaml 必须是键值映射")

    ext_id = str(raw.get("id") or "")
    if not _ID_RE.fullmatch(ext_id):
        raise ManifestError(f"id 非法（需匹配 [a-z0-9_]+）: {ext_id!r}")
    if ext_id != ext_dir.name:
        raise ManifestError(f"id {ext_id!r} 与目录名 {ext_dir.name!r} 不一致")

    api_version = raw.get("api_version")
    if isinstance(api_version, bool) or api_version != SUPPORTED_API_VERSION:
        raise ManifestError(
            f"不支持的 api_version: {api_version!r}（宿主仅支持 {SUPPORTED_API_VERSION}）"
        )

    entry = str(raw.get("entry") or DEFAULT_ENTRY)
    mod, sep, func = entry.partition(":")
    if not (mod and sep and func):
        raise ManifestError(f"entry 格式应为 '模块:函数': {entry!r}")

    permissions = raw.get("permissions") or []
    if not isinstance(permissions, list):
        raise ManifestError("permissions 必须是列表")

    return Manifest(
        id=ext_id,
        name=str(raw.get("name") or ext_id),
        version=str(raw.get("version") or "0.0.0"),
        api_version=api_version,
        min_host_version=str(raw.get("min_host_version") or ""),
        entry=entry,
        description=str(raw.get("description") or ""),
        author=str(raw.get("author") or ""),
        permissions=tuple(str(p) for p in permissions),
        path=ext_dir,
    )


def check_host_version(manifest: Manifest, host_version: str) -> None:
    """Raise ManifestError if the host version is lower than min_host_version."""
    if not manifest.min_host_version:
        return
    if parse_version(host_version) < parse_version(manifest.min_host_version):
        raise ManifestError(
            f"需要宿主版本 >= {manifest.min_host_version}，当前为 {host_version}"
        )
