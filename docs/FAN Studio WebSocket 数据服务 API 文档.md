# FSSN WebSocket API 说明

> 原 FAN Studio 多源 WebSocket（`ws.fanstudio.tech`）已停止运营。
> 现仅保留 **FSSN 地震信息** 与 **FSSN 矩心矩张量解 (CMT)**。

## 官方文档

完整协议与字段说明见：<https://fssn-api.fanstudio.tech/>

## 插件接入要点

| 项 | 内容 |
| --- | --- |
| 地址 | `wss://fssn-api.fanstudio.tech/all`（亦可 `/fssn`、`/fssn-cmt`） |
| 鉴权 | 公开路径，**无需** API Key |
| 配置组 | `data_sources.fan_studio`（兼容旧路径名），子开关：`fssn`、`fssn_cmt` |
| 心跳 | 服务端约 20s 发送 `heartbeat`；客户端可回 `ping` |
| 列表 | 建连后发送 `fssnlist` / `cmtlist` 拉取已有事件 |

## 消息类型

- `initial_all`：同时携带 `fssn` 与 `fssn-cmt` 快照
- `update`：`source` 为 `fssn` 或 `fssn-cmt`
- `fssnlist_response` / `cmtlist_response`：列表查询响应

## 迁移说明

原先通过 FAN Studio 接收的 CEA / CWA / ShakeAlert / 台风 / 气象 / 海啸 / USGS 等通道，
请改用 **Jian Project**、**Wolfx**、**P2P** 或 **EQSC** 等仍在运营的数据源。
