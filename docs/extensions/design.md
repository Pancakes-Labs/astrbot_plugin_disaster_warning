# Extension system: MVP design (contract)

Status: this pull request contains the MVP (2026-10-28). The developer guide is in `README.md`.
The language is Python only. The principle is "the smallest change that works" (YAGNI).
Items that this document does not list are not in the MVP.

The maintainer gave these goals:

- The system is modular and pluggable.
- The system isolates faults. A broken extension does not stop the host.
- Extensions are declarative and the host can find them.
- The system is backward-compatible. If no extension is enabled, the behavior does not change.
- The system supports translations (i18n) from the first version.

## 1. Layout and discovery

An extension is a Python package. The host looks for extensions in two root directories:

1. `<plugin_dir>/extensions/<id>/`: bundled extensions, for example `br_rs_disaster`.
2. `<data_dir>/extensions/<id>/`: extensions that the user installs.
   `data_dir` is `StarTools.get_data_dir("astrbot_plugin_disaster_warning")`.

If two roots contain the same `id`, the host uses the first root. The host records the
second one as an error.

```
extensions/<id>/
  extension.yaml
  __init__.py
  extension.py        # default entry module; it contains setup(api)
  locale/<lang>.json  # for example zh-CN.json, en.json, pt-BR.json and ja-JP.json
  meta.json           # optional; maintainer, coverage and credits. The host does not read it.
  extension.png       # optional; the icon of the extension
```

`extension.yaml`:

```yaml
id: br_rs_disaster                  # required; [a-z0-9_]+; it must be the same as the directory name
name: Brazil - Rio Grande do Sul
version: 0.1.0
api_version: 1             # required; the host supports only 1
min_host_version: 1.6.5    # optional; the host compares it with the version in metadata.yaml
entry: extension:setup     # optional; the default is "extension:setup"
description: ...
author: ...
permissions: [network]     # register_poller needs "network"
```

## 2. Configuration

`_conf_schema.json` has a new top-level key. It is off by default. Thus, existing users see no change.

```json
"extensions": {
  "enabled_ids": [],        // the IDs of the extensions to load
  "settings": "{}"          // JSON text: {"<id>": {...private settings...}}
}
```

`settings` can also be a dict. If the JSON text is not valid, the host writes a log message and uses `{}`.

## 3. Host registry functions

Each registration function has the argument `owner=<extension id>`. Thus, the host can remove all
items of an extension if its `setup` fails. The built-in behavior does not change.

| Module | Functions |
|---|---|
| `core/sources/source_entry.py` | New enum member `ProviderFamily.EXTENSION = "extension"` |
| `core/sources/source_catalog.py` | `register_source_entry(entry, *, owner) -> None`. It raises ValueError for a duplicate `source_id` and rebuilds all `SOURCE_IDS_BY_*` indexes. `unregister_source_entries(owner) -> list[str]`. `get_source_owner(source_id) -> str \| None`, which gives None for a built-in source. |
| `core/parsers/parser_registry.py` | `register_parser(parser_name, parser_class, *, owner)` and `unregister_parsers(owner)` |
| `core/message/presenters/presenter_registry.py` | `register_text_presenter(key, presenter_class, *, owner)` and `unregister_presenters(owner)`. The integrity check includes the registered keys. |
| `core/rules/rule_chain.py` | `register_rule(rule, *, after="local_rule", owner)` puts the rule after the built-in rule with that `rule_name`. If no rule has that name, it puts the rule at the end. `unregister_rules(owner)`. `build_default_rule_chain()` includes the registered rules. |

An extension source uses `config_group="extensions"` and `config_key=<source_id>`. A source with
`config_group == "extensions"` is **enabled globally while it is registered**. All checks of
the `data_sources` switches use this rule (`SourceEnabledRule`, `SourceRuntimeQueryService`,
`_is_source_enabled_by_catalog`). The MVP does not support session overrides for extension sources.

### Weather severity

