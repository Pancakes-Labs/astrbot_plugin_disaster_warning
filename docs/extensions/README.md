# Writing an extension

Extensions add data sources, presenters and rules on top of this plugin without forking it.
The binding contract is [`design.md`](design.md). The reference implementation is
[`extensions/br_rs`](../../extensions/br_rs) (Brazil — Rio Grande do Sul).

## 1. Enable one

In the plugin config (WebUI → 🧩 扩展, key `extensions`):

```json
"extensions": {
  "enabled_ids": ["br_rs"],
  "settings": "{\"br_rs\": {\"language\": \"pt-BR\", \"alerts_interval\": 300}}"
}
```

Reload the plugin, then run `/灾害预警扩展` to see each extension's state (🟢 loaded,
🔴 error with the message, ⚪ not found). A broken extension never stops the host.

## 2. Minimal extension

```
<data_dir>/extensions/my_ext/      # or <plugin_dir>/extensions/my_ext/ for bundled ones
  extension.yaml
  __init__.py                      # may be empty
  extension.py
  locale/zh-CN.json                # host language: must contain every key
  locale/en.json
```

```yaml
# extension.yaml
id: my_ext
name: My extension
version: 0.1.0
api_version: 1
min_host_version: 1.6.5
permissions: [network]
```

```python
# extension.py — never import host modules; use api.models
def setup(api):
    m = api.models
    api.register_source(m.SourceEntry(
        source_id="my_ext_rain", source_enum="my_ext_rain",
        source_type=m.SourceType.WEATHER, provider_family=m.ProviderFamily.EXTENSION,
        config_group="extensions", config_key="my_ext_rain",
        parser_name="", presentation_type="weather", text_presenter_key="my_ext",
        report_policy="none", intensity_mode="none", priority=0,
        display_name=api.t("source.name"),
    ))

    class Presenter:
        @classmethod
        def present(cls, ctx, options=None):
            return f"{ctx.title}\n{ctx.headline}"

    api.register_presenter("my_ext", Presenter)

    async def fetch(session):  # aiohttp.ClientSession
        async with session.get("https://example.org/alerts.json") as resp:
            data = await resp.json()
        return [build_envelope(api, item) for item in data]  # list[EventEnvelope]

    api.register_poller("my_ext_rain", fetch, interval_seconds=300)
```

Build envelopes like `extensions/br_rs/extension.py::_envelope`:

- `EventIdentity` with `event_type="weather_alarm"` and `provider_family="extension"`;
- a `WeatherEvent` whose `metadata["severity_level"]` is 0–4 (white, blue, yellow, orange, red), which the host uses for filtering and aggregation.

## 3. Things the host does for you

- **Rule chain:**
  - time window: events older than 1 h are dropped, so use the *last update* time;
  - minimum colour level, from `severity_level`;
  - keyword filters.
- **Push:** session targeting and weather aggregation.
- **Reliability:**
  - a shared HTTP session;
  - a timeout on every fetch;
  - exponential backoff on failures;
  - throttled error logs.
- **i18n:** `api.t(key, **fmt)` looks in `locale/<language>.json`, then `locale/zh-CN.json`, then falls back to the key itself.

## 4. Testing

`tests/conftest.py` stubs AstrBot, so tests run without it:

```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

See `tests/br_rs/` for testing parsers on recorded fixtures with a fake `api`. See
`tests/test_extension_integration.py` for loading an extension through the real host.
