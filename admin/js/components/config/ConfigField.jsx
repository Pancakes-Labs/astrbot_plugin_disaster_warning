/**
 * 配置字段分发器组件 (ConfigField)
 * 作为动态配置渲染的核心路由分发枢纽，根据服务端的 Schema 类型定义，
 * 将配置项导向并渲染为对应的 React 组件（如对象组、布尔开关、数字滑块、下拉选择、文本或多行列表框）。
 *
 * @param {Object} props
 * @param {string} props.fieldKey 配置项字段名 (例如 'display_timezone')
 * @param {Object} props.schema 描述当前字段类型的 JSON Schema 元数据定义
 * @param {any} props.value 当前字段在本地草稿中的值
 * @param {Function} props.onChange 字段值发生变化时的回调函数，接收新值作为参数
 * @param {number} [props.depth=0] 嵌套树深度，根节点为 0
 * @param {string} [props.path=''] 嵌套路径标识，例如 'web_admin.password'，用作折叠展开的 Key
 * @param {string[]} [props.expandedKeys=[]] 当前已展开的所有嵌套对象路径列表
 * @param {Function} [props.onToggleExpand] 切换嵌套对象折叠展开的回调函数
 */
function ConfigField({ 
    fieldKey, 
    schema, 
    value, 
    onChange, 
    depth = 0, 
    path = '', 
    expandedKeys = [], 
    onToggleExpand = () => {} 
}) {
    // 拼接得到当前字段在配置树中的绝对路径路径标识
    const currentPath = path ? `${path}.${fieldKey}` : fieldKey;

    // 特殊安全防护设计：允许 'web_admin.password' 与 'data_sources.eqsc.refresh_token' 敏感字段
    // 即使标记为 hidden 也照常在页面上渲染，但会被强制转换为 password 输入框（掩码显示，防泄露）
    const allowHiddenPasswordField =
        currentPath === 'web_admin.password' ||
        currentPath === 'data_sources.eqsc.refresh_token';
    
    // 如果 Schema 不存在，或者是隐藏字段且非受信任的密码字段，则返回 null 不渲染
    if (!schema || (schema.hidden && !allowHiddenPasswordField)) return null;

    // 解析最终渲染所使用的 Schema 属性
    const resolvedSchema = allowHiddenPasswordField ? { ...schema, type: 'password' } : schema;

    // 1. 若当前字段为嵌套的 Object 复杂对象组，则调用 ConfigObjectGroup 渲染为折叠风琴面板
    if (resolvedSchema.type === 'object' && resolvedSchema.items) {
        return (
            <ConfigObjectGroup 
                fieldKey={fieldKey} 
                schema={resolvedSchema} 
                value={value} 
                onChange={onChange} 
                depth={depth} 
                path={currentPath} 
                expandedKeys={expandedKeys} 
                onToggleExpand={onToggleExpand} 
            />
        );
    }

    // 2. 若字段为布尔型，渲染为 Switch 开关组件
    if (resolvedSchema.type === 'bool' || resolvedSchema.type === 'boolean') {
        return (
            <ConfigBooleanField 
                fieldKey={fieldKey} 
                schema={resolvedSchema} 
                value={value} 
                onChange={onChange} 
                depth={depth} 
            />
        );
    }

    // 3. 若字段为数字型（支持整数、浮点数、双精度浮点数等），渲染为带数字微调或滑块的输入组件
    if (['integer', 'int', 'number', 'float', 'double'].includes(resolvedSchema.type)) {
        return (
            <ConfigNumberField 
                fieldKey={fieldKey} 
                schema={resolvedSchema} 
                value={value} 
                onChange={onChange} 
                depth={depth} 
            />
        );
    }

    // 4. 若字段为列表/数组型，渲染为按行分割的多行文本框组件
    if (resolvedSchema.type === 'list' || resolvedSchema.type === 'array') {
        return (
            <ConfigListField 
                fieldKey={fieldKey} 
                schema={resolvedSchema} 
                value={value} 
                onChange={onChange} 
                depth={depth} 
            />
        );
    }

    // 5. 若 Schema 显式定义了 options 枚举数组，渲染为单选下拉下拉列表 (Select)
    if (resolvedSchema.options && Array.isArray(resolvedSchema.options)) {
        return (
            <ConfigSelectField 
                fieldKey={fieldKey} 
                schema={resolvedSchema} 
                value={value} 
                onChange={onChange} 
                depth={depth} 
            />
        );
    }

    // 6. 默认降级方案：渲染为标准单行文本框 (TextField)
    return (
        <ConfigTextField 
            fieldKey={fieldKey} 
            schema={resolvedSchema} 
            value={value} 
            onChange={onChange} 
            depth={depth} 
        />
    );
}
