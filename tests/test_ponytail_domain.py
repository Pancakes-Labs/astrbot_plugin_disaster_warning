"""Pin the behaviour of the tsunami text cleaner in the domain package."""

from __future__ import annotations

import importlib

import pytest

importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
_BASE = "astrbot_plugin_disaster_warning.core.domain.tsunami."


@pytest.mark.parametrize("module", ["tsunami_levels", "jma_tsunami_normalize"])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("  ", ""),
        ("null", ""),
        (" NONE ", ""),
        ("unknown", "unknown"),
        (0, "0"),
        (" a b ", "a b"),
    ],
)
def test_clean_text(module, raw, expected):
    """Check that both modules trim the text and change None, empty, null and none values to an empty string."""
    mod = importlib.import_module(_BASE + module)
    assert mod._clean_text(raw) == expected
