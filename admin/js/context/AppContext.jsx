const { createContext, useContext, useReducer, useEffect } = React;

/**
 * @file AppContext.jsx
 * @description 灾害预警插件管理面板的 React 全局上下文状态管理器。
 * 
 * 架构设计模式与技术要点分析：
 * 1. 核心状态总线：使用 React Context API 构建轻量级全局状态总线 (Event/State Bus)，
 *    避免多层级组件树中繁琐的 Props 逐级传递 (Prop Drilling)。
 * 2. 状态与逻辑解耦：本组件作为全局控制中枢，仅负责状态的分发与副作用逻辑编排；
 *    具体的状态变更操作委派给 appReducer.js 的纯函数 reducer 处理，
 *    而初始状态结构则归属 appState.js 统一声明，体现了关注点分离 (Separation of Concerns) 理念。
 * 3. 异步操作原子化：通过封装 useCallback 钩子提供幂等的异步数据拉取接口，
 *    包括状态更新、网络连接检查、配置获取以及数据统计同步。
 *    这些接口被注入至 Context 载荷中以供下级视图组件按需调用。
 */
const AppContext = createContext();

/**
 * 状态归一化转换函数
 * @description 将后端返回的下划线命名法 (snake_case) 数据规整为符合前端命名规范的驼峰命名法 (camelCase)，
 * 并在此处进行基础的数据清洗与异常容错（如版本缺省降级处理、时间戳解析等）。
 * 
 * @param {Object} data - 从后端 API `/api/status` 接口接收到的原始状态数据字典。
 * @param {string} previousVersion - 缓存的上一轮系统版本号，用于异常缺失时的回退策略。
 * @param {Date|null} [previousStartTime=null] - 上一轮已解析的启动时间，用于稳定 Date 引用。
 * @returns {Object} 包含标准化属性的系统状态对象。
 */
function toStatusUpdate(data = {}, previousVersion = '未知版本', previousStartTime = null) {
    const statusUpdate = {
        running: data.running,                                     // 服务运行状态标识 (布尔型)
        activeConnections: data.active_connections,               // 当前活跃网络连接数 (整型)
        totalConnections: data.total_connections,                 // 注册的总连接服务数 (整型)
        subSourceStatus: data.sub_source_status,                   // 各子数据源心跳与健康检查树状结构
        eewQueryStatus: data.eew_query_status || null,             // 紧急地震速报 (EEW) 的轮询与连接状态
        version: data.version || previousVersion,                  // 系统当前运行的固件/插件版本
    };

    // 若后端提供了服务启动时间，则转换为前端标准的 Date 实例，以便本地进行精确的跳秒运行时长计算。
    // 启动时间未变化时复用旧 Date 引用，避免触发 uptime 本地计时 effect 反复重建。
    // 有 start_time 时由前端本地跳秒独占 uptime 字段，避免与服务端 uptime 字符串互相覆盖造成前后跳秒。
    if (data.start_time) {
        const nextStartTime = new Date(data.start_time);
        const nextStartTimeMs = nextStartTime.getTime();
        const previousStartTimeMs = previousStartTime instanceof Date
            ? previousStartTime.getTime()
            : null;

        if (
            previousStartTime instanceof Date
            && !Number.isNaN(previousStartTimeMs)
            && previousStartTimeMs === nextStartTimeMs
        ) {
            statusUpdate.startTime = previousStartTime;
        } else if (!Number.isNaN(nextStartTimeMs)) {
            statusUpdate.startTime = nextStartTime;
        }
    } else if (typeof data.uptime === 'string') {
        // 仅在缺少本地跳秒基准时回退使用服务端格式化的运行时长
        statusUpdate.uptime = data.uptime;
    }

    return statusUpdate;
}

/**
 * 顶层全局状态供给器 (AppProvider)
 * @description 包裹在 React DOM 根部，承载全局状态机 (reducer state) 并提供底层依赖同步服务。
 * 
 * @param {Object} props - React 属性载荷。
 * @param {React.ReactNode} props.children - 嵌套的子级组件或视图路由。
 */
