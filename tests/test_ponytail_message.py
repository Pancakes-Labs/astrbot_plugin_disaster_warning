"""Pin message-layer behaviour that a refactor must keep."""

from __future__ import annotations

import importlib

PKG = "astrbot_plugin_disaster_warning.core.message."


def _store():
    importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
    return importlib.import_module(PKG + "runtime.fusion_state_store").FusionStateStore


def test_cenc_profile_from_dict():
    """Check that the CENC profile is built from a dictionary."""
    store = _store()
    profile = store.build_cenc_similarity_profile(
        {
            "time": "2025-01-01 10:00:00",
            "latitude": " 30.5 ",
            "longitude": 100,
            "magnitude": "x",
        }
    )
    assert profile["occurred_at"] is not None
    assert profile["latitude"] == 30.5
    assert profile["longitude"] == 100.0
    assert profile["magnitude"] is None


def test_cenc_profile_compatibility():
    """Check the compatibility rule between two CENC profiles."""
    store = _store()
    base = store.build_cenc_similarity_profile(
        {
            "occurred_at": "2025-01-01 10:00:00",
            "latitude": 30,
            "longitude": 100,
            "magnitude": 5.0,
        }
    )
    near = dict(base, latitude=30.5, magnitude="5.4")
    far = dict(base, latitude=31.0)
    no_time = dict(base, occurred_at=None)
    assert store._is_cenc_profile_compatible(base, near) is True
    assert store._is_cenc_profile_compatible(base, far) is False
    assert store._is_cenc_profile_compatible(base, no_time) is False
