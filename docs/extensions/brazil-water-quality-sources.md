<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD034 -->
# Brazil water quality sources

Status: research notes, 2026-10-28. This document lists the official water quality data
of Brazil for the extensions. Only the state of Rio Grande do Sul (RS) is complete for now.
The extension `br_rs_waterq` uses the first source in section 2.

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
| bandeira | object | | Beach flag data. All values were null on 2026-10-28. `br_rs_waterq` does not use it. |
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

#### How `br_rs_waterq` uses the API

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

## 3. Next steps

1. Find the SISAGUA open data endpoint, and filter it by `uf = RS`.
2. Ask FEPAM for a feed of the surface water results (IQA).
3. Make the same table for Santa Catarina (SC). The SC agency IMA has a bathing water site (`balneabilidade.ima.sc.gov.br`).

## Sources

- FEPAM Balneabilidade web application: http://balneabilidade.rs.gov.br/
- The API used by the application: https://secweb.procergs.com.br/baln/rest/locais
- Last bulletin of the 2025/2026 season (RS government): https://estado.rs.gov.br/ultimo-boletim-do-programa-balneabilidade-2025-2026-indica-que-92-dos-96-pontos-de-analise-estao-proprios-para-banho
- Program description (96 points, 45 municipalities, weekly bulletins): https://www.estado.rs.gov.br/programa-balneabilidade-aponta-que-91-dos-96-locais-de-coleta-estao-proprios-para-banho-em-novo-boletim-divulgado-pelo-estado
- FEPAM bulletins of environmental quality: https://www.fepam.rs.gov.br/boletins-da-qualidade-ambiental
- FEPAM data sent to ANA (Qualiágua): https://www.gov.br/ana/pt-br/assuntos/monitoramento-e-eventos-criticos/qualidade-da-agua/programa-qualiagua/rs/NOTATECNICACERTIFICAO3P_FEPAM_RS.pdf
- MMA Praia Limpa data dictionary: https://dados.mma.gov.br/dataset/77beb804-190c-4d48-9410-bdf529d454bb/resource/e65f4cac-b417-4197-8db2-ad81ca9010d9/download/dicionario_de_dados_praia_limpa_dgat_sqa.pdf
- Porto Alegre SMAMS bathing water report: https://prefeitura.poa.br/smams/noticias/smams-divulga-relatorio-de-balneabilidade-do-lami-e-belem-novo
- SISAGUA open data (study): https://revistas.ufpr.br/academica/article/view/89328
