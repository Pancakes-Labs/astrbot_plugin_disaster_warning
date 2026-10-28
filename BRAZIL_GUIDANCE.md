# BRAZIL_GUIDANCE.md

This document records what we learned when we added the extension system and the `br_rs`
extension (Brazil, Rio Grande do Sul). It is for the people and the AI agents that do the next
work in this area. The upstream repository has no `AGENTS.md` file. When it gets one, use this
document as the start.

Date: 2026-10-28. Scope: upstream issue 153 (extension system) and the first Brazilian extension.

---

## 1. Context

- The upstream repository is `Pancakes-Labs/astrbot_plugin_disaster_warning`. The work was done in a fork.
- The maintainer approved the extension system in issue 153. The maintainer will review all
  changes through issues and pull requests.
- The maintainer gave these requirements:
  - The system is modular, pluggable and composable.
  - The system isolates faults. A broken extension does not stop the host.
  - The system is declarative, discoverable and backward-compatible.
  - The system supports translations from the first version. Each extension has a `locale/`
    directory. A missing key uses the host language.
- Brazil is too large for one source. Make one extension for each state (UF). Each extension
  filters the national feeds by UF or by the IBGE municipality code.
- El Niño is background data, not a new disaster type. Heavy rain and floods use the
  existing weather type.

## 2. Hard rules

1. **Upstream actions need approval.** Do not open a pull request or write a comment in the
   upstream repository without the explicit approval of the owner. An approval is for one action only.
   - In a fork, `gh pr create` uses the parent repository by default. Always give `--repo` and `--base`.
   - Do not write `Fixes #N`, `Closes #N` or `owner/repo#N` in commits or pull request text.
     These references send notifications to the upstream repository.
2. **Language.**
   - Write all new comments, docstrings, documents and commit messages in English.
   - Use ASD-STE100 (Simplified Technical English). Write short sentences. Use the active voice.
     Use simple words with one meaning.
   - Do not add Portuguese text. Proper nouns and raw data values (for example, `Moderado`) are not our text.
   - Do not change the Chinese comments that are already in the upstream code.
   - Keep the upstream log style (`[灾害预警] ...`) and the Chinese user interface text.
3. **Python only.** Do not add JavaScript or WebUI work for this feature.
4. **A pull request only adds lines (hard rule).** Do not change or remove existing upstream code.
   This makes the review easy.
   - `git diff --numstat main | awk '$2>0'` must give no output.
   - Insert a hook as new lines. If you must change the behavior of an existing function, add a
     wrapper after it (see `build_default_rule_chain` in `core/rules/rule_chain.py`).
   - Put possible simplifications of the existing code in `docs/ponytail-audit.md`. Do not apply them.
5. **Smallest change that works (ponytail), in the new code.**
   - Reuse code that is already in the repository.
   - Use the standard library before you add code.
   - Do not add an abstraction that has only one use.

## 3. Facts about the repository

### Tools and environment

- The tests do not install AstrBot. `tests/conftest.py` replaces `astrbot` with stubs. The stub logger accepts the extra keyword arguments of the plugin (`is_event_linked`).
- Import `core.app.disaster_service` first in tests. Other import orders cause a circular import
  in `core/parsers`. This problem is in the upstream code.
- `main.py` needs the `playwright` Python package to import, but not the browser.
- `gh pr edit` fails with the error "Projects (classic) is being deprecated". Use the REST API:
  `gh api -X PATCH repos/<owner>/<repo>/pulls/<n> -F body=@file.md`.
- The CI uses Python 3.10. Thus, `tomllib` (3.11) needs the `tomli` fallback.
- The upstream ruff job only reports. It does not stop the build.
- To test in real AstrBot, use the Docker image `soulter/astrbot`. Mount the plugin to
  `/AstrBot/data/plugins/<name>` and put the configuration in `/AstrBot/data/config/<name>_config.json`.

### Host behavior that an extension must know

