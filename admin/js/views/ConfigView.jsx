/**
 * 模块名称：配置管理视图组件
 * 功能描述：作为配置管理面板的顶层视图容器，负责渲染整个参数配置页面的外壳结构。
 *
 * 双栏布局（同层级并列）：
 * - 左侧 2/3：ConfigRenderer（配置表单编辑器，内部持有 config 草稿状态）
 * - 右侧 1/3：PushPreviewPanel（实时推文预览，数据源滑条 + 过滤判定）
 *
 * 数据桥接：ConfigRenderer 通过 onConfigDraftChange 回调上报最新配置草稿，
 * ConfigView 将其注入 usePushPreview，实现"左侧编辑 → 右侧实时预览"联动。
 *
 * 状态提升（重构）：
 * - useConfigEditor 从 ConfigRenderer 提升至本视图层，顶栏可直接消费
 *   mode / sessions / selectedSession 等状态，将「全局配置 / 会话差异配置」
 *   切换控件渲染进顶栏替代静态标题，省去编辑器内部一整层工具栏，主视图更高。
 * - 编辑器状态整体以 editor 对象下发给 ConfigRenderer（保持单一数据源）。
 */

const { Typography, ToggleButton, ToggleButtonGroup, TextField, MenuItem, Chip } = MaterialUI;
const { useState, useCallback } = React;

// 会话差异配置可覆写字段（顶层键）→ 中文展示名映射。
const OVERRIDE_KEY_LABELS = {
    enabled: '启用灾害预警插件',
    display_timezone: '默认显示时区',
    data_sources: '📡 数据源配置',
    earthquake_filters: '🔍 地震信息过滤配置',
    local_monitoring: '📍 本地烈度监控配置',
    message_format: '🎨 预警消息格式配置',
    push_frequency_control: '⏱️ 推送频率控制',
    strategies: '🧠 高级策略配置',
    weather_config: '⛈️ 气象预警配置',
    typhoon_config: '🌀 台风信息配置',
    tsunami_config: '🌊 海啸信息配置',
    push_enabled: '会话推送开关',
    session_name: '🏷️ 会话备注名',
};

// 翻译 override 键列表为中文（未知键保留原样）
function translateOverrideKeys(keys) {
    return (keys || []).map((key) => OVERRIDE_KEY_LABELS[key] || key);
}

/**
 * 配置管理视图主组件
 * 构建包含装饰条、标题栏以及双栏主体的表单外壳
 */
