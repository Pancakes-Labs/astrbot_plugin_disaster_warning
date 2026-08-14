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
/**
 * 判断当前是否为暗色主题。
 * 兼容 theme prop 与全局类名/属性（html.theme-dark / body.dark-theme / [data-theme=dark]）
 * 多路判定，避免主题取值不一致导致漏判。
 */
function isDarkThemeActive(theme) {
    if (theme === 'dark') return true;
    if (typeof document === 'undefined') return false;
    const rootEl = document.documentElement;
    const bodyEl = document.body;
    return Boolean(
        (rootEl && (rootEl.classList.contains('theme-dark')
            || rootEl.getAttribute('data-theme') === 'dark'))
        || (bodyEl && (bodyEl.classList.contains('dark-theme')
            || bodyEl.getAttribute('data-theme') === 'dark'))
    );
}

/**
 * 对单个 Mermaid SVG 应用暗色主题适配。
 *
 * 背景：README 等文档硬编码浅色 classDef（fill:#E3F2FD 等），mermaid 会将其写为节点
 * 的 fill 属性/内联样式；且带 <br/> 的节点标签走 <foreignObject> + HTML 元素而非 <text>。
 * 因此这里全量扫描 SVG：
 * - 所有偏亮的 fill 统一压暗为“保留原色相的深色变体”（暗蓝/暗绿/暗紫…），既可读又保留层次；
 * - 所有文字（含 foreignObject 内 HTML）统一浅色；
 * - 连线、箭头统一浅紫。
 */
