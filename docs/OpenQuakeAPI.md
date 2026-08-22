<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD036 -->
<!-- markdownlint-disable MD051 -->
# OpenQuakeAPI

**Base URL**: `wss://api.aloys23.link`

实时地震与海洋灾害预警数据推送服务，聚合全球多源数据，通过 WebSocket 向客户端推送 JSON 事件。

## 连接方式

所有端点均使用原生 WebSocket 协议：

```markdown
wss://api.aloys23.link/{path}
```

数据源按路径名路由，客户端连接到对应路径后即可接收该源的所有事件。

| 路径 | 数据源 | 说明 |
| --- | --- | --- |
| `/ws/quake/gq` | GlobalQuake | 全球地震实时数据 |
| `/ws/tsunami/nmefc` | NMEFC | 海啸预警 |
| `/ws/tsunami/nmefc-wave` | NMEFC | 海浪警报 |
| `/ws/tsunami/nmefc-surge` | NMEFC | 风暴潮警报 |
| `/ws/all` | 全部 | 所有数据源的聚合推送 |

## 事件通用格式

所有 WebSocket 消息均为 JSON 文本，外层统一采用 `RealtimeEvent` 结构：

```json
{
  // 数据源标识: gq / nmefc / nmefc-wave / nmefc-surge / all
  "source": "gq",
  // 事件分类: earthquake / cluster / station / status / tsunami / alert
  "type": "earthquake",
  // 事件动作: update / archived / cancelled / remove / info / intensity / connected / disconnected
  "action": "update",
  // 事件发生时间戳(ms)
  "timestampMs": 1740787200000,
  // 事件具体数据，结构因 source 和 type 而异
  "payload": {
    // 地震事件唯一标识
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    // 纬度
    "latitude": 35.0,
    // 经度
    "longitude": 140.0,
    // 深度(km)
    "depth": 10.0,
    // 震级
    "magnitude": 6.5,
    // 发震时间戳(ms)
    "originTimeMs": 1740787200000,
    // 发震时间 ISO 8601
    "originTimeIso": "2026-03-01T00:00:00Z",
    // 最后更新时间戳
    "lastUpdateMs": 1740787205000,
    // 修订版本号
    "revisionId": 3,
    // 地区名称
    "region": "日本关东地区",
    // 是否为固定深度
    "fixedDepth": false,
    // 最大峰值地面加速度(Gal)
    "maxPGA": 140.0,
    // MMI 烈度（罗马数字 I–XII）
    "intensity": "VIII",
    // 事件簇信息
    "cluster": {
      "id": "cluster-uuid",
      "latitude": 35.1,
      "longitude": 140.1,
      "level": 1
    },
    // 定位质量信息
    "quality": {
      "errOrigin": 0.5,
      "errDepth": 2.0,
      "errNS": 5.0,
      "errEW": 4.0,
      "pct": 80.0,
      "stations": 15
    },
    // 台站统计
    "stationCount": {
      "total": 30,
      "selected": 20,
      "used": 15,
      "matching": 12
    },
    // 深度置信区间
    "depthConfidence": {
      "minDepth": 8.0,
      "maxDepth": 12.0
    }
  }
}
```

各数据源的 payload 结构不同，详见对应页面。

---

## 全部数据聚合

**路径**: `/ws/all`

所有数据源的聚合推送，连接此端点可同时接收 GlobalQuake、NMEFC 海啸、海浪、风暴潮等全部数据源的事件。

## 事件说明

`/ws/all` 透传所有数据源的事件， `source` 字段保留原始数据源标识，事件结构与各数据源相同。

| source | 原始数据源 | 事件类型 |
| --- | --- | --- |
| `gq` | GlobalQuake 全球地震 | earthquake / cluster / station / status |
| `nmefc` | NMEFC 海啸预警 | tsunami |
| `nmefc-wave` | NMEFC 海浪警报 | alert |
| `nmefc-surge` | NMEFC 风暴潮警报 | alert |
| `cma` | CMA 气象预警 | weather |

各事件的具体数据结构请参考对应数据源页面。

---

## 全球地震数据

**路径**: `/ws/quake/gq`

数据来自 GlobalQuake 全球地震监测网络，订阅全球范围内的实时地震事件。

## 事件列表

| type | action | 说明 |
| --- | --- | --- |
| `earthquake` | `update` | 地震参数更新/新地震 |
| `earthquake` | `archived` | 地震事件结束归档 |
| `earthquake` | `cancelled` | 地震事件被取消 |

## 地震更新 / 归档

