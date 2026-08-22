<!-- markdownlint-disable MD024 -->
<!-- markdownlint-disable MD025 -->
<!-- markdownlint-disable MD028 -->
<!-- markdownlint-disable MD033 -->
<!-- markdownlint-disable MD034 -->
<!-- markdownlint-disable MD041 -->
# ChangeLog

# 2026/08/22 v1.6.0

经历了几次跳票、砍需求、加上我连续一个多月的爆肝后，灾害预警 v1.6.0 正式版本（没错原先计划的其他 beta 版直接跳过了）也是终于问世了！

在这个版本中，我们带来了非常多的新功能，以及大量优化。现在它不仅是一个预警推送插件，更是一个初具规模的预警信息分析平台。

我们还顺便更新了一下插件 Logo 的视觉形象，希望你喜欢~  ~~PS：缩小了看怎么那么像冰与火之舞~~

未来我们还计划推出拓展系统（Extension），面向更多的开发者，支持社区扩展数据源，规则和展示等能力。

另外本次更新有部分默认行为发生了改变，请您仔细阅读下方更新日志的提示内容。

## 🚀 What's Changed

### ✨ New Features (新功能)

- 支持将 P2P `各地震度详情` 中详细的町丁目名称转换为**地域**级别展示 by @DBJD-CR in #142 #180
- 支持台风信息推送，并完成了完整的前后端适配 by @DBJD-CR in #147 #158 #160 #167 #176 #183 #184 #189 #190 #196 #204 #208 #209 #212 #213
- 在地震预警消息推送中新增预估走时信息与本地 S 波到达的预估倒计时信息 by @DBJD-CR in #148 #184 #190 #195
- 在预警信息推送中新增 `预估影响区县`（中国） 与 `预估影响地域`（日本）信息 by @DBJD-CR in #148 #203
- 支持 NIED S-Net 海底震度分布推送，并完成了完整的前后端适配 by @DBJD-CR in #150 #151 #156 #157 #175
- 支持在推送文本中对 Emoji 进行可选的三档级别的过滤 by @DBJD-CR in #152
- 支持为各个过滤器自定义逻辑组合（`OR` 逻辑与 `AND` 逻辑） by @DBJD-CR in #156
- 支持 JMA 震央分布查询与绘图功能 by @DBJD-CR in #161 #206
- 支持中国地震台网烈度速报推送，并完成了完整的前后端适配 by @DBJD-CR in #163 #175 #181
- 支持美国 ShakeAlert 地震预警推送，并完成了完整的前后端适配 by @DBJD-CR in #165
- 支持 FSSN 矩心矩张量解 (CMT) 推送，并完成了完整的前后端适配 by @DBJD-CR in #174
- 新增沙滩球绘图功能 by @DBJD-CR in #174 #203
- 支持全量的气象预警推送，并补充聚合推送（合并转发）能力 by @DBJD-CR in #180 #203 #212
- 新增启动横幅与信息汇总大屏，优化启动阶段的日志打印 by @DBJD-CR in #184 #190
- 气象预警查询支持取消 72 小时的时间过滤 by @DBJD-CR in #184
- 支持气象雷达（组合反射率）查询功能 by @DBJD-CR in #188
- 新增气象实况排行查询相关功能 by @DBJD-CR in #189 #194
- 新增气象站实况查询相关功能 by @DBJD-CR in #191
- 支持 AQI（空气质量指数）查询功能 by @DBJD-CR in #193 #194
- 新增地震动预测功能，支持自动提取地震参数并解析 by @DBJD-CR in #195 #203
- 新增模拟预警系统，支持多灾种事件流编排 by @DBJD-CR in #196 #198
- 新增降水量预报查询功能 by @DBJD-CR in #197
- 支持在本地监控中根据位置自动解析并应用合适的烈度/震度体系，当然也可以自己选择它 by @DBJD-CR in #203
- 支持 FAN Studio 主备服务器偏好切换 by @DBJD-CR in #206
- 支持通过命令控制插件与 AstrBot 重载 by @DBJD-CR in #207

### 🎨 Visualization (可视化与渲染)

- 新增会话推送 Top10 卡片、风王榜卡片与台风强度等级卡片 by @DBJD-CR in #147
- 支持渲染 S-Net 测站分布图 by @DBJD-CR in #150
- 新增 S-Net 历史最大震度卡片并优化最大震级卡片样式与排版 by @DBJD-CR in #150
- 优化了统计口径说明卡片 by @DBJD-CR in #151
- 优化海啸卡片信息展示 by @DBJD-CR in #154
- 支持渲染台风路径图 by @DBJD-CR in #158 #176 #183
- 添加可翻转的 FAN Studio 状态卡片，正面显示主通道，背面显示 CENC 烈度速报 by @DBJD-CR in #168
  - 同时优化了连接矩阵卡片的布局
- 新增 `通道健康` 面板，基于本地网络可用性，展示整体状态、90 天可用性趋势、每日健康详情及历史事故 by @DBJD-CR in #169 #174 #192
- 大幅优化了事件列表里对各类型事件的展示行为，提供更丰富和详细的信息 by @DBJD-CR in #177
- 重构跑马灯为三栏竖向滚动并且能感知气象预警类型和级别，显示对应 emoji by @DBJD-CR in #187 #190
- 为通用地图瓦片附加简单的事件描述标签 by @DBJD-CR in #187
- 支持发送气象雷达图（组合反射率）与动图版本 by @DBJD-CR in #188
- 新增 `模拟预警` 页面 by @DBJD-CR in #196
- 支持发送降水量预报图与动图版本 by @DBJD-CR in #197
- 在配置页面中添加了实时推文预览面板，并将布局重构为“编辑器 / 预览”的双栏设计 by @DBJD-CR in #198
- 重新设计侧边栏页脚以及 GitHub/插件目录按钮 by @DBJD-CR in #198
- 修复了 Mermaid 图表在暗色模式下的可读性问题 by @DBJD-CR in #201
- 重构 Markdown 代码块为 VSCode 风格语法高亮与明暗双主题终端外观 by @DBJD-CR in #202
- 重构管理端首屏加载体验 by @DBJD-CR in #206
- 管理端连接矩阵面板展示 FAN Studio 主备服务器标签 by @DBJD-CR in #206
- 微调了 Global Quake 卡片样式，以保证在烈度 10 及以上时的视觉效果 by @DBJD-CR in #208

### 🌐 Data Sources & Network (数据源与网络)

- 接入 EQSC 数据源 by @DBJD-CR in #147 #154 #175 #183 #192
  - 实时活跃台风轮询
  - JMA 海啸情报轮询
  - CENC 烈度速报轮询
- 接入 MSIL 的 S-Net 瓦片轮询 by @DBJD-CR in #150
- 实现 FAN Studio 连接配额与优先级策略，保证 `/all` 始终作为主连接，次级连接能正确延迟与退避 by @DBJD-CR in #163
- 支持 FAN Studio 应用鉴权 by @DBJD-CR in #167
- 迁移 Global Quake 连接端点 by @DBJD-CR in #168 #175
- 接入 OpenQuakeAPI 数据源 by @DBJD-CR in #173 #180
  - Global Quake
  - 中国气象局：气象预警
- 使用 ResAPI 进行气象预警地区查询 by @DBJD-CR in #186 #190 #191 #194
- 优化了抓取远程图件的稳定性 by @DBJD-CR in #203
- 改进在 TLS 阻断场景下的主备切换与重连行为，避免在不可用地址上反复重试 by @DBJD-CR in #206
- 加强遥测覆盖率、隐私保护、节流控制和生命周期清理 by @DBJD-CR in #207
- 迁移遥测与通知中心至新域名 by @DBJD-CR & Aloys233 in #214
- 调整地图瓦片请求格式以适应上游协议 by @DBJD-CR in #214

### 🧠 Rules & Filters (规则与过滤)

- 新增台风过滤相关规则 by @DBJD-CR in #147
- 新增 S-Net 过滤相关规则 by @DBJD-CR in #150 #156
- 针对性的放宽时间规则，避免丢弃诸如烈度速报、CMT 等有效但略有延迟的产品 by @DBJD-CR in #177

### 📊 Statistics & Storage (统计与存储)

- 增强数据库以支持台风信息持久化 by @DBJD-CR in #147
  - 支持数据库台风信息过少时自动重建最新 20 个的台风数据
- 增强数据库以支持 S-Net 信息持久化 by @DBJD-CR in #151
- 自动折叠和清理历史脏数据 by @DBJD-CR in #154 #157 #170
- 改善事件历史记录合并与去重，减少重复或来源不完整的记录 by @DBJD-CR in #170
- 扩展基于数据库的唯一键跟踪和短窗口解析缓存，优化气象预警的去重逻辑与统计数据恢复 by @DBJD-CR in #194
- 扩展备份还原功能，支持缓存/模拟流/通知/日志等 7 类数据 by @DBJD-CR in #199

