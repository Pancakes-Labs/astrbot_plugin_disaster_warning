(() => {
    /**
     * @file appState.js
     * @description 全局状态树的初始形态定义与常量枚举声明。
     * 
     * 架构设计与业务语境分析：
     * 1. 单一数据源 (Single Source of Truth)：整个前端管理面板的所有数据形态均在此文件集中声明，
     *    不仅作为运行时的零值填充，还承担着前端数据模型的 Schema 自文档化职责。
     * 2. 数据隔离设计：状态机被逻辑切分为：
     *    - config: 系统基础元配置（如时区，用来纠正多国发震时间差）。
     *    - status: 宿主进程的活性心跳数据（包括 Uptime 本地计算源起点）。
     *    - stats: 大屏统计与图表分析所需的聚合结构。
     *    - events: 活跃与历史事件的缓存数组。
     *    - connections: 三方防灾警报推送网络连接质量矩阵。
     *    - notifications: 管理员通知信箱列表。
     *    - markdownFiles: 系统操作及二次开发离线说明书。
     * 3. 命名空间绑定：由于前端采用无模块化直接注入，这些常量与结构体均挂载在全局 `window` 域下以便跨文件直接引用。
     */

    /**
     * 全局状态派发动作类型 (Action Types)
     * 定义了状态机能响应的所有用户交互、API 同步与 WebSocket 异步推送行为。
     */
    const AppActionTypes = {
        UPDATE_STATUS: 'UPDATE_STATUS',                  // 同步服务运行心跳、硬件负载、子源检测状态
        UPDATE_CONFIG: 'UPDATE_CONFIG',                  // 同步系统显示时区与服务器环境设定
        UPDATE_STATS: 'UPDATE_STATS',                    // 重新载入全量灾害分析、极值、排行榜与图表坐标集
        UPDATE_CONNECTIONS: 'UPDATE_CONNECTIONS',        // 更新 WebSocket 数据源的连接质量与网络延迟 (Latency)
        ADD_EVENT: 'ADD_EVENT',                          // 由实时推送通道捕获并插入新发生的重大预警/地震速报
        SET_WS_CONNECTED: 'SET_WS_CONNECTED',            // 标记管理面板后台 WebSocket 的断线或重连状态
        SET_NOTIFICATIONS: 'SET_NOTIFICATIONS',          // 填充最新的未读或已读全局业务通知
        SET_NOTIFICATIONS_META: 'SET_NOTIFICATIONS_META',// 同步通知相关的计数与最后同步戳
        SET_MARKDOWN_FILES: 'SET_MARKDOWN_FILES',        // 写入离线指南 Markdown 文件路径资源树
        SET_MARKDOWN_DOCUMENT: 'SET_MARKDOWN_DOCUMENT',  // 写入正在被阅读的目标 Markdown 解密正文与元数据
        SET_SELECTED_MARKDOWN_PATH: 'SET_SELECTED_MARKDOWN_PATH', // 设置选中的文档树路由 Key
        TOGGLE_THEME: 'TOGGLE_THEME',                    // 暗黑/明亮视觉模式切换开关
    };

    /**
     * 初始全局状态树原型
     * 声明了各业务模块的默认初始值，防止在异步拉取完成前发生致命的 `undefined` 渲染中断。
     */
    const initialState = {
        // 配置相关状态
        config: {
            apiUrl: '',                                  // 后台服务网关 Base URL
            displayTimezone: 'UTC+8',                    // 默认显示时区，用以将 UTC 发震事件转化为本地时间
        },
        // 核心服务状态指标
        status: {
            running: false,                              // 防灾主线程后台监听服务状态开关
            uptime: '--',                                // 累计不间断运行时长，格式如: "2天5小时3分钟"
            startTime: null,                             // 服务实例的绝对启动时间戳 (Date 对象)，用于本地跳秒基准
            activeConnections: 0,                        // 当前维持正常握手的心跳长连接数
            totalConnections: 0,                         // 注册的外部总连接提供商个数
            version: '未知版本',                          // 插件系统软件版本号
            eewQueryStatus: null,                        // EEW 本地秒级轮询器的连接健康摘要
        },
        // 灾害多维分析统计数据库
        stats: {
            totalEvents: 0,                              // 历史捕捉累计灾害警报总次数
            earthquakeCount: 0,                          // 地震相关事件频次总和
            warningCount: 0,                             // 气象/地质活动警报频次总和
            tsunamiCount: 0,                             // 海啸预警频次总和
            weatherCount: 0,                             // 细分气象灾害警报次数
            maxMagnitude: null,                          // 观测周期内的全球最大发生震级 (M 级极值)
            earthquakeRegions: [],                        // 频发地震的区域及频次 Top10 排行数据集
            weatherRegions: [],                          // 气象警报高发区域 Top10 排行数据集
            weatherLevels: [],                           // 气象等级(红橙黄蓝)分布频数，用于 Conic-gradient 占比图
            weatherTypes: [],                            // 气象预警类别（雷电、暴雨、台风等）分布饼图源数据
            dataSources: [],                             // 各数据源提供的警报贡献值排行
            logStats: null,                              // 日志拦截分析统计（含已分配空间及命中效率）
        },
        connections: {},                                 // 当前连接节点的质量状态索引，以各 Connection ID 为 Key
        events: [],                                      // 当前加载在时间轴与大厅的灾害事件快照列表（排重滑动窗口模式）
        lastEvent: null,                                 // 最新发生的一起重大防灾事件，常用于弹窗与播报组件的紧急中断提醒
        magnitudeDistribution: {},                       // 地震震级区间直方图映射表，形如 { "3.0-3.9": 45, "4.0-4.9": 12 }
        wsConnected: false,                              // 管理员控制台与主服务间的 Web 通信状态
        notifications: [],                               // 全局非阻塞通知体缓冲队列
        notificationsMeta: {                             // 通知状态元数据
            unread_count: 0,                             // 系统未读通知数目
            last_sync_at: '',                            // 最后一笔通知同步握手时刻
            total_count: 0,                              // 累计通知条数
        },
        markdownFiles: [],                               // 离线使用文档索引结构数组
        markdownDocument: null,                          // 当前文档渲染器的数据源载荷
        selectedMarkdownPath: '',                        // 被点亮的高亮文档相对路径
        // 全局 UI 主题，启动时优先读取用户上一次的偏好设置；
        // 未显式选择时跟随系统 prefers-color-scheme，避免与首屏主题拦截器（html 预挂 theme-dark 类）不一致导致闪烁
        theme: (function () {
            try {
                const saved = localStorage.getItem('theme');
                if (saved) return saved;
                if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
                return 'light';
            } catch (e) {
                return 'light';
            }
        })(),
        dataLoaded: false,                               // 核心握手成功标识，用于全局解密并展示控制面板界面
    };

    // 挂载至 window 命名空间以保证全局跨组件的可访问性
    window.AppActionTypes = AppActionTypes;
    window.initialAppState = initialState;
})();
