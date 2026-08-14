const { Box, Typography, IconButton } = MaterialUI;
const { useState, useEffect } = React;

/**
 * 实时时钟子组件 (RealTimeClock)
 * 用于在页头显示当前系统或配置的预警时区实时时间，支持每秒自动更新。
 * 
 * @param {Object} props 组件属性
 * @param {string} props.timeZone 目标时区标识符，例如 'UTC+8'
 */
function RealTimeClock({ timeZone }) {
    // 存储格式化后的时间字符串，格式为 "YYYY-MM-DD HH:mm:ss"
    const [timeStr, setTimeStr] = useState('');

    useEffect(() => {
        /**
         * 更新当前显示的时间
         */
        const updateTime = () => {
            const now = new Date();
            // 调用全局工具函数 formatTimeWithZone 进行时区转换与格式化
            const formatted = formatTimeWithZone(now.toISOString(), timeZone || 'UTC+8', true);
            // 补全秒数 (由于 formatTimeWithZone 默认只精确到分钟，此处手动追加秒数)
            const seconds = String(now.getSeconds()).padStart(2, '0');
            setTimeStr(`${formatted}:${seconds}`);
        };

        // 初始化执行一次，防止首帧空白
        updateTime();
        
        // 设立每秒触发的定时器
        const timer = setInterval(updateTime, 1000);
        
        // 组件卸载时清除定时器，避免内存泄漏
        return () => clearInterval(timer);
    }, [timeZone]);

    // 若时间字符串尚未完成首次计算，则返回 null 以避免渲染时的布局抖动
    if (!timeStr) return null;

    return (
        // 时钟每秒自动刷新且为纯装饰信息，对读屏用户无意义，整体标记为 aria-hidden 避免朗读噪音
        <div className="real-time-clock" aria-hidden="true">
            <span className="real-time-clock__label">当前时间 🕒</span>
            <span className="real-time-clock__value">
                {timeStr}
            </span>
        </div>
    );
}

/**
 * 页头组件 (Header)
 * 作为应用的管理控制台顶部栏，提供以下核心功能：
 * 1. 异步数据加载状态下的顶部进度条渲染。
 * 2. 当前激活视图的动态标题展示。
 * 3. 对应时区的实时时钟显示。
 * 4. WebSocket (WS) 实时长连接状态可视化芯片。
 * 5. 应用暗黑/亮色主题切换控制器。
 *
 * @param {Object} props 组件属性
 * @param {string} props.currentView 当前激活的视图 ID ('status' | 'events' | 'stats' | 'config' 等)
 */
function Header({ currentView }) {
    // 从全局应用上下文中获取状态 state 与调度器 dispatch
    const { state, dispatch } = useAppContext();
    const { config, dataLoaded } = state;
    
    // 获取配置中的显示时区，若未配置则默认采用 UTC+8
    const displayTimezone = config.displayTimezone || 'UTC+8';

    /**
     * 触发全局主题切换逻辑
     */
    const toggleTheme = () => {
        dispatch({ type: 'TOGGLE_THEME' });
    };

    // 从视图注册表中获取当前视图的元数据定义 (包括 title, icon 等)
    const currentViewDefinition = window.ViewRegistry.getViewDefinition(currentView);

    return (
        <>
            {/* 全局异步数据未加载完毕时，渲染顶部线性加载指示器 */}
            {!dataLoaded && (
                <div className="app-loading-progress">
                    <div className="app-loading-progress__bar"></div>
                </div>
            )}
            <div className="top-bar">
                {/* 动态视图标题 */}
                <Typography variant="h5" className="header-title">
                    {currentViewDefinition.title}
                </Typography>
                
                {/* 右侧动作控制区 */}
                <Box className="header-actions">
                    {/* 实时时钟组件 */}
                    <RealTimeClock timeZone={displayTimezone} />

                    {/* WebSocket 连接状态指示芯片 */}
                    <div className={`ws-status-chip ${state.wsConnected ? 'is-connected' : 'is-disconnected'}`} role="status" aria-live="polite">
                        <div className="ws-status-chip__dot" aria-hidden="true"></div>
                        <Typography variant="body2" className="ws-status-chip__label">
                            {state.wsConnected ? '已连接' : '未连接'}
                        </Typography>
                    </div>
                    
                    {/* 主题切换图标按钮 */}
                    <IconButton
                        onClick={toggleTheme}
                        className="theme-toggle-button"
                        aria-label={state.theme === 'dark' ? '切换到亮色模式' : '切换到暗色模式'}
                        aria-pressed={state.theme === 'dark'}
                    >
                        <span className="theme-toggle-button__icon" aria-hidden="true">
                            {state.theme === 'dark' ? '🌞' : '🌙'}
                        </span>
                    </IconButton>
                </Box>
            </div>
        </>
    );
}
