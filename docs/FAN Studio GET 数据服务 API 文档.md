<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD034 -->
# FAN Studio GET 数据服务 API 文档

GET API 提供了通过标准 HTTP GET 请求来获取最新或历史数据的方式。

**基础 URL:**`https://api.fanstudio.tech/`  
**认证方式:** 当前所有接口均无需认证。  
**返回格式:** 所有成功响应均为 JSON 格式，并遵循统一的结构。

## 通用约定

- 所有接口均使用 `GET` 方法。
- 字符编码统一为 `UTF-8` 。
- 请遵守合理的请求频率，避免对服务器造成不必要的压力。

---

## 基础瓦片地图服务 tilemap.fanstudio.tech

**详细请查看:** https://tilemap.fanstudio.tech/ (可参考 Fan Studio TileMap API.md)

---

## IP 地理位置查询 /tool/geo\_ip.php

**请求方法:** `GET`  
**接口路径:** `/tool/geo_ip.php`  
**功能描述:** 根据IP地址查询其地理位置信息。如果未提供IP参数，则返回当前请求客户端的IP信息。

## URL 参数

| 参数 | 类型 | 是否必需 | 描述 |
| --- | --- | --- | --- |
| `ip` | string | 否 | 要查询的IP地址。 **如果留空，将自动查询当前访客的IP地址。** |

## 请求示例

**示例1：** 查询指定IP地址 (114.114.114.114)

```bash
curl "https://api.fanstudio.tech/tool/geo_ip.php?ip=114.114.114.114"
```

**示例2：** 查询当前访客自己的IP地址

```bash
curl "https://api.fanstudio.tech/tool/geo_ip.php"
```

## 成功响应示例

响应体是一个包含地理位置信息的JSON对象。

```json
{
  "ip": "114.114.114.114",
  "country": "中国",
  "province": "江苏",
  "city": "南京",
  "isp": "114DNS",
  "latitude": 34.7732,
  "longitude": 113.722
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ip` | string | 查询的IP地址。 |
| `country` | string | IP地址所在的国家。 |
| `province` | string | IP地址所在的省份。 |
| `city` | string | IP地址所在的城市。 |
| `isp` | string | IP地址所属的运营商或机构。 |
| `latitude` | float | 估算的纬度。 |
| `longitude` | float | 估算的经度。 |

## 错误响应示例

当提供的IP地址格式不正确时，可能会返回错误信息。

```json
{
  "error": "AUV俺不中了"
}
```

---

## 全国气象站数据 /we/station\_all.php

**请求方法:** `GET`  
**接口路径:** `/we/station_all.php`  
**功能描述:** 获取全国所有气象站点的指定要素实时数据。

## URL 参数

| 参数 | 类型 | 是否必需 | 描述与可选值 |
| --- | --- | --- | --- |
| `type` | string | **是** | 指定要查询的气象要素类型。 **如果此参数缺失或无效，将返回错误信息。**   可选值包括： - `temperature` (气温) - `pressure` (气压) - `windspeed` (风速) - `maxwindspeed24h` (24小时最大风速) - `humidity` (相对湿度) - `visibility` (能见度) - `rain` (1小时降水) |

## 请求示例

获取全国所有站点的实时气温数据：

```bash
curl "https://api.fanstudio.tech/we/station_all.php?type=temperature"
```

## 成功响应示例 (code: 200)

响应体为一个JSON对象，包含数据时间和站点数据数组。

```json
{
  "time": "2025-09-07 15:00",
  "data": [
    {
      "val": "20.8",
      "sta_name": "镇巴",
      "lon": "107.9",
      "lat": "32.53",
      "stationid": "57238"
    },
    {
      "val": "25",
      "sta_name": "安康",
      "lon": "109.03",
      "lat": "32.72",
      "stationid": "57245"
    },
    // ... 更多站点数据
  ]
}
```

### 响应字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `time` | string | 数据对应的时间点 (UTC+8)。 |
| `data` | array | 包含所有站点数据的数组。 |
| `val` | string | 当前站点监测到的数值。 |
| `sta_name` | string | 气象站点的中文名称。 |
| `lon` | string | 站点的经度。 |
| `lat` | string | 站点的纬度。 |
| `stationid` | string | 站点的唯一ID。 |

## 错误响应示例

当 `type` 参数缺失或提供的值无效时，将返回如下错误信息：

```json
{
  "error": "无效或缺失 type 参数. 请使用 'temperature' (气温), 'pressure' (气压), 'windspeed' (风速), 'maxwindspeed24h' (24小时最大风速), 'humidity' (湿度), 'visibility' (能见度)."
}
```

---

## 全国雷达矢量图 /we/img/radar\_china.php