function applyMermaidDarkAdaptation(svgEl) {
    if (!svgEl || typeof svgEl.querySelectorAll !== 'function') {
        console.warn('[Mermaid] 暗色适配跳过：svgEl 无效或不可查询');
        return;
    }

    const stats = { shapes: 0, texts: 0, htmlTexts: 0, edges: 0, stylesRemoved: 0 };
    const countAndApply = (el, prop, value) => {
        el.style.setProperty(prop, value, 'important');
    };

    const DARK_TEXT_FILL = '#ece7f6';
    const DARK_STROKE = '#9a8fc0';
    const DARK_EDGE = '#b9a8e0';
    const DARK_NODE_FILL = '#241f31';

    const parseRgb = (raw) => {
        if (!raw) return null;
        const text = String(raw).trim();
        let m = text.match(/^#([0-9a-f]{6})$/i);
        if (m) return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16)];
        m = text.match(/^#([0-9a-f]{3})$/i);
        if (m) {
            const r = parseInt(m[1][0] + m[1][0], 16);
            const g = parseInt(m[1][1] + m[1][1], 16);
            const b = parseInt(m[1][2] + m[1][2], 16);
            return [r, g, b];
        }
        m = text.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
        if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
        return null;
    };

    const rgbToHsl = ([r, g, b]) => {
        const rr = r / 255; const gg = g / 255; const bb = b / 255;
        const max = Math.max(rr, gg, bb); const min = Math.min(rr, gg, bb);
        let h = 0; const l = (max + min) / 2; let s = 0;
        if (max !== min) {
            const d = max - min;
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            if (max === rr) h = (gg - bb) / d + (gg < bb ? 6 : 0);
            else if (max === gg) h = (bb - rr) / d + 2;
            else h = (rr - gg) / d + 4;
            h /= 6;
        }
        return [h * 360, s, l];
    };

    const hslToRgb = ([h, s, l]) => {
        const hh = h / 360;
        const hue2rgb = (p, q, t) => {
            let tt = t;
            if (tt < 0) tt += 1;
            if (tt > 1) tt -= 1;
            if (tt < 1 / 6) return p + (q - p) * 6 * tt;
            if (tt < 1 / 2) return q;
            if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
            return p;
        };
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        return [
            Math.round(hue2rgb(p, q, hh + 1 / 3) * 255),
            Math.round(hue2rgb(p, q, hh) * 255),
            Math.round(hue2rgb(p, q, hh - 1 / 3) * 255),
        ];
    };

    const darkenColor = (raw, targetLightness) => {
        const rgb = parseRgb(raw);
        if (!rgb) return null;
        const [h, s] = rgbToHsl(rgb);
        const [r2, g2, b2] = hslToRgb([h, Math.min(1, Math.max(0.35, s)), targetLightness]);
        return `rgb(${r2},${g2},${b2})`;
    };

    // 1. 删除 SVG 内嵌 <style> 标签中「带具体浅色值」的 classDef 规则块：
    //    mermaid 会把 classDef 的 fill:#E3F2FD 等写进内嵌 <style>，这些规则优先级高于
    //    元素属性，但低于内联样式；这里直接把这些规则块整个移除，让 mermaid dark 主题
    //    的默认深色节点样式接管，同时内联样式覆盖保证 100% 生效。
    svgEl.querySelectorAll('style').forEach((styleTag) => {
        const css = styleTag.textContent || '';
        // 匹配 .classname > selector { ... } 形式的规则块，且块内含 fill/color 声明
        const rewritten = css.replace(
            /[^{}]+\{[^{}]*?(?:fill|color)\s*:[^{}]*?[#\w][^{}]*?\}/gi,
            (block) => {
                // 仅移除「fill 或 color 声明了具体颜色」的规则块
                if (/:\s*#[0-9a-f]{3,8}\b/i.test(block) || /:\s*rgba?\(/i.test(block)) {
                    return '';
                }
                return block;
            }
        );
        if (rewritten !== css) {
            styleTag.textContent = rewritten;
            stats.stylesRemoved += 1;
        }
    });

    // 2. 节点形状：用 setProperty(..., 'important') 强制内联覆盖（优先级最高），
    //    所有节点形状统一深底 + 浅紫描边，不依赖任何读取/判断，100% 保证可读。
    const nodeShapes = svgEl.querySelectorAll('.node rect, .node polygon, .node circle, .node path, .cluster rect, .cluster polygon, .cluster circle, .cluster path');
    nodeShapes.forEach((shape) => {
        shape.style.setProperty('fill', DARK_NODE_FILL, 'important');
        shape.style.setProperty('stroke', DARK_STROKE, 'important');
        stats.shapes += 1;
    });

    // 3. 兜底：所有 rect/polygon/circle 统一强制深底（含未带 .node/.cluster 类名的形状）
    svgEl.querySelectorAll('rect, polygon, circle').forEach((shape) => {
        shape.style.setProperty('fill', DARK_NODE_FILL, 'important');
        shape.style.setProperty('stroke', DARK_STROKE, 'important');
        stats.shapes += 1;
    });

    // 4. 所有 SVG 文字统一浅色（含 tspan）
    svgEl.querySelectorAll('text, tspan').forEach((textEl) => {
        textEl.style.setProperty('fill', DARK_TEXT_FILL, 'important');
        textEl.style.setProperty('color', DARK_TEXT_FILL, 'important');
        stats.texts += 1;
    });

    // 5. foreignObject 内的 HTML 文字（带 <br/> 的节点标签）统一浅色
    svgEl.querySelectorAll('foreignObject div, foreignObject span, foreignObject p').forEach((htmlEl) => {
        htmlEl.style.setProperty('color', DARK_TEXT_FILL, 'important');
        stats.htmlTexts += 1;
    });

    // 6. 连线与箭头统一浅紫
    svgEl.querySelectorAll('.edgePath path, .flowchart-link, .edgePaths path, [class*="edge"] path').forEach((edgeEl) => {
        edgeEl.style.setProperty('stroke', DARK_EDGE, 'important');
        stats.edges += 1;
    });
    svgEl.querySelectorAll('marker path').forEach((arrowEl) => {
        arrowEl.style.setProperty('fill', DARK_EDGE, 'important');
        arrowEl.style.setProperty('stroke', DARK_EDGE, 'important');
        stats.edges += 1;
    });

    console.log(
        `[Mermaid] 暗色适配统计：形状=${stats.shapes} 文字=${stats.texts} HTML文字=${stats.htmlTexts} ` +
        `连线=${stats.edges} 移除样式规则=${stats.stylesRemoved}`
    );
}

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

        // mermaid 初始化配置：主题跟随当前 UI 主题。
        // 首次初始化后记录当前主题；主题切换时（theme 依赖变化触发本 effect 重跑）
        // 重新 initialize 并按新主题重绘，保证暗色模式下图表可读。
        if (typeof mermaid.initialize === 'function') {
            mermaid.initialize({
                startOnLoad: false,
                securityLevel: 'strict',
                theme: theme === 'dark' ? 'dark' : 'default',
            });
        }
        if (window.__DISASTER_MERMAID_INITIALIZED_THEME__ !== theme) {
            window.__DISASTER_MERMAID_INITIALIZED_THEME__ = theme;
            // 主题变化时清空已渲染的 SVG，交由下方 renderAllMermaidBlocks 按新主题重绘
            mermaidBlocks.forEach((block) => {
                const viewport = block.querySelector('.notification-md-mermaid-viewport');
                const svg = viewport ? viewport.querySelector('svg') : block.querySelector('svg');
                if (svg) {
                    const container = viewport || block;
                    container.innerHTML = block.getAttribute('data-mermaid-source') || '';
                }
            });
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

                    // 暗色主题适配：mermaid 会以 inline style 写入节点（classDef 硬编码的浅色底/深色字），
                    // dark 主题也覆盖不掉；且 README 里大量节点带 <br/>，mermaid 会用
                    // <foreignObject> + HTML <div>/<span> 渲染文字（不是 <text>），
                    // 精确类名选择器无法覆盖。因此这里做全量 DOM 扫描：
                    // - 所有带浅色 fill 的形状统一压暗（保留色相）；
                    // - 所有文字（含 foreignObject 内 HTML 元素）统一浅色；
                    // - 所有 path 连线统一浅紫。
                    // 同时兼容 theme prop 与全局类名/属性多路暗色判定。
                    // attach 前先对渲染出的 SVG 执行一次暗色适配（深底浅字 + 保留色相压暗）。
                    const isDarkTheme = isDarkThemeActive(theme);
                    if (isDarkTheme) {
                        applyMermaidDarkAdaptation(block.querySelector('svg'));
                    }

                    window.MermaidViewport.attachMermaidViewportControls(block, cleanupFns);

                    // attach 会把 SVG 移入 viewport 容器，此处对移动后的 SVG 补执行一次暗色适配，
                    // 确保任何时序下节点底/文字/连线都已重写为深底浅字。
                    if (isDarkTheme) {
                        const movedSvg = block.querySelector('.notification-md-mermaid-viewport svg');
                        applyMermaidDarkAdaptation(movedSvg || block.querySelector('svg'));
                        console.log('[Mermaid] 暗色适配已应用（节点底/文字/连线已重写）');
                    }

                    // 延迟兜底：待浏览器完成 SVG 挂载与样式计算后再次强制覆盖，
                    // 防止 mermaid 异步注入的 <style> 规则在适配执行后才生效导致颜色回弹。
                    if (isDarkTheme) {
                        const timer = setTimeout(() => {
                            if (disposed) return;
                            const lateSvg = block.querySelector('.notification-md-mermaid-viewport svg')
                                || block.querySelector('svg');
                            applyMermaidDarkAdaptation(lateSvg);
                        }, 120);
                        cleanupFns.push(() => clearTimeout(timer));
                    }
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
