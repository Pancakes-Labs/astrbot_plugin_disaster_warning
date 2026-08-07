<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD034 -->

# API 通用说明

## **所有开放接口共用的请求约定、响应格式与 HTTP 头说明**

本文适用于 **[https://www.resapi.cn](https://www.resapi.cn/)** 下所有 `/v1/*` 开放接口。各业务 API 的路径、参数与字段见对应文档页。

> 使用本站即表示您同意 [免责声明与使用条款](https://www.resapi.cn/docs/terms) 与 [隐私政策](https://www.resapi.cn/docs/privacy) 。免费服务仅供技术验证；数据请自行甄别；商用请选用更稳定服务商；默认不将查询参数写入业务数据库；责任限制见条款全文。

**AI Agent 接入** ：Cursor、WorkBuddy 等可通过 [`/agent.json`](https://www.resapi.cn/agent.json) 发现能力。MCP 远程地址为 [`/mcp`](https://www.resapi.cn/mcp) ，完整步骤见 [MCP 接入说明](https://www.resapi.cn/docs/agent/mcp) 与 [WorkBuddy 接入](https://www.resapi.cn/docs/agent/workbuddy) 。

### MCP 快速开始

| 步骤 | 操作 |
| --- | --- |
| 1 | 在 Cursor MCP 配置中添加 `"url": "https://www.resapi.cn/mcp"` （生产请与 `site.base_url` 一致，如 `https://www.resapi.cn/mcp` ；勿用会 301 的裸域） |
| 2 | 重启客户端，确认 14 个 Tool 已加载 |
| 3 | 对话测试：「2026 年 10 月 1 日是否放假？」 |

聚合接口（ `/v1/agent/*` ）与 `agent_hint` 说明亦见 [MCP 文档](https://www.resapi.cn/docs/agent/mcp#%E8%81%9A%E5%90%88%E6%8E%A5%E5%8F%A3%E5%87%8F%E5%B0%91-ai-%E5%A4%9A%E6%AC%A1%E8%B0%83%E7%94%A8) 。

---

## 平台原则 {#平台原则}

| 原则 | 说明 |
| --- | --- |
| **只读查询** | 开放 API 以 `GET` 为主，仅根据请求参数即时查询并返回 JSON， **不提供** 数据写入、上传、注册或用户资料托管。 |
| **用户参数不缓存** | **不保存** 手机号、地址、证件号等查询参数与响应到业务库；Redis 也 **不缓存** 此类接口（重复查询极少，缓存无意义）。 |
| **平台数据可缓存** | 启用 Redis 时，仅缓存 **平台自有数据** 类接口（如节假日、时区、区划/高校查询）；相同请求可命中 `X-Cache: HIT` 。 |
| **仅计数限流** | 为公平使用，仅按客户端 IP **统计请求次数** （参考 `X-Forwarded-For` / `X-Real-IP` ），用于访问频率限制；计数与具体查询内容无关，时间窗口结束后自动重置。 |

> 默认配置下 `cache.enabled: false` （全局不启用 Redis 缓存）。开启后仍遵守上表「用户参数不缓存」策略。

---

## 通用约定

| 项目 | 说明 |
| --- | --- |
| 协议 | HTTPS（生产环境）/ HTTP（本地调试） |
| 方法 | 以 `GET` 为主 |
| 数据格式 | 请求与响应均为 **JSON** ，UTF-8 编码 |
| 认证 | 当前无需 API Key，直接请求即可 |
| 字符集 | 查询参数中的中文请使用 URL 编码（各语言 HTTP 库通常自动处理） |

### 成功响应结构

**单条资源** （如单日判断）：

```json
{
  "data": { }
}
```

**列表资源** （如区间批量判断）：

```json
{
  "data": [ ],
  "meta": {
    "page": 1,
    "page_size": 10,
    "total": 8,
    "total_pages": 1
  }
}
```

### 错误响应

通用错误码与响应体格式见下文 **「通用错误码」** ；各 API 在文档页「错误代码」Tab 中仅列出 **本接口补充** 说明。

---

## 推荐请求头

客户端 **建议** 携带以下请求头（未携带时多数接口仍可正常返回 JSON）：

| 请求头 | 建议值 | 含义 |
| --- | --- | --- |
| `Accept` | `application/json` | 声明期望 JSON 响应，便于网关与客户端统一处理 |
| `Accept-Encoding` | `gzip` （可选） | 若前置 CDN/反向代理支持压缩，可减小传输体积 |

当前 **不需要** `Authorization` 、 `X-Api-Key` 等认证头。

### 与部署相关的请求头（了解即可）

若服务部署在反向代理之后，代理可能传入（由服务端用于识别客户端， **一般无需你手动设置** ）：

| 请求头 | 含义 |
| --- | --- |
| `X-Forwarded-For` | 原始客户端 IP 链，限流按 IP 统计时会参考 |
| `X-Real-IP` | 代理认定的客户端 IP |

---

## 响应头说明

以下响应头在 **`/v1/*` JSON 接口** 上可能出现。并非每次响应都包含全部字段——取决于是否开启限流、是否命中缓存等。

### 始终常见

| 响应头 | 示例值 | 含义 |
| --- | --- | --- |
| `Content-Type` | `application/json; charset=utf-8` | 响应体为 JSON，编码 UTF-8 |
| `X-Request-Id` | `7f3c2a1b-...` | 本次请求的唯一追踪 ID，与访问日志中 `request_id` 一致 |
| `X-Data-Version-Regions` | `2026` | 仅区划/地址相关 API；行政区划数据版本标签 |
| `X-Data-Version-Colleges` | `2026` | 仅高校 API；高校数据版本标签 |

### 限流相关

当平台开启 **访问频率限制** 时，每次响应（含被拒绝的 `429` ）可能包含：

| 响应头 | 类型 | 含义 |
| --- | --- | --- |
| `X-RateLimit-Limit` | 整数 | 当前时间窗口内，允许同一 IP 的最大请求次数 |
| `X-RateLimit-Remaining` | 整数 | 本窗口内 **剩余** 可用次数；为 `0` 时继续请求可能返回 `429` |
| `X-RateLimit-Reset` | Unix 时间戳（秒） | 当前窗口结束、计数重置的时刻，可用作客户端退避参考 |

**示例理解** ： `Limit=60` 、 `Remaining=12` 、 `Reset=1717200060` 表示本分钟内还可请求 12 次，约在 `Reset` 时刻后额度刷新。

未开启限流时，以上三个头 **不会出现** 。

### 缓存相关（可选，默认关闭）

**默认** `cache.enabled: false` ，所有接口实时返回。

开启 Redis 响应缓存后，策略如下：

| 类型 | 路径示例 | 是否缓存 | 说明 |
| --- | --- | --- | --- |
| 平台数据 | `/v1/holidays/*` 、 `/v1/time/*` 、 `/v1/regions/*` 、 `/v1/colleges/*` 、 `/v1/zipcode/*` 、 `/v1/ich/*` | **是** | 节假日、时区、区划/高校/邮编/非遗等；相同查询会重复，缓存减轻数据库压力 |
| 用户输入 | `/v1/phone/*` 、 `/v1/validate/*` 、 `/v1/address/*` | **否** | 手机号、证件、地址等；几乎不会重复查询，缓存无意义且不宜留存 |

响应头（启用缓存时）：

| 响应头 | 示例值 | 含义 |
| --- | --- | --- |
| `X-Cache` | `HIT` | 命中平台数据类接口的短期缓存 |
| `X-Cache` | `MISS` | 可缓存接口未命中，已实时查询并写入缓存 |
| `X-Cache` | `BYPASS` | 用户输入类接口， **主动跳过** 缓存 |
| `X-Cache-TTL` | `1h0m0s` | 仅 `HIT` 时出现，剩余有效期 |

未启用 Redis 时上述头 **不会出现** 。限流仍只记次数，不记查询内容。

---

## HTTP 状态码

| 状态码 | 典型场景 |
| --- | --- |
| `200` | 请求成功 |
| `400` | 查询参数不合法（如日期格式错误） |
| `404` | 资源不存在 |
| `429` | 请求过于频繁，请稍后重试 |
| `500` | 服务端异常，请稍后重试 |

---

## 接口根路径

| 路径 | 说明 |
| --- | --- |
| `https://www.resapi.cn/v1/...` | 业务数据 API |
| `https://www.resapi.cn/catalog.json` | 机器可读的 API 目录（含 `agent_summary` ） |
| `https://www.resapi.cn/agent.json` | Agent 服务发现与核心 Tool 清单 |
| `https://www.resapi.cn/openapi.json` | OpenAPI 3.0 规范（JSON） |
| `https://www.resapi.cn/docs/agent/mcp` | MCP 接入说明（Cursor 等） |
| `https://www.resapi.cn/docs/agent/workbuddy` | WorkBuddy 接入说明 |
| `GET https://www.resapi.cn/v1/agent/suggest?q=` | Agent 意图路由 |
| `GET https://www.resapi.cn/mcp` | MCP Streamable HTTP（远程） |
| `https://www.resapi.cn/health` | 服务健康检查（含数据库与数据量） |
| `https://www.resapi.cn/metrics` | Prometheus 指标（文本格式） |

平台接口详情、健康检查字段与 OpenAPI 说明见下文 **平台接口** ；版本变更见 **变更日志** 。

文档站页面（HTML）与上述 JSON API 相互独立；开发对接请以 `/v1/*` 及本文为准。

---

## 通用错误码

以下错误码适用于平台全部 `/v1/*` 接口（各接口在「错误代码」Tab 中另有 **本接口补充** 说明）。

### 错误响应格式

HTTP 状态码非 2xx 时，响应体一般为：

```json
{
  "error": {
    "code": "错误码",
    "message": "错误信息"
  }
}
```

### 错误码一览

| HTTP | code | message | 说明 |
| --- | --- | --- | --- |
| 400 | invalid\_params | 参数无效 | 必填参数缺失、格式不合法或超出允许范围 |
| 404 | not\_found | 资源不存在 | 按编码或 ID 查询时无匹配记录 |
| 429 | rate\_limit\_exceeded | 请求过于频繁 | 超过当前 IP 的访问频率限制 |
| 500 | internal\_error | 服务器内部错误 | 服务端或数据库异常，请稍后重试 |

限流、缓存等 HTTP 响应头含义见上文「响应头说明」。

---

## 平台接口

除 `/v1/*` 业务 API 外，平台提供以下 **非业务数据** 接口，便于运维、对接与监控。

---

## GET /health

服务健康检查。用于负载均衡、容器探针与状态页。

### 响应字段

| 字段 | 说明 |
| --- | --- |
| status | `ok` 表示服务可用；数据库不可用时为 `degraded` |
| database | `ok` / `down` |
| redis | 未启用时不出现；启用时为 `ok` / `down` |
| data.regions | 行政区划表记录数（数据库可读时） |
| data.colleges | 高校表记录数（数据库可读时） |
| data.phone\_segments | 手机号段表记录数（数据库可读时） |

### 响应示例

```json
{
  "status": "ok",
  "database": "ok",
  "redis": "ok",
  "data": {
    "regions": 664239,
    "colleges": 2859
  }
}
```

### HTTP 状态码

| 状态码 | 说明 |
| --- | --- |
| 200 | 服务与数据库正常 |
| 503 | Redis 不可用（启用 Redis 且 Ping 失败时） |

---

## GET /metrics

Prometheus 文本格式指标，用于监控抓取（如 Prometheus、Grafana、Uptime Kuma 插件）。

| 指标名 | 类型 | 说明 |
| --- | --- | --- |
| `resapi_http_requests_total` | counter | 按 `method` 、 `route` （路由模板）、 `status` 聚合的请求数 |
| `resapi_http_request_duration_ms_sum` | counter | 响应耗时累计（毫秒） |
| `resapi_http_request_duration_ms_count` | counter | 请求次数（与 sum 配套算平均耗时） |

`route` 为 Chi 路由模板（如 `/v1/regions/{code}` ），避免按真实 URL 高基数爆炸。

响应 `Content-Type`: `text/plain; version=0.0.4; charset=utf-8`

---

## 访问日志（结构化）

服务端对每次 HTTP 请求输出 **一行 JSON** 到标准错误（stderr），便于日志采集（Loki、ELK、云日志等）。

| 字段 | 说明 |
| --- | --- |
| time | RFC3339 时间 |
| method | HTTP 方法 |
| path | 请求路径（含查询串前的 path） |
| route | 路由模板（未匹配路由时与 path 相同） |
| status | HTTP 状态码 |
| latency\_ms | 处理耗时（毫秒） |
| request\_id | 与响应头 `X-Request-Id` 一致 |
| ip | 客户端 IP（考虑 `X-Forwarded-For` / `X-Real-IP` ） |

示例：

```json
{"time":"2026-06-03T12:00:00+08:00","method":"GET","path":"/v1/regions/search","route":"/v1/regions/search","status":200,"latency_ms":15,"request_id":"7f3c-...","ip":"127.0.0.1"}
```

静态资源 `/static/*` 不写入访问日志，以降低噪音。

---

## GET /openapi.json

OpenAPI **3.0** 描述文件（JSON），汇总当前已开放的 `/v1/*` 路径，便于 Postman 导入、SDK 生成与 CI 契约检查。

| 项目 | 说明 |
| --- | --- |
| 格式 | `application/json` ，OpenAPI 3.0.3 |
| 更新 | 随 API 发版由服务端根据目录与路由生成 |
| 认证 | 与业务 API 相同，当前无需 API Key |

`info.version` 与变更记录见下文 **变更日志** 。

---

## GET /catalog.json

机器可读的 API 目录（名称、描述、文档链接），见首页说明。

---

## 数据版本响应头

部分依赖 SQLite 静态数据的 `GET /v1/*` 响应可能包含：

| 响应头 | 说明 |
| --- | --- |
| `X-Data-Version-Regions` | 行政区划数据版本标签（配置项 `data.regions_version` ，默认 `2026` ） |
| `X-Data-Version-Colleges` | 高校数据版本标签（配置项 `data.colleges_version` ，默认 `2026` ） |
| `X-Data-Version-Phone` | 手机号段数据版本标签（配置项 `data.phone_version` ，默认 `2026-05` ） |

仅当请求路径前缀匹配 `/v1/regions` 、 `/v1/address` 、 `/v1/colleges` 、 `/v1/phone` 时附加，便于客户端缓存失效判断。

---

## 变更日志

版本与破坏性变更说明见下文 **变更日志** 章节（源文件 `docs/global/changelog.md` ）。

---

## 访问频率限制

平台对 API 请求按 **客户端 IP** 计数（参考 `X-Forwarded-For` / `X-Real-IP` ）。 **仅统计请求次数** ，不记录、不存储查询参数或响应内容。响应头见 [API 通用说明](https://www.resapi.cn/docs/guide) 中的限流章节。

### 全局限流

配置项（ `config.yaml` ）：

| 配置 | 说明 | 默认 |
| --- | --- | --- |
| `rate_limit.enabled` | 是否启用 | `true` |
| `rate_limit.limit` | 每 IP 每窗口最大请求数 | `60` |
| `rate_limit.window` | 窗口长度 | `1m` |

### 搜索类接口加严

以下路径使用 **独立、更严格** 的额度（防止刷库）：

| 路径 | 说明 |
| --- | --- |
| `GET /v1/regions/search` | 区划地名搜索 |
| `GET /v1/colleges/search` | 高校搜索 |
| `GET /v1/address/suggest` | 地址联想 |

| 配置 | 说明 | 默认 |
| --- | --- | --- |
| `rate_limit.search_limit` | 搜索类每 IP 每窗口上限； `0` 表示与全局限流相同 | `20` |

超限返回 `429` ， `code` 为 `rate_limit_exceeded` 。

### 不计入限流的路径

`/health` 、 `/metrics` 、 `/openapi.json` 、 `/catalog.json` 、 `/static/*` 、文档页 `/` 与 `/docs/*` 不参与 API 限流计数。

---

## 变更日志

| 版本 | 日期 | 说明 |
| --- | --- | --- |
| 1.14.0 | 2026-07-23 | 车牌号牌 API `/v1/plate/*` ；MCP 工具 `plate_lookup` / `plate_search` ；聚合 `/v1/agent/plate` ； `cmd/import-plate` |
| 1.13.0 | 2026-07-04 | 阶段 4： `/v1/agent/*` 聚合接口、 `agent_hint` 、MCP HTTP `/mcp` （免 API Key） |
| 1.12.0 | 2026-07-04 | Agent 接入： `/agent.json` 、MCP Server、WorkBuddy Skill；OpenAPI `operationId` 增强 |
| 1.11.0 | 2026-06-04 | 省级非遗 API `/v1/ich/*` （项目/传承人/统计）； `cmd/import-ich` |
| 1.10.0 | 2026-06-04 | `GET /v1/zipcode/lookup` 邮编查地名； `GET /v1/zipcode/search` 地名查邮编； `cmd/import-zipcode` |
| 1.9.1 | 2026-06-04 | `GET /v1/holidays/solar-to-lunar` 公历转农历； `GET /v1/holidays/lunar-to-solar` 农历转公历（支持闰月） |
| 1.9.0 | 2026-06-04 | `GET /v1/holidays/lunar` 农历/节气/生肖/星座； `GET /v1/holidays/terms` 年度 24 节气 |
| 1.8.1 | 2026-06-03 | 修订《免责声明与使用条款》；新增《隐私政策》 `/docs/privacy` |
| 1.8.0 | 2026-06-03 | `GET /v1/phone/lookup` 手机号段归属地查询； `/health` 增加 `phone_segments` 计数； `X-Data-Version-Phone` |
| 1.7.0 | 2026-06-03 | `validate/bank-card` ；搜索类接口单独限流；撤回误加的 `holidays/workdays` 、 `holidays/lunar` |
| 1.6.0 | 2026-06-03 | 地址 `region_code` ； `regions/ancestors` ； `/health` 增强； `openapi.json` |
| 1.5.0 | 2026-06-03 | `GET /metrics` ；结构化访问日志； `X-Data-Version-*` 响应头 |
| 1.4.0 | 2026-06-03 | 地址 parse/normalize 增加 `region_code` ； `GET /v1/regions/{code}/ancestors` ； `/health` 增强； `GET /openapi.json` |
| 1.3.0 | 2026-06-03 | `regions` 列表分页、 `/provinces` ； `validate/idcard` |
| 1.2.0 | 2026-06-03 | `regions/search` 分页与省/市筛选； `regions/children` |
| 1.1.0 | 2026-06-03 | 行政区划、地址、USCC 校验等 API 上线 |
| 1.0.0 | 2026-06-03 | 节假日、时区、高校 API；文档站与 catalog |

破坏性变更将在此表标注 **BREAKING** 。

---

## 全国行政区划 API

- **提供省市区镇村查询、搜索分页、子级级联与地名检索**

**Base URL**:

```bash
https://www.resapi.cn/v1/regions
```

数据来源于 2026 年全国行政区划公开数据。支持省级列表、关键词搜索分页、子级级联与按编码查详情。

## GET /v1/regions

行政区划列表查询，支持按层级、父编码筛选。

### 查询参数

| 名称 | 类型 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| level | string | 否 | 层级： `province` / `city` / `district` / `town` / `village` | `city` |
| parent\_code | string | 否 | 父级编码 | `110000` |
| page | int | 否 | 页码，默认 `1` | `1` |
| page\_size | int | 否 | 每页条数，默认 `10` ，最大 `100` | `50` |
| limit | int | 否 | 兼容旧参数：等价于 `page=1` 且 `page_size=limit` （最大 100） | `50` |

### 响应字段（meta）

| 字段 | 说明 |
| --- | --- |
| total | 命中总数 |
| count | 本次返回条数 |
| page | 当前页码 |
| page\_size | 每页条数 |
| total\_pages | 总页数 |

### 响应示例

```json
{
  "data": [
    {
      "code": "110100",
      "name": "市辖区",
      "level": "city",
      "type": "区",
      "parent_code": "110000",
      "full_name": "北京市/市辖区"
    }
  ],
  "meta": {
    "count": 1,
    "total": 16,
    "page": 1,
    "page_size": 50,
    "total_pages": 1
  }
}
```

---

## GET /v1/regions/provinces

获取全国省级行政区划列表（等价于 `GET /v1/regions?level=province` ），用于级联选择第一步。

### 查询参数

| 名称 | 类型 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| page | int | 否 | 页码，默认 `1` | `1` |
| page\_size | int | 否 | 每页条数，默认 `10` ，最大 `100` | `50` |

### 响应示例

```json
{
  "data": [
    {
      "code": "110000",
      "name": "北京市",
      "level": "province",
      "full_name": "北京市"
    }
  ],
  "meta": {
    "count": 34,
    "total": 34,
    "page": 1,
    "page_size": 50,
    "total_pages": 1
  }
}
```

---

## GET /v1/regions/{code}

按行政区划编码获取单条详情。

### 路径参数

| 名称 | 类型 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| code | string | 是 | 行政区划编码 | `110000` |

### 响应示例

```json
{
  "data": {
    "code": "110000",
    "name": "北京市",
    "level": "province",
    "type": "市",
    "parent_code": "",
    "full_name": "北京市",
    "child_count": 16
  }
}
```

---

## GET /v1/regions/{code}/ancestors

根据行政区划编码，返回从 **省级到当前节点** 的层级路径（结构化数组，便于级联回显与面包屑）。

### 路径参数

| 名称 | 类型 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| code | string | 是 | 任意层级编码（省/市/区/镇/村） | `110108` |

### 响应字段

| 字段 | 说明 |
| --- | --- |
| code | 当前节点编码 |
| name | 当前节点名称 |
| level | 当前节点层级 |
| full\_name | 全路径名称（斜杠分隔） |
| path | 从省到当前节点的数组，每项含 `code` 、 `name` 、 `level` |

### 响应示例

```json
{
  "data": {
    "code": "110108",
    "name": "海淀区",
    "level": "district",
    "full_name": "北京市/市辖区/海淀区",
    "path": [
      { "code": "110000", "name": "北京市", "level": "province" },
      { "code": "110100", "name": "市辖区", "level": "city" },
      { "code": "110108", "name": "海淀区", "level": "district" }
    ]
  }
}
```

---

## GET /v1/regions/search

关键词搜索行政区划（名称/编码/全路径），支持按省、市缩小范围。

### 查询参数

| 名称 | 类型 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| q | string | 是 | 搜索关键词（村名、镇名等） | `火楼村` |
| level | string | 否 | 层级过滤： `province` / `city` / `district` / `town` / `village` | `village` |
| province | string | 否 | 省级名称，不传则全国检索 | `山东省` |
| city | string | 否 | 市级名称，可与 `province` 组合 | `济南市` |
| parent\_code | string | 否 | 父级编码过滤（精确） | `110100` |
| page | int | 否 | 页码，从 1 开始，默认 `1` | `2` |
| page\_size | int | 否 | 每页条数，默认 `10` ，最大 `50` | `10` |
| limit | int | 否 | 兼容旧参数：仅传 `limit` 时等价于 `page=1` 且 `page_size=limit` （最大 50） | `10` |

### 响应字段（meta）

| 字段 | 说明 |
| --- | --- |
| total | 命中总数 |
| count | 本次返回条数 |
| page | 当前页码 |
| page\_size | 每页条数 |
| total\_pages | 总页数 |
| scope | 检索范围： `全国` 或 `省名` 或 `省/市` |

### 示例：全国检索同名村

`GET /v1/regions/search?q=火楼村&level=village`

### 示例：仅在山东省检索

`GET /v1/regions/search?q=火楼村&level=village&province=山东省`

### 示例：分页浏览第 2 页

`GET /v1/regions/search?q=火楼村&level=village&page=2&page_size=10`

### 响应示例

```json
{
  "data": [
    {
      "code": "370181201212",
      "name": "火楼村",
      "level": "village",
      "full_name": "山东省/济南市/章丘区/某某街道/火楼村"
    }
  ],
  "meta": {
    "q": "火楼村",
    "level": "village",
    "province": "山东省",
    "city": "",
    "scope": "山东省",
    "count": 10,
    "total": 12,
    "page": 1,
    "page_size": 10,
    "total_pages": 2
  }
}
```

---

## GET /v1/regions/children

按父级编码查询 **直接下级** 行政区划，适用于省→市→区县→镇→村级联选择器。

### 查询参数

| 名称 | 类型 | 必填 | 说明 | 示例 |
| --- | --- | --- | --- | --- |
| parent\_code | string | 是 | 父级行政区划编码 | `110000` |
| level | string | 否 | 仅返回指定层级（一般为父级的下一级） | `city` |
| page | int | 否 | 页码，默认 `1` | `1` |
| page\_size | int | 否 | 每页条数，默认 `10` ，最大 `50` | `20` |

### 响应示例

```json
{
  "data": [
    {
      "code": "110100",
      "name": "市辖区",
      "level": "city",
      "type": "区",
      "parent_code": "110000",
      "full_name": "北京市/市辖区"
    }
  ],
  "meta": {
    "parent_code": "110000",
    "count": 1,
    "total": 16,
    "page": 1,
    "page_size": 10,
    "total_pages": 2
  }
}
```

### 级联示例

1. 全国省份： `GET /v1/regions/provinces` （或 `GET /v1/regions?level=province` ）
2. 北京市下辖区： `GET /v1/regions/children?parent_code=110000`
3. 某区下街道： `GET /v1/regions/children?parent_code=110101&level=town`
