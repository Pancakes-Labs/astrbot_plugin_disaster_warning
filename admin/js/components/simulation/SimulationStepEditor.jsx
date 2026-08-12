const { Box, Typography, TextField, Select, MenuItem, ListSubheader, FormControl, InputLabel, FormControlLabel, Switch, Divider, Button, FormHelperText, IconButton, Tooltip, Chip } = MaterialUI;
const { useState, useEffect, useMemo, useCallback, useRef } = React;

// 事件编排字段：这些字段是 SimulationStep 的"顶层字段"（非 params），
// 单独分组展示，读写直接映射到 step 顶层，避免与 params 混淆。
const ORCHESTRATION_KEYS = ['report_num', 'event_key', 'is_final'];

// 编排字段的 flex 宽度类映射（配合 .sim-editor-orchestration 的 flex 单行布局）：
// 报数固定窄宽、事件键弹性占满、最终报固定窄宽
const ORCHESTRATION_WIDTH = {
    report_num: 'sim-field-third--report',
    event_key: 'sim-field-half--event',
    is_final: 'sim-field-third--final',
};

// 事件键自动分组时使用的键前缀说明（帮助用户理解事件键语义）
const EVENT_KEY_HINT = '想模拟"同一事件连发多报"时，给这些步骤填同一个事件键（比如一次地震的第1报、第2报、最终报）。填了相同事件键的步骤会被当成同一个事件，按顺序自动递增第几报。';

// 解析 JSON 数组文本为数组（容错：非法输入返回 []，且只保留对象元素，绝不在渲染期抛错）
function safeParseArray(text) {
    const raw = String(text || '').trim();
    if (!raw) return [];
    try {
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) return [];
        // 只保留纯对象元素；null/标量/嵌套数组一律剔除，避免 renderInput 取值抛错
        return parsed.filter(item => item !== null && typeof item === 'object' && !Array.isArray(item));
    } catch (e) {
        return [];
    }
}

// 解析 JSON 对象文本为对象（容错：非法输入返回 {}，仅保留纯对象，渲染期绝不抛错）
function safeParseObject(text) {
    const raw = String(text || '').trim();
    if (!raw) return {};
    try {
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed;
        return {};
    } catch (e) {
        return {};
    }
}

// 把任意单元格值归一化为 TextField 可安全渲染的字符串
// （布尔/对象/数组等非文本类型转字符串；null/undefined 转空串）
function normalizeCellValue(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') {
        // 对象/数组显示为 JSON 文本，避免 React 抛 value 类型错误
        try {
            return JSON.stringify(value);
        } catch (e) {
            return '';
        }
    }
    return String(value);
}

// 按点路径读取嵌套值，支持列定义引用嵌套对象字段。
// 路径不存在时返回 undefined；row 非对象时安全返回 undefined。
function getNestedValue(row, key) {
    if (!row || typeof row !== 'object') return undefined;
    if (!String(key).includes('.')) return row[key];
    return String(key).split('.').reduce((acc, part) => {
        if (acc === null || acc === undefined) return undefined;
        return typeof acc === 'object' ? acc[part] : undefined;
    }, row);
}

// 按点路径写入嵌套值，中间层级不存在时自动创建对象，避免覆盖同级的其他嵌套字段。
function setNestedValue(row, key, value) {
    const parts = String(key).split('.');
    const last = parts.pop();
    if (!last) return row;
    if (parts.length === 0) {
        return { ...row, [last]: value };
    }
    const head = row || {};
    let cursor = { ...head };
    let root = cursor;
    parts.forEach((part, idx) => {
        const cur = cursor[part];
        const next = (cur && typeof cur === 'object' && !Array.isArray(cur)) ? { ...cur } : {};
        if (idx === 0) {
            root = { ...row, [part]: next };
        } else {
            cursor[part] = next;
        }
        cursor = next;
    });
    cursor[last] = value;
    return root;
}

// 把任意 JSON 字段值归一化为"合法 JSON 文本"字符串：
// - 对象/数组（历史草稿常见）先 JSON.stringify，避免 String() 产生 "[object Object]" 非法文本
// - 空/undefined → 空串
function safeJsonString(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') {
        try {
            return JSON.stringify(value);
        } catch (e) {
            return '';
        }
    }
    return String(value);
}

/**
 * 局部渲染错误边界：任何子渲染异常只降级显示提示，不拖垮整个视图（防白屏）。
 * 同时覆盖"可视化表格"与"原始 JSON"两种视图，并提供"重试"按钮。
 */
class JsonEditorErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { hasError: false, message: '' };
    }
    static getDerivedStateFromError(error) {
        return {
            hasError: true,
            message: String(error && error.message ? error.message : error),
        };
    }
    componentDidCatch(error) {
        console.error('JSON 编辑器渲染异常', error);
    }
    handleRetry = () => {
        this.setState({ hasError: false, message: '' });
    };
    render() {
        if (this.state.hasError) {
            return (
                <div className="sim-json-table-empty">
                    <Typography variant="caption" color="error">
                        该字段渲染出错，已局部降级，不影响其他区域
                    </Typography>
                    {this.state.message && (
                        <Typography variant="caption" color="text.secondary" component="div" style={{ opacity: 0.7 }}>
                            {this.state.message}
                        </Typography>
                    )}
                    <div style={{ marginTop: 6 }}>
                        <Button size="small" variant="outlined" color="primary" onClick={this.handleRetry}>
                            重试渲染
                        </Button>
                    </div>
                </div>
            );
        }
        return this.props.children;
    }
}

/**
 * 可视化 JSON 数组编辑器（可视化视图 + 原始 JSON 双视图）
 * 用于 S-Net 测站、台风轨迹、海啸预报区等手写 JSON 痛苦的字段。
 *
 * @param {Object} props
 * @param {Object} props.field 字段定义（含 json_table 元数据）
 * @param {string} props.value 当前 JSON 字符串值
 * @param {Function} props.onChange 变更回调（输出 JSON 字符串）
 */
