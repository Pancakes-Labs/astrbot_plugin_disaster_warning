/**
 * 模块名称：前端控制台单页应用根入口
 * 文件路径：admin/js/app.jsx
 * 功能描述：作为整个灾害管理面板的 React 运行根节点。
 *           负责统筹明暗色盘主题转换、加载全局 CSS 基准样式、绑定全局唯一的 WebSocket 心跳连接、
 *           监听主视区滚动位置以便记忆还原、注册侧边栏菜单及渲染模态框等全局挂载组件。
 */

const { ThemeProvider, CssBaseline, Typography, Button } = MaterialUI;
const { useState, useMemo, Component } = React;

/**
 * 全局渲染错误边界：任何子视图渲染异常时降级显示错误卡片而非整页白屏。
 * 项目此前没有任何 ErrorBoundary，子组件（如 JSON 编辑器）一旦抛错，
 * React 会卸载整棵组件树导致管理端白屏，必须重载网页。
 */
class AppErrorBoundary extends Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, message: '' };
    }
    static getDerivedStateFromError(error) {
        return { hasError: true, message: String(error && error.message ? error.message : error) };
    }
    componentDidCatch(error) {
        console.error('视图渲染异常（已被错误边界拦截）', error);
    }
    handleReload = () => {
        window.location.reload();
    };
    render() {
        if (this.state.hasError) {
            return (
                <div className="app-error-boundary">
                    <Typography variant="h6" className="app-error-boundary-title">
                        ⚠️ 视图渲染出错
                    </Typography>
                    <Typography variant="body2" color="text.secondary" className="app-error-boundary-desc">
                        界面组件遇到异常，已阻止白屏。可尝试切换视图或刷新页面恢复。
                    </Typography>
                    <Typography variant="caption" className="app-error-boundary-msg">
                        {this.state.message}
                    </Typography>
                    <Button variant="contained" color="primary" size="small" onClick={this.handleReload}>
                        🔄 刷新页面
                    </Button>
                </div>
            );
        }
        return this.props.children;
    }
}

/**
 * 应用程序主应用壳组件
 * 处理路由导航、弹窗仿真状态及骨架屏过场后的全局渲染
 */
function App() {
    // 获取全局上下文中的当前状态树，以及提供给子视图的重载数据触发器
    const { state, refreshData } = useAppContext();
    
    // 将响应式状态直接暴露到全局视窗对象，方便浏览器控制台调试查看实时心跳和数据源状态
    window.__DISASTER_APP_STATE__ = state;
    
    // 初始化本地存储记忆的视图键值，默认展示 status 即运行健康面板
    const { currentView, setCurrentView } = usePersistedViewState('currentView', 'status');

    // 绑定防抖滚动位置记录钩子，在切换事件与统计图表数据时，保留物理滚动条位置，防止用户阅读中断
    const mainContentRef = useMainScrollMemory({
        currentView,
        restoreTriggers: [state.events, state.stats],
    });

    // 激活持久化 WebSocket 双向长连接，监听并向 Reducer 纯函数状态机广播突发事件或连接心跳
    useWebSocket();

    // 监听网络握手与配置载入完毕事件，平滑淡出初始加载骨架遮罩屏（Bootloader）
    useBootloaderDismiss();

    // 监听全局主题状态 themeMode 变化，动态演算出对应的明暗配色 MaterialUI 调色板
    const theme = useMemo(() => window.createAppTheme(state.theme), [state.theme]);

    /**
     * 根据当前选中的路由视图键值，从注册表中获取目标组件定义并注入通用回调方法
     */
    const renderView = () => {
        const viewDefinition = window.ViewRegistry.getViewDefinition(currentView);
        return viewDefinition.component({
            onRefresh: refreshData, // 手动触发全局数据同步
        });
    };

    return (
        // 主题供给容器，下发经过演算的配置包
        <ThemeProvider theme={theme}>
            {/* 重置并注入明暗模式自适应的基础 CSS 样式 */}
            <CssBaseline />
            <div className="app">
                {/* 侧边导航栏：包括路由跳转及角标计算 */}
                <Sidebar currentView={currentView} onViewChange={setCurrentView} />

                {/* 右侧核心主内容包装层 */}
                <div className="main-wrapper">
                    {/* 公共头部栏，展示当前视图标题及实时数字时钟 */}
                    <Header currentView={currentView} />

                    {/* 主滚动物理区域，绑定 Ref 锚点以便多频定位机制执行滚动恢复 */}
                    <div className="main-content" ref={mainContentRef}>
                        {renderView()}
                    </div>
                </div>
            </div>
        </ThemeProvider>
    );
}

/**
 * 身份认证防御层包装组件
 * 阻止在令牌认证失败或者网络不可达时的渲染溢出，等待系统引导就绪后再行载入
 */
function AuthWrapper() {
    // 获取底层握手检查的准备状态
    const ready = useAuthReadyState();

    // 在认证接口响应前，维持空白或骨架动画状态
    if (!ready) return null;

    return (
        // 全局错误边界兜底：子组件渲染异常时不白屏，降级显示错误卡片
        <AppErrorBoundary>
            {/* 嵌套注入全局上下文总线及系统 Toast 消息吐司服务 */}
            <AppProvider>
                <ToastProvider>
                    <App />
                </ToastProvider>
            </AppProvider>
        </AppErrorBoundary>
    );
}

// 防重入渲染门禁锁：由于动态加载 Babel 或二次认证跳转可能导致本入口脚本重复加载，加入全局标记防御
if (!window.__DISASTER_WEBUI_INITIALIZED) {
    window.__DISASTER_WEBUI_INITIALIZED = true;
    const rootElement = document.getElementById('root');
    const root = ReactDOM.createRoot(rootElement);
    // 挂载至物理 DOM 树根节点
    root.render(<AuthWrapper />);
}
