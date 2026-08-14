const { Box, Typography } = MaterialUI;

/**
 * 侧边栏导航组件 (Sidebar)
 * 该组件承载了应用的主导航控制，主要负责展示品牌标识、动态菜单导航列表，
 * 插件快捷操作入口（例如打开插件本地配置文件目录），以及插件仓库/开发者信息的 GitHub 卡片展示。
 * 
 * @param {Object} props 组件属性
 * @param {string} props.currentView 当前所处视图的唯一标识符
 * @param {Function} props.onViewChange 视图切换触发的回调函数，接收目标视图 ID 作为参数
 */
function Sidebar({ currentView, onViewChange }) {
    // 获取全局应用状态 context
    const { state } = useAppContext();
    // 全局状态 API，主要用于调用宿主进程的本地 OS 接口
    const statusApi = window.DisasterStatusApi;
    // 从系统状态中抽取当前插件版本号
    const { version } = state.status;
    // 实例化轻量提示 Hook
    const { showToast } = useToast();

    // 挂载全局调试状态，允许控制台直接获取和追踪 React 状态树
    window.__DISASTER_APP_STATE__ = state;
    
    // 从全局视图注册表中获取目前可供导航的项列表
    const menuItems = window.ViewRegistry.getNavigationItems();

    return (
        <div className="sidebar">
            {/* 品牌标识区块 (Header) */}
            <div className="sidebar-header">
                <img src="/logo.png" alt="Logo" className="sidebar-logo-img" />
                <div>
                    <Typography variant="h6" className="sidebar-brand-title">
                        灾害预警
                    </Typography>
                    <Typography variant="caption" className="sidebar-brand-subtitle">
                        Admin Console
                    </Typography>
                </div>
            </div>

            {/* 动态导航菜单列表 (Nav Items)：使用 nav + button 语义化，支持键盘 Tab 聚焦与读屏朗读 */}
            <nav className="sidebar-nav" aria-label="主导航">
                {menuItems.map((item) => (
                    <button
                        key={item.id}
                        type="button"
                        className={`nav-item ${currentView === item.id ? 'active' : ''}`}
                        onClick={() => onViewChange(item.id)}
                        aria-current={currentView === item.id ? 'page' : undefined}
                        aria-label={item.badge > 0 ? `${item.label}，${item.badge} 条未读` : item.label}
                    >
                        <span className="nav-item__icon" aria-hidden="true">{item.icon}</span>
                        <span className="nav-item__label">{item.label}</span>
                        {/* 如果存在大于0的徽标角标，则显示未读数（最大99+）。
                            aria-label 已合并未读数，徽标本身仅作视觉指示，读屏不重复播报 */}
                        {item.badge > 0 && (
                            <span className="nav-badge" aria-hidden="true">
                                {item.badge > 99 ? '99+' : item.badge}
                            </span>
                        )}
                    </button>
                ))}
            </nav>

            {/* 底部功能按钮与贡献者栏 (Footer) */}
            <Box className="sidebar-footer">
                {/* 快捷操作：调用 API 打开插件在宿主系统中的本地数据存储目录 */}
                <button
                    className="btn sidebar-plugin-dir-button"
                    onClick={() => {
                        statusApi.openPluginDir()
                            .catch(err => showToast(`请求失败: ${err?.message || err}`, 'error'));
                    }}
                >
                    <span className="sidebar-plugin-dir-button__icon">📂</span>
                    打开插件文件目录
                </button>
                
                {/* 开源仓库 GitHub 卡片 */}
                <a
                    href="https://github.com/DBJD-CR/astrbot_plugin_disaster_warning"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="github-btn"
                    data-theme={state.theme}
                >
                    <div className="github-card">
                        {/* GitHub 官方标志矢量 SVG 图形 */}
                        <svg className="github-card__icon" height="28" width="28" viewBox="0 0 16 16">
                            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path>
                        </svg>
                        
                        <div className="github-card__meta">
                            {/* 第一行: 作者与仓库信息 */}
                            <Typography variant="caption" className="github-card__authors">
                                @DBJD-CR & Aloys233
                            </Typography>
                            
                            {/* 第二行: 项目名称及当前加载的插件版本号 */}
                            <Typography variant="caption" className="github-card__version">
                                🔧 (灾害预警) {version || '获取中...'}
                            </Typography>
                        </div>
                    </div>
                </a>
                
                {/* Star 标语 */}
                <Typography variant="caption" className="sidebar-star-hint">
                    点个 Star 吧~ ⭐
                </Typography>
            </Box>
        </div>
    );
}
