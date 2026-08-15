/**
 * 模块名称：Markdown 代码块语法高亮引擎（v2 流式扫描架构）
 * 文件路径：admin/js/utils/markdownHighlighter.js
 *
 * 架构说明（v2 重构）：
 * - 采用「原始源码流式扫描 → 直接 emit token」架构，彻底解决 v1 的三大顽疾：
 *   1) 哨兵泄露：不再使用 @@S{n}@@ 占位符与 protect/restore，输出为线性拼接，天然无嵌套 span；
 *   2) 注释吞换行：注释扫描到 \n 前止，\n 由主循环作为普通文本输出，保留换行；
 *   3) 实体二次转义：在原始字符上匹配词法，转义仅在 emit 时进行，不会二次污染。
 * - 支持语言：python / javascript / typescript / jsx / tsx / css / scss / less /
 *             html / xml / svg / bash / shell / sh / zsh / powershell / cmd /
 *             json / yaml / yml。
 * - 配色对齐 VSCode Dark Modern，由 admin/css/views/markdown.css 的 .token-* 上色。
 */

/* ==========================================================================
   1. 内部工具
   ========================================================================== */

/** HTML 实体转义（仅在输出 token 时调用一次）。
 *  注意：替换目标使用十六进制转义写法，防止 IDE/工具链二次转义破坏字面量。 */