```json
{
  // 数据源标识
  "source": "gq",
  // 事件分类: earthquake / cluster / station / status
  "type": "earthquake",
  // 事件动作: update / archived / cancelled
  "action": "update",
  // 事件发生时间戳(ms)
  "timestampMs": 1740787200000,
  "payload": {
    // 地震事件唯一标识
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    // 纬度
    "latitude": 35.0,
    // 经度
    "longitude": 140.0,
    // 深度(km)
    "depth": 10.0,
    // 震级
    "magnitude": 6.5,
    // 发震时间戳(ms)
    "originTimeMs": 1740787200000,
    // 发震时间 ISO 8601
    "originTimeIso": "2026-03-01T00:00:00Z",
    // 最后更新时间戳
    "lastUpdateMs": 1740787205000,
    // 修订版本号
    "revisionId": 3,
    // 地区名称
    "region": "日本关东地区",
    // 是否为固定深度
    "fixedDepth": false,
    // 最大峰值地面加速度(Gal)
    "maxPGA": 140.0,
    // MMI 烈度（罗马数字 I–XII）
    "intensity": "VIII",
    // 事件簇信息
    "cluster": {
      // 簇 ID
      "id": "cluster-uuid",
      // 簇中心纬度
      "latitude": 35.1,
      // 簇中心经度
      "longitude": 140.1,
      // 簇层级
      "level": 1
    },
    // 定位质量信息
    "quality": {
      // 发震时间误差(s)
      "errOrigin": 0.5,
      // 深度误差(km)
      "errDepth": 2.0,
      // 南北方向误差(km)
      "errNS": 5.0,
      // 东西方向误差(km)
      "errEW": 4.0,
      // 质量百分比
      "pct": 80.0,
      // 参与定位台站数
      "stations": 15
    },
    // 台站统计
    "stationCount": {
      // 总台站数
      "total": 30,
      // 选中台站数
      "selected": 20,
      // 使用台站数
      "used": 15,
      // 匹配台站数
      "matching": 12
    },
    // 深度置信区间
    "depthConfidence": {
      // 最小可能深度(km)
      "minDepth": 8.0,
      // 最大可能深度(km)
      "maxDepth": 12.0
    }
  }
}
```

## 地震取消

```json
{
  "source": "gq",
  "type": "earthquake",
  // action: cancelled - 地震事件被取消
  "action": "cancelled",
  "timestampMs": 1740787200000,
  "payload": {
    // 被取消的地震事件 ID
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  }
}
```

---

## 海啸预警

**路径**: `/ws/tsunami/nmefc`

数据来自国家海洋环境预报中心 (NMEFC)，订阅中国沿海海啸预警信息。

## 事件列表

| type | action | 说明 |
| --- | --- | --- |
| `tsunami` | `update` | 新的海啸预警或更新 |

## 事件数据

```json
{
  "source": "nmefc",
  "type": "tsunami",
  "action": "update",
  "timestampMs": 1740787200000,
  "payload": {
    // 预警标题
    "title": "海啸警报",
    // 预警级别: 红/橙/黄/蓝
    "level": "橙色",
    // 发布时间
    "issueTime": "2024-01-15 10:00:00",
    // 预警编号
    "code": "TS20240115001",
    // 发布单位
    "orgUnit": "国家海洋环境预报中心",
    // 震中位置描述
    "location": "日本本州东岸近海",
    // 震中纬度
    "latitude": 36.0,
    // 震中经度
    "longitude": 141.0,
    // 地震震级
    "magnitude": 7.8,
    // 地震深度(km)
    "depthKm": 20.0,
    // 深度类型描述
    "depthDescription": "浅源",
    // 地震描述全文
    "earthquakeDescription": "据中国地震台网正式测定，1月15日9时30分在日本本州东岸近海（北纬36.0度，东经141.0度）发生7.8级地震，震源深度20千米。",
    // 海啸影响评估
    "assessment": "根据初步地震参数判断，地震可能会在震源周围引发局地海啸，但不会对我国沿岸造成灾害性影响。",
    // 后续跟踪说明
    "followUpNote": "我中心将继续跟踪分析地震和海啸监测数据，并及时发布信息。",
    // 水位观测数据列表
    "waterLevels": [
      {
        // 验潮站名称
        "stationName": "鹿儿岛",
        // 位置
        "location": "日本",
        // 坐标
        "coordinates": "31°N 130°E",
        // 观测时间(北京时间)
        "timeBjt": "2024-01-15 10:30",
        // 最大振幅(cm)
        "maxAmplitudeCm": "50"
      }
    ],
    // 水位数据说明
    "waterLevelNote": "以上为国外实测水位数据",
    // 海啸分类说明
    "classificationNote": "地震海啸",
    // 值班预报员
    "dutyOfficer": "张三",
    // 值班电话
    "dutyPhone": "010-12345678",
    // 主管机构
    "supervisingAuthority": "自然资源部",
    // 震中位置图 URL
    "earthquakePositionImageUrl": "https://www.nmefc.cn/Warning/TsunamiAdvice/.../Earthquake_Pos.jpg",
    // 签发图 URL
    "signatureImageUrl": "https://www.nmefc.cn/Warning/TsunamiAdvice/.../sig.jpg"
  }
}
```

