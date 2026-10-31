"""Tests for the extension framework: manifest, i18n, load isolation, polling."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import textwrap
from types import SimpleNamespace

import pytest

# Import the main service first, as the plugin does, to prevent a circular import
importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
PKG = "astrbot_plugin_disaster_warning.core.extensions"
api_mod = importlib.import_module(f"{PKG}.api")
manager_mod = importlib.import_module(f"{PKG}.manager")
manifest_mod = importlib.import_module(f"{PKG}.manifest")
i18n_mod = importlib.import_module(f"{PKG}.i18n")


@pytest.fixture(autouse=True)
def _patch_provider_family(monkeypatch):
    # ProviderFamily.EXTENSION comes from the host. The test does not depend on it.
    monkeypatch.setattr(
        api_mod, "ProviderFamily", SimpleNamespace(EXTENSION="extension")
    )


class FakeRegistry:
    """Fake host registry that records the calls."""

    def __init__(self):
        self.calls = []

    def register_source_entry(self, entry, *, owner):
        """Record the source entry."""
        self.calls.append(("source", entry.source_id, owner))

    def register_parser(self, name, cls, *, owner):
        """Record the parser."""
        self.calls.append(("parser", name, owner))

    def register_text_presenter(self, key, cls, *, owner):
        """Record the presenter."""
        self.calls.append(("presenter", key, owner))

    def register_rule(self, rule, *, after="local_rule", owner):
        """Record the rule."""
        self.calls.append(("rule", after, owner))

    def unregister_source_entries(self, owner):
        """Record the removal of the owner sources."""
        self.calls.append(("unreg_source", owner))

    def unregister_parsers(self, owner):
        """Record the removal of the owner parsers."""
        self.calls.append(("unreg_parser", owner))

    def unregister_presenters(self, owner):
        """Record the removal of the owner presenters."""
        self.calls.append(("unreg_presenter", owner))

    def unregister_rules(self, owner):
        """Record the removal of the owner rules."""
        self.calls.append(("unreg_rule", owner))


class FakeService:
    """Fake disaster service that stores the handled envelopes."""

    def __init__(self, enabled, settings="{}"):
        self.config = {"extensions": {"enabled_ids": enabled, "settings": settings}}
        self.events = []

    async def _handle_disaster_event(self, env):
        if env == "bad":
            raise RuntimeError("handler boom")
        self.events.append(env)


POLLER_EXT = """
import asyncio

def setup(api):
    entry = type("E", (), dict(
        source_id="src_" + api.id, config_group="extensions",
        provider_family="extension"))()
    api.register_source(entry)

    async def fetch(session):
        mode = api.config.get("mode")
        if mode == "raise":
            raise RuntimeError("boom")
        if mode == "hang":
            await asyncio.sleep(30)
        return ["a", "bad", "b"]

    api.register_poller("src_" + api.id, fetch, interval_seconds=0.01, timeout_seconds=0.05)