function JsonArrayTableEditor({ field, value, onChange }) {
    const meta = field.json_table || {};
    const columns = meta.columns || [];
    // 对象模式：json_table.kind === 'object' 时按"键值对"编辑（一行一个键），
    // 数组模式（默认）按"行"编辑，用于数组元素列表。
    const isObjectMode = meta.kind === 'object';
    const [tab, setTab] = useState('table');
    // JSON 视图 TextField 的根容器 ref（用于修正 NotchedOutline 缺口宽度）
    const jsonTextAreaRef = useRef(null);
    // 用 safeJsonString 归一化：历史草稿可能把 JSON 字段存成对象/数组，
    // String() 会产出 "[object Object]" 非法文本，导致 JSON.parse 阶段异常。
    // 美观打印默认打开：初始化时若 JSON 可解析则格式化为可读缩进形态。
    const [localText, setLocalText] = useState(() => {
        const raw = safeJsonString(value);
        try {
            return JSON.stringify(JSON.parse(raw), null, 2);
        } catch (e) {
            return raw;
        }
    });
    const [error, setError] = useState('');
    const [pretty, setPretty] = useState(true);

    // 解析当前值（容错，渲染期不抛错）：数组模式解析数组，对象模式解析对象
    const items = isObjectMode ? [] : safeParseArray(value);
    const objectValue = isObjectMode ? safeParseObject(value) : null;

    const emitItems = useCallback((nextItems) => {
        try {
            const text = JSON.stringify(nextItems);
            onChange(text);
            // 美观打印开启时同步本地视图为缩进形态，保持开关状态一致
            setLocalText(pretty ? JSON.stringify(nextItems, null, 2) : text);
            setError('');
        } catch (e) {
            setError(String(e.message || e));
        }
    }, [onChange, pretty]);

    // 对象模式：把整个对象序列化回父级（保留全部键，仅更新被编辑的键）
    const emitObject = useCallback((nextObj) => {
        try {
            const text = JSON.stringify(nextObj);
            onChange(text);
            setLocalText(pretty ? JSON.stringify(nextObj, null, 2) : text);
            setError('');
        } catch (e) {
            setError(String(e.message || e));
        }
    }, [onChange, pretty]);

    // 文本编辑模式下同步外部值（同样归一化，避免对象值转成 "[object Object]"）。
    // 语义比较：仅当外部 value 与当前显示文本的"语义"（解析后的数据）不同才覆盖，
    // 这样表格编辑/美观打印后的本地缩进文本不会被父级回传的紧凑文本覆盖，
    // 也避免每敲一个字（onChange 同步父级 → value 变化）都触发 setLocalText 导致光标跳动。
    useEffect(() => {
        let equal = false;
        try {
            const a = JSON.parse(String(localText || ''));
            const b = JSON.parse(safeJsonString(value));
            equal = JSON.stringify(a) === JSON.stringify(b);
        } catch (e) {
            equal = false;
        }
        if (!equal) {
            setLocalText(safeJsonString(value));
        }
        // 仅监听 value：localText 是本地编辑源，不能作为依赖
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value]);

    /**
     * JSON 视图框线缺口修正。
     *
     * MUI 的 NotchedOutline legend 宽度由 JS 按 label 的 offsetWidth 测量后写入
     * inline style；但中文长 label 在 shrink 态下会被 `max-width: calc(100% - 24px)`
     * 截断（overflow hidden），导致测量宽度 < 文字真实宽度，框线缺口比文字窄而重叠。
     *
     * 这里在每次进入 JSON 视图 / label 变化时，用 label.scrollWidth（完整内容宽度）
     * 强制覆盖 legend 宽度（仅当缺口偏窄时），并用 ResizeObserver 持续跟进
     * （字体加载、主题切换、窗口缩放等导致的宽度变化）。
     */
    useEffect(() => {
        if (tab !== 'json') return;
        let rafId = null;
        let observer = null;
        const fixNotch = () => {
            const wrap = jsonTextAreaRef.current;
            if (!wrap) return;
            const legend = wrap.querySelector('.MuiOutlinedInput-notchedOutline legend');
            const labelEl = wrap.querySelector('.MuiInputLabel-root');
            if (!legend || !labelEl) return;
            const contentWidth = labelEl.scrollWidth || 0;
            // 仅在缺口偏窄时修正：避免每次 render 都改写 MUI 计算值，保留其自适应能力
            if (contentWidth > 0 && legend.offsetWidth < contentWidth) {
                legend.style.width = `${contentWidth + 8}px`;
            }
        };
        // 等 DOM 就绪（字体 / label 布局完成）后再测量
        rafId = requestAnimationFrame(() => {
            fixNotch();
            const wrap = jsonTextAreaRef.current;
            const labelEl = wrap && wrap.querySelector('.MuiInputLabel-root');
            if (labelEl && typeof ResizeObserver === 'function') {
                observer = new ResizeObserver(fixNotch);
                observer.observe(labelEl);
            }
        });
        return () => {
            if (rafId) cancelAnimationFrame(rafId);
            if (observer) observer.disconnect();
        };
    }, [tab, field.label]);

    /**
     * 切换美观打印（bool 开关）：把当前 JSON 文本格式化为可读缩进形态。
     * 仅调整展示形态，不改变存储值；切回紧凑态时按当前文本重新压缩。
     * 解析失败时仅提示，绝不抛出（保证渲染期安全）。
     */
    const togglePretty = (nextPretty) => {
        setPretty(Boolean(nextPretty));
        try {
            const text = String(localText || '').trim();
            if (!text) return;
            const parsed = JSON.parse(text);
            const nextText = nextPretty ? JSON.stringify(parsed, null, 2) : JSON.stringify(parsed);
            setLocalText(nextText);
            // 同步到父级：保证 localText 与 value prop 一致，避免后续
            // emitItems 用旧 items 覆盖美观文本，或切回表格视图时数据不一致。
            onChange && onChange(nextText);
            setError('');
        } catch (e) {
            setError('JSON 格式有误');
        }
    };

    const handleTextChange = (text) => {
        setLocalText(text);
        onChange(text);
        try {
            if (text.trim()) {
                const parsed = JSON.parse(text);
                if (isObjectMode) {
                    setError(
                        parsed && typeof parsed === 'object' && !Array.isArray(parsed)
                            ? ''
                            : '必须是 JSON 对象'
                    );
                } else {
                    setError(Array.isArray(parsed) ? '' : '必须是 JSON 数组');
                }
            } else {
                setError('');
            }
        } catch (e) {
            setError('JSON 格式有误');
        }
    };

    const updateRow = (index, key, nextVal) => {
        const next = items.map((row, i) =>
            i === index ? setNestedValue(row, key, nextVal) : row
        );
        emitItems(next);
    };

    // 对象模式：更新单个键值（基于最新 value 保留其他键）。
    // 支持点路径嵌套写入，保留对象的深层结构。
    const updateObjectValue = (key, nextVal) => {
        const base = safeParseObject(value);
        const next = setNestedValue(base, key, nextVal);
        emitObject(next);
    };

    const addRow = () => {
        const blank = {};
        columns.forEach((col) => {
            blank[col.key] = col.type === 'number' ? 0 : '';
        });
        emitItems([...items, blank]);
    };

    const removeRow = (index) => {
        emitItems(items.filter((_, i) => i !== index));
    };

    const moveRow = (index, delta) => {
        const target = index + delta;
        if (target < 0 || target >= items.length) return;
        const next = [...items];
        const [row] = next.splice(index, 1);
        next.splice(target, 0, row);
        emitItems(next);
    };

    const renderInput = (col, row, index) => {
        const colValue = normalizeCellValue(getNestedValue(row, col.key));
        if (col.type === 'number') {
            return (
                <TextField
                    size="small"
                    type="number"
                    value={colValue}
                    inputProps={{ min: col.min, max: col.max, step: col.step || 1 }}
                    onChange={(e) => {
                        const raw = e.target.value;
                        if (raw === '') {
                            updateRow(index, col.key, '');
                            return;
                        }
                        const num = parseFloat(raw);
                        updateRow(index, col.key, Number.isNaN(num) ? '' : num);
                    }}
                    className="sim-json-table-input"
                />
            );
        }
        return (
            <TextField
                size="small"
                value={colValue}
                onChange={(e) => updateRow(index, col.key, e.target.value)}
                className="sim-json-table-input"
            />
        );
    };

    // 对象模式：按列定义渲染单个键的输入控件
    const renderObjectInput = (col) => {
        const colValue = normalizeCellValue(
            objectValue ? getNestedValue(objectValue, col.key) : undefined
        );
        if (col.type === 'number') {
            return (
                <TextField
                    size="small"
                    type="number"
                    value={colValue}
                    inputProps={{ min: col.min, max: col.max, step: col.step || 1 }}
                    onChange={(e) => {
                        const raw = e.target.value;
                        if (raw === '') {
                            updateObjectValue(col.key, '');
                            return;
                        }
                        const num = parseFloat(raw);
                        updateObjectValue(col.key, Number.isNaN(num) ? '' : num);
                    }}
                    className="sim-json-table-input"
                />
            );
        }
        return (
            <TextField
                size="small"
                value={colValue}
                onChange={(e) => updateObjectValue(col.key, e.target.value)}
                className="sim-json-table-input"
            />
        );
    };

    return (
        <div className="sim-json-table-wrap">
            {/* 视图切换：可视化视图 ⇄ 原始 JSON。
                切换按钮（tabs）始终处于错误边界之外，保证 JSON 视图渲染崩溃时
                仍可切回可视化列表，避免"切不回去"的窘境。
                美观打印开关与视图按钮同行、右对齐，默认打开。 */}
            <div className="sim-json-table-tabs">
                <Button
                    size="small"
                    variant={tab === 'table' ? 'contained' : 'text'}
                    onClick={() => setTab('table')}
                    className="sim-json-tab-btn"
                >
                    <span className="sim-json-tab-icon" aria-hidden="true">
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                            <path d="M3 9h18" />
                            <path d="M3 15h18" />
                            <path d="M9 3v18" />
                        </svg>
                    </span>
                    可视化编辑
                </Button>
                <Button
                    size="small"
                    variant={tab === 'json' ? 'contained' : 'text'}
                    onClick={() => setTab('json')}
                    className="sim-json-tab-btn"
                >
                    <span className="sim-json-tab-icon" aria-hidden="true">
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5c0 1.1.9 2 2 2h1" />
                            <path d="M16 21h1a2 2 0 0 0 2-2v-5c0-1.1.9-2 2-2a2 2 0 0 0-2-2V5a2 2 0 0 0-2-2h-1" />
                        </svg>
                    </span>
                    JSON
                </Button>
                <span className="sim-json-table-tabs-spacer" />
                <FormControlLabel
                    className="sim-json-pretty-switch"
                    control={
                        <Switch
                            size="small"
                            checked={pretty}
                            onChange={(e) => togglePretty(e.target.checked)}
                        />
                    }
                    label={<span className="sim-json-pretty-label">美观打印</span>}
                />
                {tab === 'json' && (
                    <Button
                        size="small"
                        color="success"
                        variant="outlined"
                        onClick={() => {
                            try {
                                const parsed = JSON.parse(localText);
                                const valid = isObjectMode
                                    ? parsed && typeof parsed === 'object' && !Array.isArray(parsed)
                                    : Array.isArray(parsed);
                                if (valid) {
                                    setTab('table');
                                    setError('');
                                } else {
                                    setError(isObjectMode ? '必须是 JSON 对象' : '必须是 JSON 数组');
                                }
                            } catch (e) {
                                setError('JSON 格式有误');
                            }
                        }}
                    >
                        ✅ 应用并返回可视化
                    </Button>
                )}
            </div>

            {tab === 'table' ? (
                <JsonEditorErrorBoundary key="table-view">
                    {isObjectMode ? (
                        /* 对象模式：键值对表格（一行一个键），列定义描述每个键的 label/type */
                        <div className="sim-json-table">
                            <div className="sim-json-table-scroll">
                                <table className="sim-json-table-grid sim-json-table-grid--object">
                                    <thead>
                                        <tr>
                                            <th className="sim-json-table-key-cell">字段</th>
                                            <th>值</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {columns.map((col) => (
                                            <tr key={col.key}>
                                                <td className="sim-json-table-key-cell">
                                                    {col.label || col.key}
                                                </td>
                                                <td>{renderObjectInput(col)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    ) : (
                        <div className="sim-json-table">
                            {items.length === 0 ? (
                                <Typography variant="caption" color="text.secondary" className="sim-json-table-empty">
                                    {meta.empty_hint || '暂无数据'}
                                </Typography>
                            ) : (
                                <div className="sim-json-table-scroll">
                                    <table className="sim-json-table-grid">
                                        <thead>
                                            <tr>
                                                {columns.map((col) => (
                                                    <th key={col.key}>{col.label || col.key}</th>
                                                ))}
                                                <th className="sim-json-table-ops-col">操作</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {items.map((row, index) => (
                                                <tr key={index}>
                                                    {columns.map((col) => (
                                                        <td key={col.key}>{renderInput(col, row, index)}</td>
                                                    ))}
                                                    <td className="sim-json-table-ops">
                                                        <IconButton
                                                            size="small"
                                                            title="上移"
                                                            disabled={index === 0}
                                                            onClick={() => moveRow(index, -1)}
                                                        >
                                                            ↑
                                                        </IconButton>
                                                        <IconButton
                                                            size="small"
                                                            title="下移"
                                                            disabled={index === items.length - 1}
                                                            onClick={() => moveRow(index, 1)}
                                                        >
                                                            ↓
                                                        </IconButton>
                                                        <IconButton
                                                            size="small"
                                                            color="error"
                                                            title="删除"
                                                            onClick={() => removeRow(index)}
                                                        >
                                                            🗑️
                                                        </IconButton>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                            <Button
                                size="small"
                                variant="outlined"
                                color="primary"
                                onClick={addRow}
                                className="sim-json-table-add"
                            >
                                {meta.add_label || '添加'}
                            </Button>
                        </div>
                    )}
                </JsonEditorErrorBoundary>
            ) : (
                <JsonEditorErrorBoundary key="json-view">
                    <div className="sim-json-text-wrap">
                        <TextField
                            fullWidth
                            size="small"
                            label={field.label}
                            // shrink: 强制 label 保持浮动状态，让 NotchedOutline 缺口从
                            // 初始渲染就按完整 label 宽度计算——避免中文长 label 在
                            // "嵌入→浮动"切换时缺口宽度测量异常导致框线与文字重叠。
                            InputLabelProps={{
                                shrink: true,
                                style: { whiteSpace: 'nowrap' },
                            }}
                            value={localText}
                            error={Boolean(error)}
                            helperText={error || (field.placeholder ? `示例：${field.placeholder}` : '')}
                            multiline
                            minRows={field.rows || 6}
                            maxRows={(field.rows || 6) + 8}
                            className="sim-json-text-area"
                            onChange={(e) => handleTextChange(e.target.value)}
                            // inputRef 拿到 textarea 后向上取根容器，供缺口修正使用
                            inputRef={(node) => {
                                jsonTextAreaRef.current = node
                                    ? node.closest('.sim-json-text-area')
                                    : null;
                            }}
                        />
                    </div>
                </JsonEditorErrorBoundary>
            )}
        </div>
    );
}

/**
 * 模拟步骤编辑器组件
 * 根据后端 schema 动态渲染灾种选择 → 数据源选择 → 参数表单。
 *
 * 布局优化：
 * - 灾种 + 数据源选择行（数据源带 family 后缀区分同名键）
 * - 时间参数独立分组（发震时间 / 事件时间回退 / 更新时间回退）
 * - 两列布局：左列基础参数，右列数据源特有参数
 * - 事件编排分组（报数自适应：无报数语义的源自动隐藏）
 * - 事件键辅助说明 + 一键填充按钮
 * - JSON 数组字段支持可视化表格编辑
 *
 * @param {Object} props
 * @param {Object|null} props.schema 后端 schema（disaster_types / target_sessions）
 * @param {Object|null} props.step 当前编辑的步骤
 * @param {Function} props.onChange 步骤变更回调
 */
function SimulationStepEditor({ schema, step, onChange }) {
    const disasterTypes = schema?.disaster_types || {};
    const { useToast: useToastHook } = window;
    const { showToast } = (typeof useToastHook === 'function' ? useToastHook() : {});
    const [disasterType, setDisasterType] = useState(step?.disaster_type || '');
    const [sourceId, setSourceId] = useState(step?.source_id || '');
    const [params, setParams] = useState(step?.params || {});
    const [jsonErrors, setJsonErrors] = useState({});
    const [weatherCodeSuggestions, setWeatherCodeSuggestions] = useState([]);
    const [weatherCodeBusy, setWeatherCodeBusy] = useState(false);
    const [geoBusy, setGeoBusy] = useState(false);

    // 同步外部 step 变化（只监听 step_id，避免编辑中途被外部覆盖）
    useEffect(() => {
        if (step) {
            setDisasterType(step.disaster_type || '');
            setSourceId(step.source_id || '');
            setParams(step.params || {});
        }
    }, [step?.step_id]);

    // 获取当前灾种的源列表与当前源字段
    const typeData = disasterTypes[disasterType] || {};
    const sources = typeData.sources || [];
    const currentSource = sources.find(s => s.source_id === sourceId);
    const fields = currentSource?.fields || [];

    // 使用 schema 分组视图（向后兼容：无分组视图时回退全量字段拆分）
    const {
        baseFields,
        timeFields,
        sourceFields,
        orchestrationFields,
        supportsReportSemantics,
        sourceMeta,
    } = useMemo(() => {
        const src = currentSource || {};
        const bs = Array.isArray(src.base_fields) ? src.base_fields : [];
        const ts = Array.isArray(src.time_fields) ? src.time_fields : [];
        const ss = Array.isArray(src.source_fields) ? src.source_fields : [];
        const os = Array.isArray(src.orchestration_fields) ? src.orchestration_fields : [];
        // 时间参数字段键集合：绝对时间 + 回退秒数 + 延迟秒数
        const TIME_KEYS = ['event_time', 'time_offset_seconds', 'event_time_delay_seconds', 'update_time', 'update_time_offset_seconds', 'update_time_delay_seconds'];
        // 向后兼容：无分组视图时按 key 推断
        const fallbackOrchestration = (fields || []).filter(f => ORCHESTRATION_KEYS.includes(f.key));
        const fallbackTime = (fields || []).filter(f => TIME_KEYS.includes(f.key));
        const fallbackOthers = (fields || []).filter(f => !ORCHESTRATION_KEYS.includes(f.key) && !TIME_KEYS.includes(f.key));
        return {
            baseFields: bs.length > 0 ? bs : fallbackOthers,
            timeFields: ts.length > 0 ? ts : fallbackTime,
            sourceFields: ss.length > 0 ? ss : [],
            orchestrationFields: os.length > 0 ? os : fallbackOrchestration,
            supportsReportSemantics: src.supports_report_semantics !== false,
            sourceMeta: src,
        };
    }, [fields, currentSource]);

    /**
     * 切换灾种：重置源与参数（保留编排字段）
     */
    const handleDisasterTypeChange = (nextType) => {
        const nextTypeData = disasterTypes[nextType] || {};
        const nextSources = nextTypeData.sources || [];
        const nextSourceId = nextSources[0]?.source_id || '';
        const nextFields = nextSources[0]?.fields || [];

        setDisasterType(nextType);
        setSourceId(nextSourceId);

        // 用新字段的默认值重置参数（编排字段除外，保留用户当前编排设置）
        const nextParams = {};
        nextFields.forEach(f => {
            if (!ORCHESTRATION_KEYS.includes(f.key) && f.default !== undefined) {
                nextParams[f.key] = f.default;
            }
        });

        setParams(nextParams);
        setJsonErrors({});
        emitChange(nextType, nextSourceId, nextParams);
    };

    /**
     * 切换数据源：重置全部参数为新源 schema 默认值（即 docs 文档示例），
     * 仅保留事件编排字段（报数/事件键/延迟/最终报）不随源切换丢失。
     *
     * 注意：切换数据源时应让基础参数跟随该源文档示例，而不是保留旧源的值，
     * 否则会出现"选了日本源却还是中国震中"的错乱。
     */
    const handleSourceChange = (nextSourceId) => {
        const nextSource = sources.find(s => s.source_id === nextSourceId);
        const nextFields = nextSource?.fields || [];

        const nextParams = {};
        nextFields.forEach(f => {
            if (!ORCHESTRATION_KEYS.includes(f.key) && f.default !== undefined) {
                nextParams[f.key] = f.default;
            }
        });

        setSourceId(nextSourceId);
        setParams(nextParams);
        setJsonErrors({});
        emitChange(disasterType, nextSourceId, nextParams);
    };

    /**
     * 更新单个普通参数（写入 params）
     */
    const handleParamChange = (key, value) => {
        const nextParams = { ...params, [key]: value };
        setParams(nextParams);
        emitChange(disasterType, sourceId, nextParams);
    };

    /**
     * 更新编排字段（写入 step 顶层字段）
     */
    const handleOrchestrationChange = (key, value) => {
        const updated = { ...(step || {}), [key]: value };
        onChange && onChange(updated);
    };

    /**
     * 统一回调变更（仅灾种/源/普通参数）
     */
    const emitChange = (type, source, nextParams) => {
        onChange && onChange({
            ...(step || {}),
            disaster_type: type,
            source_id: source,
            params: nextParams,
        });
    };

    /**
     * 校验 JSON 文本
     */
    const validateJson = (text) => {
        if (!text || !String(text).trim()) return null;
        try {
            JSON.parse(text);
            return null;
        } catch (e) {
            return 'JSON 格式有误';
        }
    };

    /**
     * 读取字段值：编排字段从 step 顶层读取，普通字段从 params 读取
     */
    const getFieldValue = (field) => {
        if (ORCHESTRATION_KEYS.includes(field.key)) {
            const topValue = step ? step[field.key] : undefined;
            return topValue !== undefined && topValue !== '' ? topValue : field.default;
        }
        return params[field.key] !== undefined ? params[field.key] : field.default;
    };

    // 时间参数分组内的字段按 2:1:1 布局（绝对时间占半宽，回退/延迟各占四分之一）：
    // 发震时间(2) + 回退(1) + 延迟(1) / 更新时间(2) + 回退(1) + 延迟(1)
    const TIME_PAIR_KEYS = new Set([
        'event_time', 'time_offset_seconds', 'event_time_delay_seconds',
        'update_time', 'update_time_offset_seconds', 'update_time_delay_seconds',
    ]);
    // 时间参数里的"回退/延迟"秒数字段：配合 .sim-editor-time-grid 的 2fr 1fr 1fr 网格，
    // 占四分之一宽；绝对时间字段保持半宽。
    const TIME_COMPACT_KEYS = new Set(['time_offset_seconds', 'event_time_delay_seconds', 'update_time_offset_seconds', 'update_time_delay_seconds']);

    /**
     * 计算普通字段的网格宽度类：
     * - json 字段全宽
     * - 时间参数绝对时间字段占半宽（2:1:1 布局中的 "2"）
     * - 时间参数回退/延迟字段占四分之一宽（2:1:1 布局中的 "1"）
     * - 数字/布尔短字段占半宽（可并排）
     * - 文本长字段全宽
     */
    const getFieldWidthClass = (field) => {
        if (field.type === 'json') return 'sim-field-full';
        if (TIME_COMPACT_KEYS.has(field.key)) return 'sim-field-quarter';
        if (TIME_PAIR_KEYS.has(field.key)) return 'sim-field-half';
        // 后端 width 元数据优先（如台风中文名/英文名 width=half 并排）
        if (field.width === 'half') return 'sim-field-half';
        if (field.type === 'number' || field.type === 'int' || field.type === 'bool' || field.type === 'select') {
            return 'sim-field-half';
        }
        return 'sim-field-full';
    };

    /**
     * 生成事件键一键填充建议（基于当前灾种/源与时间）
     */
    const generateEventKey = () => {
        const now = new Date();
        const ymd = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`;
        const typePrefix = {
            earthquake: 'eq',
            tsunami: 'ts',
            weather: 'wx',
            typhoon: 'ty',
        }[disasterType] || 'ev';
        const key = `${typePrefix}${ymd}${String(Math.floor(Math.random() * 100)).padStart(2, '0')}`;
        handleOrchestrationChange('event_key', key);
    };

    /**
     * 根据当前预警标题/副标题自动生成气象预警编码。
     * 气象源专用：标题 → 灾害类型 + 颜色 → 紧凑 11B 编码。
     */
    const generateWeatherCode = async () => {
        const simApi = window.DisasterSimulationApi;
        const title = String(params.title || '').trim();
        const headline = String(params.headline || '').trim();
        if (!title && !headline) {
            showToast && showToast('请先填写预警标题或副标题', 'warning');
            return;
        }
        setWeatherCodeBusy(true);
        try {
            const result = await simApi.suggestWeatherCode({ title, headline });
            const code = result?.code || '';
            if (!code) {
                setWeatherCodeSuggestions([]);
                showToast && showToast('未能从标题中识别预警类型/颜色，请手动填写或调整标题', 'warning');
                return;
            }
            handleParamChange('weather_code', code);
            setWeatherCodeSuggestions([code]);
            showToast && showToast(`已生成预警编码：${code}`, 'success');
        } catch (e) {
            console.error('生成预警编码失败', e);
            showToast && showToast('生成预警编码失败: ' + (e.message || e), 'error');
        } finally {
            setWeatherCodeBusy(false);
        }
    };

    /**
     * 判断当前数据源是否包含经纬度基础参数（决定是否展示定位按钮）
     */
    const sourceHasGeo = useMemo(() => {
        const keys = new Set((fields || []).map(f => f.key));
        return keys.has('latitude') && keys.has('longitude');
    }, [fields]);

    /**
     * 调用定位服务，解析当前 IP 经纬度并回填震中位置与经纬度
     */
    const handleGeoLocate = async () => {
        const statusApi = window.DisasterStatusApi;
        if (!statusApi || typeof statusApi.getGeoLocation !== 'function') {
            showToast && showToast('定位服务不可用', 'error');
            return;
        }
        setGeoBusy(true);
        try {
            const result = await statusApi.getGeoLocation();
            const data = result?.data || result || {};
            const latitude = data.latitude;
            const longitude = data.longitude;
            if (latitude === undefined || latitude === null || longitude === undefined || longitude === null) {
                showToast && showToast('获取位置失败: 未返回有效坐标', 'error');
                return;
            }
            const location = `${data.province || ''} ${data.city || ''}`.trim() || '当前位置';
            const nextParams = {
                ...params,
                latitude,
                longitude,
            };
            // 仅当源含 place_name 字段时回填位置描述
            if ((fields || []).some(f => f.key === 'place_name')) {
                nextParams.place_name = location;
            }
            setParams(nextParams);
            emitChange(disasterType, sourceId, nextParams);
            showToast && showToast(`已定位：${location}`, 'success');
        } catch (e) {
            console.error('定位失败', e);
            showToast && showToast('定位失败: ' + (e.message || e), 'error');
        } finally {
            setGeoBusy(false);
        }
    };

    /**
     * 按字段类型渲染控件（外包宽度类 div）
     */
    const renderField = (field, isOrchestration) => {
        const { key, label, type, default: defValue, min, max, step, placeholder, options, rows, json_table } = field;
        const value = getFieldValue(field);
        const jsonError = jsonErrors[key] || null;

        const widthClass = isOrchestration
            ? (ORCHESTRATION_WIDTH[key] || 'sim-field-third')
            : getFieldWidthClass(field);

        const handleChange = (nextValue) => {
            if (isOrchestration) {
                handleOrchestrationChange(key, nextValue);
            } else {
                handleParamChange(key, nextValue);
            }
        };

        // JSON 可视化编辑字段（带 json_table 元数据）独占整行：避免被两列布局挤压，
        // 让表格/文本域获得步骤编辑区全宽，可读性大幅提升。
        // 外层再包一层错误边界：JsonArrayTableEditor 函数体（hooks 阶段）若抛错，
        // 内部错误边界捕获不到，必须由外层兜底，避免冒泡到全局边界导致整页崩溃。
        if (type === 'json' && json_table) {
            return (
                <div key={key} className="sim-field sim-field-full sim-json-table-field">
                    <Typography variant="caption" className="sim-json-table-label">
                        {label}
                    </Typography>
                    <JsonEditorErrorBoundary>
                        <JsonArrayTableEditor
                            field={field}
                            value={safeJsonString(value)}
                            onChange={handleChange}
                        />
                    </JsonEditorErrorBoundary>
                </div>
            );
        }

        let control;
        switch (type) {
            case 'number':
                control = (
                    <TextField
                        key={key}
                        fullWidth
                        size="small"
                        label={label}
                        type="number"
                        value={value ?? ''}
                        inputProps={{ min, max, step }}
                        onChange={(e) => {
                            const raw = e.target.value;
                            if (raw === '') { handleChange(''); return; }
                            const num = parseFloat(raw);
                            handleChange(Number.isNaN(num) ? '' : num);
                        }}
                    />
                );
                break;
            case 'int':
                control = (
                    <TextField
                        key={key}
                        fullWidth
                        size="small"
                        label={label}
                        type="number"
                        value={value ?? ''}
                        inputProps={{ min, max, step: 1 }}
                        onChange={(e) => {
                            const raw = e.target.value;
                            if (raw === '') { handleChange(''); return; }
                            const num = parseInt(raw, 10);
                            handleChange(Number.isNaN(num) ? '' : num);
                        }}
                    />
                );
                break;
            case 'bool':
                control = (
                    <FormControlLabel
                        key={key}
                        control={
                            <Switch
                                checked={Boolean(value)}
                                onChange={(e) => handleChange(e.target.checked)}
                                size="small"
                            />
                        }
                        label={label}
                    />
                );
                break;
            case 'select':
                control = (
                    <FormControl key={key} fullWidth size="small">
                        <InputLabel>{label}</InputLabel>
                        <Select
                            value={String(value ?? '')}
                            label={label}
                            onChange={(e) => handleChange(e.target.value)}
                        >
                            {(options || []).map((opt) => (
                                <MenuItem key={opt.value} value={opt.value}>
                                    {opt.label || opt.value}
                                </MenuItem>
                            ))}
                        </Select>
                    </FormControl>
                );
                break;
            case 'json':
                // 带 json_table 的字段已在 renderField 顶部提前返回（独占全行渲染 JsonArrayTableEditor）
                control = (
                    <TextField
                        key={key}
                        fullWidth
                        size="small"
                        label={label}
                        value={safeJsonString(value)}
                        placeholder={placeholder}
                        multiline
                        minRows={rows || 3}
                        maxRows={rows ? rows + 4 : 8}
                        error={Boolean(jsonError)}
                        helperText={jsonError || (placeholder ? `示例：${placeholder}` : '')}
                        onChange={(e) => {
                            const text = e.target.value;
                            setJsonErrors((prev) => ({
                                ...prev,
                                [key]: validateJson(text),
                            }));
                            handleChange(text);
                        }}
                    />
                );
                break;
            case 'text':
            default:
                // 震中/震源位置：手动输入 + 定位按钮（不平分宽度，输入框自动填满剩余空间）
                if (key === 'place_name' && sourceHasGeo && !isOrchestration) {
                    control = (
                        <div key={key} className="sim-geo-locate-field">
                            <TextField
                                fullWidth
                                size="small"
                                label={label}
                                value={value ?? ''}
                                placeholder={placeholder}
                                onChange={(e) => handleChange(e.target.value)}
                            />
                            <Tooltip title="使用当前 IP 自动定位填充经纬度">
                                <IconButton
                                    size="small"
                                    color="primary"
                                    onClick={handleGeoLocate}
                                    disabled={geoBusy}
                                    className="sim-geo-locate-btn"
                                >
                                    <span className="sim-geo-locate-icon">{geoBusy ? '⏳' : '📍'}</span>
                                </IconButton>
                            </Tooltip>
                        </div>
                    );
                } else if (key === 'weather_code') {
                    control = (
                        <div key={key} className="sim-weather-code-field">
                            <Box className="sim-weather-code-input-row">
                                <TextField
                                    fullWidth
                                    size="small"
                                    label={label}
                                    value={value ?? ''}
                                    placeholder={placeholder}
                                    onChange={(e) => handleChange(e.target.value)}
                                />
                                <Tooltip title="从预警标题中提取灾害类型与颜色，自动生成编码">
                                    <Button
                                        size="small"
                                        variant="outlined"
                                        color="primary"
                                        onClick={generateWeatherCode}
                                        disabled={weatherCodeBusy}
                                        className="sim-weather-code-gen"
                                    >
                                        {weatherCodeBusy ? '生成中…' : '🎲 自动生成'}
                                    </Button>
                                </Tooltip>
                            </Box>
                            {weatherCodeSuggestions.length > 0 && (
                                <Box className="sim-weather-code-suggestions">
                                    <Typography variant="caption" color="text.secondary" className="sim-weather-code-suggest-label">
                                        建议：
                                    </Typography>
                                    {weatherCodeSuggestions.map((code) => (
                                        <Chip
                                            key={code}
                                            size="small"
                                            label={code}
                                            color={String(value) === code ? 'primary' : 'default'}
                                            variant="outlined"
                                            onClick={() => handleChange(code)}
                                            className="sim-weather-code-chip"
                                        />
                                    ))}
                                </Box>
                            )}
                        </div>
                    );
                } else {
                    control = (
                        <TextField
                            key={key}
                            fullWidth
                            size="small"
                            label={label}
                            value={value ?? ''}
                            placeholder={placeholder}
                            multiline={String(label).length > 8 && (value || '').length > 20}
                            minRows={String(label).length > 8 && (value || '').length > 20 ? 2 : 1}
                            onChange={(e) => handleChange(e.target.value)}
                        />
                    );
                }
                break;
        }

        return <div key={key} className={`sim-field ${widthClass}`}>{control}</div>;
    };

    /**
     * 判断字段数组内是否包含"JSON 可视化编辑"字段
     */
    const groupHasJsonVisual = (groupFields) =>
        (groupFields || []).some(f => f.type === 'json' && f.json_table);

    /**
     * 渲染字段分组（带分组标题）。
     * 只渲染普通文本框（两列网格）；JSON 可视化字段由 renderJsonVisualSection 统一全宽渲染。
     */
    const renderFieldGroup = (groupFields, groupName, groupClass, extraHeader) => {
        if (!groupFields || groupFields.length === 0) return null;
        const normalFields = (groupFields || []).filter(f => !(f.type === 'json' && f.json_table));
        if (normalFields.length === 0) return null;
        return (
            <div className={`sim-editor-group sim-editor-group--${groupClass}`}>
                <Typography variant="subtitle2" className="sim-editor-group-title">
                    {groupName}
                </Typography>
                {extraHeader}
                <Box className="sim-editor-form-grid">
                    {normalFields.map(f => renderField(f, false))}
                </Box>
            </div>
        );
    };

    /**
     * 渲染 JSON 可视化字段（独立全宽分组，彻底脱离两列布局挤压）。
     * 返回 null 表示无此类字段。
     */
    const renderJsonVisualSection = (groupFields) => {
        const jsonVisualFields = (groupFields || []).filter(f => f.type === 'json' && f.json_table);
        if (jsonVisualFields.length === 0) return null;
        return (
            <div className="sim-editor-group sim-json-visual-group">
                <Typography variant="subtitle2" className="sim-editor-group-title">
                    ⚙️ 可视化编辑区
                </Typography>
                <Box className="sim-json-visual-section">
                    {jsonVisualFields.map(f => renderField(f, false))}
                </Box>
            </div>
        );
    };

    // 没有选中灾种时展示提示
    if (!disasterType && Object.keys(disasterTypes).length > 0) {
        return (
            <Box className="sim-editor-empty">
                <Typography variant="body2" color="text.secondary">
                    请先选择灾种类型
                </Typography>
            </Box>
        );
    }

    // 无报数语义的源：隐藏"报数"与"最终报"（两者都属于报次演进语义）。
    // 报数固定为 1；无报次演进概念时"最终报"标记也无意义。
    const effectiveOrchestration = supportsReportSemantics
        ? orchestrationFields
        : orchestrationFields.filter(f => f.key !== 'report_num' && f.key !== 'is_final');

    return (
        <Box className="sim-step-editor">
            {/* 1. 灾种 + 数据源：两列并排 */}
            <Box className="sim-editor-select-row">
                <FormControl fullWidth size="small" className="sim-editor-select">
                    <InputLabel>灾种类型</InputLabel>
                    <Select
                        value={disasterType}
                        label="灾种类型"
                        onChange={(e) => handleDisasterTypeChange(e.target.value)}
                    >
                        {Object.keys(disasterTypes).map(type => (
                            <MenuItem key={type} value={type}>
                                {disasterTypes[type].icon || ''} {disasterTypes[type].label || type}
                            </MenuItem>
                        ))}
                    </Select>
                </FormControl>

                <FormControl fullWidth size="small" className="sim-editor-select">
                    <InputLabel>数据源</InputLabel>
                    <Select
                        value={sourceId}
                        label="数据源"
                        renderValue={(val) => {
                            const src = sources.find(s => s.source_id === val);
                            if (!src) return val;
                            const suffix = src.family_label ? ` · ${src.family_label}` : '';
                            return `${src.label || src.source_id}${suffix}`;
                        }}
                        onChange={(e) => handleSourceChange(e.target.value)}
                    >
                        {(() => {
                            // 按地区分组渲染（中国 → 台湾 → 日本 → 全球），组内保持 schema 排序
                            const regions = typeData.region_list || [];
                            if (regions.length === 0) {
                                // 无地区元数据时回退平铺渲染
                                return sources.map(source => (
                                    <MenuItem key={source.source_id} value={source.source_id}>
                                        <span className="sim-source-option">
                                            <span className="sim-source-option-label">
                                                {source.label || source.source_id}
                                            </span>
                                            {source.family_label && (
                                                <span className="sim-source-option-family">
                                                    {source.family_label}
                                                </span>
                                            )}
                                        </span>
                                    </MenuItem>
                                ));
                            }
                            return regions.flatMap((region, ridx) => {
                                const regionSources = sources.filter(s => s.region === region.key);
                                if (regionSources.length === 0) return [];
                                const items = regionSources.map(source => (
                                    <MenuItem key={source.source_id} value={source.source_id}>
                                        <span className="sim-source-option">
                                            <span className="sim-source-option-label">
                                                {source.label || source.source_id}
                                            </span>
                                            {source.family_label && (
                                                <span className="sim-source-option-family">
                                                    {source.family_label}
                                                </span>
                                            )}
                                        </span>
                                    </MenuItem>
                                ));
                                return [
                                    <ListSubheader key={`region_${ridx}`} disableSticky className="sim-source-group-header">
                                        {region.label}
                                    </ListSubheader>,
                                    ...items,
                                ];
                            });
                        })()}
                    </Select>
                </FormControl>
            </Box>

            <Divider className="sim-editor-divider" />

            {/* 2. 时间参数（独立分组，上移展示） */}
            {timeFields.length > 0 && (
                <div className="sim-editor-group sim-editor-group--time">
                    <Typography variant="subtitle2" className="sim-editor-group-title">
                        ⏰ 时间参数
                    </Typography>
                    <Typography variant="caption" color="text.secondary" className="sim-editor-group-hint">
                        事件时间决定推文中的"发生/生效时刻"；更新时间决定消息的"发布/更新时刻"。两项都留空时，就使用你点击执行那一刻的时间。
                    </Typography>
                    <Typography variant="caption" color="text.secondary" className="sim-editor-group-hint sim-editor-group-hint--usage">
                        "回退"让时间往前挪（模拟历史时刻），"延迟"让时间往后挪（模拟未来时刻），两者叠加在已填的时间（或默认的当前时间）之上。
                    </Typography>
                    <Box className="sim-editor-form-grid sim-editor-time-grid">
                        {timeFields.map(f => renderField(f, false))}
                    </Box>
                </div>
            )}

            {/* 3. 主表单区：两列（左基础 + 右特有普通文本框），
                JSON 可视化字段在下方独立全宽渲染（renderJsonVisualSection） */}
            <Box className="sim-editor-two-col">
                <div className="sim-editor-col">
                    {baseFields.length === 0 ? (
                        <Typography variant="body2" color="text.secondary" className="sim-editor-empty">
                            该灾种无通用基础参数
                        </Typography>
                    ) : (
                        renderFieldGroup(baseFields, '🧱 基础参数', 'base')
                    )}
                </div>
                <div className="sim-editor-col sim-editor-col--source">
                    {sourceFields.length === 0 ? (
                        <Typography variant="body2" color="text.secondary" className="sim-editor-empty">
                            该数据源无特有参数，使用默认值即可
                        </Typography>
                    ) : (
                        renderFieldGroup(sourceFields, '⚙️ 数据源特有参数', 'source')
                    )}
                </div>
            </Box>

            {/* 3.5 JSON 可视化编辑区：独立全宽分组（脱离两列布局，表格/文本域占满步骤编辑区） */}
            {renderJsonVisualSection(sourceFields)}

            {/* 4. 事件编排字段（独立分组，紧凑单行布局） */}
            {effectiveOrchestration.length > 0 && (
                <div className="sim-editor-group sim-editor-group--orchestration">
                    <Typography variant="subtitle2" className="sim-editor-section-title">
                        🎬 事件编排
                    </Typography>
                    <Typography variant="caption" color="text.secondary" className="sim-editor-group-hint">
                        {!supportsReportSemantics && ' 当前数据源没有"第几报"的概念，报数固定为第 1 报。'}
                    </Typography>

                    {/* 编排字段单行：报数 + 事件键 + 生成按钮 + 最终报 全部同行 */}
                    <div className="sim-editor-orchestration-row">
                        <Box className="sim-editor-orchestration">
                            {effectiveOrchestration.map(f => renderField(f, true))}
                        </Box>
                        <div className="sim-editor-event-key-ops">
                            <Tooltip title={EVENT_KEY_HINT} arrow>
                                <Chip
                                    size="small"
                                    label="❓"
                                    variant="outlined"
                                    className="sim-editor-event-key-help"
                                />
                            </Tooltip>
                            <Button
                                size="small"
                                variant="outlined"
                                color="primary"
                                onClick={generateEventKey}
                                className="sim-editor-event-key-gen"
                            >
                                🎲 生成事件键
                            </Button>
                        </div>
                    </div>
                </div>
            )}
        </Box>
    );
}

window.SimulationStepEditor = SimulationStepEditor;
