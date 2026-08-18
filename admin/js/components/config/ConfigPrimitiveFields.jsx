/**
 * 基础/原子字段组件渲染包 (ConfigPrimitiveFields)
 * 该文件集成了配置管理模块最核心的基础表单域，包括布尔型、数字型、列表型、枚举单选型及普通单行/多行文本。
 * 每一个表单域均严格响应 Material-UI 规范，通过统一的 FieldRow 排版组件输出，并与 draft 草稿系统深度集成。
 */

/**
 * 1. 布尔型切换开关 (ConfigBooleanField)
 * 渲染为标准 IOS 风格的 Switch 开关，适用于真假控制策略。
 */
function ConfigBooleanField({ fieldKey, schema, value, onChange, depth }) {
    const { Switch } = MaterialUI;
    const { DescriptionSection, FieldRow, FieldControl } = window.ConfigFieldLayout;
    return (
        <FieldRow depth={depth}>
            {/* 左侧中英文描述与 Hint 提示 */}
            <DescriptionSection schema={schema} fieldKey={fieldKey} />
            <FieldControl className="config-field-control--boolean">
                {/* 交互开关，缺失值时退回到 schema 定义的默认值，再缺失则默认为 false */}
                <Switch 
                    checked={value !== undefined ? value : (schema.default || false)} 
                    onChange={(e) => onChange(e.target.checked)} 
                />
            </FieldControl>
        </FieldRow>
    );
}

/**
 * 2. 数值输入与滑块拖拽域 (ConfigNumberField)
 * 渲染为带有数字步长微调的 Input 框。若 Schema 声明了 slider 属性，则额外渲染高精度的拖拽滑块 (Slider)。
 */
function ConfigNumberField({ fieldKey, schema, value, onChange, depth }) {
    const { Box, TextField, Slider } = MaterialUI;
    const { DescriptionSection, FieldRow, FieldControl, getConfigFieldInputSx } = window.ConfigFieldLayout;
    
    // 获取滑块极值与步长配置
    const sliderConfig = schema.slider;
    const hasRange = sliderConfig !== undefined;
    const min = sliderConfig?.min ?? schema.minimum;
    const max = sliderConfig?.max ?? schema.maximum;
    
    // 智能推断数值精度步长，若为整型则默认为 1，若为浮点型则默认为 0.1
    const step = sliderConfig?.step ?? (schema.type === 'integer' || schema.type === 'int' ? 1 : 0.1);
    
    // 本地状态：缓存用户输入的原始字符串，以允许用户输入 "-" 或空字符而不被强制重置为 0
    const [localValue, setLocalValue] = React.useState(value !== undefined ? value : (schema.default || 0));

    // 监听外部 value 值的异步重载，保持本地状态与 draft 草稿同步
    React.useEffect(() => {
        setLocalValue(value !== undefined ? value : (schema.default || 0));
    }, [value, schema.default]);

    /**
     * 将输入框的原始文本解析为符合 Schema 类型的数值
     */
    const parseInputValue = (rawValue) => {
        if (rawValue === '' || rawValue === '-') return rawValue;
        const parsed = schema.type === 'integer' || schema.type === 'int'
            ? parseInt(rawValue, 10)
            : parseFloat(rawValue);
        return isNaN(parsed) ? 0 : parsed;
    };

    /**
     * 将有效的新值提交回父级草稿 state 树
     */
    const commitValue = (nextValue) => {
        setLocalValue(nextValue);
        onChange(nextValue);
    };

    return (
        <FieldRow depth={depth}>
            {/* 包含滑块时，适当收窄左侧说明宽度，为滑块腾出布局空间 */}
            <DescriptionSection schema={schema} fieldKey={fieldKey} flex={hasRange ? 5 : 8} />
            
            {/* 如果开启了滑块视图，渲染滑动条组件 */}
            {hasRange && (
                <Box className="config-field-slider-wrap">
                    <Slider
                        value={Number(localValue) || 0}
                        onChange={(e, nextValue) => setLocalValue(nextValue)}
                        onChangeCommitted={(e, nextValue) => commitValue(nextValue)}
                        min={min}
                        max={max}
                        step={step}
                        size="small"
                        valueLabelDisplay="auto"
                        className="config-field-slider"
                    />
                </Box>
            )}
            
            {/* 右侧数字输入控制台 */}
            <FieldControl>
                <TextField
                    fullWidth
                    size="small"
                    type="number"
                    value={localValue}
                    onChange={(e) => {
                        const nextValue = parseInputValue(e.target.value);
                        // 当用户在清空输入或输入负号时，在保持本地原始输入的同时，也需要向草稿状态同步更新对应的值，避免保存时残留无效旧数据
                        if (nextValue === '' || nextValue === '-') {
                            setLocalValue(nextValue);
                            onChange(nextValue);
                            return;
                        }
                        commitValue(nextValue);
                    }}
                    inputProps={{ min, max, step }}
                    variant="outlined"
                    sx={getConfigFieldInputSx()}
                />
            </FieldControl>
        </FieldRow>
    );
}

