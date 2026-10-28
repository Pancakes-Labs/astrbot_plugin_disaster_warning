"""
扩展清单（extension.yaml）解析与校验。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SUPPORTED_API_VERSION = 1  # 宿主仅支持的扩展 API 版本
DEFAULT_ENTRY = "extension:setup"
_ID_RE = re.compile(r"[a-z0-9_]+")


class ManifestError(ValueError):
    """清单缺失、格式错误或与宿主不兼容。"""


@dataclass(frozen=True, slots=True)
class Manifest:
    """extension.yaml 的只读视图。"""

    id: str
    name: str
    version: str
    api_version: int
    min_host_version: str  # 空串表示不限制
    entry: str  # "<模块>:<函数>"，模块相对扩展包
    description: str
    author: str
    permissions: tuple[str, ...]
    path: Path  # 扩展目录


def parse_version(text: str) -> tuple[int, ...]:
    """把 "v1.6.5" 之类的版本号转换为可比较的整数元组。"""
    return tuple(int(n) for n in re.findall(r"\d+", str(text).lstrip("vV")))


def load_manifest(ext_dir: Path) -> Manifest:
    """读取并校验 ext_dir/extension.yaml，失败时抛出 ManifestError。"""
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
    """宿主版本低于 min_host_version 时抛出 ManifestError。"""
    if not manifest.min_host_version:
        return
    if parse_version(host_version) < parse_version(manifest.min_host_version):
        raise ManifestError(
            f"需要宿主版本 >= {manifest.min_host_version}，当前为 {host_version}"
        )
