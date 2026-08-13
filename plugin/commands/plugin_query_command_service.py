"""
插件查询与模拟命令服务。
负责气象预警查询、台风信息查询、地震预警查询、地震列表查询与灾害预警模拟命令逻辑，
减少 main.DisasterWarningPlugin 中的查询与展示流程实现。
"""

from __future__ import annotations

import asyncio
import base64
import math
import os
import time
import traceback
import uuid
from datetime import datetime, timezone

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import MessageChain

from ...core.app.services import format_earthquake_list_text, quoted_plain_result
from ...core.domain.earthquake.cmt_normalize import (
    classify_fault_mechanism,
    format_fault_type_label,
)
from ...core.domain.event_context import EarthquakeDisplayContext
from ...core.message.presenters.earthquake_presenter import SnetPresenter
from ...core.message.presenters.weather_alarm_code_map import (
    resolve_local_weather_icon_abs_path,
    resolve_weather_icon_code,
)
from ...core.message.push.message_build_service import MessageBuildService
from ...core.message.render.beachball_renderer import BeachballRenderer
from ...core.message.render.jma_hypo_renderer import JmaHypoRenderer
from ...core.network.http.nmc_radar_client import NmcRadarClient
from ...core.services.query.aqi_query_service import (
    query_aqi,
    query_aqi_city_list,
    query_aqi_rank,
)
from ...core.services.query.ground_motion_query_service import (
    GroundMotionInput,
    predict_ground_motion,
)
from ...core.services.query.jma_hypo_query_presenter import (
    build_jma_hypo_list_text,
    build_jma_hypo_plot_caption,
)
from ...core.services.query.jma_hypo_query_service import (
    query_jma_hypo_list,
    query_jma_hypo_plot,
)
from ...core.services.query.quake_text_extractor import (
    extract_quoted_quake_params,
)
from ...core.services.query.radar_query_service import (
    format_candidates_text,
    query_radar_gif,
    query_radar_image,
    query_radar_list,
    resolve_radar_target,
)
from ...core.services.query.realrank_query_service import (
    TIME_ARG_HELP,
    parse_rank_args,
    parse_time_arg,
    query_rank,
    resolve_rank_type,
)
from ...core.services.query.typhoon_query_parser import DETAIL_CURRENT, DETAIL_FULL
from ...core.services.query.typhoon_query_presenter import attach_summary_text
from ...core.services.query.typhoon_query_service import (
    build_typhoon_query_text,
    parse_typhoon_query_args,
    query_typhoon_data,
)
from ...core.services.query.weather_query_service import query_weather_alarm_data
from ...core.services.query.weather_station_query_service import (
    WeatherStationQueryService,
)
from ...core.services.simulation.flow_models import (
    DISASTER_TYPE_EARTHQUAKE,
    DISASTER_TYPE_TSUNAMI,
    DISASTER_TYPE_TYPHOON,
    DISASTER_TYPE_WEATHER,
    SimulationStep,
)
from ...core.services.simulation.simulation_builder import SimulationBuilder
from ...core.sources.source_catalog import SOURCE_CATALOG, get_source_entry
from .forward_helper import send_forward_blocks
from .telemetry_mixin import CommandTelemetryMixin
from .typhoon_query_image_helper import append_typhoon_track_image