"""


def make_ext(root, ext_id, code=POLLER_EXT, *, permissions=("network",), **manifest):
    """Write a test extension package to disk."""
    d = root / ext_id
    d.mkdir(parents=True)
    fields = {
        "id": ext_id,
        "name": ext_id.upper(),
        "version": "0.1.0",
        "api_version": 1,
        "permissions": list(permissions),
        **manifest,
    }
    (d / "extension.yaml").write_text(json.dumps(fields), encoding="utf-8")
    (d / "__init__.py").write_text("", encoding="utf-8")
    (d / "extension.py").write_text(textwrap.dedent(code), encoding="utf-8")
    return d


def make_manager(tmp_path, service, registry=None, roots=None, **kw):
    """Return an extension manager with a fake registry and a fake service."""
    return manager_mod.ExtensionManager(
        service,
        registry or FakeRegistry(),
        roots=roots if roots is not None else [tmp_path / "bundled", tmp_path / "user"],
        host_version="1.6.5",
        data_dir=tmp_path / "data",
        **kw,
    )


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(api_mod.ExtensionAPI, "MIN_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(manager_mod.ExtensionManager, "LOG_THROTTLE_SECONDS", 0)


# ---------------- manifest ----------------


@pytest.mark.parametrize(
    "patch,msg",
    [
        ({"id": "Bad-Id"}, "id 非法"),
        ({"id": "other"}, "目录名"),
        ({"api_version": 2}, "api_version"),
        ({"api_version": None}, "api_version"),
        ({"entry": "nocolon"}, "entry"),
        ({"permissions": "network"}, "permissions"),
    ],
)
def test_manifest_errors(tmp_path, patch, msg):
    """Check that a bad manifest raises an error."""
    d = make_ext(tmp_path, "good")
    data = json.loads((d / "extension.yaml").read_text())
    data.update(patch)
    (d / "extension.yaml").write_text(json.dumps(data))
    with pytest.raises(manifest_mod.ManifestError, match=msg):
        manifest_mod.load_manifest(d)


def test_manifest_defaults_and_version_check(tmp_path):
    """Check the manifest defaults and the host version check."""
    d = make_ext(tmp_path, "good", min_host_version="1.7.0")
    m = manifest_mod.load_manifest(d)
    assert m.entry == "extension:setup" and m.permissions == ("network",)
    with pytest.raises(manifest_mod.ManifestError, match="1.7.0"):
        manifest_mod.check_host_version(m, "v1.6.5")
    manifest_mod.check_host_version(m, "v1.10.0")  # Compare numbers, not strings


def test_manifest_missing_file(tmp_path):
    """Check that a missing manifest file raises an error."""
    with pytest.raises(manifest_mod.ManifestError):
        manifest_mod.load_manifest(tmp_path)


# ---------------- i18n ----------------


def test_i18n_fallback_chain(tmp_path):
    """Check the fallback from the extension language to the host language and to the key."""
    loc = tmp_path / "locale"
    loc.mkdir()
    (loc / "zh-CN.json").write_text(json.dumps({"a": "中{x}", "b": "只有中文"}))
    (loc / "en.json").write_text(json.dumps({"a": "en {x}", "bad": "{missing}"}))
    t = i18n_mod.Translator(loc, "en").t
    assert t("a", x=1) == "en 1"  # Target language
    assert t("b") == "只有中文"  # Fall back to the host language
    assert t("zzz") == "zzz"  # Fall back to the key
    assert t("bad") == "{missing}"  # A format failure returns the original text
    assert (
        i18n_mod.Translator(loc, "xx").t("a", x=2) == "中2"
    )  # The language file is missing
    assert i18n_mod.Translator(loc).t("a", x=3) == "中3"  # Default host language


# Load


def test_happy_load_and_status(tmp_path):
    """Check that a valid extension loads and the status shows it."""
    make_ext(tmp_path / "bundled", "good")
    reg = FakeRegistry()
    mgr = make_manager(tmp_path, FakeService(["good"]), reg)
    mgr.load_all()
    (row,) = mgr.status()
    assert row == {
        "id": "good",
        "name": "GOOD",
        "version": "0.1.0",
        "state": "loaded",
        "error": "",
        "sources": ["src_good"],
    }
    # The load removes the registrations of an earlier load first, then registers.
    last_unreg = max(i for i, c in enumerate(reg.calls) if c[0].startswith("unreg"))
    assert reg.calls.index(("source", "src_good", "good")) > last_unreg


def test_not_found_and_disabled_not_loaded(tmp_path):
    """Check that a missing or disabled extension does not load."""
    make_ext(tmp_path / "bundled", "other")
    mgr = make_manager(tmp_path, FakeService(["ghost"]))
    mgr.load_all()
    rows = {row["id"]: row["state"] for row in mgr.status()}
    assert rows == {"ghost": "not_found", "other": "disabled"}
    assert "_dw_ext_other" not in sys.modules


def test_scan_lists_extensions_that_are_not_enabled(tmp_path):
    """Check scan-based discovery: name, version, a bad manifest and two roots."""
    first, second = tmp_path / "first", tmp_path / "second"
    make_ext(first, "good")
    make_ext(first, "idle")
    make_ext(second, "idle")
    (first / "bad").mkdir()
    (first / "bad" / "extension.yaml").write_text("id: wrong\n", "utf-8")
    (first / "no_manifest").mkdir()
    mgr = make_manager(tmp_path, FakeService(["good"]), roots=[first, second])
    mgr.load_all()
    rows = {row["id"]: row for row in mgr.status()}
    assert sorted(rows) == ["bad", "good", "idle"]
    assert rows["good"]["state"] == "loaded"
    assert (rows["idle"]["state"], rows["idle"]["version"]) == ("disabled", "0.1.0")
    assert rows["bad"]["state"] == "disabled" and rows["bad"]["error"]
    assert "_dw_ext_idle" not in sys.modules


def test_scan_accepts_a_missing_root(tmp_path):
    """Check that a root that does not exist gives no error."""
    mgr = make_manager(tmp_path, FakeService([]), roots=[tmp_path / "none"])
    mgr.load_all()
    assert mgr.status() == []


def test_failing_setup_isolated(tmp_path):
    """Check that a failing setup does not stop the other extensions."""
    root = tmp_path / "bundled"
    make_ext(root, "bad", "def setup(api):\n    raise RuntimeError('nope')\n")
    make_ext(root, "good")
    reg = FakeRegistry()
    mgr = make_manager(tmp_path, FakeService(["bad", "good"]), reg)
    mgr.load_all()
    states = {r["id"]: r for r in mgr.status()}
    assert states["bad"]["state"] == "error" and "nope" in states["bad"]["error"]
    assert states["good"]["state"] == "loaded"
    unreg = {c for c in reg.calls if c[0].startswith("unreg")}
    assert {c[0] for c in unreg} == {
        "unreg_source",
        "unreg_parser",
        "unreg_presenter",
        "unreg_rule",
    }
    assert {c[1] for c in unreg} == {"bad", "good"}  # good: only the pre-load cleanup
    good_unreg = [i for i, c in enumerate(reg.calls) if c[:2] == ("unreg_rule", "good")]
    assert reg.calls.index(("source", "src_good", "good")) > max(good_unreg)
    assert "_dw_ext_bad.extension" not in sys.modules


def test_import_error_and_missing_entry(tmp_path):
    """Check the error state for an import error and for a missing entry point."""
    root = tmp_path / "bundled"
    make_ext(root, "imp", "import does_not_exist_xyz\n")
    make_ext(root, "noentry", "x = 1\n")
    mgr = make_manager(tmp_path, FakeService(["imp", "noentry"]))
    mgr.load_all()
    states = {r["id"]: r for r in mgr.status()}
    assert "does_not_exist_xyz" in states["imp"]["error"]
    assert states["noentry"]["state"] == "error"


def test_relative_import_inside_extension(tmp_path):
    """Check that a relative import works inside an extension package."""
    d = make_ext(
        tmp_path / "bundled",
        "rel",
        "from .helper import VALUE\ndef setup(api):\n    api.config['seen'] = VALUE\n",
    )
    (d / "helper.py").write_text("VALUE = 42\n")
    mgr = make_manager(tmp_path, FakeService(["rel"]))
    mgr.load_all()
    assert mgr.status()[0]["state"] == "loaded"
    assert mgr._apis["rel"].config["seen"] == 42


def test_duplicate_id_first_root_wins(tmp_path):
    """Check that the first root wins when two roots have the same extension id."""
    make_ext(tmp_path / "bundled", "dup")
    make_ext(tmp_path / "user", "dup")
    mgr = make_manager(tmp_path, FakeService(["dup"]))
    mgr.load_all()
    rows = mgr.status()
    assert sorted(r["state"] for r in rows) == ["error", "loaded"]
    assert any("重复" in r["error"] for r in rows)


def test_incompatible_host_version(tmp_path):
    """Check that an extension for another host version does not load."""
    make_ext(tmp_path / "bundled", "new", min_host_version="9.0")
    mgr = make_manager(tmp_path, FakeService(["new"]))
    mgr.load_all()
    assert mgr.status()[0]["state"] == "error"


def test_poller_requires_network_permission(tmp_path):
    """Check that register_poller needs the network permission."""
    make_ext(tmp_path / "bundled", "nonet", permissions=())
    reg = FakeRegistry()
    mgr = make_manager(tmp_path, FakeService(["nonet"]), reg)
    mgr.load_all()
    row = mgr.status()[0]
    assert row["state"] == "error" and "network" in row["error"]
    assert ("unreg_source", "nonet") in reg.calls


def test_register_source_validation_and_poller_ownership(tmp_path):
    """Check the source validation and that a poller needs a source of the same extension."""
    code = """
