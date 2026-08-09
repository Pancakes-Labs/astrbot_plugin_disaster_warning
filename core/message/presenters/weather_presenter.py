"""
气象展示器。

该模块负责把气象展示上下文整理成简洁的文本预警消息，
重点处理天气类型图标、颜色等级标记、描述截断和生效时间展示。
"""

from __future__ import annotations

from ....utils.time_converter import TimeConverter
from ...domain.event_context import WeatherDisplayContext
from .base_presenter import BasePresenter
from .weather_constants import (
    DEFAULT_MAX_DESCRIPTION_LENGTH,
    resolve_weather_color_emoji,
    resolve_weather_emoji,
)


class WeatherAlertPresenter(BasePresenter):
    """气象预警文本展示器。"""

    presenter_name = "weather_alert_presenter"

    @classmethod
    def present(
        cls,
        display_context: WeatherDisplayContext,
        options: dict | None = None,
    ) -> str:
        """把气象展示上下文整理为最终文本。"""
        merged_options = dict(display_context.options or {})
        if options:
            merged_options.update(options)

        title = (
            display_context.title
            or getattr(display_context.display_model, "title", None)
            or ""
        )
        headline = display_context.headline or ""
        description = (
            display_context.description
            or getattr(display_context.display_model, "summary", None)
            or ""
        )
        issue_time = display_context.effective_at
        if not (title or headline or description or issue_time):
            return "🚨[气象预警] 数据类型错误"

        # 天气类型可能出现在显式字段、元数据或标题文本中，因此统一汇总后匹配图标。
        match_candidates = [
            display_context.weather_type,
            display_context.metadata.get("weather_type")
            if isinstance(display_context.metadata, dict)
            else "",
            display_context.metadata.get("type")
            if isinstance(display_context.metadata, dict)
            else "",
            title,
            headline,
        ]
        emoji = resolve_weather_emoji(*match_candidates)

        # 颜色等级有时直接体现在标题或颜色字段中，这里统一补上颜色提示图标。
        # 对于"升级为/降级为"类预警，取变更后的最终颜色，避免显示变更前的旧颜色。
        color_emoji = resolve_weather_color_emoji(
            display_context.severity_color, title, headline
        )

        lines = [f"{emoji}[气象预警] 中国气象局"]
        if title:
            lines.append(f"📋{title}{color_emoji}")
        elif headline:
            lines.append(f"📋{headline}{color_emoji}")

        if headline and headline != title:
            lines.append(f"🏷️副标题：{headline}")

        if description:
            desc = description
            max_len = merged_options.get(
                "max_description_length",
                DEFAULT_MAX_DESCRIPTION_LENGTH,
            )
            # 气象正文往往很长，因此在文本模式下按配置截断，避免刷屏。
            if max_len > 0 and len(desc) > max_len:
                desc = desc[: max_len - 3] + "..."
            lines.append(f"📝{desc}")

        if issue_time:
            timezone = merged_options.get("timezone", "UTC+8")
            lines.append(
                f"⏰生效时间：{TimeConverter.format_time(issue_time, timezone)}"
            )

        return "\n".join(lines)