class PluginQueryCommandService(CommandTelemetryMixin):
    """插件查询与模拟命令服务。"""

    def __init__(self, plugin):
        self.plugin = plugin

    def _build_weather_icon_components(
        self,
        icon_url: str,
        weather_type_code: str,
    ) -> list:
        """构建气象预警图标消息组件（本地优先）。

        命令发送进程无法访问管理端静态路由 /weatheralarm_logo/，
        因此当图标是本地文件时，直接读取文件转 Base64 发送；
        仅当是远程 URL 时才使用 Comp.Image.fromURL。

        Args:
            icon_url: 查询结果中的 icon_url（可能是本地静态 URL 或远程 URL）。
            weather_type_code: 气象预警类型编码，用于本地文件解析兜底。

        Returns:
            图标消息组件列表；解析失败时返回空列表（不阻断文本发送）。
        """
        icon_url_str = str(icon_url or "").strip()
        if not icon_url_str:
            return []

        # 本地静态 URL（/weatheralarm_logo/...）时直读 Base64 文件。
        # 非本地 URL（Fan Studio 官方接口等远程地址）时，按 11B 完整码尝试解析本地文件，
        # 本地文件存在则优先本地发送，否则回退远程 URL 直发。
        local_path = None
        if icon_url_str.startswith("/weatheralarm_logo/"):
            local_path = os.path.join(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                ),
                "resources",
                "weatheralarm_logo",
                os.path.basename(icon_url_str),
            )
        else:
            # 先把 weather_type_code 统一解析为 11B 完整码（p 编码/紧凑码/标题兜底），
            # 再按 11B 码映射本地文件；直接传 p 编码会导致本地文件永远找不到。
            icon_code = resolve_weather_icon_code(weather_type_code)
            if icon_code:
                local_path = resolve_local_weather_icon_abs_path(icon_code)

        if local_path and os.path.isfile(local_path):
            try:
                with open(local_path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode()
                return [Comp.Image.fromBase64(b64_data)]
            except Exception as e:
                logger.warning(
                    f"[灾害预警] 本地气象预警图标读取失败，回退 URL 发送: "
                    f"{local_path}, 错误信息: {e}"
                )

        # 远程 URL 直发
        return [Comp.Image.fromURL(icon_url_str)]

    async def handle_generate_beachball(
        self,
        event,
        strike: str,
        dip: str,
        rake: str,
        size_str: str | None = None,
        line_width_str: str | None = None,
    ):
        """处理生成沙滩球图片命令。"""

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        try:
            # 参数解析与校验
            try:
                strike_val = float(strike)
                dip_val = float(dip)
                rake_val = float(rake)
                if (
                    math.isnan(strike_val)
                    or math.isinf(strike_val)
                    or math.isnan(dip_val)
                    or math.isinf(dip_val)
                    or math.isnan(rake_val)
                    or math.isinf(rake_val)
                ):
                    raise ValueError("Numeric overflow or NaN")
            except ValueError:
                yield _quoted_plain_result("❌ 走向、倾角与滑动角必须为有效数值。")
                return

            size = 360
            if size_str:
                try:
                    size = int(size_str)
                    if size < 100 or size > 1024:
                        yield _quoted_plain_result(
                            "⚠️ 提示：图片大小限制在 100 到 1024 像素之间，已自动修正。"
                        )
                        size = max(100, min(1024, size))
                except ValueError:
                    pass

            line_width = 6
            if line_width_str:
                try:
                    line_width = int(line_width_str)
                    if line_width < 1 or line_width > 15:
                        yield _quoted_plain_result(
                            "⚠️ 提示：线宽限制在 1 到 15 像素之间，已自动修正。"
                        )
                        line_width = max(1, min(15, line_width))
                except ValueError:
                    pass

            renderer = BeachballRenderer(size=size, line_width=line_width)
            service = getattr(self.plugin, "disaster_service", None)
            message_manager = (
                getattr(service, "message_manager", None) if service else None
            )
            temp_dir = getattr(message_manager, "temp_dir", None)
            if temp_dir is None:
                plugin_root = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                temp_dir = os.path.join(plugin_root, "temp")
                os.makedirs(temp_dir, exist_ok=True)

            img_filename = f"beachball_{uuid.uuid4().hex}_{int(time.time())}.png"
            img_path = os.path.join(str(temp_dir), img_filename)

            # 调用 Pillow 渲染器（线宽经构造参数与 render 参数双重生效）
            render_started = time.perf_counter()
            out = await asyncio.to_thread(
                renderer.render,
                strike=strike_val,
                dip=dip_val,
                rake=rake_val,
                output_path=img_path,
                line_width=line_width,
            )
            elapsed = time.perf_counter() - render_started

            if not out or not os.path.exists(out):
                yield _quoted_plain_result("❌ 生成沙滩球失败。")
                return

            with open(out, "rb") as f:
                b64_data = base64.b64encode(f.read()).decode()

            try:
                os.unlink(out)
            except Exception:
                pass

            logger.info(f"[灾害预警] 沙滩球渲染成功，耗时 {elapsed:.3f}秒")

            await self._track_command_feature(
                "command_generate_beachball",
                {
                    "success": True,
                    "strike": strike_val,
                    "dip": dip_val,
                    "rake": rake_val,
                    "size": size,
                    "line_width": line_width,
                },
            )

            yield event.chain_result(
                self.plugin._with_quote_reply(
                    event,
                    [Comp.Image.fromBase64(b64_data)],
                )
            )

        except Exception as e:
            logger.error(f"[灾害预警] 生成沙滩球失败: {e}", exc_info=True)
            yield _quoted_plain_result(f"❌ 生成沙滩球失败: {e}")

    async def handle_parse_nodal_plane(
        self,
        event,
        strike: str,
        dip: str,
        rake: str,
    ):
        """处理节面解析命令。"""

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        try:
            try:
                strike_val = float(strike)
                dip_val = float(dip)
                rake_val = float(rake)
                if (
                    math.isnan(strike_val)
                    or math.isinf(strike_val)
                    or math.isnan(dip_val)
                    or math.isinf(dip_val)
                    or math.isnan(rake_val)
                    or math.isinf(rake_val)
                ):
                    raise ValueError("Numeric overflow or NaN")
            except ValueError:
                yield _quoted_plain_result("❌ 走向、倾角与滑动角必须为有效数值。")
                return

            plane = {
                "strike": strike_val,
                "dip": dip_val,
                "rake": rake_val,
                "raw": f"{strike}/{dip}/{rake}",
            }
            mechanism = classify_fault_mechanism(rake_val)
            plane.update(mechanism)

            label = format_fault_type_label(plane)

            # 解析逆断层/正断层以及占比成分信息
            dip_slip_name = plane.get("dip_slip_name") or ""
            strike_slip_name = plane.get("strike_slip_name") or ""
            dip_slip_pct = plane.get("dip_slip_pct")
            strike_slip_pct = plane.get("strike_slip_pct")

            lines = [
                "🔮 节面成分解析结果：",
                f"🧭 节面参数：走向 {strike_val}° / 倾角 {dip_val}° / 滑动角 {rake_val}°",
                f"⚙️ 破裂机制：{label}",
                "📊 运动分量：",
                f"  • 倾滑分量: {dip_slip_name} ({dip_slip_pct}%)",
                f"  • 走滑分量: {strike_slip_name} ({strike_slip_pct}%)",
            ]

            await self._track_command_feature(
                "command_parse_nodal_plane",
                {
                    "success": True,
                    "strike": strike_val,
                    "dip": dip_val,
                    "rake": rake_val,
                },
            )

            yield _quoted_plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"[灾害预警] 节面解析失败: {e}", exc_info=True)
            yield _quoted_plain_result(f"❌ 节面解析失败: {e}")

    async def handle_query_weather_alarm(
        self,
        event,
        keyword: str | None = None,
        optional_a: str | None = None,
        optional_b: str | None = None,
        optional_c: str | None = None,
    ):
        """处理气象预警查询命令，支持指定地区、类型、级别与时间范围，全国模式下支持分批合并转发展示。"""

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        def _header_builder(
            batch_index: int, batch_total: int, total_blocks: int
        ) -> str:
            """构建气象预警合并转发头部（带分段进度，对齐原实现）。"""
            return (
                f"📋 全国气象预警列表（共 {total_blocks} 段）"
                f"\n📦 分段发送：{batch_index + 1}/{batch_total}"
            )

        async def _send_forward_batches(blocks: list[str]) -> bool:
            """将全国级海量数据分批打包为合并转发气泡发送给会话。

            复用公共 forward_helper 的显式发送逻辑。
            """
            return await send_forward_blocks(
                self.plugin,
                event,
                blocks,
                header_builder=_header_builder,
                name="灾害预警",
            )

        async def _send_text_blocks(blocks: list[str], total_count: int) -> None:
            """若合并转发节点被平台拒绝，则降级为分段文本气泡发送。"""
            if not blocks:
                return

            for idx, block in enumerate(blocks):
                prefix = f"📋 气象预警列表（共 {total_count} 条）\n" if idx == 0 else ""
                if idx == 0:
                    chain = MessageChain(
                        self.plugin._with_quote_reply(
                            event, [Comp.Plain(prefix + block)]
                        )
                    )
                else:
                    chain = MessageChain([Comp.Plain(block)])
                await self.plugin.context.send_message(event.unified_msg_origin, chain)

        if not self.plugin.disaster_service:
            yield _quoted_plain_result("❌ 灾害预警服务未启动")
            return

        if not keyword:
            yield _quoted_plain_result(
                "❌ 参数不足。\n"
                "用法：\n"
                "• /气象预警查询 <省份/地名> [<预警类型>] [<预警颜色>] [全部|全日期]\n"
                "• /气象预警查询 全国 [<预警类型>] [<预警颜色>] [全部|全日期]\n"
                "• /气象预警查询 <预警ID>"
            )
            return

        try:
            db = self.plugin.disaster_service.statistics_manager.db
            result = await query_weather_alarm_data(
                db,
                keyword,
                optional_a,
                optional_b,
                optional_c=optional_c,
            )

            if not result.get("success"):
                error_text = str(result.get("error") or "查询失败")
                if "官方渠道" not in error_text:
                    error_text = f"{error_text} 可尝试通过其他官方渠道进行查询"
                filters = result.get("filters")
                if isinstance(filters, dict) and result.get("query_mode") == "search":
                    desc = [f"地区={filters.get('location')}"]
                    if filters.get("type"):
                        desc.append(f"预警类型={filters.get('type')}")
                    if filters.get("color"):
                        desc.append(f"预警颜色={filters.get('color')}")
                    # 时间范围：全日期模式或显式传了时间关键词时补充说明
                    if filters.get("all_date_mode"):
                        desc.append("时间范围=全部日期")
                    elif filters.get("time_window_hours"):
                        desc.append(
                            f"时间范围=近{filters.get('time_window_hours')}小时"
                        )
                    if desc:
                        error_text = f"❌ {error_text}\n检索条件：{'，'.join(desc)}"
                    else:
                        error_text = f"❌ {error_text}"
                else:
                    error_text = f"❌ {error_text}"

                if result.get("usage"):
                    usage_lines = "\n".join(f"• {line}" for line in result["usage"])
                    error_text = f"{error_text}\n用法：\n{usage_lines}"

                await self._track_command_feature(
                    "command_weather_query",
                    {
                        "success": False,
                        "query_mode": str(result.get("query_mode") or "unknown"),
                        "has_optional_type": bool(optional_a),
                        "has_optional_level": bool(optional_b),
                    },
                )
                yield _quoted_plain_result(error_text)
                return

            if result.get("query_mode") == "id":
                # 按预警ID检索，生成详细指南说明
                detail = result.get("data") or {}
                title_text = str(detail.get("title_text") or "").strip()
                headline_text = str(detail.get("headline_text") or "").strip()
                body_text = str(detail.get("body_text") or "").strip()
                color_emoji = str(detail.get("color_emoji") or "")

                if title_text:
                    title_line = f"📋{title_text}{color_emoji}"
                elif headline_text:
                    title_line = f"📋{headline_text}{color_emoji}"
                else:
                    title_line = "📋气象预警详情"

                lines = [title_line]
                if body_text:
                    lines.append(f"📝{body_text}")
                else:
                    lines.append("📝暂无详细描述")

                guideline_text = str(detail.get("guideline_text") or "").strip()
                if guideline_text:
                    lines.append(guideline_text)

                detail_text = "\n".join(lines)
                icon_url = detail.get("icon_url")
                weather_type_code = str(detail.get("weather_type_code") or "").strip()
                await self._track_command_feature(
                    "command_weather_query",
                    {
                        "success": True,
                        "query_mode": "id",
                        "has_icon": bool(icon_url),
                    },
                )
                if icon_url:
                    try:
                        yield event.chain_result(
                            self.plugin._with_quote_reply(
                                event,
                                [
                                    Comp.Plain(detail_text),
                                    *self._build_weather_icon_components(
                                        icon_url, weather_type_code
                                    ),
                                ],
                            )
                        )
                    except Exception as icon_error:
                        logger.warning(
                            f"[灾害预警] 发送气象预警图标失败，已回退文本: {icon_error}"
                        )
                        yield _quoted_plain_result(detail_text)
                else:
                    yield _quoted_plain_result(detail_text)
                return

            items = result.get("items") or []
            text_blocks = result.get("text_blocks") or []
            is_nationwide = bool(result.get("is_nationwide"))
            total = result.get("total", len(items))

            if is_nationwide and text_blocks:
                try:
                    # 全国级查询优先走分段合并转发通道发送
                    ok = await _send_forward_batches(text_blocks)
                    if ok:
                        await self._track_command_feature(
                            "command_weather_query",
                            {
                                "success": True,
                                "query_mode": str(result.get("query_mode") or "search"),
                                "is_nationwide": True,
                                "result_count": int(total or 0),
                                "has_optional_type": bool(optional_a),
                                "has_optional_level": bool(optional_b),
                                "delivery_mode": "forward_batches",
                            },
                        )
                        return
                except Exception as forward_error:
                    logger.warning(
                        f"[灾害预警] 合并转发送失败，回退文本: {forward_error}"
                    )
                    try:
                        await _send_text_blocks(text_blocks, total)
                        await self._track_command_feature(
                            "command_weather_query",
                            {
                                "success": True,
                                "query_mode": str(result.get("query_mode") or "search"),
                                "is_nationwide": True,
                                "result_count": int(total or 0),
                                "has_optional_type": bool(optional_a),
                                "has_optional_level": bool(optional_b),
                                "delivery_mode": "text_blocks",
                            },
                        )
                        return
                    except Exception as text_error:
                        logger.warning(f"[灾害预警] 文本回退发送失败: {text_error}")
                        # 全国分支发送与文本回退均已尝试且失败：直接结束，
                        # 避免控制流继续落入下方单卡二次尝试造成重复发送。
                        return

            # 正常区域搜索：结果较多时也走合并转发分批发送，避免单条消息过长
            if text_blocks and len(text_blocks) > 1:
                try:
                    ok = await _send_forward_batches(text_blocks)
                    if ok:
                        await self._track_command_feature(
                            "command_weather_query",
                            {
                                "success": True,
                                "query_mode": str(result.get("query_mode") or "search"),
                                "is_nationwide": is_nationwide,
                                "result_count": int(total or 0),
                                "has_optional_type": bool(optional_a),
                                "has_optional_level": bool(optional_b),
                                "delivery_mode": "forward_batches",
                            },
                        )
                        return
                except Exception as forward_error:
                    logger.warning(
                        f"[灾害预警] 合并转发送失败，回退文本: {forward_error}"
                    )
                    try:
                        await _send_text_blocks(text_blocks, total)
                        await self._track_command_feature(
                            "command_weather_query",
                            {
                                "success": True,
                                "query_mode": str(result.get("query_mode") or "search"),
                                "is_nationwide": is_nationwide,
                                "result_count": int(total or 0),
                                "has_optional_type": bool(optional_a),
                                "has_optional_level": bool(optional_b),
                                "delivery_mode": "text_blocks",
                            },
                        )
                        return
                    except Exception as text_error:
                        logger.warning(f"[灾害预警] 文本回退发送失败: {text_error}")

            # 结果较少时，组装文字概要
            lines = [f"📋 气象预警列表（共 {total} 条）"]
            for idx, item in enumerate(items):
                lines.append(f"发布时间：{item.get('issue_time') or '未知时间'}")
                lines.append(f"ID：{item.get('alarm_id') or '未知ID'}")
                lines.append(f"发布机构：{item.get('publish_org') or '未知发布机构'}")
                lines.append(
                    f"预警类型：{item.get('weather_type_line') or '未知类型预警'}"
                )
                if idx != len(items) - 1:
                    lines.append("")

            await self._track_command_feature(
                "command_weather_query",
                {
                    "success": True,
                    "query_mode": str(result.get("query_mode") or "search"),
                    "is_nationwide": is_nationwide,
                    "result_count": int(total or 0),
                    "has_optional_type": bool(optional_a),
                    "has_optional_level": bool(optional_b),
                },
            )
            yield _quoted_plain_result("\n".join(lines))
        except Exception as e:
            logger.error(f"[灾害预警] 查询气象预警失败: {e}")
            yield _quoted_plain_result(f"❌ 查询失败: {e}")

    async def handle_query_typhoon(
        self,
        event,
        arg1: str | None = None,
        arg2: str | None = None,
        arg3: str | None = None,
    ):
        """处理台风信息查询命令。

        优先复用 EQSC 查询逻辑；配置无效或查询失败时回退本地数据库（Fan/EQSC重建）。
        支持指定 ID、名称、数量、活跃过滤与详细程度（当前信息/完整路径）。
        单台风查询为渲染路径图可内部提升为完整轨迹，但返回文本仍按用户 detail。
        当结果含 history_track 时，尝试附加台风路径图（列表仅渲首张）。
        """

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        if not self.plugin.disaster_service:
            yield _quoted_plain_result("❌ 灾害预警服务未启动")
            return

        try:
            parsed = parse_typhoon_query_args(arg1, arg2, arg3)
            db = self.plugin.disaster_service.statistics_manager.db
            enrichment = getattr(
                self.plugin.disaster_service, "typhoon_enrichment_service", None
            )
            # 单台风（ID/名称）为出路径图，查询侧将 current 提升为 full 以拿到 history_track；
            # 文本展示仍尊重用户原始 detail。列表查询（无 ID/名称）不提升，避免批量拉轨迹。
            user_detail = parsed.get("detail") or DETAIL_CURRENT
            query_detail = user_detail
            if user_detail == DETAIL_CURRENT and (
                parsed.get("typhoon_id") or parsed.get("keyword")
            ):
                query_detail = DETAIL_FULL
            result = await query_typhoon_data(
                db,
                enrichment,
                typhoon_id=parsed.get("typhoon_id"),
                keyword=parsed.get("keyword"),
                count=parsed.get("count"),
                detail=query_detail,
                active_only=bool(parsed.get("active_only")),
            )

            # 轨迹字段保留给路径图；summary_text / detail 按用户参数重写，避免文本被提升成完整路径。
            if result.get("success") and query_detail != user_detail:
                data_item = result.get("data")
                if isinstance(data_item, dict):
                    attach_summary_text(data_item, detail=user_detail)
                for item in result.get("items") or []:
                    if isinstance(item, dict):
                        attach_summary_text(item, detail=user_detail)
                result["detail"] = user_detail

            await self._track_command_feature(
                "command_typhoon_query",
                {
                    "success": bool(result.get("success")),
                    "query_mode": str(result.get("query_mode") or "unknown"),
                    "source": str(result.get("source") or "unknown"),
                    "detail": str(result.get("detail") or "current"),
                    "has_id": bool(parsed.get("typhoon_id")),
                    "has_keyword": bool(parsed.get("keyword")),
                    "active_only": bool(parsed.get("active_only")),
                    "result_count": int(result.get("total") or 0),
                },
            )
            text = build_typhoon_query_text(result)
            chain_parts: list = [Comp.Plain(text)]
            chain_parts = await append_typhoon_track_image(
                plugin=self.plugin,
                result=result,
                chain_parts=chain_parts,
            )
            # 完整模式：无论有无路径图，统一走合并转发（显示名「灾害预警」）。
            # 有路径图时把图组件随文本一起进合并转发节点。
            if user_detail == DETAIL_FULL:
                comps = None
                if len(chain_parts) > 1:
                    comps = [chain_parts[1:]]
                ok = await send_forward_blocks(
                    self.plugin,
                    event,
                    [text],
                    name="灾害预警",
                    block_components=comps,
                )
                if not ok:
                    # 合并转发失败（如平台拒绝）时回退为普通引用回复，确保有文本输出
                    yield _quoted_plain_result(text)
                return

            # 非完整模式：有路径图时走普通消息链（文本+图），无图走普通文本回复
            if len(chain_parts) > 1:
                try:
                    if hasattr(self.plugin, "_with_quote_reply"):
                        yield event.chain_result(
                            self.plugin._with_quote_reply(event, chain_parts)
                        )
                    else:
                        yield event.chain_result(chain_parts)
                    return
                except Exception:
                    yield _quoted_plain_result(text)
                    try:
                        await self.plugin.context.send_message(
                            event.unified_msg_origin,
                            MessageChain([chain_parts[1]]),
                        )
                    except Exception:
                        pass
                    return
            yield _quoted_plain_result(text)
        except Exception as e:
            logger.error(f"[灾害预警] 查询台风信息失败: {e}")
            yield _quoted_plain_result(f"❌ 查询失败: {e}")

    async def handle_query_earthquake_warning(self, event):
        """处理地震预警状态查询命令，展示当前的地震预警缓存快照。"""

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        if not self.plugin.disaster_service:
            yield _quoted_plain_result("❌ 灾害预警服务未启动")
            return

        try:
            text = self.plugin.disaster_service.get_eew_query_text()
            await self._track_command_feature(
                "command_eew_status_query",
                {"success": True},
            )
            yield _quoted_plain_result(text)
        except Exception as e:
            logger.error(f"[灾害预警] 查询地震预警状态失败: {e}")
            yield _quoted_plain_result(f"❌ 查询失败: {e}")

    async def handle_query_earthquake_list(
        self,
        event,
        source: str = "cenc",
        count: int = 9,
        mode: str = "card",
    ):
        """处理历史地震列表查询命令，支持渲染多媒体卡片图或回退文本格式。"""

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        if not self.plugin.disaster_service:
            yield _quoted_plain_result("❌ 灾害预警服务未启动")
            return

        source = source.lower()
        if source not in ["cenc", "jma"]:
            yield _quoted_plain_result("❌ 无效的数据源，仅支持 cenc 或 jma")
            return

        try:
            show_card = mode.lower() != "text"
            max_count = 50 if show_card else 50
            if count > max_count:
                count = max_count
                yield _quoted_plain_result(
                    f"⚠️ 提示：{'卡片' if show_card else '文本'}模式最多支持显示 {max_count} 条记录"
                )
            elif count < 1:
                count = 1

            request_count = 50
            formatted_list = self.plugin.disaster_service.earthquake_list_service.get_formatted_list_data(
                source, request_count
            )
            if not formatted_list:
                yield _quoted_plain_result(
                    f"❌ 未找到 {source.upper()} 的地震列表数据，可能是因为服务刚启动，尚未获取到数据。"
                )
                return

            if show_card and self.plugin.disaster_service.message_manager:
                display_list = formatted_list[:count]
                source_name = (
                    "中国地震台网 (CENC)" if source == "cenc" else "日本气象厅 (JMA)"
                )
                img_path = await self.plugin.disaster_service.message_manager.render_earthquake_list_card(
                    display_list, source_name
                )
                if img_path:
                    await self._track_command_feature(
                        "command_earthquake_list_query",
                        {
                            "success": True,
                            "source": source,
                            "mode": "card",
                            "count": int(count),
                        },
                    )
                    yield event.chain_result(
                        self.plugin._with_quote_reply(
                            event,
                            [Comp.Image.fromFileSystem(img_path)],
                        )
                    )
                    return

            text = format_earthquake_list_text(formatted_list[:count], source)
            await self._track_command_feature(
                "command_earthquake_list_query",
                {
                    "success": True,
                    "source": source,
                    "mode": "card" if show_card else "text",
                    "count": int(count),
                },
            )
            if show_card:
                # 卡片模式（图片）保持普通引用回复
                yield _quoted_plain_result(text)
            else:
                # 文本模式：多条地震列表显式走合并转发，失败则回退引用回复
                ok = await send_forward_blocks(
                    self.plugin,
                    event,
                    [text],
                    name="灾害预警",
                )
                if not ok:
                    yield _quoted_plain_result(text)
        except Exception as e:
            logger.error(f"[灾害预警] 查询地震列表失败: {e}")
            yield _quoted_plain_result(f"❌ 查询失败: {e}")

    async def handle_simulate_disaster(
        self,
        event,
        arg1: str = None,
        arg2: str = None,
        arg3: str = None,
        arg4: str = None,
        arg5: str = None,
        lat: float = None,
        lon: float = None,
        magnitude: float = None,
        depth: float = None,
        source: str = "cea_fanstudio",
    ):
        """处理模拟灾害命令。

        参数按数据源动态解析（数据源置于末尾 arg5，决定灾种与参数格式）：
        - 地震源: arg1=纬度 arg2=经度 arg3=震级 arg4=深度 arg5=数据源
        - 海啸源: arg1=标题 arg2=等级 arg3=位置 arg4=源震级 arg5=数据源
        - 气象源: arg1=标题 arg2=正文 arg3=预警编码 arg5=数据源
        - 台风源: arg1=编号 arg2=名称 arg3=强度 arg5=数据源

        所有灾种统一走完整规则链评估，通过后推送展示（含 [模拟] 前缀）。
        """

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        if not self.plugin.disaster_service:
            yield _quoted_plain_result("❌ 灾害预警服务未启动")
            return

        try:
            manager = self.plugin.disaster_service.message_manager
            target_session = event.unified_msg_origin
            if not target_session:
                yield _quoted_plain_result("❌ 无法识别当前会话，无法执行模拟推送")
                return

            session_config_manager = self.plugin.disaster_service.session_config_manager
            runtime_config = session_config_manager.get_effective_config(target_session)

            # --- 数据源置于末尾：从最后一个非空参数中识别数据源 ---
            # 气象/台风业务参数较少（3 个），数据源通常落在 arg4 而非 arg5；
            # 地震/海啸省略可选参数（深度/源震级）后数据源同样会前移。
            # 因此从尾部反向扫描第一个合法数据源标识作为 source，
            # 并将其从参数列表剔除，剩余参数再按灾种解析。
            args_list = [arg1, arg2, arg3, arg4, arg5]
            source_arg_index = -1
            for idx in range(len(args_list) - 1, -1, -1):
                candidate = args_list[idx]
                if candidate and get_source_entry(candidate) is not None:
                    source = candidate
                    source_arg_index = idx
                    break
            # 末尾存在非空参数但未被识别为合法数据源 → 视为用户写错的数据源
            if source_arg_index < 0 and args_list[-1]:
                source = args_list[-1]
                source_arg_index = len(args_list) - 1
            if source_arg_index >= 0:
                args_list[source_arg_index] = None
            arg1, arg2, arg3, arg4, arg5 = args_list

            # --- 数据源预校验：无效源直接报错，避免后续静默回退地震 ---
            source_entry = get_source_entry(source)
            if source_entry is None:
                valid_sources = ", ".join(sorted(SOURCE_CATALOG.keys()))
                yield _quoted_plain_result(
                    f"❌ 无效的数据源: {source}\n可用数据源: {valid_sources}"
                )
                return

            # --- 按数据源动态解析灾种（决定参数格式，命令层不再传灾种） ---
            source_type = source_entry.source_type.value
            if source_type == "tsunami":
                disaster_type = DISASTER_TYPE_TSUNAMI
            elif source_type == "weather":
                disaster_type = DISASTER_TYPE_WEATHER
            elif source_type == "typhoon":
                disaster_type = DISASTER_TYPE_TYPHOON
            else:
                disaster_type = DISASTER_TYPE_EARTHQUAKE

            # --- 按灾种解析 arg1-4 参数 ---
            # 数字参数安全解析：非数字返回 None，由调用方回退默认值或报错
            def _safe_arg_float(value):
                if value is None or value == "":
                    return None
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None

            if disaster_type == DISASTER_TYPE_EARTHQUAKE:
                lat = (
                    lat
                    if lat is not None
                    else (_safe_arg_float(arg1) if arg1 else 39.9)
                )
                lon = (
                    lon
                    if lon is not None
                    else (_safe_arg_float(arg2) if arg2 else 116.4)
                )
                magnitude = (
                    magnitude
                    if magnitude is not None
                    else (_safe_arg_float(arg3) if arg3 else 5.5)
                )
                depth = (
                    depth
                    if depth is not None
                    else (_safe_arg_float(arg4) if arg4 else 10.0)
                )

                # 用户显式传了参数但解析失败 → 报错而非静默回退
                if (
                    (arg1 and lat is None)
                    or (arg2 and lon is None)
                    or (arg3 and magnitude is None)
                    or (arg4 and depth is None)
                ):
                    yield _quoted_plain_result(
                        "❌ 地震模拟参数无效，请检查：纬度/经度/震级/深度 应为数字"
                    )
                    return

                params = {
                    "latitude": lat,
                    "longitude": lon,
                    "magnitude": magnitude,
                    "depth": depth,
                }
            # 海啸：arg1=标题 arg2=等级 arg3=位置 arg4=源震级
            elif disaster_type == DISASTER_TYPE_TSUNAMI:
                tsunami_magnitude = _safe_arg_float(arg4)
                # 用户显式传了参数但解析失败 → 报错而非静默回退（与地震分支一致）
                if arg4 and tsunami_magnitude is None:
                    yield _quoted_plain_result(
                        "❌ 海啸模拟参数无效，请检查：源震级 应为数字"
                    )
                    return
                params = {
                    "title": arg1 or "海啸警报",
                    "level": arg2 or "警报",
                    "place_name": arg3 or "模拟海域",
                    "magnitude": tsunami_magnitude
                    if tsunami_magnitude is not None
                    else 7.5,
                }
            # 气象：arg1=标题 arg2=正文 arg3=预警编码
            elif disaster_type == DISASTER_TYPE_WEATHER:
                params = {
                    "title": arg1 or "暴雨橙色预警",
                    "headline": arg1 or "暴雨橙色预警",
                    "description": arg2 or "预计未来6小时降雨量将达50毫米以上。",
                    "weather_code": arg3 or "11B0302",
                }
            # 台风：arg1=编号 arg2=名称 arg3=强度
            elif disaster_type == DISASTER_TYPE_TYPHOON:
                params = {
                    "typhoon_id": arg1 or "2501",
                    "name": arg2 or "模拟台风",
                    "name_en": "SIM",
                    "typhoon_type": arg3 or "台风",
                }
            else:
                yield _quoted_plain_result(f"❌ 暂不支持的模拟灾种: {disaster_type}")
                return

            # --- 用 SimulationBuilder 构建合法事件（含模拟标记） ---
            builder = SimulationBuilder()
            step = SimulationStep.create(
                disaster_type=disaster_type,
                source_id=source,
                params=params,
                report_num=1,
            )
            envelope = builder.build_step_envelope(step)

            # --- 统一走完整规则链评估（产出各规则判定报告） ---
            effective_runtime_config = dict(runtime_config)
            effective_runtime_config["__simulation_bypass_regular_filters"] = True
            final_decision = manager.evaluate_push_decision(
                envelope,
                runtime_config=effective_runtime_config,
                session_id=target_session,
                emit_filter_log=False,
                commit_state=False,
            )

            report_lines = [
                f"🧪 灾害预警模拟报告 ({disaster_type})",
                f"Source: {source}",
            ]
            detail_suffix = (
                f"（{final_decision.detail}）" if final_decision.detail else ""
            )
            if final_decision.accepted:
                report_lines.append(f"✅ 规则链: 通过 ({final_decision.reason})")
            else:
                report_lines.append(
                    f"❌ 规则链: 拦截 ({final_decision.reason}{detail_suffix})"
                )

            # --- 通过后走完整推送链路 ---
            if final_decision.accepted:
                push_result = await manager.push_event(
                    envelope,
                    target_sessions=[target_session],
                    session_config_getter=session_config_manager.get_effective_config,
                    commit_state=False,
                    skip_dedup=True,
                    bypass_fusion=True,
                    return_details=True,
                )
                push_success = (
                    bool(push_result.get("success"))
                    if isinstance(push_result, dict)
                    else bool(push_result)
                )
                await self._track_command_feature(
                    "command_simulation_result",
                    {
                        "success": True,
                        "triggered": bool(push_success),
                        "source": str(source or "unknown"),
                        "disaster_type": str(disaster_type),
                    },
                )
                if push_success:
                    report_lines.append(
                        f"\n✅ 正式模拟报文已发送到当前会话: {target_session}"
                    )
                    yield _quoted_plain_result("\n".join(report_lines))
                    return

                failure_reason = ""
                if isinstance(push_result, dict):
                    failure_reason = str(
                        push_result.get("final_failure_reason") or ""
                    ).strip()
                if not failure_reason:
                    failure_reason = final_decision.reason
                report_lines.append(
                    f"\n⛔ 结论: 当前会话发送阶段仍被拦截：{failure_reason}"
                )
                yield _quoted_plain_result("\n".join(report_lines))
                return

            await self._track_command_feature(
                "command_simulation_result",
                {
                    "success": True,
                    "triggered": False,
                    "source": str(source or "unknown"),
                    "disaster_type": str(disaster_type),
                },
            )
            yield _quoted_plain_result("\n".join(report_lines))
        except Exception as e:
            logger.error(f"[灾害预警] 模拟预警失败: {e}\n{traceback.format_exc()}")
            yield _quoted_plain_result(f"❌ 模拟失败: {e}")

    async def handle_query_earthquake_warning_with_timeout(
        self, event, timeout: float = 15.0
    ):
        """带超时保护的地震预警查询。"""
        try:
            async for result in asyncio.wait_for(
                self.handle_query_earthquake_warning(event),
                timeout=timeout,
            ):
                yield result
        except TimeoutError:
            yield quoted_plain_result(self.plugin, event, "❌ 查询超时，请稍后重试")

    async def handle_query_jma_hypo_list(
        self,
        event,
        arg1: str | None = None,
        arg2: str | None = None,
    ):
        """处理 JMA 震央分布文本查询。"""

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        try:
            result = await query_jma_hypo_list(arg1, arg2)
            await self._track_command_feature(
                "command_jma_hypo_list",
                {
                    "success": bool(result.get("success")),
                    "requested_days": int(result.get("requested_days") or 0),
                    "total_events": int((result.get("stats") or {}).get("total") or 0),
                    "covered_days": int(result.get("covered_days") or 0),
                },
            )
            # JMA 震央分布显式走合并转发（震级分布/较大地震/地点统计）
            text = build_jma_hypo_list_text(result)
            ok = await send_forward_blocks(
                self.plugin,
                event,
                [text],
                name="灾害预警",
            )
            if not ok:
                # 合并转发失败时回退为普通引用回复，确保有文本输出
                yield _quoted_plain_result(text)
        except Exception as e:
            logger.error(
                f"[灾害预警] JMA 震央分布查询失败: {e}\n{traceback.format_exc()}"
            )
            yield _quoted_plain_result(f"❌ JMA 震央分布查询失败: {e}")

    async def handle_query_jma_hypo_plot(
        self,
        event,
        arg1: str | None = None,
        arg2: str | None = None,
        arg3: str | None = None,
    ):
        """处理 JMA 震央分布绘图查询。"""

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        try:
            result = await query_jma_hypo_plot(arg1, arg2, arg3)
            caption = build_jma_hypo_plot_caption(result)
            if not result.get("success"):
                await self._track_command_feature(
                    "command_jma_hypo_plot",
                    {
                        "success": False,
                        "mode": str(result.get("mode") or ""),
                    },
                )
                yield _quoted_plain_result(caption)
                return

            # 优先复用 message_manager 的临时目录；否则退到插件目录 temp
            service = getattr(self.plugin, "disaster_service", None)
            message_manager = (
                getattr(service, "message_manager", None) if service else None
            )
            temp_dir = getattr(message_manager, "temp_dir", None)
            if temp_dir is None:
                plugin_root = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
                temp_dir = os.path.join(plugin_root, "temp")
                os.makedirs(temp_dir, exist_ok=True)
            plugin_root = getattr(message_manager, "plugin_root", None)
            if not plugin_root:
                plugin_root = os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )

            img_path = os.path.join(
                str(temp_dir),
                f"jma_hypo_{uuid.uuid4().hex}_{int(time.time())}.png",
            )
            renderer = JmaHypoRenderer(plugin_root=str(plugin_root))
            # PIL 渲染与读盘为 CPU/IO 密集同步操作，放到线程池避免阻塞事件循环
            out = await asyncio.to_thread(
                renderer.render,
                events=list(result.get("events") or []),
                mode=str(result.get("mode") or "经度纬度"),
                output_path=img_path,
                start_date=result.get("start_date"),
                end_date=result.get("end_date"),
                stats=result.get("stats") or {},
            )
            await self._track_command_feature(
                "command_jma_hypo_plot",
                {
                    "success": bool(out),
                    "mode": str(result.get("mode") or ""),
                    "requested_days": int(result.get("requested_days") or 0),
                    "total_events": int((result.get("stats") or {}).get("total") or 0),
                },
            )
            if out and os.path.exists(out):

                def _read_and_cleanup(path: str) -> str:
                    with open(path, "rb") as f:
                        encoded = base64.b64encode(f.read()).decode()
                    try:
                        os.unlink(path)
                    except Exception:
                        pass
                    return encoded

                b64 = await asyncio.to_thread(_read_and_cleanup, out)
                chain_parts = [Comp.Plain(caption), Comp.Image.fromBase64(b64)]
                try:
                    if hasattr(self.plugin, "_with_quote_reply"):
                        yield event.chain_result(
                            self.plugin._with_quote_reply(event, chain_parts)
                        )
                    else:
                        yield event.chain_result(chain_parts)
                    return
                except Exception:
                    yield _quoted_plain_result(caption)
                    try:
                        await self.plugin.context.send_message(
                            event.unified_msg_origin,
                            MessageChain([Comp.Image.fromBase64(b64)]),
                        )
                    except Exception:
                        pass
                    return

            yield _quoted_plain_result(caption + "\n❌ 震央分布图渲染失败")
        except Exception as e:
            logger.error(
                f"[灾害预警] JMA 震央分布绘图失败: {e}\n{traceback.format_exc()}"
            )
            yield _quoted_plain_result(f"❌ JMA 震央分布绘图失败: {e}")

    async def handle_query_snet(self, event, arg: str | None = None):
        """处理 /snet 查询：即时抓取 MSIL 瓦片并渲染测站分布。

        用法：
          /snet
          /snet random
          /snet 7 / 6+ / 6- / 5+ / 5- / 4 / 3 / 2 / 1 / 0
        """

        def _quoted_plain_result(text: str):
            return quoted_plain_result(self.plugin, event, text)

        service = getattr(self.plugin, "disaster_service", None)
        if service is None:
            yield _quoted_plain_result("❌ 灾害预警服务未就绪")
            return

        snet_poll = getattr(service, "snet_poll_service", None)
        if snet_poll is None:
            yield _quoted_plain_result("❌ S-Net 轮询服务未就绪")
            return

        # 全局总闸：全局未启用时不允许 /snet（与轮询启动口径一致）
        # 配置读取异常时 fail-closed，避免 opt-in 开关被静默绕过。
        try:
            if hasattr(snet_poll, "is_enabled") and not snet_poll.is_enabled():
                yield _quoted_plain_result(
                    "❌ S-Net 数据源未在全局配置中启用，无法查询"
                )
                return
        except Exception as exc:
            logger.warning(f"[灾害预警] 检查 S-Net 全局启用状态失败: {exc}")
            yield _quoted_plain_result(
                "❌ 无法确认 S-Net 启用状态，已拒绝查询（请检查全局配置）"
            )
            return

        raw_arg = (arg or "").strip()
        debug_mode = None
        if raw_arg:
            key = raw_arg.lower()
            allowed = {
                "random",
                "7",
                "6+",
                "6-",
                "5+",
                "5-",
                "4",
                "3",
                "2",
                "1",
                "0",
            }
            if key not in allowed:
                yield _quoted_plain_result(
                    "用法：/snet 或 /snet random|7|6+|6-|5+|5-|4|3|2|1|0"
                )
                return
            debug_mode = key

        try:
            result = await snet_poll.fetch_for_query(
                min_shindo=-3.0,
                debug_mode=debug_mode,
            )
            if not result or not result.get("stations"):
                yield _quoted_plain_result("🗺️ 暂无 S-Net 测站数据（瓦片可能延迟）")
                return

            stations = result["stations"]
            timestamp = str(result.get("timestamp") or "")
            # 组装临时 display context 复用 SnetPresenter
            occurred_at = None
            if timestamp:
                try:
                    occurred_at = datetime.strptime(timestamp, "%Y%m%d%H%M00").replace(
                        tzinfo=timezone.utc
                    )
                except (ValueError, TypeError):
                    occurred_at = None

            ctx = EarthquakeDisplayContext(
                event_id=f"snet_query_{timestamp or int(time.time())}",
                source_id="snet_msil",
                title="日本海沟 S-Net 海底观测网",
                occurred_at=occurred_at,
                metadata={
                    "stations": stations,
                    "timestamp": timestamp,
                    "triggered": result.get("triggered") or [],
                },
                options={"timezone": "UTC+8"},
            )
            text = SnetPresenter.format_message(ctx, {"timezone": "UTC+8"})
            if debug_mode:
                text = text.replace(
                    "🚨[S-Net震度分布] NIED",
                    f"🚨[S-Net震度分布] NIED（调试:{debug_mode}）",
                )

            chain_parts: list = [Comp.Plain(text)]
            # 渲染测站图（与推送链路共用 RenderImageCache）
            message_manager = getattr(service, "message_manager", None)
            renderer = (
                getattr(message_manager, "snet_map_renderer", None)
                if message_manager
                else None
            )
            if renderer is not None:
                try:
                    temp_dir = getattr(message_manager, "temp_dir", None)
                    cache_key = MessageBuildService._build_snet_map_cache_key(
                        stations, timestamp
                    )
                    safe_ts = timestamp or str(int(time.time()))
                    img_path = os.path.join(
                        str(temp_dir or "."),
                        f"snet_map_{safe_ts}.png",
                    )

                    async def _render_snet() -> str | None:
                        return await renderer.render(stations, img_path, timestamp)

                    render_with_cache = getattr(
                        message_manager, "_render_with_cache", None
                    )
                    if callable(render_with_cache):
                        out = await render_with_cache(cache_key, _render_snet)
                    else:
                        out = await renderer.render(stations, img_path, timestamp)
                    if out and os.path.exists(out):
                        with open(out, "rb") as f:
                            b64 = base64.b64encode(f.read()).decode()
                        chain_parts.append(Comp.Image.fromBase64(b64))
                        # 缓存命中依赖磁盘文件，查询路径不主动 unlink
                except Exception as e:
                    logger.warning(f"[灾害预警] /snet 测站图渲染失败: {e}")

            # 优先 chain 结果
            try:
                yield event.chain_result(chain_parts)
            except Exception:
                # 回退：先发文本再尝试发图
                yield _quoted_plain_result(text)
                if len(chain_parts) > 1:
                    try:
                        await self.plugin.context.send_message(
                            event.unified_msg_origin,
                            MessageChain([chain_parts[1]]),
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"[灾害预警] /snet 查询失败: {e}\n{traceback.format_exc()}")
            yield _quoted_plain_result(f"❌ S-Net 查询失败: {e}")

    async def handle_query_radar(
        self,
        event,
        name: str | None = None,
    ):
        """处理 /雷达 <名称> 命令：查询最新一帧雷达图。

        支持关键词：区域拼图（全国/华北等）、城市名、省份名、拼音。
        """
        try:
            if not name or not str(name).strip():
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    "用法：/雷达 <雷达名称>\n示例：/雷达 北京、/雷达 全国\n可用站点见 /雷达列表",
                )
                return

            target = resolve_radar_target(str(name).strip())
            if not target.get("matched"):
                reason = target.get("reason", "")
                if reason == "ambiguous":
                    yield quoted_plain_result(
                        self.plugin,
                        event,
                        format_candidates_text(target.get("candidates") or []),
                    )
                else:
                    yield quoted_plain_result(
                        self.plugin,
                        event,
                        f"❌ 未找到匹配的雷达站「{name}」，可用 /雷达列表 查看全部站点。",
                    )
                return

            client = NmcRadarClient()
            try:
                result = await query_radar_image(client=client, target=target)
            finally:
                await client.close()

            if not result.get("success"):
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    f"❌ 雷达图查询失败：{result.get('error', '未知错误')}",
                )
                return

            image_comp = Comp.Image.fromBase64(result["image_base64"])

            await self._track_command_feature(
                "command_query_radar",
                {
                    "success": True,
                    "kind": result.get("kind", "station"),
                },
            )

            # 只发送雷达图片，不附带文字说明
            yield event.chain_result(
                self.plugin._with_quote_reply(
                    event,
                    [image_comp],
                )
            )
        except Exception as e:
            logger.error(f"[灾害预警] /雷达 查询失败: {e}\n{traceback.format_exc()}")
            yield quoted_plain_result(self.plugin, event, f"❌ 雷达图查询失败: {e}")

    async def handle_query_radar_gif(
        self,
        event,
        name: str | None = None,
    ):
        """处理 /雷达动图 <名称> 命令：查询最近多帧并合成循环 GIF。"""
        try:
            if not name or not str(name).strip():
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    "用法：/雷达动图 <雷达名称>\n示例：/雷达动图 北京、/雷达动图 全国",
                )
                return

            target = resolve_radar_target(str(name).strip())
            if not target.get("matched"):
                reason = target.get("reason", "")
                if reason == "ambiguous":
                    yield quoted_plain_result(
                        self.plugin,
                        event,
                        format_candidates_text(target.get("candidates") or []),
                    )
                else:
                    yield quoted_plain_result(
                        self.plugin,
                        event,
                        f"❌ 未找到匹配的雷达站「{name}」，可用 /雷达列表 查看全部站点。",
                    )
                return

            client = NmcRadarClient()
            try:
                result = await query_radar_gif(client=client, target=target)
            finally:
                await client.close()

            if not result.get("success"):
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    f"❌ 雷达动图查询失败：{result.get('error', '未知错误')}",
                )
                return

            image_comp = Comp.Image.fromBase64(result["image_base64"])

            await self._track_command_feature(
                "command_query_radar_gif",
                {
                    "success": True,
                    "kind": result.get("kind", "station"),
                    "frames": result.get("frames"),
                    "degraded": bool(result.get("degraded")),
                },
            )

            # 只发送雷达动图，不附带文字说明
            yield event.chain_result(
                self.plugin._with_quote_reply(
                    event,
                    [image_comp],
                )
            )
        except Exception as e:
            logger.error(
                f"[灾害预警] /雷达动图 查询失败: {e}\n{traceback.format_exc()}"
            )
            yield quoted_plain_result(self.plugin, event, f"❌ 雷达动图查询失败: {e}")

    async def handle_query_radar_list(self, event):
        """处理 /雷达列表 命令：输出全部雷达站点。"""
        try:
            result = await query_radar_list()
            if not result.get("success"):
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    f"❌ 雷达列表查询失败：{result.get('error', '未知错误')}",
                )
                return

            await self._track_command_feature(
                "command_query_radar_list",
                {"success": True},
            )
            # 雷达站点列表显式走合并转发，失败则回退引用回复
            text = result.get("text", "")
            ok = await send_forward_blocks(
                self.plugin,
                event,
                [text],
                name="灾害预警",
            )
            if not ok:
                yield quoted_plain_result(self.plugin, event, text)
        except Exception as e:
            logger.error(
                f"[灾害预警] /雷达列表 查询失败: {e}\n{traceback.format_exc()}"
            )
            yield quoted_plain_result(self.plugin, event, f"❌ 雷达列表查询失败: {e}")

    # ------------------------------------------------------------------
    # 实况排行查询（/气温排行 /降水排行 /风速排行）
    # ------------------------------------------------------------------
    async def handle_query_rank(
        self,
        event,
        rank_keyword: str,
        time_arg: str | None = None,
    ):
        """处理实况排行查询命令（气温/最低气温/降水/风速），支持可选跨度与时次。

        Args:
            event: 消息事件。
            rank_keyword: 排行要素关键词，如「气温」「最低气温」「降水」「风速」。
            time_arg: 可选跨度或时次，如「24小时」「6h 08时」「08日15时」。
        """
        try:
            # 解析排行要素类型
            rank_type = resolve_rank_type(rank_keyword)
            if not rank_type:
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    "用法：/气温排行 [跨度] [时次] | /最低气温排行 [跨度] [时次] | "
                    "/降水排行 [跨度] [时次] | /风速排行 [跨度] [时次]\n"
                    "示例：/气温排行、/降水排行 24小时、/降水排行 6h 昨天20时、/风速排行 昨天15时\n"
                    + TIME_ARG_HELP,
                )
                return

            # 解析「跨度 + 时次」混合参数（如「24小时 08时」）
            hour, time_text = parse_rank_args(time_arg)

            # 解析可选时次
            ymdh = None
            if time_text:
                ymdh = parse_time_arg(time_text)
                if not ymdh:
                    yield quoted_plain_result(
                        self.plugin,
                        event,
                        f"❌ 无法识别时间参数「{time_text}」，可用格式：\n"
                        "· MM月DD日HH时（如 08日15时）\n"
                        "· YYYYMMDDHH（如 2026080815）\n"
                        "· 今天HH时 / 昨天HH时（如 今天15时）",
                    )
                    return

            # 查询排行
            result = await query_rank(rank_type=rank_type, ymdh=ymdh, hour=hour)

            if not result.get("success"):
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    f"❌ 排行查询失败：{result.get('error', '未知错误')}",
                )
                return

            await self._track_command_feature(
                "command_query_rank",
                {
                    "success": True,
                    "rank_type": rank_type,
                    "time_arg": bool(ymdh),
                    # 生效跨度：query_rank 内部已按要素默认值归一化（未指定时=1h）
                    "hour": result.get("hour", hour),
                    "block_count": len(result.get("blocks") or []),
                    "item_count": len(result.get("raw_items") or []),
                },
            )

            # 多时段（如 24h 双时段 08/20）显式走合并转发，每时段一个节点；
            # 失败或单时段回退普通引用回复。
            blocks = result.get("blocks") or []
            if len(blocks) > 1:
                try:
                    ok = await send_forward_blocks(
                        self.plugin,
                        event,
                        blocks,
                        name="灾害预警",
                    )
                    if ok:
                        return
                except Exception as fwd_error:
                    logger.warning(
                        f"[灾害预警] 排行合并转发失败，回退文本: {fwd_error}"
                    )
            yield quoted_plain_result(self.plugin, event, result.get("text", ""))
        except Exception as e:
            logger.error(f"[灾害预警] 排行查询失败: {e}\n{traceback.format_exc()}")
            yield quoted_plain_result(self.plugin, event, f"❌ 排行查询失败: {e}")

    # ------------------------------------------------------------------
    # 气象站实况/历史/列表查询（/实况 /气象站 /气象站历史 /气象站列表）
    # 数据源：NMC 中央气象台（实况+近24h历史+省份城市列表）
    #         FAN Studio（五位站号->站名映射）
    # ------------------------------------------------------------------

    def _get_weather_station_service(self) -> WeatherStationQueryService:
        """获取气象站查询服务实例（懒加载并缓存到插件上，复用会话）。"""
        service = getattr(self.plugin, "_weather_station_query_service", None)
        if service is None:
            service = WeatherStationQueryService()
            self.plugin._weather_station_query_service = service
        return service

    async def handle_query_weather_real(self, event, keyword: str | None = None):
        """处理气象站实况查询命令（/实况 /气象站）。"""
        try:
            if not keyword or not str(keyword).strip():
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    "🌦️ 气象站实况查询\n"
                    "用法：/实况 <站点代码或站名>\n"
                    "示例：/实况 59270、/气象站 怀集、/气象站 广东怀集",
                )
                return

            service = self._get_weather_station_service()
            result = await service.query_real(str(keyword).strip())
            if not result.get("success"):
                await self._track_command_feature(
                    "command_weather_station_real",
                    {"success": False, "error": str(result.get("error", ""))},
                )
                yield quoted_plain_result(
                    self.plugin, event, f"❌ {result.get('error', '查询失败')}"
                )
                return

            await self._track_command_feature(
                "command_weather_station_real", {"success": True}
            )
            yield quoted_plain_result(self.plugin, event, result.get("text", ""))
        except Exception as e:
            logger.error(f"[灾害预警] 气象实况查询失败: {e}\n{traceback.format_exc()}")
            yield quoted_plain_result(self.plugin, event, f"❌ 实况查询失败: {e}")

    async def handle_query_weather_history(
        self, event, keyword: str | None = None, time_arg: str | None = None
    ):
        """处理气象站历史查询命令（/气象站历史 /实况历史）。"""
        try:
            if not keyword or not str(keyword).strip():
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    "🕐 气象站历史查询\n"
                    "用法：/气象站历史 <站点代码或站名> [时次]\n"
                    "示例：/气象站历史 59270、/实况历史 广东怀集 10时\n"
                    "💡 当前仅支持近24小时逐小时数据（数据源限制）",
                )
                return

            service = self._get_weather_station_service()
            result = await service.query_history(
                str(keyword).strip(), time_arg=time_arg
            )
            if not result.get("success"):
                await self._track_command_feature(
                    "command_weather_station_history",
                    {"success": False, "error": str(result.get("error", ""))},
                )
                yield quoted_plain_result(
                    self.plugin, event, f"❌ {result.get('error', '查询失败')}"
                )
                return

            await self._track_command_feature(
                "command_weather_station_history", {"success": True}
            )
            yield quoted_plain_result(self.plugin, event, result.get("text", ""))
        except Exception as e:
            logger.error(
                f"[灾害预警] 气象站历史查询失败: {e}\n{traceback.format_exc()}"
            )
            yield quoted_plain_result(self.plugin, event, f"❌ 气象站历史查询失败: {e}")

    async def handle_query_weather_station_list(
        self, event, province: str | None = None
    ):
        """处理气象站列表查询命令（/气象站列表）。

        无参时返回全国全量（每省一个合并转发节点）；指定省份时返回该省全量。
        长文本统一走合并转发（显示名「灾害预警」）。
        """
        try:
            service = self._get_weather_station_service()
            result = await service.query_list(province)
            if not result.get("success"):
                await self._track_command_feature(
                    "command_weather_station_list",
                    {"success": False, "error": str(result.get("error", ""))},
                )
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    f"❌ {result.get('error', '气象站列表查询失败')}",
                )
                return

            await self._track_command_feature(
                "command_weather_station_list",
                {"success": True, "is_nationwide": bool(result.get("is_nationwide"))},
            )

            blocks = result.get("blocks") or []
            if blocks:
                # 显式合并转发发送，失败则回退文本
                try:
                    ok = await send_forward_blocks(
                        self.plugin,
                        event,
                        blocks,
                        name="灾害预警",
                        quote_first=True,
                    )
                    if ok:
                        return
                except Exception as fwd_error:
                    logger.warning(
                        f"[灾害预警] 气象站列表合并转发失败，回退文本: {fwd_error}"
                    )
            yield quoted_plain_result(self.plugin, event, result.get("text", ""))
        except Exception as e:
            logger.error(
                f"[灾害预警] 气象站列表查询失败: {e}\n{traceback.format_exc()}"
            )
            yield quoted_plain_result(self.plugin, event, f"❌ 气象站列表查询失败: {e}")

    # ------------------------------------------------------------------
    # 空气质量查询（/空气质量 /空气质量排行 /空气质量列表）
    # ------------------------------------------------------------------

    async def handle_query_aqi(
        self, event, keyword: str | None = None, optional_a: str | None = None
    ):
        """处理空气质量查询命令（/空气质量）。

        用法：
          /空气质量 <城市名>        查询指定城市空气质量
          /空气质量 <省份名> [等级]  查询全省各城市空气质量（可按等级过滤）
          /空气质量 全国 [等级]      全国主要城市空气质量概览（可按等级过滤）
        """
        try:
            result = await query_aqi(
                keyword,
                quality_filter=optional_a,
            )
            if not result.get("success"):
                await self._track_command_feature(
                    "command_aqi_query",
                    {"success": False, "error": str(result.get("error", ""))},
                )
                yield quoted_plain_result(
                    self.plugin, event, f"❌ {result.get('error', '空气质量查询失败')}"
                )
                return

            mode = str(result.get("mode") or "help")
            blocks = result.get("blocks") or []
            text = result.get("text") or ""
            await self._track_command_feature(
                "command_aqi_query",
                {
                    "success": True,
                    "query_mode": mode,
                    "result_count": int(result.get("total") or 0),
                    "is_nationwide": mode == "nationwide",
                },
            )

            # 全国概览：走合并转发（每等级一段），摘要文本作为头部节点，失败回退普通文本
            if blocks:
                try:
                    ok = await send_forward_blocks(
                        self.plugin,
                        event,
                        blocks,
                        header=text,
                        name="灾害预警",
                    )
                    if ok:
                        return
                except Exception as fwd_error:
                    logger.warning(
                        f"[灾害预警] 全国空气质量合并转发送失败，回退文本: {fwd_error}"
                    )
            yield quoted_plain_result(self.plugin, event, text)
        except Exception as e:
            logger.error(f"[灾害预警] 空气质量查询失败: {e}\n{traceback.format_exc()}")
            yield quoted_plain_result(self.plugin, event, f"❌ 空气质量查询失败: {e}")

    async def handle_query_aqi_rank(self, event, direction: str | None = None):
        """处理空气质量排行命令（/空气质量排行 [最好|最差]）。

        无参时同时输出最好与最差两个榜单，显式走合并转发；
        指定方向时输出单个榜单，同样走合并转发并带引用回复。
        """
        try:
            result = await query_aqi_rank(direction)
            if not result.get("success"):
                await self._track_command_feature(
                    "command_aqi_rank",
                    {"success": False, "error": str(result.get("error", ""))},
                )
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    f"❌ {result.get('error', '空气质量排行查询失败')}",
                )
                return

            await self._track_command_feature(
                "command_aqi_rank",
                {
                    "success": True,
                    "direction": str(result.get("direction") or "both"),
                    "block_count": len(result.get("blocks") or []),
                },
            )
            blocks = result.get("blocks") or []
            if not blocks:
                yield quoted_plain_result(
                    self.plugin, event, result.get("text") or "暂无排行数据"
                )
                return
            # 显式走合并转发（显示名「灾害预警」），首个节点带引用回复
            ok = await send_forward_blocks(
                self.plugin,
                event,
                blocks,
                name="灾害预警",
                quote_first=True,
            )
            if not ok:
                yield quoted_plain_result(self.plugin, event, "\n\n".join(blocks))
        except Exception as e:
            logger.error(
                f"[灾害预警] 空气质量排行查询失败: {e}\n{traceback.format_exc()}"
            )
            yield quoted_plain_result(
                self.plugin, event, f"❌ 空气质量排行查询失败: {e}"
            )

    async def handle_query_aqi_city_list(self, event, province: str | None = None):
        """处理空气质量城市列表命令（/空气质量列表 [省份]）。"""
        try:
            result = await query_aqi_city_list(province)
            if not result.get("success"):
                await self._track_command_feature(
                    "command_aqi_list",
                    {"success": False, "error": str(result.get("error", ""))},
                )
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    f"❌ {result.get('error', '空气质量列表查询失败')}",
                )
                return

            await self._track_command_feature(
                "command_aqi_list",
                {
                    "success": True,
                    "is_nationwide": bool(result.get("is_nationwide")),
                },
            )
            blocks = result.get("blocks") or []
            if blocks and len(blocks) > 1:
                # 全国列表：按省分组走合并转发，摘要文本作为头部节点
                try:
                    ok = await send_forward_blocks(
                        self.plugin,
                        event,
                        blocks,
                        header=result.get("text", ""),
                        name="灾害预警",
                    )
                    if ok:
                        return
                except Exception as fwd_error:
                    logger.warning(
                        f"[灾害预警] 空气质量城市列表合并转发送失败，回退文本: {fwd_error}"
                    )
            yield quoted_plain_result(self.plugin, event, result.get("text", ""))
        except Exception as e:
            logger.error(
                f"[灾害预警] 空气质量城市列表查询失败: {e}\n{traceback.format_exc()}"
            )
            yield quoted_plain_result(
                self.plugin, event, f"❌ 空气质量城市列表查询失败: {e}"
            )

    async def handle_ground_motion_predict(
        self,
        event,
        lat_str: str | None = None,
        lon_str: str | None = None,
        mag_str: str | None = None,
        depth_str: str | None = None,
        point_lat_str: str | None = None,
        point_lon_str: str | None = None,
    ):
        """处理地震动预测命令（/地震动预测 <震中纬度> <震中经度> <震级> <深度> <预测点纬度> <预测点经度>）。

        六个参数齐全时按手动模式计算；参数不足时尝试从引用消息提取地震参数
        （此时预测点默认为本地配置坐标，未配置则要求手动提供预测点）。
        """
        try:
            # 尝试手动参数：需要全部 6 个
            manual = _try_parse_ground_motion_args(
                lat_str, lon_str, mag_str, depth_str, point_lat_str, point_lon_str
            )
            if manual is not None:
                result = predict_ground_motion(manual)
                yield quoted_plain_result(
                    self.plugin, event, result.format(display_timezone="UTC+8")
                )
                return

            # 参数不足：尝试引用消息提取
            params = await extract_quoted_quake_params(event)
            if params is None or not params.is_valid:
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    "❌ 参数不足。\n"
                    "用法：/地震动预测 <震中纬度> <震中经度> <震级> <震源深度> "
                    "<预测点纬度> <预测点经度>\n"
                    "或引用一条地震消息（自动提取震中参数）。",
                )
                return

            # 引用模式：预测点默认用本地配置坐标
            point_lat, point_lon, _place = self._resolve_local_point(event)
            if point_lat is None or point_lon is None:
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    "❌ 未配置本地坐标，无法确定预测点。请使用：\n"
                    "/地震动预测 <震中纬度> <震中经度> <震级> <震源深度> "
                    "<预测点纬度> <预测点经度>\n"
                    "或 /本地地震动预测 [<本地纬度>] [<本地经度>]，"
                    "或在配置中设置本地经纬度。",
                )
                return
            gm_input = GroundMotionInput(
                lat=params.lat,
                lon=params.lon,
                magnitude=params.magnitude,
                depth_km=params.depth_km if params.depth_km is not None else 10.0,
                point_lat=point_lat,
                point_lon=point_lon,
                occurred_at=params.occurred_at,
            )
            result = predict_ground_motion(gm_input)
            text = result.format(display_timezone="UTC+8")
            if params.place_name:
                text = f"引用消息：{params.place_name}\n" + text
            yield quoted_plain_result(self.plugin, event, text)
        except Exception as e:
            logger.error(f"[灾害预警] 地震动预测失败: {e}\n{traceback.format_exc()}")
            yield quoted_plain_result(self.plugin, event, f"❌ 地震动预测失败: {e}")

    async def handle_local_ground_motion_predict(
        self,
        event,
        lat_str: str | None = None,
        lon_str: str | None = None,
    ):
        """处理本地地震动预测命令（/本地地震动预测 [<本地纬度>] [<本地经度>]）。

        优先用显式传入的本地坐标；未传入时用本地监控配置坐标；
        震中参数来自引用消息（如 bot 推送的地震速报）。
        """
        try:
            params = await extract_quoted_quake_params(event)
            if params is None or not params.is_valid:
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    "❌ 请引用一条包含震中参数的地震速报消息，再使用 /本地地震动预测。",
                )
                return

            # 本地坐标：显式参数优先，其次本地监控配置
            point_lat, point_lon, place = self._resolve_local_point(
                event, lat_str, lon_str
            )
            if point_lat is None or point_lon is None:
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    "❌ 未配置本地坐标。请使用：/本地地震动预测 <本地纬度> <本地经度>"
                    "，或在配置中设置 local_monitoring.latitude/longitude。",
                )
                return

            gm_input = GroundMotionInput(
                lat=params.lat,
                lon=params.lon,
                magnitude=params.magnitude,
                depth_km=params.depth_km if params.depth_km is not None else 10.0,
                point_lat=point_lat,
                point_lon=point_lon,
                occurred_at=params.occurred_at,
            )
            result = predict_ground_motion(gm_input)
            text = result.format(display_timezone="UTC+8")
            yield quoted_plain_result(self.plugin, event, text)
        except Exception as e:
            logger.error(
                f"[灾害预警] 本地地震动预测失败: {e}\n{traceback.format_exc()}"
            )
            yield quoted_plain_result(self.plugin, event, f"❌ 本地地震动预测失败: {e}")

    def _resolve_local_point(
        self,
        event,
        lat_str: str | None = None,
        lon_str: str | None = None,
    ) -> tuple[float | None, float | None, str]:
        """解析本地预测点坐标：显式参数 > 本地监控配置。

        Returns:
            (纬度, 经度, 地点名)；未解析到时为 (None, None, "")。
            解析失败时区分「未配置」与「系统出错」并输出分级日志，
            便于排查配置问题，避免静默失败。
        """
        if lat_str is not None and lon_str is not None:
            try:
                lat = float(lat_str)
                lon = float(lon_str)
                if (
                    math.isfinite(lat)
                    and math.isfinite(lon)
                    and -90.0 <= lat <= 90.0
                    and -180.0 <= lon <= 180.0
                ):
                    return lat, lon, "本地"
            except (TypeError, ValueError):
                logger.warning(
                    f"[灾害预警] 显式本地坐标解析失败: 纬度为 {lat_str!r}, 经度为 {lon_str!r}"
                    "，将回退到本地监控配置"
                )

        # 本地监控配置：优先会话级有效配置，否则回退全局配置
        lm: dict = {}
        service = getattr(self.plugin, "disaster_service", None)
        session_manager = getattr(service, "session_config_manager", None)
        if session_manager is not None and hasattr(
            session_manager, "get_effective_config"
        ):
            try:
                target_session = getattr(event, "unified_msg_origin", None)
                runtime_config = session_manager.get_effective_config(target_session)
                if isinstance(runtime_config, dict):
                    lm = runtime_config.get("local_monitoring", {})
                    if not isinstance(lm, dict):
                        lm = {}
            except Exception as e:
                logger.warning(
                    f"[灾害预警] 获取会话级本地监控配置失败: {e}，将回退到全局配置"
                )
                lm = {}
        if not lm:
            try:
                global_cfg = self.plugin.config.get("local_monitoring", {})
                lm = global_cfg if isinstance(global_cfg, dict) else {}
            except Exception as e:
                logger.warning(f"[灾害预警] 读取全局本地监控配置失败: {e}")
                lm = {}
        if not lm:
            logger.warning("[灾害预警] 未配置本地监控坐标，本地坐标不可用")
            return None, None, ""

        enabled = bool(lm.get("enabled", False))
        lat = lm.get("latitude")
        lon = lm.get("longitude")
        if not enabled:
            logger.debug("[灾害预警] 本地监控未启用，本地坐标不可用")
            return None, None, ""
        if lat is None or lon is None:
            logger.info("[灾害预警] 本地监控配置缺少经度或纬度，本地坐标不可用")
            return None, None, ""
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            logger.warning(
                f"[灾害预警] 本地监控坐标格式错误: 纬度为 {lat!r}, 经度为 {lon!r}"
            )
            return None, None, ""
        if not (
            math.isfinite(lat_f)
            and math.isfinite(lon_f)
            and -90.0 <= lat_f <= 90.0
            and -180.0 <= lon_f <= 180.0
        ):
            logger.warning(
                f"[灾害预警] 本地监控坐标越界或非有限值: 纬度为 {lat_f!r}, 经度为 {lon_f!r}"
            )
            return None, None, ""
        return lat_f, lon_f, str(lm.get("place_name") or "本地")


