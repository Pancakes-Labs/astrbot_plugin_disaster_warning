<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD034 -->
# EQSC API 文档中心

欢迎使用 EQSC API，如有任何疑问，请通过QQ群聊 1053492801 与我们联系。

[点此进入状态监控面板](https://he83e9571.nyat.app:50025/status.html)

**Base URL**:`https://equake.top/`

## 重要声明

1\. 本 API 数据均来自公开渠道。更新间隔：JMA有关数据：实时；地震情报：约30秒；台风、火山情报：约5分钟。

2\. 本 API 会尽力保持可用性，但无法提供任何形式的 SLA 保证。本 API 不对因数据延迟、错误或不完整等情况造成的直接或间接损失负责，使用时请留意。

3\. 本 API 会收集您的邮箱地址，IP 地址等数据用于服务分析等用途，本 API 承诺不向任何第三方泄露。

---

## 创建RefreshToken

/createRefreshToken

使用 /auth 处获取的登录密钥获取用于创建 AccessToken 的 RefreshToken，密钥以 Bearer Token 形式传递。

> **注意**：普通用户通常不需要调用此接口。用户在 EQuake 设置界面获取的访问令牌即为 RefreshToken（以 `ARh.` 开头），可直接用于 `/createAccessToken`。

## 示例请求

```bash
curl -X GET "/createRefreshToken"
 -H "Authorization: Bearer your_login_key"
```

## 示例响应

成功时返回 RefreshToken,有效时间（分钟），失败时返回错误码。

```bash
ARh.ARhhhhhh.ARhhhhhh.ARhhhhhh,259200
```

---

## 创建AccessToken

/createAccessToken

使用 RefreshToken 创建请求数据用的 AccessToken，RefreshToken 以 Bearer Token 形式传递。

## 示例请求

```bash
curl -X GET "/createAccessToken"
 -H "Authorization: Bearer your_refresh_token"
```

## 示例响应

成功时返回 AccessToken,有效时间（秒），失败时返回错误码。

```bash
ATn.ATnnnnnn.ATnnnnnn.ATnnnnnn,3600
```

---

## JMA海啸情报

/jma\_tsunami.json

获取日本气象厅最新海啸情报数据。该请求需要鉴权。

## 示例请求

示例：请求数据

```json
curl -X GET "/jma_tsunami.json"
 -H "Authorization: Bearer your_access_token"
```

## 示例响应

```json
{
    "areas": [
        {
            "name": "北海道太平洋沿岸東部",
            "grade": "Warning",
            "firstHeight": {
                "condition": "第１波の到達を確認"
            },
            "maxHeight": {
                "description": "３ｍ",
                "value": "3"
            },
            "immediate": "false"
        },
        {
            "name": "北海道太平洋沿岸中部",
            "grade": "Warning",
            "firstHeight": {
                "condition": "第１波の到達を確認"
            },
            "maxHeight": {
                "description": "３ｍ",
                "value": "3"
            },
            "immediate": "false"
        },
        {
            "name": "北海道日本海沿岸南部",
            "grade": "Minor",
            "maxHeight": {
                "description": "０．２ｍ未満",
                "value": "0.2"
            },
            "firstHeight": {
                "condition": "不明"
            },
            "immediate": "false"
        }
    ],
    "issueHypocenter": {
        "originTime": "2025/07/30 08:25:00",
        "hypoCenterName": "カムチャツカ半島付近",
        "code": "941",
        "magnitude": "8.7"
    },
    "cancelled": "false",
    "expiresAt": "null",
    "eventID": "20250730082807",
    "time": "2025/07/30 18:30:07",
    "register": "2025/07/30 18:30:07",
    "isTraining": "false"
}
```

## 响应数据字段

| 字段名 | 类型 | 示例 | 描述 |
| --- | --- | --- | --- |
| areas.name | string | `北海道太平洋沿岸東部` | 海啸予报区名 |
| areas.grade | string | `Warning` | 级别，为Minor（若干的海面变动）、Watch（海啸注意报）、Warning（海啸警报）、MajorWarning（大海啸警报）的其中一值 |
| areas.firstHeight.condition | string | `第１波の到達を確認` | 预估到时或到达情况 |
| areas.maxHeight.description | string | `３ｍ` | 预估海啸最大高度 |
| areas.maxHeight.value | string | `3` | 预估海啸最大高度数值 |
| areas.immediate | string | `false` | 无 |
| issueHypocenter.originTime | string | `2025/07/30 08:25:00` | 发震时间 |
| issueHypocenter.hypoCenterName | string | `カムチャツカ半島付近` | 震央地名 |
| issueHypocenter.code | string | `941` | 震央代码 |
| issueHypocenter.magnitude | string | `8.7` | 震级（Mj） |
| cancelled | string | `false` | 有效期是否已结束 |
| expiresAt | string | `null` | 若干的海面变动取消时间（UTC+9） |
| eventID | string | `20250730082807` | 事件ID |
| time | string | `2025/07/30 18:30:07` | 发表时间 |
| register | string | `2025/07/30 18:30:07` | 发表时间 |
| isTraining | string | `false` | 是否为训练报 |

---

## CENC烈度速报列表

/listIntensityReportCENC.json

获取 CENC 烈度速报列表，该请求需要鉴权。

## 请求参数

| 参数名 | 类型 | 示例 | 描述 |
| --- | --- | --- | --- |
| limit | number | `1` | 返回数据数量，可空 |

## 示例请求

示例：请求列表的前两个数据

```bash
curl -X GET "/listIntensityReportCENC.json?limit=2"
 -H "Authorization: Bearer your_access_token"
```

## 示例响应

列表数据

```json
{
    "No1": {
        "eventID": "20251202185622",
        "placeName": "西藏那曲市比如县",
        "magnitude": "4.9",
        "url": "/intensityReportCENC.json?id=20251202185622"
    },
    "No2": {
        "eventID": "20251129065328",
        "placeName": "青海海西州都兰县",
        "magnitude": "4",
        "url": "/intensityReportCENC.json?id=20251129065328"
    }
}
```

## 响应数据字段

| 字段名 | 类型 | 示例 | 描述 |
| --- | --- | --- | --- |
| No\_.eventID | string | `20251202185622` | 事件ID |
| No\_.placeName | string | `西藏那曲市比如县` | 震央地名 |
| No\_.magnitude | string | `4.9` | 震级 |
| No\_.url | string | `/intensityReportCENC.json?id=20251202185622` | 详细URL |

---

## CENC烈度速报

/intensityReportCENC.json

获取 CENC 烈度速报详细数据，该请求需要鉴权。

## 请求参数

| 参数名 | 类型 | 示例 | 描述 |
| --- | --- | --- | --- |
| id | string | `20251202185622` | 请求事件ID |

## 示例请求

示例：请求id为20251202185622的数据

```bash
curl -X GET "/intensityReportCENC.json?id=20251202185622 "
 -H "Authorization: Bearer your_access_token"
```

## 示例响应

详细数据

```json
{
    "eventInfo": {
        "eventID": 20251129065328,
        "placeName": "青海海西州都兰县",
        "longitude": 96.48,
        "latitude": 36.75,
        "depth": 10,
        "magnitude": 4,
        "shockTime": "20251129 06:53:28"
    },
    "intensityReport": {
        "info": "  基于'GB/T177422020中国地震烈度表......",
        "stations": [
            {
                "stationInfo": {
                    "network": "QH",
                    "id": "HD031",
                    "name": "HD031",
                    "type": "SOIL",
                    "vs30": 282.927642822,
                    "province": "青海",
                    "city": "海西州",
                    "county": "都兰县",
                    "town": "宗加镇",
                    "datetime": "20251129 06:53:28"
                },
                "latitude": 36.43,
                "longitude": 96.46,
                "intensity": 4.7,
                "distance": 35.5554653915427,
                "i_pga": 5.2,
                "pga": 39.7,
                "pgaE": 21.8521270751953,
                "pgaN": 39.6974143981934,
                "pgaZ": 8.60245323181152,
                "i_pgv": 4.1,
                "pgv": 1.3,
                "pgvE": 0.494569497512951,
                "pgvN": 1.32149280286641,
                "pgvZ": 0.185401801425941
            }
        ],
        "predictedIntensityContourline": "......"
    }
}
```

## 响应数据字段

| 字段名 | 类型 | 示例 | 描述 |
| --- | --- | --- | --- |
| eventInfo.eventID | number | `20251129065328` | 事件ID |
| eventInfo.placeName | string | `青海海西州都兰县` | 震央地名 |
| eventInfo.longitude | number | `96.48` | 震央经度 |
| eventInfo.latitude | number | `36.75` | 震央纬度 |
| eventInfo.depth | number | `10` | 震源深度 |
| eventInfo.magnitude | number | `4` | 震级 |
| eventInfo.shockTime | string | `20251129 06:53:28` | 发震时间 |
| intensityReport.info | string | `基于'GB/T177422020中国地震烈度表......` | 详细信息 |
| intensityReport.stations.stationInfo.network | string | `QH` | 台站所属网络 |
| intensityReport.stations.stationInfo.id | string | `HD031` | 台站ID |
| intensityReport.stations.stationInfo.name | string | `HD031` | 台站名 |
| intensityReport.stations.stationInfo.type | string | `SOIL` | 台站类型 |
| intensityReport.stations.stationInfo.vs30 | number | `282.927642822` | 台站Vs30 |
| intensityReport.stations.stationInfo.province | string | `青海` | 台站所属省份 |
| intensityReport.stations.stationInfo.city | string | `海西州` | 台站所属市 |
| intensityReport.stations.stationInfo.county | string | `都兰县` | 台站所属县 |
| intensityReport.stations.stationInfo.town | string | `宗加镇` | 台站所属镇 |
| intensityReport.stations.stationInfo.datetime | string | `20251129 06:53:28` | 事件时间 |
| intensityReport.stations.latitude | number | `36.43` | 台站纬度 |
| intensityReport.stations.longitude | number | `96.46` | 台站经度 |
| intensityReport.stations.intensity | number | `4.7` | 台站烈度 |
| intensityReport.stations.distance | number | `35.5554653915427` | 台站震中距 |
| intensityReport.stations.i\_pga | number | `5.2` | 无 |
| intensityReport.stations.pga | number | `39.7` | 无 |
| intensityReport.stations.pgaE | number | `21.8521270751953` | 无 |
| intensityReport.stations.pgaN | number | `39.6974143981934` | 无 |
| intensityReport.stations.pgaZ | number | `8.60245323181152` | 无 |
| intensityReport.stations.i\_pgv | number | `4.1` | 无 |
| intensityReport.stations.pgv | number | `1.3` | 无 |
| intensityReport.stations.pgvE | number | `0.494569497512951` | 无 |
| intensityReport.stations.pgvN | number | `1.32149280286641` | 无 |
| intensityReport.stations.pgvZ | number | `0.185401801425941` | 无 |
| intensityReport.predictedIntensityContourline | string | `......` | 烈度等值线geojson |

---

## NMC台风路径数据

/typhoonNMC.json

获取中国气象局台风路径数据，数据量较大，请尽量使用缓存。该请求需要鉴权。

## 请求参数

| 参数名 | 类型 | 示例 | 描述 |
| --- | --- | --- | --- |
| id | string | `2518` | 获取指定四位id（年+编号）的台风数据，可空，为空时返回至多20个的最新数据 |

## 示例请求

示例：请求列表第1位的台风数据

```bash
curl -X GET "/typhoonNMC.json?id=2518"
 -H "Authorization: Bearer your_access_token"
```

## 示例响应

台风数据

```json
{
  "typhoon": [
    {
      "id": "2518",
      "nameEN": "RAGASA",
      "nameCN": "桦加沙",
      "isActive": false,
      "historyTrack": [
        {
          "time": "2025/09/22 23:00:00",
          "type": "SuperTY",
          "typeNameCN": "超强台风",
          "latitude": 19.5,
          "longitude": 119.9,
          "pressure": 920,
          "speed": "NULL",
          "direction": "NULL",
          "directionCN": "NULL",
          "windSpeed": 58,
          "windCircle": {
            "30KTS": {
              "NE": 480,
              "SE": 340,
              "SW": 340,
              "NW": 480
            },
            "50KTS": {
              "NE": 180,
              "SE": 160,
              "SW": 160,
              "NW": 180
            },
            "64KTS": {
              "NE": 90,
              "SE": 80,
              "SW": 80,
              "NW": 90
            }
          }
        }
      ],
      "futureTrack": [
        {
          "time": "2025/09/25 23:00:00",
          "type": "TD",
          "typeNameCN": "热带低压",
          "latitude": 21.9,
          "longitude": 106.1,
          "pressure": 1003,
          "windSpeed": 12
        }
      ]
    }
  ]
}
```

## 响应数据字段

| 字段名 | 类型 | 示例 | 描述 |
| --- | --- | --- | --- |
| typhoon.id | string | `2518` | 四位台风ID，年份2位+编号2位 |
| typhoon.nameEN | string | `RAGASA` | 台风英文名 |
| typhoon.nameCN | string | `桦加沙` | 台风中文名 |
| typhoon.isActive | boolean | `false` | 是否活跃 |
| typhoon.historyTrack.time | string | `2025/09/22 23:00:00` | 节点时间 |
| typhoon.historyTrack.type | string | `SuperTY` | 台风级别 |
| typhoon.historyTrack.typeNameCN | string | `超强台风` | 台风级别中文名 |
| typhoon.historyTrack.latitude | number | `19.5` | 中心纬度 |
| typhoon.historyTrack.longitude | number | `119.9` | 中心经度 |
| typhoon.historyTrack.pressure | number | `920` | 中心气压 |
| typhoon.historyTrack.speed | string | `NULL` | 移动速度，数值或STNR（ALMOST STATIONARY） |
| typhoon.historyTrack.direction | string | `NULL` | 移动方向 |
| typhoon.historyTrack.directionCN | string | `NULL` | 中文 移动方向 |
| typhoon.historyTrack.windSpeed | number | `58` | 最大风速 |
| typhoon.historyTrack.windCircle.30KTS | object | | 7级风圈半径，带有NE、SE、SW、NW四个数值字段 |
| typhoon.historyTrack.windCircle.50KTS | object | | 10级风圈半径，带有NE、SE、SW、NW四个数值字段 |
| typhoon.historyTrack.windCircle.64KTS | object | | 12级风圈半径，带有NE、SE、SW、NW四个数值字段 |
| typhoon.futureTrack.time | string | `2025/09/25 23:00:00` | 节点时间 |
| typhoon.futureTrack.type | string | `TD` | 台风级别 |
| typhoon.futureTrack.typeNameCN | string | `热带低压` | 台风级别中文名 |
| typhoon.futureTrack.latitude | number | `21.9` | 中心经度 |
| typhoon.futureTrack.longitude | number | `106.1` | 中心纬度 |
| typhoon.futureTrack.pressure | number | `1003` | 中心气压 |
| typhoon.futureTrack.windSpeed | number | `12` | 最大风速 |

---

## JMA活跃火山数据

/volcanoJMA.json

获取 JMA 活跃火山数据，数据量较大，请尽量使用缓存。该请求需要鉴权。

## 示例请求

```bash
curl -X GET "/volcanoJMA.json"
 -H "Authorization: Bearer your_access_token"
```

## 示例响应

火山数据

```json
{
  "volcanoes": [
    {
      "volcanoInfo": {
        "code": "101",
        "lat": "44.133",
        "lng": "145.161",
        "name": "知床硫黄山"
      },
      "typeCode": "11",
      "typeName": "活火山であることに留意",
      "reportTime": "null"
    }
  ]
}
```

## 响应数据字段

| 字段名 | 类型 | 示例 | 描述 |
| --- | --- | --- | --- |
| volcanoes.volcanoInfo.code | string | `101` | 火山代码 |
| volcanoes.volcanoInfo.lat | string | `44.133` | 火山纬度 |
| volcanoes.volcanoInfo.lng | string | `145.161` | 火山经度 |
| volcanoes.volcanoInfo.name | string | `知床硫黄山` | 火山名 |
| volcanoes.typeCode | string | `11` | 火山级别代码 |
| volcanoes.typeName | string | `活火山であることに留意` | 火山级别名 |
| volcanoes.reportTime | string | `null` | 发表时间（UTC+9） |
