# Survey of Brazilian data sources (start of the first regional extension)

Status: research notes, 2026-10-28. Context: upstream issue 153 (extension system).

Brazil is too large for one "Brazil" source. Thus, there is one extension for each area
(state, "UF"). Each extension uses the national feeds and filters them by the UF or by the
IBGE municipality code.

Contents:

1. How the plugin gets the JP and CN data (the template)
2. Brazilian organizations and feeds (national and state)
3. The first state
4. How the Brazilian data agrees with the host event model
5. The structure of the first extension

---

## 1. How the plugin gets the JP and CN data

Each source is a `SourceEntry` in `core/sources/source_catalog.py`. The data comes to the
pipeline through one of two transports:

| Transport | Provider family | Endpoint and authentication | Notes |
|---|---|---|---|
| WebSocket | `FAN_STUDIO` | `wss://ws.fanstudio.tech/all` (backup `.hk`). The client sends `{"type":"auth","appId","key"}`. The key is `data_sources.fan_studio.api_key`. | The only CN source for weather alarms and typhoons |
| WebSocket | `P2P` | `wss://api.p2pquake.net/v2/ws`. No authentication. | JMA. `code` 556 is EEW, 551 is a report, 552 is a tsunami. |
| WebSocket | `WOLFX` | `wss://ws-api.wolfx.jp/all_eew`. No authentication. | The router uses the `type` field (`jma_eew`, `cenc_eew`, ...). |
| WebSocket | `JIAN_PROJECT` | `wss://api.sismotide.top/all`. Token chain: `lk_` → `rt_` → `at_`. | |
| WebSocket | `GLOBAL_QUAKE` | `wss://api.aloys23.link/api/v1/alert/ws/all` | |
| HTTP poll | `EQSC` | `https://equake.top`. A bearer refresh token gives an access token. | The default interval is 120 s (from 30 s to 600 s). JMA tsunami, CMA typhoon, CENC intensity. |
| HTTP poll | `DIRECT_HTTP` | MSIL S-Net tiles. No authentication. | `SnetPollService`. The default interval is 60 s. |

How the host sets up a WebSocket source:

- `SourceEntry.build_connection_plan()` describes the connection.
- `ConnectionPlanBuilder` makes one connection for each `connection_group`. It does this only for active sources.
- `SourceMessageRouter.register_all` registers a fixed set of handlers.
- `websocket_reconnect_service.py` does the reconnection. It tries again, then it uses the backup URL. Then it tries again after a long period.

Each HTTP poller is a **separate service that a person wrote by hand** (`SnetPollService`,
`Eqsc*PollService`). There is no general "HTTP poll plan". A `SourceEntry` without a
`connection_url` does not open a connection. All the Brazilian feeds below use HTTP. Thus,
the extension system needs a **general poller** (interval, fetch, parse, fingerprint).
The MVP contains this poller.

The China weather alarm is the nearest example of a Brazilian rain alert. Its flow is:

```
FAN WS message → route_fan_studio_message → _is_source_routable (group/source/parser)
  → WeatherAlarmParser → WeatherEvent(title, headline, effective_at, metadata{province,…})
  → EventEnvelope → _handle_disaster_event → EventPipeline.handle
  → (optional weather aggregation for each session) → RuleChain (WeatherRule…)
  → WeatherAlertPresenter ("weather_cn") → push
```

### Code that is specific to China or Japan

A Brazilian source has problems with this code:

- **The host finds the severity from Chinese color words in the title.**
  - `core/rules/weather_rule.py:78` looks for 红色/橙色/黄色/蓝色/白色 (red/orange/yellow/blue/white).
    A Portuguese title becomes "white" (the lowest level).
  - `weather_aggregation_service.py` has the same map.
  - Fix: add `metadata["severity_level"]`. `WeatherRule` and the aggregation use this value.
- **The presenter header is fixed.**
  - `weather_presenter.py:71` writes `[气象预警] 中国气象局` ("[Weather warning] China Meteorological Administration").
  - The default display time zone is UTC+8.
  - Fix: presenter registration and i18n (a requirement of the maintainer).