/**
 * 3. 多行列表/数组输入域 (ConfigListField)
 * 渲染为多行文本输入框，用户通过在输入框中换行（一行一个值）来维护后台的数组项（如 `target_sessions`）。
 */
function ConfigListField({ fieldKey, schema, value, onChange, depth }) {
    const { Box, TextField, Typography } = MaterialUI;
    const { getConfigFieldInputSx } = window.ConfigFieldLayout;
    return (
        <Box className={`config-field-block ${depth === 0 ? 'config-field-block--root' : ''}`}>
            {/* 顶栏描述信息 */}
            <Box className="config-field-block-head">
                <Typography variant="subtitle2" className="config-field-label">
                    {schema.description || fieldKey}
                </Typography>
                {schema.hint && (
                    <Typography variant="caption" color="text.secondary" className="config-field-hint">
                        {schema.hint}
                    </Typography>
                )}
            </Box>
            
            {/* 数组换行文本输入 */}
            <TextField
                fullWidth
                multiline
                rows={3}
                size="small"
                value={Array.isArray(value) ? value.join('\n') : ''}
                // 将用户输入按换行符拆分成数组并同步回草稿
                onChange={(e) => onChange(e.target.value.split('\n'))}
                placeholder="每一行代表一个独立的列表项"
                variant="outlined"
                sx={getConfigFieldInputSx()}
            />
        </Box>
    );
}

/**
 * 下拉框「展示标签 ↔ 内部存储值」别名映射表。
 *
 * 部分配置项（如本地强度体系 intensity_system）在前端 Schema 中声明中文标签
 * （options: 自动判定/中国烈度/日本震度），但后端配置校验器会将其规范化为
 * 英文内部值（auto/cenc/jma）持久化。若 Select 直接用存储值匹配 options，
 * 会因「值不在选项中」导致框内空白、默认值无法回显。
 *
 * 此处建立字段级别名表，实现双向转换：
 * - 中文标签 → 内部值：MenuItem value 与 Select 当前值统一使用内部值；
 * - 内部值 → 中文标签：仅用于下拉项的展示文本；
 * - 兼容历史遗留的中文配置值：回显时若存储值为中文标签，先映射为内部值。
 */
const SELECT_OPTION_VALUE_ALIASES = {
    intensity_system: {
        '自动判定': 'auto',
        '中国烈度': 'cenc',
        '日本震度': 'jma',
    },
};

/**
 * 4. 下拉单选枚举域 (ConfigSelectField)
 * 渲染为带有 Material-UI 下拉弹窗菜单的 Select 输入框，自动绑定 schema.options 的全部选项。
 */
