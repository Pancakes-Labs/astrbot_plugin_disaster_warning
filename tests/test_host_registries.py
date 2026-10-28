"""Tests for the host registries: register and unregister, duplicate checks,
enabled state, weather severity, and presenter dispatch.
"""

from __future__ import annotations

import importlib

import pytest

# Import the main service first, as the plugin does. This prevents a
# circular import in the parser package.
importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")

PKG = "astrbot_plugin_disaster_warning.core"


def _mod(name: str):
    return importlib.import_module(f"{PKG}.{name}")


catalog = _mod("sources.source_catalog")
source_entry = _mod("sources.source_entry")
parser_registry = _mod("parsers.parser_registry")
presenter_registry = _mod("message.presenters.presenter_registry")
rule_chain = _mod("rules.rule_chain")
event_models = _mod("domain.event_models")
event_identity = _mod("domain.event_identity")
base_rule = _mod("rules.base_rule")
rule_result = _mod("rules.rule_result")
source_rule = _mod("rules.source_rule")
weather_rule = _mod("rules.weather_rule")
disaster_service = _mod("app.disaster_service")
weather_aggregation = _mod("message.push.weather_aggregation_service")
query_service = _mod("services.query.source_runtime_query_service")

OWNER = "test_ext"
SOURCE_ID = "test_ext_weather"
PRESENTER_KEY = "test_ext_weather_text"


def _make_entry(source_id: str = SOURCE_ID):
    return source_entry.SourceEntry(
        source_id=source_id,
        source_enum=f"extension_{source_id}",
        source_type=source_entry.SourceType.WEATHER,
        provider_family=source_entry.ProviderFamily.EXTENSION,
        config_group="extensions",
        config_key=source_id,
        parser_name="test_ext_parser",
        presentation_type="weather",
        text_presenter_key=PRESENTER_KEY,
        report_policy="none",
        intensity_mode="none",
        priority=1,
        display_name="测试扩展气象源",
        query_group="weather",
        routing_tags=("test_ext",),
    )


class _Parser:
    def __init__(self, message_logger=None):
        self.message_logger = message_logger


class _Presenter:
    @classmethod
    def present(cls, ctx, options):
        return (
            f"EXT|{ctx.title}|{ctx.headline}|"
            f"{ctx.metadata.get('severity_level')}|{ctx.source_id}"
        )


class _Rule:
    rule_name = "test_ext_rule"

    def evaluate(self, context):
        return rule_result.RuleDecision.accept(reason="ext")


def _snapshot_indexes():
    return {
        name: {k: list(v) for k, v in getattr(catalog, name).items()}
        for name in dir(catalog)
        if name.startswith("SOURCE_IDS_BY_")
        and isinstance(getattr(catalog, name), dict)
    }


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    catalog.unregister_source_entries(OWNER)
    parser_registry.unregister_parsers(OWNER)
    presenter_registry.unregister_presenters(OWNER)
    rule_chain.unregister_rules(OWNER)


def test_source_round_trip_and_duplicate():
    before_ids = set(catalog.SOURCE_CATALOG)
    before_idx = _snapshot_indexes()
    catalog_ref = catalog.SOURCE_CATALOG
    family_index_ref = catalog.SOURCE_IDS_BY_FAMILY

    catalog.register_source_entry(_make_entry(), owner=OWNER)
    assert catalog.SOURCE_CATALOG is catalog_ref
    assert catalog.SOURCE_IDS_BY_FAMILY is family_index_ref
    assert SOURCE_ID in catalog.SOURCE_CATALOG
    assert catalog.get_source_owner(SOURCE_ID) == OWNER
    assert catalog.get_source_owner("cea_fanstudio") is None
    ext_family = source_entry.ProviderFamily.EXTENSION
    assert catalog.get_source_ids_by_family(ext_family) == [SOURCE_ID]
    assert catalog.get_source_ids_by_config_group("extensions") == [SOURCE_ID]
    assert catalog.get_source_ids_by_routing_tag("test_ext") == [SOURCE_ID]
    assert catalog.get_source_ids_by_query_group("weather")[-1] == SOURCE_ID

    with pytest.raises(ValueError):
        catalog.register_source_entry(_make_entry(), owner="other")
    with pytest.raises(ValueError):
        # Do not replace a built-in source
        catalog.register_source_entry(_make_entry("cea_fanstudio"), owner=OWNER)

    assert catalog.unregister_source_entries(OWNER) == [SOURCE_ID]
    assert set(catalog.SOURCE_CATALOG) == before_ids
    assert _snapshot_indexes() == before_idx
    assert catalog.unregister_source_entries(OWNER) == []


def test_parser_round_trip():
    parser_registry.register_parser("test_ext_parser", _Parser, owner=OWNER)
    catalog.register_source_entry(_make_entry(), owner=OWNER)
    assert parser_registry.resolve_parser_class("test_ext_parser") is _Parser
    assert isinstance(
        parser_registry.create_parser_for_source(SOURCE_ID, None), _Parser
    )
    parser_registry.validate_catalog_parser_names()
    with pytest.raises(ValueError):
        parser_registry.register_parser("china_eew_parser", _Parser, owner=OWNER)
    parser_registry.unregister_parsers(OWNER)
    assert parser_registry.resolve_parser_class("test_ext_parser") is None
    assert "china_eew_parser" in parser_registry.PARSER_CLASS_BY_NAME