def _try_parse_ground_motion_args(
    lat_str: str | None,
    lon_str: str | None,
    mag_str: str | None,
    depth_str: str | None,
    point_lat_str: str | None,
    point_lon_str: str | None,
) -> GroundMotionInput | None:
    """解析手动地震动预测参数。

    六个参数齐全且合法时返回 GroundMotionInput；否则返回 None（由调用方
    回退到引用消息模式）。
    """
    values = [
        lat_str,
        lon_str,
        mag_str,
        depth_str,
        point_lat_str,
        point_lon_str,
    ]
    if any(v is None or not str(v).strip() for v in values):
        return None
    try:
        lat = float(str(lat_str).strip())
        lon = float(str(lon_str).strip())
        mag = float(str(mag_str).strip())
        depth = float(str(depth_str).strip())
        p_lat = float(str(point_lat_str).strip())
        p_lon = float(str(point_lon_str).strip())
    except (TypeError, ValueError):
        return None
    # 拒绝 nan/inf 等非有限数值（nan 比较恒 False 会绕过下方范围校验）
    if not all(math.isfinite(v) for v in (lat, lon, mag, depth, p_lat, p_lon)):
        return None
    # 基本范围校验
    if not (-90.0 <= lat <= 90.0 and -90.0 <= p_lat <= 90.0):
        return None
    if not (-180.0 <= lon <= 180.0 and -180.0 <= p_lon <= 180.0):
        return None
    if not (0.0 <= mag <= 12.0):
        return None
    if depth < 0.0:
        return None
    return GroundMotionInput(
        lat=lat,
        lon=lon,
        magnitude=mag,
        depth_km=depth,
        point_lat=p_lat,
        point_lon=p_lon,
    )