function ConfigSelectField({ fieldKey, schema, value, onChange, depth }) {
    const { TextField, MenuItem } = MaterialUI;
    const { DescriptionSection, FieldRow, FieldControl, getConfigFieldInputSx } = window.ConfigFieldLayout;

    // 命中别名表的字段启用「内部值」模式：下拉选项 value 用内部值，展示文本用中文标签
    const aliases = SELECT_OPTION_VALUE_ALIASES[fieldKey] || null;

    /**
     * 将任意输入值归一化为 Select 可匹配的「内部值」（英文）：
     * - 命中别名表的字段：
     *   - 空值/未知值回退 schema.default 映射后的内部值（如 auto）
     *   - 已是内部值（auto/cenc/jma）直接返回
     *   - 兼容历史遗留的中文标签（如 自动判定），反查为内部值
     * - 普通字段直接返回原始值
     *
     * 保证 TextField.value 总能匹配某个 MenuItem.value，避免空白选中状态。
     */
    const normalizeValue = (raw) => {
        const fallbackRaw = schema.default !== undefined ? schema.default : (schema.options && schema.options[0] || '');
        const fallback = (aliases && aliases[fallbackRaw] !== undefined) ? aliases[fallbackRaw] : fallbackRaw;
        if (raw === undefined || raw === null || raw === '') return fallback;
        if (aliases) {
            if (aliases[raw] !== undefined) return aliases[raw]; // 中文标签 → 内部值
            if (Object.values(aliases).includes(raw)) return raw; // 已是内部值
            return fallback; // 未知值回退默认内部值
        }
        return raw;
    };

    /**
     * 将用户选择的选项值提交给父级草稿：
     * 选项的 value 已是英文内部值（auto/cenc/jma），直接透传，
     * 与后端校验器持久化的英文内部值保持一致。
     */
    const commitValue = (raw) => {
        onChange(raw);
    };

    const currentValue = normalizeValue(value);

    return (
        <FieldRow depth={depth}>
            <DescriptionSection schema={schema} fieldKey={fieldKey} />
            <FieldControl>
                <TextField
                    select
                    fullWidth
                    size="small"
                    value={currentValue}
                    onChange={(e) => commitValue(e.target.value)}
                    variant="outlined"
                    sx={getConfigFieldInputSx({ '& .MuiSelect-select': { textAlign: 'center', pr: '32px !important' } })}
                >
                    {schema.options.map((option) => {
                        // 选项的 value 用英文内部值以便稳定匹配，展示文本保持中文标签
                        const optionValue = aliases && aliases[option] !== undefined ? aliases[option] : option;
                        return (
                            <MenuItem key={optionValue} value={optionValue} className="config-field-menu-item">
                                {option}
                            </MenuItem>
                        );
                    })}
                </TextField>
            </FieldControl>
        </FieldRow>
    );
}

/**
 * 5. 单行/多行通用文本框 (ConfigTextField)
 * 默认的基础渲染单元。支持普通文本和 password 暗文。若判定为复杂长格式（如消息卡片 JSON 模板），则自动伸展为 rows=3 的高空间多行文本域。
 */
function ConfigTextField({ fieldKey, schema, value, onChange, depth }) {
    const { Box, TextField, Typography } = MaterialUI;
    const { DescriptionSection, FieldRow, FieldControl, getConfigFieldInputSx } = window.ConfigFieldLayout;
    
    // 判断是否需要进行明文屏蔽
    const inputType = schema.type === 'password' ? 'password' : 'text';
    
    // 智能排版判断：非密码项 且 (提示语特别长 或 字段含有消息卡片、正则等关键字) 时，自动展开为大文本域
    const multiline = inputType !== 'password' && (
        (schema.hint && schema.hint.length > 100) || 
        ['format', 'template', 'pattern', 'message', 'body', 'content'].some((token) => fieldKey.includes(token))
    );

    // 渲染多行文本排版架构
    if (multiline) {
        return (
            <Box className={`config-field-block ${depth === 0 ? 'config-field-block--root' : ''}`}>
                <Box className="config-field-block-head">
                    <Typography variant="subtitle2" className="config-field-label">
                        {schema.description || fieldKey}
                    </Typography>
                    {schema.hint && (
                        <Typography variant="caption" color="text.secondary" className="config-field-hint">
                            {schema.hint}
                        </Typography>
                    )}
                </Box>
                <TextField
                    fullWidth
                    multiline
                    rows={3}
                    value={value !== undefined ? value : (schema.default || '')}
                    onChange={(e) => onChange(e.target.value)}
                    variant="outlined"
                    sx={getConfigFieldInputSx()}
                />
            </Box>
        );
    }

    // 默认单行输入架构
    return (
        <FieldRow depth={depth}>
            <DescriptionSection schema={schema} fieldKey={fieldKey} />
            <FieldControl>
                <TextField
                    fullWidth
                    size="small"
                    type={inputType}
                    // 密码防浏览器自动填充
                    autoComplete={schema.type === 'password' ? 'new-password' : 'off'}
                    value={value !== undefined ? value : (schema.default || '')}
                    onChange={(e) => onChange(e.target.value)}
                    variant="outlined"
                    sx={getConfigFieldInputSx()}
                />
            </FieldControl>
        </FieldRow>
    );
}

// 注册至 window 全局供 ConfigField.jsx 进行动态分发
window.ConfigBooleanField = ConfigBooleanField;
window.ConfigNumberField = ConfigNumberField;
window.ConfigListField = ConfigListField;
window.ConfigSelectField = ConfigSelectField;
window.ConfigTextField = ConfigTextField;
