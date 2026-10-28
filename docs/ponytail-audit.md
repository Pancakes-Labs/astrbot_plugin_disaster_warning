# Ponytail audit of the existing code (suggestions only), 2026-10-28

This document lists possible simplifications of the existing plugin code. **This pull request
does not apply them.** The pull request only adds lines, so that the review is easy. The
maintainers can decide on each item in a separate pull request.

Method:

1. We used `vulture` (60 % confidence, 205 candidates) and a manual review of `core/` and `utils/`.
2. For each "delete" item, we found zero references in the repository. The search used `.py`,
   `.js`, `.html`, `.json` and `.md` files, whole words, text strings and `getattr`.
3. For each "dup" or "shrink" item, we checked that the change gives the same result in all
   cases (None, empty text, 0, exception types, order). We wrote a test for the old behavior
   and ran it on the old code and on the changed code. These tests are not in this pull request.

Scope: complexity only. Correctness, security and performance are not in the scope.

## 1. Code without references (approximately 617 lines)

| Area | Items |
|---|---|
| `core/network` (approximately 230 lines) | Seven helpers of `websocket_manager.py` (`_log_message`, `_is_critical_error`, `_get_handler_name_for_connection`, `_schedule_reconnect`, `_cancel_and_wait`, `_emit_offline_notification`, `_find_handler_by_prefix`). Four helpers of the admin web server (`_send_full_update`, `_broadcast_data`, `_get_data_source_host`, `_ping_host`). `parse_code_from_url` and `IMAGE_BASE` of the NMC clients, `fetch_all_cities`, `set_host_override`, `resolve_active_server_domain`, `get_persisted_refresh_token`, `access_token_expires_at`. |
| `core/app` | `DisasterWarningService._cancel_and_wait`, `is_in_silence_period` (two places), `get_sub_source_status`, `get_active_data_sources`, `note_connection_skipped`, `note_poll_skipped`. |
| `core/message` | `format_binary_timestamp`, `_extract_content_without_timestamp`, `_get_source_org_name`, `_get_envelope`, `update_config`, `get_buffer_stats`, `_compute_t_p_axes`, `_legend_dot_radius`, `_initialize_remote_page_pool`. |
| `core/services` | Three `get_runtime_status` methods (EQSC pollers), `lookup_sect`, `is_data_loaded`, `get_region_stats`, `_remember_cenc_ir_fingerprint`, `get_meta`, `is_family_enabled`, `_resolve_disaster_type`, `create_flow`, `update_flow`, `_next_sim_event_sequence`, `observe_event`. |
| `core/storage` | `remove_change_listener`, `_extract_region`, `_get_event_description`, `get_event_description`. |
| `core/sources`, `core/parsers` | `get_source_ids_by_fusion_group`, `get_source_ids_by_institution_key`, `SourceEntry.config_path`, `_decode_pixel`. |
| `plugin/` | `handle_query_earthquake_warning_with_timeout`. It also does not work (`async for` on `asyncio.wait_for`). `LOCATION_SCOPE_CURRENT`. |
| `requirements.txt` | No code imports `pydantic` or `python-dateutil`. fastapi installs pydantic. |

## 2. Structure (duplicates, wrappers, standard library)

| # | Tag | Suggestion | Location |
|---|---|---|---|
| 1 | shrink | `create_parser_for_source` uses 7 `if` blocks to select a parser class. Two dict tables can replace them. Keep the weather case as an `if` block. | `core/parsers/parser_registry.py` |
| 2 | dup | Six HTTP clients have the same User-Agent, `_ensure_session` and `close()`. One small mixin can replace them. The weather client keeps its Referer header. | `core/network/http/` (NMC and FAN Studio clients) |
| 3 | dup | The radar client and the precipitation client have the same image download code. | `core/network/http/` |
| 4 | dup | Four copies of `safe_int` and `safe_float` in the simulation code. | `core/services/simulation/` |
| 5 | dup | The weather color map `{"白色":0 … "红色":4}` (white … red) is in `weather_rule.py` and in `weather_aggregation_service.py`. | `core/rules`, `core/message/push` |
| 6 | dup | `_clean_text` is the same in `tsunami_levels.py` and in `jma_tsunami_normalize.py`. | `core/domain/tsunami/` |
| 7 | wrapper | `FusionStateStore._safe_float` and `_parse_similarity_time` only call other helpers. | `core/message/runtime/fusion_state_store.py` |
| 8 | shrink | The `cenc_eqlist` and `jma_eqlist` branches are almost the same. A dict lookup can replace them. | `core/network/source_ingress_side_effect_service.py` |
| 9 | stdlib | The `try/except ImportError` around `zoneinfo` is not necessary. Python 3.10 always has it. | `utils/time_converter.py` |
| 10 | stdlib | Four hand-written loops can be `any()`, `sum()` or `Counter`. | `core/services/health/`, `core/services/query/`, `core/services/notification/` |

## 3. Items that need a decision of the maintainers

These items are possible, but the change can change the behavior. We did not test them.

| # | Tag | Finding | Reason for caution |
|---|---|---|---|
| 11 | delete | `MSIL_SHINDO_TO_RGB` (approximately 25 lines). Only a comment refers to it. | It can be reference data. |
| 12 | delete | SQL helpers without references: `insert_sample`, `upsert_station_peak`, `get_event_sources`, `_is_cenc_intensity_report_row`, `build_cenc_intensity_report_sql_predicate`. | The code is about the database. |
| 13 | delete | `CN_SOURCE_ALIASES` and `_legacy_marker_paths`. | Migration code. |
| 14 | delete | `note_poll_fetch_started`, `note_poll_fetch_completed`, the field `primed_at`. | Hooks that no code calls. They can be for future use. |
| 15 | delete | Possibly dead: `switch_fan_server_preference`, `get_disaster_service`, `stop_disaster_service`, `register_reconnect_callback`, `resolve_shared_token_manager`, `resolve_connection_counts`, `fetch_typhoon_detail`, and some client methods in `core/network/http/`. | We did not check these items. |
| 16 | delete | Attributes that code writes but does not read: `init_started_at`, `db_maintenance_lock`, `stop_started_at`, `is_active`. | They can be for future use. |
| 17 | bug? | `SimulationStepBuilder.reset`. Its docstring tells that the code calls it before each run, but no code calls it. The code never clears `_event_key_prefixes`. | This can be a bug. |
| 18 | delete | The parameters `wait_until` and `parse_stations` have no use. | The signatures change. |
| 19 | dup | Many `safe_int`, `safe_float` and `_as_float` helpers. Many date parsers that are similar to `TimeConverter.parse_datetime`. | They are different for bool, inf, nan, empty values and formats. |
| 20 | dup | `_clean_text` in `tsunami_title.py` and in `china_intensity_report_eqsc_parser.py`. | `tsunami_title.py` also removes "unknown" and "未知" ("unknown"). |
| 21 | dup | JSON file load with a fallback in four places. | Each place has its own log text and fallback value. |
| 22 | yagni | `MessageSystemNotificationService` and `SourceIngressSideEffectService` have one method each. | Other modules use their names. |
| 23 | stdlib | `utils/version.py` tries `tomllib`, then `tomli`. | Keep it. The minimum Python version is 3.10, and `tomllib` starts in 3.11. |

We did not do a line-by-line review of the largest files (`japan_eew_parser.py`,
`global_sources_parser.py`, `tsunami_parser.py`, `snet_parser.py`, `earthquake_presenter.py`,
`browser_manager.py`, `jma_hypo_renderer.py`, `message_build_service.py`,
`websocket_manager.py` and `core/network/admin/`).