### ⚙️ Configuration (配置)

- 新增 `JMA 震度按地域汇总` 配置项 by @DBJD-CR in #142
- 新增 `EQSC API` 配置组 by @DBJD-CR in #147 #154 #175
- 为 `FAN Studio Websocket 数据源` 添加多个子数据源 by @DBJD-CR in #147 #163 #165 #174
  - 新增 `中国气象局：实时活跃台风` 推送开关，默认关闭
  - 新增 `中国地震台网（CENC）：烈度速报` 推送开关，默认关闭
  - 新增 `美国 ShakeAlert：地震预警` 推送开关，默认开启
  - 新增 `FSSN：矩心矩张量解 (CMT)` 推送开关，默认开启
- 新增 `🌀 台风信息配置` 配置组 by @DBJD-CR in #147
- 新增 `NIED S-Net 海底震度` 与 `S-Net 海底震度专用过滤器` 配置组 by @DBJD-CR in #150 #156
- 增强了**会话差异配置**的稳定性 by @DBJD-CR in #151
- 新增 `推送文本 Emoji 过滤` 配置项 by @DBJD-CR in #152
- 新增 `🌊 海啸信息配置` 配置组 by @DBJD-CR in #154
- 为各个过滤器新增 `条件组合方式` 配置项 by @DBJD-CR in #156
- 新增 `台风路径图瓦片源` 配置项 by @DBJD-CR in #158
- 调整 `📍本地监控配置` 的 `通知阈值(烈度)` 默认值为 `2.0` by @DBJD-CR in #163
- 新增 `FAN Studio API Key` 配置项 by @DBJD-CR in #167
- **移除** `启动后静默时间` 配置项，新增 `是否静默启动插件` 配置项并默认开启 by @DBJD-CR in #171
- 新增 `OpenQuakeAPI 数据源` 配置组 by @DBJD-CR in #173 #180
  - 新增 `Global Quake` 配置项，默认开启
  - 新增 `中国气象局：气象预警` 配置项，默认开启
- 新增 `气象预警聚合推送` 配置组 by @DBJD-CR in #180
- 新增 `事件流日志级别覆盖` 配置组 by @DBJD-CR in #180
- 调整 FAN Studio 的 `中国气象局：气象预警` 配置项为**默认关闭** by @DBJD-CR in #180
- 新增 `台风停编通知` 与 `静默启动期间丢弃事件流日志` 配置项，默认启用 by @DBJD-CR in #184
- 新增 `包含测站分布图` 与 `包含台风路径图` 配置项并默认开启 by @DBJD-CR in #192
- 新增 `忽略浏览器 HTTPS 证书错误（仅本地模式）` 配置项，默认关闭 by @DBJD-CR in #192
- **移除**了 `原始消息日志文件路径` 配置项 by @DBJD-CR in #192
- 新增 `记录气象预警正文` 配置项并默认开启 by @DBJD-CR in #194
- 新增 `通知阈值(震度)` 与 `本地强度体系` 配置项 by @DBJD-CR in #203
- 新增 `节点未满时等待凑满再推送` 配置项并默认开启 by @DBJD-CR in #203
- 新增 `附加中国区县烈度预估` 和 `附加日本地域震度预估` 配置项，默认关闭。您可以根据自己的情况决定是否启用 by @DBJD-CR in #203

### 💻 WebUI / Frontend (前端)

- 优化了前端卡片的响应式设计，防止抖动与文字发糊 by @DBJD-CR in #150 #162
- 增强事件列表的筛选功能，提升查询能力 by @DBJD-CR in #155
  - 支持按时间筛选
  - 地震支持按深度筛选
  - 地震支持按烈度/震度筛选
  - 台风支持按各核心参数与活跃状态筛选
- 优化了前端 a11y 支持、小屏适配、暗色主题增强，提升使用体验 by @DBJD-CR in #201
- 新增 `markedjs` `mermaidjs` `DOMPurify` 三个本地库 by @DBJD-CR in #201
- 重构文档浏览页面，优化阅读体验 by @DBJD-CR in #205

### ♻️ Refactor (重构)

- 对海啸相关的前后端内容与推送文本进行了大幅重构与增强 by @DBJD-CR in #154
- 重构静默启动流程，使其在插件重载和 AstrBot 启动/重载的场景下都更加科学且无感 by @DBJD-CR in #171 #174 #180 #181
- 将 Global Quake 纳入为 OpenQuakeAPI 的子源并统一修改内部和外部展示名 by @DBJD-CR in #173 #183
- 气象预警图标优先使用本地图标，并拓展更多气象预警图标 by @DBJD-CR in #181 #187 #203
- 将所有数据源别名、展示名称映射以及连接展示元数据集中到单一的后端注册表 by @DBJD-CR in #185
- 将合并转发消息的构造与发送集中到可复用的辅助工具中，并更新多个命令，显式使用合并转发进行回复 by @DBJD-CR in #191
- 抽取省份/文本格式化公共工具，并在气象、统计和站点查询中复用 by @DBJD-CR in #193
- 引入统一的严重程度指示 emoji 模块，并在地震、气象、海啸、台风、AQI 以及排名展示中统一使用 by @DBJD-CR in #194
- 移除基于旧版的地震模拟 API 及相关 UI 弹窗样式，将模拟能力整合到新的多灾害模拟系统中 by @DBJD-CR in #196

### ⚡ Performance (性能优化)

- 优化通知中心启动为异步，避免阻塞启动主流程 by @DBJD-CR in #148
- 大幅优化了事件列表页面中的事件加载速度 by @DBJD-CR in #201

### 🐛 Bug Fixes (修复)

- 加固 Websocket 连接生命周期，避免挤占上游连接配额 by @DBJD-CR in #148
- 修复 Global Quake 不支持最终报的问题 by @DBJD-CR in #154
- 修复震度 5弱 以上时的震度解析问题并修正 PLUM 占位震级，优化展示文本 by @DBJD-CR in #177 #180
- 修复并优化有关海啸事件的去重问题 by @DBJD-CR in #177
- 修复升级/降级类预警颜色识别错误与机构名提取遗漏的问题 by @DBJD-CR in #180
- 修复了 `Task was destroyed but it is pending!` 的事件循环噪音 和 `GeneratorExit` 报错 by @DBJD-CR in #184
- 修复气象预警查询误匹配预警类型的问题 by @DBJD-CR in #193
- 改进 websocket 重连通知，避免在重连间隔非常短时出现误报的「离线时间过长」提示 by @DBJD-CR in #194
- 修复网络错误被误判为 SSL 错误并导致提前停止重连的问题 by @DBJD-CR in #206

### 🛡️ Stability & Security (稳定性与安全)

- 提升卡片截图浏览器的渲染健壮性，包括浏览器/页面健康检查、自动重建池、字体就绪超时以及更好的错误处理 by @DBJD-CR in #154
- 优化了启动时序，推迟浏览器预热，避免出现 `创建页面 1 超时` `浏览器初始化失败` `捕获未处理的异步异常: Task exception was never retrieved` 等报错 by @DBJD-CR in #191

### 🛠️ Commands (指令系统)

- 新增 `/台风信息查询` 命令 by @DBJD-CR in #147
- 新增 `/snet` 命令 by @DBJD-CR in #150
- 新增 `/JMA震央分布` 和 `/JMA震央分布绘图` 命令 by @DBJD-CR in #161
- 新增 `/生成沙滩球` 和 `/节面解析` 命令 by @DBJD-CR in #174
- 优化了数据源离线通知的文案 by @DBJD-CR in #184
- 新增 `/雷达` `/雷达动图` `/雷达列表` 命令 by @DBJD-CR in #188
- 新增 `/气温排行` `/最低气温排行` `/降水排行` `/风速排行` 命令 by @DBJD-CR in #189
- 新增 `/气象站实况` `/气象站历史` `气象站列表` 命令 by @DBJD-CR in #191
- 新增 `/空气质量` `/空气质量排行` `/空气质量列表` 命令 by @DBJD-CR in #193
- 新增 `/地震动预测` 和 `/本地地震动预测` 命令 by @DBJD-CR in #195
- 升级了 `/灾害预警模拟` 命令，支持全灾种简单模拟 by @DBJD-CR in #196
- 新增 `/降水量预报` 和 `/降水量预报动图` by @DBJD-CR in #197
- 优化 `/灾害预警重连` 命令，新增重连结果反馈与展示名优化 by @DBJD-CR in #204
- 新增 `/灾害预警重启` 和 `/重启AstrBot` 命令 by @DBJD-CR in #207