function AppProvider({ children }) {
    // 实例化 useReducer 状态机，绑定挂载于 window 对象下的全局 reducer 纯函数与初始状态
    const [state, dispatch] = useReducer(window.appReducer, window.initialAppState);
    const statusApi = window.DisasterStatusApi; // 从全局注入的后台 API 通信模块

    // 副作用钩子：同步当前主题模式（Light/Dark）至 HTML/Body 的 className 属性，激活对应的 CSS Theme 变量体系
    useThemeSync(state.theme);

    /**
     * 异步拉取最新运行状态
     * 采用 useCallback 包装，防止子组件重复渲染时引起 API 请求链路重复生成的冗余性能损耗。
     */
    const refreshData = React.useCallback(async () => {
        try {
            const data = await statusApi.getStatus();
            dispatch({
                type: window.AppActionTypes.UPDATE_STATUS,
                payload: toStatusUpdate(data, state.status.version, state.status.startTime),
            });
            return data;
        } catch (err) {
            console.error('Failed to fetch status:', err);
            throw err;
        }
    }, [statusApi, state.status.version, state.status.startTime]);

    /**
     * 异步拉取当前各数据源连接明细与网络延迟数据
     */
    const fetchConnections = React.useCallback(async () => {
        try {
            const data = await statusApi.getConnections();
            if (data.connections) {
                dispatch({ type: window.AppActionTypes.UPDATE_CONNECTIONS, payload: data.connections });
            }
            return data;
        } catch (err) {
            console.error('Failed to fetch connections:', err);
            throw err;
        }
    }, [statusApi]);

    /**
     * 异步拉取全局系统配置参数，如显示时区设置
     */
    const fetchConfig = React.useCallback(async () => {
        try {
            const data = await statusApi.getConfig();
            if (data.display_timezone) {
                dispatch({
                    type: window.AppActionTypes.UPDATE_CONFIG,
                    payload: { displayTimezone: data.display_timezone },
                });
            }
            return data;
        } catch (err) {
            console.error('Failed to fetch config:', err);
            throw err;
        }
    }, [statusApi]);

    /**
     * 异步拉取多维灾害历史统计指标（包括事件计数、地震区间分布等）
     */
    const fetchStatistics = React.useCallback(async () => {
        try {
            const data = await statusApi.getStatistics();
            dispatch({ type: window.AppActionTypes.UPDATE_STATS, payload: data || {} });
            return data;
        } catch (err) {
            console.error('Failed to fetch statistics:', err);
            throw err;
        }
    }, [statusApi]);

    // 初始化编排钩子 (Bootstrap Hook)：在首次挂载时，按时序并发初始化所有核心数据接口
    useAppBootstrap({
        refreshData,
        fetchConfig,
        fetchConnections,
        fetchStatistics,
    });

    // 运行时长本底跳秒钩子 (Uptime Counter Hook)：本地维护计时器，驱动面板展示的秒级 Uptime，降低轮询开销
    useStatusUptimeEffect({
        running: state.status.running,
        startTime: state.status.startTime,
        dispatch,
    });

    return (
        <AppContext.Provider value={{ state, dispatch, refreshData, fetchConnections, fetchConfig, fetchStatistics }}>
            {children}
        </AppContext.Provider>
    );
}

/**
 * 快捷读取 Context 的 React Hook 封装
 * @description 强制在子组件内提取上下文，若子组件未处于 Provider 包裹中，则抛出断言异常。
 * 
 * @returns {{
 *   state: Object,
 *   dispatch: Function,
 *   refreshData: Function,
 *   fetchConnections: Function,
 *   fetchConfig: Function,
 *   fetchStatistics: Function
 * }} 全局状态与 API 操作接口集合。
 */
function useAppContext() {
    const context = useContext(AppContext);
    if (!context) {
        throw new Error('useAppContext must be used within AppProvider');
    }
    return context;
}

// 绑定各公开成员至 window 顶层对象，解决在无模块化系统（如旧版单页面嵌入式 JS 渲染）下的全局命名空间交叉访问
window.AppProvider = AppProvider;
window.useAppContext = useAppContext;
window.toStatusUpdate = toStatusUpdate;