**请求方法:** `GET`  
**接口路径:** `https://api.fanstudio.tech/we/img/radar_china.php`  
**功能描述:** 获取最新全国雷达的透明背景图像（Base64格式），以及图像对应的地理边界坐标和尺寸信息。

## 成功响应示例

响应体是一个包含雷达图片Base64数据、地理范围及尺寸信息的JSON对象。

```json
{
  "status": "success",
  "time": "2026-04-28 16:36:00",
  "bounds": {
    "sw": [11.1784, 67.5],
    "ne": [55.7766, 140.625]
  },
  "size": {
    "width": 860,
    "height": 662
  },
  "image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA..."
}
```

## 字段说明

| 字段路径 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 请求状态。成功时返回 `"success"` 。 |
| `time` | string | 雷达数据时间 (YYYY-MM-DD HH:mm:ss)。 |
| `bounds` | object | 图片的地理边界范围对象。 |
| `bounds.sw` | array | 图片西南角（左下角）坐标数组，格式为 `[纬度, 经度]` 。 |
| `bounds.ne` | array | 图片东北角（右上角）坐标数组，格式为 `[纬度, 经度]` 。 |
| `size` | object | 图像尺寸信息。 |
| `size.width` | integer | 图像宽度（像素）。 |
| `size.height` | integer | 图像高度（像素）。 |
| `image` | string | 图片的 Base64 编码字符串 |

---

## 东南沿海及西太卫星云图 /we/img/cloud\_china.php

**请求方法:** `GET`  
**接口路径:** `https://api.fanstudio.tech/we/img/cloud_china.php`  
**功能描述:** 获取最新卫星云图的图像数据（Base64格式），包含图像精确地理边界范围信息。

## 成功响应示例

响应体是一个包含卫星云图Base64数据、地理范围的JSON对象。

```json
{
  "status": "success",
  "time": "2026-04-28 17:00:00",
  "bounds": {
    "sw": [-2, 95],
    "ne": [43, 160]
  },
  "image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...",
}
```

## 字段说明

| 字段路径 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 请求状态。成功时返回 `"success"` 。 |
| `time` | string | 云图生成时间 (北京时间 YYYY-MM-DD HH:mm:ss)。 |
| `bounds` | object | 云图的地理边界范围对象。 |
| `bounds.sw` | array | 图片西南角（左下角）坐标数组，格式为 `[纬度, 经度]` 。 |
| `bounds.ne` | array | 图片东北角（右上角）坐标数组，格式为 `[纬度, 经度]` 。 |
| `image` | string | 卫星云图的 Base64 编码字符串 |

---

## 气象站单站历史数据 /we/station\_history.php

**请求方法:** `GET`  
**接口路径:** `/we/station_history.php`  
**功能描述:** 根据站点ID和日期，获取该站点一整天的逐小时历史气象数据。

## URL 参数

| 参数 | 类型 | 是否必需 | 描述 |
| --- | --- | --- | --- |
| `id` | string | **是** | 需要查询的气象站点的唯一ID。 |
| `date` | string | **是** | 需要查询的日期，格式为 `YYYY-MM-DD` 。 |

## 请求示例

查询ID为 `56288` 的气象站在 `2025-01-01` 的历史数据：

```bash
curl "https://api.fanstudio.tech/we/station_history.php?id=56288&date=2025-01-01"
```

## 成功响应示例

响应体是一个JSON对象，其中每个键 (key) 是一个具体的时间点，值 (value) 是该时间点的气象数据。

```json
{
  "2025-01-01 01:00 +0800": {
    "temperature": "3.6",
    "pressure": "961.5",
    "humidity": "93",
    "wind_direction": "9/N",
    "wind_speed": "0.7"
  },
  "2025-01-01 02:00 +0800": {
    "temperature": "3.3",
    "pressure": "961.2",
    "humidity": "95",
    "wind_direction": "304/NW",
    "wind_speed": "0.2"
  },
  "2025-01-01 03:00 +0800": {
    "temperature": "3.5",
    "pressure": "961.1",
    "humidity": "97",
    "wind_direction": "59/ENE",
    "wind_speed": "0.3"
  }
  // ... 当天其他小时的数据
}
```

### 响应字段说明 (每个时间点对象内部)

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `temperature` | string | 温度 (°C)。 |
| `pressure` | string | 气压 (hPa)。 |
| `humidity` | string | 相对湿度 (%)。 |
| `wind_direction` | string | 风向，格式为 "角度/方向缩写"。 |
| `wind_speed` | string | 风速 (m/s)。 |

## 错误处理

如果提供的 `id` 或 `date` 无效，或找不到对应的数据，可能会返回一个空对象 `{}` 或错误信息。

---

## 城市空气质量指数 (AQI) /we/aqi.php

