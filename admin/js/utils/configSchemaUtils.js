(() => {
    // 定义在会话群组差异覆写视图中需要隐藏的高级敏感参数 Key
    const CONFIG_SESSION_DIFF_HIDDEN_KEYS = new Set([
        'enabled', 'admin_users', 'target_sessions', 'offline_notification_sessions', 'display_timezone',
        'web_admin', 'notification_settings', 'websocket_config', 'debug_config', 'telemetry_config'
    ]);
    
    // 定义会话覆写专属的局部参数 Key（如单群推送开关、会话备注名）
    const CONFIG_SESSION_ONLY_KEYS = new Set(['push_enabled', 'session_name']);

    // 仅允许全局配置修改的嵌套路径（会话模式隐藏且保存时剥离）
    // S-Net 轮询间隔、EQSC 通道鉴权参数影响全局运行态；
    // eqsc.typhoon 允许会话差异（部分会话只要 Fan，部分要 EQSC 富化）。
    // 允许会话级覆盖，会话级关闭仅屏蔽该会话内全部子源，不影响全局通道建连。
    const CONFIG_SESSION_GLOBAL_ONLY_PATHS = [
        ['data_sources', 'snet', 'poll_interval_seconds'],
        ['data_sources', 'eqsc', 'refresh_token'],
        // EQSC 统一轮询间隔影响全局运行态，会话模式隐藏且保存时剥离
        ['data_sources', 'eqsc', 'poll_interval_seconds'],
        // FAN Studio 鉴权影响全局 WebSocket 建连，会话模式隐藏且保存时剥离
        ['data_sources', 'fan_studio', 'api_key'],
        // 浏览器页面池大小影响全局运行态，会话模式隐藏且保存时剥离
        ['message_format', 'browser_pool_size'],
        // 是否忽略 HTTPS 证书错误是浏览器启动级配置，会话模式隐藏且保存时剥离
        ['message_format', 'browser_ignore_https_errors'],
    ];

    /**
     * 递归获取 Schema 下所有可折叠对象节点的分支路径列表
     */
    function getAllExpandablePaths(schema, prefix = '') {
        let paths = [];
        Object.entries(schema || {}).forEach(([key, value]) => {
            const currentPath = prefix ? `${prefix}.${key}` : key;
            // 若节点是对象类型且含有子项 items，则属于可折叠面板路径
            if (value.type === 'object' && value.items) {
                paths.push(currentPath);
                // 深度递归遍历子节点
                paths = paths.concat(getAllExpandablePaths(value.items, currentPath));
            }
        });
        return paths;
    }

    /**
     * 判定获取的 Schema 配置是否为合法的对象结构
     */
    function isValidSchemaObject(value) {
        return !!(value && typeof value === 'object' && !Array.isArray(value) && !value.error && Object.keys(value).length > 0);
    }

    /**
     * 深度清洗表单配置项
     * 
     * 清洗细节：
     * - 去除字符串值的首尾多余空格占位。
     * - 自动过滤清空数组中的空字符串值，防止提交冗余无效数据。
     */
    function cleanConfig(obj) {
        if (Array.isArray(obj)) {
            return obj
                .map((item) => typeof item === 'string' ? item.trim() : item)
                .filter((item) => item !== ''); // 剔除空字符串
        }
        if (obj && typeof obj === 'object') {
            const next = {};
            Object.keys(obj).forEach((key) => { next[key] = cleanConfig(obj[key]); });
            return next;
        }
        return obj;
    }

    /**
     * 根据 Schema 递归生成一份完整配置的初始默认值副本
     */
    function generateDefaults(schema) {
        const config = {};
        Object.entries(schema || {}).forEach(([key, value]) => {
            config[key] = value.type === 'object' && value.items
                ? generateDefaults(value.items) // 递归生成对象子项默认值
                : (value.default !== undefined ? value.default : null); // 使用 schema 默认值或 null
        });
        return config;
    }

    /**
     * 依据给定的 Schema 对象骨架，从源配置数据中提取对应的参数值
     */
    function pickConfigBySchema(sourceConfig, schemaObject) {
        if (!sourceConfig || typeof sourceConfig !== 'object' || Array.isArray(sourceConfig)) return {};
        const picked = {};
        // 匹配存在于 schema 骨架中的属性
        Object.keys(schemaObject || {}).forEach((key) => {
            if (Object.prototype.hasOwnProperty.call(sourceConfig, key)) picked[key] = sourceConfig[key];
        });
        // 强制提取会话覆写专属字段
        CONFIG_SESSION_ONLY_KEYS.forEach((key) => {
            if (Object.prototype.hasOwnProperty.call(sourceConfig, key)) picked[key] = sourceConfig[key];
        });
        return picked;
    }

    /**
     * 从对象中剥离仅全局可改的嵌套字段。
     */
    function stripGlobalOnlyFields(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return value;
        const next = JSON.parse(JSON.stringify(value));
        CONFIG_SESSION_GLOBAL_ONLY_PATHS.forEach((path) => {
            let cursor = next;
            const stack = [];
            for (let i = 0; i < path.length; i += 1) {
                const key = path[i];
                if (!cursor || typeof cursor !== 'object' || Array.isArray(cursor) || !(key in cursor)) {
                    return;
                }
                stack.push([cursor, key]);
                cursor = cursor[key];
            }
            if (!stack.length) return;
            const [leafParent, leafKey] = stack[stack.length - 1];
            delete leafParent[leafKey];
            for (let i = stack.length - 2; i >= 0; i -= 1) {
                const [parent, key] = stack[i];
                const child = parent[key];
                if (child && typeof child === 'object' && !Array.isArray(child) && Object.keys(child).length === 0) {
                    delete parent[key];
                } else {
                    break;
                }
            }
        });
        return next;
    }

    /**
     * 从 Schema 中移除仅全局可改的嵌套字段定义。
     */
    function stripGlobalOnlyFromSchema(schemaObject) {
        if (!schemaObject || typeof schemaObject !== 'object' || Array.isArray(schemaObject)) {
            return schemaObject;
        }
        const next = JSON.parse(JSON.stringify(schemaObject));
        CONFIG_SESSION_GLOBAL_ONLY_PATHS.forEach((path) => {
            let cursor = next;
            for (let i = 0; i < path.length; i += 1) {
                const key = path[i];
                if (!cursor || typeof cursor !== 'object') return;
                // schema 对象节点的子项在 items 下
                if (cursor.type === 'object' && cursor.items && typeof cursor.items === 'object') {
                    cursor = cursor.items;
                }
                if (i === path.length - 1) {
                    if (cursor && typeof cursor === 'object') {
                        delete cursor[key];
                    }
                    return;
                }
                const child = cursor[key];
                if (!child || typeof child !== 'object') return;
                cursor = child;
            }
        });
        return next;
    }

    /**
     * 根据当前的配置编辑模式获取过滤后的可见 Schema
     * 
     * 过滤策略：
     * - 全局模式下直接返回完整 Schema。
     * - 会话差异覆写模式下，剔除敏感及不适用于单群的字段，并动态塞入局部单会话推送开关的表单描述定义。
     * - 同时剥离仅全局可改的嵌套字段（如 S-Net 轮询间隔）。
     */
    function getVisibleSchema(schemaArg, currentMode = 'global') {
        if (!schemaArg || currentMode !== 'session') return schemaArg;
        // 剔除隐藏字段
        const visible = Object.fromEntries(
            Object.entries(schemaArg).filter(([key]) => !CONFIG_SESSION_DIFF_HIDDEN_KEYS.has(key))
        );
        // 动态注入会话专用开关属性
        if (!visible.push_enabled) {
            visible.push_enabled = {
                type: 'bool',
                description: '单会话推送开关',
                hint: '仅作用于当前会话，用于控制该会话是否接收本插件推送。',
                default: true,
            };
        }
        // 会话模式隐藏全局-only 嵌套字段
        return stripGlobalOnlyFromSchema(visible);
    }

    // 绑定至全局
    window.ConfigSchemaUtils = {
        CONFIG_SESSION_DIFF_HIDDEN_KEYS,
        CONFIG_SESSION_ONLY_KEYS,
        CONFIG_SESSION_GLOBAL_ONLY_PATHS,
        getAllExpandablePaths,
        isValidSchemaObject,
        cleanConfig,
        generateDefaults,
        pickConfigBySchema,
        stripGlobalOnlyFields,
        stripGlobalOnlyFromSchema,
        getVisibleSchema,
    };
})();