### 📚 Documentation (文档)

- 新增 EQSC API 文档 by @DBJD-CR in #147 #175
- 新增 OpenQuakeAPI 文档并移除了过时的 Global Quake (HTML) 文档 by @DBJD-CR in #173
- 更新各文档至最新官方版本 by @DBJD-CR in #191 #210 #214
- 更新适用于 v1.6.0 的 README 文档、贡献指南和更新日志 by @DBJD-CR in #210

~~### 🧪 Testing & CI (测试与CI)~~

### 📦 Dependencies (依赖)

- 新增依赖 `Pillow>=10.0.0` by @DBJD-CR in #150

### 🔧 Chore (杂项)

- 调整日志打印风格 by @DBJD-CR in #142 #203
- 优化了部分日志的打印级别 by @DBJD-CR in #151 #200
- 现在日志在打印渲染耗时可以显示对应的卡片类型并优化相关渲染时误报 by @DBJD-CR in #172 #192
- 支持过滤对应事件流的日志 by @DBJD-CR in #180 #181
- 优化了项目内的行内导入情况 by @DBJD-CR in #192
- 为模拟事件消息增加 `[模拟]` 标识 by @DBJD-CR in #196
- 优化全链路日志输出策略，削减高频 DEBUG 刷屏 by @DBJD-CR in #200
- 增强了原始消息日志中的 JSON 字段格式化功能 by @DBJD-CR in #203

---

## ❤️ New Contributors

- @coderabbitai[bot] made their first contribution in #151
- @chatgpt-codex-connector[bot] made their first contribution in #151
- @qodo-free-for-open-source-projects[bot] made their first contribution in #192

此外还要感谢 @Grok4.5（已降智）、@DeepSeekV4Flash（已涨价）、@GLM5.2（更贵了） 在本版本开发中的杰出表现。
以及 @coderabbitai[bot]、@sourcery-ai[bot]、@qodo-free-for-open-source-projects[bot] 兢兢业业的 Review。

---

> [!CAUTION]
> FAN Studio 与 EQSC 数据源现在需要鉴权，为了正常使用本插件功能，更新后请务必阅读 README 中的 `🔑 数据源鉴权引导` 章节，并根据引导完成鉴权。

> [!WARNING]
>
> 在新版本中，我们将 FAN Studio 的气象预警改为默认关闭，转为默认使用插件自建源的全量气象预警。
>
> 如果感觉气象预警的推送数量过多，请善用我们新增的聚合推送相关功能与原有的过滤功能。
>
> 如果发现数据库大小增长过快或存储空间紧张，可尝试**关闭** `记录气象预警正文` 配置项。
>
> 此外还有部分新增的数据源为默认开启，也有部分数据源被调整为默认关闭。如果不希望被无关的消息打扰，更新后请检查各数据源的启用状况。
>
> `附加中国区县烈度预估` 和 `附加日本地域震度预估` 配置项默认关闭，您可以根据自己的情况决定是否启用。
>
> 如果你发现插件启动时的 ASCII 艺术字出现了换行错位，在终端窗口一起按 `CTRL` 和 `-` 缩放终端大小即可正常显示。

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.6.0-beta.1...v1.6.0

<details>
<summary>点击查看历史更新内容</summary>

# 2026/07/12 v1.6.0-beta.1

## 🚀 What's Changed

### ✨ New Features (新功能)

- 新增数据导出导入与备份功能 by @DBJD-CR in #136
  - 你可以在插件 WebUI 的配置管理页全局配置的底部找到该功能的入口
- 新增插件内部日志分级 by @DBJD-CR in #137
  - 现在你可以在调试配置组里面调整日志输出，实现插件启动后的无感运行，避免大量事件刷屏（默认仍为全量打印）
- 新增会话备注名功能，并同步调整日志打印和指令输出 by @DBJD-CR in #138
- 迁移气象预警图标系统并补充本地回退机制 by @DBJD-CR in #139

### 🎨 Visualization (可视化与渲染)

- 微调了 Global Quake 卡片里最终报的样式 by @DBJD-CR in #137

### 📚 Documentation (文档)

- 更新适用于 v1.6.0-beat.1 的 README 文档、贡献指南和更新日志 by @DBJD-CR in #140

### 🔧 Chore (杂项)

- 更新 Github Actions 版本 by @dependabot[bot] in #130

---

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.5.3...v1.6.0-beta.1

# 2026/06/15 v1.5.3

## 🚀 What's Changed

### 🐛 Bug Fixes (修复)

- 适配了新的 Global Quake 数据格式推送 by @DBJD-CR in #135

### 🔧 Chore (杂项)

- 调整了 Websocket 连接断开时的重连策略 by @DBJD-CR in #135

---

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.5.2...v1.5.3

# 2026/06/15 v1.5.2

## 🚀 What's Changed

### 🐛 Bug Fixes (修复)

- 修复了降级重发策略不健壮导致的重复推送问题 by @DBJD-CR in [(f24287f)](https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/commit/f24287fb4551060bce67d2aeaa7691ddb9b200dc)

---

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.5.1...v1.5.2

# 2026/06/12 v1.5.1

## 🚀 What's Changed

### ✨ New Features (新功能)

- 增强远程模式下的浏览器截图能力与降级策略 by @DBJD-CR in #126

### 🐛 Bug Fixes (修复)

- 修复气象预警类型解析不准确与地区统计合并的历史遗留问题 by @DBJD-CR in #126
- 增强插件重载时的资源清理能力，避免端口冲突 by @Ayleovelle in #127

### 📚 Documentation (文档)

- 更新适用于 v1.5.1 的 README 文档、贡献指南和更新日志 by @DBJD-CR in #126

### 🔧 Chore (杂项)

- 更新 Github Actions 版本 by @dependabot[bot] in #105
- 更新依赖约束，增强与 AstrBot 的兼容性并清理未使用的包 by @Ayleovelle in #123
- 优化遥测事件上报，避免频繁触发 429 错误 by @DBJD-CR in #126

---

## ❤️ New Contributors

- @Ayleovelle made their first contribution in #123

---

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.5.0...v1.5.1

<details>
<summary>点击查看历史更新内容</summary>

# 2026/05/24 v1.5.0

经过了一个多月的开发，1.5.0 版本也是终于和大家见面了！为了后续更好的可拓展性与可维护性，我下了很大力气，几乎直接重构了整个插件。
现在，这个插件应该足以应付未来的挑战了。除了重构，还带来了一些小的功能提升与改进，以下是详细的内容：

## 🚀 What's Changed

### ✨ New Features (新功能)

- 为管理端上的高危操作添加二次密码验证 by @DBJD-CR in #111
- 新增数据源离线通知功能，当离线时间过长时自动发送离线通知 by @DBJD-CR in #111
- 新增 `docs/` 目录，用于存放插件开发所需要用到的内部及外部文档 by @DBJD-CR in #112
- 在前端中新增 **通知中心** 与 **文档浏览** 页面 by @DBJD-CR in #112
- 为前端的卡片组件添加了简单的动画 by @DBJD-CR in #114
- 增强了遥测中对 AstrBot 版本的获取方式 by @DBJD-CR in #116
- 增强了遥测错误与用户行为上报 by @DBJD-CR in #116
- 增强并拓展了数据库管理器的功能 by @DBJD-CR in #119
- 事件列表查询新增按关键词检索的功能 by @DBJD-CR in #119
- 事件列表查询新增按预警颜色 (气象预警) 和 预警级别 (海啸预警) 过滤的功能 by @DBJD-CR in #119
- 新增了一批 GitHub Issue 模板 by @DBJD-CR in #121

### ♻️ Refactor (重构)

- 后端模块一期重构 by @DBJD-CR in #106
  - 拆分了数个大文件中的核心巨类
  - 新增了几十个拆分文件职责的小文件
- 后端模块二期重构 by @DBJD-CR in #110
  - 移除 `filters/` 目录，由 `rules/` 取代
  - 移除 `handlers/` 目录，由 `parsers/` 取代
  - 移除 `support/` 目录，由 `services/` 取代
  - 移除 `models/` 目录，由 `domain/` 取代
  - 移除 `formatters/` 目录，由 `presenters/` 取代
  - 新增 `sources/` 文件夹，承接部分原 `handlers/` 和 `models/` 文件夹所属的职责
- 前端模块一期重构 by @DBJD-CR in #119
  - 拆分 `style.css` 为多个文件职责较为清晰的样式文件
  - 拆分例如事件列表组件、配置渲染器等逻辑严重耦合的文件
  - 治理了多个组件中的内联样式，统一集中管理
  - 新增了一批 Hooks 和组件，以及数十个拆分出的 JS 文件

### ⚡ Performance (优化)

