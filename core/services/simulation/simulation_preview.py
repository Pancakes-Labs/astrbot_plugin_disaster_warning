"""
配置管理页实时推文预览能力模块。

把"单步模拟推文预览"抽象为两端可复用的后端能力：
- SimulationView（模拟预警）：单步试执行预览（mode=preview）
- ConfigView（配置管理）：左侧编辑配置草稿时，右侧实时预览
  基于"当前数据源示例数据 + 编辑中的 runtime_config"生成的推文
  与规则链过滤判定结果。

设计要点：
- 复用 SimulationBuilder 构建合法 EventEnvelope（含 [模拟] 标记）
- 复用 text_message_builder 构建消息链（纯文本优先，避免高频预览触发
  图片渲染/远程抓图导致卡顿；同时完整传入草稿 config 以反映
  display_timezone / emoji_filter_mode / weather_config 等展示参数）
- 复用 manager.evaluate_push_decision 做规则链评估（runtime_config 可传前端草稿）
- 图片/卡片降级提示按"数据源类型 + 该源对应渲染开关"精确匹配：
  互不污染；未开启某源图片功能时，该源事件不出现降级提示。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ...message.push.message_build_service import MessageBuildService
from .flow_models import SimulationStep
from .simulation_builder import SimulationBuilder


def _get_merged_metadata(envelope) -> dict[str, Any]:
    """合并事件 domain 与 envelope 层级的 metadata（envelope 覆盖 domain）。"""
    merged: dict[str, Any] = {}
    domain_event = getattr(envelope, "event", None)
    domain_meta = getattr(domain_event, "metadata", None)
    if isinstance(domain_meta, dict):
        merged.update(domain_meta)
    env_meta = getattr(envelope, "metadata", None)
    if isinstance(env_meta, dict):
        merged.update(env_meta)
    return merged


def _get_image_flags(
    runtime_config: dict[str, Any] | None,
    source_id: str,
    envelope=None,
) -> list[str]:
    """按数据源精确判断该源在当前配置下会附加哪些图片/卡片附件。"""
    metadata = _get_merged_metadata(envelope) if envelope is not None else {}
    return MessageBuildService.resolve_image_flags(
        source_id=source_id,
        active_config=runtime_config,
        metadata=metadata,
    )


def _is_image_render_enabled(
    runtime_config: dict[str, Any] | None,
    source_id: str,
    envelope=None,
) -> bool:
    """判断指定数据源在当前配置下是否附加了任何图片/卡片附件。"""
    return len(_get_image_flags(runtime_config, source_id, envelope)) > 0


def _extract_plain_text(chain) -> str:
    """从 MessageChain 中提取全部纯文本片段（拼接为预览文本）。"""
    texts = [
        getattr(comp, "text", "")
        for comp in getattr(chain, "chain", [])
        if hasattr(comp, "text")
    ]
    return "\n".join(text for text in texts if text).strip()


def _detect_image_components(chain) -> int:
    """统计消息链中的图片类组件数量（判断是否有图片未被纯文本展示）。"""
    count = 0
    for comp in getattr(chain, "chain", []) or []:
        type_name = type(comp).__name__.lower()
        if "image" in type_name:
            count += 1
    return count


async def build_config_preview(
    *,
    message_manager,
    step: SimulationStep,
    runtime_config: dict[str, Any] | None = None,
    session_id: str = "",
    builder: SimulationBuilder | None = None,
) -> dict[str, Any]:
    """构建配置页实时推文预览。

    复用模拟系统的构建 + 消息 + 规则链评估链路，但 runtime_config
    允许传入"前端编辑中的草稿配置"，从而实时反映过滤规则改动。

    消息构建策略：
    - 直接调用 text_message_builder.build()，传入草稿 config 的
      message_format 与完整 config，确保 display_timezone /
      emoji_filter_mode / weather_config 等展示参数实时反映草稿
    - 不触发图片渲染 / 远程抓图（适配高频实时预览场景）
    - 图片降级提示按"数据源 + 该源渲染开关"精确判断：
      开启了该源图片功能 → 提示"含 N 类图片附件，纯文本预览已省略"；
      未开启 → 不追加提示（用户根本没开该源的图片功能）

    Args:
        message_manager: 消息推送管理器（提供 text_message_builder 与
            evaluate_push_decision）
        step: 单步模拟指令（灾种/数据源/参数）
        runtime_config: 生效配置快照（缺省回退 message_manager.config）
        session_id: 规则链评估用的会话标识（可选，用于会话级差异配置）
        builder: 复用外部构建器实例（可选）

    Returns:
        {
            "preview_text": 纯文本预览
            "decision": {"accepted": bool, "reason": str, "detail": str}
            "image_flags": [str],  # 该源在当前配置下附加的图片类型列表
            "image_render_enabled": bool,  # 该源是否附加了任何图片
            "media_notice": str,  # 降级提示（附加了图片时才非空）
        }
    """
    builder = builder or SimulationBuilder()
    envelope = builder.build_step_envelope(step)

    # 仅在 runtime_config 不是 dict 时才回退持久化配置：
    # 前端显式传入 {}（如清空全部草稿字段）时，预览必须如实反映空配置，
    # 而非回退到持久化配置导致结果失真。
    active_config = (
        runtime_config
        if isinstance(runtime_config, dict)
        else getattr(message_manager, "config", {}) or {}
    )
    source_id = str(getattr(envelope.identity, "source_id", "") or "")

    # 1. 规则链评估：真实评估前端草稿配置（不注入 bypass 标记）。
    #    与发送链路（simulation_bypass_regular_filters=True 全放行）不同，
    #    预览必须让业务过滤规则（震级阈值 / 数据源开关 / 天气级别 / 关键词等）
    #    真实作用于草稿配置，才能实时反映"改过滤配置 → 推文拦截状态变化"。
    # 评估失败的默认值语义：accepted=False + 明确错误原因，绝不默认放行。
    # 规则链抛异常说明评估不可信，返回"评估失败"而非误导性的"规则链通过"，
    # 前端徽章会据此展示异常状态（拦截）。
    decision_payload: dict[str, Any] = {
        "accepted": False,
        "reason": "规则链评估失败",
        "detail": "",
    }
    evaluate_succeeded = False
    try:
        evaluate = getattr(message_manager, "evaluate_push_decision", None)
        if callable(evaluate):
            final_decision = evaluate(
                envelope,
                runtime_config=active_config,
                session_id=session_id,
                emit_filter_log=False,
                commit_state=False,
            )
            if final_decision is not None:
                decision_payload = {
                    "accepted": bool(getattr(final_decision, "accepted", True)),
                    "reason": str(getattr(final_decision, "reason", "") or ""),
                    "detail": str(getattr(final_decision, "detail", "") or ""),
                }
                evaluate_succeeded = True
    except Exception as exc:
        logger.debug(f"[灾害预警] 配置预览规则链评估失败: {exc}")
    if not evaluate_succeeded:
        # 规则链不可用或未返回结果时，同样标记为"评估失败"而非默认放行
        decision_payload = {
            "accepted": False,
            "reason": "规则链评估失败",
            "detail": "",
        }

    # 2. 构建消息链（纯文本优先，反映草稿展示参数）
    text_builder = getattr(message_manager, "text_message_builder", None)
    if text_builder is None:
        raise RuntimeError("文本消息构建器不可用")

    message_format_config = active_config.get("message_format", {})
    if not isinstance(message_format_config, dict):
        message_format_config = {}

    chain = text_builder.build(
        envelope,
        source_id,
        message_format_config,
        full_config=active_config,
    )
    # 应用 [模拟] 前缀（与完整推送链路出口对齐）
    build_service = getattr(message_manager, "message_build_service", None)
    if build_service is not None and hasattr(build_service, "_apply_simulation_prefix"):
        try:
            chain = build_service._apply_simulation_prefix(envelope, chain)
        except Exception:
            pass

    preview_text = _extract_plain_text(chain)
    has_images = _detect_image_components(chain)
    # 传入 envelope 供 CWA/海啸等"有媒体数据才附加"的类型按 metadata 精确判断
    image_flags = _get_image_flags(active_config, source_id, envelope)
    image_render_enabled = len(image_flags) > 0

    # 3. 降级提示：仅当该源在当前配置下确实附加了图片/卡片时追加
    #    （各源独立判断，互不污染；未开启某源图片功能则无提示）
    media_notice = ""
    if image_render_enabled:
        label = "、".join(image_flags)
        media_notice = f"\n\n本条消息包含{label}等附件，纯文本预览中已省略"

    return {
        "event_id": envelope.id,
        "preview_text": preview_text,
        "media_notice": media_notice,
        "has_images": has_images,
        "image_flags": image_flags,
        "image_render_enabled": image_render_enabled,
        "decision": decision_payload,
    }


__all__ = [
    "build_config_preview",
    "_get_image_flags",
    "_is_image_render_enabled",
    "_extract_plain_text",
    "_detect_image_components",
]
