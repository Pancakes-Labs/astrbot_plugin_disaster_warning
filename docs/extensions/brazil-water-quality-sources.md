<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD034 -->
# Brazil water quality sources

Status: research notes, 2026-10-28. This document lists the official water quality data
of Brazil for the extensions. The three states of the South are complete for now:
Rio Grande do Sul (RS, section 2), Santa Catarina (SC, section 3) and Paraná (PR, section 4).
The extension `br_south_waterq` uses sections 2.1, 3.1 and 4.1. Each state has a provider
module in `extensions/br_south_waterq/providers/`.

Legend:

- ✅: a program can read the data. We tested it on 2026-10-28.
- ⚠️: the data exists, but it needs credentials, scraping or a check.
- ❌: the data is only in PDF files or on web pages.

---

## 1. National sources (to do)

These sources can cover all states. We did not check them yet.

| Organization | Data | Status |
|---|---|---|
| Ministério da Saúde, SISAGUA / Vigiagua | Drinking water monitoring (coliforms, E. coli, free chlorine) for each municipality | ⚠️ Open data exists. We did not find the endpoint. |
| ANA, Qualiágua and HidroWeb | Surface water quality from the state networks. FEPAM sends the RS data to ANA. | ⚠️ HidroWeb needs credentials |
| MMA, Praia Limpa | Bathing water data from states and municipalities | ⚠️ The open data file covers only 2021-04-01 to 2022-09-16 |

---

## 2. Rio Grande do Sul

### 2.1 FEPAM Programa Balneabilidade (bathing water quality) ✅

FEPAM (the state environmental agency) monitors the bathing water of beaches on the sea,
lakes, the Guaíba and rivers. The program started in 1979. In the 2025/2026 season, it
monitored 96 points in 45 municipalities.

- **Base URL**: `https://secweb.procergs.com.br/baln/rest`
- **Operator**: PROCERGS (the data processing center of the RS government) hosts the API for FEPAM.
  The web application `http://balneabilidade.rs.gov.br/` (an Ionic app) uses this API.
- **Authentication**: none. **Format**: JSON. **TLS**: the certificate is valid with the
  system store and with certifi.

#### Important notes

1. The season is from November to February. FEPAM publishes a bulletin each Friday.
   Outside the season, the API keeps the data of the last bulletin.
2. The laboratories are FEPAM (the North coast), Sanep (Pelotas, Laranjal beach) and
   CORSAN (the other points). The field `laboratorio` shows `FEPAM`, `PELOTAS` or `CORSAN`.
3. The text values (`nome`, `nomeMunicipio`) are in Portuguese and in upper case. They are data.
4. The API has no documentation. We found the endpoints and the codes in the code of the web application.

#### Monitoring points

`GET /locais`

This endpoint gives all monitoring points with the result of the last bulletin.

##### Example request

```bash
curl "https://secweb.procergs.com.br/baln/rest/locais"
```

##### Example response

```json
[
  {
    "codLocal": 1026,
    "nome": "PRAIA DA PICADA – LAGO GUAÍBA",
    "latitude": "-30.286383",
    "longitude": "-51.299964",
    "situacao": 2,
    "ibge": "4301909",
    "bandeira": {"ativada": null, "condicao": null, "nro": null, "data": null, "hora": null,
                 "inicioManha": null, "finalManha": null, "inicioTarde": null, "finalTarde": null},
    "ultimaColeta": "24/02/2026",
    "laboratorio": "CORSAN",
    "nomeMunicipio": "BARRA DO RIBEIRO"
  }
]
```

##### Response fields

