/**
 * 文档 (Markdown) 文件的加载与管理钩子。
 * 
 * 核心机制说明：
 * 1. 全局状态结合：将加载出的 Markdown 文件目录树和当前选中的文档详情保存至全局状态上下文中，
 *    确保管理员切换菜单或进行其他操作时，阅读进度和选中的文档高亮状态不会丢失。
 * 2. 自动首选加载：若首次进入文档页面且无任何历史选中状态，自动拉取列表第一篇文档作为默认阅读载荷。
 * 3. 异步 HTML 编译：利用 Markdown 渲染工具对获取的文本进行语法标记解析，并在内存中完成向 HTML 的安全转换。
 */
function useMarkdownDocs() {
    const { state, dispatch } = useAppContext();
    const notificationApi = window.DisasterNotificationApi;
    const markdownFiles = Array.isArray(state.markdownFiles) ? state.markdownFiles : [];
    const markdownDocument = state.markdownDocument || null;
    const selectedMarkdownPath = String(state.selectedMarkdownPath || '');
    const [loadingList, setLoadingList] = React.useState(false);
    const [loadingDocument, setLoadingDocument] = React.useState(false);
    // Markdown 增强依赖（marked / DOMPurify）是否就绪。
    // renderedHtml 在渲染期同步读取 window.marked，若脚本就绪晚于文档内容到达，
    // 组件不会自动重渲染而停留在降级渲染；用该 state 触发一次就绪后的重渲染。
    const [libsReady, setLibsReady] = React.useState(
        Boolean(window.MarkdownRenderUtil
            && window.marked
            && window.DOMPurify)
    );

    /**
     * 异步拉取可读的 Markdown 说明书相对路径列表
     */
    // 进入文档浏览页时，按需动态加载 marked / DOMPurify 增强依赖（本地化脚本，异步注入）。
    // 加载失败时 renderMarkdownToHtml 自动降级到内置渲染器，不阻塞页面。
    React.useEffect(() => {
        const markdownUtil = window.MarkdownRenderUtil;
        if (markdownUtil && typeof markdownUtil.ensureMarkdownLibs === 'function') {
            markdownUtil.ensureMarkdownLibs().then((ready) => {
                setLibsReady(Boolean(ready));
            }).catch(() => {});
        }
    }, []);

    const loadMarkdownFiles = React.useCallback(async () => {
        setLoadingList(true);
        try {
            const payload = await notificationApi.listMarkdownFiles();
            const items = Array.isArray(payload?.items) ? payload.items : [];
            dispatch({ type: window.AppActionTypes.SET_MARKDOWN_FILES, payload: items });

            // 若无历史选中项，则默认点亮列表第一项
            if (!selectedMarkdownPath && items.length > 0) {
                dispatch({ type: window.AppActionTypes.SET_SELECTED_MARKDOWN_PATH, payload: items[0].path || '' });
            }
            return items;
        } catch (e) {
            console.error('加载 Markdown 文档列表失败:', e);
            return [];
        } finally {
            setLoadingList(false);
        }
    }, [notificationApi, dispatch, selectedMarkdownPath]);

    /**
     * 根据相对路径拉取对应的 Markdown 详细内容体
     */
    const loadMarkdownDocument = React.useCallback(async (path) => {
        const normalizedPath = String(path || '').trim();
        if (!normalizedPath) {
            dispatch({ type: window.AppActionTypes.SET_MARKDOWN_DOCUMENT, payload: null });
            return null;
        }

        setLoadingDocument(true);
        try {
            const payload = await notificationApi.getMarkdownFile(normalizedPath);
            dispatch({ type: window.AppActionTypes.SET_MARKDOWN_DOCUMENT, payload: payload || null });
            dispatch({ type: window.AppActionTypes.SET_SELECTED_MARKDOWN_PATH, payload: normalizedPath });
            return payload || null;
        } catch (e) {
            console.error('加载 Markdown 文档失败:', e);
            return null;
        } finally {
            setLoadingDocument(false);
        }
    }, [notificationApi, dispatch]);

    // 监听文件列表空状态，自动完成首次目录树同步
    React.useEffect(() => {
        if (markdownFiles.length === 0) {
            loadMarkdownFiles();
        }
    }, [loadMarkdownFiles, markdownFiles.length]);

    // 监听选中路径变动，自动同步加载对应的文档载荷
    React.useEffect(() => {
        if (!selectedMarkdownPath && markdownFiles.length > 0) {
            loadMarkdownDocument(markdownFiles[0].path || '');
            return;
        }

        if (
            selectedMarkdownPath
            && (!markdownDocument || String(markdownDocument.path || '') !== selectedMarkdownPath)
        ) {
            loadMarkdownDocument(selectedMarkdownPath);
        }
    }, [loadMarkdownDocument, markdownDocument, markdownFiles, selectedMarkdownPath]);

    /**
     * 手动触发的强制重载
     */
    const refreshCurrentDocument = React.useCallback(async () => {
        await loadMarkdownFiles();
        if (selectedMarkdownPath) {
            await loadMarkdownDocument(selectedMarkdownPath);
        }
    }, [loadMarkdownDocument, loadMarkdownFiles, selectedMarkdownPath]);

    const currentDocumentTitle = markdownDocument?.title
        || markdownFiles.find((item) => item.path === selectedMarkdownPath)?.title
        || 'Markdown 文档';
    const currentDocumentPath = markdownDocument?.path || selectedMarkdownPath;
    const markdownUtil = window.MarkdownRenderUtil;
    
    // 调用全局 Markdown 解析工具，将纯文本编译为安全的 HTML 结构，用于前台渲染
    const renderedHtml = markdownDocument?.content && markdownUtil
        ? markdownUtil.renderMarkdownToHtml(markdownDocument.content)
        : '';

    return {
        theme: state.theme,
        markdownFiles,
        markdownDocument,
        selectedMarkdownPath,
        loadingList,
        loadingDocument,
        currentDocumentTitle,
        currentDocumentPath,
        markdownUtil,
        renderedHtml,
        loadMarkdownFiles,
        loadMarkdownDocument,
        refreshCurrentDocument,
    };
}

window.useMarkdownDocs = useMarkdownDocs;
