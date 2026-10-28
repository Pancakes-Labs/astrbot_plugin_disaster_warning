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
| 9 | stdlib | `utils/version.py` tries `tomllib`, then `tomli`, then None. If the minimum Python version becomes 3.11, use only `import tomllib` and remove `tomli`. | `utils/version.py` |
| 10 | stdlib | A `try/except ImportError` around `zoneinfo`. Python 3.9 and later always has `zoneinfo`. Then the `ZoneInfo is None` branches are also dead. | `utils/time_converter.py:18-25` |
| 11 | delete | Reference tables and constants that no code reads: `CSIS_GRADE_NAMES`, `FLAP_IGNORE_SECONDS`, `DEPTH_FRAC_DEEP`, `FAN_IP_CONNECTION_LIMIT` | different files |
| 12 | shrink | The weather color map `{"白色":0 … "红色":4}` (white … red) is in two files: `weather_rule.py` and `weather_aggregation_service.py`. Put it in one constant. | `core/rules`, `core/message/push` |

We did not do a full review of `core/rules/` and `core/domain/`. `vulture` found no candidates there.

net: -617 lines and -2 dependencies applied. The table above can remove approximately 300 more lines.
