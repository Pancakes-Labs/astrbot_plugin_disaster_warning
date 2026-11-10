"""
解析器子系统。
统一导出解析器基类、解析器创建入口与目录一致性校验能力。
"""

# 从当前包中导出基础解析器与注册表函数
from .base_parser import BaseParser
from .parser_registry import (
    create_parser_for_source,
    resolve_parser_class,
    validate_catalog_parser_names,
)

__all__ = [
    "BaseParser",
    "create_parser_for_source",
    "resolve_parser_class",
    "validate_catalog_parser_names",
]
