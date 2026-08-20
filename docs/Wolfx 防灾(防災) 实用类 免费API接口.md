<!-- markdownlint-disable MD036 -->
# Wolfx Open API 使用须知

- 感谢您使用 Wolfx Open API。使用本服务前，请阅读本页面底部的《隐私政策》和《使用条款》。如有疑问，请联系 [contact@mtf.edu.kg](mailto:contact@mtf.edu.kg) 。Wolfx Project 是一个非官方项目，与任何国家或地区的政府机构、气象机构或地震相关机构均无隶属或合作关系。本服务提供的信息仅供参考，不能替代官方地震预警、灾害信息、疏散信息或其他官方警报渠道。灾害发生时，请务必以相关官方机构发布的最新信息为准。

## WebSocket API 使用须知

- 请前往此处查看详细： [/zh/docs/websocket\_zh](https://wolfx.jp/zh/docs/websocket)

## WebSocket 简介

- WebSocket API 将在服务端收到EEW后自动向所有客户端推送相关信息
- 心跳包机制：服务端将在每分钟和建立连接后发送一个heartbeat心跳包以保持连接，客户端可选回复ping包（推荐）

### WebSocket 调用地址

- 接收所有 JSON API 推送:

```text
wss://ws-api.wolfx.jp/all_eew
```

- 四川省地震局 地震预警 JSON API:

```text
wss://ws-api.wolfx.jp/sc_eew
```

- JMA 緊急地震速報 JSON API:

```text
wss://ws-api.wolfx.jp/jma_eew
```

- 福建省地震局 地震预警 JSON API:

```text
wss://ws-api.wolfx.jp/fj_eew
```

- 重庆市地震局 地震预警 JSON API:

```text
wss://ws-api.wolfx.jp/cq_eew
```

- 中国地震台网 地震预警 JSON API:

```text
wss://ws-api.wolfx.jp/cenc_eew
```

- 中国地震台网 地震信息 JSON API:

```text
wss://ws-api.wolfx.jp/cenc_eqlist
```

- JMA 地震情報 JSON API:

```text
wss://ws-api.wolfx.jp/jma_eqlist
```

- JSON字段解析详见： [https://api.wolfx.jp](https://api.wolfx.jp/)

## WebSocket 手动查询指令

- Ping:

```text
ping
```

- 四川省地震局 地震预警 JSON:

```text
query_sceew
```

- JMA 緊急地震速報 JSON:

```text
query_jmaeew
```

- 福建省地震局 地震预警 JSON:

```text
query_fjeew
```

- 重庆市地震局 地震预警 JSON:

```text
query_cqeew
```

- 中国地震台网 地震预警 JSON:

```text
query_cenceew
```

- 中国地震台网 地震信息 JSON:

```text
query_cenceqlist
```

- JMA 地震情報 JSON:

```text
query_jmaeqlist
```

## WebSocket JSON 资料说明

- 共有JSON字段解析:

| Field | Description |
| --- | --- |
| `type` | 资料类型/提供源(对应值详见API主页)(字符串型) |

- WebSocket 心跳包JSON字段解析:

| Field | Description |
| --- | --- |
| `type` | heartbeat(字符串型) |
| `ver` | 服务端版本号(数值型) |
| `id` | 客户端连接UUID(字符串型) |
| `timestamp` | 心跳包发送毫秒级时间戳(字符串型) |

- WebSocket Pong包JSON字段解析:

| Field | Description |
| --- | --- |
| `type` | pong(字符串型) |
| `timestamp` | Pong包发送毫秒级时间戳(字符串型) |

**Doc version: v20260415**

---

## JMA 緊急地震速報 JSON API

- 说明: 实时获取日本气象厅发布的紧急地震速报。低延迟接收协力: [t0729](https://x.com/t0729_)
- HTTP GET API地址:

```text
https://api.wolfx.jp/jma_eew.json
```

- WebSocket API地址:

```text
wss://ws-api.wolfx.jp/jma_eew
```

- JSON字段解析(数据类型):

| Field | Description |
| --- | --- |
| `type` | （WebSocket专用）jma\_eew（字符串类型） |
| `Title` | 紧急地震速报的标题（字符串类型） |
| `CodeType` | 发布分类的说明（字符串类型） |
| `Issue.Source` | 发布机构的位置（字符串类型） |
| `Issue.Status` | 发布状态（字符串类型） |
| `EventID` | 紧急地震速报的事件 ID（字符串类型） |
| `Serial` | 速报序号（数值类型） |
| `AnnouncedTime` | 发布时间（UTC+9）（字符串类型） |
| `OriginTime` | 地震发生时间（UTC+9）（字符串类型） |
| `Hypocenter` | 震源地（字符串类型） |
| `Latitude` | 震源纬度（数值类型） |
| `Longitude` | 震源经度（数值类型） |
| `Magunitude` | 震级（数值类型） |
| `Depth` | 震源深度（数值类型） |
| `MaxIntensity` | 最大震度（弱/强）（字符串类型） |
| `Accuracy.Epicenter` | 关于震央精度的信息（字符串类型） |
| `Accuracy.Depth` | 关于深度精度的信息（字符串类型） |
| `Accuracy.Magnitude` | 关于震级精度的信息（字符串类型） |
| `MaxIntChange.String` | 关于最大震度变化的说明（字符串类型） |
| `MaxIntChange.Reason` | 最大震度变更的原因（字符串类型） |
| `WarnArea` | 警报对象地区列表（数组类型 / JSON 值） |
| `WarnArea[].Chiiki` | 警报对象地区（字符串类型） |
| `WarnArea[].Shindo1` | 地区最大震度（弱/强）（字符串类型） |
| `WarnArea[].Shindo2` | 地区最小震度（弱/强）（字符串类型） |
| `WarnArea[].Time` | 该地区警报发布时间（字符串类型） |
| `WarnArea[].Type` | 地区发布类型，“预报”或“警报”（字符串类型） |
| `WarnArea[].Arrive` | 该地区地震波是否已经到达（以实际响应为准） |
| `isSea` | 是否为海域地震（布尔类型） |
| `isTraining` | 是否为训练报（布尔类型） |
| `isAssumption` | 是否使用假定震源要素（布尔类型） |
| `isWarn` | 是否为警报（布尔类型） |
| `isFinal` | 是否为最终报（布尔类型） |
| `isCancel` | 是否为取消报（布尔类型） |
| `OriginalText` | 气象厅发布的原文（字符串类型） |

## 中国地震台网 地震信息 JSON API

- 描述: 获取中国地震台网发布的最新地震信息, 共50条
- HTTP GET API地址:

```text
https://api.wolfx.jp/cenc_eqlist.json
```

- WebSocket API地址:

```text
wss://ws-api.wolfx.jp/cenc_eqlist
```

- JSON字段解析(数据类型):

| Field | Description |
| --- | --- |
| `type` | （WebSocket专用）cenc\_eqlist(字符串型) |
| `No(1~50)` | 地震信息条目数，发布时间顺序(字符串型) |
| `type` | 信息类型，分为"automatic"和"reviewed"(字符串型) |
| `time` | 发震时间(UTC+8)(字符串型) |
| `location` | 震源地(对原数据进行了处理以保证国内地区格式一致性)(字符串型) |
| `placeName` | 震源地(未对原数据进行修改以保证数据的原始性)(字符串型) |
| `magnitude` | 震级(字符串型) |
| `depth` | 震源深度(字符串型) |
| `latitude` | 震源地纬度(字符串型) |
| `longitude` | 震源地经度(字符串型) |
| `intensity` | 最大烈度(字符串型) |
| `md5` | 地震信息更新校验码(字符串型) |

## CWA 地震预警 JSON API (仅服务大陆地区)

- 描述: 实时获取CWA发布的地震预警
- HTTP GET API地址:

```text
https://api.wolfx.jp/cwa_eew.json
```

- JSON字段解析(数据类型):

| Field | Description |
| --- | --- |
| `ID` | EEW发报ID(数值型) |
| `ReportTime` | EEW发报时间(UTC+8)(字符串型) |
| `ReportNum` | EEW发报数(数值型) |
| `OriginTime` | 发震时间(UTC+8)(字符串型) |
| `HypoCenter` | 震源地(字符串型) |
| `Latitude` | 震源地纬度(数值型) |
| `Longitude` | 震源地经度(数值型) |
| `Magunitude` | 震级(数值型) |
| `Depth` | 震源深度(数值型) |
| `MaxIntensity` | 最大震度(弱/強)(字符串型) |

## JMA 地震情報 JSON API

- 描述: 获取日本気象庁发布的最新地震情報, 共50条
- HTTP GET API地址:

```text
https://api.wolfx.jp/jma_eqlist.json
```

- WebSocket API地址:

```text
wss://ws-api.wolfx.jp/jma_eqlist
```

- JSON字段解析(数据类型):

| Field | Description |
| --- | --- |
| `type` | （WebSocket专用）jma\_eqlist(字符串型) |
| `Title` | 发报报头(字符串型) |
| `No(1~50)` | 地震情报条目数，发布时间顺序(字符串型) |
| `time` | 发震时间(UTC+9)(字符串型) |
| `location` | 震源地(字符串型) |
| `magnitude` | 震级(字符串型) |
| `shindo` | 最大震度(-/+)(字符串型) |
| `depth` | 震源深度(字符串型) |
| `latitude` | 震源地纬度(字符串型) |
| `longitude` | 震源地经度(字符串型) |
| `info` | 津波情报(仅第一条提供)(字符串型) |
| `md5` | 地震情报更新校验码(字符串型) |

## 中国气象实况排行 JSON API

- 描述: 提供每小时国家级气象观测站气温、降水、风速实况排行
- HTTP GET API地址:

```text
https://api.wolfx.jp/weather_rank.json
```

- JSON字段解析(数据类型):

| Field | Description |
| --- | --- |
| `YYYYMMDDHH00` | 分别提供最近8小时内的全国气象实况排行(UTC+8)(字符串型) |
| `tempRank` | 气温排行(从高到低10条)(字符串型) |
| `rainRank` | 降水排行(从高到低10条)(字符串型) |
| `windSRank` | 风速排行(从高到低10条)(字符串型) |
| `md5` | 排行数据更新校验码(字符串型) |

## IP位址资讯查询 JSON API

- 描述: 获取请求方或指定IP位址的相关资讯
- HTTP GET API地址:

```text
https://api.wolfx.jp/geoip.php
```

```text
https://api.wolfx.jp/geoip.php?ip=<IP位址>
```

- JSON字段解析(数据类型):

| Field | Description |
| --- | --- |
| `ip` | 请求IP(字符串型) |
| `country_code` | 所在国家或地区缩写(字符串型) |
| `country_name` | 所在国家或地区(字符串型) |
| `country_name_zh` | 所在国家或地区(中文)(字符串型) |
| `province_code` | 所在省或州代码(字符串型) |
| `province_name` | 所在省或州(字符串型) |
| `province_name_zh` | 所在省或州(中文)(字符串型) |
| `city` | 所在城市(字符串型) |
| `city_zh` | 所在城市(中文)(字符串型) |
| `latitude` | 所在纬度(可能无法获取)(数值型) |
| `longitude` | 所在经度(可能无法获取)(数值型) |
