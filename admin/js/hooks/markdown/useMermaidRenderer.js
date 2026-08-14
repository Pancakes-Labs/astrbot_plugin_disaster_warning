/**
 * 针对 Markdown 文档内 Mermaid 图表语法的副作用渲染与交互操作钩子。
 * 
 * 核心技术细节与操作逻辑：
 * 1. 块过滤与提取：扫描文档 DOM 中包含特定 CSS 类名的 Mermaid 容器，排除空数据块。
 * 2. 状态机制保护：若图表库未初始化，则进行全局单例初始化，并根据当前面板主题自动配置明亮或暗黑配色主题。
 * 3. 异步排队解析：由于单个复杂图表解析高耗 CPU 算力，通过循环串行完成各块的解析和渲染。
 * 4. 视口交互注入：当图表绘制成功输出 SVG 代码后，自动注入平移拖拽、
 *    鼠标滚轮无极缩放、双击还原等高阶视口交互算法，并收集其注销闭包。
 * 5. 资源清理：在文档销毁、主题切换或用户重新加载时，自动打断渲染循环，并遍历执行闭包垃圾回收。
 */
function useMermaidRenderer(articleRef, { documentPath, renderedHtml, theme }) {
    // 按需加载本地化后的 mermaid.min.js（约 2.5MB），仅在文档页真正出现 Mermaid 图表时才注入，
    // 避免其体积拖慢管理端冷启动与视图切换；加载失败时下方渲染逻辑自动降级展示纯文本。
    const [mermaidReady, setMermaidReady] = React.useState(Boolean(window.mermaid));

    React.useEffect(() => {
        if (window.mermaid) {
            setMermaidReady(true);
            return;
        }
        if (window.__DISASTER_MERMAID_LOADING__) return;
        window.__DISASTER_MERMAID_LOADING__ = true;
        const script = document.createElement('script');
        script.src = 'lib/mermaid.min.js';
        script.async = true;
        script.onload = () => setMermaidReady(Boolean(window.mermaid));
        script.onerror = () => setMermaidReady(false);
        document.head.appendChild(script);
    }, []);

    React.useEffect(() => {
        if (!mermaidReady) return;
        const articleEl = articleRef.current;
        const mermaid = window.mermaid;
        if (!articleEl) return;

        // 获取当前文档中被 Markdown 工具归类解析为 Mermaid 代码段的所有容器块
        const mermaidBlocks = articleEl.querySelectorAll('.notification-md-mermaid[data-mermaid-source]');
        if (!mermaidBlocks.length) return;

        // 若全局未注入 Mermaid 渲染库，标记错误类名直接退化展示纯文本
        if (!mermaid || typeof mermaid.render !== 'function') {
            mermaidBlocks.forEach((block) => block.classList.add('is-error'));
            return;
        }

        // 单例模式全局初始化配置
        if (!window.__DISASTER_MERMAID_INITIALIZED && typeof mermaid.initialize === 'function') {
            mermaid.initialize({ 
                startOnLoad: false, 
                securityLevel: 'strict', 
                theme: theme === 'dark' ? 'dark' : 'default' 
            });
            window.__DISASTER_MERMAID_INITIALIZED = true;
        }

        let disposed = false; // 垃圾回收中断标志
        const cleanupFns = [];  // 各视口组件的注销闭包收集栈

        const renderAllMermaidBlocks = async () => {
            for (let index = 0; index < mermaidBlocks.length; index += 1) {
                if (disposed) return;
                const block = mermaidBlocks[index];
                const source = String(block.getAttribute('data-mermaid-source') || '').trim();
                if (!source) continue;
                
                // 自动组装在 DOM 中全局唯一的组件 ID，过滤特殊非法标点
                const renderId = `disaster-mermaid-${documentPath || 'doc'}-${index}-${Date.now()}`.replace(/[^a-zA-Z0-9_-]/g, '-');

                try {
                    block.classList.remove('is-error');
                    // 语法解析校验
                    if (typeof mermaid.parse === 'function') {
                        await mermaid.parse(source, { suppressErrors: true });
                    }
                    // 执行 SVG 代码生成
                    const renderResult = await mermaid.render(renderId, source);
                    if (disposed) return;
                    
                    // 将生成的矢量图形注入容器，并绑定拖拽缩放的高级视口控制器
                    block.innerHTML = renderResult?.svg || '';
                    window.MermaidViewport.attachMermaidViewportControls(block, cleanupFns);
                } catch (error) {
                    if (disposed) return;
                    block.classList.add('is-error');
                    block.textContent = source;
                }
            }
        };

        renderAllMermaidBlocks();
        
        // 返回资源清理闭包
        return () => {
            disposed = true;
            cleanupFns.forEach((cleanup) => {
                try { cleanup(); } catch (e) {}
            });
        };
    }, [articleRef, documentPath, renderedHtml, theme, mermaidReady]);
}
