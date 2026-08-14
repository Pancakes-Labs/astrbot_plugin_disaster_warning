/**
 * 提供管理端通用的 Markdown 渲染与安全清洗能力，供多个视图复用。
 *
 * 职责边界说明：
 * - 本文件负责 Markdown 渲染管线（marked / DOMPurify 增强渲染 + 内置 fallback 编译器）、
 *   安全清洗、代码块 HTML 结构拼装，保持职责解耦。
 */

// 代码块展示标签的配置字典
const MARKDOWN_CODE_LANGUAGE_LABELS = {
    js: 'JavaScript',
    jsx: 'JSX',
    javascript: 'JavaScript',
    ts: 'TypeScript',
    tsx: 'TSX',
    typescript: 'TypeScript',
    json: 'JSON',
    bash: 'Bash',
    shell: 'Shell',
    sh: 'Shell',
    zsh: 'Zsh',
    powershell: 'PowerShell',
    cmd: 'CMD',
    python: 'Python',
    py: 'Python',
    yaml: 'YAML',
    yml: 'YAML',
    html: 'HTML',
    htm: 'HTML',
    xml: 'XML',
    svg: 'SVG',
    css: 'CSS',
    scss: 'SCSS',
    less: 'Less',
    md: 'Markdown',
    markdown: 'Markdown',
    mermaid: 'Mermaid',
    text: 'Text',
    plaintext: 'Text',
};

// 安全清洗白名单参数
const MARKDOWN_SANITIZE_OPTIONS = {
    // 仅放行基础展示标签，拦截高风险标签防止脚本注入
    ALLOWED_TAGS: [
        'a', 'blockquote', 'br', 'code', 'del', 'details', 'div', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'hr', 'img', 'li', 'ol', 'p', 'pre', 'span', 'strong', 'summary', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul'
    ],
    // 仅放行图片、链接及图表自定义属性
    ALLOWED_ATTR: ['href', 'target', 'rel', 'class', 'data-language', 'data-mermaid-source', 'src', 'alt', 'title', 'width', 'height', 'align', 'open', 'loading', 'decoding', 'referrerpolicy'],
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form'],
    FORBID_ATTR: ['style', 'onerror', 'onload', 'onclick', 'onmouseover', 'onfocus'],
    RETURN_DOM_FRAGMENT: true,
};

/**
 * 规范化文本换行符为标准 LF
 */
function normalizeMarkdownContent(content) {
    return String(content || '')
        .replace(/\r\n/g, '\n')
        .replace(/\\n/g, '\n')
        .replace(/\r\n?/g, '\n');
}

/**
 * 通过正则特征粗筛是否属于 Markdown 排版语法
 */
