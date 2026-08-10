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
from ...core.services.query.jma_hypo_query_presenter import (
    build_jma_hypo_list_text,
    build_jma_hypo_plot_caption,
)
from ...core.services.query.jma_hypo_query_service import (
    query_jma_hypo_list,
    query_jma_hypo_plot,
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
from ...core.services.simulation.simulation_service import build_earthquake_simulation
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

            from ...core.domain.earthquake.cmt_normalize import (
                classify_fault_mechanism,
                format_fault_type_label,
            )

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
        lat: float,
        lon: float,
        magnitude: float,
        depth: float,
        source: str = "cea_fanstudio",
    ):
        """处理虚拟地震模拟命令，构建事件包并运行规则评估与渲染效果测试。"""

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
            simulation_result = build_earthquake_simulation(
                manager,
                lat=lat,
                lon=lon,
                magnitude=magnitude,
                depth=depth,
                source=source,
                runtime_config=runtime_config,
            )

            # 模拟时评估过滤决策
            if simulation_result.global_pass and simulation_result.local_pass:
                push_result = await manager.push_event(
                    simulation_result.disaster_event,
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
                        "magnitude_bucket": round(magnitude),
                        "depth_bucket": int(depth // 10 * 10),
                    },
                )
                if push_success:
                    simulation_result.report_lines.append(
                        f"\n✅ 正式模拟报文已发送到当前会话: {target_session}"
                    )
                    yield _quoted_plain_result(
                        "\n".join(simulation_result.report_lines)
                    )
                    return

                failure_reason = ""
                if isinstance(push_result, dict):
                    failure_reason = str(
                        push_result.get("final_failure_reason") or ""
                    ).strip()
                if not failure_reason:
                    effective_runtime_config = dict(runtime_config)
                    # 模拟绕过去重标志
                    effective_runtime_config["__simulation_bypass_regular_filters"] = (
                        True
                    )
                    final_decision = manager.evaluate_push_decision(
                        simulation_result.disaster_event,
                        runtime_config=effective_runtime_config,
                        session_id=target_session,
                        emit_filter_log=False,
                        commit_state=False,
                    )
                    detail_suffix = (
                        f"（{final_decision.detail}）" if final_decision.detail else ""
                    )
                    failure_reason = f"{final_decision.reason}{detail_suffix}"
                simulation_result.report_lines.append(
                    f"\n⛔ 结论: 当前会话发送阶段仍被拦截：{failure_reason}"
                )
                yield _quoted_plain_result("\n".join(simulation_result.report_lines))
                return

            await self._track_command_feature(
                "command_simulation_result",
                {
                    "success": True,
                    "triggered": False,
                    "source": str(source or "unknown"),
                    "magnitude_bucket": round(magnitude),
                    "depth_bucket": int(depth // 10 * 10),
                },
            )
            yield _quoted_plain_result("\n".join(simulation_result.report_lines))
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
        """处理实况排行查询命令（气温/最低气温/降水/风速），支持可选历史时次。

        Args:
            event: 消息事件。
            rank_keyword: 排行要素关键词，如「气温」「最低气温」「降水」「风速」。
            time_arg: 可选历史时次，如「08日15时」「2026080815」「今天15时」。
        """
        try:
            # 解析排行要素类型
            rank_type = resolve_rank_type(rank_keyword)
            if not rank_type:
                yield quoted_plain_result(
                    self.plugin,
                    event,
                    "用法：/气温排行 [时次] | /最低气温排行 [时次] | /降水排行 [时次] | /风速排行 [时次]\n"
                    "示例：/气温排行、/最低气温排行、/降水排行 08日15时、/风速排行 2026080815\n"
                    + TIME_ARG_HELP,
                )
                return

            # 解析可选时次
            ymdh = None
            if time_arg and str(time_arg).strip():
                ymdh = parse_time_arg(str(time_arg).strip())
                if not ymdh:
                    yield quoted_plain_result(
                        self.plugin,
                        event,
                        f"❌ 无法识别时间参数「{time_arg}」，可用格式：\n"
                        "· MM月DD日HH时（如 08日15时）\n"
                        "· YYYYMMDDHH（如 2026080815）\n"
                        "· 今天HH时 / 昨天HH时（如 今天15时）",
                    )
                    return

            # 查询排行
            result = await query_rank(rank_type=rank_type, ymdh=ymdh)

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
                    "item_count": len(result.get("raw_items") or []),
                },
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