---

**路径**: `/ws/tsunami/nmefc-wave`

数据来自国家海洋环境预报中心 (NMEFC)，订阅中国沿海海浪预警信息。

## 事件列表

| type | action | 说明 |
| --- | --- | --- |
| `alert` | `update` | 新的海浪警报 |

## 事件数据

```json
{
  "source": "nmefc-wave",
  "type": "alert",
  "action": "update",
  "timestampMs": 1740787200000,
  "payload": {
    // 唯一标识
    "id": "WAVE20240115001",
    // 更新日期
    "updateDate": "2024-01-15 10:00:00",
    // 预警编号
    "code": "WAVE20240115001",
    // 预警级别: 红/橙/黄/蓝
    "level": "黄色",
    // 预警类型
    "warnType": "海浪",
    // 发布人
    "author": "国家海洋环境预报中心",
    // 发布单位
    "orgUnit": "国家海洋环境预报中心",
    // 标题
    "title": "海浪黄色警报",
    // 副标题
    "subtitle": "受冷空气影响...",
    // 预警描述（含 HTML）
    "description": "受冷空气影响，预计1月15日夜间...",
    // 预警日期
    "alarmDate": "2024-01-15",
    // 类型标识
    "type": "wave",
    // Logo 路径
    "logo": "/images/wave.png",
    // 图片路径
    "image": "/images/wave_20240115.png",
    // 签发图片路径
    "signUrl": "/sign/wave_20240115.png",
    // 联系电话
    "phone": "010-12345678"
  }
}
```

---

**路径**: `/ws/tsunami/nmefc-surge`

数据来自国家海洋环境预报中心 (NMEFC)，订阅中国沿海风暴潮预警信息。

## 事件列表

| type | action | 说明 |
| --- | --- | --- |
| `alert` | `update` | 新的风暴潮警报 |

## 事件数据

```json
{
  "source": "nmefc-surge",
  "type": "alert",
  "action": "update",
  "timestampMs": 1740787200000,
  "payload": {
    // 唯一标识
    "id": "SURGE20240115001",
    // 更新日期
    "updateDate": "2024-01-15 10:00:00",
    // 预警编号
    "code": "SURGE20240115001",
    // 预警级别: 红/橙/黄/蓝
    "level": "橙色",
    // 预警类型
    "warnType": "风暴潮",
    // 发布人
    "author": "国家海洋环境预报中心",
    // 发布单位
    "orgUnit": "国家海洋环境预报中心",
    // 标题
    "title": "风暴潮橙色警报",
    // 副标题
    "subtitle": "受台风影响...",
    // 预警描述（含 HTML）
    "description": "受台风影响，预计1月15日下午...",
    // 预警日期
    "alarmDate": "2024-01-15",
    // 类型标识
    "type": "surge",
    // Logo 路径
    "logo": "/images/surge.png",
    // 图片路径
    "image": "/images/surge_20240115.png",
    // 签发图片路径
    "signUrl": "/sign/surge_20240115.png",
    // 联系电话
    "phone": "010-12345678"
  }
}
```

---

## 气象预警

**路径**: `/ws/cma`

数据来自中国气象局 (CMA) 国家预警信息发布中心，订阅全国气象灾害预警信息。

## 事件列表

| type | action | 说明 |
| --- | --- | --- |
| `weather` | `new` | 新的气象预警 |

首次连接时建立基线缓存，后续仅推送新增预警，已撤销的预警不会主动推送移除事件。

payload 字段直接透传自 [中国气象局预警地图 API](https://weather.cma.cn/api/map/alarm) ，不额外处理。

## 事件数据

```json
{
  "source": "cma",
  "type": "weather",
  "action": "new",
  "timestampMs": 1752401400000,
  "payload": {
    "id": "32031241600000_20260729124523",
    "headline": "铜山区气象台发布强对流黄色预警[Ⅲ级/较重]",
    "effective": "2026/07/29 12:45",
    "description": "铜山区气象台2026年07月29日12时41分发布强对流黄色预警信号：预计今天午后到上半夜我区部分镇（街道）将出现雷电，并伴有短时强降水、局地7-9级雷暴大风等强对流天气，区应急、水务、气象联合提醒加强防范。",
    "longitude": 117.1839,
    "latitude": 34.1929,
    "type": "p0000003",
    "title": "江苏省徐州市铜山区发布强对流黄色预警"
  }
}
```

## 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | string | 预警唯一标识 |
| `headline` | string | 预警标题行 |
| `effective` | string | 生效时间 |
| `description` | string | 预警详细描述 |
| `longitude` | number | 经度 |
| `latitude` | number | 纬度 |
| `type` | string | 预警类型代码（如 p0000003） |
| `title` | string | 预警标题 |
