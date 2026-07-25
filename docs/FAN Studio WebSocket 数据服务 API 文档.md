<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD051 -->
# FAN Studio WebSocket 数据服务 API 文档

WebSocket API 将在服务端收到新消息后自动向所有客户端推送相关信息。

**正式服务版本：** 2.5.2

**正式服务器地址：** `wss://ws.fanstudio.tech/[路径]`

**备用服务器地址：** `wss://ws.fanstudio.hk/[路径]`

**心跳间隔：** 服务端将在每分钟和建立连接后发送一个 heartbeat 心跳包以保持连接，客户端可选回复 ping 包。

## 可用路径

| 路径 (Path) | 说明 |
| --- | --- |
| `/all` | `接收全部源推送` |
| `/weatheralarm` | [`中国气象局气象预警`](#weatheralarm) |
| `/tsunami` | [`自然资源部海啸预警中心海啸预警信息`](#tsunami) |
| `/typhoon` | [`实时活跃台风`](#typhoon) |
| `/cenc` | [`中国地震台网地震信息`](#cenc) |
| `/cenc-ir` | [`中国地震台网烈度速报`](#cenc-ir) |
| `/cea` | [`中国地震预警网地震预警`](#cea) |
| `/cea-pr` | [`中国地震预警网各省级网地震预警`](#cea-pr) |
| `/ningxia` | [`宁夏自治区地震局地震信息`](#ningxia) |
| `/guangxi` | [`广西壮族自治区地震局地震信息`](#guangxi) |
| `/shanxi` | [`山西省地震局地震信息`](#shanxi) |
| `/beijing` | [`北京市地震局地震信息`](#beijing) |
| `/yunnan` | [`云南省地震局地震信息`](#yunnan) |
| `/cwa` | [`台湾省气象署地震报告`](#cwa) |
| `/cwa-eew` | [`台湾省气象署地震预警`](#cwa-eew) |
| `/jma` | [`日本气象厅地震预警`](#jma) |
| `/hko` | [`香港天文台地震信息`](#hko) |
| `/usgs` | [`美国地质调查局地震信息`](#usgs) |
| `/sa` | [`美国 ShakeAlert 地震预警`](#sa) |
| `/emsc` | [`欧洲地中海地震中心地震信息`](#emsc) |
| `/bcsf` | [`法国中央地震研究所地震信息`](#bcsf) |
| `/gfz` | [`德国地学研究中心地震信息`](#gfz) |
| `/usp` | [`巴西圣保罗大学地震信息`](#usp) |
| `/kma` | [`韩国气象厅地震信息`](#kma) |
| `/kma-eew` | [`韩国气象厅地震预警`](#kma-eew) |
| `/kma-station` | [`韩国气象厅 PEWS 测站实时数据`](#kma-station) |
| `/fssn` | [`FSSN 地震信息`](#fssn) |
| `/fssn-cmt` | [`FSSN 地震矩心矩张量解`](#fssn-cmt) |

## 消息协议

### 服务器推送消息

**初始数据 (initial)**

```json
{
  "type": "initial",
  "data": { ... }
}
```

**数据更新 (update)**

```json
{
  "type": "update",
  "data": { ... }
}
```

**心跳包 (heartbeat)**

```json
{
  "type": "heartbeat",
  "ver": "1.1.0",
  "id": "uuid",
  "timestamp": 1630000000000
}
```

### 客户端请求

**查询数据 (query)**

```json
"query" 或 { "type": "query" }
```

响应示例：

```json
{
  "type": "query_response",
  "data": { ... }
}
```

**心跳检测 (ping)**

```json
"ping" 或 { "type": "ping" }
```

服务器响应：

```json
{ "type": "pong", "timestamp": 1630000000000 }
```

## 注意事项

- 客户端需自行实现断线重连逻辑。
- 数据格式与对应业务接口一致。

---

## 全部数据源聚合 /all

**功能：** 聚合所有可用数据源的实时推送，一次连接接收全部数据。

**更新频率：** 随各数据源实时推送，无固定间隔。

**说明：** `/all` 本身不轮询外部 API，而是在连接时发送全部数据源的最新快照，之后实时推送各数据源的增量更新。默认排除 `/cenc-ir` 和 `/kma-station` ，需单独连接。

## 消息格式

### 1\. 初始全量数据 (initial\_all)

连接建立后立即发送，包含所有数据源的最新快照。

```json
{
  "type": "initial_all",
  "weatheralarm": { "Data": { ... }, "md5": "..." },
  "tsunami":      { "Data": { ... }, "md5": "..." },
  "typhoon":      { "Data": { ... }, "md5": "..." },
  "cenc":         { "Data": { ... }, "md5": "..." },
  "cea":          { "Data": { ... }, "md5": "..." },
  "cea-pr":       { "Data": { ... }, "md5": "..." },
  "ningxia":      { "Data": { ... }, "md5": "..." },
  "guangxi":      { "Data": { ... }, "md5": "..." },
  "shanxi":       { "Data": { ... }, "md5": "..." },
  "beijing":      { "Data": { ... }, "md5": "..." },
  "yunnan":       { "Data": { ... }, "md5": "..." },
  "cwa":          { "Data": { ... }, "md5": "..." },
  "cwa-eew":      { "Data": { ... }, "md5": "..." },
  "jma":          { "Data": { ... }, "md5": "..." },
  "hko":          { "Data": { ... }, "md5": "..." },
  "usgs":         { "Data": { ... }, "md5": "..." },
  "sa":           { "Data": { ... }, "md5": "..." },
  "emsc":         { "Data": { ... }, "md5": "..." },
  "bcsf":         { "Data": { ... }, "md5": "..." },
  "gfz":          { "Data": { ... }, "md5": "..." },
  "usp":          { "Data": { ... }, "md5": "..." },
  "kma":          { "Data": { ... }, "md5": "..." },
  "kma-eew":      { "Data": { ... }, "md5": "..." },
  "fssn":         { "Data": { ... }, "md5": "..." },
  "fssn-cmt":     { "Data": { ... }, "md5": "..." }
}
```

每个 key 为去掉 `/` 前缀的路径名，值为 `{ Data, md5 }` 格式的快照数据。

### 2\. 增量更新 (update)

当某个数据源检测到变化时，服务端广播增量消息。包含 `source` 字段标识来源。

```json
{
  "type": "update",
  "source": "cenc",
  "Data": {
    "id": 2024031901,
    "eventId": "CEIC.2024031901",
    "shockTime": "2024-03-19 08:15:32",
    "createTime": "2024-03-19 08:20:00",
    "latitude": 39.5,
    "longitude": 116.7,
    "depth": 10.0,
    "magnitude": 4.5,
    "placeName": "河北廊坊市",
    "infoTypeName": "[正式测定]"
  },
  "md5": "a1b2c3d4e5f6..."
}
```

### 3\. 查询响应 (query\_response)

客户端发送 `{"type": "query"}` 后，服务端返回与 `initial_all` 相同结构的全量快照。

```json
{
  "type": "query_response",
  "cenc": { "Data": { ... }, "md5": "..." },
  "cea":  { "Data": { ... }, "md5": "..." }
  // ... 所有可用数据源
}
```

### 4\. 心跳包 (heartbeat)

服务端定期发送心跳以保持连接，客户端可选回复 `ping` 。

```json
{
  "type": "heartbeat",
  "ver": "2.5.0",
  "id": "uuid-client-id",
  "timestamp": 1710835200000
}
```

## 排除的数据源

以下数据源不会通过 `/all` 推送，需要单独连接对应路径：

| 路径 | 说明 |
| --- | --- |
| `/cenc-ir` | 中国地震台网烈度速报 |
| `/kma-station` | 韩国气象厅 PEWS 测站实时数据 |

## 注意事项

- 增量更新只包含发生变化的数据源，未变化的数据源不会出现在 `update` 消息中。
- `query` 请求会返回完整的全量快照，与 `initial_all` 结构一致，可用于客户端重新同步状态。
- 如需获取 `/cenc-ir` 或 `/kma-station` 数据，请另外建立独立 WebSocket 连接。
- 各数据源的 `Data` 结构详见对应路径的独立文档页面。

---

## 中国气象局气象预警 /weatheralarm

**数据来源：** 中国气象局实时气象预警信息  
**更新频率：** 收到新预警后立即推送  
**示例返回：**

```json
{
  "Data": {
    "id": "620421-20260710023227-11B2002",
    "headline": "靖远县气象台继续发布雷雨大风黄色预警信号",
    "effective": "2026-07-10 02:32:27",
    "description": "靖远县气象台2026年07月10日02时32分继续发布雷雨大风黄色预警信号：预计6小时内，我县...",
    "longitude": 104.67786,
    "latitude": 36.5623,
    "type": "11B20_yellow",
    "title": "靖远县气象台继续发布雷雨大风黄色预警信号"
  },
  "md5": "93b5b90a2e54c1d17142f6588b3b2ec2"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 预警唯一标识符 |
| `headline` | string | 预警标题 |
| `effective` | string | 生效时间（UTC+8） |
| `description` | string | 预警详细描述 |
| `longitude` | number \| null | 预警区域中心经度，可能为空 |
| `latitude` | number \| null | 预警区域中心纬度，可能为空 |
| `type` | string | 预警类型编码，可根据此编码查询预警图标。图标接口：`/we/img/alarm_icon.php?type={type}` |

## 注意事项

- 经纬度为预警区域的中心点，实际影响范围需结合业务逻辑处理。

---

## 自然资源部海啸预警中心 /tsunami

**数据来源：** 自然资源部海啸预警中心  
**更新频率：** 收到最新动态后立即推送  
**说明：** 当级别为“信息”时仅返回基础参数；当级别为“警报”或“解除”时，会返回预报及实况数据。

## 示例返回

### 1\. 海啸消息 (Level: 信息)

仅提供基本参数，不含具体预报或监测数据。

```json
{
  "Data": {
    "id": "85585680e5aa4c22bf6b08ba087eac17",
    "code": "202512272305",
    "warningInfo": {
      "title": "海啸信息",
      "level": "信息",
      "subtitle": "中国台湾省周边海域",
      "orgUnit": "自然资源部海啸预警中心"
    },
    "timeInfo": {
      "alarmDate": "2025-12-28 02:25:45",
      "updateDate": "2025-12-28 01:53:10"
    },
    "shockInfo":{
      "shockTime":"2025-12-27 23:05",
      "latitude":24.67,
      "longitude":122.06,
      "depth":60,
      "magnitude":6.6,
      "placeName":"中国台湾省周边海域"
    },
    "details": {
      "batch": "3",
      "logoUrl": "http://obs.nmefc.cn/CMS/warnLogo/海啸消息灰.png",
      "htmlUrl": "https://obs.nmefc.cn/Warning/TsunamiAdvice/202512272305_3_file/202512272305_3.html",
      "maps": {
        "earthquakeMapUrl": "https://obs.nmefc.cn/Warning/TsunamiAdvice/202512272305_3_file/Earthquake_Pos.jpg",
        "amplitudeMapUrl": "",
        "coastalMapUrl": ""
      }
    },
    "forecasts": [],
    "waterLevelMonitoring": []
  },
  "md5": "a45588e9382c33c3422885ab9bcc611e"
}
```

### 2\. 海啸预警 (Level: 黄色/橙色/红色/蓝色)

```json
{
  "Data": {
    "id": "30f9abacce8949debc2f8f2ffeed7a22",
    "code": "202507300724",
    "warningInfo": {
      "title": "海啸黄色警报",
      "level": "黄色",
      "subtitle": "堪察加东岸远海海域",
      "orgUnit": "自然资源部海啸预警中心"
    },
    "timeInfo": {
      "alarmDate": "2025-07-30 10:52:45",
      "updateDate": "2025-07-30 13:34:00"
    },
    "shockInfo":{
      "shockTime":"2025-07-30 07:24",
      "latitude":52.53,
      "longitude":160.16,
      "depth":20,
      "magnitude":8.8,
      "placeName":"堪察加东岸远海海域"
    },
    "details": {
      "batch": "4",
      "logoUrl": "http://obs.nmefc.cn/CMS/warnLogo/海啸黄.png",
      "htmlUrl": "https://obs.nmefc.cn/Warning/TsunamiAdvice/202507300724_4_file/202507300724_4.html",
      "maps": {
        "earthquakeMapUrl": "https://obs.nmefc.cn/Warning/TsunamiAdvice/202507300724_4_file/Earthquake_Pos.jpg",
        "amplitudeMapUrl": "https://obs.nmefc.cn/Warning/TsunamiAdvice/202507300724_4_file/Tsunami_Maximum_Amplitude.jpg",
        "coastalMapUrl": "https://obs.nmefc.cn/Warning/TsunamiAdvice/202507300724_4_file/Coastal_Forecast_Point.jpg"
      }
    },
    "forecasts": [
      {
        "province": "台湾",
        "forecastArea": "花莲",
        "forecastPoint": "花莲",
        "estimatedArrivalTime": "13:23",
        "maxWaveHeight": "30-100",
        "warningLevel": "黄色"
      },
      ...
    ],
    "waterLevelMonitoring": [
      {
        "stationName": "浮标21416",
        "location": "俄罗斯",
        "coordinates": {
          "latitude": 48.1,
          "longitude": 163.5
        },
        "time": "08:00",
        "maxWaveHeight": "90.0"
      },
      ...
    ]
  },
  "md5": "291134dcd4f0adc2e428ec0c3c9175f7"
}
```

## 字段说明

### 主事件对象结构

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 本条记录唯一ID。 |
| `code` | string | 事件编号。同一海啸事件的多次更新编号一致。 |
| `warningInfo` | object | 标题、级别等。 |
| `timeInfo` | object | 包含发布时间 (alarmDate) 和最近更新时间 (updateDate)。 |
| `details` | object | 包含批次、HTML报文链接及多张地图URL。 |
| `details` | object | 包含震源参数。 |
| `forecasts` | array | 沿海预报数据。 **级别为“信息”或“解除”时通常为空。** |
| `waterLevelMonitoring` | array | 全球监测站实况。包含坐标、时间及监测到的波高。 |

### shockInfo 对象

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `shockTime` | string | 地震发生的北京时间 (YYYY-MM-DD HH:mm)。 |
| `latitude` | number | 震中纬度。 |
| `longitude` | number | 震中经度。 |
| `depth` | int | 震源深度 (千米)。 |
| `magnitude` | number | 震级。 |
| `placeName` | string | 震中参考位置名称。 |

### waterLevelMonitoring 内对象

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `stationName` | string | 监测站名。 |
| `location` | string | 所属国家或地区。 |
| `coordinates` | object | 坐标对象： `{ latitude: number, longitude: number }` 。西经和南纬为负数。 |
| `time` | string | 观测到最大波幅的发生时间 (BJT)。 |
| `maxWaveHeight` | string | 实测最大波幅，单位：厘米。 |

## 注意事项

- **空数据处理** ：当级别为“信息”时，此时 `forecasts` 和 `waterLevelMonitoring` 固定返回空数组 `[]` 。
- **图表地址** ： `maps` 内的 URL 仅在官方详情页提供相应图片时才会有值，否则为空字符串。

---

## 实时活跃台风 /typhoon

**数据来源：** 中国气象局

**更新频率：** 有状态更新时推送

**说明：** 返回当前西太平洋及南海海域所有活跃的台风实时参数。由于可能存在“多台风共舞”的情况，数据部分固定为数组格式。

## 示例返回

包含多个活跃台风（如双台风）时的标准返回格式。

```json
{
  "Data": [
    {
      "id": "202609",
      "name": "巴威",
      "name_en": "BAVI",
      "latitude": 13.7,
      "longitude": 147.1,
      "moveDirection": "西北西",
      "moveSpeed": 18,
      "power": 18,
      "pressure": 915,
      "windSpeed": 62,
      "type": "超强台风",
      "radius7": 380,
      "radius10": 160,
      "updateTime": "2026-07-05 20:00:00"
    },
    {
      "id": "202610",
      "name": "美莎克",
      "name_en": "MAYSAK",
      "latitude": 24.3,
      "longitude": 108.8,
      "moveDirection": "北北东",
      "moveSpeed": 21,
      "power": 8,
      "pressure": 995,
      "windSpeed": 20,
      "type": "热带风暴",
      "radius7": null,
      "radius10": null,
      "updateTime": "2026-07-05 22:00:00"
    }
  ],
  "md5": "a901afa30b4bbefcc9dbd527f82d591b"
}
```

## 字段说明

### 主事件对象结构

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `Data` | array | 活跃台风对象数组。包含当前正在编报的一个或多个台风。 |
| `md5` | string | 数据校验值。任意一个活跃台风的坐标或数据发生改变都会生成新的 MD5。 |

### Data 数组内台风对象

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 台风国际编号（如 `202609` 代表 2026 年第 9 号台风）。 |
| `name` | string | 台风中文命名（如 "巴威"）。 |
| `name_en` | string | 台风英文命名（如 "BAVI"）。 |
| `latitude` | number | 台风中心当前所处的纬度。 |
| `longitude` | number | 台风中心当前所处的经度。 |
| `moveDirection` | string | 台风未来的移动方向（如 "西北西"、"北北东"）。 |
| `moveSpeed` | number | 台风中心移动速度，单位：千米/小时 (km/h)。 |
| `power` | int | 中心附近最大风力级别（如 8、18）。 |
| `pressure` | int | 中心最低气压，单位：百帕 (hPa)。 |
| `windSpeed` | number | 中心附近最大风速，单位：米/秒 (m/s)。 |
| `type` | string | 台风当前强度等级（如 "热带低压"、"热带风暴"、"强台风"、"超强台风"）。 |
| `radius7` | int | 七级风圈半径，单位：千米 (km)。若未达到该级别则为 `null` 。 |
| `radius10` | int | 十级风圈半径，单位：千米 (km)。若未达到该级别则为 `null` 。 |
| `updateTime` | string | 该数据的实际观测时间（北京时间，格式 YYYY-MM-DD HH:mm:ss）。 |

## 注意事项

- **空数据处理：** 对于强度较弱的气旋（如热带低压或刚生成的热带风暴），七级风圈 `radius7` 和十级风圈 `radius10` 的数据可能不存在，此时接口将返回 `null` ，前端需做好容错。
- **无台风状态：** 当西太平洋及南海无活跃台风时，接口可能不推送更新，或直接返回空数组 `[]` 。

---

## 中国地震台网地震信息 /cenc

**数据来源：** 中国地震台网中心  
**更新规则：** 收到新预警后立即推送  
**示例返回：**

```json
{
"Data":{
"id":45714,
"eventId":"CC.20251004000748.000_I",
"autoFlag":"I",
"shockTime":"2025-10-04 00:07:48",
"longitude":159.5,
"latitude":51.8,
"placeName":"堪察加东岸附近海域",
"magnitude":6.1,
"createTime":"2025-10-04 08:37:21",
"depth":30,
"earthtype":4,
"infoTypeName":"[正式测定]"
},
"md5":"e244cab43590126c54d7f6daee126a03"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `eventId` | string | 地震事件国际标准编码 |
| `shockTime` | string | 地震发生时间（UTC+8，格式：YYYY-MM-DD HH:mm:ss） |
| `createTime` | string | 数据记录生成时间（UTC+8） |
| `longitude` | number | 震中经度 |
| `latitude` | number | 震中纬度 |
| `placeName` | string | 地震发生地地名 |
| `magnitude` | number | 地震震级 |
| `depth` | integer | 震源深度（单位：千米） |
| `infoTypeName` | string | 测定标识符，例如：\[自动测定\]/\[正式测定\] |

## 注意事项

- 暂无

---

## 中国地震台网地震烈度速报 /cenc-ir

**数据来源：** 中国地震台网地震烈度速报  
**更新规则：** 当烈度速报发布后延迟不超过20分钟推送  
**示例返回：**

```json
{
  "Data": {
    "id": 20260402153913,
    "uniEventId": "CD1775115553000",
    "oriTime": "2026-04-02 15:39:13",
    "locName": "新疆吐鲁番市托克逊县",
    "epiLon": "87.709999",
    "epiLat": "43.209999",
    "focDepth": "25.00",
    "magnitude": "4.7",
    "subjectCodes": "base-info, intensity-report, seismicity",
    "nameByInfo": "新疆吐鲁番市托克逊县4.7级地震",
    "gmtCreate": "2026-04-02 15:44:40",
    "intensity_info_text": "基于'GB/T17742-2020中国地震烈度表', 结合台站实测仪器烈度...本次地震推测最高烈度为7度...",
    "contour_geojson": { "type": "FeatureCollection", "features": [...] },
    "instrument_intensity_json": [ { "stName": "A0001", "INT": 1.3, ... } ]
  },
  "md5": "68bf8324cfcc31fae5a4ef6645b29450"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `uniEventId` | string | 地震事件唯一标识符 |
| `oriTime` | string | 地震发生时间（UTC+8） |
| `gmtCreate` | string | 烈度报告生成时间（UTC+8） |
| `locName` | string | 震中位置名称 |
| `epiLon` | string | 震中经度 |
| `epiLat` | string | 震中纬度 |
| `magnitude` | string | 震级大小 |
| `focDepth` | string | 震源深度（单位：千米） |
| `subjectCodes` | string | 报告包含的主题编码 |
| `intensity_info_text` | string | 烈度分布的文字描述 |
| `contour_geojson` | object | 烈度等震线地理数据，符合 GeoJSON 标准，用于地图绘制 |
| `instrument_intensity_json` | array | 各台站实测的仪器烈度详细数据 |

## 注意事项

- 暂无

---

## 中国地震预警网 /cea

**数据来源：** 中国地震预警网  
**更新规则：** 收到新预警后立即推送  
**示例返回：**

```json
{
"Data":{
"id":"bi9wyea65mayd",
"eventId":"202507152247.0001",
"shockTime":"2025-07-15 22:47:25",
"longitude": 101.09,
"latitude": 29.43,
"placeName":"四川甘孜州雅江县",
"magnitude": 4.0,
"epiIntensity": 5.5,
"depth": 8,
"updates": 3
},
"md5":"a1d427cb598646832a4ce3fe81ee2536"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 预警事件唯一标识符 |
| `eventId` | string | 事件编码（格式： `年月日时分.序号` ） |
| `shockTime` | string | 地震发生时间 |
| `longitude` | number | 震中经度 |
| `latitude` | number | 震中纬度 |
| `placeName` | string | 地震发生地地名 |
| `magnitude` | number | 地震震级 |
| `epiIntensity` | number \| null | 预估地震烈度 |
| `depth` | integer \| null | 震源深度（单位：千米） |
| `updates` | integer | 该事件的更新报数 |

## 注意事项

- 暂无

---

## 中国地震预警网省级网地震预警 /cea-pr

**数据来源:** 中国地震预警网各省级分中心  
**更新规则:** 收到新预警或更新后立即推送  
**示例返回:**

```json
{
  "Data":{
    "id":"bs1cppwt59wyy",
    "eventId":"202509120550.0001",
    "shockTime":"2025-09-12 05:50:58",
    "longitude":102.89,
    "latitude":33.002,
    "placeName":"四川阿坝州红原县",
    "magnitude":4.4,
    "epiIntensity":6.1,
    "depth":5,
    "updates":1,
    "province":"四川"
  },
  "md5":"ee90e4f7612add97e1c919fa89c82314"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 预警事件的唯一标识符 |
| `eventId` | string | 事件编码，格式通常为 `年月日时分.序号` |
| `shockTime` | string | 地震发生时间 (UTC+8, 格式: YYYY-MM-DD HH:mm:ss) |
| `longitude` | float | 震中经度 |
| `latitude` | float | 震中纬度 |
| `placeName` | string | 地震发生地点的详细名称 |
| `magnitude` | float | 地震震级 |
| `epiIntensity` | float | 预估的震中最大烈度 |
| `depth` | integer | 震源深度 (单位: 千米) |
| `updates` | integer | 该地震事件的更新报数，从 `1` 开始 |
| `province` | string | 发布该预警的省级分中心所在省份 |
| `md5` | string | 数据校验码。 |

## 注意事项

- 此接口推送的是由各省级地震预警分中心发布的数据。 `province` 字段指明了发布来源。
- 同一地震事件可能会有多次更新推送，客户端应自行识别并更新数据。

---

## 台湾省气象署地震报告 /cwa

**数据来源：** 台湾省气象署  
**更新规则：** 收到新报告或更新后立即推送  
**示例返回：**

```json
{
  "Data":{
    "id":"115011",
    "shockTime":"2026-01-27 02:48:37",
    "latitude":21.8,
    "longitude":120.8,
    "depth":23.9,
    "magnitude":4.6,
    "placeName":"屏東縣政府南南東方  103.2  公里 (位於屏東縣近海)",
    "imageURI":"https://scweb.cwa.gov.tw/webdata/OLDEQ/202601/2026012702483746011_H.png",
    "shakemapURI":"https://scweb.cwa.gov.tw/webdata/drawTrace/plotContour/2026/2026011i.png"
  },
  "md5":"d53220238999edac2607241289833745"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 地震事件唯一ID |
| `shockTime` | string | 地震发生时间 (UTC+8, 格式: YYYY-MM-DD HH:mm:ss) |
| `latitude` | number | 震中纬度 |
| `longitude` | number | 震中经度 |
| `depth` | number | 震源深度 (单位: 千米) |
| `magnitude` | number | 地震震级 |
| `placeName` | string | 震中参考地名 / 位置描述 |
| `imageURI` | string | 地震报告图片链接 |
| `shakemapURI` | string | 等震度图 (Shakemap) 图片链接 |
| `md5` | string | 数据校验码 |

## 注意事项

- 地名 `placeName` 为中文繁体。

---

## 台湾省气象署地震预警 /cwa-eew

**数据来源：** 台湾省气象署  
**更新规则：** 收到新预警或更新后立即推送  
**示例返回：**

```json
{
  "Data": {
    "id": "1150008",
    "updates": 4,
    "shockTime": "2026-01-19 07:48:27",
    "latitude": 23.33,
    "longitude": 120.82,
    "depth": 10.0,
    "magnitude": 4.5,
    "placeName": "高雄市桃源區",
    "locationDesc": [
      "嘉義縣",
      "嘉義市"
    ]
  },
  "md5": "934fa7fc68158d0cf704ba1034011b6c"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 地震事件唯一ID |
| `updates` | integer | 该事件的更新报数 |
| `shockTime` | string | 地震发生时间 (UTC+8, 格式: YYYY-MM-DD HH:mm:ss) |
| `latitude` | number | 震中纬度 |
| `longitude` | number | 震中经度 |
| `depth` | number | 震源深度 (单位: 千米) |
| `magnitude` | number | 地震震级 |
| `placeName` | string | 震中参考地名 |
| `locationDesc` | array | 影响区域或位置描述列表 |
| `md5` | string | 数据校验码 |

## 注意事项

- 地名 `placeName` 及 `locationDesc` 为中文繁体。

---

## 日本气象厅地震预警/jma

**数据来源:** 日本气象厅地震预警  
**更新规则:** 收到新预警或更新后立即推送  
**示例返回:**

```json
{
"Data":{
"id":"20251214232654",
"updates":9,
"shockTime":"2025-12-14 23:26:50",
"createTime":"2025-12-14 23:27:48",
"latitude":37.1,
"longitude":136.6,
"depth":10,
"magnitude":5,
"placeName":"能登半島沖",
"epiIntensity":"4",
"infoTypeName":"予報",
"final":true,
"cancel":false
},
"md5":"0cb2ed09122b95a4300a5f7dfdff84dd"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 事件唯一ID。 |
| `updates` | integer | 该事件的更新报数。 |
| `shockTime` | string | 地震发生时间 (UTC+9, 格式: YYYY-MM-DD HH:mm:ss)。 |
| `createTime` | string | 本条信息的创建时间 (UTC+9, 格式: YYYY-MM-DD HH:mm:ss)。 |
| `latitude` | float | 震中纬度。 |
| `longitude` | float | 震中经度。 |
| `depth` | integer | 震源深度 (单位: 千米)。 |
| `magnitude` | float | 地震震级。 |
| `placeName` | string | 震源地名 (日语原文)。 |
| `epiIntensity` | string | 最大震度。 |
| `infoTypeName` | string | 信息类型 (日语原文)。 |
| `final` | boolean | 是否为最终报文。 `true` 表示这是此事件的最终确定信息。 |
| `cancel` | boolean | 是否为取消报文。 `true` 表示此前的预报被取消。 |
| `md5` | string | 数据校验码。 |

## 注意事项

- `placeName` 和 `infoTypeName` 字段为日语原文，客户端可能需要自行翻译或处理。
- 重要标志位： `final` 和 `cancel` 。当收到 `final: true` 的报文时，可以认为该地震事件的参数已最终确定。当收到 `cancel: true` 时，应撤销该事件的预警。

---

## 美国地质调查局 /usgs

**数据来源：** 美国地质调查局地震信息  
**更新规则：** 收到新地震报告或更新后立即推送  
**示例返回：**

```json
{
"Data":{
"id": "41026127",
"title": "M 2.6 - 6 km SW of Idyllwild, CA",
"magnitude": 2.62,
"placeName": "6 km SW of Idyllwild, CA",
"shockTime": "2025-07-19 14:37:14",
"updateTime": "2025-07-19 14:42:35",
"longitude": -116.7711667,
"latitude": 33.7043333,
"depth": 16.33,
"url": "https://earthquake.usgs.gov/earthquakes/eventpage/ci41026127"
},
"md5": "db17f95f0e71872e6511799b4a2c5dcd"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | USGS 地震事件唯一标识 |
| `title` | string | 地震的完整标题描述 |
| `infoTypeName` | string | 测定标识符，例如：automatic/reviewed |
| `magnitude` | number | 地震震级 |
| `placeName` | string | 震中位置的文字描述 |
| `shockTime` | string | 地震发生时间（UTC+8，格式：YYYY-MM-DD HH:mm:ss） |
| `updateTime` | string | 此条数据的最后更新时间（UTC+8，格式：YYYY-MM-DD HH:mm:ss） |
| `longitude` | number | 震中经度 |
| `latitude` | number | 震中纬度 |
| `depth` | number | 震源深度（单位：千米） |
| `url` | string | 指向 USGS 官方地震事件页面的链接 |

## 注意事项

- 所有时间均为 **UTC+8 (北京时间)** 。

---

## 美国ShakeAlert地震预警 /sa

**数据来源：** 美国ShakeAlert地震预警  
**更新规则：** 收到新地震预警后立即推送  
**示例返回：**

```json
{
"Data":{
"id":"ci41021687",
"shockTime":"2025-07-13 20:27:55",
"latitude": 36.1743333,
"longitude": -118.0321667,
"depth": 2,
"magnitude": 3.98,
"placeName":"12 km SSW of Olancha, CA"
},
"md5":"43007947779d132c5be9fbf0a4e953ae"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 地震事件唯一标识 |
| `shockTime` | string | 地震发生时间（UTC+8，格式：YYYY-MM-DD HH:mm:ss） |
| `latitude` | number | 震中纬度 |
| `longitude` | number | 震中经度 |
| `depth` | integer \| null | 震源深度（单位：千米） |
| `magnitude` | number \| null | 地震震级 |
| `placeName` | string | 震中位置的文字描述 |

## 注意事项

- 所有时间（ `shockTime` ）均为 **UTC+8 (北京时间)** 。

---

## FSSN 地震信息 /fssn

**数据来源：** FAN Studio Seismic Network

**更新规则：** 收到新信息后立即推送

**示例返回：**

```json
{
  "Data": {
    "id": "FSSN2025waoy",
    "shockTime": "2025-11-10 17:20:47",
    "createTime": "2025-11-10 17:34:00",
    "latitude": 29.3528,
    "longitude": 101.9758,
    "depth": 10,
    "magnitude": 3.26,
    "placeName": "Sichuan,China",
    "placeName_zh": "中国四川",
    "infoTypeName": "正式(已核实)"
  },
  "md5": "8ad45f73a99fe1113ca16fc186e33f53"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 地震事件唯一标识符。 |
| `shockTime` | string | 地震发生时间（UTC+8，格式：YYYY-MM-DD HH:mm:ss）。 |
| `latitude` | number | 震中纬度。 |
| `longitude` | number | 震中经度。 |
| `depth` | integer | 震源深度（单位：千米）。 |
| `magnitude` | number | 地震震级。 |
| `placeName` | string | 地震发生地地名。 |
| `infoTypeName` | string | 速报类型，例如："已确认、正式(已核实)"。 |

## 注意事项

- 该数据源由 FSSN 测报部报告，仅供学术研究。
- 该数据源包含全球范围内的地震信息。

---

## FSSN 矩心矩张量解 (CMT) /fssn-cmt

**数据来源：** FAN Studio Seismic Network (CMT Project)  
**更新规则：** 地震发生后完成 CMT 反演计算时推送  
**示例返回：**

```json
{
  "Data": {
    "id": "69a3d519b393b",
    "eventId": "FSSN2026eegb",
    "shockTime": "2026-03-01 13:44:43",
    "latitude": -21.8973,
    "longitude": -179.5057,
    "depth": "612(+/- 8)",
    "allMagnitudes": {
      "M": 6.1,
      "mB": 6.2,
      "mb": 6.1,
      "MLv": 6.6,
      "Mwp": 6,
      "Mww": 6.3,
      "Mw(mB)": 5.9,
      "Mw(Mwp)": 5.8
    },
    "placeName": "斐济群岛地区",
    "centroidDepth": "582.1",
    "nodalPlane1": "200/77/74",
    "nodalPlane2": "73/21/141",
    "mnn": "-5.0526e+17",
    "mee": "-6.9553e+17",
    "mdd": "1.2008e+18",
    "mne": "1.2994e+18",
    "mnd": "-9.2356e+17",
    "med": "2.6576e+18"
  },
  "md5": "44340d29de28add5963512ef7d179ade"
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | CMT 记录内部唯一标识符 |
| `eventId` | string | 关联的 FSSN 地震事件 ID（对应 `/fssn` 中的 id） |
| `shockTime` | string | 地震发生时间（UTC+8, 格式: YYYY-MM-DD HH:mm:ss） |
| `latitude` | number | 震中纬度 |
| `longitude` | number | 震中经度 |
| `depth` | string | 震源深度（单位：千米，包含误差范围） |
| `allMagnitudes` | object | 震级集合，包含不同类型的震级计算结果 (M, mb, Mw 等) |
| `placeName` | string | 地震发生地地名 |
| `centroidDepth` | string | 矩心深度（单位：千米） |
| `nodalPlane1` | string | 断层节面 1 参数（走向 Strike / 倾角 Dip / 滑动角 Rake） |
| `nodalPlane2` | string | 断层节面 2 参数（走向 Strike / 倾角 Dip / 滑动角 Rake） |
| `mnn`, `mee`, `mdd` | string | 矩张量对角线分量（单位：N·m），分别对应北-北、东-东、垂直-垂直方向 |
| `mne`, `mnd`, `med` | string | 矩张量非对角线分量（单位：N·m），分别对应北-东、北-垂直、东-垂直方向 |

## 注意事项

- CMT 数据通常在地震发生后，由 FSSN 地震学部完成反演后报告，具有一定的滞后性，仅供学术研究
- `eventId` 可用于将此 CMT 解与常规速报接口 `/fssn` 的数据进行关联。
- 矩张量分量采用科学计数法字符串表示，方便物理机制分析与震源球绘制。
- `depth` 字段包含误差估算值，解析时请注意其字符串格式。
