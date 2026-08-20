"""
模拟预警系统 REST API 路由。

承载模拟预警的完整后端接口：
- GET  /api/simulation/schema         全灾种 × 全源参数 schema（前端表单驱动）
- GET  /api/simulation/flows          草稿列表
- POST /api/simulation/flows          保存/更新草稿
- DELETE /api/simulation/flows/{id}   删除草稿
- POST /api/simulation/run            整流执行（返回 run_id，前端轮询进度）
- GET  /api/simulation/run/{run_id}   查询执行进度/结果
- POST /api/simulation/run/step       单步试执行（返回预览/发送结果）
- GET  /api/simulation/runs           最近执行记录

设计要点：
- 执行器由模块级单例持有，避免每次请求重建（保留 event_key 前缀映射）
- 整流执行放入后台任务，接口立即返回 run_id
- 统计入库与跑马灯广播天然旁路（不经过 EventPipeline）
"""

from __future__ import annotations

import asyncio
from typing import Any

from astrbot.api import logger

try:
    from fastapi import Request
except ImportError:
    Request = None  # type: ignore[assignment,misc]

from ....services.simulation.flow_models import SimulationFlow, SimulationStep
from ....services.simulation.simulation_builder import SimulationBuilder
from ....services.simulation.simulation_preview import build_config_preview
from ....services.simulation.simulation_runner import SimulationRunner
from ....services.simulation.simulation_schema import (
    build_simulation_schema,
    suggest_weather_code,
    validate_step_params,
)
from ....services.simulation.simulation_storage import SimulationStorage
from ..payloads.api_response import ApiResponse

# 注意：runner / storage 不再使用模块级单例，改为挂在 disaster_service 上，
# 避免插件热重载后 disaster_service 重建时单例仍指向旧 message_manager 悬挂。


def _get_runner(
    disaster_service, message_manager, session_config_manager
) -> SimulationRunner:
    """获取执行器实例（挂在 disaster_service 上，热重载自动重建）。

    注意：progress_callback 通过每次调用从 disaster_service 动态读取，
    避免插件热重载后 runner 仍持有指向旧 disaster_service 的悬挂回调。
    """
    runner = getattr(disaster_service, "simulation_runner", None)
    if runner is None or runner.message_manager is not message_manager:
        runner = SimulationRunner(
            message_manager,
            session_config_manager,
            progress_callback=_make_progress_callback(disaster_service),
        )
        disaster_service.simulation_runner = runner
    else:
        # 保持回调动态：disaster_service 重建后仍能拿到新实例的 notify 方法
        runner._progress_callback = _make_progress_callback(disaster_service)
    return runner


def _make_progress_callback(disaster_service):
    """构造始终指向当前 disaster_service 实例的进度回调包装器。"""

    def _progress_callback(run):
        notify = getattr(disaster_service, "notify_simulation_progress", None)
        if callable(notify):
            return notify(run)
        return None

    return _progress_callback


def _get_storage(disaster_service) -> SimulationStorage:
    """获取草稿存储实例（挂在 disaster_service 上，热重载自动重建）。"""
    storage = getattr(disaster_service, "simulation_storage", None)
    if storage is None:
        data_dir = getattr(disaster_service, "storage_dir", None)
        # 兜底：storage_dir 缺失（服务初始化异常/早期装配）时，
        # 回退到 StarTools 数据目录，确保草稿始终可落盘，避免重载后丢失。
        if not data_dir:
            try:
                from astrbot.api.star import StarTools

                data_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")
            except Exception:
                data_dir = None
        storage = SimulationStorage(data_dir)
        disaster_service.simulation_storage = storage
    return storage


def _resolve_target_session(
    config: dict[str, Any], target_session: str = ""
) -> str | None:
    """解析模拟发送目标会话（显式指定优先，否则回退首个配置会话）。

    白名单校验（fail-closed）：显式传入的目标会话必须属于
    config["target_sessions"]。未配置目标会话或目标不在白名单内一律
    返回 None（调用处返回 400），避免把模拟消息推送到任意会话——
    空白名单时放行任意会话会把"未配置"静默变成"全放行"。
    """
    # 白名单统一规范化：去除首尾空白、字符串化，与整流接口
    # （simulation_runner）的会话比较规则保持一致。
    target_sessions = [
        str(s).strip() for s in config.get("target_sessions", []) if str(s).strip()
    ]
    if not target_sessions:
        return None
    target = str(target_session or "").strip()
    if target:
        return target if target in target_sessions else None
    return target_sessions[0]