- `EventTimeRule` ignores events that are older than one hour. A CEMADEN alert stays active for
  days. Thus, use the time of the last update (`ult_atualizacao`) as the event time, not the creation time.
- The host found the weather severity from Chinese color words in the title. Now an event can
  set `metadata["severity_level"]` (0 to 4). `WeatherRule` and the weather aggregation use it.
- The weather presenter header, the region resolver and the icon maps are for China only.
  An extension supplies its own presenter.
- `SOURCE_CATALOG` and the `SOURCE_IDS_BY_*` indexes are module dicts. Other modules keep
  references to them. Change them in place. Do not assign new dicts.
- Extensions load before `_initialize_parsers()`. A plugin reload must remove the old extension
  modules from `sys.modules`. If it does not, code changes do not apply.

### Brazilian data sources

| Source | Access | Notes |
|---|---|---|
| CEMADEN alerts `painelalertas.cemaden.gov.br/wsAlertas2` | Public JSON | Levels `Moderado`, `Alto`, `Muito Alto` → 2, 3, 4. Hazards: hydrological risk and mass movement. |
| CEMADEN rain gauges `resources.cemaden.gov.br/.../getJson2.php?uf=RS` | Public JSON | The server sends JSON as `text/html`. A missing total is the string `"-"`. A gauge without a reading has a null time. |
| NOAA CPC ONI `oni.ascii.txt` | Public text | The last row is the current season. |
| INMET warnings | Timeout from outside Brazil | Test from a Brazilian IP address. WMO WIS2 is a possible alternative. |
| SGB SACE river levels | No API (Java RPC and PDF) | Needs reverse engineering. |
| ANA HidroWeb | Needs credentials | Request them by e-mail. |
| Defesa Civil (IDAP, CAP) | No public feed found | Portaria MIDR 2.458/2026 tells them to publish CAP. |

- The CEMADEN certificate chain ends at GlobalSign Root R46. Some system stores (macOS
  `/etc/ssl/cert.pem`) do not have this root. The extension HTTP session uses `certifi`.
  The AstrBot Docker image has the root and `certifi`.
- RS is the best first state for floods, heavy rain and El Niño (455 gauges, four SGB river systems,
  a strong El Niño in 2026). SC is the second state. The survey is in `docs/extensions/brazil-sources-survey.md`.

## 4. How to work (lessons from this session)

1. **Write the contract first.** We wrote `docs/extensions/design.md` before the code. Then three
   agents worked in parallel against the same interface, and the merge had no conflicts.
2. **Give each parallel agent its own files.** Parallel work is safe only when the file sets do not overlap.
3. **Check the base of a worktree.** The agent worktrees started from `main`, not from the feature
   branch. Tell each agent to run `git reset --hard <feature-branch>` first.
4. **Check the claims of an agent.** Agents reported "zero references", "the same behavior" and
   "the docstring describes the test". We found errors when we checked them. Do these checks again:
   - Use `grep -rnw` on all file types, including strings, the JS frontend and templates.
   - Before a refactor, write a test that keeps the old behavior. Run the new test on the **old**
     code too. It must pass on the old code and on the new code.
   - Compare each docstring with the assertions of its test.
5. **Do not refactor the upstream code in a feature pull request.** We did two ponytail passes on the
   upstream code (approximately 617 lines removed and 173 lines simplified). The owner asked us to
   revert all of them, because a feature pull request must only add lines. Now the findings are
   suggestions in `docs/ponytail-audit.md`. If two helpers differ in an edge case
   (None, bool, inf, nan, empty text), they are not duplicates.
6. **Test with real data at three levels:**
   - Unit tests with recorded data (`tests/br_rs/fixtures/`).
   - Live tests with the real feeds (`tests/live/`). They run only with `DW_LIVE_TESTS=1`.
     They check the schema, the parser results and the full path through the host.
     An empty result (no alerts now) is a valid state.
   - A run in real AstrBot (Docker).