def setup(api):
    bad = type("E", (), dict(source_id="s", config_group="data_sources",
                             provider_family="extension"))()
    try:
        api.register_source(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("config_group not checked")
    try:
        api.register_poller("not_mine", None, interval_seconds=1)
    except ValueError:
        api.config["ok"] = True
"""
    make_ext(tmp_path / "bundled", "val", code)
    mgr = make_manager(tmp_path, FakeService(["val"]))
    mgr.load_all()
    assert mgr.status()[0]["state"] == "loaded"
    assert mgr._apis["val"].config["ok"]


def test_settings_json_and_dict(tmp_path):
    """Check that the settings accept a JSON string and a dictionary."""
    code = "def setup(api):\n    api.logger.info('hi')\n"
    make_ext(tmp_path / "bundled", "cfg", code)
    for settings in ('{"cfg": {"language": "en"}}', {"cfg": {"language": "en"}}):
        mgr = make_manager(tmp_path, FakeService(["cfg"], settings))
        mgr.load_all()
        assert mgr._apis["cfg"].config == {"language": "en"}
    mgr = make_manager(tmp_path, FakeService(["cfg"], "{not json"))
    mgr.load_all()
    assert mgr._apis["cfg"].config == {}


def test_poller_interval_clamped(tmp_path, monkeypatch):
    """Check that the poll interval has a minimum value."""
    monkeypatch.setattr(api_mod.ExtensionAPI, "MIN_INTERVAL_SECONDS", 60)
    make_ext(tmp_path / "bundled", "clamp")
    mgr = make_manager(tmp_path, FakeService(["clamp"]))
    mgr.load_all()
    assert mgr._apis["clamp"].pollers[0].interval_seconds == 60


def test_api_data_dir_created_on_demand(tmp_path):
    """Check that the data directory is created at the first access."""
    make_ext(tmp_path / "bundled", "dd", "def setup(api):\n    pass\n")
    mgr = make_manager(tmp_path, FakeService(["dd"]))
    mgr.load_all()
    target = tmp_path / "data" / "extensions_data" / "dd"
    assert not target.exists()
    assert mgr._apis["dd"].data_dir == target and target.is_dir()


# Polling


async def _wait_for(cond, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "condition timeout"
        await asyncio.sleep(0.01)


async def test_poller_delivers_envelopes_and_survives_handler_error(tmp_path):
    """Check that the poller sends envelopes and continues after a handler error."""
    make_ext(tmp_path / "bundled", "p1")
    svc = FakeService(["p1"])
    mgr = make_manager(tmp_path, svc)
    mgr.load_all()
    await mgr.start()
    try:
        # "bad" raises an error. The items a and b are still delivered.
        # The loop continues to the second round.
        await _wait_for(lambda: svc.events.count("a") >= 2)
        assert "b" in svc.events
    finally:
        await mgr.stop()


@pytest.mark.parametrize("mode", ["raise", "hang"])
async def test_poller_failures_do_not_kill_task(tmp_path, mode):
    """Check that a failed fetch does not stop the polling task."""
    make_ext(tmp_path / "bundled", "p2")
    svc = FakeService(["p2"], {"p2": {"mode": mode}})
    mgr = make_manager(tmp_path, svc)
    mgr.load_all()
    await mgr.start()
    try:
        await asyncio.sleep(0.4)
        (task,) = mgr._tasks
        assert not task.done()
        assert svc.events == []
    finally:
        await mgr.stop()


async def test_backoff_capped(tmp_path, monkeypatch):
    """Check that the retry backoff has a maximum."""
    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(d):
        sleeps.append(d)
        await real_sleep(0)

    monkeypatch.setattr(manager_mod.asyncio, "sleep", fake_sleep)
    make_ext(tmp_path / "bundled", "p3")
    svc = FakeService(["p3"], {"p3": {"mode": "raise"}})
    mgr = make_manager(tmp_path, svc)
    mgr.load_all()
    interval = mgr._apis["p3"].pollers[0].interval_seconds
    await mgr.start()
    await real_sleep(0.1)
    await mgr.stop()
    assert sleeps[:4] == [interval * 2, interval * 4, interval * 8, interval * 10]
    assert max(sleeps) == interval * 10


async def test_stop_cancels_cleanly_and_is_idempotent(tmp_path):
    """Check that stop cancels the tasks and that a second call is safe."""
    make_ext(tmp_path / "bundled", "p4")
    mgr = make_manager(tmp_path, FakeService(["p4"]))
    mgr.load_all()
    await mgr.start()
    tasks = list(mgr._tasks)
    session = mgr._session
    assert tasks and session is not None
    await mgr.stop()
    assert all(t.done() for t in tasks) and session.closed
    assert mgr._tasks == [] and mgr._session is None
    await mgr.stop()


async def test_start_without_pollers_is_noop(tmp_path):
    """Check that start does nothing when no poller is registered."""
    mgr = make_manager(tmp_path, FakeService([]))
    mgr.load_all()
    await mgr.start()
    assert mgr._session is None and mgr.status() == []
    await mgr.stop()


@pytest.mark.parametrize("failure", ["false", "raises"])
async def test_failed_event_is_given_to_the_host_again(tmp_path, failure):
    """Check that an event that the host did not handle stays for the next poll."""
    make_ext(tmp_path / "bundled", "good")

    class Service(FakeService):
        async def _handle_disaster_event(self, env):
            if failure == "raises":
                raise RuntimeError("handler failed")
            return False

    mgr = make_manager(tmp_path, Service(["good"]))
    assert await mgr._deliver(["a", "b"], lambda _msg: None) == ["a", "b"]


async def test_handled_event_is_not_given_again(tmp_path):
    """Check True and None. The host and a test service can return these values."""
    results = iter([True, None])

    class Service(FakeService):
        async def _handle_disaster_event(self, env):
            return next(results)

    mgr = make_manager(tmp_path, Service([]))
    assert await mgr._deliver(["a", "b"], lambda _msg: None) == []


async def test_stop_removes_the_registrations(tmp_path):
    """Check that a stopped extension leaves no registration in the shared host."""
    make_ext(tmp_path / "bundled", "good")
    reg = FakeRegistry()
    mgr = make_manager(tmp_path, FakeService(["good"]), reg)
    mgr.load_all()
    reg.calls.clear()
    await mgr.stop()
    assert any(call[0].startswith("unreg") for call in reg.calls)
    assert mgr.status()[0]["state"] == "loaded"