def test_presenter_round_trip():
    builtin = set(presenter_registry.get_text_presenter_keys())
    presenter_registry.register_text_presenter(PRESENTER_KEY, _Presenter, owner=OWNER)
    assert PRESENTER_KEY in presenter_registry.get_text_presenter_keys()
    assert presenter_registry.get_presenter_by_text_key(PRESENTER_KEY) is _Presenter
    with pytest.raises(ValueError):
        presenter_registry.register_text_presenter(
            PRESENTER_KEY, _Presenter, owner=OWNER
        )
    presenter_registry.unregister_presenters(OWNER)
    assert presenter_registry.get_text_presenter_keys() == builtin
    assert presenter_registry.get_presenter_by_text_key(PRESENTER_KEY) is None


def test_rule_round_trip_and_position():
    base = [r.rule_name for r in rule_chain.build_default_rule_chain().rules]
    rule_chain.register_rule(_Rule(), owner=OWNER)
    names = [r.rule_name for r in rule_chain.build_default_rule_chain().rules]
    assert names == [*base, "test_ext_rule"]  # local_rule is the last built-in rule
    rule_chain.unregister_rules(OWNER)
    rule_chain.register_rule(_Rule(), after="source_rule", owner=OWNER)
    names = [r.rule_name for r in rule_chain.build_default_rule_chain().rules]
    assert names.index("test_ext_rule") == names.index("source_rule") + 1
    rule_chain.unregister_rules(OWNER)
    rule_chain.register_rule(_Rule(), after="no_such_rule", owner=OWNER)
    assert rule_chain.build_default_rule_chain().rules[-1].rule_name == "test_ext_rule"
    rule_chain.unregister_rules(OWNER)
    assert [r.rule_name for r in rule_chain.build_default_rule_chain().rules] == base


def _weather_envelope(title="暴雨", metadata=None, source_id=SOURCE_ID):
    ident = event_identity.EventIdentity(
        event_id="ext-1", source_id=source_id, event_type="weather_alarm"
    )
    event = event_models.WeatherEvent(
        title=title, headline="头条", metadata=dict(metadata or {})
    )
    return event_models.EventEnvelope(identity=ident, event=event)


def test_source_enabled_rule_accepts_extension_source():
    catalog.register_source_entry(_make_entry(), owner=OWNER)
    ctx = base_rule.RuleContext(
        event=_weather_envelope(),
        runtime_config={"data_sources": {}},
        policy_state={"global_data_sources": {}},
    )
    assert source_rule.SourceEnabledRule().evaluate(ctx).accepted
    assert disaster_service._is_source_enabled_by_catalog(SOURCE_ID, {})
    qs = query_service.SourceRuntimeQueryService({})
    assert qs.is_source_enabled(SOURCE_ID)
    assert qs.is_group_enabled(SOURCE_ID)
    assert qs.is_source_active(SOURCE_ID)
    # After unregister, the rule rejects the source as not registered
    catalog.unregister_source_entries(OWNER)
    assert not source_rule.SourceEnabledRule().evaluate(ctx).accepted
    assert not disaster_service._is_source_enabled_by_catalog(SOURCE_ID, {})


@pytest.mark.parametrize(
    ("title", "level", "min_color", "accepted"),
    [
        (
            "红色暴雨预警",
            1,
            "黄色",
            False,
        ),  # severity_level has priority over the title color word
        ("蓝色暴雨预警", 3, "橙色", True),
        ("暴雨", 2, "橙色", False),
        ("暴雨", 4, "红色", True),
        (
            "红色暴雨预警",
            None,
            "黄色",
            True,
        ),  # Without severity_level, use the title color word
        ("蓝色暴雨预警", None, "黄色", False),
    ],
)
def test_weather_rule_uses_severity_level(title, level, min_color, accepted):
    meta = {} if level is None else {"severity_level": level}
    ctx = base_rule.RuleContext(
        event=_weather_envelope(title=title, metadata=meta),
        runtime_config={},
        policy_state={
            "weather_filter": {"enabled": True, "min_color_level": min_color}
        },
    )
    assert weather_rule.WeatherRule().evaluate(ctx).accepted is accepted


def test_weather_aggregation_uses_severity_level():
    cls = weather_aggregation.WeatherAggregationService
    svc = cls.__new__(cls)
    env = _weather_envelope(title="蓝色暴雨预警", metadata={"severity_level": 3})
    assert svc._resolve_color_level(env) == (3, "橙色")
    env = _weather_envelope(title="蓝色暴雨预警")
    assert svc._resolve_color_level(env) == (1, "蓝色")


def test_present_message_uses_registered_presenter():
    catalog.register_source_entry(_make_entry(), owner=OWNER)
    presenter_registry.register_text_presenter(PRESENTER_KEY, _Presenter, owner=OWNER)
    env = _weather_envelope(title="Alert", metadata={"severity_level": 2})
    text = presenter_registry.present_message(env, SOURCE_ID)
    assert text == "EXT|Alert|头条|2|test_ext_weather"


def test_format_source_name_for_extension_source():
    storage = _mod("storage.source_compat")
    assert storage.format_source_name(SOURCE_ID) == SOURCE_ID
    catalog.register_source_entry(_make_entry(), owner=OWNER)
    assert storage.format_source_name(SOURCE_ID) == "测试扩展气象源"
