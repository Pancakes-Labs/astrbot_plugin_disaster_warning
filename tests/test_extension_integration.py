"""集成测试：经真实宿主注册表加载 br_rs，并让真实 CEMADEN 告警走完规则链与展示器。"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "br_rs" / "fixtures"
PLUGIN_DIR = Path(__file__).resolve().parent.parent


class _FakeResponse:
    def __init__(self, text: str):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def text(self):
        return self._text


class _FakeSession:
    """按 URL 关键字返回本地夹具，避免测试访问网络。"""

    def get(self, url: str):
        if "wsAlertas2" in url:
            # 夹具录制于 2026-10-28；把更新时间改为当前巴西利亚时间，模拟刚更新的告警
            data = json.loads((FIXTURES / "cemaden_alertas.json").read_text("utf-8"))
            now = datetime.now(timezone(timedelta(hours=-3)))
            for alert in data["alertas"]:
                alert["ult_atualizacao"] = now.strftime("%Y-%m-%d %H:%M:%S.000")
            return _FakeResponse(json.dumps(data))
        if "oni.ascii" in url:
            return _FakeResponse((FIXTURES / "oni.ascii.txt").read_text("utf-8"))
        return _FakeResponse((FIXTURES / "cemaden_rain_RS.json").read_text("utf-8"))


class _FakeService:
    def __init__(self, config):
        self.config = config
        self.handled = []

    async def _handle_disaster_event(self, envelope):
        self.handled.append(envelope)


@pytest.fixture
def loaded_br_rs(tmp_path):
    importlib.import_module("astrbot_plugin_disaster_warning.core.app.disaster_service")
    from astrbot_plugin_disaster_warning.core.extensions.host_registry import (
        HostRegistry,
    )
    from astrbot_plugin_disaster_warning.core.extensions.manager import (
        ExtensionManager,
    )

    service = _FakeService(
        {
            "extensions": {
                "enabled_ids": ["br_rs"],
                "settings": json.dumps({"br_rs": {"language": "pt-BR"}}),
            }
        }
    )
    manager = ExtensionManager(
        service,
        HostRegistry(),
        roots=[PLUGIN_DIR / "extensions"],
        data_dir=tmp_path,
    )
    manager.load_all()
    yield manager
    HostRegistry().unregister_source_entries(owner="br_rs")
    HostRegistry().unregister_presenters(owner="br_rs")


def test_br_rs_loads_through_real_host(loaded_br_rs):
    from astrbot_plugin_disaster_warning.core.sources.source_catalog import (
        SOURCE_CATALOG,
        get_source_owner,
    )

    (record,) = loaded_br_rs.status()
    assert record["state"] == "loaded", record["error"]
    assert set(record["sources"]) == {"br_rs_cemaden_alerts", "br_rs_cemaden_rain"}
    for source_id in record["sources"]:
        assert source_id in SOURCE_CATALOG
        assert get_source_owner(source_id) == "br_rs"


async def test_cemaden_alert_passes_rule_chain_and_renders(loaded_br_rs):
    from astrbot_plugin_disaster_warning.core.message.presenters.presenter_registry import (
        present_message,
    )
    from astrbot_plugin_disaster_warning.core.message.push.push_policy import (
        evaluate_push_decision_with_components,
    )

    api = loaded_br_rs._apis["br_rs"]
    poller = next(p for p in api.pollers if p.source_id == "br_rs_cemaden_alerts")
    envelopes = await poller.fetch(_FakeSession())

    # 夹具中 RS 有 3 条告警
    assert len(envelopes) == 3
    envelope = envelopes[0]
    assert envelope.event.metadata["uf"] == "RS"
    assert envelope.event.metadata["enso"]["phase"] == "el_nino"

    runtime_config = {"weather_config": {"weather_filter": {"enabled": True}}}
    decision = evaluate_push_decision_with_components(
        envelope, runtime_config=runtime_config, policy_state={}
    )
    assert decision.accepted, decision.reason

    text = present_message(envelope, envelope.source_id)
    assert "CEMADEN" in text
    assert envelope.event.metadata["municipio"].title() in text.title()