- **The region resolution is for China only.** See `utils/china_regions.py`,
  `weather_region_resolver.py` and the province match in the keyword filter.
  A Brazilian source needs a UF/IBGE resolver.
- **Some code checks the text of the source ID:**
  - `base_parser.py:139` checks `startswith("china_weather")`.
  - The host gives the `weather_alarm` log stream to each ID that contains `weather`.
  - `parser_registry.py` has a special case for `china_weather_jianproject`.
- **The icons and code maps** (`weather_alarm_code_map.py`, `WEATHER_EMOJI_MAP`) use CMA
  codes and Chinese keywords (暴雨 rainstorm, 内涝 urban flood).
- **`ProviderFamily` is a closed Enum**, and the list of WebSocket handlers is fixed.

The extension MVP must change these items before a Brazilian weather source shows correctly.

---

## 2. Brazilian organizations and feeds

Legend:

- ✅: the feed worked from outside Brazil on 2026-10-28.
- ⚠️: the feed exists, but it needs credentials, scraping or a Brazilian IP address.
- ❌: we did not find a feed that a program can read.

### National feeds (you can filter them by UF or IBGE code)

| Organization | Data | Access | Status |
|---|---|---|---|
| **CEMADEN** (the national center for disaster monitoring and alerts, MCTI) | Active **risk alerts** for municipalities: hydrological risk (floods) and mass movement (landslides). The levels are `Moderado`, `Alto` and `Muito Alto`. | `GET https://painelalertas.cemaden.gov.br/wsAlertas2` gives JSON `{alertas:[{cod_alerta, datahoracriacao, ult_atualizacao, codibge, evento, nivel, status, uf, municipio, latitude, longitude}]}` | ✅ public, no authentication |
| CEMADEN | **Rain gauges** for each UF, with totals for 1/3/6/12/24/48/72/96 h | `GET https://resources.cemaden.gov.br/graficos/interativo/getJson2.php?uf=RS` | ✅ public, no authentication |
| CEMADEN | The hourly series of one station | `GET https://mapservices.cemaden.gov.br/MapaInterativoWS/resources/horario/{idEstacao}/{n}` | ✅ |
| **INMET** (the national meteorological institute) | Official **weather warnings** (CAP format). The levels are `Perigo Potencial`, `Perigo` and `Grande Perigo` (yellow, orange and red). Portal: `alertas2.inmet.gov.br`. API: `apiprevmet3.inmet.gov.br/avisos/ativos`. INMET also sends the warnings to WMO WIS2 (`wis2bra.inmet.gov.br`, `urn:wmo:md:br-inmet:alerts`). | The DNS name resolves, but the API **timed out** from our network (outside Brazil). The WIS2 node sent 200. | ⚠️ Test from a Brazilian IP address. WIS2 (MQTT) is a possible alternative. |
| **SGB / CPRM** (the geological survey), SACE system | River level alerts with three levels (attention, alert, flood), forecasts and PDF bulletins | One web application for each basin (`sace.sgb.gov.br/<basin>/`). It uses a Java RPC framework (DWR) internally. There is no documented API. | ⚠️ needs reverse engineering |
| **ANA** (the national water agency), HidroWeb and Hidro-Telemetria | Telemetry of river level, discharge and rain (each 15 min or each hour) | HidroWebService API v2. It needs credentials. Send an e-mail to hidro@ana.gov.br to get them. | ⚠️ credentials |
| **Defesa Civil Nacional** (the national civil defense), IDAP system | Official civil defense alerts (cell broadcast, SMS 40199). Portaria MIDR 2.458/2026 tells the organization to keep the alerts in **CAP** format and to make them public. | We did not find a public feed. Only civil defense organizations can use IDAP. Anatel has a public data panel. | ❌ for now, but this is the most official feed to monitor |
| **INPE / CPTEC** (the space research institute and its weather center) | Forecasts, radar, climate bulletins | Mostly web products | ❌ for alerts |
| **NOAA CPC** (United States) | **El Niño (ENSO) state**: the ONI index and the monthly discussion | `https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt` (plain text) | ✅ |

