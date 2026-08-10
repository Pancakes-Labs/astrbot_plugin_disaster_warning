"""
备份与还原服务层
"""

import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

from astrbot.api import logger
from astrbot.api.star import StarTools

from ...utils.version import get_plugin_version


class BackupService:
    """
    备份与还原服务层，负责：
    1. 导出/导入完整备份 ZIP (包含数据库, 会话配置差异和内存统计快照)
    2. 导出/导入仅会话差异配置 JSON (并提供增量合并与覆盖选项)
    """

    def __init__(self, disaster_service=None):
        self.disaster_service = disaster_service
        self.storage_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
        self.db_path = self.storage_dir / "events.db"
        self.session_file = self.storage_dir / "session_overrides.json"
        self.stats_file = self.storage_dir / "statistics.json"

    def export_full_backup(self, targets: list[str] = None) -> io.BytesIO:
        """
        打包指定数据为 ZIP 字节流。支持选择部分备份。
        :param targets: 允许传入 ['db', 'sessions', 'stats'] 的子集。如果为 None 则默认打包全部。
        """
        if targets is None:
            targets = ["db", "sessions", "stats"]

        logger.info(f"[灾害预警] 正在执行数据打包备份流程，选择项: {targets}...")
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            # 1. 写入 manifest.json 元数据
            version = "unknown"
            try:
                version = get_plugin_version()
            except Exception as e:
                logger.warning(f"[灾害预警] 打包数据时获取插件版本失败: {e}")

            manifest = {
                "backup_time": datetime.now().isoformat(),
                "plugin": "astrbot_plugin_disaster_warning",
                "version": version,
                "has_db": "db" in targets and self.db_path.exists(),
                "has_sessions": "sessions" in targets and self.session_file.exists(),
                "has_stats": "stats" in targets and self.stats_file.exists(),
            }
            zip_file.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
            logger.info(f"[灾害预警] 备份元信息写入成功，插件版本号 {version}")

            # 2. 写入 events.db
            if "db" in targets and self.db_path.exists():
                temp_db_fd, temp_db_path = tempfile.mkstemp(suffix=".db")
                os.close(temp_db_fd)
                try:
                    src = sqlite3.connect(str(self.db_path))
                    dst = sqlite3.connect(temp_db_path)
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                        src.close()
                    zip_file.write(temp_db_path, "events.db")
                    logger.info("[灾害预警] 数据库已安全打包")
                finally:
                    try:
                        os.remove(temp_db_path)
                    except Exception:
                        pass

            # 3. 写入 session_overrides.json
            if "sessions" in targets and self.session_file.exists():
                zip_file.write(str(self.session_file), "session_overrides.json")
                logger.info("[灾害预警] 会话差异配置已打包")

            # 4. 写入 statistics.json
            if "stats" in targets and self.stats_file.exists():
                zip_file.write(str(self.stats_file), "statistics.json")
                logger.info("[灾害预警] 统计数据已打包")

        zip_buffer.seek(0)
        logger.info("[灾害预警] 数据打包备份完成")
        return zip_buffer

    async def import_full_backup(self, zip_bytes: bytes) -> tuple[bool, str]:
        """
        从 ZIP 字节包还原备份。
        包含完整的数据库和配置文件替换，为了安全性，在替换前进行当前数据的备份。
        只覆盖 ZIP 包中包含的文件，未包含的文件不会被清除或覆盖。
        """
        logger.info("[灾害预警] 收到数据还原请求，准备解析备份包...")
        try:
            zip_file = zipfile.ZipFile(io.BytesIO(zip_bytes))
            namelist = zip_file.namelist()

            if "manifest.json" not in namelist:
                logger.error("[灾害预警] 还原失败: 备份包中缺少 manifest.json")
                return False, "备份包中缺少 manifest.json 元数据文件"

            # 验证 manifest
            manifest_content = zip_file.read("manifest.json").decode("utf-8")
            manifest = json.loads(manifest_content)
            if manifest.get("plugin") != "astrbot_plugin_disaster_warning":
                logger.error(
                    f"[灾害预警] 还原失败: 备份包对应的插件名不符({manifest.get('plugin')})"
                )
                return False, "无效的备份包，该备份包不属于灾害预警插件"

            # 确定这次备份包里到底有哪些数据项需要被还原
            has_db_in_zip = "events.db" in namelist
            has_sessions_in_zip = "session_overrides.json" in namelist
            has_stats_in_zip = "statistics.json" in namelist

            logger.info(
                f"[灾害预警] 解析包发现有效数据模块: 数据库：{has_db_in_zip}, 会话差异配置：{has_sessions_in_zip}, 统计数据：{has_stats_in_zip}"
            )

            # 暂停当前数据库与统计管理器的连接
            db_mgr = None
            stats_mgr = None
            if self.disaster_service:
                stats_mgr = getattr(self.disaster_service, "statistics_manager", None)
                if stats_mgr:
                    db_mgr = getattr(stats_mgr, "db", None)

            # 关闭现有数据库连接（只有当需要覆盖 db 时才需要断开）
            if db_mgr and has_db_in_zip:
                logger.info("[灾害预警] 正在断开当前数据库连接...")
                await db_mgr.close()

            # 备份当前本地数据作为 .bak 回滚文件（只备份需要覆盖的文件）
            temp_backups = []
            logger.info("[灾害预警] 正在为将被覆盖的本地数据创建临时回滚快照...")

            try:
                if has_db_in_zip and self.db_path.exists():
                    bak_path = self.db_path.with_suffix(self.db_path.suffix + ".bak")
                    src = sqlite3.connect(str(self.db_path))
                    dst = sqlite3.connect(str(bak_path))
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                        src.close()
                    temp_backups.append((self.db_path, bak_path))

                for path in [self.session_file, self.stats_file]:
                    if (
                        path == self.session_file
                        and has_sessions_in_zip
                        and path.exists()
                    ) or (
                        path == self.stats_file and has_stats_in_zip and path.exists()
                    ):
                        bak_path = path.with_suffix(path.suffix + ".bak")
                        shutil.copy2(str(path), str(bak_path))
                        temp_backups.append((path, bak_path))
            except Exception as e:
                logger.error(f"[灾害预警] 创建备份回滚文件失败: {e}，已中断还原流程")
                # 清除已经创建的部分备份文件
                for _, bak_path in temp_backups:
                    if bak_path.exists():
                        try:
                            os.remove(bak_path)
                        except Exception:
                            pass
                # 重新初始化数据库连接，防止连接被永久关闭
                if db_mgr and has_db_in_zip:
                    try:
                        await db_mgr.initialize()
                    except Exception as init_err:
                        logger.error(f"[灾害预警] 恢复数据库连接失败: {init_err}")
                return False, f"创建本地回滚备份失败，已中止还原: {str(e)}"

            # 解压还原新文件
            logger.info("[灾害预警] 开始解压并替换选中的本地数据文件...")
            try:
                if has_db_in_zip:
                    zip_file.extract("events.db", str(self.storage_dir))
                    logger.info("[灾害预警] 历史数据库已成功覆盖")
                if has_sessions_in_zip:
                    zip_file.extract("session_overrides.json", str(self.storage_dir))
                    logger.info("[灾害预警] 会话差异配置已成功覆盖")
                if has_stats_in_zip:
                    zip_file.extract("statistics.json", str(self.storage_dir))
                    logger.info("[灾害预警] 统计数据已成功覆盖")
            except Exception as e:
                # 恢复备份
                logger.error(f"[灾害预警] 解压备份包失败，正在回滚旧数据: {e}")
                for path, bak_path in temp_backups:
                    if bak_path.exists():
                        try:
                            os.replace(str(bak_path), str(path))
                        except Exception:
                            pass
                return False, f"解压还原数据时出错，已回滚: {str(e)}"
            finally:
                # 无论成功失败，都尝试清掉 .bak 缓存文件
                for _, bak_path in temp_backups:
                    if bak_path.exists():
                        try:
                            os.remove(bak_path)
                        except Exception:
                            pass

                # 重新初始化数据库连接（只有当 db 改变或被关闭时重新 initialize）
                if db_mgr and has_db_in_zip:
                    logger.info("[灾害预警] 正在重新建立数据库连接并初始化...")
                    await db_mgr.initialize()

                # 如果有统计管理器，且 stats 或者 db 改变了，重新加载统计数据和去重集合
                if stats_mgr and (has_stats_in_zip or has_db_in_zip):
                    logger.info("[灾害预警] 正在重新加载内存统计数据并刷新缓存...")
                    await stats_mgr._load_stats()
                    await stats_mgr.refresh_derived_stats_from_database()

                # 如果有会话配置管理器，且 session 配置改变了，重新加载
                if (
                    has_sessions_in_zip
                    and self.disaster_service
                    and hasattr(self.disaster_service, "session_config_manager")
                ):
                    sess_mgr = getattr(self.disaster_service, "session_config_manager")
                    if sess_mgr:
                        logger.info("[灾害预警] 正在重新装载会话覆写差异...")
                        sess_mgr._load()

            logger.info("[灾害预警] 数据还原流程执行完毕")
            return True, "数据还原成功！"
        except Exception as e:
            logger.error(f"[灾害预警] 导入备份发生未知异常: {e}")
            return False, f"导入备份失败: {str(e)}"

    def export_session_overrides(self) -> dict:
        """
        导出仅会话差异配置
        """
        logger.info("[灾害预警] 正在读取并导出会话差异配置...")
        if self.session_file.exists():
            try:
                with open(self.session_file, encoding="utf-8") as f:
                    data = json.load(f)
                    logger.info(f"[灾害预警] 成功载入 {len(data)} 个会话的覆写参数")
                    return data
            except Exception as e:
                logger.error(f"[灾害预警] 读取会话差异配置失败: {e}")
        return {}

    def import_session_overrides(
        self, imported_data: dict, merge: bool = True
    ) -> tuple[bool, str]:
        """
        导入会话差异配置
        :param imported_data: 导入的 JSON 配置
        :param merge: 是否以增量合并方式导入（若为 False，则会全量覆盖）
        """
        logger.info(f"[灾害预警] 准备导入会话差异配置 (merge={merge})...")
        if not isinstance(imported_data, dict):
            logger.error("[灾害预警] 导入会话差异配置失败: 格式非 JSON 对象")
            return False, "会话差异配置数据格式错误，必须为 JSON 对象"

        try:
            # 引入 SessionConfigManager 辅助清洗和合并
            sess_mgr = None
            if self.disaster_service and hasattr(
                self.disaster_service, "session_config_manager"
            ):
                sess_mgr = getattr(self.disaster_service, "session_config_manager")

            if not sess_mgr:
                logger.error(
                    "[灾害预警] 导入会话差异配置失败: session_config_manager 未就绪"
                )
                return False, "插件未完全初始化，无法使用会话配置管理器"

            # 校验和清洗导入的配置项，仅保留符合 Schema 规范的项
            cleaned_overrides = {}
            for umo, override in imported_data.items():
                if not isinstance(override, dict):
                    continue
                # 利用现有的 sanitize_patch 对传入数据做白名单与 Schema 清洗
                clean_patch = sess_mgr._sanitize_patch(override)
                if clean_patch:
                    cleaned_overrides[umo] = clean_patch

            logger.info(
                f"[灾害预警] 已清洗过滤导入数据，符合 Schema 规范的会话数: {len(cleaned_overrides)}"
            )

            if merge:
                # 增量合并：在已存差异上，使用 deep_merge 进行会话级与字段级的合并
                current_overrides = sess_mgr._overrides
                for umo, override in cleaned_overrides.items():
                    if umo in current_overrides:
                        current_overrides[umo] = sess_mgr.deep_merge(
                            current_overrides[umo], override
                        )
                    else:
                        current_overrides[umo] = override
                sess_mgr._overrides = current_overrides
                logger.info("[灾害预警] 增量合并完成")
            else:
                # 全量覆盖
                sess_mgr._overrides = cleaned_overrides
                logger.info("[灾害预警] 全量覆盖完成")
            # 保存修改到 session_overrides.json
            sess_mgr._save()
            logger.info("[灾害预警] 会话配置保存成功")
            return True, f"成功导入 {len(cleaned_overrides)} 个会话配置差异！"
        except Exception as e:
            logger.error(f"[灾害预警] 导入会话差异配置发生异常: {e}")
            return False, f"导入会话配置失败: {str(e)}"