**请求方法:**`GET`  
**接口路径:**`/we/aqi.php`  
**功能描述:** 获取全国主要城市AQI的指定要素实时数据。

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `Id` | integer | 数据记录ID |
| `TimePoint` | string | 数据时间点 (ISO 8601 格式) |
| `AQI` | string | 空气质量指数 (Air Quality Index) |
| `COLevel` | integer | 一氧化碳 (CO) 分指数等级 |
| `NO2Level` | integer | 二氧化氮 (NO₂) 分指数等级 |
| `O3Level` | integer | 臭氧 (O₃) 分指数等级 |
| `PM10Level` | integer | 可吸入颗粒物 (PM10) 分指数等级 |
| `PM2_5Level` | integer | 细颗粒物 (PM2.5) 分指数等级 |
| `SO2Level` | integer | 二氧化硫 (SO₂) 分指数等级 |
| `Area` | string | 地区/城市名称 |
| `CityCode` | integer | 城市行政区划代码 |
| `AqiLevel` | integer | 空气质量指数级别 (1级为最优) |
| `PrimaryPollutant` | string | 首要污染物，"—"表示无 |
| `Quality` | string | 空气质量等级描述 (如："优", "良") |
| `Measure` | string | 对健康影响与建议措施 |
| `Unheathful` | string | 对健康影响的详细描述 |
| `Latitude` | string | 监测点纬度 |
| `Longitude` | string | 监测点经度 |
| `ProvinceId` | integer | 省份ID |

## 注意事项

- `AQI`, `Latitude`, `Longitude` 字段以字符串形式提供，使用时请根据需要自行转换。
- `TimePoint` 为 ISO 8601 格式的时间字符串，可能需要客户端进行格式化处理。

---

## 全球火山喷发通告 /we/volcanic.php

**请求方法:**`GET`  
**接口路径:**`/we/volcanic.php`  
**功能描述:** 获取最新的全球火山喷发及火山灰通告 (VAAC - Volcanic Ash Advisory Centers)。

## 成功响应示例

响应体是一个JSON对象，其中 `data` 字段是一个包含多个火山通告的数组。

```json
{
  "volcanic_eruptions": {
    "meta": {
      "from": "2025-09-17T04:57:06Z"
    },
    "data": [
      {
        "advisory": {
          "number": "2025/021",
          "aviation_color_code": "Unknown",
          // ... more advisory details
        },
        "volcano": {
          "name": "Sabancaya",
          "area": "Peru",
          // ... more volcano details
        },
        "observations": [
          {
            "issuetime": "2025-09-17T04:20:00Z",
            "geometry": {
              "type": "Polygon",
              "coordinates": [ /* ... */ ]
            }
          }
        ],
        "forecasts": [ /* ... */ ]
      }
      // ... more eruption data
    ]
  }
}
```

## 字段说明

### 主结构

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `meta.from` | string | 数据生成时间 (UTC/Zulu time)。 |
| `data` | array | 火山通告对象数组。 |

### data 数组内对象结构

| 字段路径 | 类型 | 说明 |
| --- | --- | --- |
| `advisory` | object | 通告详情。 |
| `advisory.number` | string | 通告编号。 |
| `advisory.source` | string | 数据来源，如卫星、网络摄像头等。 |
| `advisory.aviation_color_code` | string | 航空颜色代码 (如 Orange, Red)，表示火山活动对航空的威胁等级。 |
| `advisory.eruption.raw_text` | string | 喷发情况的原始文本描述。 |
| `advisory.eruption.time` | string | 喷发事件时间 (UTC/Zulu time)。 |
| `advisory.remarks` | string | 备注信息，提供更详细的解释。 |
| `advisory.next` | string | 下一次通告的预计发布时间。 |
| `volcano` | object | 火山本身的信息。 |
| `volcano.name` | string | 火山名称。 |
| `volcano.number` | string | 火山的唯一编号。 |
| `volcano.coordinates` | array | 火山坐标 `[经度, 纬度]` 。 |
| `volcano.area` | string | 火山所在的国家或地区。 |
| `volcano.summit_elevation` | object | 山顶海拔高度，包含 `value` (数值) 和 `units` (单位)。 |
| `observations` | array | 火山灰云观测数据数组。 |
| `observations[].issuetime` | string | 观测发布时间 (UTC/Zulu time)。 |
| `observations[].geometry` | object | 火山灰云范围的GeoJSON Polygon 对象，用于地图绘制。 |
| `forecasts` | array | 火山灰云移动预测数据数组。 |
| `forecasts[].valid_time` | string | 预测生效时间 (UTC/Zulu time)。 |
| `forecasts[].geometry` | object | 预测的火山灰云范围 GeoJSON Polygon 对象。 |

## 重要提示

- **时区注意：** 所有时间戳均为UTC时间 (以 'Z' 结尾)，在使用时请根据需要转换为本地时间。
- 此API提供的数据专业性较强，主要用于航空安全等领域。
