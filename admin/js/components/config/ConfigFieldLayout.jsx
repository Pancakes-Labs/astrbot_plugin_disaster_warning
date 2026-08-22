/**
 * 全局配置组图标映射表
 * 将配置项中高频使用的 key 绑定到生动的 Emoji 图标上，丰富配置页面视觉效果。
 */
const CONFIG_ICONS = {
    enabled: '🔌',
    admin_users: '👥',
    target_sessions: '📨',
    offline_notification_sessions: '⚠️',
    display_timezone: '🌍',
    data_sources: '📡',
    local_monitoring: '📍',
    earthquake_filters: '🔍',
    strategies: '🧠',
    push_frequency_control: '⏱️',
    message_format: '🎨',
    weather_config: '⛈️',
    tsunami_config: '🌊',
    typhoon_config: '🌀',
    websocket_config: '🔌',
    web_admin: '💻',
    notification_settings: '🔔',
    debug_config: '🐛',
    telemetry_config: '📊',
};

/**
 * 匹配文本开头 Emoji 表情符号的正则表达式，支持多重修饰字符
 */
const LEADING_EMOJI_REGEX = /^\s*(?:\p{Extended_Pictographic}(?:\uFE0F|\u200D\p{Extended_Pictographic})*)\s*/u;

/**
 * 辅助函数：剥离文本开头的 Emoji 图标，主要用于折叠面板标题的清洁提取
 * 
 * @param {string} text 待处理字符串
 * @returns {string} 处理后的字符串
 */
const stripLeadingEmoji = (text) => typeof text === 'string' ? text.replace(LEADING_EMOJI_REGEX, '').trimStart() : text;

/**
 * 配置字段描述区域组件 (DescriptionSection)
 * 渲染配置项的描述文本（通常是 Schema 的 description 翻译）与辅助提示信息。
 * 
 * @param {Object} props
 * @param {Object} props.schema 包含描述和提示的 JSON Schema 对象
 * @param {string} props.fieldKey 降级备用展示的字段 Key 名
 * @param {number} [props.flex=8] CSS Flex-Grow 的占比因子，以平衡左侧说明和右侧控件的比例
 */
function DescriptionSection({ schema, fieldKey, flex = 8 }) {
    const { Box, Typography } = MaterialUI;
    return (
        <Box className="config-field-description" style={{ '--config-field-description-flex': flex }}>
            {/* 字段描述中文主标题 */}
            <Typography variant="subtitle2" className="config-field-label">
                {schema.description || fieldKey}
            </Typography>
            {/* 字段气泡或下方补充提示文本 */}
            {schema.hint && (
                <Typography variant="caption" color="text.secondary" className="config-field-hint">
                    {schema.hint}
                </Typography>
            )}
        </Box>
    );
}

/**
 * 字段物理排版行组件 (FieldRow)
 * 负责限制单行配置项在不同嵌套层级下的缩进样式。
 * 
 * @param {Object} props
 * @param {React.ReactNode} props.children 行内子组件
 * @param {number} props.depth 嵌套深度 (0 代表根节点，非 0 将去除根边框和额外留白)
 */
function FieldRow({ children, depth }) {
    const { Box } = MaterialUI;
    return <Box className={`config-field-row ${depth === 0 ? 'config-field-row--root' : ''}`}>{children}</Box>;
}

/**
 * 字段交互控件包裹容器 (FieldControl)
 * 提供表单输入控件（如 TextField, Select）的标准定位样式。
 * 
 * @param {Object} props
 * @param {React.ReactNode} props.children 控制控件
 * @param {string} [props.className=''] 额外注入的类名
 */
function FieldControl({ children, className = '' }) {
    const { Box } = MaterialUI;
    return <Box className={`config-field-control ${className}`}>{children}</Box>;
}

/**
 * 获取 MaterialUI 文本输入框 (TextField) 的公共样式覆写配置，以统一全站 MD3 圆角与聚焦行为。
 * 
 * @param {Object} [extra={}] 需要合并的额外 CSS 属性
 * @returns {Object} 供 sx 属性调用的样式声明对象
 */
function getConfigFieldInputSx(extra = {}) {
    return {
        '& .MuiOutlinedInput-root': {
            borderRadius: 1.5,
            bgcolor: 'background.paper',
            '&.Mui-focused > fieldset': { borderColor: 'primary.main' },
        },
        ...extra,
    };
}

// 暴露到全局 ConfigFieldLayout 以供其他配置渲染子模块共享
window.ConfigFieldLayout = {
    CONFIG_ICONS,
    stripLeadingEmoji,
    DescriptionSection,
    FieldRow,
    FieldControl,
    getConfigFieldInputSx,
};
