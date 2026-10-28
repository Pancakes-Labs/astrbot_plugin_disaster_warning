# Extension system — MVP design (contract)

Status: MVP implemented on the fork, 2026-10-28 (see README.md for the developer guide). Language: Python only. Principle: smallest change that
works (YAGNI). Anything not listed here is out of scope for the MVP.

Goals from the maintainer: modular / pluggable, **fault-isolated** (a broken extension
never stops the host), declarative, discoverable, backward-compatible (no extension
enabled → behaviour identical to today), **i18n from day one**.

## 1. Layout and discovery

Extensions are Python packages in one of two roots:

1. `<plugin_dir>/extensions/<id>/` — bundled (official examples, e.g. `br_rs`)
2. `<data_dir>/extensions/<id>/` — user-installed (`data_dir = StarTools.get_data_dir("astrbot_plugin_disaster_warning")`)

The first root wins on duplicate `id`; the duplicate is reported as an error.

```
extensions/<id>/
  extension.yaml
  __init__.py
  extension.py        # default entry module, exposes setup(api)
  locale/<lang>.json  # e.g. zh-CN.json, en.json, pt-BR.json
```

`extension.yaml`:

```yaml
id: br_rs                  # required, [a-z0-9_]+, must equal the directory name
name: Brasil — Rio Grande do Sul
version: 0.1.0
api_version: 1             # required; host supports exactly 1
min_host_version: 1.6.5    # optional; compared to metadata.yaml version (leading "v" stripped)
entry: extension:setup     # optional, default "extension:setup" (module relative to the package)
description: ...
author: ...
permissions: [network]     # informational in the MVP; "network" is required to call register_poller
```

## 2. Configuration

A new top-level key in `_conf_schema.json` (off by default, so nothing changes for existing users):

```json
"extensions": {
  "enabled_ids": [],        // list of extension ids to load
  "settings": "{}"          // JSON text: {"<id>": {...private config...}}
}
```

`settings` may also arrive already parsed as a dict. Malformed JSON → logged, treated as `{}`.

## 3. Host registry functions (implemented by the host refactor)

All registration takes `owner=<extension id>`, so everything an extension registered can be
removed if its `setup` fails. Built-in behaviour must be unchanged.

| Module | Functions |
|---|---|
| `core/sources/source_entry.py` | new enum member `ProviderFamily.EXTENSION = "extension"` |
| `core/sources/source_catalog.py` | `register_source_entry(entry, *, owner) -> None` (ValueError on duplicate `source_id`; rebuilds every `SOURCE_IDS_BY_*` index); `unregister_source_entries(owner) -> list[str]`; `get_source_owner(source_id) -> str \| None` (None for built-ins) |
| `core/parsers/parser_registry.py` | `register_parser(parser_name, parser_class, *, owner)`; `unregister_parsers(owner)` |
| `core/message/presenters/presenter_registry.py` | `register_text_presenter(key, presenter_class, *, owner)`; `unregister_presenters(owner)`; registered keys appear in the integrity check |
| `core/rules/rule_chain.py` | `register_rule(rule, *, after="local_rule", owner)` inserts after the built-in rule whose `rule_name` matches (falls back to the end); `unregister_rules(owner)`; `build_default_rule_chain()` includes registered rules |

Extension sources use `config_group="extensions"` and `config_key=<source_id>`. A source with
`config_group == "extensions"` counts as **enabled globally while it is registered** (its
extension is in `enabled_ids`). Every place that checks the `data_sources` switches
(`SourceEnabledRule`, `SourceRuntimeQueryService`, `_is_source_enabled_by_catalog`, …) must
honour this. Session overrides of extension sources are out of scope for the MVP.

### Weather severity

`WeatherEvent.metadata["severity_level"]` is an optional `int`: 0 white, 1 blue, 2 yellow,
3 orange, 4 red. When present, `WeatherRule` and the weather aggregation service use it
instead of scanning the Chinese colour words in the title. When absent, behaviour is
unchanged.

