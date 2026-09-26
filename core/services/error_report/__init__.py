"""
错误报告子系统导出。
统一导出错误报告服务的装配、挂钩与关闭入口。
"""

from .error_report_service import (
    ErrorReportService,
    close_error_report_service,
    configure_error_report_service,
    get_error_report_service,
    report_error_safely,
)

__all__ = [
    "ErrorReportService",
    "close_error_report_service",
    "configure_error_report_service",
    "get_error_report_service",
    "report_error_safely",
]
