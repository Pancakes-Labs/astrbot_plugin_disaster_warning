(() => {
/**
 * 模块名称：Markdown 文档阅读视图组件
 * 文件路径：admin/js/views/MarkdownDocsView.jsx
 * 功能描述：在管理端界面中提供一个内置的 Markdown 文件浏览器。
 *           支持读取插件根目录及子目录下的说明文档、更新日志等，
 *           配合 Mermaid 渲染器实现架构图与时序图的可视化展示。
 */

const { Box, Typography, Button, Chip, CircularProgress } = MaterialUI;

/**
 * 文档浏览视图主组件
 * 构建包含侧边栏文件树与右侧主渲染区的双栏自适应阅读器
 */
function MarkdownDocsView() {
    // 渲染文章 DOM 的引用，用于给 Mermaid 渲染 Hook 提供挂载的容器
    const articleRef = React.useRef(null);
    
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
                            在插件前端中直接阅读原生 Markdown 文件，例如 README、CHANGELOG 与 docs 目录文档。
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

            {/* 双栏主工作区 */}
            <div className="markdown-docs-shell">
                {/* 左侧侧边栏：文档目录树 */}
                <aside className="markdown-docs-aside-column">
                    <div className="markdown-docs-aside-sticky">
                        <div className="card markdown-docs-sidebar-card">
                            <div className="markdown-docs-sidebar-head">
                                <Typography variant="subtitle1" className="markdown-docs-sidebar-title">
                                    文档目录
                                </Typography>
                                <Typography variant="body2" color="text.secondary" className="markdown-docs-sidebar-subtitle">
                                    仅展示插件目录内允许浏览的 Markdown 文件。
                                </Typography>
                            </div>

                            {/* 条件渲染列表内容 */}
                            {loadingList ? (
                                <div className="tasks-empty-card markdown-docs-empty-side-card" style={{ padding: '24px 0', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                                    <CircularProgress size={24} style={{ marginBottom: '12px' }} />
                                    <Typography variant="body2" color="text.secondary">正在加载文档列表…</Typography>
                                </div>
                            ) : markdownFiles.length === 0 ? (
                                <div className="tasks-empty-card markdown-docs-empty-side-card">
                                    <div className="tasks-empty-icon">📚</div>
                                    <Typography variant="body1" className="markdown-docs-empty-title">
                                        暂无可浏览文档
                                    </Typography>
                                </div>
                            ) : (
                                <div className="markdown-docs-file-list">
                                    {markdownFiles.map((item) => {
                                        const isActive = item.path === currentDocumentPath;
                                        return (
                                            <button
                                                key={item.path}
                                                type="button"
                                                className={`markdown-docs-file-item ${isActive ? 'is-active' : ''}`}
                                                aria-current={isActive ? 'true' : undefined}
                                                onClick={() => {
                                                    // 如果是当前已选中的文档，则避免重复发起多余的异步请求
                                                    if (isActive) {
                                                        return;
                                                    }
                                                    loadMarkdownDocument(item.path);
                                                }}
                                            >
                                                <div className="markdown-docs-file-item-top">
                                                    <span className="markdown-docs-file-icon">📝</span>
                                                    <span className="markdown-docs-file-title">{item.title || item.filename || item.path}</span>
                                                </div>
                                                {/* 显示文件在服务器磁盘上的等宽相对路径 */}
                                                <div className="markdown-docs-file-path mono">{item.path}</div>
                                            </button>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    </div>
                </aside>

                {/* 右侧内容主展板 */}
                <div className="markdown-docs-content-column">
                    <div className="card markdown-docs-content-card">
                        {/* 分状态渲染不同的提示信息与文本视图 */}
                        {!currentDocumentPath ? (
                            // 状态 1：未选择任何文件时的缺省空白页提示
                            <div className="tasks-empty-card markdown-docs-empty-card">
                                <div className="tasks-empty-icon">📄</div>
                                <Typography variant="h6" className="markdown-docs-empty-title">
                                    请选择左侧文档
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
        </Box>
    );
}

// 绑定全局窗口属性，方便其他层通过原生或动态加载方式调用此视图
window.MarkdownDocsView = MarkdownDocsView;
})();