The ENSO state on 2026-10-28: the last ONI values are MJJ +1.39, JJA +1.80 and **JAS 2026 +2.16 °C**.
This is a **strong El Niño**. El Niño increases the rain in southern Brazil. It makes a flood
more probable, but it is not the direct cause of one flood. Thus, the South is the best first area.

### State feeds (examples)

| State | Organizations | Access for a program |
|---|---|---|
| **RS** | Defesa Civil RS; the SEMA situation room; SGB SACE river systems for **Taquari, Caí, Uruguai and Guaíba** (Guaíba started in June 2025; UFRGS built it) | SACE ⚠️ (scraping); CEMADEN ✅ (455 gauges) |
| **SC** | Defesa Civil SC (good alert practice); Epagri/Ciram; AlertaBlu (Blumenau); CEOPS/FURB (Itajaí basin) | We did not find a public API. CEMADEN ✅ (443 gauges) |
| **RJ** | Alerta Rio (Geo-Rio, city of Rio); INEA | Alerta Rio stations through ArcGIS REST ✅ (`pgeo3.rio.rj.gov.br/.../Estacoes_AlertaRio/MapServer`; we checked only the metadata). CEMADEN ✅ (391) |
| **SP** | CGE São Paulo (the emergency center of the city); Defesa Civil SP; SAISP | We did not find a public API. CEMADEN ✅ (724) |
| **MG** | IGAM; Defesa Civil MG; SGB SACE Rio Doce | CEMADEN ✅ (825, the largest number of all states) |
| **PR** | Simepar; Defesa Civil PR | simepar.br is behind Cloudflare (403). CEMADEN ✅ (213) |

The number of CEMADEN rain gauges in each UF (live count, 2026-10-28):
MG 825 · SP 724 · BA 501 · RS 455 · SC 443 · PE 440 · RJ 391 · CE 305 · PR 213 · ES 205 ·
MA 178 · PB 139 · PI 133 · AL 115 · AM 95 · RN 89 · PA 83 · GO 81 · SE 57 · AC 53 ·
AP 40 · TO 38 · RO 35 · MS 29 · MT 28 · RR 13 · DF 5.

The active CEMADEN alerts on 2026-10-28: 15 in total (BA 8, RS 3, SP 1, MG 1, MT 1, GO 1).
This is one sample. It is not a statistic.

---

## 3. The first state

**Recommendation: Rio Grande do Sul (RS).** The second area is Santa Catarina (SC).

MG and SP have more rain gauges. But for **heavy rain, floods and El Niño**, RS has the best set of data:

1. **El Niño signal.** The South has the clearest relation between ENSO and rain in Brazil.
   A strong El Niño is active now (ONI +2.16). The floods of 2023 (Uruguai river, rain more
   than 300 mm above normal) and 2024 (the RS disaster) are good historical events for tests.
2. **Flood data.** There are four SGB river alert systems (Taquari, Caí, Uruguai, Guaíba).
   Each system has official attention, alert and flood levels. These levels agree with the warning levels.
3. **Rain data.** There are 455 CEMADEN gauges with totals for many periods. They are
   sufficient for heavy rain warnings with limits (for example, 24 h and 72 h totals).
4. **Official alerts.** The CEMADEN hydrological alerts already include RS municipalities
   (3 alerts were active at the time of this survey).

SC is a good second area. It has a similar El Niño risk, a history of floods in the Itajaí
valley and 443 gauges. Most of its feeds are national (CEMADEN). Thus, an SC extension is
easy to make after the RS extension. MG and SP are good areas for a later step (urban flash floods and landslides).

---

## 4. How the Brazilian data agrees with the host event model

