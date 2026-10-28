# Ponytail audit (a pass to remove over-engineering), 2026-10-28

Scope: all the Python code. Method:

1. We used `vulture` (60 % confidence, 205 candidates).
2. We checked each candidate by hand.
3. We removed code **only if it had zero references** in the repository. The check included
   `.py`, `.js`, `.html`, `.json` and `.md` files, whole words, text strings and `getattr`.
4. A second person (agent) checked each removal again before the merge.

The tests (62), the import of `main.py` and ruff pass. This audit is about complexity only.
Correctness, security and performance are not in its scope.

Commits: `2bca331`, `461dd2e`, `8ce4018`, `6d855ea`, `33e3b1d`, `2dd6497` (approximately **-617 lines, -2 dependencies**).

## Items that we removed

- **delete**: Methods and functions without references in `core/network` and `core/app`. Approximately 231 lines. They include:
  - Seven helpers of the WebSocket manager.
  - Four broadcast and ping helpers of the admin web server.
  - Helpers of the NMC clients.
  - Getters of tokens and policies.
  - Helpers for status and startup silence.
- **delete**: Methods and functions without references in `core/message`, `core/services` and `core/storage`. Approximately 369 lines. They include:
  - `_initialize_remote_page_pool`.
  - Three `get_runtime_status` methods.
  - `create_flow` and `update_flow`.
  - Getters for regions, geo data, S-Net and notifications.
  - Wrappers for statistics.
- **delete**: `handle_query_earthquake_warning_with_timeout`. It had no references, and it did not work (`async for` on `asyncio.wait_for`). Also `LOCATION_SCOPE_CURRENT`.
- **delete**: `get_source_ids_by_fusion_group`, `get_source_ids_by_institution_key` and `SourceEntry.config_path`.
- **delete (dependencies)**: `pydantic` and `python-dateutil` in `requirements.txt`. No code imports them. fastapi still installs pydantic. We changed the install line in the README.

## Items for the maintainers to decide (largest first)

| # | Tag | Finding | Location |
|---|---|---|---|
| 1 | delete | `MSIL_SHINDO_TO_RGB` (approximately 25 lines). Only a comment refers to it. | `core/message/render/snet_map_renderer.py:62`, `simulation_builder.py:122` |
| 2 | delete | SQL helpers without references: `ConnectionHealthRepository.insert_sample`, `SnetPeakRepository.upsert_station_peak`, `DatabaseManager.get_event_sources`, `_is_cenc_intensity_report_row`, `build_cenc_intensity_report_sql_predicate` | `core/storage/*` |
| 3 | delete | Data from old migrations: `CN_SOURCE_ALIASES`, `_legacy_marker_paths` | `core/storage/history_dirty_data_cleanup_service.py:158,184` |
| 4 | delete | Hooks that no code calls: `note_poll_fetch_started` and `note_poll_fetch_completed`. Field that no code reads: `primed_at`. | `core/app/runtime/startup_silence_coordinator.py` |
| 5 | delete | Possibly dead code: `switch_fan_server_preference`, `get_disaster_service`, `stop_disaster_service`, `register_reconnect_callback`, `resolve_shared_token_manager`, `resolve_connection_counts`, `fetch_typhoon_detail`. Also some client methods in `core/network/http/*` (`clear_cache`, `fetch_list`, `fetch_detail`, `fetch_aqi`, `find_station`, `fetch_range` and others). We did not check these items. | `core/app/*`, `core/network/http/*` |
| 6 | delete | Attributes that code writes but does not read: `init_started_at`, `db_maintenance_lock`, `stop_started_at`, `is_active` | `core/app/*` |
| 7 | delete | `SimulationStepBuilder.reset`. Its docstring tells that the code calls it before each run, but no code calls it. This can be a bug: the code never clears `_event_key_prefixes`. | `core/services/simulation/simulation_builder.py` |
| 8 | delete | Parameters without use: `wait_until` and `parse_stations`. Removal changes the signatures. | `browser_manager.py:769`, `snet_poll_service.py:198` |
| 9 | stdlib | `utils/version.py` tries `tomllib`, then `tomli`, then None. Keep it: the minimum Python version is 3.10, and `tomllib` starts in 3.11. | `utils/version.py` |
| 10 | stdlib | Done in the second pass (see below). | `utils/time_converter.py` |
| 11 | delete | Reference tables and constants that no code reads: `CSIS_GRADE_NAMES`, `FLAP_IGNORE_SECONDS`, `DEPTH_FRAC_DEEP`, `FAN_IP_CONNECTION_LIMIT` | different files |
| 12 | shrink | The weather color map `{"白色":0 … "红色":4}` (white … red) is in two files: `weather_rule.py` and `weather_aggregation_service.py`. Put it in one constant. | `core/rules`, `core/message/push` |