function ConfigView() {
    // 实例化轻量 Toast 控制器（提升到视图层，供 useConfigEditor 使用）
    const { showToast } = useToast();

    // 编辑器状态中枢：模式/会话/草稿/折叠/保存等全部状态与控制器
    const editor = useConfigEditor(showToast);

    // 从 editor 中解构预览联动所需的草稿与作用域信息
    const { mode, selectedSession, config, loading } = editor;

    // 接收 ConfigRenderer 上报的配置草稿（供预览使用）
    const [draftState, setDraftState] = useState(null);
    const [draftSeq, setDraftSeq] = useState(0);

    // 配置作用域键：mode + 会话 的组合。
    // 全局配置 / 不同会话差异配置各自独立，切换作用域时旧配置的预览必须立即作废，
    // 避免异步加载间隙用上一个作用域的配置评估（多会话/全局-会话切换混淆的根因）。
    const scopeKey = `${mode || 'global'}|${mode === 'session' ? (selectedSession || '') : ''}`;

    // ConfigRenderer 回调：上报最新 config / mode / selectedSession / ready
    const handleDraftChange = useCallback((payload) => {
        setDraftState(payload);
        setDraftSeq((prev) => prev + 1); // 每次变化递增，强制预览刷新
    }, []);

    // 实时推文预览：draftState.config 变化 → 防抖 → 生成预览
    const { usePushPreview } = window;
    const previewState = usePushPreview({
        runtimeConfig: draftState?.config || null,
        targetSession: draftState?.mode === 'session' ? (draftState?.selectedSession || '') : '',
        enabled: Boolean(draftState?.ready),
        // draftSeq 变化时强制重新评估（config 引用可能相同但字段已变）
        refreshToken: draftSeq,
        // 作用域变化时重置预览（丢弃旧作用域的残留结果）
        scopeKey,
    });

    return (
        // 整个配置管理视图的外层包裹容器，应用特定的视图壳样式
        <div className="config-view-shell">
            {/* 卡片化包装容器，提供一致的阴影、圆角和背景表现 */}
            <div className="card config-card">
                {/* 主体：双栏布局 —— 左 2/3 配置编辑器 + 右 1/3 实时推文预览
                    顶栏仅覆盖左栏，右侧推文预览直接顶到卡片顶部占满全高 */}
                <div className="config-view-main">
                    {/* 左栏：配置编辑器（顶栏 + 表单区域） */}
                    <div className="config-view-main-left">
                        {/* 左栏顶部：以模式切换控件替代静态标题，紧凑化布局 */}
                        <div className="config-header config-header--compact config-header--sidebar">
                            <div className="config-view-title-row">
                                {/* 标题左侧的彩色高亮修饰块 */}
                                <div className="config-view-title-accent"></div>
                                {/* 模式切换：全局配置 / 会话差异配置 */}
                                <ToggleButtonGroup
                                    exclusive
                                    size="small"
                                    value={mode}
                                    onChange={(e, val) => { if (val) editor.setMode(val); }}
                                    className="config-header-mode-switch"
                                >
                                    <ToggleButton value="global">全局配置</ToggleButton>
                                    <ToggleButton value="session">会话差异配置</ToggleButton>
                                </ToggleButtonGroup>
                            </div>

                            {/* 会话差异模式：会话选择下拉 + 覆写状态提示 */}
                            {mode === 'session' && (
                                <div className="config-view-title-row config-header-session-row">
                                    <TextField
                                        select
                                        size="small"
                                        label="目标会话"
                                        value={selectedSession}
                                        onChange={(e) => editor.setSelectedSession(e.target.value)}
                                        className="config-mode-toolbar__session-field"
                                    >
                                        {editor.sessions.map((item) => {
                                            const name = item.session_name;
                                            const label = name ? `${item.session} (${name})` : item.session;
                                            return (
                                                <MenuItem key={item.session} value={item.session}>
                                                    {label}
                                                </MenuItem>
                                            );
                                        })}
                                    </TextField>
                                    {editor.selectedSessionMeta?.has_override && (
                                        <Chip size="small" color="primary" label="已存在差异覆写" />
                                    )}
                                    {/* 单独加载特定会话时的骨架加载提示 */}
                                    {editor.sessionLoading && (
                                        <Typography variant="caption" color="text.secondary" className="config-mode-toolbar__loading">
                                            会话配置加载中...
                                        </Typography>
                                    )}
                                </div>
                            )}

                            {/* 会话差异模式覆写状态摘要：仅在已选中会话时展示 */}
                            {mode === 'session' && editor.selectedSessionMeta && (
                                <div className="config-header-meta">
                                    <Typography variant="caption" color="text.secondary">
                                        会话推送开关：{editor.selectedSessionMeta.push_enabled ? '开启' : '关闭'} ｜ 差异覆写字段：{translateOverrideKeys(editor.selectedSessionMeta.override_keys).join('、') || '无'}
                                    </Typography>
                                </div>
                            )}
                        </div>

                        <ConfigRenderer editor={editor} onConfigDraftChange={handleDraftChange} />
                    </div>
                    {/* 右栏：实时推文预览 */}
                    <div className="config-view-main-right">
                        <window.PushPreviewPanel
                            variant="config"
                            title="实时推文预览"
                            sourceList={previewState.sourceList}
                            selectedSourceId={previewState.selectedSourceId}
                            onSelectSource={previewState.selectSource}
                            preview={previewState.preview}
                            loading={previewState.loading}
                            error={previewState.error}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
}