- 优化了前端页面进行明暗主题切换时的动画性能 by @DBJD-CR in #114
- 优化了在前端中进行会话差异配置时的体验，不再显示全局配置 by @DBJD-CR in #116
- 优化数据查询与网络传输/请求，提升前端中的数据加载速度 by @DBJD-CR in #119

### 🐛 Bug Fixes (修复)

- 修复了 `/地震列表查询` 指令在“无震度/烈度信息”场景下的异常渲染问题 by @DBJD-CR in #104
- 再次全面重构和增强了融合策略的稳定性 by @DBJD-CR in #104 #111
- 修复日志轮转完成的瞬间立刻读取新日志文件导致的空文件报错问题 by @DBJD-CR in #112
- 修复会话发送失败被误判为成功的问题，现在可以识别 AstrBot 未实际投递消息的场景 by @DBJD-CR in #114
- 修复并增强了国内地震高发地相关的统计逻辑 by @DBJD-CR in #119

### 📚 Documentation (文档)

- 更新适用于 v1.5.0 的 README 文档、贡献指南和更新日志 by @DBJD-CR in #106 #110 #112 #121

### 🔧 Chore (杂项)

- 优化了某个逆天 AI 的指令别名写法 by @DBJD-CR in #104
- 气象预警 emoji 映射表更新 by @DBJD-CR in #104
- 过滤高频率低价值的遥测错误上报 by @DBJD-CR in #104
- 优化了项目内的 logger 使用行为 by @DBJD-CR in #104 #115
- 为全项目的文件补充了更详细的注释 by @DBJD-CR in #110 #119 #121
- 移除了海啸信息中的发布时间显示 by @DBJD-CR in #111
- 修改 ruff 检查工作流固定使用的 ruff 版本为 0.14.2 by @DBJD-CR in #112
- 调整了重大事件回溯卡片中的判定标准 by @DBJD-CR in #114
  - 现在震级 ≥ M 6.0，气象预警为红色预警，才计入为重大事件 (有关海啸的不变)
- 为部分指令新增了一些别名，详见 README 文档
- 调整了部分指令的回复内容

---

## ❤️ New Contributors

- @codex made their first contribution in #104
- @roomote made their first contribution in #104

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.4.5...v1.5.0

# 2026/03/23 v1.4.5

本版本主要新增了两个新指令，新增了数据源离线通知，增强了融合策略的处理逻辑并修复了前端中事件列表的部分展示问题。

## 🚀 What's Changed

### ✨ New Features (新功能)

- 新增 `/地震预警查询` 指令及对应的 WebUI 状态卡片，用于查看各机构地震预警状态及无预警时长 by @DBJD-CR in #98
- 新增 `/气象预警查询` 指令及对应的 WebUI 快捷查询面板，支持按地区、类型、颜色查询气象预警，或按 ID 查询详情 by @DBJD-CR in #98
  - 也支持通过别名 `/地震预警` 和 `/气象预警` 触发
- 现在触发部分插件指令时将会使用引用回复消息 by @DBJD-CR in #98
- 数据源离线通知: 当数据源进入兜底重试或停止重连时，会推送系统消息，提高系统运维的可见性 by @DBJD-CR in #98
  - 支持配置专门用于接收离线通知的会话 by @DBJD-CR in #99
- 新增 CWA EEW (台湾中央气象署地震预警) 融合策略，支持 Fan (主) + Wolfx (副) 模式，可补充影响区域字段 by @DBJD-CR in #98
- 为前端中的重大事件回溯卡片添加可选的展示数量选择器 by @DBJD-CR in #98

### ♻️ Refactor (重构)

- 重构融合策略，实现精确匹配和双向缓存，融合结果更加健壮和准确 by @DBJD-CR in #98

### 🐛 Bug Fixes (修复)

- 修复了事件列表的排序逻辑 by @DBJD-CR in #98
- 修复了前端事件列表中历史报数的烈度/震度信息被覆盖的问题 by @DBJD-CR in #98
- 修复了前端事件列表中历史报数丢失时间信息的问题 by @DBJD-CR in #98

### 📚 Documentation (文档)

- 更新适用于 v1.4.5 的 README 文档和更新日志 by @DBJD-CR in #99

### 🔧 Chore (杂项)

- 更新 GitHub Actions 工作流程，以在代码检出、Python 环境设置和 GitHub 脚本执行等核心操作上使用最新的大版本 by @dependabot[bot] in #96
- 优化了部分 WebUI 组件在暗色模式下的视觉效果 by @DBJD-CR in #98
- 优化了海啸信息的去重逻辑 by @DBJD-CR in #98
- 更新数据库结构以支持新的查询功能和展示优化 by @DBJD-CR in #98
- 更新 `/地震列表查询` 指令在图片模式下的默认参数为 9，并解限最大传入参数至 50 条 by @DBJD-CR in #98
- SVG 元数据更新 by @DBJD-CR in #99
- 添加一个自动化的 GitHub Actions 工作流，用于构建插件并创建 Release 草稿 by @Aloys233 in #101

---

## ❤️ New Contributors

- @dependabot[bot] made their first contribution in #96
- @gemini-code-assist[bot] made their first contribution in #98

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.4.0...v1.4.5

---

# 2026/03/01 v1.4.0

本版本为插件 WebUI 的首次完整发布并全面支持精细化配置，更新重点聚焦于“可观测、可操作、可回溯、可配置”的一体化前端管理体验。现在可统一通过 Web 控制台完成绝大部分日常操作并实现在不同会话中的个性化配置，彻底告别共用一套全局配置的窘境。

## 🚀 What's Changed

### ✨ New Features (新功能)

- **WebUI 首次完整发布** by @DBJD-CR & @Aloys233 & @openai-codex[bot] in #25 #34 #68 #70 #72 #77 #78 #81 #83 #84 #85 #86 #89 #91 #93
  - 正式提供 `运行状态 / 事件列表 / 数据统计 / 配置管理` 四大页面，形成统一操作入口
  - 前端定位从“信息展示”升级为“日常值守 + 运维操作 + 配置维护”的一体化控制台
  - 支持设置密码校验

- **主视图能力一次性到位**
  - 状态视图：服务状态卡片、连接矩阵、实时动态与快捷操作区
  - 事件视图：事件分页、类型筛选、震级阈值、排序、数据源筛选、页码跳转
  - 统计视图：震级分布、趋势图、日历热力图、来源贡献榜、地区榜、日志统计卡片
  - 配置视图：基于 Schema 的动态配置渲染，覆盖布尔/数字/枚举/列表/对象等字段

- **状态与运维效率增强**
  - 新增状态页实时动态跑马灯，支持悬停暂停与无缝滚动
  - 连接矩阵统一展示 FAN / P2P / Wolfx / Global Quake 在线状态、重试次数、延迟与子数据源详情
  - 提供快捷运维入口：手动重连数据源、刷新控制台数据、一键清除统计
  - 模拟预警可视化：支持目标会话、测试模板与参数化输入（经纬度、震级、深度、地点）
  - 支持定位辅助回填经纬度，降低测试配置门槛

- **事件链路与回溯体验增强**
  - 同一事件按 event_id 聚合，支持“第 N 报”展开，清晰回溯多报演进
  - 单事件历史报文可展开查看完整报次链路，并标记最新报
  - 新增重大事件横向回溯时间轴，支持拖拽、长按连续滚动、双击快速跳边界
  - 事件徽章语义化展示：自动切换 `震度 / 烈度 / 震级` 样式与色阶
  - 全站时间展示统一按配置时区输出，减少跨时区部署下的阅读歧义

- **配置体系与持久化能力增强**
  - 支持配置页草稿自动保存，降低误操作导致的配置丢失风险
  - 支持配置分组展开/收起与全部展开/全部收起，提升大配置集维护效率
  - 会话差异配置界面化：可编辑会话级个性配置并查看覆写状态
  - 提供会话覆写清理入口，可一键回退到全局继承状态

- **视觉系统与交互体验升级**
  - 上线亮色/暗色主题切换与本地持久化，支持全站即时生效
  - 引入 MD3 风格视觉体系（色板、圆角、卡片层级与组件态）
  - 主框架采用玻璃态控制台风格，平衡信息密度与视觉舒适度
  - 首屏加载体验升级：启动骨架屏 + Boot Loader + 平滑退场 + 异常兜底隐藏
  - 顶部全局状态栏整合当前视图标题、WebSocket 指示、实时钟表与主题切换
  - 全局 Toast 通知替代阻塞式弹窗，反馈更连贯

