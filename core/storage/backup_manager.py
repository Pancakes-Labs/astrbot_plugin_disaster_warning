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

# 可备份目标清单（target 标识）
# - db:            events.db                 历史预警事件库
# - sessions:      session_overrides.json    会话差异配置
# - stats:         statistics.json           统计快照
# - caches:        earthquake_lists_cache.json / eew_query_cache.json  运行时缓存
# - simulations:   simulation_flows.json     模拟流草稿
# - notifications: notifications_cache.json  通知缓存（含已读状态）
# - logs:          logger_stats.json         日志过滤统计
_SUPPORTED_TARGETS = (
    "db",
    "sessions",
    "stats",
    "caches",
    "simulations",
    "notifications",
    "logs",
)

# target -> 应纳入打包的数据文件名（相对插件数据目录，ZIP 内保持同名归档）
_TARGET_FILES: dict[str, tuple[str, ...]] = {
    "sessions": ("session_overrides.json",),
    "stats": ("statistics.json",),
    "caches": ("earthquake_lists_cache.json", "eew_query_cache.json"),
    "simulations": ("simulation_flows.json",),
    "notifications": ("notifications_cache.json",),
    "logs": ("logger_stats.json",),
}

# 各目标的中文说明，用于打包日志
_TARGET_LABELS: dict[str, str] = {
    "sessions": "会话差异配置",
    "stats": "统计数据",
    "caches": "运行时缓存",
    "simulations": "模拟流草稿",
    "notifications": "通知缓存",
    "logs": "日志统计",
}


