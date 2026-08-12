(() => {
    /**
     * 获取未读通知的角标数字
     * 
     * 业务逻辑说明：
     * - 优先读取挂载在全局 window 对象上的灾害应用状态树。
     * - 提取未读计数并转换为安全数值，用于侧边栏导航条等处的红色 Badge 数字标徽高亮渲染。
     */
    function getUnreadNotificationsBadge() {
        const state = window.__DISASTER_APP_STATE__;
        return Number(state?.notificationsMeta?.unread_count || 0);
    }

    /**
     * 视图注册表定义
     * 声明了管理后台支持的所有一级导航页面及其配置。
     * 
     * 路由属性说明：
     * - id: 视图唯一标识，用以在侧边栏、持久化存储及头部标题处进行路由识别。
     * - label: 侧边栏按钮展示的菜单名称。
     * - icon: 侧边栏和标签页头部渲染的 Emoji 图标。
     * - title: 面板中央或顶栏展示的页面大标题。
     * - badge: 选配的数字角标回调函数。
     * - component: 渲染页面视窗的 React 组件生成器，支持传入事件回调和刷新句柄。
     */
    const VIEW_REGISTRY = [
        {
            id: 'status',
            label: '运行状态',
            icon: '📊',
            title: '运行状态',
            component: () => <StatusView />,
        },
        {
            id: 'events',
            label: '事件列表',
            icon: '📋',
            title: '事件列表',
            component: () => <EventsView />,
        },
        {
            id: 'stats',
            label: '数据统计',
            icon: '📈',
            title: '数据统计',
            component: () => <StatsView />,
        },
        {
            id: 'notifications',
            label: '通知中心',
            icon: '🔔',
            title: '通知中心',
            badge: getUnreadNotificationsBadge,
            component: (props = {}) => <NotificationsView onRefresh={props.onRefresh} />,
        },
        {
            id: 'docs',
            label: '文档浏览',
            icon: '📚',
            title: '文档浏览',
            component: () => <MarkdownDocsView />,
        },
        {
            id: 'config',
            label: '配置管理',
            icon: '⚙️',
            title: '配置管理',
            component: () => <ConfigView />,
        },
        {
            id: 'simulation',
            label: '模拟预警',
            icon: '🧪',
            title: '模拟预警',
            component: () => <SimulationView />,
        },
    ];

    /**
     * 依据视图标识获取对应的视图描述对象
     * 
     * 防错机制说明：
     * - 若传入不存在的视图，默认回退至第一个（即运行状态 status 视图），防止发生空指针崩溃。
     */
    function getViewDefinition(viewId) {
        return VIEW_REGISTRY.find((item) => item.id === viewId) || VIEW_REGISTRY[0];
    }

    /**
     * 提取并组装适用于侧边栏导航条渲染的数据菜单集
     * 
     * 动态求值说明：
     * - badge 字段若为计算函数则自动执行并回填具体数字，否则透传空置，保障渲染性能。
     */
    function getNavigationItems() {
        return VIEW_REGISTRY.map((item) => ({
            id: item.id,
            label: item.label,
            icon: item.icon,
            badge: typeof item.badge === 'function' ? item.badge() : item.badge,
        }));
    }

    // 绑定至全局 ViewRegistry 命名空间以供 Sidebar 和 App 根组件调用
    window.ViewRegistry = {
        items: VIEW_REGISTRY,
        getViewDefinition,
        getNavigationItems,
    };
})();