| Field | Type | Example | Description |
|---|---|---|---|
| codLocal | int | `1026` | The ID of the monitoring point |
| nome | string | `PRAIA DA PICADA – LAGO GUAÍBA` | The name of the beach and of the water body |
| latitude | string | `-30.286383` | The latitude, as text |
| longitude | string | `-51.299964` | The longitude, as text |
| situacao | int | `2` | The result. See the table of codes below. |
| ibge | string | `4301909` | The IBGE code of the municipality |
| bandeira | object | | Beach flag data. All values were null on 2026-10-28. `br_south_waterq` does not use it. |
| ultimaColeta | string | `24/02/2026` | The date of the last sample (dd/mm/yyyy) |
| laboratorio | string | `CORSAN` | The laboratory: `FEPAM`, `PELOTAS` or `CORSAN` |
| nomeMunicipio | string | `BARRA DO RIBEIRO` | The name of the municipality. It is null in `GET /locais/{codLocal}`. |

##### Codes of `situacao`

The web application maps the codes with `condicao = ["NC", "NC", "P", "R"]`.

| Code | Meaning | Text in the application |
|---|---|---|
| 0, 1 | NC: no sample | |
| 2 | P: suitable for bathing | Própria |
| 3 | R: unsuitable for bathing | Imprópria |

On 2026-10-28, the API gave 92 points with code 2 and 4 points with code 3. This agrees with
the last bulletin of the 2025/2026 season.

##### Criterion (CONAMA 274/2000)

The web application gives this criterion:

- **Suitable**: in 80 % or more of the samples of the previous 5 weeks, at the same point,
  the result is a maximum of 1,000 thermotolerant coliforms or 800 E. coli per 100 mL.
- **Unsuitable**: in more than 20 % of the samples of the previous 5 weeks, the result is
  more than 1,000 thermotolerant coliforms or 800 E. coli per 100 mL. A point is also
  unsuitable if the last sample is more than 2,500 thermotolerant coliforms or 2,000 E. coli per 100 mL.

#### One monitoring point

`GET /locais/{codLocal}`

This endpoint gives one point, with the same fields as `GET /locais`.

#### Samples of one point

`GET /coletas/{codLocal}`

This endpoint gives the last samples of one point. The web application sends the request header `qtd: 5`.

```bash
curl "https://secweb.procergs.com.br/baln/rest/coletas/1026" -H "qtd: 5"
```

```json
[
  {"dataColeta": "2026-02-24T07:40:00.000-03:00", "situacao": 2, "semana": 16},
  {"dataColeta": "2026-02-16T08:00:00.000-03:00", "situacao": 2, "semana": 15}
]
```

| Field | Type | Example | Description |
|---|---|---|---|
| dataColeta | string | `2026-02-24T07:40:00.000-03:00` | The time of the sample (ISO 8601, Brasília time) |
| situacao | int | `2` | The result of this sample, with the same codes |
| semana | int | `16` | The week of the season |

#### Municipalities

`GET /municipios` gives `[{"nome": "ALEGRETE", "ibge": 4300406}, ...]`: the municipalities with monitoring points.

#### How `br_south_waterq` uses the API

- It polls `GET /locais` each 6 hours (setting `poll_interval`).
- The first poll records the state of each point. It sends no message.
- Then it sends a message when a new bulletin gives "unsuitable" for a point, or when a point changes from "unsuitable" to "suitable".
- A result with a sample date more than 90 days old is past. The extension does not send it. This limit is fixed (`MAX_AGE_DAYS`).
  Thus, out of the season, the extension sends no message. On 2026-10-28, all 96 points were past (last samples 23/02/2026 and 24/02/2026).

### 2.2 FEPAM surface water quality bulletins ❌

FEPAM monitors the rivers and the lakes of RS with ANA (programa Qualiágua). It publishes
bulletins for each hydrographic region (Guaíba, Litoral, Uruguai, Baixo Jacuí, Santa Maria,
Vacacaí). The bulletins use the water quality index (IQA). They are PDF files at
`https://www.fepam.rs.gov.br/boletins-da-qualidade-ambiental`. The samples are approximately each 6 months.

### 2.3 FEPAM pesticides in surface water ❌

FEPAM has a page about pesticides in surface water: `https://www.fepam.rs.gov.br/monitoramento-de-agrotoxicos`.
The results are in reports, not in a data feed.

### 2.4 Porto Alegre (municipal) ❌