function escapeHtmlText(text) {
    return String(text || '')
        .replace(/&/g, '\x26amp;')
        .replace(/</g, '\x26lt;')
        .replace(/>/g, '\x26gt;')
        .replace(/"/g, '\x26quot;')
        .replace(/'/g, '\x26#39;');
}

function isDigit(c) {
    return c !== undefined && c >= '0' && c <= '9';
}

function isHexDigit(c) {
    return c !== undefined && (isDigit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'));
}

function isIdentStart(c) {
    return /[A-Za-z_]/.test(c || '');
}

function isIdentPart(c) {
    return /[A-Za-z0-9_]/.test(c || '');
}

function isSpace(c) {
    return c === ' ' || c === '\t' || c === '\r' || c === '\u00a0';
}

/**
 * 输出发射器：线性累积 HTML，避免嵌套 span 与哨兵。
 */
function createEmitter() {
    let out = '';
    return {
        /** 追加普通文本（自动转义） */
        text(t) {
            out += escapeHtmlText(t);
        },
        /** 追加已转义的原始片段（用于递归扫描结果的透传） */
        raw(s) {
            out += s;
        },
        /** 追加一个 token（自动转义） */
        token(cls, t) {
            if (t) out += `<span class="token ${cls}">${escapeHtmlText(t)}</span>`;
        },
        /** 追加一个 token（内容为已转义 HTML，用于拼合标点） */
        tokenRaw(cls, html) {
            if (html) out += `<span class="token ${cls}">${html}</span>`;
        },
        toString() {
            return out;
        },
    };
}

/** 从 openIndex 的 { 开始查找匹配的 }（简化实现，忽略字符串内部花括号） */
function findMatchingBrace(source, openIndex) {
    let depth = 0;
    for (let i = openIndex; i < source.length; i += 1) {
        const ch = source[i];
        if (ch === '{') depth += 1;
        else if (ch === '}') {
            depth -= 1;
            if (depth === 0) return i;
        }
    }
    return -1;
}

/**
 * 扫描一段字符串字面量的结束位置（不含闭合引号），用于预判后续字符（如 dict key 判定）。
 * @param {string} source 源码
 * @param {number} start 起始引号位置
 * @returns {number} 字符串结束后的位置（不含）
 */
function scanStringEndIndex(source, start) {
    const quote = source[start];
    let i = start + 1;
    while (i < source.length) {
        if (source[i] === '\\') { i += 2; continue; }
        if (source[i] === quote) return i + 1;
        if (source[i] === '\n' && quote !== '`') return i;
        i += 1;
    }
    return source.length;
}

/**
 * 在 f-string 插值表达式内部查找格式说明符 : 的位置。
 * 规则：忽略出现在 () / [] 内部的冒号（如切片 a[1:2]、三元 x if c else y），
 * 也不匹配字典字面量内部的 {k: v}。返回 -1 表示没有格式说明符。
 */
function findFormatSpecIndex(expr) {
    let depthParen = 0;
    let depthBracket = 0;
    let depthBrace = 0;
    for (let i = 0; i < expr.length; i += 1) {
        const ch = expr[i];
        if (ch === '(') depthParen += 1;
        else if (ch === ')') depthParen -= 1;
        else if (ch === '[') depthBracket += 1;
        else if (ch === ']') depthBracket -= 1;
        else if (ch === '{') depthBrace += 1;
        else if (ch === '}') depthBrace -= 1;
        else if (ch === ':' && depthParen === 0 && depthBracket === 0 && depthBrace === 0) {
            // 三元表达式 ?: 的分号（x if cond else y 无冒号，但 dict 推导可能有）
            const before = expr.slice(0, i);
            // 判断是否为 {k: v} 字典字面量内部：向前找最近的 { 与 , 的位置
            const lastBrace = before.lastIndexOf('{');
            const lastComma = before.lastIndexOf(',');
            if (lastBrace !== -1 && lastBrace > lastComma) {
                continue; // 在字典字面量内部，跳过
            }
            return i;
        }
    }
    return -1;
}

/**
 * 扫描一段字符串字面量（含转义与可选插值）。
 * @param {object} em 发射器
 * @param {string} source 源码
 * @param {number} start 起始引号位置（source[start] 为引号）
 * @param {object} opts { interpolate: (innerCode) => string } 插值回调：返回已转义的内部高亮 HTML
 * @returns {number} 字符串结束后的位置（不含），或 -1（未闭合）
 */
function scanStringRegion(em, source, start, opts) {
    const quote = source[start];
    const { interpolate } = opts || {};
    // 输出起始引号
    em.token('token-string', quote);
    let i = start + 1;
    let segStart = i;
    while (i < source.length) {
        const c = source[i];
        if (c === '\\') {
            i += 2;
            continue;
        }
        if (c === quote) {
            em.token('token-string', source.slice(segStart, i));
            em.token('token-string', quote);
            return i + 1;
        }
        // 模板字符串插值 ${...}（VSCode 中 ${ 与 } 为紫粉色 #C586C0）
        if (interpolate && quote === '`' && c === '$' && source[i + 1] === '{') {
            em.token('token-string', source.slice(segStart, i));
            em.token('token-atrule', '${');
            const end = findMatchingBrace(source, i + 1);
            const innerEnd = end === -1 ? source.length : end;
            em.raw(interpolate(source.slice(i + 2, innerEnd)));
            em.token('token-atrule', '}');
            i = end === -1 ? innerEnd : end + 1;
            segStart = i;
            continue;
        }
        // f-string 插值 {expr[:format_spec]}（{{ 为转义字面量；格式说明符剥离后按字符串色）
        if (interpolate && quote !== '`' && c === '{') {
            if (source[i + 1] === '{') {
                i += 2;
                continue;
            }
            em.token('token-string', source.slice(segStart, i));
            em.token('token-punctuation', '{');
            const end = findMatchingBrace(source, i);
            const innerEnd = end === -1 ? source.length : end;
            // 在表达式末尾剥离 :format_spec（忽略 : 出现在 [] / () 内部的情况）
            const inner = source.slice(i + 1, innerEnd);
            const fmtIdx = findFormatSpecIndex(inner);
            const expr = fmtIdx === -1 ? inner : inner.slice(0, fmtIdx);
            const spec = fmtIdx === -1 ? '' : inner.slice(fmtIdx);
            em.raw(interpolate(expr));
            if (spec) {
                em.token('token-string', spec);
            }
            em.token('token-punctuation', '}');
            i = end === -1 ? innerEnd : end + 1;
            segStart = i;
            continue;
        }
        // 单行字符串不跨行
        if (c === '\n' && quote !== '`') {
            break;
        }
        i += 1;
    }
    em.token('token-string', source.slice(segStart, i));
    return i;
}

/**
 * 归一化语言名
 */
function normalizeLanguageName(language) {
    const normalized = String(language || '').trim().toLowerCase();
    if (!normalized) return 'text';
    return normalized.replace(/[^a-z0-9_-]/g, '') || 'text';
}

/* ==========================================================================
   2. Python 扫描器
   ========================================================================== */

const PY_KEYWORDS = new Set([
    'and', 'as', 'assert', 'async', 'await', 'break', 'class', 'continue', 'def',
    'del', 'elif', 'else', 'except', 'finally', 'for', 'from', 'global', 'if',
    'import', 'in', 'is', 'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise',
    'return', 'try', 'while', 'with', 'yield', 'False', 'None', 'True',
]);

const PY_BUILTINS = new Set([
    'abs', 'all', 'any', 'ascii', 'bin', 'bool', 'breakpoint', 'bytearray',
    'bytes', 'callable', 'chr', 'classmethod', 'compile', 'complex', 'delattr',
    'dict', 'dir', 'divmod', 'enumerate', 'eval', 'exec', 'exit', 'filter',
    'float', 'format', 'frozenset', 'getattr', 'globals', 'hasattr', 'hash',
    'help', 'hex', 'id', 'input', 'int', 'isinstance', 'issubclass', 'iter',
    'len', 'list', 'locals', 'map', 'max', 'memoryview', 'min', 'next', 'object',
    'oct', 'open', 'ord', 'pow', 'print', 'property', 'quit', 'range', 'repr',
    'reversed', 'round', 'set', 'setattr', 'slice', 'sorted', 'staticmethod',
    'str', 'sum', 'super', 'tuple', 'type', 'vars', 'zip',
]);

/** Python 内置类型（类型标注上下文中按青绿类型色显示） */
const PY_TYPE_BUILTINS = new Set([
    'str', 'int', 'float', 'bool', 'bytes', 'bytearray', 'list', 'dict',
    'set', 'frozenset', 'tuple', 'complex', 'object', 'type', 'None',
]);

function scanPython(source, opts) {
    const em = createEmitter();
    const expressionMode = Boolean(opts && opts.expressionMode);
    const n = source.length;
    let i = 0;

    while (i < n) {
        const c = source[i];

        // 注释（保留换行）
        if (c === '#') {
            let j = i;
            while (j < n && source[j] !== '\n') j += 1;
            em.token('token-comment', source.slice(i, j));
            i = j;
            continue;
        }

        // 字符串（含 f/r/b/u 前缀）
        const prefixMatch = /^([rRbBuUfF]{1,2})(?=['"])/.exec(source.slice(i, i + 3));
        if (prefixMatch || c === '"' || c === "'") {
            const prefix = prefixMatch ? prefixMatch[1] : '';
            if (prefix) {
                const isF = /[fF]/.test(prefix);
                // 前缀本身也属于字符串，但 f 前缀可单色（VSCode 中前缀与字符串同色）
                em.token('token-string', prefix);
                i += prefix.length;
            }
            const quote = source[i];
            const triple = quote.repeat(3);
            if (source.startsWith(triple, i)) {
                em.token('token-string', triple);
                const start = i + 3;
                let j = start;
                let found = false;
                while (j < n) {
                    const idx = source.indexOf(triple, j);
                    if (idx === -1) break;
                    em.token('token-string', source.slice(start, idx));
                    em.token('token-string', triple);
                    i = idx + 3;
                    found = true;
                    break;
                }
                if (!found) {
                    em.token('token-string', source.slice(start, n));
                    return em.toString();
                }
                continue;
            }
            const isF = /[fF]/.test(prefix);
            // 判断是否为 dict 字面量键：非 f-string、前文为 { , 或空白、字符串后紧跟冒号
            const beforeTrim = source.slice(0, i).replace(/\s+$/, '');
            const prevCh = beforeTrim[beforeTrim.length - 1];
            const afterIdx = scanStringEndIndex(source, i);
            const afterSlice = source.slice(afterIdx).replace(/^\s*/, '');
            const isDictKey = !isF && (prevCh === '{' || prevCh === ',' || prevCh === '(' || prevCh === '[') && afterSlice[0] === ':';
            if (isDictKey) {
                // 手动扫描字符串输出 token-property（浅蓝），不经过 scanStringRegion
                em.token('token-property', source.slice(i, afterIdx));
                i = afterIdx;
                continue;
            }
            const end = scanStringRegion(em, source, i, {
                interpolate: isF ? (inner) => scanPython(inner, { expressionMode: true }) : undefined,
            });
            i = end;
            continue;
        }

        // 数字
        const numMatch = /^(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?[jJ]?)/.exec(source.slice(i));
        if (numMatch) {
            em.token('token-number', numMatch[0]);
            i += numMatch[0].length;
            continue;
        }

        // 装饰器：@ 与装饰器名统一为 token-decorator（对齐 VSCode 黄色装饰器色）
        if (c === '@' && isIdentStart(source[i + 1])) {
            let j = i + 1;
            while (j < n && (isIdentPart(source[j]) || source[j] === '.')) j += 1;
            em.token('token-decorator', source.slice(i, j));
            i = j;
            continue;
        }

        // 标识符
        if (isIdentStart(c)) {
            let j = i;
            while (j < n && isIdentPart(source[j])) j += 1;
            const word = source.slice(i, j);
            const next = source.slice(j).replace(/^\s*/, '');
            const nextCh = next[0];

            if (PY_KEYWORDS.has(word)) {
                em.token('token-keyword', word);
                // def / class / import / from 后接的名称单独着色
                i = j;
                if (word === 'def' || word === 'class') {
                    // def / class 后的名称与形参
                    const restAfter = source.slice(j);
                    const nameMatch = /^\s*([A-Za-z_]\w*)/.exec(restAfter);
                    if (nameMatch) {
                        const name = nameMatch[1];
                        const nameCls = word === 'class' ? 'token-class' : 'token-function';
                        // 保留 keyword 与名称之间的空白（nameMatch[0] 含前导空白）
                        em.text(nameMatch[0].slice(0, nameMatch[0].length - name.length));
                        em.token(nameCls, name);
                        i = j + nameMatch[0].length;
                        // def 形参高亮 + 返回类型标注
                        if (word === 'def') {
                            const parenMatch = /^(\s*)(\()/.exec(source.slice(i));
                            if (parenMatch) {
                                em.text(parenMatch[1]);
                                em.token('token-punctuation', '(');
                                i += parenMatch[0].length;
                                // 扫描括号内参数直到匹配的 )
                                let depth = 1;
                                let paramStart = i;
                                while (i < n && depth > 0) {
                                    if (source[i] === '(') depth += 1;
                                    else if (source[i] === ')') depth -= 1;
                                    if (depth === 0) break;
                                    i += 1;
                                }
                                const signature = source.slice(paramStart, i);
                                em.raw(scanPythonSignature(signature));
                                if (source[i] === ')') {
                                    em.token('token-punctuation', ')');
                                    i += 1;
                                }
                                // 返回类型标注 -> T
                                const retMatch = /^(\s*)(->)(\s*)/.exec(source.slice(i));
                                if (retMatch) {
                                    em.text(retMatch[1]);
                                    em.token('token-operator', '->');
                                    em.text(retMatch[3]);
                                    i += retMatch[0].length;
                                    // 扫描返回类型表达式（直到行尾或 :，支持泛型）
                                    let retStart = i;
                                    while (i < n && source[i] !== '\n' && source[i] !== ':') i += 1;
                                    const retType = source.slice(retStart, i).trim();
                                    if (retType) {
                                        em.raw(scanPythonTypeAnnotation(retType));
                                    }
                                }
                            }
                        }
                    }
                } else if (word === 'import') {
                    // import 模块名（浅蓝）
                    const rest = source.slice(j);
                    const modMatch = /^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)/.exec(rest);
                    if (modMatch) {
                        // 保留 import 与模块名之间的空白
                        em.text(modMatch[0].slice(0, modMatch[0].length - modMatch[1].length));
                        em.token('token-import', modMatch[1]);
                        i = j + modMatch[0].length;
                    }
                } else if (word === 'from') {
                    const rest = source.slice(j);
                    const modMatch = /^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)/.exec(rest);
                    if (modMatch) {
                        // 保留 from 与模块名之间的空白
                        em.text(modMatch[0].slice(0, modMatch[0].length - modMatch[1].length));
                        em.token('token-import', modMatch[1]);
                        i = j + modMatch[0].length;
                        // import 后的名称
                        const impMatch = /^\s*import\s+/.exec(source.slice(i));
                        if (impMatch) {
                            em.text(impMatch[0]);
                            i += impMatch[0].length;
                            const namesStr = source.slice(i);
                            const namesEnd = namesStr.search(/[\n]/);
                            const names = (namesEnd === -1 ? namesStr : namesStr.slice(0, namesEnd)).trim();
                            em.raw(scanPythonImportNames(names));
                            i += (namesEnd === -1 ? namesStr.length : namesEnd);
                        }
                    }
                }
                continue;
            }

            if (PY_BUILTINS.has(word)) {
                em.token('token-builtin', word);
                i = j;
                continue;
            }

            if (word === 'self' || word === 'cls') {
                em.token('token-parameter', word);
                i = j;
                continue;
            }

            // 函数调用内的关键字参数 name= → 浅蓝参数色
            const prevTrimKw = source.slice(0, i).replace(/\s+$/, '');
            const prevChKw = prevTrimKw[prevTrimKw.length - 1];
            const parenCountKw = (prevTrimKw.match(/\(/g) || []).length - (prevTrimKw.match(/\)/g) || []).length;
            if (parenCountKw > 0 && nextCh === '=' && (prevChKw === '(' || prevChKw === ',' || prevChKw === ' ' || prevChKw === '\t')) {
                em.token('token-parameter', word);
                i = j;
                continue;
            }
            // lambda 参数（lambda x: expr 中 x / lambda x, y: 中 y）→ 浅蓝参数色
            const lambdaPrefix = source.slice(0, i).replace(/\s+$/, '');
            if (nextCh === ':' && (
                /lambda\s*$/.test(lambdaPrefix) ||
                /lambda\s+[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*\s*,\s*$/.test(lambdaPrefix)
            )) {
                em.token('token-parameter', word);
                i = j;
                continue;
            }
            // 列表/生成器推导式变量（for e in / for i, m in 中 for 后或逗号后的变量）→ 浅蓝参数色
            const compPrefix = source.slice(0, i).replace(/\s+$/, '');
            const isAfterFor = /(?:^|[\[;,(\s])\s*for\s*$/.test(compPrefix);
            const isAfterCommaInFor = /,\s*$/.test(compPrefix) &&
                /(?:^|[\[;,(\s])\s*for\s+[A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*\s*$/.test(compPrefix.replace(/,\s*$/, ''));
            if (isAfterFor || isAfterCommaInFor) {
                em.token('token-parameter', word);
                i = j;
                continue;
            }
            // 类型标注上下文中的类型名（Dict/List/Optional 大写类 + str/bool 等内置类型，
            // 对齐 VSCode 青绿类型色 #4EC9B0）
            const prevTrimType = source.slice(0, i).replace(/\s+$/, '');
            const prevChType = prevTrimType[prevTrimType.length - 1];
            if ((/^[A-Z]/.test(word) && (prevChType === ':' || prevChType === '[' || prevChType === ',' || prevChType === '(')) ||
                (PY_TYPE_BUILTINS.has(word) && (prevChType === ':' || prevChType === '[' || prevChType === ','))) {
                em.token('token-class', word);
                i = j;
                continue;
            }
            // 类实例化 Name(（首字母大写视为类）——须在通用函数调用判定之前，
            // 否则 Foo(...) 会被误染为函数色，与注释意图不符
            if (/^[A-Z]/.test(word) && nextCh === '(') {
                em.token('token-class', word);
                i = j;
                continue;
            }
            // 函数调用 name(
            if (nextCh === '(') {
                em.token('token-function', word);
                i = j;
                continue;
            }
            // 表达式模式（f-string 插值等）下，裸标识符视为变量（浅蓝）
            if (expressionMode) {
                em.token('token-parameter', word);
            } else {
                em.text(word);
            }
            i = j;
            continue;
        }

        // 属性访问 .name（后跟括号 ( 为实例方法调用 → 函数色；否则属性浅蓝）
        if (c === '.' && isIdentStart(source[i + 1])) {
            em.token('token-punctuation', '.');
            let j = i + 1;
            while (j < n && isIdentPart(source[j])) j += 1;
            const afterDot = source.slice(j).replace(/^\s*/, '');
            if (afterDot[0] === '(') {
                em.token('token-function', source.slice(i + 1, j));
            } else {
                em.token('token-property', source.slice(i + 1, j));
            }
            i = j;
            continue;
        }

        // 括号 / 逗号 / 冒号等标点
        if ('()[]{}:,.'.includes(c)) {
            em.token('token-punctuation', c);
            i += 1;
            continue;
        }

        em.text(c);
        i += 1;
    }

    return em.toString();
}

/** 扫描 def 签名中的形参与类型标注（形参浅蓝、类型标注青绿类型色） */
function scanPythonSignature(signature) {
    const em = createEmitter();
    const n = signature.length;
    let i = 0;
    let inTypeAnnotation = false; // 是否处于 : 之后的类型标注区域
    while (i < n) {
        const c = signature[i];
        if (isIdentStart(c) || c === '*') {
            let j = i;
            if (c === '*') {
                j += 1;
                if (signature[j] === '*') j += 1;
            }
            while (j < n && isIdentPart(signature[j])) j += 1;
            const name = signature.slice(i, j);
            const rest = signature.slice(j).replace(/^\s*/, '');
            if (inTypeAnnotation) {
                // 类型标注区域：类型名（大写/已知类型/内置类型）→ 青绿
                em.token(typeNameClass(name), name);
            } else if (/^[:=,)]/.test(rest)) {
                // 形参名 → 浅蓝参数色
                em.token('token-parameter', name);
            } else {
                em.raw(scanPython(name));
            }
            i = j;
            continue;
        }
        if (c === ':') {
            em.token('token-punctuation', ':');
            // 冒号后若为空格+类型名，进入类型标注区域；但 :: 或切片冒号除外
            const afterColon = signature.slice(i + 1).replace(/^\s+/, '');
            inTypeAnnotation = /^[A-Za-z_*\[]/.test(afterColon);
            i += 1;
            continue;
        }
        if ('()=, '.includes(c)) {
            em.token('token-punctuation', c);
            if (c === ')') inTypeAnnotation = false;
            if (c === ',') inTypeAnnotation = false; // 逗号后回到形参
            i += 1;
            continue;
        }
        if (c === '[' || c === ']') {
            em.token('token-punctuation', c);
            i += 1;
            continue;
        }
        // 返回类型标注箭头 ->
        if (c === '-' && signature[i + 1] === '>') {
            em.token('token-operator', '->');
            i += 2;
            inTypeAnnotation = true;
            continue;
        }
        em.text(c);
        i += 1;
    }
    return em.toString();
}

/** 判定类型标注中名字的类别：大写/已知类型/内置类型 → 青绿类型色，否则浅蓝 */
function typeNameClass(name) {
    return (PY_KNOWN_TYPES.has(name) || PY_TYPE_BUILTINS.has(name) || /^[A-Z]/.test(name)) ? 'token-class' : 'token-parameter';
}

/** 扫描返回类型/类型标注表达式（如 Dict[str, bool]、Optional[float]、tuple[int, str]）
 *  类型名/内置类型→青绿，标点→标点色，泛型括号内递归。 */
function scanPythonTypeAnnotation(expr) {
    const em = createEmitter();
    const n = expr.length;
    let i = 0;
    while (i < n) {
        const c = expr[i];
        if (isIdentStart(c)) {
            let j = i;
            while (j < n && isIdentPart(expr[j])) j += 1;
            const name = expr.slice(i, j);
            em.token(typeNameClass(name), name);
            i = j;
            continue;
        }
        if (c === '[' || c === ']' || c === ',' || c === '(' || c === ')' || c === '.') {
            em.token('token-punctuation', c);
            i += 1;
            continue;
        }
        if (c === ' ' || c === '\t') {
            em.text(c);
            i += 1;
            continue;
        }
        em.text(c);
        i += 1;
    }
    return em.toString();
}

/** 常见 Python 类型/类名（导入时按类型色高亮；涵盖 typing/datetime/collections 等） */
const PY_KNOWN_TYPES = new Set([
    'Any', 'Callable', 'Dict', 'List', 'Optional', 'Sequence', 'Set', 'Tuple',
    'Type', 'Union', 'Iterable', 'Iterator', 'Mapping', 'NamedTuple', 'DefaultDict',
    'Counter', 'Deque', 'OrderedDict', 'ChainMap', 'Awaitable', 'Coroutine',
    'AsyncIterator', 'AsyncIterable', 'Pattern', 'Match', 'Literal', 'Final',
    'ClassVar', 'TypeVar', 'Generic', 'Protocol', 'TypedDict', 'Annotated',
    'datetime', 'time', 'date', 'timedelta', 'timezone', 'tzinfo',
    'BaseModel', 'Enum', 'IntEnum', 'Path', 'UUID', 'Decimal', 'Fraction',
]);

/** 判定导入名的着色类别：已知类型/首字母大写→类型色（青绿），否则为变量/函数（浅蓝） */
function importNameClass(name) {
    return (PY_KNOWN_TYPES.has(name) || /^[A-Z]/.test(name)) ? 'token-class' : 'token-parameter';
}

/** 扫描 from X import a, b 的导入名列表（保留逗号与空白） */
function scanPythonImportNames(names) {
    const em = createEmitter();
    // 按逗号切分但保留每个部分的前导/尾随空白
    const parts = names.split(',');
    parts.forEach((part, idx) => {
        if (idx > 0) em.token('token-punctuation', ',');
        const trimmed = part.trim();
        // 输出名称前的空白（除第一个元素外，逗号后的空白保留）
        em.text(part.slice(0, part.indexOf(trimmed)));
        if (!trimmed) return;
        if (trimmed.startsWith('*')) {
            em.token('token-operator', '*');
            return;
        }
        if (trimmed.startsWith('(')) {
            em.text(trimmed);
            return;
        }
        // 导入名分层着色（对齐 VSCode）：
        // - 首字母大写的类型名（Dict/List/Optional 等）→ 青绿类型色 token-class
        // - 小写函数/变量（dataclass/field 等）→ 浅蓝变量色 token-parameter
        // - 支持别名 import X as Y（保留 as 前后空白）
        const pieces = trimmed.split(/\s+/);
        if (pieces[0] === 'as') {
            em.token('token-keyword', 'as');
            if (pieces[1]) em.token(importNameClass(pieces[1]), pieces[1]);
        } else if (pieces.includes('as')) {
            const asIdx = pieces.indexOf('as');
            const orig = pieces.slice(0, asIdx).join(' ');
            const alias = pieces[asIdx + 1];
            // 按单词边界定位 as 分隔符，避免原名（如 dataclass）内含 as 子串时
            // indexOf 命中错误位置，导致输出文本与源码不一致
            const asMatch = /\bas\b/.exec(trimmed);
            const asStart = asMatch ? asMatch.index : trimmed.indexOf('as');
            em.token(importNameClass(orig), orig);
            em.text(trimmed.slice(orig.length, asStart));       // ' as ' 中的前导空白
            em.token('token-keyword', 'as');
            if (alias) {
                em.text(trimmed.slice(asStart + 2, asStart + 2 + (trimmed.slice(asStart + 2).indexOf(alias))));
                em.token(importNameClass(alias), alias);
            }
        } else {
            em.token(importNameClass(trimmed), trimmed);
        }
        // 名称后的空白
        em.text(part.slice(part.indexOf(trimmed) + trimmed.length));
    });
    return em.toString();
}

/* ==========================================================================
   3. JavaScript / TypeScript 扫描器
   ========================================================================== */

const JS_KEYWORDS = new Set([
    'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue',
    'debugger', 'default', 'delete', 'do', 'else', 'export', 'extends', 'finally',
    'for', 'from', 'function', 'get', 'if', 'import', 'in', 'instanceof', 'let',
    'new', 'of', 'return', 'set', 'static', 'super', 'switch', 'this', 'throw',
    'try', 'typeof', 'var', 'void', 'while', 'with', 'yield',
]);

const JS_BOOLEANS = new Set(['true', 'false', 'null', 'undefined', 'NaN', 'Infinity']);

/**
 * 正则字面量微观词法高亮：锚点/转义/量词多色语义着色，对齐 VSCode Dark Modern。
 */
function scanRegexLiteral(regex) {
    const em = createEmitter();
    const n = regex.length;
    let i = 0;
    em.token('token-regex', '/');
    i += 1;
    while (i < n && regex[i] !== '/') {
        const c = regex[i];
        if (c === '\\' && i + 1 < n && regex[i + 1] !== '/') {
            const escMatch = /^\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|u\{[0-9a-fA-F]+\}|[\s\S])/.exec(regex.slice(i));
            if (escMatch) { em.token('token-constant', escMatch[0]); i += escMatch[0].length; continue; }
        }
        if (c === '^' || c === '$') { em.token('token-constant', c); i += 1; continue; }
        if (c === '{') {
            const quantMatch = /^\{(\d+)(,(\d+)?)?\}/.exec(regex.slice(i));
            if (quantMatch) {
                em.token('token-regex', '{');
                em.token('token-number', quantMatch[1]);
                if (quantMatch[2]) {
                    em.token('token-regex', ',');
                    if (quantMatch[3]) em.token('token-number', quantMatch[3]);
                }
                em.token('token-regex', '}');
                i += quantMatch[0].length;
                continue;
            }
        }
        em.token('token-regex', c);
        i += 1;
    }
    if (i < n && regex[i] === '/') { em.token('token-regex', '/'); i += 1; }
    if (i < n) em.token('token-regex', regex.slice(i));
    return em.toString();
}

/** 扫描 JS/TS（供 JSX 复用；reuseAsJsx 时启用 JSX 标签规则） */
function scanJavaScript(source, opts) {
    const em = createEmitter();
    const n = source.length;
    const jsxMode = Boolean(opts && opts.jsxMode);
    let i = 0;

    while (i < n) {
        const c = source[i];

        // 行注释
        if (c === '/' && source[i + 1] === '/') {
            let j = i + 2;
            while (j < n && source[j] !== '\n') j += 1;
            em.token('token-comment', source.slice(i, j));
            i = j;
            continue;
        }
        // 块注释
        if (c === '/' && source[i + 1] === '*') {
            const end = source.indexOf('*/', i + 2);
            const j = end === -1 ? n : end + 2;
            em.token('token-comment', source.slice(i, j));
            i = j;
            continue;
        }

        // 字符串 / 模板字符串
        if (c === '"' || c === "'" || c === '`') {
            const end = scanStringRegion(em, source, i, {
                interpolate: c === '`' ? (inner) => scanJavaScript(inner) : undefined,
            });
            i = end;
            continue;
        }

        // 数字
        const numMatch = /^(?:0[xX][0-9a-fA-F]+|0[bB][01]+|0[oO][0-7]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(source.slice(i));
        if (numMatch) {
            em.token('token-number', numMatch[0]);
            i += numMatch[0].length;
            continue;
        }

        // 正则字面量（宽松：赋值/参数/括号上下文）——内部微观词法高亮
        if (c === '/' && /^[=(\s,:[!&|?{]/.test(source[i - 1] || '') && !/^\/[/*]/.test(source.slice(i))) {
            const reMatch = /^\/(?:\\\/|[^/\n])+\/[dgimsuvy]*/.exec(source.slice(i));
            if (reMatch) {
                em.raw(scanRegexLiteral(reMatch[0]));
                i += reMatch[0].length;
                continue;
            }
        }

        // JSX 标签（jsxMode）：支持开标签 <Name 与闭合标签 </Name
        if (jsxMode && c === '<' && /[A-Za-z/]/.test(source[i + 1] || '')) {
            const tagMatch = /^<\/?([A-Za-z][\w.-]*)/.exec(source.slice(i));
            if (tagMatch) {
                const whole = tagMatch[0];
                const close = whole.startsWith('</');
                const name = tagMatch[1];
                const isComponent = /^[A-Z]/.test(name);
                em.token('token-punctuation', close ? '</' : '<');
                em.token(isComponent ? 'token-class' : 'token-tag', name);
                i += whole.length;
                continue;
            }
        }

        // 私有字段 #name
        if (c === '#' && isIdentStart(source[i + 1])) {
            let j = i + 1;
            while (j < n && isIdentPart(source[j])) j += 1;
            em.token('token-property', source.slice(i, j));
            i = j;
            continue;
        }

        // 标识符
        if (isIdentStart(c) || c === '$') {
            let j = i;
            while (j < n && (isIdentPart(source[j]) || source[j] === '$')) j += 1;
            const word = source.slice(i, j);
            const restTrim = source.slice(j).replace(/^\s*/, '');
            const nextCh = restTrim[0];

            if (JS_KEYWORDS.has(word)) {
                em.token('token-keyword', word);
                i = j;
                continue;
            }
            if (JS_BOOLEANS.has(word)) {
                em.token('token-boolean', word);
                i = j;
                continue;
            }
            // import { x, y } 解构导入名（浅蓝变量色，非属性色）
            const prevTrimForImport = source.slice(0, i).replace(/\s+$/, '');
            const inImportBrace = /(^|[^\w])import\s*\{[^}]*$/.test(prevTrimForImport);
            if (inImportBrace) {
                em.token('token-parameter', word);
                i = j;
                continue;
            }
            // 函数调用 / 方法调用
            if (nextCh === '(') {
                em.token('token-function', word);
                i = j;
                continue;
            }
            // 对象键 { key: value } 或 , key: value
            const prevTrim = source.slice(0, i).replace(/\s+$/, '');
            const prevCh = prevTrim[prevTrim.length - 1];
            if (nextCh === ':' && (prevCh === '{' || prevCh === ',')) {
                em.token('token-property', word);
                i = j;
                continue;
            }
            // 箭头函数参数 (a, b = 1) => ...
            if (prevCh === '(' || prevCh === ',' || prevCh === '=') {
                const lookAhead = source.slice(j).replace(/^\s*/, '');
                if (/^[:=,)]/.test(lookAhead)) {
                    // 检查是否是箭头函数上下文（后面有 =>）
                    const toArrow = source.slice(j, source.indexOf('\n', j) === -1 ? n : source.indexOf('\n', j));
                    if (/=>/.test(toArrow.slice(0, 60))) {
                        em.token('token-parameter', word);
                        i = j;
                        continue;
                    }
                }
            }
            // 形参解构花括号内的参数名（constructor({ station, onAlert })）
            // 仅保留解构花括号上下文判定，避免数组字面量 / 函数实参中
            // 逗号后的标识符被误染为参数色
            const prevTrimDestr = source.slice(0, i).replace(/\s+$/, '');
            const inDestrParam = /\(\s*\{[^}]*$/.test(prevTrimDestr);
            if (inDestrParam) {
                em.token('token-parameter', word);
                i = j;
                continue;
            }
            // JSX 标签属性表达式 {expr} 内的裸标识符（浅蓝变量色）
            if (jsxMode && /\{[^}]*$/.test(source.slice(0, i))) {
                em.token('token-parameter', word);
                i = j;
                continue;
            }
            // 成员属性访问前的标识符保持默认
            em.text(word);
            i = j;
            continue;
        }

        // 成员访问 .prop（后跟 ( 为方法调用 → 函数色；否则属性浅蓝）
        if (c === '.' && isIdentStart(source[i + 1])) {
            em.token('token-punctuation', '.');
            let j = i + 1;
            while (j < n && (isIdentPart(source[j]) || source[j] === '$')) j += 1;
            const afterMember = source.slice(j).replace(/^\s*/, '');
            if (afterMember[0] === '(') {
                em.token('token-function', source.slice(i + 1, j));
            } else {
                em.token('token-property', source.slice(i + 1, j));
            }
            i = j;
            continue;
        }

        // 标点 / 操作符
        if ('()[]{}:;,?.'.includes(c)) {
            em.token('token-punctuation', c);
            i += 1;
            continue;
        }
        if ('+-*/%=<>&|!~^'.includes(c)) {
            // 多字符操作符
            const opMatch = /^(?:===|!==|>>>|<<=|>>=|=>|\*\*|\+\+|--|&&|\|\||\?\?|\?\?=|\?\s*:|[+\-*/%<>=!&|^~]=?)/.exec(source.slice(i));
            if (opMatch) {
                em.token('token-operator', opMatch[0]);
                i += opMatch[0].length;
                continue;
            }
            em.token('token-operator', c);
            i += 1;
            continue;
        }

        em.text(c);
        i += 1;
    }

    return em.toString();
}

/* ==========================================================================
   4. JSX / TSX（基于 JS 内核 + JSX 标签规则）
   ========================================================================== */

function scanJsx(source) {
    // JSX 中标签内属性由 JS 扫描器负责识别（attr="..."），组件/原生标签区分在此
    return scanJavaScript(source, { jsxMode: true });
}

/* ==========================================================================
   5. CSS 扫描器
   ========================================================================== */

/** CSS 常见属性值枚举/关键字（VSCode 中呈现为浅蓝属性值色 #9CDCFE） */
const CSS_VALUE_KEYWORDS = new Set([
    // 定位与盒模型
    'relative', 'absolute', 'fixed', 'static', 'sticky', 'auto', 'inherit',
    'initial', 'unset', 'none', 'block', 'inline', 'inline-block', 'flex',
    'inline-flex', 'grid', 'inline-grid', 'table', 'table-cell', 'contents',
    // flex/grid 布局
    'row', 'column', 'row-reverse', 'column-reverse', 'wrap', 'nowrap',
    'wrap-reverse', 'start', 'end', 'center', 'space-between', 'space-around',
    'space-evenly', 'stretch', 'baseline', 'flex-start', 'flex-end',
    'self-start', 'self-end', 'normal', 'left', 'right', 'top', 'bottom',
    'both', 'scroll', 'hidden', 'visible', 'clip', 'ellipsis', 'break-all',
    'break-word', 'pre', 'pre-wrap', 'pre-line', 'nowrap',
    // 颜色/背景
    'transparent', 'currentColor', 'currentcolor',
    // 边框
    'solid', 'dashed', 'dotted', 'double', 'groove', 'ridge', 'inset',
    'outset', 'border-box', 'content-box', 'padding-box',
    // 过渡/动画
    'ease', 'ease-in', 'ease-out', 'ease-in-out', 'linear', 'step-start',
    'step-end', 'infinite', 'forwards', 'backwards', 'paused', 'running',
    'alternate', 'alternate-reverse', 'normal',
    // 字体/文本
    'bold', 'bolder', 'lighter', 'italic', 'oblique', 'underline',
    'overline', 'line-through', 'uppercase', 'lowercase', 'capitalize',
    'serif', 'sans-serif', 'monospace', 'cursive', 'fantasy', 'system-ui',
    'small-caps', 'sub', 'super', 'middle', 'baseline', 'text-top',
    'text-bottom', 'justify', 'justify-all', 'match-parent',
    // 常用值
    'pointer', 'default', 'grab', 'grabbing', 'crosshair', 'move', 'wait',
    'help', 'not-allowed', 'zoom-in', 'zoom-out', 'n-resize', 's-resize',
    'e-resize', 'w-resize', 'cover', 'contain', 'repeat', 'no-repeat',
    'space', 'round', 'center', 'fill', 'paint-order', 'respect', 'nonzero',
    'evenodd', 'inside', 'outside', 'open', 'closed', 'separate', 'collapse',
    'revert', 'revert-layer', 'scale-down', 'linear', 'ease', 'both',
]);

/** 判断 CSS 源码中 openIndex 处的 { 是否为块级 at-rule（@media/@supports 等）的块起始。
 *  判定依据：{ 之前最近的非空白字符为 )，且向前找到 @media 等 at-rule 关键字。 */
function isAtRuleBlockStart(source, openIndex) {
    const before = source.slice(0, openIndex).replace(/\s+$/, '');
    if (!before.endsWith(')')) return false;
    const atMatch = /@(?:media|supports|container|layer|document)\b/i.exec(before);
    if (!atMatch) return false;
    // 确认该 at-rule 与 { 之间只有查询表达式（括号/空白/关键字），没有其他规则集的花括号
    const after = before.slice(atMatch.index + atMatch[0].length);
    return /^[\s()a-zA-Z:,\d.%-]*$/.test(after);
}

function scanCss(source) {
    const em = createEmitter();
    const n = source.length;
    let i = 0;
    // 模式栈：栈顶为当前上下文（'selector' 选择器区域 / 'value' 声明区域）。
    // @media 等块级 at-rule 的 { 内部仍是选择器区域，普通规则集 { 内部是声明区域。
    const modeStack = ['selector'];
    const curMode = () => modeStack[modeStack.length - 1];

    while (i < n) {
        const c = source[i];

        // 注释
        if (c === '/' && source[i + 1] === '*') {
            const end = source.indexOf('*/', i + 2);
            const j = end === -1 ? n : end + 2;
            em.token('token-comment', source.slice(i, j));
            i = j;
            continue;
        }

        // 花括号切换状态
        if (c === '{') {
            em.token('token-punctuation', '{');
            if (isAtRuleBlockStart(source, i)) {
                modeStack.push('selector'); // @media 块内仍是选择器区域
            } else {
                modeStack.push('value');    // 普通规则集内是声明区域
            }
            i += 1;
            continue;
        }
        if (c === '}') {
            em.token('token-punctuation', '}');
            if (modeStack.length > 1) modeStack.pop();
            i += 1;
            continue;
        }

        // 字符串
        if (c === '"' || c === "'") {
            i = scanStringRegion(em, source, i);
            continue;
        }

        // 数字 + 单位
        const numMatch = /^(-?\d+(?:\.\d+)?)(%|px|em|rem|ex|ch|vw|vh|vmin|vmax|cm|mm|in|pt|pc|deg|rad|grad|turn|s|ms|fr|dpi|dpcm|dppx)?/.exec(source.slice(i));
        if (numMatch) {
            em.token('token-number', numMatch[1]);
            if (numMatch[2]) em.token('token-unit', numMatch[2]);
            i += numMatch[0].length;
            continue;
        }

        // 十六进制颜色
        if (c === '#') {
            const hex = /^#([0-9a-fA-F]{3,8})\b/.exec(source.slice(i));
            if (hex && curMode() === 'value') {
                em.token('token-string', hex[0]);
                i += hex[0].length;
                continue;
            }
        }

        // at-rule
        if (c === '@') {
            const atMatch = /^@[\w-]+/.exec(source.slice(i));
            if (atMatch) {
                em.token('token-atrule', atMatch[0]);
                i += atMatch[0].length;
                continue;
            }
        }

        // !important / !default / !global 等 SCSS 标记：
        // 先匹配实际标记文本，避免硬编码 !important 吞掉 !default 等标记及相邻字符
        if (c === '!') {
            const bangMatch = /^!important\b|^![a-zA-Z-]+/.exec(source.slice(i));
            if (bangMatch) {
                em.token('token-keyword', bangMatch[0]);
                i += bangMatch[0].length;
                continue;
            }
            em.text(c);
            i += 1;
            continue;
        }

        // 函数调用
        if (isIdentStart(c)) {
            let j = i;
            while (j < n && /[A-Za-z0-9_-]/.test(source[j])) j += 1;
            const word = source.slice(i, j);
            const restTrim = source.slice(j).replace(/^\s*/, '');
            const nextCh = restTrim[0];
            if (nextCh === '(') {
                // var(--x) 或 calc() 等
                if (word === 'var' || word === 'env') {
                    em.token('token-function', word);
                    // 内部变量：只截取到匹配的 )，避免吞掉后续内容
                    const innerFull = source.slice(j);
                    const closeIdx = innerFull.indexOf(')');
                    const innerEnd = closeIdx === -1 ? innerFull.length : closeIdx + 1;
                    const inner = innerFull.slice(0, innerEnd);
                    const varMatch = /^\(\s*(--[\w-]+)/.exec(inner);
                    if (varMatch) {
                        const vStart = varMatch.index + 1;
                        const vEnd = vStart + varMatch[1].length;
                        em.text(inner.slice(0, vStart));
                        em.token('token-variable', varMatch[1]);
                        em.text(inner.slice(vEnd));
                        i = j + inner.length;
                    } else {
                        em.text(inner);
                        i = j + inner.length;
                    }
                } else {
                    em.token('token-function', word);
                    i = j;
                }
                continue;
            }
            // 声明内的属性名（冒号前）
            if (curMode() === 'value' && nextCh === ':') {
                if (word.startsWith('--')) {
                    em.token('token-variable', word);
                } else {
                    em.token('token-property', word);
                }
                i = j;
                continue;
            }
            // 选择器区域
            if (curMode() === 'selector') {
                // @media / @supports 查询中的逻辑关键字
                if (word === 'and' || word === 'or' || word === 'not' || word === 'only') {
                    em.token('token-keyword', word);
                    i = j;
                    continue;
                }
                // @media 括号内的媒体特性名（max-width: 720px）→ 属性色
                const prevNonSpaceCss = source.slice(0, i).replace(/\s+$/, '');
                const prevNonSpaceCh = prevNonSpaceCss[prevNonSpaceCss.length - 1];
                if (nextCh === ':' && (prevNonSpaceCh === '(' || prevNonSpaceCh === ',')) {
                    em.token('token-property', word);
                    i = j;
                    continue;
                }
                em.token('token-selector', word);
                i = j;
                continue;
            }
            // 值区域：识别 CSS 关键字/枚举值（浅蓝属性值色）
            if (curMode() === 'value' && CSS_VALUE_KEYWORDS.has(word)) {
                em.token('token-value', word);
                i = j;
                continue;
            }
            // 值区域普通词（默认文本）
            em.text(word);
            i = j;
            continue;
        }

        // 选择器符号
        if (c === '.') {
            const clsMatch = /^\.[A-Za-z_][\w-]*/.exec(source.slice(i));
            if (clsMatch && curMode() === 'selector') {
                em.token('token-punctuation', '.');
                em.token('token-selector', clsMatch[0].slice(1));
                i += clsMatch[0].length;
                continue;
            }
        }
        if (c === '#' && curMode() === 'selector') {
            const idMatch = /^#[A-Za-z_][\w-]*/.exec(source.slice(i));
            if (idMatch) {
                em.token('token-punctuation', '#');
                em.token('token-selector', idMatch[0].slice(1));
                i += idMatch[0].length;
                continue;
            }
        }
        if (c === ':' && curMode() === 'selector') {
            const pseudoMatch = /^:{1,2}[a-zA-Z-]+/.exec(source.slice(i));
            if (pseudoMatch) {
                em.token('token-punctuation', ':'.repeat(pseudoMatch[0].startsWith('::') ? 2 : 1));
                em.token('token-selector', pseudoMatch[0].replace(/^:+/, ''));
                i += pseudoMatch[0].length;
                // 函数式伪类（nth-child / not / lang 等）：括号内部按语义微着色
                // 注意：伪类参数使用圆括号，不能用 findMatchingBrace（其仅匹配花括号）
                if (source[i] === '(') {
                    const parenEnd = source.indexOf(')', i + 1);
                    const innerEndIdx = parenEnd === -1 ? n : parenEnd;
                    em.token('token-punctuation', '(');
                    const inner = source.slice(i + 1, innerEndIdx);
                    em.raw(scanCssPseudoArg(inner));
                    if (innerEndIdx < n) em.token('token-punctuation', ')');
                    i = innerEndIdx === n ? n : innerEndIdx + 1;
                    continue;
                }
                continue;
            }
        }
        if ((c === '>' || c === '+' || c === '~') && curMode() === 'selector') {
            em.token('token-operator', c);
            i += 1;
            continue;
        }
        if (c === ',' && curMode() === 'selector') {
            em.token('token-punctuation', ',');
            i += 1;
            continue;
        }
        if (c === ';') {
            em.token('token-punctuation', ';');
            i += 1;
            continue;
        }
        if (c === ':') {
            em.token('token-punctuation', ':');
            i += 1;
            continue;
        }

        em.text(c);
        i += 1;
    }

    return em.toString();
}

/** 扫描函数式伪类括号内的参数（如 nth-child 的 2n+1 / even / odd） */
function scanCssPseudoArg(arg) {
    const em = createEmitter();
    const n = arg.length;
    let i = 0;
    while (i < n) {
        const c = arg[i];
        if (isDigit(c)) {
            const numMatch = /^\d+/.exec(arg.slice(i));
            if (numMatch) {
                em.token('token-number', numMatch[0]);
                i += numMatch[0].length;
                continue;
            }
        }
        if (c === '+' || c === '-' || c === '*') {
            em.token('token-operator', c);
            i += 1;
            continue;
        }
        if (isIdentStart(c)) {
            let j = i;
            while (j < n && isIdentPart(arg[j])) j += 1;
            const word = arg.slice(i, j);
            // nth-child 表达式中的 n / even / odd → 语法关键字（紫粉/粉褐色）
            if (word === 'n' || word === 'even' || word === 'odd') {
                em.token('token-keyword', word);
            } else {
                em.token('token-value', word);
            }
            i = j;
            continue;
        }
        em.text(c);
        i += 1;
    }
    return em.toString();
}

/* ==========================================================================
   6. HTML / XML 扫描器
   ========================================================================== */

function scanHtml(source) {
    const em = createEmitter();
    const n = source.length;
    let i = 0;

    while (i < n) {
        const c = source[i];

        // 注释
        if (c === '<' && source.startsWith('<!--', i)) {
            const end = source.indexOf('-->', i + 4);
            const j = end === -1 ? n : end + 3;
            em.token('token-comment', source.slice(i, j));
            i = j;
            continue;
        }

        // DOCTYPE
        if (c === '<' && /^<!DOCTYPE\b/i.test(source.slice(i))) {
            const end = source.indexOf('>', i);
            const j = end === -1 ? n : end + 1;
            const tagText = source.slice(i, j);
            em.token('token-punctuation', '<');
            em.token('token-keyword', '!DOCTYPE');
            const rest = tagText.slice('<!DOCTYPE'.length, -1);
            // 剩余部分：html 等关键字
            const restTrim = rest.trim();
            if (restTrim) {
                em.token('token-attribute', restTrim);
            }
            em.token('token-punctuation', '>');
            i = j;
            continue;
        }

        // 标签
        if (c === '<' && /[a-zA-Z/!]/.test(source[i + 1] || '')) {
            const end = source.indexOf('>', i);
            if (end !== -1) {
                const tagText = source.slice(i, end + 1);
                em.raw(scanHtmlTag(tagText));
                i = end + 1;
                continue;
            }
        }

        em.text(c);
        i += 1;
    }

    return em.toString();
}

/** 扫描单个 HTML 标签（含属性） */
function scanHtmlTag(tagText) {
    const em = createEmitter();
    const n = tagText.length;
    let i = 0;

    // 开标签 < / 结束 </
    if (tagText.startsWith('</')) {
        em.token('token-punctuation', '</');
        i = 2;
    } else {
        em.token('token-punctuation', '<');
        i = 1;
    }
    // 标签名
    const nameMatch = /^[a-zA-Z][\w-]*/.exec(tagText.slice(i));
    if (nameMatch) {
        em.token('token-tag', nameMatch[0]);
        i += nameMatch[0].length;
    }

    // 属性与内容
    while (i < n) {
        const c = tagText[i];
        if (c === '>') {
            em.token('token-punctuation', '>');
            i += 1;
            continue;
        }
        if (c === '/') {
            em.token('token-punctuation', '/');
            i += 1;
            continue;
        }
        if (isSpace(c)) {
            em.text(c);
            i += 1;
            continue;
        }
        // 属性名
        if (isIdentStart(c)) {
            const attrMatch = /^[a-zA-Z][\w:-]*/.exec(tagText.slice(i));
            if (attrMatch) {
                em.token('token-attribute', attrMatch[0]);
                i += attrMatch[0].length;
                continue;
            }
        }
        // = 号
        if (c === '=') {
            em.token('token-punctuation', '=');
            i += 1;
            continue;
        }
        // 属性值
        if (c === '"' || c === "'") {
            i = scanStringRegion(em, tagText, i);
            continue;
        }
        em.text(c);
        i += 1;
    }

    return em.toString();
}

/* ==========================================================================
   7. Bash / Shell 扫描器
   ========================================================================== */

const BASH_KEYWORDS = new Set([
    'if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'until', 'do', 'done',
    'case', 'esac', 'function', 'in', 'select', 'time', 'local', 'export',
    'declare', 'readonly', 'unset', 'shift', 'source', 'alias', 'trap',
    'return', 'break', 'continue', 'exit', 'true', 'false', 'test', 'echo',
    'printf', 'read', 'cd', 'pwd', 'set',
]);

/** Bash 常见外部命令（高亮为命令色 token-command，与普通参数区分） */
const BASH_COMMON_COMMANDS = new Set([
    'nohup', 'pkill', 'kill', 'killall', 'sleep', 'wait', 'exec', 'eval',
    'type', 'which', 'whereis', 'find', 'grep', 'egrep', 'fgrep', 'sed',
    'awk', 'cat', 'tail', 'head', 'less', 'more', 'sort', 'uniq', 'wc',
    'cut', 'tr', 'paste', 'join', 'xargs', 'tee', 'touch', 'mkdir', 'rmdir',
    'rm', 'cp', 'mv', 'ln', 'chmod', 'chown', 'chgrp', 'ls', 'dir', 'df',
    'du', 'mount', 'umount', 'ps', 'top', 'htop', 'free', 'uptime', 'uname',
    'hostname', 'date', 'cal', 'tar', 'gzip', 'gunzip', 'zip', 'unzip',
    'curl', 'wget', 'ping', 'nc', 'ncat', 'ssh', 'scp', 'sftp', 'rsync',
    'git', 'svn', 'hg', 'make', 'cmake', 'gcc', 'g++', 'clang', 'python',
    'python3', 'pip', 'pip3', 'npm', 'node', 'yarn', 'pnpm', 'deno', 'bun',
    'docker', 'kubectl', 'systemctl', 'service', 'journalctl', 'crontab',
    'cron', 'at', 'sudo', 'su', 'useradd', 'usermod', 'userdel', 'passwd',
    'groupadd', 'id', 'whoami', 'who', 'w', 'users', 'groups', 'env',
    'export', 'unset', 'hash', 'help', 'jobs', 'fg', 'bg', 'disown',
    'readlink', 'realpath', 'dirname', 'basename', 'file', 'stat', 'lsof',
    'ss', 'ip', 'ifconfig', 'route', 'netstat', 'nslookup', 'dig', 'host',
]);

function scanBash(source) {
    const em = createEmitter();
    const n = source.length;
    let i = 0;
    let lineStart = true; // 行首标志，用于命令名 / 赋值判断
    let inCase = false;   // 是否位于 case ... esac 分支区域

    while (i < n) {
        const c = source[i];

        // 换行
        if (c === '\n') {
            em.text('\n');
            lineStart = true;
            i += 1;
            continue;
        }

        // 行首空白
        if (lineStart && (c === ' ' || c === '\t')) {
            let j = i;
            while (j < n && (source[j] === ' ' || source[j] === '\t')) j += 1;
            em.text(source.slice(i, j));
            i = j;
            continue;
        }

        // shebang 首行
        if (i === 0 && source.startsWith('#!', i)) {
            let j = i;
            while (j < n && source[j] !== '\n') j += 1;
            em.token('token-comment', source.slice(i, j));
            i = j;
            continue;
        }

        // 注释（# 前为空白或行首；保留换行）
        if (c === '#' && (lineStart || isSpace(source[i - 1]))) {
            let j = i;
            while (j < n && source[j] !== '\n') j += 1;
            em.token('token-comment', source.slice(i, j));
            i = j;
            continue;
        }

        // 行首命令 / 赋值
        if (lineStart && (isIdentStart(c) || c === '.')) {
            const cmdMatch = /^([A-Za-z_][A-Za-z0-9_.-]*|\.?\/[\w./-]+)/.exec(source.slice(i));
            if (cmdMatch) {
                const cmd = cmdMatch[1];
                const after = source.slice(i + cmd.length).replace(/^\s*/, '');
                if (after[0] === '=') {
                    // 赋值：左值为变量色
                    em.token('token-variable', cmd);
                    i += cmd.length;
                    lineStart = false;
                    continue;
                }
                // 关键字优先
                if (BASH_KEYWORDS.has(cmd)) {
                    em.token('token-keyword', cmd);
                    if (cmd === 'case') inCase = true;
                    if (cmd === 'esac') inCase = false;
                } else if (inCase && /^[^*]*\)\s*/.test(source.slice(i + cmd.length).replace(/^\s*/, ''))) {
                    // case 分支标签（start) / stop) 等）→ 控制关键字色
                    em.token('token-keyword', cmd);
                } else {
                    em.token('token-command', cmd);
                }
                i += cmd.length;
                lineStart = false;
                continue;
            }
        }

        // case 分支标签 *（*) 形式）
        if (lineStart && c === '*' && source[i + 1] === ')') {
            em.token('token-keyword', '*');
            em.token('token-punctuation', ')');
            i += 2;
            lineStart = false;
            continue;
        }

        // 行首关键字（如 if/for/case 在行首无空格）
        if (lineStart && BASH_KEYWORDS.has(source.slice(i).match(/^[A-Za-z]+/)?.[0] || '')) {
            const kwMatch = /^[A-Za-z]+/.exec(source.slice(i));
            const kw = kwMatch[0];
            em.token('token-keyword', kw);
            if (kw === 'case') inCase = true;
            if (kw === 'esac') inCase = false;
            i += kwMatch[0].length;
            lineStart = false;
            continue;
        }

        // 变量 $VAR / ${VAR} / $1
        if (c === '$') {
            const varMatch = /^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|^\$\d/.exec(source.slice(i));
            if (varMatch) {
                em.token('token-variable', varMatch[0]);
                i += varMatch[0].length;
                continue;
            }
            // $() 命令替换
            if (source[i + 1] === '(') {
                const end = source.indexOf(')', i + 2);
                em.token('token-operator', '$(');
                if (end !== -1) {
                    em.raw(scanBash(source.slice(i + 2, end)));
                    em.token('token-operator', ')');
                    i = end + 1;
                } else {
                    i += 2;
                }
                continue;
            }
        }

        // 字符串
        if (c === '"' || c === "'") {
            i = scanStringRegion(em, source, i);
            continue;
        }

        // 标志位
        if (c === '-' && /^--?[A-Za-z0-9]/.test(source.slice(i))) {
            const flagMatch = /^-[A-Za-z0-9](?:[A-Za-z0-9-]*)?|^--[A-Za-z0-9][A-Za-z0-9-]*/.exec(source.slice(i));
            if (flagMatch) {
                em.token('token-flag', flagMatch[0]);
                i += flagMatch[0].length;
                lineStart = false;
                continue;
            }
        }

        // 数字
        if (isDigit(c)) {
            const numMatch = /^\d+(?:\.\d+)?/.exec(source.slice(i));
            em.token('token-number', numMatch[0]);
            i += numMatch[0].length;
            continue;
        }

        // 操作符
        if (';|&<>'.includes(c)) {
            const opMatch = /^(?:\|\||&&|>>|<<|>&|\|\||\|)/.exec(source.slice(i));
            if (opMatch) {
                em.token('token-operator', opMatch[0]);
                i += opMatch[0].length;
                continue;
            }
            em.token('token-operator', c);
            i += 1;
            continue;
        }

        // 非行首的普通词（命令参数等）
        if (isIdentStart(c)) {
            let j = i;
            while (j < n && /[A-Za-z0-9_.-]/.test(source[j])) j += 1;
            const word = source.slice(i, j);
            const prevChar = source[i - 1] || '';
            const afterWord = source.slice(j).replace(/^\s*/, '');
            // 前一个非空白 token 是否为标志位（-m core.collector 中 -m）
            const beforeTrimBash = source.slice(0, i).replace(/\s+$/, '');
            const prevToken = beforeTrimBash.split(/\s+/).pop() || '';
            if (BASH_KEYWORDS.has(word) && isSpace(prevChar)) {
                em.token('token-keyword', word);
            } else if (BASH_COMMON_COMMANDS.has(word) && (isSpace(prevChar) || ';&|'.includes(prevChar))) {
                // 管道/分号/& 后的常见命令 → 命令色
                em.token('token-command', word);
            } else if (/^--?[A-Za-z]/.test(prevToken)) {
                // 标志位后的参数（-m core.collector / --source snet 等）→ 参数色
                em.token('token-parameter', word);
            } else if (afterWord[0] === '(' && isSpace(prevChar)) {
                // 函数调用 start_service( → 函数色
                em.token('token-function', word);
            } else {
                em.text(word);
            }
            i = j;
            continue;
        }

        em.text(c);
        i += 1;
    }

    return em.toString();
}

/* ==========================================================================
   8. YAML 扫描器
   ========================================================================== */

function scanYaml(source) {
    const em = createEmitter();
    const lines = source.split('\n');

    lines.forEach((line, lineIndex) => {
        if (lineIndex > 0) em.text('\n');
        let i = 0;
        const n = line.length;
        let inValue = false; // 是否已匹配过 key: 进入值区域（值区域裸字符串按字符串色）

        while (i < n) {
            const c = line[i];

            // 注释（# 前为空白或行首）
            if (c === '#' && (i === 0 || isSpace(line[i - 1]))) {
                em.token('token-comment', line.slice(i));
                i = n;
                continue;
            }

            // 文档分隔符
            if (/^---\s*$/.test(line.slice(i))) {
                em.token('token-operator', line.slice(i));
                i = n;
                continue;
            }

            // 缩进
            if (c === ' ' || c === '\t') {
                let j = i;
                while (j < n && (line[j] === ' ' || line[j] === '\t')) j += 1;
                em.text(line.slice(i, j));
                i = j;
                continue;
            }

            // 列表项 - 
            if (c === '-' && (isSpace(line[i + 1] || '') || i + 1 >= n)) {
                em.token('token-punctuation', '-');
                i += 1;
                continue;
            }

            // 锚点 & 与别名 *
            if (c === '&' || c === '*') {
                const nameMatch = /^[&*][A-Za-z0-9_-]+/.exec(line.slice(i));
                if (nameMatch) {
                    em.token('token-punctuation', nameMatch[0][0]);
                    em.token(c === '&' ? 'token-variable' : 'token-parameter', nameMatch[0].slice(1));
                    i += nameMatch[0].length;
                    continue;
                }
            }

            // 标签 !tag
            if (c === '!') {
                const tagMatch = /^![A-Za-z0-9_-]+/.exec(line.slice(i));
                if (tagMatch) {
                    em.token('token-punctuation', '!');
                    em.token('token-constant', tagMatch[0].slice(1));
                    i += tagMatch[0].length;
                    continue;
                }
            }

            // YAML 块标量指示符 | 与 >（多行文本块起始，值区域识别）
            if ((c === '|' || c === '>') && inValue) {
                const blockMatch = /^[|>][+-]?\d*/.exec(line.slice(i));
                if (blockMatch) {
                    em.token('token-operator', blockMatch[0]);
                    i += blockMatch[0].length;
                    continue;
                }
            }

            // 键 key: value（冒号后必须跟空白/行尾，避免 URL 的 https: 被误判为键；
            // 使用 lookahead 保证冒号后的空格不吞掉、交由后续分支输出）
            if ((isIdentStart(c) || c === '"' || c === "'") && !inValue) {
                const keyMatch = /^([A-Za-z0-9_][\w.-]*)(\s*):(?=[ \t]|$)/.exec(line.slice(i));
                if (keyMatch) {
                    em.token('token-property', keyMatch[1]);
                    em.text(keyMatch[2]);
                    em.token('token-punctuation', ':');
                    i += keyMatch[0].length;
                    inValue = true;
                    // 剩余值
                    continue;
                }
                // 带引号的键 "some:key": value / 'key': value：
                // 先扫描完整字符串，再前瞻冒号判定，作为键着色而非字符串值，
                // 与不带引号的键保持行为一致
                if (c === '"' || c === "'") {
                    let quotedEnd = i + 1;
                    while (quotedEnd < line.length) {
                        if (line[quotedEnd] === '\\') { quotedEnd += 2; continue; }
                        if (line[quotedEnd] === c) { quotedEnd += 1; break; }
                        quotedEnd += 1;
                    }
                    const afterQuoted = line.slice(quotedEnd).replace(/^\s+/, '');
                    if (afterQuoted[0] === ':') {
                        const colonIdx = quotedEnd + (line.slice(quotedEnd).length - afterQuoted.length);
                        em.token('token-property', line.slice(i, quotedEnd));
                        em.text(line.slice(quotedEnd, colonIdx));
                        em.token('token-punctuation', ':');
                        i = colonIdx + 1;
                        inValue = true;
                        continue;
                    }
                }
                // 字符串值
                if (c === '"' || c === "'") {
                    i = scanStringRegion(em, line, i);
                    inValue = true;
                    continue;
                }
            }

            // IP 地址 / 版本号（如 127.0.0.1 / 1.2.3）：整体按字符串/值输出，避免被拆成数字+残留
            const ipMatch = /^(?:\d{1,3}\.){2,3}\d{1,3}(?:\/\d+)?\b/.exec(line.slice(i));
            if (ipMatch && inValue) {
                em.token('token-string', ipMatch[0]);
                i += ipMatch[0].length;
                continue;
            }

            // 数字
            const numMatch = /^(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|0[xX][0-9a-fA-F_]+)/.exec(line.slice(i));
            if (numMatch) {
                em.token('token-number', numMatch[0]);
                i += numMatch[0].length;
                continue;
            }

            // 布尔
            const boolMatch = /^(true|false|null|yes|no|on|off|True|False|Null|NULL|None|~)\b/.exec(line.slice(i));
            if (boolMatch) {
                em.token('token-boolean', boolMatch[0]);
                i += boolMatch[0].length;
                continue;
            }

            // 内联标点
            if ('[]{}:,'.includes(c)) {
                em.token('token-punctuation', c);
                i += 1;
                continue;
            }

            // 普通文本（URL 等）：值区域以字符串色输出，键区域保留默认文本
            const plainMatch = /^[\w./:?=&%+#@!~-]+/.exec(line.slice(i));
            if (plainMatch && c !== ' ') {
                if (inValue) {
                    em.token('token-string', plainMatch[0]);
                } else {
                    em.text(plainMatch[0]);
                }
                i += plainMatch[0].length;
                continue;
            }

            em.text(c);
            i += 1;
        }
    });

    return em.toString();
}

/* ==========================================================================
   9. JSON 扫描器
   ========================================================================== */

function scanJson(source) {
    const em = createEmitter();
    const n = source.length;
    let i = 0;

    while (i < n) {
        const c = source[i];

        // 字符串（判断是键还是值：字符串后紧跟冒号则为键）
        if (c === '"') {
            // 先扫描到字符串结束位置，但不输出（scanStringRegion 会写 em，改用手动扫描）
            let j = i + 1;
            while (j < n) {
                if (source[j] === '\\') { j += 2; continue; }
                if (source[j] === '"') { j += 1; break; }
                j += 1;
            }
            const strEnd = j;
            const after = source.slice(strEnd).replace(/^\s*/, '');
            if (after[0] === ':') {
                // 键 → token-key
                em.token('token-key', source.slice(i, strEnd));
            } else {
                // 值 → token-string
                em.token('token-string', source.slice(i, strEnd));
            }
            i = strEnd;
            continue;
        }

        // 数字
        const numMatch = /^(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/.exec(source.slice(i));
        if (numMatch) {
            em.token('token-number', numMatch[0]);
            i += numMatch[0].length;
            continue;
        }

        // 布尔
        const boolMatch = /^(true|false|null)\b/.exec(source.slice(i));
        if (boolMatch) {
            em.token('token-boolean', boolMatch[0]);
            i += boolMatch[0].length;
            continue;
        }

        // 标点
        if ('{}[],:'.includes(c)) {
            em.token('token-punctuation', c);
            i += 1;
            continue;
        }

        em.text(c);
        i += 1;
    }

    return em.toString();
}

/* ==========================================================================
   10. 统一入口
   ========================================================================== */

function highlightMarkdownCode(code, language) {
    const lang = normalizeLanguageName(language);
    const source = String(code || '');

    switch (lang) {
        case 'python': case 'py':
            return scanPython(source);
        case 'js': case 'javascript': case 'ts': case 'typescript':
            return scanJavaScript(source);
        case 'jsx': case 'tsx':
            return scanJsx(source);
        case 'css': case 'scss': case 'less':
            return scanCss(source);
        case 'html': case 'xml': case 'htm': case 'svg':
            return scanHtml(source);
        case 'bash': case 'shell': case 'sh': case 'zsh': case 'powershell': case 'cmd':
            return scanBash(source);
        case 'yaml': case 'yml':
            return scanYaml(source);
        case 'json':
            return scanJson(source);
        case 'mermaid':
            return escapeHtmlText(source);
        default:
            return escapeHtmlText(source);
    }
}

// 绑定全局暴露（供 markdown.js 渲染管线调用，保持两模块解耦）
window.MarkdownCodeHighlighter = {
    highlightMarkdownCode,
    normalizeLanguageName,
};