- **工程化与跨端可用性增强**
  - 前端实时链路上线：WebSocket 实时更新与 HTTP 拉取协同
  - WebSocket 采用全局单例连接，支持断线重连与监听器复用
  - 增加滚动位置记忆，减少视图切换与异步刷新时的跳动打断
  - 侧栏补齐版本信息、仓库入口、插件目录打开等运维联动入口
  - 响应式布局覆盖桌面/平板/移动端/横屏，小屏侧栏自动转为顶部横向导航
  - 补充语义属性、读屏辅助文本与“减少动态效果”适配，增强可访问性
  - 前端按 `context / hooks / components / views` 模块化组织，便于后续持续迭代

- **精细化配置**: 为插件的前后端添加了完整的会话个性化配置支持 by @DBJD-CR in #85
- **配置校验**: 现在插件会自动纠正不合理的配置项 by @DBJD-CR
- **远程 Playwright 支持**: 允许用户配置浏览器运行模式和远程服务器地址 by @Aloys233 in #72 #77 #81
- **Protobuf 支持**: 支持 Protocol Buffers 格式的消息解析，增强 Global Quake 处理器功能 by @Aloys233 in #75 #79
- **添加数据库模块**: 实现更加简单高效的增删查改与数据管理 by @Aloys233 in #77 #80
- **原始消息记录器增强**: 现在同样支持解析二进制消息为友好可读格式 by @DBJD-CR in #81
- **自动化脚本**: 新增一键 ruff 脚本用于开发辅助 by @DBJD-CR in #81
- **气象预警过滤升级**: 现在不强制要求白名单为省份/直辖市等，您可以输入任意关键词进行更精细的过滤 by @DBJD-CR in #91
- **海啸解析能力增强**: 支持解析信息与预警两种级别的海啸信息，并显示更丰富的内容 by @DBJD-CR in #91

### ♻️ Refactor (重构)

- 将中国地震预警网(省级) 拆分为独立的数据源选项，支持与国家级预警网分别启用 by @DBJD-CR
- 将插件的模拟功能模块重构为单一文件便于集中统一管理与后续拓展 by @DBJD-CR in #81
- 将 `core/` 文件夹重构为 `app`、`message`、`network`、`storage`、`support`等子包，并相应更新导入路径 by @DBJD-CR in #85
- 将 CENC 融合策略的分支改为异步架构，避免阻塞同连接的后续任务 by @DBJD-CR in #89

### 🐛 Bug Fixes (修复)

- 修复日志文件大小、数量、条目数计算错误的问题，现在会包含轮转日志文件的大小，数量和条目数 by @DBJD-CR
- 修复了地震列表查询卡片中深度显示为 极浅 时的字体大小问题 by @DBJD-CR
- 修复停用插件后可能依然有残留连接的问题，增强稳定性 by @Clhikari in #76
- 修复了工作流失效的问题并调整生效范围 by @DBJD-CR & @Aloys233 in #81 #83
- 修复了地图瓦片渲染偶发不完整的问题 by @DBJD-CR in #81
- 修复了上游 API 字段变更导致的气象预警过滤失效问题，并且现在还会在格式化消息中显示副标题 by @DBJD-CR & @Aloys233 in #86 #88

### 📚 Documentation (文档)

- 修订适用于 v1.4.0 的 README 文档、更新日志和贡献指南，更新 PR 模板 by @DBJD-CR in #91
- 修订 DeepWiki 生成指南 by @DBJD-CR in #91

### 🔧 Chore (杂项)

- 调整了 WebSocket 的重连策略 by @DBJD-CR in #78
- 调整了部分配置项的默认值 by @DBJD-CR in #78 #91
- 日志打印行为调整 by @DBJD-CR in #85
- ruff format & fix by @DBJD-CR
- 调整 `数据源贡献` 为使用去重后的事件统计做为数据源 by @DBJD-CR
- 插件徽章 SVG 数据更新 by @DBJD-CR in #91
- 插件元数据中添加最低 AstrBot 版本要求为 v4.11.2+，并声明可用性为所有平台可用 by @DBJD-CR in #91

---

## ❤️ New Contributors

- @openai-codex[bot] made their first contribution in #81

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.3.9...v1.4.0

---

# 2026/02/05 v1.3.9

本次更新修复了 **中国地震预警网省级预警 (CEA-PR)** 数据源无法正常格式化的问题，并引入了 **启动自检机制** 以防止类似问题再次发生。同时，本次更新还包含大量的 **稳定性增强**、**性能优化** 与 **代码重构**，修复了多个潜在的资源泄露风险，并对底层网络连接与文件 I/O 进行了深度加固。

## 🚀 What's Changed

### 🐛 Bug Fixes (修复)

- **CEA-PR 修复**: 修复了中国地震预警网省级预警 (`cea-pr`) 数据源因缺少格式化器注册导致无法正常显示格式化消息，回退到基础格式的问题 by @DBJD-CR

### 🛡️ Stability & Security (稳定性与安全)

- **启动自检**: 新增注册表完整性自检机制，插件启动时会自动检查数据源、处理器、格式化器和配置的一致性，防止配置遗漏 by @DBJD-CR
- **资源泄露防护**:
  - 修复了插件初始化失败时可能导致后台任务（如遥测、心跳）残留的问题，现在会强制清理所有资源 by @DBJD-CR
  - 修复了 `aiohttp.ClientSession` 可能的泄露问题，确保在创建新会话前安全关闭旧会话 by @DBJD-CR
  - 修复了日志去重缓存可能无限增长导致的内存泄露风险，实现了 FIFO 清理策略 by @DBJD-CR
- **并发控制**:
  - 限制了 WebSocket 重连任务的并发数量，防止网络波动时产生“重连风暴” by @DBJD-CR
  - 为浏览器渲染服务添加了页面获取与信号量的超时控制，防止高负载下系统卡死 by @DBJD-CR
  - 实现了日志轮转的文件锁机制，防止多线程/协程竞争导致的文件损坏 by @DBJD-CR
- **潜在问题**:
  - 修复了 `translate_place_name` 在高频调用时潜在的阻塞主线程的问题 by @DBJD-CR
  - 修复了日志写入时潜在的因磁盘满等 IO 错误导致程序崩溃的问题 by @DBJD-CR
- **地图渲染**:
  - 延长了 `.map_ready` 的等待超时时间，提高地图加载成功概率 by @DBJD-CR

### ⚡ Performance (性能优化)

- **I/O 优化**:
  - **异步预加载**: 实现了 `fe_regions` 数据的异步预加载机制，彻底消除了同步文件读取阻塞事件循环的隐患 by @DBJD-CR
  - **异步日志**: 将日志写入操作移交到线程池执行，避免阻塞主事件循环 by @DBJD-CR
  - **原子化写入**: 地震列表缓存写入改为“写入临时文件 -> 原子重命名”的方式，防止写入中断导致文件损坏 by @DBJD-CR
- **超时控制**:
  - 为 WebSocket 握手过程添加了显式的超时控制，避免连接尝试无限挂起 by @DBJD-CR
  - 为定时获取地震列表的 HTTP 请求添加了 60 秒超时限制 by @DBJD-CR
- **缓存优化**:
  - 实现了时区对象缓存 (`_timezone_cache`)，避免频繁创建相同的 `timezone` 对象 by @DBJD-CR
  - 将高频调用的正则表达式预编译为模块级常量，提升格式化性能 by @DBJD-CR
- **时区优化**:
  - 优化部分文件中跨时区去重逻辑中的时区处理问题，引入了更准确的 IANA 时区支持 by @DBJD-CR

### ♻️ Refactor (重构)

- **自动映射**: 重构了数据源 ID 映射逻辑，实现了从模型定义到消息管理器的自动同步，消除了硬编码维护的风险 by @DBJD-CR
- **代码规范**:
  - 统一了浮点数转换逻辑，修复了部分处理器混用导入函数和基类方法的问题 by @DBJD-CR
  - 提取了 `ROMAN_TO_INT` 到工具类，消除了硬编码 by @DBJD-CR
  - 统一了所有数据处理器的数据提取逻辑 (`_extract_data`)，消除了大量重复代码 by @DBJD-CR
  - **转换逻辑统一**: 新增 `utils/converters.py` 工具类，统一了所有处理器的烈度/震度转换和数值转换逻辑，消除了大量重复代码 by @DBJD-CR
- **清理策略**:
  - 改进了临时文件清理逻辑，增加了文件数量上限检查（默认 256 个），并优先清理最旧的文件 by @DBJD-CR
  - 实现了过期事件的自动清理机制，防止内存无限增长 by @DBJD-CR

---

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.3.8...v1.3.9

---

# 2026/02/01 v1.3.8

Hot Fix For v1.3.7

