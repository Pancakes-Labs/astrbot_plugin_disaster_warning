<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD034 -->
# CEMADEN API

CEMADEN is the National Center for Monitoring and Early Warning of Natural Disasters of Brazil.
The `br_rs_disaster` extension reads two public endpoints of CEMADEN. Both endpoints use HTTP GET and need no key.

**Base URL**: `https://painelalertas.cemaden.gov.br/` (alerts) and `https://resources.cemaden.gov.br/` (rain gauges)

## Important notes

1\. The data comes from public sources. CEMADEN gives no SLA. The extension polls each endpoint every 300 seconds by default.

2\. The certificate chain of the server ends at the GlobalSign Root R46. Some system certificate stores do not have this root. The request then fails with `CERTIFICATE_VERIFY_FAILED`. The plugin uses the root certificates of the `certifi` package for all extension requests.

3\. The rain endpoint sends JSON with the content type `text/html`. Do not use a JSON decoder that checks the content type. Read the body as text, then parse it.

4\. All times are Brasília time (UTC-3, no daylight saving time). The only exception is the `atualizado` field of the alerts endpoint, which is in UTC.

5\. The text values `municipio`, `cidade`, `nivel` and `evento` are Portuguese. They are data. The extension does not translate them, but it uses some of them as keys.

---

## Alerts

/wsAlertas2

Get the active hydrological and geological risk alerts for all Brazilian states. The extension keeps only the alerts of the state `RS`.

### Example request

```bash
curl -X GET "https://painelalertas.cemaden.gov.br/wsAlertas2"
```

### Example response

```json
{
 "alertas": [
  {
   "cod_alerta": 3141,
   "datahoracriacao": "2026-10-25 11:20:44.159",
   "ult_atualizacao": "2026-10-25 11:20:44.159",
   "codibge": 4318002,
   "evento": "Risco Hidrológico - Alto",
   "nivel": "Alto",
   "status": 1,
   "uf": "RS",
   "municipio": "SÃO BORJA",
   "latitude": -28.746805688578,
   "longitude": -55.796610881728
  }
 ],
 "atualizado": "28-10-2026 13:21:01 UTC"
}
```

### Response fields

| Field | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `alertas` | array | `[...]` | The list of active alerts. |
| `atualizado` | string | `"28-10-2026 13:21:01 UTC"` | The time of the last update of the list, in UTC. The extension does not use it. |
| `alertas[].cod_alerta` | integer | `3141` | The unique id of the alert. The extension uses it to find new alerts. |
| `alertas[].datahoracriacao` | string | `"2026-10-25 11:20:44.159"` | The time when CEMADEN created the alert, in Brasília time. The extension shows it as the issue time. |
| `alertas[].ult_atualizacao` | string | `"2026-10-25 11:20:44.159"` | The time of the last update of the alert, in Brasília time. The extension uses it as the event time. |
| `alertas[].codibge` | integer | `4318002` | The IBGE code of the municipality. |
| `alertas[].evento` | string | `"Risco Hidrológico - Alto"` | The type of risk and the level. See "Meaning of `evento`". |
| `alertas[].nivel` | string | `"Alto"` | The level of the alert. See "Meaning of `nivel`". |
| `alertas[].status` | integer | `1` | The state of the alert. All alerts in the recorded sample have the value `1`. The extension does not use it. |
| `alertas[].uf` | string | `"RS"` | The code of the Brazilian state. |
| `alertas[].municipio` | string | `"SÃO BORJA"` | The name of the municipality. |
| `alertas[].latitude` | number | `-28.7468` | The latitude of the municipality. |
| `alertas[].longitude` | number | `-55.7966` | The longitude of the municipality. |

### Meaning of `nivel`

| Value | Level in the host | Color | Description |
| :--- | :--- | :--- | :--- |
| `Moderado` | 2 | Yellow | Moderate risk. |
| `Alto` | 3 | Orange | High risk. |
| `Muito Alto` | 4 | Red | Very high risk. |

The extension ignores an alert with any other `nivel` value. The recorded sample has only `Moderado` and `Alto`. The extension code accepts `Muito Alto`, but the sample does not show it.

### Meaning of `evento`

The text has the format `<type of risk> - <level>`. The recorded sample has these values:

| Value | Description |
| :--- | :--- |
| `Risco Hidrológico - Moderado` | Flood risk (hydrological), moderate level. |
| `Risco Hidrológico - Alto` | Flood risk (hydrological), high level. |
| `Movimentos de Massa - Moderado` | Landslide risk (mass movement), moderate level. |