def register_simulation_routes(app, disaster_service, config: dict[str, Any]):
    """注册模拟系统 REST API 路由。

    说明：message_manager / session_config_manager 在请求时动态读取，
    避免插件热重载后闭包内仍持有旧实例（悬挂引用）。
    """
    storage = _get_storage(disaster_service)

    def _current_message_manager():
        return getattr(disaster_service, "message_manager", None)

    def _current_session_config_manager():
        return getattr(disaster_service, "session_config_manager", None)

    def _get_current_runner() -> SimulationRunner:
        return _get_runner(
            disaster_service,
            _current_message_manager(),
            _current_session_config_manager(),
        )

    @app.get("/api/simulation/schema")
    async def get_simulation_schema():
        """获取模拟参数 Schema（前端动态渲染表单）。"""
        try:
            return ApiResponse.success(
                build_simulation_schema(config, _current_session_config_manager())
            )
        except Exception as e:
            logger.error(f"[灾害预警] 获取模拟 Schema 失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    # ------------------------------------------------------------------
    # 辅助接口：气象预警编码自动生成
    # ------------------------------------------------------------------
    @app.get("/api/simulation/weather-code-suggest")
    async def suggest_weather_code_api(request: Request):
        """根据预警标题/副标题自动生成紧凑 11B 预警编码。

        Query 参数：
        - title: 预警标题（必填）
        - headline: 副标题（可选，用于补充匹配）

        示例：GET /api/simulation/weather-code-suggest?title=靖远县气象台继续发布雷雨大风黄色预警信号
        → {"code": "11B2002"}
        """
        try:
            title = str(request.query_params.get("title") or "").strip()
            headline = str(request.query_params.get("headline") or "").strip()
            return ApiResponse.success({"code": suggest_weather_code(title, headline)})
        except Exception as e:
            logger.error(f"[灾害预警] 生成预警编码失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    # ------------------------------------------------------------------
    # 草稿 CRUD
    # ------------------------------------------------------------------
    @app.get("/api/simulation/flows")
    async def list_simulation_flows():
        """列出全部模拟流草稿。"""
        try:
            return ApiResponse.success({"flows": storage.list_flows()})
        except Exception as e:
            logger.error(f"[灾害预警] 列出模拟流草稿失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.post("/api/simulation/flows")
    async def save_simulation_flow(data: dict[str, Any]):
        """保存（新增或更新）模拟流草稿。"""
        try:
            flow = storage.import_from_dict(data)
            return ApiResponse.success({"flow": flow.to_dict()})
        except Exception as e:
            logger.error(f"[灾害预警] 保存模拟流草稿失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.delete("/api/simulation/flows/{flow_id}")
    async def delete_simulation_flow(flow_id: str):
        """删除模拟流草稿。"""
        try:
            deleted = storage.delete_flow(flow_id)
            if not deleted:
                return ApiResponse.error("草稿不存在", status_code=404)
            return ApiResponse.success({"deleted": True})
        except Exception as e:
            logger.error(f"[灾害预警] 删除模拟流草稿失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    @app.post("/api/simulation/run")
    async def run_simulation_flow(data: dict[str, Any]):
        """整流执行模拟流（后台任务，立即返回 run_id）。"""
        try:
            runner = _get_current_runner()
            if _current_message_manager() is None:
                return ApiResponse.error("消息推送管理器不可用", status_code=503)

            mode = str(data.get("mode") or "send").strip().lower()
            if mode not in ("send", "preview"):
                mode = "send"

            # 从请求体解析模拟流（支持直接传流或引用草稿）
            flow_data = data.get("flow")
            flow_id = data.get("flow_id") or ""
            if isinstance(flow_data, dict) and not flow_id:
                flow_id = flow_data.get("flow_id") or ""
            if isinstance(flow_data, dict):
                flow = SimulationFlow.from_dict(flow_data)
            elif flow_id:
                existing = storage.get_flow(flow_id)
                if existing is None:
                    return ApiResponse.error("草稿不存在", status_code=404)
                flow = existing
            else:
                return ApiResponse.error("缺少模拟流数据", status_code=400)

            # 参数校验：缺必填字段直接 400
            for idx, step in enumerate(flow.steps):
                missing = validate_step_params(step)
                if missing:
                    return ApiResponse.error(
                        f"步骤 {idx + 1} 缺少必填字段: {', '.join(missing)}",
                        status_code=400,
                    )

            # 预创建运行态并立即返回 run_id，前端可轮询进度
            run = runner.create_run(flow, mode=mode)
            task = asyncio.create_task(
                runner.run_flow(flow, mode=mode, run=run),
                name=f"sim_run_{flow.flow_id}",
            )
            # 注册后台任务，确保停机时统一回收
            if hasattr(disaster_service, "register_background_task"):
                disaster_service.register_background_task(task)

            return ApiResponse.success({"run_id": run.run_id})
        except Exception as e:
            logger.error(f"[灾害预警] 启动模拟流执行失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/simulation/run/{run_id}")
    async def get_simulation_run(run_id: str):
        """查询执行进度/结果。"""
        try:
            run = _get_current_runner().get_run(run_id)
            if run is None:
                return ApiResponse.error("执行不存在", status_code=404)
            return ApiResponse.success(run.to_dict())
        except Exception as e:
            logger.error(f"[灾害预警] 查询模拟执行失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.post("/api/simulation/run/{run_id}/cancel")
    async def cancel_simulation_run(run_id: str):
        """取消执行中的模拟流（仅标记取消，执行协程在步骤间隙检查）。"""
        try:
            cancelled = _get_current_runner().cancel_run(run_id)
            if not cancelled:
                return ApiResponse.error("执行不存在或已结束", status_code=404)
            return ApiResponse.success({"cancelled": True, "run_id": run_id})
        except Exception as e:
            logger.error(f"[灾害预警] 取消模拟执行失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.get("/api/simulation/runs")
    async def list_simulation_runs():
        """列出最近执行记录。"""
        try:
            return ApiResponse.success(
                {"runs": _get_current_runner().list_runs(limit=20)}
            )
        except Exception as e:
            logger.error(f"[灾害预警] 列出模拟执行记录失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    @app.post("/api/simulation/run/step")
    async def run_simulation_step(data: dict[str, Any]):
        """单步试执行：构建单步事件并返回预览或发送结果。"""
        try:
            message_manager = _current_message_manager()
            session_config_manager = _current_session_config_manager()
            if message_manager is None:
                return ApiResponse.error("消息推送管理器不可用", status_code=503)

            step_data = data.get("step")
            mode = str(data.get("mode") or "preview").strip().lower()
            if mode not in ("send", "preview"):
                mode = "preview"
            if not isinstance(step_data, dict):
                return ApiResponse.error("缺少步骤数据", status_code=400)

            step = SimulationStep.from_dict(step_data)
            missing = validate_step_params(step)
            if missing:
                return ApiResponse.error(
                    f"缺少必填字段: {', '.join(missing)}", status_code=400
                )

            builder = SimulationBuilder()
            envelope = builder.build_step_envelope(step)

            if mode == "preview":
                # 仅构建消息并返回预览文本
                chain = message_manager.message_build_service.build_message(envelope)
                texts = [
                    getattr(comp, "text", "")
                    for comp in getattr(chain, "chain", [])
                    if hasattr(comp, "text")
                ]
                preview = "\n".join(text for text in texts if text).strip()
                return ApiResponse.success(
                    {
                        "event_id": envelope.id,
                        "preview_text": preview,
                        "mode": "preview",
                    }
                )

            # 发送模式：解析目标会话后推送（含白名单校验）
            # 先规范化显式目标，避免与整流接口（simulation_runner）比较规则不一致。
            target_session = str(data.get("target_session") or "").strip()
            final_target_session = _resolve_target_session(config, target_session)
            if not target_session:
                # 未显式指定时回退到首个配置会话
                if not final_target_session:
                    return ApiResponse.error("未配置目标会话", status_code=400)
            elif final_target_session is None:
                # 显式指定的目标不在白名单内（与"未配置"区分，便于排查）
                return ApiResponse.error(
                    "目标会话不在已配置的目标会话列表中", status_code=400
                )

            runtime_config = None
            if session_config_manager is not None:
                try:
                    runtime_config = session_config_manager.get_effective_config(
                        final_target_session
                    )
                except Exception:
                    runtime_config = None
            if isinstance(runtime_config, dict):
                runtime_config = dict(runtime_config)
                runtime_config["__simulation_bypass_regular_filters"] = True

            # 先走规则链评估，输出拦截原因（与整流 runner / 命令侧对齐）
            decision_text = ""
            try:
                evaluate = getattr(message_manager, "evaluate_push_decision", None)
                if callable(evaluate):
                    final_decision = evaluate(
                        envelope,
                        runtime_config=runtime_config,
                        session_id=final_target_session,
                        emit_filter_log=False,
                        commit_state=False,
                    )
                    if final_decision is not None:
                        reason = str(getattr(final_decision, "reason", "") or "")
                        detail = str(getattr(final_decision, "detail", "") or "")
                        if getattr(final_decision, "accepted", False):
                            decision_text = f"规则链: ✅ 通过（{reason}）"
                        else:
                            suffix = f"（{detail}）" if detail else ""
                            decision_text = f"规则链: ❌ 拦截（{reason}{suffix}）"
            except Exception as exc:
                logger.debug(f"[灾害预警] 单步模拟规则链评估失败（已忽略）: {exc}")

            push_result = await message_manager.push_event(
                envelope,
                target_sessions=[final_target_session],
                session_config_getter=session_config_manager.get_effective_config
                if session_config_manager is not None
                else None,
                commit_state=False,
                skip_dedup=True,
                bypass_fusion=True,
                return_details=True,
            )
            if isinstance(push_result, dict):
                pushed = bool(push_result.get("success"))
                fail_reason = str(
                    push_result.get("final_failure_reason")
                    or push_result.get("reason")
                    or ""
                ).strip()
            else:
                pushed = bool(push_result)
                fail_reason = ""
            if pushed:
                msg_text = f"模拟事件已推送到 {final_target_session}"
            else:
                reason_part = f"｜{fail_reason}" if fail_reason else ""
                msg_text = f"事件未产生实际推送（被会话筛选拦截{reason_part}）"
            if decision_text:
                msg_text = f"{msg_text}\n{decision_text}"
            return ApiResponse.success(
                {
                    "event_id": envelope.id,
                    "mode": "send",
                    "success": pushed,
                    "message": msg_text,
                }
            )
        except Exception as e:
            logger.error(f"[灾害预警] 单步模拟执行失败: {e}")
            return ApiResponse.error(str(e), status_code=500)

    # ------------------------------------------------------------------
    # 配置页实时推文预览（复用模拟构建 + 消息 + 规则链链路）
    # ------------------------------------------------------------------
    @app.post("/api/simulation/preview")
    async def preview_simulation_message(data: dict[str, Any]):
        """配置管理页实时推文预览。

        请求体：
        {
            "disaster_type": "earthquake",
            "source_id": "cea_fanstudio",
            "params": { ... },        # 数据源示例参数（默认取自 schema）
            "runtime_config": { ... }, # 前端编辑中的配置草稿（未保存）
            "target_session": ""       # 可选：会话级配置合并用
        }

        返回：
        {
            "event_id": str,
            "preview_text": str,
            "media_notice": str,
            "has_images": int,
            "image_render_enabled": bool,
            "decision": {"accepted": bool, "reason": str, "detail": str},
        }
        """
        try:
            message_manager = _current_message_manager()
            if message_manager is None:
                return ApiResponse.error("消息推送管理器不可用", status_code=503)

            disaster_type = str(data.get("disaster_type") or "").strip()
            source_id = str(data.get("source_id") or "").strip()
            params = data.get("params")
            if not disaster_type or not source_id:
                return ApiResponse.error("缺少灾种或数据源标识", status_code=400)
            if not isinstance(params, dict):
                return ApiResponse.error("缺少参数对象 (params)", status_code=400)

            # 会话级配置：session 模式下前端编辑的就是 effective 配置
            # （global + override 已由后端合并），因此直接采用前端草稿即可；
            # 若前端未传（缺省），则按会话获取完整生效配置。
            runtime_config = data.get("runtime_config")
            if not isinstance(runtime_config, dict):
                runtime_config = None
            session_config_manager = _current_session_config_manager()
            target_session = str(data.get("target_session") or "").strip()
            if (
                runtime_config is None
                and target_session
                and session_config_manager is not None
            ):
                try:
                    runtime_config = session_config_manager.get_effective_config(
                        target_session
                    )
                except Exception:
                    runtime_config = None

            step = SimulationStep.create(
                disaster_type=disaster_type,
                source_id=source_id,
                params=params,
                report_num=1,
            )
            result = await build_config_preview(
                message_manager=message_manager,
                step=step,
                runtime_config=runtime_config,
                session_id=target_session,
            )
            return ApiResponse.success(result)
        except Exception as e:
            logger.error(f"[灾害预警] 配置预览失败: {e}")
            return ApiResponse.error(str(e), status_code=500)