| Brazilian data | Host concept | Notes |
|---|---|---|
| CEMADEN alert (`evento`, `nivel`) | `WeatherEvent` (an existing disaster type, as the issue tells) | `Moderado` → yellow, `Alto` → orange, `Muito Alto` → red. `cod_alerta` and `ult_atualizacao` make the fingerprint and the event_id. |
| INMET CAP warning | `WeatherEvent` | `Perigo Potencial`, `Perigo`, `Grande Perigo` → yellow, orange, red |
| SGB river level compared with the alert levels | `WeatherEvent` (flood) | attention, alert, flood → yellow, orange, red |
| CEMADEN rain total above a limit | `WeatherEvent` (heavy rain), made by the extension | The limits are settings of the extension. |
| ENSO / ONI | **Background data**, not an event | The extension adds a line to rain and flood messages (for example, "a strong El Niño is active"). |

Region keys: the UF and the IBGE municipality code (`codibge`). Time zone: `America/Sao_Paulo`.
The text comes from the `locale/` directory of the extension (English and Chinese for now).

---

## 5. The structure of the first extension (`br_rs`)

```
extensions/br_rs/
  extension.yaml        # id, api_version, min_host_version, permissions: [network]
  locale/en.json, zh-CN.json
  extension.py          # sources, pollers, parsers and envelopes
  presenter.py          # the text of the message
  enso.py               # the ENSO (NOAA ONI) background data
```

This extension needs these changes in the host (all are in the MVP):

1. A general HTTP poller (now each poller is a separate service).
2. A `severity_level` value in `WeatherEvent.metadata`. `WeatherRule` and the weather
   aggregation use it, not the Chinese color words.
3. A region resolution that a source can supply (UF/IBGE, not China provinces).
4. Presenter registration and i18n.
5. An open `ProviderFamily`.

### Open questions

- Can a program outside Brazil use INMET (is there a geographical block)? Is WIS2 MQTT an acceptable transport?
- SACE: do we have permission to scrape the DWR endpoints? Is this stable? The alternative is to read the PDF bulletins.
- Defesa Civil CAP: does the organization publish a public feed because of Portaria MIDR 2.458/2026?
- CEMADEN: what are the terms of use and the rate limits? Use a conservative interval (5 min or more).

## Sources

- CEMADEN alerts JSON: https://painelalertas.cemaden.gov.br/wsAlertas2
- CEMADEN rain JSON: https://resources.cemaden.gov.br/graficos/interativo/getJson2.php?uf=RS
- CEMADEN datasets documentation: https://plataforma-clima-ambiente-readthedocs.readthedocs.io/en/latest/cemaden_data.html
- INMET data access note: https://portal.inmet.gov.br/noticias/saiba-como-acessar-os-dados-meteorológicos-disponíveis-no-site-do-inmet
- INMET in WMO WIS2: https://wis2-gdc.weather.gc.ca/collections/wis2-discovery-metadata/items?q=br-inmet
- Portaria MIDR 2.458/2026 (IDAP and CAP): https://www.lex.com.br/portaria-midr-no-2-458-de-30-de-julho-de-2026/
- Defesa Civil Alerta (Anatel): https://www.gov.br/anatel/pt-br/dados/utilidade-publica/alertas-de-desastres/defesa-civil-alerta
- ANA HidroWebService manual: https://www.gov.br/ana/pt-br/assuntos/monitoramento-e-eventos-criticos/monitoramento-hidrologico/orientacoes-manuais/manuais/manual-hidrowebservice_publica.pdf
- SGB alert systems in RS: https://agenciagov.ebc.com.br/noticias/202608/niveis-dos-rios-apresentam-tendencia-de-reducao-na-bacia-do-rio-uruguai-sgb
- Guaíba alert system: https://www.jornaldocomercio.com/geral/2025/07/1210935-sistema-de-alerta-no-guaiba-consegue-antecipar-cheias.html
- Alerta Rio ArcGIS: https://pgeo3.rio.rj.gov.br/arcgis/rest/services/Geotecnia/Estacoes_AlertaRio/MapServer?f=pjson
- NOAA ONI: https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
- El Niño 2023 bulletin (INMET): https://portal.inmet.gov.br/noticias/el-ni%C3%B1o-2023-boletim-de-novembro
- ENSO and the RS floods: https://www.canalrural.com.br/tempo/enchentes-no-rs-nao-estao-associadas-apenas-ao-el-nino/