> [!TIP]
>
> **有关地震关键词过滤的补充说明**：
>
> 我们在 v1.3.5 版本的更新中引入了基于关键词的地震事件过滤器，如果你要填写黑白名单，请注意：
>
> - 关键词填写应以 `省州市区/都道府县` 的级别填写， **请勿填写国家/地区名**，这会导致绝大部分符合推送条件的消息被过滤。
> - 关键词填写应该尽量简短 (避免填写完整的省市名，如 `XX省XX市`，根据过滤范围直接填 `浙江`、`杭州` 即可)。
> - ✅ 正确示例（精确过滤）：“新疆”、“西双版纳州”、“大同市”、“陇西县”、“宜蘭縣”、“千葉県”、“能登半島”、“宗谷地方”、“阿拉斯加”
> - ✅ 正确示例（模糊匹配）：“省”、“州”、“市”、“县”、“県”、“区”、“地区”、“道”、“附近”、“岛”、“海”、“沖”
> - ❌ 错误示例：“中国”、“台湾”、“日本”、“美国”

## 🚀 What's Changed

### ✨ New Features (新功能)

- 增强了消息格式化器的错误检测机制，现在当缺少格式化映射时会输出明确的警告日志，而不是静默回退到基础格式 by @DBJD-CR
- 增加了格式化器调用的异常捕获保护，确保单一格式化器出错不会导致整个推送流程中断 by @DBJD-CR
- 增加了处理器注册表的自检逻辑，启动时会自动检查是否所有定义的数据源映射都已正确注册，避免因配置遗漏导致的功能异常 by @DBJD-CR
- **可配置页面池**: 新增 `browser_pool_size` 配置项，允许用户调整浏览器页面池大小以优化并发处理能力（默认 2） by @DBJD-CR
- **遥测心跳**: 新增定时心跳数据功能，每12小时自动发送心跳数据（仅包含实例ID、时间戳和运行时长），用于统计活跃实例 by @Aloys233 in #64

### ⚡ Optimization (优化)

- **极速渲染优化**: 优化了浏览器等待策略，大幅提升卡片渲染速度（从 3-7s 提升至 1-4s） by @DBJD-CR
- **时间测量优化**: 心跳运行时长测量改用 `time.monotonic()` 单调时钟，避免系统时间调整带来的问题 by @Aloys233 in #64

### 🐛 Bug Fixes (修复)

- **BrowserManager 修复**: 修复了并发启动浏览器时可能导致 `TargetClosedError` 崩溃的问题，增加了初始化锁机制 by @DBJD-CR
- 修复了 CWA 地震报告因缺少映射关系导致无法解析，回退到基础格式化的问题 by @DBJD-CR
- 修复了在 Python 3.11 以下版本环境中因缺少 `tomllib` 标准库导致插件无法加载的问题。现在通过引入可选依赖 `tomli` 并增加兼容性逻辑来解决此问题 by @DBJD-CR

### 📚 Documentation & Chore (文档与杂项)

- **Workflows**:
  - 新增 **屎山代码检测** 工作流，自动评估代码质量并生成趣味报告 by @DBJD-CR
  - 新增并优化 **Stale** 工作流，支持多语言自动回复与统一的标签管理 by @DBJD-CR
  - 新增并升级 **Ruff** 代码检查工作流，支持智能生成详细报告与 PR 评论 by @DBJD-CR
- **Badges**: 新增 **屎山指数** 徽章，“含金量”拉满 by @DBJD-CR
- 更新适用于 v1.3.8 的 `README.md` 文档和 `CHANGELOG.md` by @DBJD-CR

---

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.3.7...v1.3.8

---

# 2026/01/31 v1.3.7

本次更新主要适配了 Fan Studio 上游 API 服务端点变更，支持推送中国地震预警网省级网地震预警，并对台湾地区的地震预警功能进行了升级。

## 🚀 What's Changed

### ✨ New Features (新功能)

- **CWA Upgrade (台湾中央气象署升级)**:
  - 适配了 Fan Studio 新的 `/cwa-eew` 接口，确保地震预警功能正常运行 by @DBJD-CR
  - 新增 **台湾地震报告** (CWA Report) 数据源，支持接收包含震中图、等震度图的正式地震报告 by @DBJD-CR
  - 新增 `locationDesc` 字段解析，支持显示台湾地震预警的 **影响区域** 描述 by @DBJD-CR
  - 新增 `CWAReportFormatter` 消息格式化器，优化台湾地震报告的排版与图片展示 by @DBJD-CR
- **CEA Upgrade (中国地震预警网升级)**:
  - 适配了 Fan Studio 的 `/cea-pr` 接口，支持接收 **省级地震预警中心** 发布的地震预警信息 by @DBJD-CR
  - 新增 `province` 字段识别逻辑，当接收到省级预警时，标题将自动显示为“XX地震局”（如四川地震局） by @DBJD-CR

### 🎨 Visualization (可视化与渲染)
  
- **UI Polish (UI 润色)**:
  - 新增 **强降温预警** Emoji 图标映射 (📉🥶) by @DBJD-CR
  - 优化地震列表卡片的深度显示：0km 智能显示为“极浅”或“ごく浅い”，并自动适配中日文标签 by @DBJD-CR
  
### ♻️ Refactor (架构重构)

- **Config Update**: 更新配置文件结构，新增 `taiwan_cwa_report` 开关，允许用户独立控制预警和报告的推送 by @DBJD-CR
- **Router Logic**: 适配 `handler_registry` 的消息路由逻辑，根据消息特征智能分发至对应的 EEW 或 Report 处理器 by @DBJD-CR
- 移除获取 AstrBot 版本的静态方法，改为使用独立函数获取版本信息 by @Aloys233 in #58

### 🐛 Bug Fixes (修复)

- 修复了因上游 API 变更导致的 CWA 数据源解析错误的问题 by @DBJD-CR
- 修复了在 Windows 系统下时间格式化时因中文字符导致的 `UnicodeEncodeError` 报错 by @DBJD-CR
- 修复了遥测上报其他插件报错的问题 by @Aloys233 in #57
- 修复了 Wolfx 数据源日志过滤策略失效的问题，防止 HTTP 列表数据刷屏仅记录 WebSocket 列表摘要记录 by @DBJD-CR
- 修复了定时清理任务被打断导致临时文件夹 (`/temp`) 中旧图片文件堆积的问题，现在每次启动时会自动清理残留文件 by @DBJD-CR

### 📚 Documentation & Chore (文档与杂项)

- 根据 AI (KIMI K2.5) 审核建议修改了多个代码文件 by @Aloys233 in #57
- 根据 @sourcery-ai[bot] PR review 建议修改了多个代码文件 by @DBJD-CR & @sourcery-ai[bot]
- 更新适用于 v1.3.7 的 `README.md` 文档和 `CHANGELOG.md` by @DBJD-CR & @sourcery-ai[bot]

---

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.3.6...v1.3.7

---

# 2026/01/27 v1.3.6

Hot Fix For v1.3.5

## 🚀 What's Changed

### 🐛 Bug Fixes (修复)

- 修复了预警消息重复推送的问题 in #55 by @Aloys233
- 修复并增强了遥测的错误上报功能 in #51 by @Aloys233
- 修复了未清理干净的函数调用 by @DBJD-CR

---

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.3.5...v1.3.6

---

# 2026/01/27 v1.3.5

> [!IMPORTANT]
>
> 由于本次更新重构了 UMO 的获取逻辑，更新后您需要**重新配置需要推送的会话**。你可以使用指令 `/sid` 来快速获取 UMO 或手动构造。
>
> 更多详细的更新内容可查阅 README 文档，感谢您的支持。

本次更新再次重构了插件的部分组件，并引入了 **Leaflet.js** 配合 D3.js 进行更强大的地图渲染。新增了用于帮助改进插件的 **遥测系统** 并调整了 **Wolfx 连接逻辑**，让 Wolfx 数据源也能正常使用。此外，我们还新增了多项实用指令与过滤器，让预警更加直观、精准与智能。

特别感谢 @Aloys233 在遥测系统上做出的贡献！🤝

## 🚀 What's Changed

### ✨ New Features (新功能)

- **Data Fusion (数据融合)**:
  - 新增 **CENC 地震情报融合策略**：智能合并 Fan Studio 与 Wolfx 的数据，利用 Wolfx 的烈度信息补充 Fan 的数据，同时解决 Wolfx 字段不稳定的问题 by @DBJD-CR
  - 增强 **JMA EEW 解析**：支持显示 PLUM 法、训练报、取消报及警报区域等详细信息 by @DBJD-CR
- **Smart Filter (智能过滤)**:
  - 新增 **全局地震关键词过滤器**：支持自定义黑白名单，控制推送范围 by @DBJD-CR
  - 新增 **中国省份常量列表 (Model)**：增强文件复用性，让代码更简洁 by @DBJD-CR
