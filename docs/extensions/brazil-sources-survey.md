# Brazil data-source survey (bootstrap for the first regional extension)

Status: research notes, 2026-10-28. Context: upstream issue 153 (Extension system).
Brazil is too large for a single "Brazil" source, so extensions are planned **per area
(state / UF)**, reusing national feeds filtered by UF / IBGE municipality code.

Contents:

1. How JP / CN sources are fetched today (the template to follow)
2. Brazilian entities and feeds (national and state level)
3. Which state to start with
4. Mapping Brazilian data to the host's event model
5. Proposed shape of the first extension

---

## 1. How JP / CN sources are fetched today

Each source is a `SourceEntry` in `core/sources/source_catalog.py`. Data reaches the
pipeline over one of two transports:

| Transport | Provider family | Endpoint / auth | Notes |
|---|---|---|---|
| WebSocket | `FAN_STUDIO` | `wss://ws.fanstudio.tech/all` (backup `.hk`); `{"type":"auth","appId","key"}` frame, key from `data_sources.fan_studio.api_key` | Only CN source of weather alarms and typhoons |
| WebSocket | `P2P` | `wss://api.p2pquake.net/v2/ws`, no auth | JMA: `code` 556 EEW / 551 report / 552 tsunami |
| WebSocket | `WOLFX` | `wss://ws-api.wolfx.jp/all_eew`, no auth | Routed by the `type` field (`jma_eew`, `cenc_eew`, ...) |
| WebSocket | `JIAN_PROJECT` | `wss://api.sismotide.top/all`; token chain `lk_` → `rt_` → `at_` | |
| WebSocket | `GLOBAL_QUAKE` | `wss://api.aloys23.link/api/v1/alert/ws/all` | |
| HTTP poll | `EQSC` | `https://equake.top`, bearer refresh token → access token | Default 120 s, clamped to 30–600 s; JMA tsunami, CMA typhoon, CENC intensity |
| HTTP poll | `DIRECT_HTTP` | MSIL S-Net tiles, no auth | `SnetPollService`, default 60 s |

How a WebSocket source is set up:

- `SourceEntry.build_connection_plan()` describes the connection.
- `ConnectionPlanBuilder` builds one connection per `connection_group`, and only for sources that are active.
- `SourceMessageRouter.register_all` registers a fixed set of handlers.
- Reconnects are handled by `websocket_reconnect_service.py`: retries, then the backup URL, then long-period fallback retries.

HTTP pollers are **hand-written services**, one per source (`SnetPollService`,
`Eqsc*PollService`). There is no generic "HTTP poll plan": a `SourceEntry` without a
`connection_url` never opens a connection. Every Brazilian feed found below is HTTP, so
the extension system needs a **generic poller** (interval + fetch + parse + fingerprint).
That is exactly the "HTTP polling example extension" of the MVP.

Flow for a China weather alarm, the closest analogue to a Brazilian rain alert:

```
FAN WS message → route_fan_studio_message → _is_source_routable (group/source/parser)
  → WeatherAlarmParser → WeatherEvent(title, headline, effective_at, metadata{province,…})
  → EventEnvelope → _handle_disaster_event → EventPipeline.handle
  → (optional per-session weather aggregation) → RuleChain (WeatherRule…)
  → WeatherAlertPresenter ("weather_cn") → push
```

### China/Japan assumptions a Brazilian source would hit

- **Severity is parsed from Chinese colour words in the title.**
  - `core/rules/weather_rule.py:78` scans for 红色/橙色/黄色/蓝色/白色 (red/orange/yellow/blue/white). A Portuguese title becomes "white" (lowest).
  - The same map is repeated in `weather_aggregation_service.py`.
  - Fix: add a structured `metadata["severity_level"]` and teach `WeatherRule` and aggregation to use it.
