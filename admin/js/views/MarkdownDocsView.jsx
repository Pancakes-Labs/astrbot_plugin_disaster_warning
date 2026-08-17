(() => {
/**
 * 模块名称：Markdown 文档阅读视图组件
 * 文件路径：admin/js/views/MarkdownDocsView.jsx
 * 功能描述：在管理端界面中提供一个内置的 Markdown 文件浏览器。
 *           支持读取插件根目录及子目录下的说明文档、更新日志等，
 *           配合 Mermaid 渲染器实现架构图与时序图的可视化展示。
 * 布局说明：文档目录固定于页面顶部并横向展示，支持展开/收起；
 *           向下滚动时目录条保持吸附在顶部导航栏之下；
 *           下方文档阅读区独占全宽。
 * 结构说明：主工作区使用 .markdown-docs-layout 包裹「目录条 + 阅读区」，
 *           目录条采用 sticky 定位并配以不透明毛玻璃背景，
 *           滚动时阅读区内容从目录条下方干净通过，互不干扰。
 */

const { Box, Typography, Button, Chip, CircularProgress } = MaterialUI;

/**
 * 文档浏览视图主组件
 * 采用「顶部固定横向目录条 + 下方全宽阅读区」的单列自适应阅读器
 */
function MarkdownDocsView() {
    // 渲染文章 DOM 的引用，用于给 Mermaid 渲染 Hook 提供挂载的容器
    const articleRef = React.useRef(null);
    // 顶部目录横向列表的引用，用于绑定鼠标滚轮横滚
    const tocListRef = React.useRef(null);
    // 顶部目录条的展开/收起状态（默认展开）
    const [tocCollapsed, setTocCollapsed] = React.useState(false);

    // 获取 Markdown 相关的底层状态和异步操作函数
    const docs = useMarkdownDocs();
    const {
        theme,                  // 当前系统的主题模式（明/暗）
        markdownFiles,         // 可供读取的 Markdown 文件列表数组
        markdownDocument,      // 当前选中的文档数据包对象
        loadingList,           // 文件目录列表加载状态标识
        loadingDocument,       // 当前选中的文档内容加载状态标识
        currentDocumentTitle,  // 当前渲染文档的显示标题
        currentDocumentPath,   // 当前渲染文档的相对路径
        markdownUtil,          // 全局 Markdown 编译工具类实例
        renderedHtml,          // 编译后的安全 HTML 内容字符串
        loadMarkdownFiles,     // 重新从服务端拉取文件列表的方法
        loadMarkdownDocument,  // 异步加载指定路径文档内容的方法
        refreshCurrentDocument, // 手动重新载入当前选中文件的方法
    } = docs;

    // 绑定 Mermaid 流程图渲染钩子，在 HTML 渲染完成后解析图表代码块并绘制 SVG 矢量图
    useMermaidRenderer(articleRef, {
        documentPath: currentDocumentPath,
        renderedHtml,
        theme,
    });

    // 切换顶部目录条的展开/收起状态
    const toggleToc = () => setTocCollapsed((prev) => !prev);

    // 目录横向列表的鼠标滚轮横滚支持：
    // 滚轮垂直滚动时转为横向滚动，触控板/滚轮步进不一致时归一化 delta 平滑连续滚动。
    // 监听器绑定在始终渲染的 .markdown-docs-toc-body 上（列表为条件渲染，
    // 直接挂列表会在加载完成前丢失绑定），事件内再查找实际列表元素滚动。
    React.useEffect(() => {
        const host = tocListRef.current;
        if (!host) return;
        const handleWheel = (e) => {
            // 仅当垂直滚动占主导且确实存在横向列表时接管
            if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
            const list = host.querySelector('.markdown-docs-toc-list');
            if (!list) return; // 加载中 / 空状态 / 收起状态：放行默认滚动
            e.preventDefault();
            // 归一化 delta：触控板/鼠标滚轮步进不一致时仍可平滑连续滚动
            const step = Math.abs(e.deltaY) < 1 ? e.deltaY * 16 : e.deltaY;
            list.scrollLeft += step;
        };
        host.addEventListener('wheel', handleWheel, { passive: false });
        return () => host.removeEventListener('wheel', handleWheel);
    }, [tocCollapsed]);

    return (
        // 外部容器，复用了通知中心的部分样式并加入文档特定主题类
        <Box className="notifications-view markdown-docs-view">
            {/* 顶栏控制卡片，展示文档总数、当前阅读路径及功能操作按钮 */}
            <div className="card notifications-hero-card markdown-docs-hero-card">
                <Box className="tasks-header-row notifications-header-row">
                    {/* 左侧文字介绍与状态徽章 */}
                    <div className="notifications-header-main">
                        <Box className="notifications-title-row">
                            <Box className="notifications-title-stack">
                                {/* 主标题，动态插值文档列表长度 */}
                                <Typography variant="h6" className="notifications-title-text">
                                    {`文档浏览 (当前共 ${markdownFiles.length} 份文档)`}
                                </Typography>
                                {/* 状态徽标，提示当前正在查看的文档名称或选中状态 */}
                                <Chip
                                    label={currentDocumentPath ? `当前：${currentDocumentTitle}` : '请选择文档'}
                                    size="small"
                                    color="primary"
                                    variant="outlined"
                                />
                            </Box>
                        </Box>
                        <Typography variant="body2" className="tasks-header-subtitle notifications-hero-subtitle">
                            在插件前端中直接阅读原生 Markdown 文件，例如 README、CHANGELOG 与 docs 目录文档
                        </Typography>
                    </div>
                    {/* 右侧动作控制区，可刷新目录或重载当前文本内容 */}
                    <Box className="notifications-actions-row">
                        <Button
                            variant="outlined"
                            onClick={loadMarkdownFiles}
                            disabled={loadingList}
                            className="notifications-action-btn"
                        >
                            刷新目录
                        </Button>
                        <Button
                            variant="contained"
                            onClick={refreshCurrentDocument}
                            disabled={loadingList || loadingDocument || !currentDocumentPath}
                            startIcon={<span>📘</span>}
                            className="notifications-action-btn notifications-action-btn--primary"
                        >
                            刷新文档
                        </Button>
                    </Box>
                </Box>
            </div>

            {/* 主工作区：顶部固定横向目录条 + 下方全宽阅读区 */}
            <div className="markdown-docs-layout">
                {/* 顶部固定横向目录条：随页面滚动吸附在顶栏下方，支持展开/收起 */}
                <div className={`markdown-docs-toc ${tocCollapsed ? 'is-collapsed' : ''}`}>
                    <div className="markdown-docs-toc-inner">
                        {/* 目录头部：标题 + 展开/收起切换按钮 */}
                        <div className="markdown-docs-toc-head">
                            <div className="markdown-docs-toc-head-text">
                                <Typography variant="subtitle1" className="markdown-docs-sidebar-title">
                                    文档目录
                                </Typography>
                                <Typography variant="body2" color="text.secondary" className="markdown-docs-toc-subtitle">
                                    仅展示插件目录内允许浏览的 Markdown 文件
                                </Typography>
                            </div>
                            <button
                                type="button"
                                className="markdown-docs-toc-toggle"
                                onClick={toggleToc}
                                aria-expanded={!tocCollapsed}
                                title={tocCollapsed ? '展开文档目录' : '收起文档目录'}
                            >
                                <span className="markdown-docs-toc-toggle-icon">{tocCollapsed ? '▸' : '▾'}</span>
                                <span className="markdown-docs-toc-toggle-text">{tocCollapsed ? '展开目录' : '收起目录'}</span>
                            </button>
                        </div>

                        {/* 文档列表区域：始终渲染以支持收起/展开的高度过渡动画 */}
                        <div ref={tocListRef} className="markdown-docs-toc-body">
                            {loadingList ? (
                                <div className="markdown-docs-toc-loading">
                                    <CircularProgress size={22} />
                                    <Typography variant="body2" color="text.secondary">正在加载文档列表…</Typography>
                                </div>
                            ) : markdownFiles.length === 0 ? (
                                <div className="markdown-docs-toc-empty">
                                    <span className="markdown-docs-toc-empty-icon">📚</span>
                                    <Typography variant="body2" color="text.secondary">暂无可浏览文档</Typography>
                                </div>
                            ) : (
                                <div className="markdown-docs-toc-list">
                                    {markdownFiles.map((item) => {
                                        const isActive = item.path === currentDocumentPath;
                                        return (
                                            <button
                                                key={item.path}
                                                type="button"
                                                className={`markdown-docs-toc-item ${isActive ? 'is-active' : ''}`}
                                                aria-current={isActive ? 'true' : undefined}
                                                onClick={() => {
                                                    // 如果是当前已选中的文档，则避免重复发起多余的异步请求
                                                    if (isActive) {
                                                        return;
                                                    }
                                                    loadMarkdownDocument(item.path);
                                                }}
                                            >
                                                <span className="markdown-docs-toc-item-icon">📝</span>
                                                <span className="markdown-docs-toc-item-body">
                                                    <span className="markdown-docs-toc-item-title">{item.title || item.filename || item.path}</span>
                                                    {/* 显示文件在服务器磁盘上的等宽相对路径 */}
                                                    <span className="markdown-docs-toc-item-path mono">{item.path}</span>
                                                </span>
                                            </button>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                {/* 下方全宽文档阅读区 */}
                <div className="markdown-docs-content-column markdown-docs-content-column--full">
                    <div className="card markdown-docs-content-card">
                        {/* 卡片内部独立滚动容器：卡片自身不滚动（视觉面 ::before 背景完整覆盖），
                            内容在此容器内滚动，避免背景随滚动破裂 */}
                        <div className="markdown-docs-content-scroll">
                        {/* 分状态渲染不同的提示信息与文本视图 */}
                        {!currentDocumentPath ? (
                            // 状态 1：未选择任何文件时的缺省空白页提示
                            <div className="tasks-empty-card markdown-docs-empty-card">
                                <div className="tasks-empty-icon">📄</div>
                                <Typography variant="h6" className="markdown-docs-empty-title">
                                    请选择上方文档
                                </Typography>
                                <Typography variant="body1" color="text.secondary" className="markdown-docs-empty-subtitle">
                                    选择后即可在当前管理端中直接阅读 Markdown 文档内容。
                                </Typography>
                            </div>
                        ) : loadingDocument && !markdownDocument ? (
                            // 状态 2：正在发起网络请求时的骨架加载屏
                            <div className="tasks-empty-card markdown-docs-empty-card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '200px' }}>
                                <CircularProgress size={32} style={{ marginBottom: '16px' }} />
                                <Typography variant="body2" color="text.secondary">
                                    正在加载文档内容…
                                </Typography>
                            </div>
                        ) : markdownDocument ? (
                            // 状态 3：获取数据成功，开始进行语法高亮或纯文本渲染
                            <>
                                <div className="markdown-docs-article-head">
                                    <div>
                                        {/* 文档的主标题级字号展示 */}
                                        <Typography variant="h5" className="markdown-docs-article-title">
                                            {currentDocumentTitle}
                                        </Typography>
                                        {/* 打印文档所在的内部物理相对位置 */}
                                        <Typography variant="body2" className="task-card-session-sub mono markdown-docs-article-path">
                                            {currentDocumentPath}
                                        </Typography>
                                    </div>
                                </div>
                                {/* 如果解析渲染编译器可用，则作为安全 HTML 嵌入显示，否则优雅降级为纯文本输出 */}
                                {markdownUtil ? (
                                    <Box
                                        ref={articleRef}
                                        className="notification-md markdown-docs-article markdown-docs-article-html"
                                        dangerouslySetInnerHTML={{ __html: renderedHtml }}
                                    />
                                ) : (
                                    <Typography
                                        ref={articleRef}
                                        variant="body2"
                                        className="notification-md markdown-docs-article markdown-docs-article--plain"
                                    >
                                        {String(markdownDocument.content || '')}
                                    </Typography>
                                )}
                            </>
                        ) : (
                            // 状态 4：接口响应失败或者解析出现异常时的容错面板
                            <div className="tasks-empty-card markdown-docs-empty-card">
                                <div className="tasks-empty-icon">⚠️</div>
                                <Typography variant="h6" className="markdown-docs-empty-title">
                                    文档暂时不可用
                                </Typography>
                                <Typography variant="body1" color="text.secondary" className="markdown-docs-empty-subtitle">
                                    当前文档未能成功加载，请稍后重试。
                                </Typography>
                            </div>
                        )}
                        </div>
                    </div>
                </div>
            </div>
        </Box>
    );
}

// 绑定全局窗口属性，方便其他层通过原生或动态加载方式调用此视图
window.MarkdownDocsView = MarkdownDocsView;
})();
