"""
数据备份与还原路由。
"""

from __future__ import annotations

from typing import Any

from fastapi import File, Query, UploadFile
from fastapi.responses import StreamingResponse

from astrbot.api import logger

from ....storage.backup_manager import BackupService
from ..payloads.api_response import ApiResponse


def register_backup_routes(app, *, disaster_service):
    """注册备份相关路由。"""
    backup_service = BackupService(disaster_service)

    @app.get("/api/backup/export")
    def export_backup(
        targets: str = Query(
            None,
            description="需要备份的部分，以逗号分隔",
        ),
    ):
        """导出指定数据的备份压缩包 (ZIP)"""
        try:
            target_list = None
            if targets:
                target_list = [t.strip() for t in targets.split(",") if t.strip()]

            zip_buffer = backup_service.export_full_backup(target_list)
            headers = {
                "Content-Disposition": "attachment; filename=disaster_warning_backup.zip"
            }
            return StreamingResponse(
                zip_buffer, media_type="application/zip", headers=headers
            )
        except Exception as e:
            logger.error(f"[灾害预警] 导出备份失败: {e}")
            return ApiResponse.error(f"导出备份失败: {str(e)}", status_code=500)

    @app.post("/api/backup/import")
    async def import_backup(file: UploadFile = File(...)):
        """导入全量备份压缩包 (ZIP)"""
        try:
            contents = await file.read()
            success, msg = await backup_service.import_full_backup(contents)
            if success:
                return ApiResponse.success({"message": msg})
            return ApiResponse.error(msg, status_code=400)
        except Exception as e:
            logger.error(f"[灾害预警] 导入全量备份失败: {e}")
            return ApiResponse.error(f"导入备份失败: {str(e)}", status_code=500)

    @app.get("/api/backup/session-overrides")
    async def export_session_overrides():
        """导出仅会话差异配置 (JSON)"""
        try:
            data = backup_service.export_session_overrides()
            return ApiResponse.success(data)
        except Exception as e:
            logger.error(f"[灾害预警] 导出会话差异配置失败: {e}")
            return ApiResponse.error(f"导出会话差异配置失败: {str(e)}", status_code=500)

    @app.post("/api/backup/session-overrides")
    async def import_session_overrides(
        payload: dict[str, Any],
        merge: bool = Query(True, description="是否增量合并，若为 False 则会覆盖"),
    ):
        """导入会话差异配置 (JSON)"""
        try:
            success, msg = backup_service.import_session_overrides(payload, merge=merge)
            if success:
                return ApiResponse.success({"message": msg})
            return ApiResponse.error(msg, status_code=400)
        except Exception as e:
            logger.error(f"[灾害预警] 导入会话差异配置失败: {e}")
            return ApiResponse.error(f"导入会话差异配置失败: {str(e)}", status_code=500)