- **Commands (指令系统)**:
  - 新增 `/地震列表查询` 指令：支持查询历史地震记录，并配备了仿 `JQuake` 风格的精美 **卡片渲染模板** by @DBJD-CR
  - 新增 `/灾害预警推送开关` 指令：支持在群组/会话中快速开启或关闭推送功能 by @Aloys233 & @DBJD-CR in #44 & #46
- **Configuration (配置升级)**:
  - **UMO 重构**：全面重构会话构建逻辑，支持更灵活的多平台/多实例推送配置 by @DBJD-CR
  - 新增 **自定义时区** 配置项，优化跨时区服务器的时间显示问题 by @DBJD-CR
  - 新增 **气象预警图标** ，自动根据预警类型代码附加中国气象局官方预警图标 by @DBJD-CR

### 🎨 Visualization (可视化与渲染)

- **Map Engine Upgrade (地图引擎升级)**:
  - **Leaflet.js**：支持更强大的地图瓦片渲染功能 by @Aloys233 in #45
  - 新增适用于所有地震事件的 **基础地图瓦片渲染模板**，告别简陋的链接跳转 by @DBJD-CR
  - 新增用于 `/地震列表查询` 的仿 `JQuake` 风格的卡片模板 by @DBJD-CR
  - 改进了 EEW 类型数据源的地图瓦片渲染行为 by @DBJD-CR
- **UI Polish (UI 润色)**:
  - 优化震源深度显示：添加深度格式化函数，0km 显示为“极浅” by @Aloys233 in #45
  - 调整卡片样式中的部分文字描述 by @DBJD-CR

### ♻️ Refactor (架构重构)

- **Time Utils (时间工具)**：新增专用的时间解析、转换、格式化工具类，统一处理所有时间逻辑，增强鲁棒性 by @DBJD-CR
- **Telemetry (遥测)**:
  - 新增并简单重构了 **遥测管理器**，优化事件上报、错误处理逻辑并调整 Payload 结构以符合新版 API 规范 by @Aloys233 #47
  - 修复遥测管理器初始化时硬编码插件版本的 Bug by @DBJD-CR
- **Connection Strategy (连接策略)**:
  - 重构 **Wolfx 处理器**，支持全量连接策略，解决因连接数限制导致的 503 错误 by @DBJD-CR
  - 移除废弃的 `/灾害预警测试` 指令及相关冗余代码，简化代码结构 by @Aloys233 in #44
  - 更多文件中的的代码清理与格式化工作以及日志调整 by @DBJD-CR

### 🐛 Bug Fixes (修复)

- **Logic**: 修复国内地区统计 Top10 未正确排除国外地震的 Bug by @DBJD-CR
- **Logic**: 修复 Wolfx JMA EEW 没有正确传导报数参数的问题 by @DBJD-CR
- **Logic**: 修复获取 EEW 事件指纹的逻辑，增强时间解析能力 by @DBJD-CR
- **System**: 修复部分函数调用参数错误的问题 by @DBJD-CR
- **Security**: 更新遥测管理器中的编码密钥以增强安全性 by @Aloys233 in #43

### 📚 Documentation (文档)

- **Badges**: 新增 **高仿 GitHub Trending** 与 **Plugin Market Rank** 徽章，排面拉满 by @DBJD-CR
- **Changelog**: 初始提交 - 新增符合 AstrBot v4.11.2+ 规范的 **插件更新日志文档** (`CHANGELOG.md`)，支持在 AstrBot WebUI 直接查看更新日志，并在插件更新完成时自动弹出窗口展示 by @DBJD-CR
- **Guide**: 更新适用于 v1.3.5 的 `README.md` 和 `CONTRIBUTING.md`，以及众多插件文件中的注释 by @DBJD-CR

---

> 下个大版本中将会推出插件自己的 WebUI 并实现精细化的配置管理，敬请期待！

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.3.1...v1.3.5

---

# 2026/01/12 v1.3.1

> [!WARNING]
> 以下内容由 AI 生成，我只做了简单润色，请仔细甄别

本次更新将灾害预警插件的体验提升到了全新的高度，引入了基于 Playwright 的现代化卡片渲染引擎。同时，我们重构了气象预警过滤器、报数控制器以及底层的网络连接模块，使插件更加稳定、强大且易于配置。

特别感谢 @Aloys233 为本次更新带来的精美卡片模板与渲染逻辑！🎨

## 🚀 What's Changed

### ✨ New Features (新功能)

- **Global Quake Visualization**:
  - 新增 Global Quake **消息卡片推送**功能，支持异步渲染，告别纯文本时代 by @Aloys233 in #21
  - 新增 **Aurora (极光)** 和 **DarkNight (暗夜)** 等多款精美卡片主题模板 by @Aloys233 in #22
  - 新增震中标记描边效果，微调极光主题样式以提升可视性 by @DBJD-CR
  - 新增卡片开关配置项，用户可自由选择文本或图片模式 by @DBJD-CR
- **Weather Filter 2.0**:
  - 全新的气象预警过滤器，支持按 **省份/地区白名单** 进行精准投递 by @DBJD-CR
  - 支持按 **预警颜色级别** (🔵/🟡/🟠/🔴) 进行过滤 by @DBJD-CR
  - 优化气象预警展示，为不同类型的灾害添加了专属 **Emoji 图标** by @DBJD-CR
- **Granular Control**:
  - **拆分报数控制器**：将原本全局统一的报数限制拆分为三套独立配置 (CEA/CWA, JMA, Global Quake)，默认值更科学 by @DBJD-CR
  - 新增 **插件启动静默期** 配置，防止重启时旧消息刷屏 by @DBJD-CR
  - 新增 **管理员配置项** 与指令权限分级逻辑 by @DBJD-CR
- **Statistics**:
  - 新增 `StatisticalManager` 统计管理器，支持更丰富和统一的事件记录 by @DBJD-CR
  - 新增气象预警与 CENC 地震测定的 **地区统计功能** by @DBJD-CR
  - 回归并增强 `/灾害预警统计` 指令，新增 `/灾害预警统计清除` 指令 by @DBJD-CR

### ♻️ Refactor (重构)

- **Network Overhaul**: **彻底移除 `websockets` 库依赖**，全面迁移至 `aiohttp` 重构 WebSocket 连接管理，解决兼容性问题并提升稳定性 by @DBJD-CR
  - **Command System**:
  - 重构 `/灾害预警状态`，提供数据源状态、运行时间等更有价值的调试信息 by @DBJD-CR
  - 重构 `/灾害预警配置`，现在直接返回完整的 JSON 配置内容，所见即所得 by @DBJD-CR
- **Log Optimization**: 优化 Wolfx 数据源的日志记录逻辑，支持配置最大记录数，防止 HTTP 轮询导致日志文件冗余 by @Aloys233 & @DBJD-CR in #20
- **Image Cache**: 实现了图片缓存文件的自动清理机制，防止磁盘空间占用过大 by @DBJD-CR

### 🐛 Bug Fixes (修复)

- **Critical**: 修复了自动清理任务中因时区问题导致的报错 by @DBJD-CR
- **Logic**: 修复了“只推送最终报”功能失效的问题 by @DBJD-CR
- **Logic**: 修复了 KMA (韩国气象厅) 消息被错误识别为 CWA (台湾中央气象署) 的问题 by @DBJD-CR
- **Config**: 修复了推送间隔为 0 时逻辑判断错误的问题 by @Aloys233 in #21
- **System**: 修复了临时文件路径创建逻辑，确保正确使用 AstrBot 提供的数据目录 by @Aloys233 & DBJD-CR in #22 & #26
- **Data**: 修复了 Wolfx 地震信息测定字段解析错误的问题 by @DBJD-CR

### 📚 Documentation & Chore (文档与杂项)

- **Docs**: 更新适用于 v1.3.1 的 README 文档，补充新功能说明 by @DBJD-CR
- **Deps**: 更新 `requirements.txt` 依赖列表 (新增 `playwright` 等) by @DBJD-CR
- **UI**: 为 WebUI 的数值配置项添加滑动条组件 (Slider) 支持 by @DBJD-CR
- **I18n**: 调整 MaxIntensity 键名映射与 Emoji 映射行为 by @DBJD-CR

---

## ❤️ New Contributors

- @sourcery-ai[bot] made their first contribution (Grammar fix) in #26

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.2.3...v1.3.1

---

# 2026/01/01 v1.2.3

本次更新主要修复了 Fan 数据源的连接问题，并新增了气象预警过滤白名单功能。

## 🚀 What's Changed

### ✨ New Features (新功能)

- 新增按省份（包括直辖市与港澳台地区）过滤气象预警  by @Aloys233 in #19

### ♻️ Refactor (重构)