7. **Follow the upstream documentation pattern:**
   - Each data source has one API document in `docs/` (see `docs/EQSC API.md`).
   - The README has sections for data source status, configuration, push examples and commands.
   - `CHANGELOG.md` uses emoji category headings.
   - Each module starts with a docstring that tells its responsibility.
8. **Ask only for real decisions.** The owner changed the language rule two times. Write a durable
   rule into `AGENTS.md` and into memory when the owner says "hard rule".

## 5. Ponytail review of `extensions/br_rs` (after all fixes)

The review uses the ponytail ladder: (1) is it necessary, (2) does the repository already have it,
(3) does the standard library have it, (4) can it be shorter.

| Item | Result |
|---|---|
| No host imports | Pass. The extension uses `api.models` only. |
| Dependencies | Pass. Standard library and `aiohttp` only. |
| Pure parsers | Pass. `parse_alerts`, `parse_rain`, `rain_level` and `build_enso` take decoded data. Tests do not need the network. |
| Duplicate code | Pass. One time parser (`_parse_time`). One envelope builder (`_envelope`, 5 arguments). |
| Constants | Pass. The thresholds, the levels, the ENSO strength limits and the ONI column count are named constants. |
| Complexity | Pass. `parse_rain` was rank C (16). Now `rain_level` and `_gauge_item` contain the threshold logic. The highest rank is B (9). |
| Exceptions | Pass, with one justified exception. `EnsoCache.get` catches all exceptions (`# noqa: BLE001`), because ENSO is optional data. A failure there must not stop an alert. |
| Removed | The identity attribute `fingerprint`. It was a copy of `published_at` and no code read it. The pt-BR locale. The parameter `source_id` of `_envelope` (the hazard gives it). |
| Known limit | `AlertState` keeps each alert ID forever (a few KB in a year). A `ponytail:` comment marks this limit. Removing old IDs can cause a duplicate push if CEMADEN drops an alert for one poll. |
| Not changed | `present` stays a classmethod, because the upstream `BasePresenter` uses a classmethod. Vulture reports its `cls` argument; this is a false positive. `_entry` sets some optional `SourceEntry` fields (`dispatch_family`, `routing_tags`) that only WebSocket routing uses. They cause no problem. |

## 6. Code smell scores of `extensions/br_rs`

Rules: `extensions/ruff.toml` (strict: bugbear, complexity, pylint, simplify, exceptions and more).
Complexity and maintainability: radon 6.0.1. Dead code: vulture 2.14 (60 % confidence).
"Before" is the state of pull request 1. "After" is the state after this change.

| Metric | Before | After |
|---|---|---|
| Ruff strict findings | 12 | **0** (1 justified `noqa`) |
| Highest cyclomatic complexity | 16, rank C (`parse_rain`) | **9, rank B** (`parse_alerts`) |
| Functions with rank C or worse | 1 | **0** |
| Average cyclomatic complexity | A (3.60) | **A (3.29)** |
| Maintainability index, `extension.py` | A (36.82) | **A (44.19)** |
| Maintainability index, `enso.py` | A (56.61) | **A (67.41)** |
| Maintainability index, `presenter.py` | A (50.16) | A (50.16) |
| Source lines (SLOC) | 349 | 349 |
| Vulture findings | 4 | 3 (all are entry points of the extension contract) |
| Tests (offline / live) | 61 / 7 | 61 / 7 |

The ruff findings before the change: 7 lines too long, 2 magic values, 1 blind `except`,
1 function with too many arguments, 1 unused argument.

The CI job `.github/workflows/extensions-code-smell.yml` runs these checks for each change in
`extensions/`. It stops the build if ruff finds a problem or if a function has rank C or worse.

## 7. Open items

- INMET: test the access from a Brazilian IP address. Then add the INMET CAP warnings.
- SGB river levels: get permission, or read the PDF bulletins.
- Session overrides for extension sources are not in the MVP.
- `docs/ponytail-audit.md` lists suggestions for the existing code. The maintainers decide. Row 15 needs a check.
- Do not send `AGENTS.md` (the rules of the fork) to the upstream repository.