class BackupService:
    """
    备份与还原服务层，负责：
    1. 导出/导入完整备份 ZIP
    2. 导出/导入仅会话差异配置 JSON (并提供增量合并与覆盖选项)
    """

    def __init__(self, disaster_service=None):
        self.disaster_service = disaster_service
        self.storage_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
        self.db_path = self.storage_dir / "events.db"
        self.session_file = self.storage_dir / "session_overrides.json"
        self.stats_file = self.storage_dir / "statistics.json"

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def export_full_backup(self, targets: list[str] = None) -> io.BytesIO:
        """
        打包指定数据为 ZIP 字节流。支持选择部分备份。
        :param targets: 允许传入 _SUPPORTED_TARGETS 的子集。如果为 None 则默认打包全部。
                        传入未知目标会记录警告，且在全部目标无效时抛出异常。
        """
        if targets is None:
            # 未显式指定时，默认打包全部支持的目标
            targets = list(_SUPPORTED_TARGETS)
        else:
            # 过滤掉未知目标，避免前端/外部误传非法值导致打包异常，
            # 同时记录被丢弃的目标，便于排查配置/调用问题
            invalid_targets = [t for t in targets if t not in _SUPPORTED_TARGETS]
            if invalid_targets:
                logger.warning(
                    "[灾害预警] 导出全量备份时收到未知备份目标，将被丢弃: %s",
                    invalid_targets,
                )
            targets = [t for t in targets if t in _SUPPORTED_TARGETS]
            if not targets:
                # 所有目标均无效时直接报错，避免执行“空备份”而无感知
                raise ValueError(
                    "export_full_backup: 所有备份目标均为未知值，请检查调用参数。"
                )

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
            }
            # 统一为各文件型目标生成 has_* 标记
            for target, fnames in _TARGET_FILES.items():
                if target in targets:
                    manifest[f"has_{target}"] = any(
                        (self.storage_dir / fname).exists() for fname in fnames
                    )
            zip_file.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
            logger.info(f"[灾害预警] 备份元信息写入成功，插件版本号 {version}")

            # 2. 写入 events.db（使用 SQLite 在线备份保证一致性）
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

            # 3. 统一写入各文件型目标（sessions/stats/caches/simulations/notifications/logs）
            for target, fnames in _TARGET_FILES.items():
                if target not in targets:
                    continue
                label = _TARGET_LABELS.get(target, target)
                for fname in fnames:
                    fpath = self.storage_dir / fname
                    if fpath.exists():
                        zip_file.write(str(fpath), fname)
                        logger.info(f"[灾害预警] {label}已打包: {fname}")

        zip_buffer.seek(0)
        logger.info("[灾害预警] 数据打包备份完成")
        return zip_buffer

    # ------------------------------------------------------------------
    # 导入（全量还原）
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_import_plan(namelist: list[str]) -> dict[str, bool]:
        """根据 ZIP 包内文件清单确定需要还原的目标集合。"""
        plan: dict[str, bool] = {"db": "events.db" in namelist}
        for target, fnames in _TARGET_FILES.items():
            plan[target] = any(fname in namelist for fname in fnames)
        return plan

    @staticmethod
    def _remove_file_quietly(path) -> None:
        """静默删除文件，失败不抛异常。"""
        try:
            if path.exists():
                os.remove(path)
        except Exception:
            pass

    @staticmethod
    def _rollback_files(temp_backups: list, created_files: list) -> None:
        """回滚文件：
        1. 把 .bak 快照替换回原路径（仅针对还原前已存在的文件）；
        2. 删除本次导入新创建的、还原前不存在的文件。
        """
        for path, bak_path in temp_backups:
            if bak_path.exists():
                try:
                    os.replace(str(bak_path), str(path))
                except Exception:
                    pass
        for path in created_files:
            BackupService._remove_file_quietly(path)

    async def _reload_caches(self) -> None:
        """缓存文件改变后重新载入内存（地震列表 + EEW 查询状态）。"""
        if not self.disaster_service:
            return
        cache_service = getattr(self.disaster_service, "cache_service", None)
        if not cache_service:
            logger.warning("[灾害预警] 缓存服务未装配，缓存将在下次启动时自动载入")
            return
        logger.info("[灾害预警] 正在重新加载运行时缓存...")
        try:
            cache_service.load_earthquake_lists_cache()
        except Exception as e:
            logger.warning(f"[灾害预警] 重新加载地震列表缓存失败: {e}")
        try:
            cache_service.load_eew_query_cache()
        except Exception as e:
            logger.warning(f"[灾害预警] 重新加载 EEW 缓存失败: {e}")

    async def _reload_simulations(self) -> None:
        """模拟流草稿文件改变后重新载入内存（若尚未装配则下次访问自动加载）。"""
        if not self.disaster_service:
            return
        storage = getattr(self.disaster_service, "simulation_storage", None)
        if storage is None:
            logger.info("[灾害预警] 模拟流存储尚未装配，将在下次访问时自动加载新草稿")
            return
        try:
            storage.configure(self.storage_dir)
            logger.info("[灾害预警] 模拟流草稿已重新加载")
        except Exception as e:
            logger.warning(f"[灾害预警] 重新加载模拟流草稿失败: {e}")

    async def _reload_notifications(self) -> None:
        """通知缓存文件改变后重新载入内存（含已读状态）。"""
        if not self.disaster_service:
            return
        notification_center = getattr(
            self.disaster_service, "notification_center", None
        )
        if not notification_center:
            logger.warning("[灾害预警] 通知中心未装配，通知缓存将在下次启动时自动载入")
            return
        try:
            await notification_center.load_cache()
            logger.info("[灾害预警] 通知缓存已重新加载")
        except Exception as e:
            logger.warning(f"[灾害预警] 重新加载通知缓存失败: {e}")

    async def _reload_logs(self) -> None:
        """日志统计文件改变后重新载入内存。"""
        if not self.disaster_service:
            return
        message_logger = getattr(self.disaster_service, "message_logger", None)
        if not message_logger:
            logger.warning(
                "[灾害预警] 原始消息记录器未装配，日志统计将在下次启动时自动载入"
            )
            return
        try:
            message_logger._load_stats()
            logger.info("[灾害预警] 日志统计已重新加载")
        except Exception as e:
            logger.warning(f"[灾害预警] 重新加载日志统计失败: {e}")

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
            plan = self._resolve_import_plan(namelist)
            has_db_in_zip = plan["db"]
            has_sessions_in_zip = plan["sessions"]
            has_stats_in_zip = plan["stats"]
            has_caches_in_zip = plan["caches"]
            has_simulations_in_zip = plan["simulations"]
            has_notifications_in_zip = plan["notifications"]
            has_logs_in_zip = plan["logs"]

            logger.info(
                f"[灾害预警] 解析包发现有效数据模块: "
                f"数据库：{has_db_in_zip}, 会话差异配置：{has_sessions_in_zip}, "
                f"统计数据：{has_stats_in_zip}, 运行时缓存：{has_caches_in_zip}, "
                f"模拟流草稿：{has_simulations_in_zip}, 通知缓存：{has_notifications_in_zip}, "
                f"日志统计：{has_logs_in_zip}"
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

            # 备份当前本地数据作为 .bak 回滚文件（只备份需要覆盖的文件），
            # 同时记录“还原前原本不存在”的路径，供失败时删除新文件实现真正回滚。
            temp_backups = []
            created_files = []
            logger.info("[灾害预警] 正在为将被覆盖的本地数据创建临时回滚快照...")

            try:
                if has_db_in_zip:
                    if self.db_path.exists():
                        bak_path = self.db_path.with_suffix(
                            self.db_path.suffix + ".bak"
                        )
                        src = sqlite3.connect(str(self.db_path))
                        dst = sqlite3.connect(str(bak_path))
                        try:
                            src.backup(dst)
                        finally:
                            dst.close()
                            src.close()
                        temp_backups.append((self.db_path, bak_path))
                    else:
                        created_files.append(self.db_path)

                # 统一遍历各文件型目标，仅处理本次需要还原的目标
                for target, fnames in _TARGET_FILES.items():
                    if not plan.get(target):
                        continue
                    for fname in fnames:
                        fpath = self.storage_dir / fname
                        if fpath.exists():
                            bak_path = fpath.with_suffix(fpath.suffix + ".bak")
                            shutil.copy2(str(fpath), str(bak_path))
                            temp_backups.append((fpath, bak_path))
                        else:
                            created_files.append(fpath)
            except Exception as e:
                logger.error(f"[灾害预警] 创建备份回滚文件失败: {e}，已中断还原流程")
                # 清除已经创建的部分备份文件
                for _, bak_path in temp_backups:
                    BackupService._remove_file_quietly(bak_path)
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
                for target, fnames in _TARGET_FILES.items():
                    if not plan.get(target):
                        continue
                    label = _TARGET_LABELS.get(target, target)
                    for fname in fnames:
                        if fname in namelist:
                            zip_file.extract(fname, str(self.storage_dir))
                            logger.info(f"[灾害预警] {label}已成功覆盖: {fname}")
            except Exception as e:
                # 恢复备份：既有文件用 .bak 还原，新创建文件删除
                logger.error(f"[灾害预警] 解压备份包失败，正在回滚旧数据: {e}")
                self._rollback_files(temp_backups, created_files)
                return False, f"解压还原数据时出错，已回滚: {str(e)}"
            finally:
                # 无论成功失败，都尝试清掉 .bak 缓存文件
                for _, bak_path in temp_backups:
                    BackupService._remove_file_quietly(bak_path)

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

                # 按目标逐一重载对应运行时状态
                if has_caches_in_zip:
                    await self._reload_caches()
                if has_simulations_in_zip:
                    await self._reload_simulations()
                if has_notifications_in_zip:
                    await self._reload_notifications()
                if has_logs_in_zip:
                    await self._reload_logs()

            logger.info("[灾害预警] 数据还原流程执行完毕")
            return True, "数据还原成功！"
        except Exception as e:
            logger.error(f"[灾害预警] 导入备份发生未知异常: {e}")
            return False, f"导入备份失败: {str(e)}"

    # ------------------------------------------------------------------
    # 会话差异配置（独立 JSON 导出/导入）
    # ------------------------------------------------------------------
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
        logger.info(
            f"[灾害预警] 准备导入会话差异配置（合并模式：{'开启' if merge else '关闭'}）..."
        )
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