- **The presenter header is hard-coded.**
  - `weather_presenter.py:71` writes `[气象预警] 中国气象局` ("[Weather warning] China Meteorological Administration").
  - Display timezones default to UTC+8.
  - Fix: presenter registration plus i18n (the maintainer's localization requirement).
- **Region resolution is China-only.** `utils/china_regions.py`, `weather_region_resolver.py` and province-short matching in the keyword whitelist. A Brazilian source needs a UF/IBGE resolver.
- **Logic keyed on source-id strings:**
  - `base_parser.py:139` checks `startswith("china_weather")`;
  - any id containing `weather` gets the `weather_alarm` log stream;
  - `parser_registry.py` has `china_weather_jianproject` as a special case.
- **Icons and code maps** (`weather_alarm_code_map.py`, `WEATHER_EMOJI_MAP`) match CMA codes and Chinese keywords (暴雨 rainstorm, 内涝 urban flooding).
- **`ProviderFamily` is a closed Enum**, and the WS handler list is fixed.

These are the concrete refactors the extension MVP must include before any Brazilian
weather source looks right.

---

## 2. Brazilian entities and feeds

Legend: ✅ verified working from outside Brazil on 2026-10-28 · ⚠️ exists but needs
credentials, scraping or a Brazilian IP · ❌ no machine-readable access found.

### National (filterable by UF / IBGE code)

| Entity | What | Access | Status |
|---|---|---|---|
| **CEMADEN** (national disaster monitoring and early-warning centre, MCTI) | Active municipal **risk alerts**: hydrological risk (floods) and mass movement (landslides); levels Moderado/Alto/Muito Alto | `GET https://painelalertas.cemaden.gov.br/wsAlertas2` → JSON `{alertas:[{cod_alerta, datahoracriacao, ult_atualizacao, codibge, evento, nivel, status, uf, municipio, latitude, longitude}]}` | ✅ public, no auth |
| CEMADEN | **Rain gauges** per UF, with 1/3/6/12/24/48/72/96 h accumulations | `GET https://resources.cemaden.gov.br/graficos/interativo/getJson2.php?uf=RS` | ✅ public, no auth |
| CEMADEN | Per-station hourly series | `GET https://mapservices.cemaden.gov.br/MapaInterativoWS/resources/horario/{idEstacao}/{n}` | ✅ |
| **INMET** (national meteorological institute) | Official **weather warnings** (CAP); levels Perigo Potencial / Perigo / Grande Perigo = yellow / orange / red. Portal `alertas2.inmet.gov.br`, API `apiprevmet3.inmet.gov.br/avisos/ativos`, also published to WMO WIS2 (`wis2bra.inmet.gov.br`, `urn:wmo:md:br-inmet:alerts`) | DNS resolves but the API **timed out** from our (non-BR) network; WIS2 node answered 200 | ⚠️ test from a Brazilian IP; WIS2 (MQTT) is a candidate alternative |
| **SGB / CPRM** (geological survey) — SACE | River-level alert systems (cotas de atenção / alerta / inundação, i.e. attention / alert / flood levels), forecasts, PDF bulletins | Web app per basin (`sace.sgb.gov.br/<basin>/`) using a Java RPC framework (DWR) internally, no documented API; PDF bulletins | ⚠️ needs reverse-engineering |
| **ANA** (national water agency) — HidroWeb / Hidro-Telemetria | River level, discharge and rain telemetry (15 min / hourly) | HidroWebService API v2, needs credentials (request by e-mail to hidro@ana.gov.br) | ⚠️ credentials |
| **Defesa Civil Nacional** (national civil defence) — IDAP / Defesa Civil Alerta | Official civil-defence alerts (cell broadcast, SMS 40199). Portaria MIDR 2.458/2026 requires them to be stored in **CAP** and kept public | No public feed endpoint found yet. IDAP itself is restricted to civil-defence agencies; a public data panel exists on Anatel | ❌ for now, but the most authoritative feed to watch |
| **INPE / CPTEC** (space research institute / weather centre) | Forecasts, radar, climate bulletins | Mostly web products | ❌ for alerts |
| **NOAA CPC** (US) | **El Niño (ENSO) state**: ONI index, monthly diagnostic discussion | `https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt` (plain text) | ✅ |

ENSO right now: the latest ONI values are MJJ +1.39, JJA +1.80, **JAS 2026 +2.16 °C**,
i.e. a **strong El Niño in progress**. El Niño favours above-normal rain in southern
Brazil (with the caveat that it boosts rather than causes individual flood events),
which makes the South the most relevant first case.

### State level (examples found)

| State | Entities | Machine access |
|---|---|---|
| **RS** | Defesa Civil RS; SEMA situation room; SGB SACE basin systems for **Taquari, Caí, Uruguai and Guaíba** (Guaíba operational since June 2025, built by UFRGS) | SACE ⚠️ (scrape); CEMADEN ✅ (455 gauges) |
| **SC** | Defesa Civil SC (strong alert practice); Epagri/Ciram; AlertaBlu (Blumenau); CEOPS/FURB (Itajaí basin) | No public API found; CEMADEN ✅ (443 gauges) |
| **RJ** | Alerta Rio (Geo-Rio, city of Rio); INEA | Alerta Rio stations as ArcGIS REST ✅ (`pgeo3.rio.rj.gov.br/.../Estacoes_AlertaRio/MapServer`, metadata only verified); CEMADEN ✅ (391) |
| **SP** | CGE São Paulo (city emergency management centre); Defesa Civil SP; SAISP | No public API found; CEMADEN ✅ (724) |
| **MG** | IGAM; Defesa Civil MG; SGB SACE Rio Doce | CEMADEN ✅ (825, the most of any state) |
| **PR** | Simepar; Defesa Civil PR | simepar.br sits behind Cloudflare (403); CEMADEN ✅ (213) |

CEMADEN rain gauges per UF (live count, 2026-10-28):
MG 825 · SP 724 · BA 501 · RS 455 · SC 443 · PE 440 · RJ 391 · CE 305 · PR 213 · ES 205 ·
MA 178 · PB 139 · PI 133 · AL 115 · AM 95 · RN 89 · PA 83 · GO 81 · SE 57 · AC 53 ·
AP 40 · TO 38 · RO 35 · MS 29 · MT 28 · RR 13 · DF 5.

Snapshot of active CEMADEN alerts on 2026-10-28: 15 total (BA 8, RS 3, SP 1, MG 1, MT 1,
GO 1). That is a single sample, not a statistic.

---

## 3. Which state to start with

**Recommendation: Rio Grande do Sul (RS)**, with Santa Catarina (SC) as the second area.

Counting gauges alone, MG and SP lead. But for the specific goal of **heavy rain + flood +
El Niño**, RS has the best combination:

1. **El Niño signal**: the South has the clearest ENSO→rain link in Brazil, and a strong
   El Niño is active now (ONI +2.16). Floods in 2023 (Uruguai river, >300 mm anomalies)
   and 2024 (the RS catastrophe) give historical events to test the extension against.
2. **Flood hydrology coverage**: four SGB river-alert systems (Taquari, Caí, Uruguai,
   Guaíba), each with official attention / alert / flood levels, which map naturally to
   warning levels.
3. **Rain coverage**: 455 CEMADEN gauges with multi-window accumulations, enough for
   threshold-based heavy-rain warnings (e.g. 24 h / 72 h accumulation).
4. **Official alerts**: CEMADEN hydrological alerts already include RS municipalities
   (3 active at the time of writing).

SC is a strong second: similar El Niño exposure, the Itajaí valley flood history, and 443
gauges. Most of its feeds are national (CEMADEN), so an SC extension is cheap once RS
exists. MG/SP are better targets later, for urban flash floods and landslides.

---

## 4. Mapping to the host event model

| Brazilian datum | Host concept | Notes |
|---|---|---|
| CEMADEN alert (`evento`, `nivel`) | `WeatherEvent` (existing disaster type, as discussed in the issue) | `Moderado`→yellow, `Alto`→orange, `Muito Alto`→red; `cod_alerta` + `ult_atualizacao` → fingerprint / event_id |
| INMET CAP warning | `WeatherEvent` | Perigo Potencial / Perigo / Grande Perigo → yellow / orange / red |
| SGB river level vs. levels | `WeatherEvent` (flood) | attention / alert / flood → yellow / orange / red |
| CEMADEN accumulated rain over a threshold | `WeatherEvent` (heavy rain) generated by the extension | Thresholds are configurable per extension |
| ENSO / ONI | **Enrichment / background**, not an event | Appended as context ("strong El Niño active") to rain/flood messages; optionally a low-frequency status notice when the ENSO phase changes |

Region keys: UF + IBGE municipality code (`codibge`), timezone `America/Sao_Paulo`, text in pt-BR
through the extension `locale/` directory (falling back to the host language).

---

## 5. Proposed shape of the first extension (`br_rs`)

```
extensions/br_rs/
  extension.yaml        # id, api_version, min_host_version, permissions: [network, register_source, ...]
  locale/pt-BR.json, en.json, zh-CN.json
  sources.py            # SourceEntry-like declarations: cemaden_alerts_rs, cemaden_rain_rs, sgb_sace_rs
  pollers.py            # generic HTTP poller config (interval, url, uf filter, fingerprint)
  parsers.py            # payload → WeatherEvent with metadata.severity_level / codibge / uf
  presenter.py          # pt-BR card/text
  enrichment.py         # ENSO (NOAA ONI) context
```

Host work this requires (all in scope of the extension MVP):

1. A generic HTTP-poll connection type (today each poller is a bespoke service).
2. Structured `severity_level` in `WeatherEvent.metadata`, used by `WeatherRule` and weather aggregation instead of Chinese colour words.
3. Region resolution that a source can provide (UF/IBGE instead of China provinces).
4. Presenter registration and i18n.
5. An open `ProviderFamily`.

### Open questions

- INMET access from outside Brazil (geo-restriction?), and whether WIS2 MQTT is an acceptable transport.
- SACE: permission and stability of scraping the DWR endpoints vs. parsing PDF bulletins.
- Defesa Civil CAP: whether a public feed is published under Portaria MIDR 2.458/2026.
- CEMADEN feed terms of use and rate limits (be conservative: ≥ 5 min polling).

## Sources

- CEMADEN alerts JSON: https://painelalertas.cemaden.gov.br/wsAlertas2
- CEMADEN rain JSON: https://resources.cemaden.gov.br/graficos/interativo/getJson2.php?uf=RS
- CEMADEN datasets doc: https://plataforma-clima-ambiente-readthedocs.readthedocs.io/en/latest/cemaden_data.html
- INMET data access note: https://portal.inmet.gov.br/noticias/saiba-como-acessar-os-dados-meteorológicos-disponíveis-no-site-do-inmet
- INMET in WMO WIS2: https://wis2-gdc.weather.gc.ca/collections/wis2-discovery-metadata/items?q=br-inmet
- Portaria MIDR 2.458/2026 (IDAP → CAP): https://www.lex.com.br/portaria-midr-no-2-458-de-30-de-julho-de-2026/
- Defesa Civil Alerta (Anatel): https://www.gov.br/anatel/pt-br/dados/utilidade-publica/alertas-de-desastres/defesa-civil-alerta
- ANA HidroWebService manual: https://www.gov.br/ana/pt-br/assuntos/monitoramento-e-eventos-criticos/monitoramento-hidrologico/orientacoes-manuais/manuais/manual-hidrowebservice_publica.pdf
- SGB alert systems in RS: https://agenciagov.ebc.com.br/noticias/202608/niveis-dos-rios-apresentam-tendencia-de-reducao-na-bacia-do-rio-uruguai-sgb
- Guaíba alert system: https://www.jornaldocomercio.com/geral/2025/07/1210935-sistema-de-alerta-no-guaiba-consegue-antecipar-cheias.html
- Alerta Rio ArcGIS: https://pgeo3.rio.rj.gov.br/arcgis/rest/services/Geotecnia/Estacoes_AlertaRio/MapServer?f=pjson
- NOAA ONI: https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
- El Niño 2023 bulletin (INMET): https://portal.inmet.gov.br/noticias/el-ni%C3%B1o-2023-boletim-de-novembro
- ENSO and RS floods caveat: https://www.canalrural.com.br/tempo/enchentes-no-rs-nao-estao-associadas-apenas-ao-el-nino/