net: -617 lines and -2 dependencies applied. The table above can remove approximately 300 more lines.

## Second pass: structure (duplicate helpers, wrappers, stdlib)

Method: three agents read `core/` and `utils/` by area. They applied a change only if it
gives the same result in all cases (None, empty text, 0, exception types, order and return types).
For each change, a test keeps the old behavior. We wrote each test before the change.
We ran all new tests on the old code, and they passed. Result: 109 tests pass.
The code is 173 lines shorter (-477, +304), and there are 48 new tests.

### Items that we changed

- **shrink**: `create_parser_for_source` used 7 `if` blocks to select a parser class. Two dict tables replace them. The weather case stays an `if` block. Test: `tests/test_ponytail_parsers.py` (all 36 catalog sources). [`core/parsers/parser_registry.py`]
- **dup**: `_clean_text` was in `tsunami_levels.py` and in `jma_tsunami_normalize.py` with the same body. Now `tsunami_levels.py` imports it. Test: `tests/test_ponytail_domain.py`. [`core/domain/tsunami/`]
- **dup**: Six HTTP clients had the same User-Agent, `_ensure_session` and `close()`. They now use `UaSessionMixin`. The weather client keeps its Referer header. Test: `tests/test_ponytail_network_http.py`. [`core/network/http/ua_session.py` and six clients]
- **dup**: The radar client and the precipitation client had the same image download code. They now use two helpers in the mixin. The log text does not change. Test: `tests/test_ponytail_network_download.py`. [`core/network/http/`]
- **dup**: Four copies of `safe_int` and `safe_float` in the simulation code became one pair in `flow_models.py`. Test: `tests/test_ponytail_services.py`. [`core/services/simulation/`]
- **wrapper**: `FusionStateStore._safe_float` and `_parse_similarity_time` only called other helpers. The callers now call those helpers directly. Test: `tests/test_ponytail_message.py`. [`core/message/runtime/fusion_state_store.py`]
- **shrink**: Two `cenc_eqlist` and `jma_eqlist` branches that were almost the same became one dict lookup. Test: `tests/test_ponytail_network_ingress.py`. [`core/network/source_ingress_side_effect_service.py`]
- **stdlib**: We removed the `try/except ImportError` around `zoneinfo`. Python 3.10 always has it. Test: `tests/test_ponytail_services.py`. [`utils/time_converter.py`]
- **stdlib**: Four hand-written loops became `any()`, `sum()` and `Counter`. [`core/services/health/`, `core/services/query/`, `core/services/notification/`]
- **shrink**: We removed two `isinstance(data, dict)` checks that an earlier check already does. [`core/network/http/eqsc_typhoon_client.py`]

### Items for the maintainers to decide (second pass)

| # | Tag | Finding | Why we did not change it |
|---|---|---|---|
| 13 | dup | Many `safe_int`, `safe_float` and `_as_float` helpers in `core/services/query/`, `core/storage/`, `core/network/http/jma_hypo_client.py` and `core/domain/typhoon/typhoon_peaks.py`. `utils/converters.safe_float_convert` can replace some of them. | The helpers are different for bool, inf, nan and empty values. |
| 14 | dup | Many date parsers: `typhoon_rule._coerce_datetime`, `typhoon_event_adapter._normalize_time`, `flow_models._parse_dt` and `typhoon_enrichment_service._coerce_datetime`. They are similar to `TimeConverter.parse_datetime`. | The formats and the fallback values are different. |
| 15 | dup | `_clean_text` in `tsunami_title.py` and in `china_intensity_report_eqsc_parser.py`. | `tsunami_title.py` also removes "unknown" and "未知" ("unknown"). This is a different function. |
| 16 | dup | JSON file load with a fallback in four places (`weather_region_resolver`, `region_service`, `radar_query_service`, `session_config_manager`). | Each place has its own log text and fallback value. |
| 17 | dup | `EqscHttpClient` and `EqscTokenManager` have their own session code. | They use different timeouts, no User-Agent, and a different `close()`. |
| 18 | yagni | `MessageSystemNotificationService` and `SourceIngressSideEffectService` have one method each. | Other modules use their names and attributes. |
| 19 | dup | `timezone(timedelta(hours=8))` is in three files. `TIMEZONES["UTC+8"]` can replace it. | The change does not make the code shorter. |

We did not do a line-by-line review of the largest files: `japan_eew_parser.py`, `global_sources_parser.py`,
`tsunami_parser.py`, `snet_parser.py`, `earthquake_presenter.py`, `browser_manager.py`,
`jma_hypo_renderer.py`, `message_build_service.py`, `websocket_manager.py` and `core/network/admin/`.
These files handle live data or the admin API. A change there needs more tests first.
