(() => {
    /**
     * @file appReducer.js
     * @description 全局状态机的核心控制中心。基于 Redux-like 动作调度模型，
     * 用于处理应用中包括网络状态更新、配置更改、灾害数据注入、WebSocket 状态和通知系统的所有状态变更。
     * 
     * 架构设计规范与交互逻辑细节：
     * 1. 纯函数属性：此 Reducer 保证在相同 state 与 action 传入下始终输出预测一致的新 state，
     *    不产生任何副作用 (Side Effects)。
     * 2. 重复数据拦截：在添加新灾害事件时，通过 appendUniqueEvent 进行细粒度排重判别，
     *    防止由于网络瞬断、重连或者多路复用信道推送的重叠事件对前端产生二次渲染震荡。
     * 3. 统计载荷规整：在更新分析数据时，通过挂载的归一化处理器
     *    将复杂的嵌套统计数据展开并格式化为前端可直接渲染的坐标、区间和极值数组。
     */
    const ACTIONS = window.AppActionTypes;
    const normalizeStatsPayload = window.StatsNormalizer.normalizeStatsPayload;

    /**
     * 向状态树的事件列表中追加唯一的灾害事件
     * 
     * 业务拦截逻辑说明：
     * - 设置了上限 MAX_EVENTS = 100，对超期缓存事件进行淘汰以控制前端内存占用。
     * - 多维判重机制：优先使用唯一的 UUID (id) 进行核对，
     *   对于缺乏显式 ID 的底层警报，退化为通过“发震/警报时间戳 (event_time) + 灾害大类 (type)”的复合指纹来进行重叠截断。
     * 
     * @param {Object} state - 当前的全局 state 对象。
     * @param {Object} newEvent - 实时捕获推送的新灾害事件对象（包含地震、气象、海啸等）。
     * @returns {Object} 拼接排重并限制滑动窗口大小后的新 state。
     */
    function appendUniqueEvent(state, newEvent) {
        const MAX_EVENTS = 100;
        const isDuplicate = state.events.some((event) => (
            newEvent.id
                ? event.id === newEvent.id
                : event.event_time === newEvent.event_time && event.type === newEvent.type
        ));

        // 如果该事件已存在，直接透传原 state 引用，中断 React 协调器 (Reconciliation) 对下级树的二次审查，节约算力
        if (isDuplicate) {
            return state;
        }

        // 首部压入，尾部裁剪，始终保留最近的 100 条重大灾害快照
        const events = [newEvent, ...state.events].slice(0, MAX_EVENTS);
        return { ...state, events, lastEvent: newEvent };
    }

    /**
     * 全局核心 Reducer 分发器
     * 
     * @param {Object} state - 当前所处的只读全局状态树。
     * @param {Object} action - 派发的业务行为动作载荷。
     * @param {string} action.type - 行为类型标识。
     * @param {*} [action.payload] - 随动作附带的参数或新状态结构。
     * @returns {Object} 演算合并生成的深/浅拷贝新状态树。
     */
    function appReducer(state, action) {
        switch (action.type) {
            // ==================== 运行状态与心跳维护 ====================
            case ACTIONS.UPDATE_STATUS:
                // 收到心跳响应，追加合并状态，并将 dataLoaded 激活为 true，以便解除整个管理面板的初始化骨架屏遮罩
                return { ...state, status: { ...state.status, ...action.payload }, dataLoaded: true };
            
            // ==================== 配置系统响应 ====================
            case ACTIONS.UPDATE_CONFIG:
                return { ...state, config: { ...state.config, ...action.payload } };
            
            // ==================== 历史分析与图表归一化 ====================
            case ACTIONS.UPDATE_STATS: {
                // 利用 StatsNormalizer 将后端扁平或高维的统计聚合数据归类分流，直接切分到图表渲染各自所需的特定数据集
                const normalized = normalizeStatsPayload(action.payload || {});
                const nextEvents = normalized.events || [];
                const prevEvents = state.events || [];
                // 引用稳定性优化：WS 心跳广播（默认 30s 一次）会周期性携带 statistics 载荷，
                // 若内容与上次完全一致则直接透传原 state 引用，避免 events / stats 引用被重置，
                // 从而引发事件列表与重大事件时间轴的无谓静默全量重拉（首次加载/页面切换变慢的根因之一）。
                // 比较口径覆盖完整事件快照 + 震级分布 + stats 图表数据：
                // - 仅比较 id/event_id 会漏掉「同 ID 事件更新描述/震级/updated_at」与无 ID 事件误判；
                // - 仅比较 events/dist 会漏掉「统计数据变化但事件列表不变」时 StatsView 不更新的情况。
                // events 有 100 条上限，序列化开销远小于完整 stats 榜单数组，且早退后不再重复序列化。
                const eventsJson = JSON.stringify(nextEvents);
                const prevEventsJson = JSON.stringify(prevEvents);
                const distJson = JSON.stringify(normalized.magnitudeDistribution);
                const prevDistJson = JSON.stringify(state.magnitudeDistribution);
                const statsJson = JSON.stringify(normalized.stats);
                const prevStatsJson = JSON.stringify(state.stats);
                if (eventsJson === prevEventsJson && distJson === prevDistJson && statsJson === prevStatsJson) {
                    return state;
                }
                return {
                    ...state,
                    stats: normalized.stats,
                    events: nextEvents,
                    magnitudeDistribution: normalized.magnitudeDistribution,
                };
            }
            
            // ==================== 连接服务矩阵 ====================
            case ACTIONS.UPDATE_CONNECTIONS:
                return { ...state, connections: action.payload };
            
            // ==================== 实时事件消息推送 ====================
            case ACTIONS.ADD_EVENT:
                return appendUniqueEvent(state, action.payload);
            
            // ==================== WebSocket 长连接状态芯片 ====================
            case ACTIONS.SET_WS_CONNECTED:
                return { ...state, wsConnected: action.payload };
            
            // ==================== 系统通知与同步元数据 ====================
            case ACTIONS.SET_NOTIFICATIONS:
                // 历史消息通知通知体队列注入
                return { ...state, notifications: action.payload || [] };
            case ACTIONS.SET_NOTIFICATIONS_META:
                // 消息未读计数、最后同步时间戳等元信息管理
                return {
                    ...state,
                    notificationsMeta: action.payload || {
                        unread_count: 0,
                        last_sync_at: '',
                        total_count: 0,
                    },
                };
            
            // ==================== 离线 Markdown 使用文档渲染 ====================
            case ACTIONS.SET_MARKDOWN_FILES:
                // 文件资源树形列表
                return { ...state, markdownFiles: action.payload || [] };
            case ACTIONS.SET_MARKDOWN_DOCUMENT:
                // 当前正处于阅读视图的解析后 Markdown 结构
                return { ...state, markdownDocument: action.payload || null };
            case ACTIONS.SET_SELECTED_MARKDOWN_PATH:
                // 选中的文档相对路径标识
                return { ...state, selectedMarkdownPath: action.payload || '' };
            
            // ==================== 全局视觉风格切换 ====================
            case ACTIONS.TOGGLE_THEME: {
                const nextTheme = state.theme === 'light' ? 'dark' : 'light';
                // 顺便进行 localStorage 久性同步，确保管理员下次打开控制台时能获得期望的暗黑舒适度
                localStorage.setItem('theme', nextTheme);
                return { ...state, theme: nextTheme };
            }
            
            default:
                return state;
        }
    }

    // 全局空间注入，以供 AppProvider 在顶层顺利消费
    window.appReducer = appReducer;
})();