`WeatherEvent.metadata["severity_level"]` is an optional `int`: 0 is white, 1 is blue, 2 is
yellow, 3 is orange and 4 is red. If the value is set, `WeatherRule` and the weather
aggregation service use it. If the value is not set, the host finds the color word in the
Chinese title, as before.

## 4. ExtensionAPI (the argument of `setup(api)`)

```python
class ExtensionAPI:
    id: str
    version: str
    config: dict            # the private settings of the extension (extensions.settings[id])
    logger                  # a logger that adds the prefix "[扩展:<id>]"
    data_dir: pathlib.Path  # <data_dir>/extensions_data/<id>/; the host makes it when necessary
    host_version: str
    models: types.SimpleNamespace  # host classes; extensions do not import host modules
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
        interval_seconds: int,   # the minimum value is 60
        timeout_seconds: int = 30,
    ) -> None: ...
```

- Extension code does not import host modules, because the host package name is different
  in different installations. Use `api.models`.
- A presenter is any class with the classmethod `present(display_context, options) -> str`.
  For weather events, `display_context` has `.title`, `.headline`, `.metadata` and `.source_id`.
  `.metadata` contains `WeatherEvent.metadata`.
- A rule is any object with `rule_name: str` and `evaluate(context) -> RuleDecision`.
- `register_source` raises ValueError if `entry.config_group` is not `"extensions"`.
  It also raises ValueError if `entry.provider_family` is not `ProviderFamily.EXTENSION`.
- `register_poller` needs `"network"` in `permissions`. The same extension must register the `source_id` first.
- `t()` looks for the key in this sequence:
  1. `locale/<lang>.json`. `lang` is `config.get("language")`. If it is not set, `lang` is the host language `zh-CN`.
  2. `locale/zh-CN.json` (the host language).
  3. The key itself.

  `t()` applies `.format(**fmt)` to the result. If the format operation fails, `t()` gives the text without a change.

## 5. Runtime behavior (ExtensionManager)

`core/extensions/manager.py` contains `ExtensionManager(service, host_registry)`.

- `DisasterWarningService.__init__` calls `load_all()` one time, **before** `_initialize_parsers()`.
  Thus, the host makes the extension parsers in the same way as the built-in parsers.
  For each ID in `enabled_ids`, `load_all()` does these steps:
  1. It finds and reads the manifest.
  2. It checks `api_version` and `min_host_version`.
  3. It imports the entry module and calls `setup(api)`.

  If a step raises an exception, the manager does these steps:
  - It sets the state of the extension to `error` and keeps the message.
  - It calls each `unregister_*(owner=id)` function.
  - It writes a warning to the log.
  - It continues with the next extension.
- The lifecycle start calls `start()`. The manager starts one asyncio task for each poller:
  - All pollers use one `aiohttp.ClientSession`. The session uses the certifi root certificates.
  - The manager calls `fetch` with `asyncio.wait_for(timeout_seconds)`.
  - The manager sends each envelope to `await service._handle_disaster_event(envelope)`.
    Each call has its own try/except.
  - If an exception occurs, the manager writes a log message (not more than one in each period).
    Then it waits longer before the next attempt (exponential backoff). The maximum wait is 10 times the interval.
    A poller task does not stop because of an exception.
- `stop()` cancels the poller tasks and closes the session.
- `status() -> list[dict]` gives `[{id, name, version, state, error, sources}]`.
  `state` is `"loaded"`, `"error"` or `"not_found"`. The admin command `/灾害预警扩展` shows this list.

`host_registry` is a small object with the functions of section 3. The production object is in
`core/extensions/host_registry.py`. Tests use a fake object.

## 6. Items that are not in the MVP

- Process sandboxes.
- A marketplace and signatures.
- Installation from zip files or git.
- WebAdmin pages.
- WebSocket handlers from extensions.
- New disaster types.
- Session overrides for extension sources.
- Migration of the built-in sources.
