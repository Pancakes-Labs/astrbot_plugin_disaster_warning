"""
模拟流草稿持久化存储。

负责把 SimulationFlow 草稿保存到插件数据目录（跨重启恢复），
并支持按 flow_id 查询、更新与删除。轻量 JSON 文件存储，无需数据库。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .flow_models import SimulationFlow

# 草稿文件默认文件名（落在插件数据目录下）
_DEFAULT_FILENAME = "simulation_flows.json"


class SimulationStorage:
    """模拟流草稿持久化存储。"""

    def __init__(self, data_dir: Path | str | None = None):
        # 存储类在构造时自持数据目录，不依赖外部装配；调用方未传 data_dir 时
        # 兜底回退到 StarTools 插件数据目录，避免“只写内存不落盘、重载后丢失”。
        if data_dir is None:
            try:
                from astrbot.api.star import StarTools

                data_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
            except Exception:
                data_dir = None
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self._file_path: Path | None = None
        # 进程内草稿索引：flow_id -> SimulationFlow（内存态为真源，落盘为镜像）
        self._flows: dict[str, SimulationFlow] = {}
        self._lock = threading.Lock()
        # 传入 data_dir 时立即配置落盘路径并加载既有草稿。
        # 若不在这里自动 configure，_file_path 恒为 None，_save_to_disk 会静默跳过，
        # 导致草稿只写内存不落盘，插件重载/进程重启后全部丢失。
        if self.data_dir is not None:
            self.configure(self.data_dir)

    def configure(self, data_dir: Path | str) -> None:
        """配置数据目录并加载既有草稿。"""
        self.data_dir = Path(data_dir)
        self._file_path = self.data_dir / _DEFAULT_FILENAME
        self._load_from_disk()

    @property
    def file_path(self) -> Path | None:
        """草稿文件路径（未配置时为 None）。"""
        return self._file_path

    def _load_from_disk(self) -> None:
        """从磁盘加载草稿索引。"""
        if self._file_path is None or not self._file_path.exists():
            self._flows = {}
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
            items = raw.get("flows", []) if isinstance(raw, dict) else []
            self._flows = {
                item.get("flow_id", ""): SimulationFlow.from_dict(item)
                for item in items
                if isinstance(item, dict) and item.get("flow_id")
            }
        except Exception as exc:
            logger.warning(f"[灾害预警] 加载模拟流草稿失败（已重置为空）: {exc}")
            self._flows = {}

    def _save_to_disk(self) -> None:
        """把内存索引落盘（临时文件 + 原子替换）。"""
        # 防御：data_dir 已配置但 file_path 尚未配置时自动补齐，
        # 避免草稿只写内存不落盘。
        if self._file_path is None and self.data_dir is not None:
            self._file_path = self.data_dir / _DEFAULT_FILENAME
        if self._file_path is None:
            return
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "flows": [flow.to_dict() for flow in self._flows.values()],
            }
            temp_file = self._file_path.with_suffix(".json.tmp")
            temp_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 原子替换，避免写入中途进程退出导致草稿文件损坏/半写
            os.replace(temp_file, self._file_path)
        except Exception as exc:
            logger.error(f"[灾害预警] 保存模拟流草稿失败: {exc}")
            # 清理残留临时文件
            try:
                temp_file = (
                    self._file_path.with_suffix(".json.tmp")
                    if self._file_path
                    else None
                )
                if temp_file and temp_file.exists():
                    temp_file.unlink()
            except Exception:
                pass

    def list_flows(self) -> list[dict[str, Any]]:
        """返回全部草稿（降序：最近更新在前）。"""
        with self._lock:
            flows = sorted(
                self._flows.values(),
                key=lambda f: f.updated_at,
                reverse=True,
            )
            return [flow.to_dict() for flow in flows]

    def get_flow(self, flow_id: str) -> SimulationFlow | None:
        """按 flow_id 获取草稿。"""
        with self._lock:
            return self._flows.get(flow_id)

    def save_flow(self, flow: SimulationFlow) -> SimulationFlow:
        """保存（新增或更新）草稿，并落盘。"""
        with self._lock:
            flow.updated_at = datetime.now(timezone.utc)
            self._flows[flow.flow_id] = flow
            self._save_to_disk()
            return flow

    def create_flow(
        self,
        *,
        name: str,
        steps: list[Any] | None = None,
        description: str = "",
        target_session: str = "",
    ) -> SimulationFlow:
        """创建新草稿并保存。"""
        flow = SimulationFlow.create(
            name=name,
            steps=steps,
            description=description,
            target_session=target_session,
        )
        return self.save_flow(flow)

    def update_flow(
        self,
        flow_id: str,
        *,
        name: str | None = None,
        steps: list[Any] | None = None,
        description: str | None = None,
        target_session: str | None = None,
    ) -> SimulationFlow | None:
        """更新既有草稿（仅覆盖传入字段），返回更新后的草稿。"""
        with self._lock:
            flow = self._flows.get(flow_id)
            if flow is None:
                return None
            if name is not None:
                flow.name = str(name).strip() or flow.name
            if steps is not None:
                flow.steps = list(steps)
            if description is not None:
                flow.description = str(description)
            if target_session is not None:
                flow.target_session = str(target_session)
            flow.updated_at = datetime.now(timezone.utc)
            self._save_to_disk()
            return flow

    def delete_flow(self, flow_id: str) -> bool:
        """删除草稿。"""
        with self._lock:
            existed = flow_id in self._flows
            if existed:
                self._flows.pop(flow_id, None)
                self._save_to_disk()
            return existed

    def import_from_dict(self, data: dict[str, Any]) -> SimulationFlow:
        """从前端提交的字典导入草稿（flow_id 不存在时生成新 ID）。"""
        flow = SimulationFlow.from_dict(data)
        return self.save_flow(flow)


# 全局草稿存储实例（由服务装配时配置数据目录）
_simulation_storage: SimulationStorage | None = None


def get_simulation_storage() -> SimulationStorage:
    """获取全局草稿存储实例（懒初始化）。"""
    global _simulation_storage
    if _simulation_storage is None:
        _simulation_storage = SimulationStorage()
    return _simulation_storage


__all__ = ["SimulationStorage", "get_simulation_storage"]
