"""
宿主注册表适配器：把扩展的注册/注销请求转发到宿主的注册函数。

宿主函数在方法内延迟导入，避免本模块在宿主注册表未就绪时无法导入。
"""

from __future__ import annotations


class HostRegistry:
    """生产环境使用的 host_registry；测试可注入同名方法的假对象。"""

    def register_source_entry(self, entry, *, owner: str) -> None:
        from ..sources.source_catalog import register_source_entry

        register_source_entry(entry, owner=owner)

    def register_parser(self, parser_name: str, parser_class, *, owner: str) -> None:
        from ..parsers.parser_registry import register_parser

        register_parser(parser_name, parser_class, owner=owner)

    def register_text_presenter(self, key: str, presenter_class, *, owner: str) -> None:
        from ..message.presenters.presenter_registry import register_text_presenter

        register_text_presenter(key, presenter_class, owner=owner)

    def register_rule(self, rule, *, after: str = "local_rule", owner: str) -> None:
        from ..rules.rule_chain import register_rule

        register_rule(rule, after=after, owner=owner)

    def unregister_source_entries(self, owner: str):
        from ..sources.source_catalog import unregister_source_entries

        return unregister_source_entries(owner)

    def unregister_parsers(self, owner: str) -> None:
        from ..parsers.parser_registry import unregister_parsers

        unregister_parsers(owner)

    def unregister_presenters(self, owner: str) -> None:
        from ..message.presenters.presenter_registry import unregister_presenters

        unregister_presenters(owner)

    def unregister_rules(self, owner: str) -> None:
        from ..rules.rule_chain import unregister_rules

        unregister_rules(owner)
