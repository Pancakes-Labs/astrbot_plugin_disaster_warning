/**
 * @file useConfigLoader.js
 * @description 配置编辑器数据拉取与时序处理 Hook。
 * 
 * 核心技术细节：
 * 1. 时序保护 (Race Conditions Avoidance)：在快速切换“全局”与“会话”配置，或者快速点选不同会话群组时，
 *    由于网络请求的响应到达先后不一，可能会发生“旧请求覆盖新渲染”的问题。
 *    为此，我们设计了自增的计数器。每次发起异步加载前，生成局部快照。
 *    只有当 API 回包时与当前的全局最新计数器完全吻合时，才允许写入状态树，从而切断过期异步响应的干扰。
 * 2. 状态恢复逻辑：拉取到后端物理配置后，优先检查本地是否存在未保存的草稿。若有，则将其合并为 finalConfig 并将 Dirty 设为 true。
 */
function useConfigLoader({
    api,                   // 配置交互 API 实例
    draft,                 // 本地草稿控制器
    schema,                // 表单元描述 Schema
    mode,                  // 当前模式：global / session
    selectedSession,       // 选中的 Session ID
    getVisibleSchema,      // 根据模式过滤 Schema 的方法
    getAllExpandablePaths, // 提取所有风琴面板可折叠路径列表的工具函数
    showToast,             // 弹出通知接口
    setConfig,             // 设置最新配置体
    setExpandedKeys,       // 展开折叠 Key 更新器
    setLoading,            // 全局加载动画锁
    setSessionLoading,     // 局部切换群组的加载动画锁
    setLoadError,          // 加载异常说明更新器
    setSessions,           // 会话群组列表 setter
    setSelectedSession,    // 选中会话 setter
    setDirty,              // 脏更改 setter
    loadConfigSeqRef,      // 跨渲染周期的时序 Ref 计数器
}) {
    /**
     * 业务状态归一化决策
     * 判定是否使用本地缓存草稿、读取或还原手风琴展开历史，拦截非法接口回包。
     */
    const resolveLoadedConfigState = React.useCallback((configData, currentMode, currentSession, schemaOverride) => {
        let finalConfig = configData;
        let usedDraft = false;

        // 仅在全局配置模式下启用未存草稿热恢复，避免会话群组覆写发生冲突
        if (currentMode === 'global') {
            const draftConfig = draft.readJson(draft.getDraftKey(currentMode, currentSession));
            if (draftConfig && typeof draftConfig === 'object') {
                finalConfig = draftConfig;
                usedDraft = true;
            }
        }

        // 读取历史手风琴折叠状态，若无，则默认将 Schema 下所有对象容器节点全部展开
        const cachedExpanded = draft.readJson(draft.getExpandedKey(currentMode, currentSession));
        const finalExpandedKeys = Array.isArray(cachedExpanded)
            ? cachedExpanded
            : getAllExpandablePaths(getVisibleSchema(currentMode, schemaOverride));

        if (!finalConfig || typeof finalConfig !== 'object' || Array.isArray(finalConfig)) {
            throw new Error(currentMode === 'session'
                ? '会话差异配置加载失败：服务端未返回有效配置对象'
                : '全局配置加载失败：服务端未返回有效配置对象');
        }

        return {
            finalConfig,
            finalExpandedKeys,
            usedDraft,
        };
    }, [draft, getAllExpandablePaths, getVisibleSchema]);

    /**
     * 设置会话配置列表并维持合理的默认值
     */
    const applySessionList = React.useCallback((sessionList = []) => {
        setSessions(sessionList);
        setSelectedSession((prev) => {
            if (prev && sessionList.some((item) => item.session === prev)) {
                return prev;
            }
            return sessionList[0]?.session || '';
        });
        return sessionList;
    }, [setSelectedSession, setSessions]);

    /**
     * 异步拉取所有存在差异覆盖的聊天群组列表
     */
    const loadSessions = React.useCallback(async () => {
        try {
            const result = await api.listSessionConfigs();
            const sessionList = result?.sessions || [];
            return applySessionList(sessionList);
        } catch (e) {
            console.error('加载会话列表失败', e);
            showToast('加载会话列表失败,请检查控制台', 'error');
            return applySessionList([]);
        }
    }, [api, applySessionList, showToast]);

    /**
     * 基础网络拉取，解耦会话配置和全局配置的端点路径
     */
    const fetchRawConfigData = React.useCallback(async (currentMode = mode, currentSession = selectedSession, requestSeq = loadConfigSeqRef.current) => {
        if (currentMode === 'session') {
            if (!currentSession) {
                if (requestSeq === loadConfigSeqRef.current) {
                    setConfig(null);
                }
                return null;
            }
            setSessionLoading(true);
            const sessionData = await api.getSessionConfig(currentSession);
            // 会话模式必须编辑“生效配置”(global + override)，
            // 否则未写入 override 的字段会显示成 schema 默认关，
            // 但运行时仍继承全局开启，导致“界面关了却仍推送”。
            if (sessionData?.effective && typeof sessionData.effective === 'object') {
                return sessionData.effective;
            }
            return sessionData?.override || {};
        }
        return await api.getFullConfig();
    }, [api, loadConfigSeqRef, mode, selectedSession, setConfig, setSessionLoading]);

    /**
     * 时序保护下的配置加载流程编排
     */
    const loadConfig = React.useCallback(async (currentMode = mode, currentSession = selectedSession, schemaOverride = schema) => {
        if (!schemaOverride) return;
        setLoadError('');
        const requestSeq = ++loadConfigSeqRef.current; // 时序自增
        setLoading(true);
        try {
            const configData = await fetchRawConfigData(currentMode, currentSession, requestSeq);
            if (configData === null) {
                return;
            }

            // 时序拦截：如果在请求期间用户进行了模式切换或会话重选，直接弃用该响应
            if (requestSeq !== loadConfigSeqRef.current) return;

            const {
                finalConfig,
                finalExpandedKeys,
                usedDraft,
            } = resolveLoadedConfigState(configData, currentMode, currentSession, schemaOverride);

            setDirty(usedDraft);
            setConfig(finalConfig);
            setExpandedKeys(finalExpandedKeys);
        } catch (e) {
            if (requestSeq === loadConfigSeqRef.current) {
                console.error('加载配置失败', e);
                setLoadError(e?.message || '配置加载失败，请检查接口返回与服务端日志');
                showToast('加载配置失败,请检查控制台', 'error');
                setConfig(null);
            }
        } finally {
            if (requestSeq === loadConfigSeqRef.current) {
                setSessionLoading(false);
                setLoading(false);
            }
        }
    }, [fetchRawConfigData, loadConfigSeqRef, mode, resolveLoadedConfigState, schema, selectedSession, setConfig, setDirty, setExpandedKeys, setLoadError, setLoading, setSessionLoading, showToast]);

    return {
        resolveLoadedConfigState,
        applySessionList,
        loadSessions,
        fetchRawConfigData,
        loadConfig,
    };
}