function isProbablyMarkdown(content) {
    const normalized = normalizeMarkdownContent(content);
    return /(^|\n)\s{0,3}(#{1,6}\s|>\s|[-*+]\s)|(\n|^)\s*\d+\.\s|(^|\n)\s*```|(^|\n)\|.+\|/m.test(normalized);
}

/**
 * 对普通字符串进行安全的 HTML 实体转义
 */
function escapeMarkdownHtml(text) {
    return String(text || '')
        // 所有 fallback 渲染路径都先做 HTML 转义，防止原始文本直接进入 innerHTML。
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}


/**
 * 净化并规整代码语言标识符
 */
function normalizeMarkdownLanguageName(language) {
    const normalized = String(language || '').trim().toLowerCase();
    if (!normalized) return 'text';
    return normalized.replace(/[^a-z0-9_-]/g, '') || 'text';
}

/**
 * 获取友好的语言展示标签
 */
function getMarkdownLanguageLabel(language) {
    const normalized = normalizeMarkdownLanguageName(language);
    return MARKDOWN_CODE_LANGUAGE_LABELS[normalized] || normalized.toUpperCase() || 'Text';
}

/**
 * 链接协议安全性拦截过滤
 */
function getSafeMarkdownLinkHref(href) {
    const value = String(href || '').trim();
    if (/^https?:\/\//i.test(value) || value.startsWith('/') || value.startsWith('#')) {
        return value;
    }
    return '#';
}

/**
 * 图片资源路径安全性拦截过滤
 */
function getSafeMarkdownImageSrc(src) {
    const value = String(src || '').trim();
    if (/^https?:\/\//i.test(value) || value.startsWith('/') || value.startsWith('./') || value.startsWith('../')) {
        return value;
    }
    return '';
}

/**
 * 对代码文本进行语法高亮着色。
 * 委托给独立的高亮引擎模块 markdownHighlighter.js（职责解耦）。
 */
function highlightMarkdownCode(code, language) {
    const highlighter = window.MarkdownCodeHighlighter;
    if (highlighter && typeof highlighter.highlightMarkdownCode === 'function') {
        return highlighter.highlightMarkdownCode(code, language);
    }
    // 高亮引擎未就绪时兜底：保证输出安全可读的纯文本。
    return escapeMarkdownHtml(code);
}

/**
 * 拼装代码块顶部标题栏 HTML：经典 macOS 红绿灯圆点 + 语言徽章，强化代码块 / 终端风格。
 */
function buildMarkdownCodeHeaderHtml(languageLabel) {
    return [
        '<div class="notification-md-code-header">',
        '<span class="notification-md-code-traffic-lights" aria-hidden="true">',
        '<i class="notification-md-traffic-light is-red"></i>',
        '<i class="notification-md-traffic-light is-yellow"></i>',
        '<i class="notification-md-traffic-light is-green"></i>',
        '</span>',
        `<span class="notification-md-code-lang">${escapeMarkdownHtml(languageLabel)}</span>`,
        '</div>',
    ].join('');
}

/**
 * 拼装 Mermaid 图表源码容器块 HTML
 */
function buildMarkdownMermaidBlockHtml(code) {
    const normalizedCode = normalizeMarkdownContent(code).trim();
    const escapedCode = escapeMarkdownHtml(normalizedCode);
    return [
        '<div class="notification-md-mermaid-block" data-language="mermaid">',
        buildMarkdownCodeHeaderHtml(getMarkdownLanguageLabel('mermaid')),
        `<div class="notification-md-mermaid" data-mermaid-source="${escapedCode}">${escapedCode}</div>`,
        '</div>',
    ].join('');
}

/**
 * 拼装常规语法高亮代码块 HTML
 */
function buildMarkdownCodeBlockHtml(code, language) {
    const normalizedLanguage = normalizeMarkdownLanguageName(language);
    if (normalizedLanguage === 'mermaid') {
        return buildMarkdownMermaidBlockHtml(code);
    }
    const languageClass = normalizedLanguage !== 'text' ? ` language-${normalizedLanguage}` : ' language-text';
    const languageLabel = getMarkdownLanguageLabel(normalizedLanguage);
    const highlightedCode = highlightMarkdownCode(code, normalizedLanguage);
    return [
        `<div class="notification-md-code-block${languageClass}">`,
        buildMarkdownCodeHeaderHtml(languageLabel),
        `<pre><code class="${languageClass.trim()}">${highlightedCode}</code></pre>`,
        '</div>',
    ].join('');
}

/**
 * 提取内联 code 代码段并暂时用占位符替换，防止受后续段落级正则解析干扰
 */
function extractInlineCodePlaceholders(text) {
    const placeholders = [];
    let output = '';
    const source = String(text || '');
    let index = 0;

    while (index < source.length) {
        const char = source[index];
        const prevChar = index > 0 ? source[index - 1] : '';
        if (char !== '`' || prevChar === '\\') {
            output += char;
            index += 1;
            continue;
        }

        let fenceLength = 1;
        while (source[index + fenceLength] === '`') {
            fenceLength += 1;
        }
        const fence = '`'.repeat(fenceLength);
        const searchStart = index + fenceLength;
        const closingIndex = source.indexOf(fence, searchStart);
        if (closingIndex === -1) {
            output += fence;
            index = searchStart;
            continue;
        }

        const code = source.slice(searchStart, closingIndex);
        if (!code) {
            output += fence + fence;
            index = closingIndex + fenceLength;
            continue;
        }

        const placeholderIndex = placeholders.push(`<code>${escapeMarkdownHtml(code)}</code>`) - 1;
        output += `@@INLINE_CODE_${placeholderIndex}@@`;
        index = closingIndex + fenceLength;
    }

    return { output, placeholders };
}

/**
 * 还原占位符为 HTML 内联 code 样式
 */
function restoreInlineCodePlaceholders(text, placeholders) {
    return String(text || '').replace(/@@INLINE_CODE_(\d+)@@/g, (_, index) => placeholders[Number(index)] || '');
}

/**
 * 转换内联的超链接、加粗、斜体与删除线标记
 */
function applyInlineMarkdownTokens(text) {
    const extracted = extractInlineCodePlaceholders(text);
    let output = extracted.output;

    // 解析 [文本](链接) 为可点亮超链接
    output = output.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
        const safeHref = getSafeMarkdownLinkHref(href);
        return `<a href="${escapeMarkdownHtml(safeHref)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
    // 解析粗体斜体与删除线
    output = output.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    output = output.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    output = output.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, '$1<em>$2</em>');
    output = output.replace(/(^|[^_])_([^_]+)_(?!_)/g, '$1<em>$2</em>');
    output = output.replace(/~~([^~]+)~~/g, '<del>$1</del>');

    return restoreInlineCodePlaceholders(output, extracted.placeholders);
}

function renderInlineMarkdownText(text) {
    return applyInlineMarkdownTokens(escapeMarkdownHtml(text));
}

function splitMarkdownTableCells(line) {
    const trimmed = String(line || '').trim().replace(/^\|/, '').replace(/\|$/, '');
    return trimmed.split('|').map((cell) => cell.trim());
}

/**
 * 段落缓冲栈结算为 HTML p 元素
 */
function flushMarkdownParagraph(paragraphBuffer, htmlParts) {
    if (!paragraphBuffer.length) return [];
    const safeText = paragraphBuffer
        .map((line) => renderInlineMarkdownText(line))
        .join('<br />');
    htmlParts.push(`<p>${safeText}</p>`);
    return [];
}

function closeMarkdownList(listState, htmlParts) {
    if (!listState) return null;
    htmlParts.push(`</${listState}>`);
    return null;
}

/**
 * 引用块缓冲栈结算为 HTML blockquote 元素
 */
function flushMarkdownBlockquote(blockquoteBuffer, htmlParts) {
    if (!blockquoteBuffer.length) return [];
    // 递归解析块引用内部的子排版标记
    const quoteHtml = fallbackRenderMarkdownHtml(blockquoteBuffer.join('\n'));
    htmlParts.push(`<blockquote>${quoteHtml}</blockquote>`);
    return [];
}

/**
 * 特殊判定是否为命令别名对照表格，用于加载特定宽度的 CSS 样式
 */
function isMarkdownCommandAliasTableHeader(headerCells) {
    const normalizedHeader = (headerCells || []).map((cell) => String(cell || '').trim());
    return normalizedHeader.length === 3
        && normalizedHeader[0] === '命令'
        && normalizedHeader[1] === '别名'
        && normalizedHeader[2] === '描述';
}

/**
 * 表格缓存栈结算为 HTML table 元素并添加包裹层
 */
function flushMarkdownTable(tableHeader, tableRows, htmlParts) {
    if (!tableHeader) {
        return { tableHeader: null, tableRows: [] };
    }
    const tableClass = isMarkdownCommandAliasTableHeader(tableHeader) ? ' class="notification-md-command-alias-table"' : '';
    const headCells = tableHeader.map((cell) => `<th>${renderInlineMarkdownText(cell)}</th>`).join('');
    const bodyRows = tableRows
        .map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdownText(cell)}</td>`).join('')}</tr>`)
        .join('');
    htmlParts.push(`<div class="notification-md-table-wrap"><table${tableClass}><thead><tr>${headCells}</tr></thead><tbody>${bodyRows}</tbody></table></div>`);
    return { tableHeader: null, tableRows: [] };
}

/**
 * 原生极简 Markdown 逐行编译逻辑
 */
function fallbackRenderMarkdownHtml(md) {
    const normalized = normalizeMarkdownContent(md);
    const lines = normalized.split('\n');
    const htmlParts = [];
    let paragraphBuffer = [];
    let listState = null;
    let blockquoteBuffer = [];
    let codeFence = null;
    let tableHeader = null;
    let tableRows = [];

    for (let index = 0; index < lines.length; index += 1) {
        const rawLine = lines[index];
        const line = rawLine.trimEnd();
        const trimmed = line.trim();

        // 处于 fenced 代码块内部，直接添加至缓存并忽略解析
        if (codeFence) {
            if (/^```/.test(trimmed)) {
                htmlParts.push(buildMarkdownCodeBlockHtml(codeFence.lines.join('\n'), codeFence.language));
                codeFence = null;
            } else {
                codeFence.lines.push(rawLine);
            }
            continue;
        }

        // 代码块开启标识
        if (/^```/.test(trimmed)) {
            paragraphBuffer = flushMarkdownParagraph(paragraphBuffer, htmlParts);
            listState = closeMarkdownList(listState, htmlParts);
            blockquoteBuffer = flushMarkdownBlockquote(blockquoteBuffer, htmlParts);
            ({ tableHeader, tableRows } = flushMarkdownTable(tableHeader, tableRows, htmlParts));
            codeFence = {
                language: trimmed.slice(3).trim(),
                lines: [],
            };
            continue;
        }

        // 表格数据填充
        const tableSeparator = /^\|?(\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\|?$/.test(trimmed);
        if (tableHeader && !tableSeparator && trimmed.includes('|')) {
            tableRows.push(splitMarkdownTableCells(trimmed));
            continue;
        }
        if (tableHeader && (!trimmed || !trimmed.includes('|'))) {
            ({ tableHeader, tableRows } = flushMarkdownTable(tableHeader, tableRows, htmlParts));
        }

        // 空行结算
        if (!trimmed) {
            paragraphBuffer = flushMarkdownParagraph(paragraphBuffer, htmlParts);
            listState = closeMarkdownList(listState, htmlParts);
            blockquoteBuffer = flushMarkdownBlockquote(blockquoteBuffer, htmlParts);
            ({ tableHeader, tableRows } = flushMarkdownTable(tableHeader, tableRows, htmlParts));
            continue;
        }

        // 新表格表头解析判定
        const nextLine = String(lines[index + 1] || '').trim();
        if (!tableHeader && trimmed.includes('|') && /^\|?(\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\|?$/.test(nextLine)) {
            paragraphBuffer = flushMarkdownParagraph(paragraphBuffer, htmlParts);
            listState = closeMarkdownList(listState, htmlParts);
            blockquoteBuffer = flushMarkdownBlockquote(blockquoteBuffer, htmlParts);
            tableHeader = splitMarkdownTableCells(trimmed);
            tableRows = [];
            index += 1; // 跳过下一行分隔线
            continue;
        }

        // H1-H6 标题级别解析
        const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
        if (heading) {
            paragraphBuffer = flushMarkdownParagraph(paragraphBuffer, htmlParts);
            listState = closeMarkdownList(listState, htmlParts);
            blockquoteBuffer = flushMarkdownBlockquote(blockquoteBuffer, htmlParts);
            ({ tableHeader, tableRows } = flushMarkdownTable(tableHeader, tableRows, htmlParts));
            const level = heading[1].length;
            htmlParts.push(`<h${level}>${renderInlineMarkdownText(heading[2])}</h${level}>`);
            continue;
        }

        // HR 水平分割线解析
        if (/^---+$/.test(trimmed) || /^\*\*\*+$/.test(trimmed)) {
            paragraphBuffer = flushMarkdownParagraph(paragraphBuffer, htmlParts);
            listState = closeMarkdownList(listState, htmlParts);
            blockquoteBuffer = flushMarkdownBlockquote(blockquoteBuffer, htmlParts);
            ({ tableHeader, tableRows } = flushMarkdownTable(tableHeader, tableRows, htmlParts));
            htmlParts.push('<hr />');
            continue;
        }

        // 块引用解析
        const quote = line.match(/^>\s?(.*)$/);
        if (quote) {
            paragraphBuffer = flushMarkdownParagraph(paragraphBuffer, htmlParts);
            listState = closeMarkdownList(listState, htmlParts);
            ({ tableHeader, tableRows } = flushMarkdownTable(tableHeader, tableRows, htmlParts));
            blockquoteBuffer.push(quote[1]);
            continue;
        }
        blockquoteBuffer = flushMarkdownBlockquote(blockquoteBuffer, htmlParts);

        // 无序及有序列表解析
        const unorderedItem = trimmed.match(/^[-*+]\s+(.+)$/);
        const orderedItem = trimmed.match(/^\d+\.\s+(.+)$/);
        const listType = unorderedItem ? 'ul' : orderedItem ? 'ol' : null;
        if (listType) {
            paragraphBuffer = flushMarkdownParagraph(paragraphBuffer, htmlParts);
            ({ tableHeader, tableRows } = flushMarkdownTable(tableHeader, tableRows, htmlParts));
            if (listState && listState !== listType) {
                listState = closeMarkdownList(listState, htmlParts);
            }
            if (!listState) {
                htmlParts.push(`<${listType}>`);
                listState = listType;
            }
            const listContent = unorderedItem ? unorderedItem[1] : orderedItem[1];
            htmlParts.push(`<li>${renderInlineMarkdownText(listContent)}</li>`);
            continue;
        }

        listState = closeMarkdownList(listState, htmlParts);
        ({ tableHeader, tableRows } = flushMarkdownTable(tableHeader, tableRows, htmlParts));
        paragraphBuffer.push(line);
    }

    // 清理最终残存缓存
    paragraphBuffer = flushMarkdownParagraph(paragraphBuffer, htmlParts);
    listState = closeMarkdownList(listState, htmlParts);
    blockquoteBuffer = flushMarkdownBlockquote(blockquoteBuffer, htmlParts);
    ({ tableHeader, tableRows } = flushMarkdownTable(tableHeader, tableRows, htmlParts));

    if (codeFence) {
        htmlParts.push(buildMarkdownCodeBlockHtml(codeFence.lines.join('\n'), codeFence.language));
    }

    return htmlParts.join('');
}

const MARKDOWN_CALLOUT_META = {
    note: { label: 'Note', icon: '📝' },
    tip: { label: 'Tip', icon: '💡' },
    important: { label: 'Important', icon: '❗' },
    warning: { label: 'Warning', icon: '⚠️' },
    caution: { label: 'Caution', icon: '🚨' },
};

/**
 * 转换 GFM 格式的警告提示卡片 (Callout Blockquotes)
 */
function enhanceMarkdownCalloutBlockquotes(sanitizedFragment) {
    sanitizedFragment.querySelectorAll('blockquote').forEach((blockquote) => {
        const firstChild = blockquote.firstElementChild;
        if (!firstChild || firstChild.tagName !== 'P') {
            return;
        }

        const firstTextNode = Array.from(firstChild.childNodes).find((node) => node.nodeType === Node.TEXT_NODE && String(node.textContent || '').trim());
        if (!firstTextNode) {
            return;
        }

        const originalText = String(firstTextNode.textContent || '');
        const match = originalText.match(/^\s*\[!([A-Z]+)\]\s*(.*)$/);
        if (!match) {
            return;
        }

        const calloutType = String(match[1] || '').trim().toLowerCase();
        const calloutMeta = MARKDOWN_CALLOUT_META[calloutType];
        if (!calloutMeta) {
            return;
        }

        const inlineTitle = String(match[2] || '').trim();
        blockquote.className = ''; // 清空并赋给警告类名
        blockquote.classList.add('notification-md-callout', `is-${calloutType}`);

        const header = document.createElement('div');
        header.className = 'notification-md-callout-header';

        const icon = document.createElement('span');
        icon.className = 'notification-md-callout-icon';
        icon.textContent = calloutMeta.icon;
        header.appendChild(icon);

        const title = document.createElement('span');
        title.className = 'notification-md-callout-title';
        title.textContent = inlineTitle || calloutMeta.label;
        header.appendChild(title);

        const nextText = originalText.replace(match[0], '').trimStart();
        if (nextText) {
            firstTextNode.textContent = nextText;
        } else {
            firstTextNode.parentNode.removeChild(firstTextNode);
        }

        if (!firstChild.childNodes.length) {
            firstChild.remove();
        }

        blockquote.insertBefore(header, blockquote.firstChild || null);
    });
}

/**
 * 对三方渲染的 HTML 片段进行深度后处理：注入安全属性、包裹表结构、附加代码高亮等
 */
function enhanceMarkdownFragment(sanitizedFragment) {
    // 强制赋予超链接外跳安全性属性
    sanitizedFragment.querySelectorAll('a[href]').forEach((link) => {
        const href = String(link.getAttribute('href') || '').trim();
        if (!/^https?:\/\//i.test(href) && !href.startsWith('/') && !href.startsWith('#')) {
            link.setAttribute('href', '#');
        }
        link.setAttribute('target', '_blank');
        link.setAttribute('rel', 'noopener noreferrer');
    });

    // 强制赋予图片安全懒加载属性
    sanitizedFragment.querySelectorAll('img[src]').forEach((img) => {
        const safeSrc = getSafeMarkdownImageSrc(img.getAttribute('src'));
        if (!safeSrc) {
            img.remove();
            return;
        }
        img.setAttribute('src', safeSrc);
        img.setAttribute('loading', 'lazy');
        img.setAttribute('decoding', 'async');
        img.setAttribute('referrerpolicy', 'no-referrer');
    });

    // 表格样式重载及外包横向滑动容器
    sanitizedFragment.querySelectorAll('table').forEach((table) => {
        const headerCells = Array.from(table.querySelectorAll('thead th')).map((cell) => String(cell.textContent || '').trim());
        if (isMarkdownCommandAliasTableHeader(headerCells)) {
            table.classList.add('notification-md-command-alias-table');
        }

        if (!table.parentElement || !table.parentElement.classList.contains('notification-md-table-wrap')) {
            const wrapper = document.createElement('div');
            wrapper.className = 'notification-md-table-wrap';
            table.parentNode.insertBefore(wrapper, table);
            wrapper.appendChild(table);
        }
    });

    enhanceMarkdownCalloutBlockquotes(sanitizedFragment);

    // 折叠菜单样式重定义
    sanitizedFragment.querySelectorAll('details').forEach((detailsEl) => {
        detailsEl.classList.add('notification-md-details');
        const firstSummary = Array.from(detailsEl.children).find((child) => child.tagName === 'SUMMARY');
        if (firstSummary) {
            firstSummary.classList.add('notification-md-summary');
        } else {
            const summary = document.createElement('summary');
            summary.className = 'notification-md-summary';
            summary.textContent = '展开详情';
            detailsEl.insertBefore(summary, detailsEl.firstChild || null);
        }
    });

    // 为普通 code 代码段添加复制、语言标签与轻量高亮效果
    sanitizedFragment.querySelectorAll('pre > code').forEach((codeEl) => {
        const originalClass = String(codeEl.getAttribute('class') || '');
        const languageMatch = originalClass.match(/language-([a-z0-9_-]+)/i);
        const language = normalizeMarkdownLanguageName(languageMatch ? languageMatch[1] : 'text');
        const rawCodeText = codeEl.textContent || '';
        const pre = codeEl.parentElement;

        if (language === 'mermaid') {
            const mermaidWrapper = document.createElement('div');
            mermaidWrapper.innerHTML = buildMarkdownMermaidBlockHtml(rawCodeText);
            pre.parentNode.replaceChild(mermaidWrapper.firstElementChild, pre);
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.className = `notification-md-code-block language-${language}`;
        wrapper.setAttribute('data-language', language);

        const header = document.createElement('div');
        header.className = 'notification-md-code-header';

        // 经典红绿灯圆点，强化代码块 / 终端风格
        const trafficLights = document.createElement('span');
        trafficLights.className = 'notification-md-code-traffic-lights';
        trafficLights.setAttribute('aria-hidden', 'true');
        ['is-red', 'is-yellow', 'is-green'].forEach((lightClass) => {
            const light = document.createElement('i');
            light.className = `notification-md-traffic-light ${lightClass}`;
            trafficLights.appendChild(light);
        });

        const langChip = document.createElement('span');
        langChip.className = 'notification-md-code-lang';
        langChip.textContent = getMarkdownLanguageLabel(language);

        header.appendChild(trafficLights);
        header.appendChild(langChip);

        codeEl.className = `language-${language}`;
        codeEl.innerHTML = highlightMarkdownCode(rawCodeText, language);

        pre.parentNode.insertBefore(wrapper, pre);
        wrapper.appendChild(header);
        wrapper.appendChild(pre);
    });
}

/**
 * 按需动态加载 Markdown 增强依赖（marked / DOMPurify）。
 *
 * 背景：这三个库体积较大（mermaid 约 2.5MB），且此前从 unpkg.com CDN 以 defer 方式
 * 阻塞加载在 app.jsx 入口之前，外网超时（浏览器默认约 30s）会直接卡死整个管理端
 * 首次加载与页面切换。因此改为本地化 + 惰性加载：仅当真正进入“文档浏览”页时
 * 才注入同源脚本，加载失败时静默降级到内置渲染器，不影响任何功能。
 *
 * @returns {Promise<boolean>} 是否成功加载增强依赖
 */
function ensureMarkdownLibs() {
    if (window.__DISASTER_MARKDOWN_LIBS_READY__) {
        return Promise.resolve(true);
    }
    if (window.__DISASTER_MARKDOWN_LIBS_LOADING__) {
        return window.__DISASTER_MARKDOWN_LIBS_LOADING__;
    }
    const loadScript = (src) => new Promise((resolve) => {
        const existing = document.querySelector(`script[src="${src}"]`);
        if (existing) {
            resolve();
            return;
        }
        const script = document.createElement('script');
        script.src = src;
        script.async = true;
        script.onload = () => resolve();
        script.onerror = () => resolve(); // 失败也 resolve，由调用方检查 window.marked 决定是否降级
        document.head.appendChild(script);
    });

    window.__DISASTER_MARKDOWN_LIBS_LOADING__ = Promise.all([
        loadScript('lib/marked.min.js'),
        loadScript('lib/purify.min.js'),
    ]).then(() => {
        const ready = Boolean(window.marked && window.DOMPurify);
        window.__DISASTER_MARKDOWN_LIBS_READY__ = ready;
        window.__DISASTER_MARKDOWN_LIBS_LOADING__ = null;
        return ready;
    });
    return window.__DISASTER_MARKDOWN_LIBS_LOADING__;
}

/**
 * 入口方法：将原始 Markdown 文本渲染并编译输出为安全的 HTML 代码段
 */
function renderMarkdownToHtml(content) {
    const normalized = normalizeMarkdownContent(content);

    try {
        const marked = window.marked;
        const DOMPurify = window.DOMPurify;

        // 如果存在外部注入的 marked 解析器和 Purify 安全库，进行高防度渲染
        if (marked && DOMPurify) {
            const parseMarkdown = (md) => {
                if (typeof marked.parse === 'function') {
                    return marked.parse(md, { gfm: true, breaks: true });
                }
                if (typeof marked === 'function') {
                    return marked(md, { gfm: true, breaks: true });
                }
                throw new Error('marked parser unavailable');
            };

            const rawHtml = parseMarkdown(normalized);
            // 执行 DOMPurify 清洗以消除 XSS 盲区
            const sanitizedFragment = DOMPurify.sanitize(rawHtml, MARKDOWN_SANITIZE_OPTIONS);
            enhanceMarkdownFragment(sanitizedFragment);

            const container = document.createElement('div');
            container.appendChild(sanitizedFragment);
            return container.innerHTML;
        }
    } catch (e) {
        // 出错则降级到本地自研编译器兜底
    }

    return fallbackRenderMarkdownHtml(normalized);
}

// 绑定全局暴露
window.MarkdownRenderUtil = {
    normalizeMarkdownContent,
    isProbablyMarkdown,
    renderMarkdownToHtml,
    ensureMarkdownLibs,
};
