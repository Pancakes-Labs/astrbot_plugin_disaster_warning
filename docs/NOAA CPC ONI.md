<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD034 -->
# NOAA CPC ONI

The Oceanic Niño Index (ONI) measures the El Niño and La Niña climate events. The Climate Prediction Center (CPC) of NOAA publishes it as a text file.
The `br_south_disaster` extension reads this file to add background data to its messages.

**Base URL**: `https://www.cpc.ncep.noaa.gov/`

## Important notes

1\. The file is public and needs no key. NOAA gives no SLA.

2\. The file gets one new row each month. The data changes slowly. The extension reads the file one time each 24 hours and keeps the last value. If a request fails, the extension keeps the old value.

3\. The ONI is background data. It is not the cause of a CEMADEN alert. The message text says this.

---

## ONI table

/data/indices/oni.ascii.txt

Get the three-month running mean of the sea surface temperature anomaly in the Niño 3.4 region. The file has one header line and one row for each three-month season.

### Example request

```bash
curl -X GET "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
```

### Example response

The example shows the header and some rows of the recorded sample.

```text
 SEAS  YR   TOTAL   ANOM
  ASO 2024  26.64  -0.12
  SON 2024  26.52  -0.19
  ...
  MJJ 2026  29.02   1.39
  JJA 2026  29.09   1.80
  JAS 2026  29.12   2.16
```

### Response fields

The response is plain text. Each row has four columns that spaces separate.

| Field | Type | Example | Description |
| :--- | :--- | :--- | :--- |
| `SEAS` | string | `JAS` | The code of the three-month season. See "Season codes". |
| `YR` | integer | `2026` | The year of the season. |
| `TOTAL` | number | `29.12` | The mean sea surface temperature of the season, in degrees Celsius. |
| `ANOM` | number | `2.16` | The anomaly of the temperature, in degrees Celsius. This is the ONI value. |

### Season codes

Each code has the first letters of three months.

| Code | Months | Code | Months |
| :--- | :--- | :--- | :--- |
| `DJF` | December, January, February | `JAS` | July, August, September |
| `JFM` | January, February, March | `ASO` | August, September, October |
| `FMA` | February, March, April | `SON` | September, October, November |
| `MAM` | March, April, May | `OND` | October, November, December |
| `AMJ` | April, May, June | `NDJ` | November, December, January |
| `MJJ` | May, June, July | `JJA` | June, July, August |

### Classification in `br_south_disaster`

The extension uses the last row of the file. It reads `ANOM` and applies the bands in `extensions/br_south_disaster/enso.py`. The sign sets the phase. The size sets the strength.

| `ANOM` | Phase | Strength |
| :--- | :--- | :--- |
| Greater than -0.5 and less than 0.5 | Neutral | None |
| 0.5 to less than 1.0 | El Niño | Weak |
| 1.0 to less than 1.5 | El Niño | Moderate |
| 1.5 to less than 2.0 | El Niño | Strong |
| 2.0 or more | El Niño | Very strong |
| -0.5 to greater than -1.0 | La Niña | Weak |
| -1.0 to greater than -1.5 | La Niña | Moderate |
| -1.5 to greater than -2.0 | La Niña | Strong |
| -2.0 or less | La Niña | Very strong |

In the recorded sample, the last row is `JAS 2026` with `ANOM` 2.16. This value gives a very strong El Niño.

The extension adds a line to the message only for the El Niño and La Niña phases. In the neutral phase, it adds no line.
