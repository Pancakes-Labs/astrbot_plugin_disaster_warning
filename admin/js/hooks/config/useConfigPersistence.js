/**
 * @file useConfigPersistence.js
 * @description 处理全局配置及会话差异覆写配置保存、清空、恢复默认与重置动作的网络持久化 Hook。
 * 
 * 核心持久化策略与安全控制：
 * 1. 字段剪裁与归一化：为了降低数据传输负荷与存储冗余，保存时根据当前展示 Schema 
 *    裁剪出只读或由于模式隔离无需上传的冗余字段（特别是在会话差异覆写模式下，只保留当前会话覆写的值，不上传未修改的空值）。
 * 2. 深度配置比对 (Deep Diff)：在高级版本中支持深层次数据结构变更差异化合并。
 * 3. 事务性清理：在保存成功、完全清空、恢复默认或一键撤销后，自动对本地 localStorage 进行清理，
 *    删除草稿文件，重设 isDirty 为 false。
 */
function useConfigPersistence({
    api,                  // 后台配置 API
    draft,                // 本地草稿工具
    mode,                 // global / session 模式
    selectedSession,      // 当前会话 UUID / 标识
    config,               // 当前内存中的配置副本
    getVisibleSchema,     // 可见 Schema 过滤器
    pickConfigBySchema,   // 依据 Schema 剪裁函数
    cleanConfig,          // 清理空属性或格式化类型函数
    generateDefaults,     // 生产出厂默认值函数
    markConfigChanged,    // 触发局部配置修改 setter
    loadConfig,           // 配置拉取调度函数
    loadSessions,         // 会话拉取调度函数
    setConfig,            // 完整配置覆盖 setter
    setSaving,            // 保存中 pending 控制器
    setDirty,             // 脏状态控制器
    showToast,            // Toast 全局消息提示
    triggerReload,        // 外部重载同步触发器
}) {
    /**
     * 编译构建待提交的载荷数据，过滤与当前展示模式冲突的配置子集
     */
    const buildSavePayload = React.useCallback(() => {
        const visibleSchema = getVisibleSchema(mode);
        // 会话模式下仅截取并过滤出差异覆写表单子元素，全局模式透传所有
        let configToSave = mode === 'session'
            ? pickConfigBySchema(config, visibleSchema)
            : config;

        // 会话保存时强制剥离仅全局可改字段（如 S-Net 轮询间隔）
        if (mode === 'session' && window.ConfigSchemaUtils && window.ConfigSchemaUtils.stripGlobalOnlyFields) {
            configToSave = window.ConfigSchemaUtils.stripGlobalOnlyFields(configToSave);
        }

        return {
            visibleSchema,
            cleanedConfig: cleanConfig(configToSave),
        };
    }, [cleanConfig, config, getVisibleSchema, mode, pickConfigBySchema]);

    /**
     * 深度差分对比算子
     * 计算 baseValue 和 targetValue 之间的差异并返回差异对象，若完全等价则输出 undefined
     */
    const computeDiff = React.useCallback((baseValue, targetValue) => {
        if (Array.isArray(baseValue) && Array.isArray(targetValue)) {
            if (baseValue.length !== targetValue.length) return targetValue;
            return baseValue.every((item, index) => JSON.stringify(item) === JSON.stringify(targetValue[index]))
                ? undefined
                : targetValue;
        }

        if (
            baseValue
            && targetValue
            && typeof baseValue === 'object'
            && typeof targetValue === 'object'
            && !Array.isArray(baseValue)
            && !Array.isArray(targetValue)
        ) {
            const next = {};
            Object.keys(targetValue).forEach((key) => {
                const diff = computeDiff(baseValue[key], targetValue[key]);
                if (diff !== undefined) {
                    next[key] = diff;
                }
            });
            return Object.keys(next).length ? next : undefined;
        }

        return JSON.stringify(baseValue) === JSON.stringify(targetValue)
            ? undefined
            : targetValue;
    }, []);

    /**
     * 一键保存配置修改
     */
    const handleSave = React.useCallback(async () => {
        setSaving(true);
        try {
            const { cleanedConfig } = buildSavePayload();
            if (mode === 'session') {
                if (!selectedSession) {
                    showToast('请先选择会话', 'warning');
                    return;
                }

                // 会话表单展示的是生效配置；以 effective 模式提交，
                // 由后端 compute_diff 生成最小 override（可显式写入 false）。
                await api.updateSessionConfig(selectedSession, {
                    mode: 'effective',
                    effective: cleanedConfig,
                });
                showToast('会话差异配置已保存', 'success');
                setDirty(false);
                localStorage.removeItem(draft.getDraftKey(mode, selectedSession)); // 保存完毕，移除本会话草稿
                await loadSessions();
                await loadConfig(mode, selectedSession);
            } else {
                // 提交全局完整的配置表单
                await api.updateConfig(cleanedConfig);
                showToast('全局配置已保存', 'success');
                setDirty(false);
                setConfig(cleanedConfig);
                localStorage.removeItem(draft.getDraftKey(mode, selectedSession)); // 移除全局草稿
            }
        } catch (e) {
            console.error('保存配置失败', e);
            showToast('保存配置失败,请检查控制台', 'error');
        } finally {
            setSaving(false);
        }
    }, [api, buildSavePayload, draft, loadConfig, loadSessions, mode, selectedSession, setConfig, setDirty, setSaving, showToast]);

    /**
     * 清空会话的独立覆盖参数，回归全局默认状态
     */
    const handleResetOverride = React.useCallback(async () => {
        if (!selectedSession) {
            showToast('请先选择会话', 'warning');
            return;
        }
        if (!confirm('确定要清空该会话的差异配置吗？\n\n清空后将完全继承全局默认配置。')) {
            return;
        }
        setSaving(true);
        try {
            await api.resetSessionConfig(selectedSession);
            showToast('会话差异配置已清空', 'success');
            localStorage.removeItem(draft.getDraftKey(mode, selectedSession));
            await loadSessions();
            await loadConfig(mode, selectedSession);
        } catch (e) {
            console.error('清空会话差异配置失败', e);
            showToast('清空会话差异配置失败', 'error');
        } finally {
            setSaving(false);
        }
    }, [api, draft, loadConfig, loadSessions, mode, selectedSession, setSaving, showToast]);

    /**
     * 恢复出厂默认值
     */
    const handleRestoreDefaults = React.useCallback(() => {
        if (!confirm('⚠️ 确定要恢复出厂设置吗？\n\n这将覆盖当前所有配置项为默认值（需要点击“保存配置”才能生效）。')) {
            return;
        }
        const defaults = generateDefaults(getVisibleSchema());
        markConfigChanged((prev) => ({ ...prev, ...defaults }));
        localStorage.removeItem(draft.getDraftKey(mode, selectedSession));
    }, [draft, generateDefaults, getVisibleSchema, markConfigChanged, mode, selectedSession]);

    /**
     * 放弃本地修改，全量撤回已保存状态
     */
    const handleRevert = React.useCallback(() => {
        if (!confirm('确定要撤销所有未保存的更改吗？\n\n这将重新加载服务器上已保存的配置。')) {
            return;
        }
        localStorage.removeItem(draft.getDraftKey(mode, selectedSession));
        setDirty(false);
        triggerReload();
        loadConfig(mode, selectedSession);
    }, [draft, loadConfig, mode, selectedSession, setDirty, triggerReload]);

    return {
        buildSavePayload,
        handleSave,
        handleResetOverride,
        handleRestoreDefaults,
        handleRevert,
    };
}