- SMAMS (the environment secretariat of Porto Alegre) publishes the bathing water results of
  the Lami and Belém Novo beaches as news and reports.
- DMAE (the water department of Porto Alegre) publishes drinking water quality reports.

### 2.5 CORSAN ⚠️

CORSAN supplies drinking water to most municipalities of RS and does samples for the Balneabilidade program.
CORSAN is private since 2023. Thus, its own reports are not government data.

---

## 3. Santa Catarina

### 3.1 IMA Balneabilidade (bathing water quality) ✅

IMA (Instituto do Meio Ambiente de Santa Catarina, the state environmental agency) monitors
the bathing water of the sea, lagoons and rivers of the coast. Samples continue all year.
On 2026-10-28, the map had 260 points in 28 municipalities. The newest sample was 27/10/2026.

- **Base URL**: `https://balneabilidade.ima.sc.gov.br`
- **Operator**: IMA. The web site `https://balneabilidade.ima.sc.gov.br/` uses these endpoints
  (jQuery `$.post` calls in the page).
- **Authentication**: none. **Format**: JSON as `text/html`. Some bodies start with a byte order mark.
- **TLS**: the certificate is valid with the system store.

#### Important notes

1. The map endpoint gives the current status of each point, but **no sample date**.
   The samples endpoint gives the dates. It accepts one point for each request.
2. IMA does not document the API. We found the endpoints in the code of the web site.
3. The text values are in Portuguese, in mixed case. They are data.
4. The criterion of the page: a point is suitable when 4 of the last 5 samples have less than
   800 E. coli per 100 mL, and the last sample has less than 2,000 E. coli per 100 mL (CONAMA 274/2000).

#### Current status of all points

`POST /relatorio/mapa` (GET gives the same body)

```bash
curl -X POST "https://balneabilidade.ima.sc.gov.br/relatorio/mapa"
```

```json
[
  {
    "CODIGO": "ca409384-a717-4bdc-8a14-b5fd6ed3cea8",
    "MUNICIPIO_COD_IBGE": "4201950",
    "MUNICIPIO": "Balneário Arroio do Silva",
    "PONTO_NOME": "Ponto 03",
    "BALNEARIO": "Praia do Arroio do Silva",
    "LOCALIZACAO": "Em frente à Avenida Mondardo, lado direito do córrego (no mar)",
    "LATITUDE": "-28.98813600",
    "LONGITUDE": "-49.41199200",
    "CONDICAO": "IMPRÓPRIO"
  }
]
```

| Field | Type | Example | Description |
|---|---|---|---|
| CODIGO | string | `ca409384-…` | The ID of the point (a UUID) |
| MUNICIPIO_COD_IBGE | string | `4201950` | The IBGE code of the municipality |
| MUNICIPIO | string | `Balneário Arroio do Silva` | The name of the municipality |
| PONTO_NOME | string | `Ponto 03` | The name of the point |
| BALNEARIO | string | `Praia do Arroio do Silva` | The name of the beach |
| LOCALIZACAO | string | | The description of the place |
| LATITUDE, LONGITUDE | string | `-28.98813600` | The position, as text |
| CONDICAO | string | `IMPRÓPRIO` | `PRÓPRIO`: suitable. `IMPRÓPRIO`: unsuitable. The site shows other values as unknown. |

On 2026-10-28, the map had 198 points with `PRÓPRIO` and 62 points with `IMPRÓPRIO`.

#### Samples of one point

`POST /relatorio/analisesPonto` with the form field `codigo=<CODIGO>`

```bash
curl -X POST "https://balneabilidade.ima.sc.gov.br/relatorio/analisesPonto" \
  -d "codigo=ca409384-a717-4bdc-8a14-b5fd6ed3cea8"
```

```json
[
  {"DATA": "19/10/2026", "CONDICAO": "IMPRÓPRIO", "CHUVA": "Ausente", "RESULTADO": "6131", "TEMP_AGUA": "15.00"},
  {"DATA": "13/10/2026", "CONDICAO": "IMPRÓPRIO", "CHUVA": "Ausente", "RESULTADO": "19863", "TEMP_AGUA": "17.00"}
]
```