The extension reads only the start of the text. If `evento` starts with `Movimentos de Massa`, the hazard is a landslide. For all other values, the hazard is a flood.

### Fields that `br_rs_disaster` uses

| Field | Use |
| :--- | :--- |
| `uf` | Keep only the value `RS`. |
| `nivel` | Set the severity level (2, 3 or 4). |
| `evento` | Set the hazard (flood or landslide). |
| `cod_alerta` | Make the event id and find new alerts. |
| `ult_atualizacao` | Set the event time. The extension sends the alert again when this value changes. |
| `datahoracriacao` | Show the issue time in the message. |
| `municipio`, `codibge`, `latitude`, `longitude` | Put the place in the message and in the event data. |

The extension ignores `status` and `atualizado`.

---

## Rain gauges

/graficos/interativo/getJson2.php

Get the latest readings of all rain gauges of one state. The query parameter `uf` selects the state. The extension uses `uf=RS`.

### Example request

```bash
curl -X GET "https://resources.cemaden.gov.br/graficos/interativo/getJson2.php?uf=RS"
```

### Example response

The response is a JSON array. This example shows two of the 25 records of the recorded sample.

```json
[
 {
  "idestacao": 4312,
  "uf": "RS",
  "codibge": 4314050,
  "cidade": "PAROBÉ",
  "nomeestacao": "G2-Laranjeiras",
  "ultimovalor": 0,
  "datahoraUltimovalor": "28/10/26 13:10",
  "acc1hr": "-",
  "acc3hr": "-",
  "acc6hr": "-",
  "acc12hr": "-",
  "acc24hr": "-",
  "acc48hr": 9.2,
  "acc72hr": 53.6,
  "acc96hr": 73,
  "tipoestacao": 1,
  "status": 0
 },
 {
  "idestacao": 16916,
  "uf": "RS",
  "codibge": 4321204,
  "cidade": "TAQUARA",
  "nomeestacao": "G2-432120401A",
  "ultimovalor": 0,
  "datahoraUltimovalor": "28/10/26 13:10",
  "acc1hr": "-",
  "acc3hr": "-",
  "acc6hr": "-",
  "acc12hr": "-",
  "acc24hr": 0.2,
  "acc48hr": 15,
  "acc72hr": 31.4,
  "acc96hr": 43.4,
  "tipoestacao": 1,
  "status": 0
 }
]
```

### Response fields

Each array item is one rain gauge. A rain total is a number in millimeters (mm). When the gauge has no data for the period, the value is the string `"-"`.

| Field | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `idestacao` | integer | `4312` | The unique id of the gauge. |
| `uf` | string | `"RS"` | The code of the Brazilian state. |
| `codibge` | integer | `4314050` | The IBGE code of the municipality. |
| `cidade` | string | `"PAROBÉ"` | The name of the municipality. |
| `nomeestacao` | string | `"G2-Laranjeiras"` | The name of the gauge. |
| `ultimovalor` | number | `0` | The last reading of the gauge. |
| `datahoraUltimovalor` | string | `"28/10/26 13:10"` | The time of the last reading, in Brasília time. The format is `dd/mm/yy HH:MM`. |
| `acc1hr` | number or string | `"-"` | The rain total of the last 1 hour. |
| `acc3hr` | number or string | `"-"` | The rain total of the last 3 hours. |
| `acc6hr` | number or string | `"-"` | The rain total of the last 6 hours. |
| `acc12hr` | number or string | `"-"` | The rain total of the last 12 hours. |
| `acc24hr` | number or string | `0.2` | The rain total of the last 24 hours. |
| `acc48hr` | number or string | `15` | The rain total of the last 48 hours. |
| `acc72hr` | number or string | `31.4` | The rain total of the last 72 hours. |
| `acc96hr` | number or string | `43.4` | The rain total of the last 96 hours. |
| `tipoestacao` | integer | `1` | The type of the gauge. All gauges in the recorded sample have the value `1`. |
| `status` | integer | `0` | The state of the gauge. All gauges in the recorded sample have the value `0`. |

### Fields that `br_rs_disaster` uses

The extension uses `uf`, `codibge`, `cidade`, `nomeestacao`, `datahoraUltimovalor`, `acc1hr` and `acc24hr`. It ignores all other fields.

For each municipality, the extension keeps the gauge with the highest level. A gauge meets a level when `acc1hr` or `acc24hr` is equal to or higher than the threshold of that level. The `rain_thresholds` setting sets the thresholds. The defaults are in the [README](../README.md#extensions-config).