## 4. ExtensionAPI (passed to `setup(api)`)

```python
class ExtensionAPI:
    id: str
    version: str
    config: dict            # this extension's private settings (from extensions.settings[id])
    logger                  # logger whose messages are prefixed with "[扩展:<id>]"
    data_dir: pathlib.Path  # <data_dir>/extensions_data/<id>/, created on demand
    host_version: str
    models: types.SimpleNamespace  # host classes, so extensions never import host modules:
    # SourceEntry, SourceType, ProviderFamily, FusionRole, WeatherEvent, EventEnvelope,
    # EventIdentity, SourcePayload, RuleDecision

    def t(self, key: str, **fmt) -> str: ...
    def register_source(self, entry: SourceEntry) -> None: ...
    def register_parser(self, name: str, parser_class) -> None: ...
    def register_presenter(self, key: str, presenter_class) -> None: ...
    def register_rule(self, rule, *, after: str = "local_rule") -> None: ...
    def register_poller(
        self,
        source_id: str,
        fetch,                   # async def fetch(session: aiohttp.ClientSession) -> list[EventEnvelope]
        *,
        interval_seconds: int,   # clamped to >= 60
        timeout_seconds: int = 30,
    ) -> None: ...
```

- Extension code must not import host modules (the host package name differs between
  installs); it uses `api.models`. Presenters are duck-typed: any class with a classmethod
  `present(display_context, options) -> str`. For weather events the `display_context`
  exposes `.title`, `.headline`, `.metadata` (the `WeatherEvent.metadata`), `.source_id`.
  Rules are duck-typed: an object with `rule_name: str` and `evaluate(context) -> RuleDecision`.
- `register_source` requires `entry.config_group == "extensions"` and
  `entry.provider_family == ProviderFamily.EXTENSION`; otherwise ValueError.
- `register_poller` requires `"network"` in `permissions` and a `source_id` already
  registered by the same extension.
- `t()` lookup order:
  1. `locale/<lang>.json`, where `lang = config.get("language")` or the host language `zh-CN`;
  2. `locale/zh-CN.json` (host language);
  3. the key itself.

  The result is `.format(**fmt)`-ed; formatting errors return the unformatted string.

## 5. Runtime behaviour (ExtensionManager)

`core/extensions/manager.py` — `ExtensionManager(service, host_registry)`:

- `load_all()` runs once in `DisasterWarningService.__init__` **before** `_initialize_parsers()`, so extension parsers are built like built-in ones. For each id in `enabled_ids`:
  1. discover and parse the manifest;
  2. check `api_version` / `min_host_version`;
  3. import the entry and call `setup(api)`.

  Any exception at any step:
  - marks the extension `error` and stores the message;
  - calls every `unregister_*(owner=id)`;
  - logs a warning;
  - continues with the next extension.
- `start()` runs in the lifecycle start. One asyncio task per poller:
  - share one `aiohttp.ClientSession`;
  - call `fetch` under `asyncio.wait_for(timeout_seconds)`;
  - feed each returned envelope to `await service._handle_disaster_event(envelope)`, each inside its own try/except;
  - on an exception, log it (throttled) and apply exponential backoff capped at 10× the interval. A poller never raises out of its task.
- `stop()` cancels the poller tasks and closes the session.
- `status() -> list[dict]` returns `[{id, name, version, state: "loaded"|"error"|"not_found", error, sources: [...]}]`. The admin subcommand `扩展` ("extensions") prints this list.

`host_registry` is a small object exposing the §3 functions. The production adapter lives in
`core/extensions/host_registry.py`; tests inject a fake.

## 6. Non-goals (MVP)

Process sandboxing, marketplace/signing, zip/git install, WebAdmin pages, WebSocket
handlers from extensions, new disaster types, session-level overrides of extension
sources, migrating built-in sources.