| Field | Type | Example | Description |
|---|---|---|---|
| DATA | string | `19/10/2026` | The date of the sample (dd/mm/yyyy) |
| CONDICAO | string | `IMPRÓPRIO` | The status after this sample |
| CHUVA | string | `Ausente` | Rain in the last 24 hours |
| RESULTADO | string | `6131` | E. coli, in MPN (NMP) per 100 mL |
| TEMP_AGUA | string | `15.00` | The water temperature (°C) |

The endpoint gives the last 5 samples. Do not trust the order. Use the newest `DATA`.

#### Other endpoints (not used)

- `POST /relatorio/exportarCSV` with `municipioID`, `localID` and `ano`: the samples of one year, as CSV (UTF-8 with a byte order mark).
- `GET /relatorio/downloadPDF/<yyyy-mm-dd>`: the PDF bulletin of a date.
- `POST /municipio/getMunicipios`, `POST /local/getLocaisByMunicipio`, `POST /pontoColeta/getPontosByLocal`: lists for the site menus.

#### How `br_south_waterq` uses the API

- It polls `/relatorio/mapa` each 6 hours (setting `poll_interval`). The first poll records the status of each point. It sends no message.
- When the status of a point changes, it calls `/relatorio/analisesPonto` for that point only. The newest sample gives the date and the E. coli result.
- Thus, in SC, a new sample with the same status is not news. The extension sends a message only when a point becomes unsuitable, or suitable again.
- A sample older than 90 days is past. The extension does not send it.

---

## 4. Paraná

### 4.1 IAT Balneabilidade (bathing water quality, ArcGIS layer) ✅

IAT (Instituto Água e Terra, the state environmental agency) monitors the bathing water of the
coast (Litoral) and of the reservoirs and rivers of the West (Costa Oeste) in the summer season.
On 2026-10-28, the layer had 76 points in 14 municipalities, for the season 2025/2026.

- **Base URL**: `https://geopr.iat.pr.gov.br/server/rest/services/Geoprocessamento/balneabilidade/FeatureServer/0`
- **Operator**: IAT, on the GeoPR portal (ArcGIS Enterprise). The IAT page
  `https://www.iat.pr.gov.br/Pagina/Balneabilidade` links to the PDF bulletins and to the map.
- **Authentication**: none. **Format**: ArcGIS JSON (`f=json`) or GeoJSON (`f=geojson`).
- **TLS**: the certificate is valid with the system store.

#### Important notes

1. The layer has **one row for each point** and **one column for each campaign** of the season:
   `b1` to `b11` (the result) and `b1_data` to `b11_data` (the date, in epoch milliseconds).
   The last campaign with a result is the current result.
2. On 2026-10-28, only `b1_data` (18/12/2025) and `b2_data` (26/12/2025) had dates.
   The campaigns `b3` to `b9` had results, but no dates. The PDF bulletins give the dates.
3. IAT uses the same layer each season (`ano1` is the year). The schema can change in a new season.
4. The names of the municipalities have no accents (`Foz do Iguacu`).

#### Query all points

```bash
curl "https://geopr.iat.pr.gov.br/server/rest/services/Geoprocessamento/balneabilidade/FeatureServer/0/query?where=1%3D1&outFields=*&returnGeometry=false&f=json"
```

```json
{
  "features": [
    {
      "attributes": {
        "cod": "GN4", "regiao": "Litoral", "municipio": "Guaratuba",
        "praia_rio": "Balneario Brejatuba", "local": "CANAL DO CAMPING - FOZ",
        "b1": "Impropria", "b1_data": 1766026800000,
        "b2": "Impropria", "b2_data": 1766718000000,
        "b9": "Impropria", "b9_data": null, "b10": "", "b11": "",
        "ano1": "2026", "cod_ibge": "4109609",
        "latitude": -25.91092984, "longitude": -48.57199568
      }
    }
  ]
}
```

