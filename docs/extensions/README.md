# How to write an extension

An extension adds data sources, presenters and rules to this plugin. You do not need a fork.
The contract is in [`design.md`](design.md).

This repository has the extension system, but no business extension. Each extension is in a separate
repository, and its author maintains it.

## Community extensions

| Extension | Coverage | Author |
| :--- | :--- | :--- |
| [`br_south_disaster`](https://github.com/h0ffmann/astrbot_dw_ext_br_south_disaster) | CEMADEN rain, flood and landslide alerts for the South of Brazil | [@h0ffmann](https://github.com/h0ffmann) |
| [`br_south_waterq`](https://github.com/h0ffmann/astrbot_dw_ext_br_south_waterq) | Bathing water quality alerts for the South of Brazil | [@h0ffmann](https://github.com/h0ffmann) |

These two repositories are also complete examples. This list is not a security review. An extension
runs as Python code in the same process as the plugin.

## 1. Enable an extension

Open the plugin configuration (WebUI → 🧩 扩展, key `extensions`). Set these values:

```json
"extensions": {
  "enabled_ids": ["my_ext"],
  "settings": "{\"my_ext\": {\"language\": \"en\"}}"
}
```

Reload the plugin. Then send `/灾害预警扩展`. The command shows the state of each extension:

- 🟢: the extension is loaded.
- 🔴: an error occurred. The command shows the error message.
- ⚪: the host did not find the extension.

A broken extension does not stop the host.

## 2. A minimal extension

```
<data_dir>/extensions/my_ext/      # or <plugin_dir>/extensions/my_ext/ for a bundled extension
  extension.yaml
  __init__.py                      # this file can be empty
  extension.py
  locale/zh-CN.json                # the host language; it must contain all keys
  locale/en.json
  locale/<lang>.json               # more languages, for example pt-BR.json and ja-JP.json
  meta.json                        # optional: maintainer, coverage, data sources and icon credit
  extension.png                    # optional: the icon; give its license in meta.json
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
# extension.py. Do not import host modules. Use api.models.
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

Make the envelopes in the same way as [`envelopes.py`](https://github.com/h0ffmann/astrbot_dw_ext_br_south_disaster/blob/main/envelopes.py) of `br_south_disaster`:

- Use an `EventIdentity` with `event_type="weather_alarm"` and `provider_family="extension"`.
- Use a `WeatherEvent`. Set `metadata["severity_level"]` to a value from 0 to 4
  (white, blue, yellow, orange, red). The host uses this value to filter and to aggregate.

## 3. Functions that the host does for you

- The rule chain:
  - The time window. The host ignores events that are older than one hour.
    Thus, use the time of the *last update* as the event time.
  - The minimum color level. The host uses `severity_level`.
  - The keyword filters.
- Push: session targets and weather aggregation.
- Reliability:
  - One shared HTTP session.
  - A timeout for each fetch.
  - A longer wait after each failure (exponential backoff).
  - Not more than one error log message in each period.
- Translation: `api.t(key, **fmt)` looks in `locale/<language>.json`, then in `locale/zh-CN.json`.
  If `api.t` does not find the key, it gives the key itself.

## 4. Tests

`tests/conftest.py` replaces AstrBot with stubs. Thus, the tests do not need AstrBot:

```bash
uv venv .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

For tests of parsers with recorded data and a fake `api`, see the `tests` directory of [`br_south_disaster`](https://github.com/h0ffmann/astrbot_dw_ext_br_south_disaster/tree/main/tests).
For a test that loads an extension through the real host, see `tests/test_extension_integration.py`.

## 5. Publish an extension

Put the extension in a separate repository. The repository root is the extension root:

```text
astrbot_dw_ext_<id>/
  extension.yaml
  __init__.py
  extension.py
  locale/zh-CN.json
  README.md
  LICENSE
```

- The repository name is `astrbot_dw_ext_<id>`. The `id` in `extension.yaml` is the same value.
- Use underscores, not hyphens. The host imports the `id` as a Python package name.
- Do not change an `id` after a release.
- Make a tag `v<version>` that is the same as `version` in `extension.yaml`.
- A zip file has one top-level directory, with the name `<id>`.
- The README tells the data sources and the settings. It also tells that the data is for reference only.
