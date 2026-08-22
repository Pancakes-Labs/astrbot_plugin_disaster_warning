"""
运维/工具类路由。
承接日志摘要、打开日志目录、打开插件目录等与主业务状态弱耦合的接口，
减少 WebAdminServer 中的内联路由体积。
"""

from __future__ import annotations

import asyncio
import os
import platform
from pathlib import Path

from astrbot.api import logger

from ....sources.display_registry import (
    CONNECTION_DISPLAY_NAMES,
    SOURCE_ALIAS_MAP,
    SOURCE_DISPLAY_MAP,
)
from ..host.runtime_environment import is_running_in_docker
from ..payloads.api_response import ApiResponse


def register_utility_routes(app, disaster_service, plugin_root: str):
    """注册运维与工具类接口。"""

    @app.get("/api/logs")
    async def get_logs():
        """获取日志摘要。"""
        try:
            if not disaster_service or not disaster_service.message_logger:
                return ApiResponse.success(
                    {"enabled": False, "message": "日志功能未启用"}
                )

            return ApiResponse.success(
                disaster_service.message_logger.get_log_summary()
            )
        except Exception as e:
            logger.error(f"[灾害预警] 获取日志失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.post("/api/open-log-dir")
    async def open_log_dir():
        """在宿主机上打开日志目录。"""
        try:
            if not disaster_service or not disaster_service.message_logger:
                return ApiResponse.error("日志功能不可用", status_code=503)

            log_path = disaster_service.message_logger.log_file_path
            log_dir = log_path.parent
            if not log_dir.exists():
                return ApiResponse.error("日志目录不存在", status_code=404)

            if is_running_in_docker():
                # 容器内无法可靠控制宿主机文件管理器，因此直接给出明确错误提示。
                return ApiResponse.error(
                    "Docker 环境下不支持在宿主机打开目录，请手动查看挂载路径",
                    status_code=400,
                )

            system = platform.system()
            if system == "Windows":
                os.startfile(log_dir)
            elif system == "Darwin":
                await asyncio.create_subprocess_exec("open", str(log_dir))
            else:
                await asyncio.create_subprocess_exec("xdg-open", str(log_dir))

            return ApiResponse.success(
                {"success": True, "message": "已在文件浏览器中打开日志目录"}
            )
        except Exception as e:
            logger.error(f"[灾害预警] 打开日志目录失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.post("/api/open-plugin-dir")
    async def open_plugin_dir():
        """在宿主机上打开插件根目录。"""
        try:
            if not os.path.exists(plugin_root):
                return ApiResponse.error("插件目录不存在", status_code=404)

            if is_running_in_docker():
                # 与打开日志目录保持同一策略：容器内不尝试拉起宿主机文件管理器。
                return ApiResponse.error(
                    "Docker 环境下不支持在宿主机打开目录，请手动查看挂载路径",
                    status_code=400,
                )

            system = platform.system()
            if system == "Windows":
                os.startfile(plugin_root)
            elif system == "Darwin":
                await asyncio.create_subprocess_exec("open", str(plugin_root))
            else:
                await asyncio.create_subprocess_exec("xdg-open", str(plugin_root))

            return ApiResponse.success(
                {"success": True, "message": "已在文件浏览器中打开插件目录"}
            )
        except Exception as e:
            logger.error(f"[灾害预警] 打开插件目录失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    def _to_workspace_relative_path(path: Path) -> str:
        """将绝对路径转换为插件目录内相对路径。"""
        root = Path(plugin_root).resolve()
        return str(path.resolve().relative_to(root)).replace("\\", "/")

    def _list_markdown_documents() -> list[dict]:
        """列出允许在管理端浏览的 Markdown 文档。"""
        items = []
        seen = set()
        root = Path(plugin_root).resolve()
        docs_root = (root / "docs").resolve()
        allowed_paths = []
        if root.exists():
            allowed_paths.extend(sorted(root.glob("*.md")))
        if docs_root.exists():
            allowed_paths.extend(sorted(docs_root.rglob("*.md")))

        for path in allowed_paths:
            if not path.is_file():
                continue
            try:
                relative_path = _to_workspace_relative_path(path)
            except ValueError:
                continue
            normalized = relative_path.replace("\\", "/")
            if normalized in seen:
                continue
            seen.add(normalized)
            items.append(
                {
                    "path": normalized,
                    "title": path.stem,
                    "filename": path.name,
                    "category": "root"
                    if path.parent.resolve() == root
                    else path.parent.name,
                }
            )

        items.sort(
            key=lambda item: (
                0 if item["path"].count("/") == 0 else 1,
                item["path"].lower(),
            )
        )
        return items

    def _resolve_markdown_document(raw_path: str) -> Path | None:
        """解析并校验前端请求的 Markdown 相对路径。"""
        normalized = str(raw_path or "").strip().replace("\\", "/")
        if not normalized or not normalized.lower().endswith(".md"):
            return None
        if (
            normalized.startswith("/")
            or normalized.startswith("../")
            or "/../" in normalized
        ):
            return None

        root = Path(plugin_root).resolve()
        docs_root = (root / "docs").resolve()
        candidate = (root / normalized).resolve()
        if not candidate.is_file():
            return None

        try:
            relative_path = candidate.relative_to(root)
        except ValueError:
            return None

        if relative_path.parent == Path("."):
            return candidate
        try:
            candidate.relative_to(docs_root)
            return candidate
        except ValueError:
            return None

    @app.get("/api/sources/meta")
    async def get_sources_meta():
        """返回数据源展示元数据（事实层）。

        供管理端前端动态拉取，用于补充本地映射表缺失的 key，
        避免新增数据源后前端漏配展示名。只读接口，无副作用。
        """
        return ApiResponse.success(
            {
                "source_alias_map": SOURCE_ALIAS_MAP,
                "source_display_map": SOURCE_DISPLAY_MAP,
                "connection_display_names": CONNECTION_DISPLAY_NAMES,
            }
        )

    @app.get("/api/markdown-files")
    async def list_markdown_files():
        """列出可浏览的 Markdown 文档。"""
        return ApiResponse.success({"items": _list_markdown_documents()})

    @app.get("/api/markdown-files/{file_path:path}")
    async def get_markdown_file(file_path: str):
        """读取指定 Markdown 文档内容。"""
        resolved = _resolve_markdown_document(file_path)
        if not resolved:
            return ApiResponse.error("文档不存在或不允许访问", status_code=404)

        try:
            content = await asyncio.to_thread(resolved.read_text, encoding="utf-8")
        except UnicodeDecodeError:
            return ApiResponse.error(
                "文档编码不受支持，仅支持 UTF-8 Markdown 文件", status_code=400
            )
        except Exception as e:
            logger.error(f"[灾害预警] 读取 Markdown 文档失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

        return ApiResponse.success(
            {
                "path": _to_workspace_relative_path(resolved),
                "title": resolved.stem,
                "content": content,
                "content_format": "markdown",
            }
        )
