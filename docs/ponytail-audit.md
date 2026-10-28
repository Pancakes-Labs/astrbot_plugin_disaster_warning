# Ponytail audit (over-engineering pass), 2026-10-28

Scope: the whole Python tree. Method: a `vulture` scan (60 % confidence, 205 candidates)
followed by manual verification. Code was **cut only when it had zero references**
anywhere in the repo (`.py`/`.js`/`.html`/`.json`/`.md`, whole-word, including string and
`getattr` use). Each cut was re-checked independently before merge. Tests (62), the
`main.py` import, and ruff all pass. Complexity only; correctness, security and
performance are out of scope.

Commits: `2bca331`, `461dd2e`, `8ce4018`, `6d855ea`, `33e3b1d`, `2dd6497` (≈ **-617 lines, -2 deps**).

## Applied

- **delete** — unreferenced methods and functions in `core/network` (websocket manager helpers ×7, admin web-server broadcast/ping helpers ×4, NMC client helpers, token/policy getters) and `core/app` (status/silence helpers). About 231 lines.
- **delete** — unreferenced methods and functions in `core/message`, `core/services` and `core/storage`:
  - `_initialize_remote_page_pool`;
  - 3 × `get_runtime_status`;
  - `create_flow` / `update_flow`;
  - region / geo / snet / notification getters;
  - stats wrappers.

  About 369 lines.
- **delete** — `handle_query_earthquake_warning_with_timeout` (unreferenced and broken: `async for` over `asyncio.wait_for`), and `LOCATION_SCOPE_CURRENT`.
- **delete** — `get_source_ids_by_fusion_group`, `get_source_ids_by_institution_key` and `SourceEntry.config_path`.
- **delete (deps)** — `pydantic` and `python-dateutil` from `requirements.txt`: never imported. pydantic still arrives through fastapi. The README install line was updated to match.

## Not applied — needs maintainer judgement (ranked)

| # | Tag | Finding | Where |
|---|---|---|---|
| 1 | delete | `MSIL_SHINDO_TO_RGB` (~25 lines): referenced only by a comment | `core/message/render/snet_map_renderer.py:62`, `simulation_builder.py:122` |
| 2 | delete | Unreferenced SQL helpers: `ConnectionHealthRepository.insert_sample`, `SnetPeakRepository.upsert_station_peak`, `DatabaseManager.get_event_sources`, `_is_cenc_intensity_report_row`, `build_cenc_intensity_report_sql_predicate` | `core/storage/*` |
| 3 | delete | Migration leftovers `CN_SOURCE_ALIASES`, `_legacy_marker_paths` | `core/storage/history_dirty_data_cleanup_service.py:158,184` |
| 4 | delete | Never-wired hooks `note_poll_fetch_started/completed`; write-only field `primed_at` | `core/app/runtime/startup_silence_coordinator.py` |
| 5 | delete | Possibly dead: `switch_fan_server_preference`, `get_disaster_service`, `stop_disaster_service`, `register_reconnect_callback`, `resolve_shared_token_manager`, `resolve_connection_counts`, `fetch_typhoon_detail`, and several `core/network/http/*` client methods (`clear_cache`, `fetch_list`, `fetch_detail`, `fetch_aqi`, `find_station`, `fetch_range`, …) | `core/app/*`, `core/network/http/*` |
| 6 | delete | Write-only attributes `init_started_at`, `db_maintenance_lock`, `stop_started_at`, `is_active` | `core/app/*` |
| 7 | delete | `SimulationStepBuilder.reset`: its docstring says it is called before each run, but no caller exists (possible bug: `_event_key_prefixes` is never cleared) | `core/services/simulation/simulation_builder.py` |
| 8 | delete | Unused parameters `wait_until`, `parse_stations` (signature change) | `browser_manager.py:769`, `snet_poll_service.py:198` |
| 9 | stdlib | `tomllib`/`tomli`/None triple fallback: if the floor becomes Python 3.11, `import tomllib` alone, and drop `tomli` | `utils/version.py` |
| 10 | stdlib | `try/except ImportError` around `zoneinfo` (always present on 3.9+), plus the `ZoneInfo is None` branches | `utils/time_converter.py:18-25` |
| 11 | delete | Reference tables and constants with no readers: `CSIS_GRADE_NAMES`, `FLAP_IGNORE_SECONDS`, `DEPTH_FRAC_DEEP`, `FAN_IP_CONNECTION_LIMIT` | various |
| 12 | shrink | The weather colour map `{"白色":0 … "红色":4}` (white … red) is duplicated in `weather_rule.py` and `weather_aggregation_service.py`; hoist it to one constant | `core/rules`, `core/message/push` |

Not deep-reviewed: `core/rules/` and `core/domain/` had no vulture hits.

net: -617 lines, -2 deps applied; roughly -300 more possible from the table above.