| Field | Type | Example | Description |
|---|---|---|---|
| cod | string | `GN4` | The ID of the point |
| regiao | string | `Litoral` | The region: `Litoral`, `Costa Oeste` or `Norte` |
| municipio | string | `Guaratuba` | The name of the municipality |
| praia_rio | string | `Balneario Brejatuba` | The beach or the river |
| local | string | `CANAL DO CAMPING - FOZ` | The description of the place |
| b1 … b11 | string | `Impropria` | The result of the campaign: `Propria` (suitable), `Impropria` (unsuitable) or empty (no sample) |
| b1_data … b11_data | integer or null | `1766718000000` | The date of the campaign, in epoch milliseconds |
| ano1 | string | `2026` | The year of the season |
| cod_ibge | string | `4109609` | The IBGE code of the municipality |
| latitude, longitude | number | `-25.91092984` | The position (SIRGAS 2000) |

The layer gives a maximum of 2,000 rows for each request. `returnCountOnly=true` gives the count.

#### How `br_south_waterq` uses the API

- It polls the query each 6 hours (setting `poll_interval`). The first poll records the result of each point. It sends no message.
- The result of a point is the last campaign with a value. The bulletin ID is `<ano1>-b<N>`.
- The sample date is the newest date of this campaign or of an earlier campaign. The real sample
  is not older than this date. Thus, the 90-day limit never sends an old result as active.
  But it can mark a new result as past when IAT gives no date.
- On 2026-10-28, all results were past (newest date 26/12/2025). The extension sent no message.

---

## 5. Next steps

1. Find the SISAGUA open data endpoint, and filter it by `uf` for the South states.
2. Ask FEPAM for a feed of the surface water results (IQA).
3. Ask IAT to fill the campaign dates (`b3_data` to `b11_data`) in the layer.
4. To add a state, add a provider module in `extensions/br_south_waterq/providers/` and one line in `states.py`.

## Sources

- IMA Balneabilidade web site (SC): https://balneabilidade.ima.sc.gov.br/
- IAT Balneabilidade page (PR): https://www.iat.pr.gov.br/Pagina/Balneabilidade
- IAT GeoPR layer (PR): https://geopr.iat.pr.gov.br/server/rest/services/Geoprocessamento/balneabilidade/FeatureServer/0

- FEPAM Balneabilidade web application: http://balneabilidade.rs.gov.br/
- The API used by the application: https://secweb.procergs.com.br/baln/rest/locais
- Last bulletin of the 2025/2026 season (RS government): https://estado.rs.gov.br/ultimo-boletim-do-programa-balneabilidade-2025-2026-indica-que-92-dos-96-pontos-de-analise-estao-proprios-para-banho
- Program description (96 points, 45 municipalities, weekly bulletins): https://www.estado.rs.gov.br/programa-balneabilidade-aponta-que-91-dos-96-locais-de-coleta-estao-proprios-para-banho-em-novo-boletim-divulgado-pelo-estado
- FEPAM bulletins of environmental quality: https://www.fepam.rs.gov.br/boletins-da-qualidade-ambiental
- FEPAM data sent to ANA (Qualiágua): https://www.gov.br/ana/pt-br/assuntos/monitoramento-e-eventos-criticos/qualidade-da-agua/programa-qualiagua/rs/NOTATECNICACERTIFICAO3P_FEPAM_RS.pdf
- MMA Praia Limpa data dictionary: https://dados.mma.gov.br/dataset/77beb804-190c-4d48-9410-bdf529d454bb/resource/e65f4cac-b417-4197-8db2-ad81ca9010d9/download/dicionario_de_dados_praia_limpa_dgat_sqa.pdf
- Porto Alegre SMAMS bathing water report: https://prefeitura.poa.br/smams/noticias/smams-divulga-relatorio-de-balneabilidade-do-lami-e-belem-novo
- SISAGUA open data (study): https://revistas.ufpr.br/academica/article/view/89328
