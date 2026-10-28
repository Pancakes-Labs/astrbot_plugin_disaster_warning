"""Pin the behaviour of small helpers before and after the ponytail refactor."""

from __future__ import annotations

import importlib

import pytest

PKG = "astrbot_plugin_disaster_warning"
importlib.import_module(f"{PKG}.core.app.disaster_service")


def _mod(name: str):
    return importlib.import_module(f"{PKG}.{name}")


def test_has_connecting_channels():
    """Check the detection of channels that are connecting."""
    cls = _mod("core.services.health.connection_health_service").ConnectionHealthService
    f = cls._has_connecting_channels
    assert f([]) is False
    assert f([{"enabled": True, "connected": True}]) is False
    assert f([{"enabled": False, "connected": False}]) is False
    assert f([{"enabled": True, "connected": False}]) is True
    assert f([{"enabled": 1}, {"enabled": 0, "connected": 0}]) is True


@pytest.mark.parametrize(
    ("area", "kw", "expected"),
    [
        ("北京市", "北京", True),
        ("北京", "北京", True),
        ("阿坝藏族羌族自治州", "阿坝藏族羌族", True),
        ("海东地区", "海东", True),
        ("上海", "海", True),
        ("上海", "广州", False),
        ("", "x", False),
    ],
)
def test_match_area(area, kw, expected):
    """Check the area keyword match of the AQI query service."""
    m = _mod("core.services.query.aqi_query_service")
    assert m._match_area({"Area": area}, kw) is expected


def test_place_counts_order():
    """Check the place counts order. Ties keep the first-seen order."""
    m = _mod("core.services.query.jma_hypo_query_service")
    events = [
        {"mag": 1, "dep": 1, "place_cn": "B"},
        {"mag": 1, "dep": 1, "place_cn": "A"},
        {"mag": 1, "dep": 1, "place_cn": "A"},
        {"mag": 1, "dep": 1},
        {"mag": 1, "dep": 1, "place_cn": ""},
    ]
    stats = m._build_stats(events)
    assert stats["place_counts"] == [
        {"place": "A", "count": 2},
        {"place": "未知地点", "count": 2},
        {"place": "B", "count": 1},
    ] or stats["place_counts"] == [
        {"place": "未知地点", "count": 2},
        {"place": "A", "count": 2},
        {"place": "B", "count": 1},
    ]
    # Ties keep the first-seen order.
    assert [p["place"] for p in stats["place_counts"]][:2] == ["A", "未知地点"]


def test_notification_unread_count():
    """Check the count of unread notifications."""
    m = _mod("core.services.notification.notification_center")
    obj = object.__new__(m.NotificationCenter)
    obj._cache = {
        "items": [{"id": 1}, {"id": 2}, {"id": 3}, {}],
        "read_map": {"1": True, "2": False},
        "last_sync_at": "t",
    }
    meta = obj._build_meta_locked()
    assert meta == {"unread_count": 3, "last_sync_at": "t", "total_count": 4}
    obj._cache = {}
    assert obj._build_meta_locked()["unread_count"] == 0


def test_simulation_step_safe_numbers():
    """Check that a simulation step uses the default value for a bad number."""
    m = _mod("core.services.simulation.flow_models")
    step = m.SimulationStep.from_dict(
        {
            "report_num": "abc",
            "time_offset_seconds": "",
            "event_time_delay_seconds": None,
            "update_time_offset_seconds": "2.5",
            "update_time_delay_seconds": {},
        }
    )
    assert step.report_num == 1
    assert step.time_offset_seconds == 0.0
    assert step.update_time_offset_seconds == 2.5
    assert step.update_time_delay_seconds == 0.0
    assert m.SimulationStep.from_dict({"report_num": "3.9"}).report_num == 3
    assert m.SimulationStep.from_dict({"report_num": ""}).report_num == 1
    with pytest.raises(OverflowError):
        m.SimulationStep.from_dict({"report_num": "inf"})


def test_time_converter_zoneinfo():
    """Check the time conversion with a zoneinfo time zone."""
    mod = _mod("utils.time_converter")
    tc = mod.TimeConverter
    assert tc._get_timezone("Asia/Tokyo").key == "Asia/Tokyo"
    assert tc._get_timezone("UTC-5").utcoffset(None).total_seconds() == -18000
    assert tc._get_timezone("Not/AZone") is mod.TIMEZONES["UTC+8"]