- Fan Studio API 使用新的 `/all` 路径建立连接，减少重复连接和资源浪费 by @DBJD-CR
- 设计了新的兜底重试机制，将原有的重连机制改为短时间内的重连行为与长时间的自动重试机制结合，并提取为常量支持在 WebUI 中进行配置 by @Aloys233 in #19

### 🐛 Bug Fixes (修复)

- 修复日本气象厅（JMA）：紧急地震速报选项失效的问题 by @Aloys233 in #17
- 修复了本地预估烈度功能没有正常工作的问题 by @Aloys233 in #18
- 修复了部分数据源遇到整数震级时的小数点位数显示问题，统一显示一位小数 by @Aloys233 in #19

### 📚 Documentation & Chore (文档与杂项)

- 更新适用于 v1.2.3 的 README 文档 by @DBJD-CR
- 使用 Ruff 格式化代码并修复潜在问题 by @DBJD-CR

---

> 我们在 1.3.0 版本中会专注于优化推送范围和过滤等社区反馈的问题，并引入新的消息卡片，敬请期待！

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.2.2...v1.2.3

---

# 2025/12/24 v1.2.2 (功能完备版)

> [!WARNING]
> 以下内容由 AI 生成，我只做了简单润色，请仔细甄别

本次更新正式完成了对  **Global Quake** 数据源的支持，引入了专用过滤器和更丰富的数据字段，并修复了主备服务器切换机制失效等关键问题。自此，本插件终于完整实现了发布之初时的所有功能描述。

感谢 @Aloys233 在接入 Global Qukae 数据源上的贡献！

## 🚀 What's Changed

### ✨ New Features (新功能)

- 新增 Global Quake 专用过滤器配置选项，支持更精细的推送控制 by @DBJD-CR
- 支持显示 **最大加速度 (PGA)** 和 **触发测站数量** 字段 by @DBJD-CR
- 优化震级和深度的格式化显示，确保与其他数据源风格一致 by @Aloys233 in #11
- 全面修改过滤器逻辑为 **OR (或)** 关系，现在只要满足任意一个启用过滤器的条件即会推送 by @DBJD-CR

### ♻️ Refactor (重构)

- 拆分数据处理器 (`data_handlers`) 和消息格式化器 (`message_formatters`)，代码结构更清晰 by @Aloys233 in #10
- 规范包的导出结构，优化模块引用 by @DBJD-CR
- 移除重构后的冗余逻辑代码 by @DBJD-CR

### 🐛 Bug Fixes (修复)

- 修复了主备服务器切换机制失效的问题，提升服务可用性 by @DBJD-CR
- 修复了因 API 字段变动导致 CWA 推送被错误过滤的问题 by @DBJD-CR

### 📚 Documentation & Chore (文档与杂项)

- 更新适用于 v1.2.2 的 README 文档 by @DBJD-CR
- 使用 Ruff 格式化代码并修复潜在问题 by @DBJD-CR

---

> 我们在下个版本中会专注于优化推送范围和过滤等社区反馈的问题，敬请期待！

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.2.0...v1.2.2

---

# 2025/12/20 v1.2.0

> [!WARNING]
> 以下内容由 AI 生成，我只做了简单润色，请仔细甄别

本次更新修复了 v1.1.0 中存在的众多 Bug 与推送问题，并引入了许多新功能。

特别感谢 @Aloys233 在本版本中的杰出贡献！🎉

## 🚀 What's Changed

### ✨ New Features (新功能)

- 新增本地烈度计算器（根据震级和距离估算），并支持 USGS 英文地名自动翻译为中文 by @Aloys233 in #5
- 新增 **Fan Studio JMA EEW** (日本气象厅紧急地震速报) 数据源支持 by @Aloys233 in #8
- 实现 Fan Studio 主/备服务器连接与故障自动切换逻辑 by @Aloys233 in #8
- 新增 `/灾害预警模拟` 命令，方便测试和预览预警效果 by @Aloys233 in #5
- 优化预估本地烈度在消息中的展示，使用 Emoji 图标直观展示烈度等级 by @DBJD-CR
- 添加气象预警去重缓存，防止短时间内因重连导致的重复推送 by @DBJD-CR

### ♻️ Refactor (重构)

- 将散落在根目录的代码文件归类至 `core/`, `models/`, `utils/` 等模块，并使其成为规范的 Python 包 by @Aloys233 in #5
- 重构 Global Quake 配置板块，移除冗余开关并合并相关设置 by @Aloys233 in #8
- 部分重构消息处理与过滤逻辑，移除无效的数据源配置与映射 by @DBJD-CR

### 🐛 Bug Fixes (修复)

- 修复了时区问题导致的时间窗口过滤失效 bug by @DBJD-CR
- 修复 WebSocket 连接成功后重试次数未重置的问题 by @Aloys233 in #5
- 修复查看 `/灾害预警状态` 命令失效的问题 by @Aloys233 in #5
- 修复了部分数据源映射错误 by @Aloys233 in #8

### 📚 Documentation & Chore (文档与杂项)

- 添加 `CONTRIBUTING.md` (贡献指南) 和 `CODE_OF_CONDUCT.md` (行为准则) by @DBJD-CR
- 更新适用于 v1.2.0 的 README 文档 by @DBJD-CR
- 使用 Ruff 格式化代码 by @DBJD-CR
- 更新 `.gitignore` 忽略 IDE 配置文件 by @Aloys233 in #5

---

## ❤️ New Contributors

- @Aloys233 made their first contribution in #5

**Full Changelog**: https://github.com/DBJD-CR/astrbot_plugin_disaster_warning/compare/v1.1.0...v1.2.0

---

# 2025/12/13 v1.1.0

> [!WARNING]
> 以下内容由 AI 生成，我只做了简单润色，请仔细甄别

本次更新对插件架构进行了深度重构，重点优化了多源数据的处理逻辑、推送策略及消息展示效果，旨在提供更精准、专业的灾害预警服务。

## 🚀 What's Changed

### ♻️ 核心架构重构

- **数据源处理细分**：针对不同数据源（CEA, CWA, JMA, USGS 等）实现了独立的解析与处理流程，确保每个数据源的特性（如字段定义、状态标识）都能被准确识别。
- **过滤器拆分**：将原有的全局过滤器拆分为“震级+烈度”和“震级+震度”两套独立系统，并新增 USGS 专用的震级过滤器，实现了更精细的阈值控制。
- **报数控制优化**：明确了报数控制的作用范围，仅对 EEW（紧急地震速报）类数据源生效，避免误拦截 CENC、USGS 等非报数类情报。

### ✨ 功能增强与优化

- **多源协同去重**：
  - 调整了去重策略，不再简单屏蔽后续数据源。现在允许多个数据源对同一事件进行推送，实现了多源信息的互补。
  - 强化了**单数据源内部去重**，有效防止同一数据源因网络波动或重复分发导致的刷屏问题。
- **专业消息格式化**：
  - 全新设计的消息模板，针对不同数据源定制了专属的 Emoji 和字段布局。
  - 实现了智能状态标识：
    - **CENC/USGS**：自动区分 [自动测定] 与 [正式测定]。
    - **JMA**：自动识别 [震度速报]、[震源相关情报] 与 [震源・震度情报] 以及更多情报类型。
    - **JMA EEW**：根据预估最大震度自动判断 [予报] 或 [警报]。
- **USGS 数据源优化**：新增了针对 USGS 的专用去重逻辑和状态升级机制（Automatic -> Reviewed），确保信息的准确性与时效性。

### 🛠️ 系统稳定性

- **日志系统适配**：全面适配新架构，确保所有数据源的原始消息都能被正确记录和格式化，保留了核心的垃圾信息过滤功能。
- **配置结构调整**：优化了配置文件结构，支持更细粒度的数据源开关和参数设置。

### 🐛 问题修复

- 修复了原始消息记录器无法写入日志的问题。
- 修复了`测试预警命令失效`的问题。
- 修复了 WebSockets 库版本的兼容性问题，并添加依赖版本控制。@jinyiwei2012 in #4

---

## New Contributors

- @jinyiwei2012 made their first contribution in #4

---

# 2025/12/06 v1.0.0

> [!WARNING]
> 以下内容由 AI 生成，我只做了简单润色，请仔细甄别

# 🚀 AstrBot 灾害预警插件 v1.0.0

AstrBot 多数据源灾害预警插件的首个发行版。

## ✨ 主要功能

- **多数据源支持**：USGS、JMA、中国地震台网、气象预警、P2P地震网络......
- **智能消息处理**：重复事件过滤、类型分类、原始消息记录
- **灵活配置**：自定义推送规则、震级阈值、地区筛选

## 🚧 已知限制

- 已知部分地图服务商的缩放级别参数可能不生效
- 部分数据源无法写入原始消息日志
- Global Quake 服务基本处于不可用状态

</details>
