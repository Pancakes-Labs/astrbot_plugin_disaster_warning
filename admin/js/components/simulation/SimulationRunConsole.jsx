const { Box, Typography, Button, CircularProgress, Divider, Tooltip, Chip } = MaterialUI;
const { useState, useEffect } = React;

/**
 * 模拟执行控制台组件
 * 展示事件流整流执行的逐步骤进度与结果：
 * - 步骤状态（pending/running/success/failed/cancelled）
 * - 步骤消息 / 预览文本 / 错误信息
 * - 支持轮询执行进度 + WebSocket 实时进度
 * - 支持取消执行中任务
 *
 * @param {Object} props
 * @param {string|null} props.runId 当前执行的 run_id
 * @param {Function} props.getRun run_id → 执行状态获取器（默认走 window.DisasterSimulationApi）
 * @param {Function} props.onCancel run_id → 取消执行
 * @param {Object|null} props.schema schema 元数据（用于把灾种/数据源原始键名展示为可读标签）
 */
function SimulationRunConsole({ runId, getRun, onCancel, steps, selectedStepId, schema, onRunStep, onRunFlow, onMergeWithPrev, onSelectStep }) {
    const disasterMeta = schema?.disaster_types || {};
    const [run, setRun] = useState(null);
    const [polling, setPolling] = useState(false);
    const [expandedStep, setExpandedStep] = useState(null);
    const [cancelling, setCancelling] = useState(false);
    const runIdRef = React.useRef(runId);

    runIdRef.current = runId;

    const fetchRun = async (id) => {
        if (!id) return;
        try {
            const fetcher = getRun || ((rid) => window.DisasterSimulationApi.getRun(rid));
            const result = await fetcher(id);
            setRun(result);
            return result;
        } catch (e) {
            console.error('查询执行进度失败', e);
            return null;
        }
    };

    // runId 变化时开始轮询
    useEffect(() => {
        if (!runId) {
            setRun(null);
            return;
        }
        let cancelled = false;
        let timer = null;

        const poll = async () => {
            const result = await fetchRun(runId);
            if (cancelled) return;
            // 执行完成或失败则停止轮询
            if (result && (result.status === 'completed' || result.status === 'failed' || result.status === 'cancelled')) {
                setPolling(false);
                return;
            }
            setPolling(true);
            timer = setTimeout(poll, 1500);
        };
        poll();

        return () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
        };
    }, [runId]);

    // WebSocket 实时进度订阅：收到 simulation_progress 且 run_id 匹配时直接更新
    useEffect(() => {
        const wsc = window.WebSocketClient;
        if (!wsc || typeof wsc.subscribe !== 'function') return;

        const unsubscribe = wsc.subscribe({
            onMessage: (msg) => {
                if (!msg || msg.type !== 'simulation_progress') return;
                const data = msg.data || {};
                if (runIdRef.current && data.run_id === runIdRef.current) {
                    setRun(data);
                    setPolling(false);
                    // 完成后清除轮询
                    if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
                        setPolling(false);
                    } else {
                        setPolling(true);
                    }
                }
            },
        });
        return unsubscribe;
    }, []);

    /**
     * 取消当前执行
     */
    const handleCancel = async () => {
        const id = runIdRef.current;
        if (!id || cancelling) return;
        setCancelling(true);
        try {
            const canceller = onCancel || ((rid) => window.DisasterSimulationApi.cancelRun(rid));
            await canceller(id);
            setPolling(false);
            // 立即轮询一次获取最新状态
            await fetchRun(id);
        } catch (e) {
            console.error('取消执行失败', e);
        } finally {
            setCancelling(false);
        }
    };

    const statusMeta = {
        pending: { label: '待执行', className: 'is-pending' },
        running: { label: '执行中', className: 'is-running' },
        success: { label: '成功', className: 'is-success' },
        failed: { label: '失败', className: 'is-failed' },
        cancelled: { label: '已取消', className: 'is-cancelled' },
    };

    const isRunning = run && (run.status === 'pending' || run.status === 'running');

    const hasSteps = Array.isArray(steps) && steps.length > 0;
    const stepCount = hasSteps ? steps.length : 0;
    const selectedStep = hasSteps ? (steps.find(s => s.step_id === selectedStepId) || null) : null;

    /**
     * 把结果卡片中的灾种/数据源原始键名解析为可读展示文本。
     * 例如 "earthquake · cea_fanstudio" → "🌍 地震 · 中国地震预警网 (CEA) · FAN Studio"
     * 解析不到时回退原始键名。
     */
    const formatResultTitle = (result) => {
        const typeMeta = disasterMeta[result.disaster_type];
        const typeLabel = typeMeta
            ? `${typeMeta.icon || ''} ${typeMeta.label || result.disaster_type}`.trim()
            : result.disaster_type;
        let sourceLabel = result.source_id;
        if (typeMeta) {
            const source = (typeMeta.sources || []).find(s => s.source_id === result.source_id);
            if (source) {
                const suffix = source.family_label ? ` · ${source.family_label}` : '';
                sourceLabel = `${source.label || result.source_id}${suffix}`;
            }
        }
        return `${typeLabel} · ${sourceLabel}`;
    };

    // 运行快照是否已过期：当前步骤列表与执行时快照不一致时置 true。
    // 通过 step_id 集合与步骤数双重对比，覆盖"删除/新增步骤""排序"两类变更；
    // 纯参数修改无法从快照侧感知（结果不含参数摘要），由警告条 + 清除按钮兜底。
    const runSnapshotStepIds = (run?.step_results || []).map(r => r.step_id).join('|');
    const currentStepIds = (steps || []).map(s => s.step_id).join('|');
    const resultCount = (run?.step_results || []).length;
    const runIsStale = Boolean(
        run
        && !isRunning
        && runSnapshotStepIds
        && (runSnapshotStepIds !== currentStepIds || resultCount !== stepCount)
    );

    // 清理执行结果：清空 run 与轮询状态（兜底按钮 / 步骤大改后自动清理）
    const handleClearRun = () => {
        setRun(null);
        setPolling(false);
        setExpandedStep(null);
    };

    // 点击结果项：始终切换展开/收起详情（显示预览文本 / 事件ID / 错误），
    // 若该步骤仍存在于当前流程中，同时回传选中（跳转左侧对应卡片编辑）。
    const handleResultClick = (result) => {
        // 优先切换展开状态：步骤存在时也要能查看预览，不能只选中而不展开
        setExpandedStep(expandedStep === result.step_id ? null : result.step_id);
        const exists = (steps || []).some(s => s.step_id === result.step_id);
        if (exists && onSelectStep) {
            onSelectStep(result.step_id);
        }
    };

    return (
        <Box className="sim-run-console">
            <Box className="sim-run-console-header">
                <Typography variant="subtitle1" className="sim-run-console-title">
                    ⚡ 执行控制台
                </Typography>
                {polling && <CircularProgress size={16} className="sim-run-console-spinner" />}
                {run && (
                    <Typography variant="caption" color="text.secondary">
                        {run.flow_name || ''} · {run.mode === 'preview' ? '预览模式' : '发送模式'}
                    </Typography>
                )}
                {run && !isRunning && (
                    <Button
                        size="small"
                        variant="outlined"
                        onClick={handleClearRun}
                        className="sim-run-console-clear"
                    >
                        🗑️ 清除结果
                    </Button>
                )}
                {isRunning && (
                    <Button
                        size="small"
                        color="error"
                        variant="outlined"
                        onClick={handleCancel}
                        disabled={cancelling}
                        className="sim-run-console-cancel"
                    >
                        {cancelling ? '取消中...' : '🛑 取消执行'}
                    </Button>
                )}
            </Box>

            {/* 执行操作条：整流 + 单步操作（由顶部工具栏下沉至此，功能分区更合理） */}
            <Box className="sim-run-console-actions">
                <Box className="sim-run-console-actions-group">
                    {/* enterDelay：鼠标需持续 hover 400ms 才显示提示，避免快速移动/边缘抖动
                        导致 tooltip 反复开关与位置跳动（MUI 默认 100ms 过敏感） */}
                    <Tooltip title="预览整流：构建全部步骤消息但不发送" enterDelay={400}>
                        <span>
                            <Button variant="contained" size="small" onClick={() => onRunFlow && onRunFlow('preview')} disabled={!hasSteps}>
                                🔍 预览执行流
                            </Button>
                        </span>
                    </Tooltip>
                    <Tooltip title="整流发送：按编排顺序与延迟逐条推送到目标会话" enterDelay={400}>
                        <span>
                            <Button variant="contained" size="small" color="success" onClick={() => onRunFlow && onRunFlow('send')} disabled={!hasSteps}>
                                ▶️ 执行事件流
                            </Button>
                        </span>
                    </Tooltip>
                </Box>
                <Divider orientation="vertical" flexItem className="sim-run-console-actions-divider" />
                <Box className="sim-run-console-actions-group">
                    <Tooltip title="预览当前选中步骤的构建消息（不发送）" enterDelay={400}>
                        <span>
                            <Button variant="contained" size="small" color="primary" onClick={() => onRunStep && onRunStep('preview')} disabled={!selectedStep}>
                                👁️ 预览当前步
                            </Button>
                        </span>
                    </Tooltip>
                    <Tooltip title="发送当前选中步骤到目标会话" enterDelay={400}>
                        <span>
                            <Button variant="contained" size="small" color="secondary" onClick={() => onRunStep && onRunStep('send')} disabled={!selectedStep}>
                                📤 发送当前步
                            </Button>
                        </span>
                    </Tooltip>
                    <Tooltip
                        title="把当前步骤和上一步合并成同一个事件（共用事件键，自动递增第几报）。适合把分开添加的多报步骤合成一次地震的连续推送。"
                        enterDelay={400}
                    >
                        <span>
                            <Button variant="outlined" size="small" onClick={() => onMergeWithPrev && onMergeWithPrev()} disabled={!selectedStep}>
                                🔗 合并为同事件
                            </Button>
                        </span>
                    </Tooltip>
                </Box>
                <Chip
                    size="small"
                    label={`${stepCount} 步`}
                    variant="outlined"
                    className="sim-run-console-actions-count"
                />
            </Box>

            {!runId && (
                <Typography variant="body2" color="text.secondary" className="sim-run-console-empty">
                    {hasSteps
                        ? '已就绪：可预览/执行事件流，或选中左侧步骤进行单步操作'
                        : '尚未添加步骤，先在上方点击"添加步骤"或插入模板'}
                </Typography>
            )}

            {run && (
                <Box className="sim-run-console-body">
                    {/* 步骤变更过期提示 */}
                    {runIsStale && (
                        <Typography
                            variant="caption"
                            color="warning"
                            className="sim-run-console-stale"
                        >
                            ⚠️ 当前事件流步骤已变更，以下结果为旧快照，可能不再对应。可点击结果跳转到对应步骤，或点「清除结果」刷新。
                        </Typography>
                    )}

                    {/* 整体状态 */}
                    <Box className="sim-run-console-summary">
                        <span className={`sim-run-status-tag ${statusMeta[run.status]?.className || ''}`}>
                            {statusMeta[run.status]?.label || run.status}
                        </span>
                        <span className="sim-run-console-count">
                            {run.step_results.filter(r => r.status === 'success').length}/{run.step_results.length} 步成功
                        </span>
                    </Box>

                    {/* 步骤结果列表 */}
                    <Box className="sim-run-step-results">
                        {run.step_results.map((result, index) => {
                            const meta = statusMeta[result.status] || statusMeta.pending;
                            const expanded = expandedStep === result.step_id;
                            const stillExists = (steps || []).some(s => s.step_id === result.step_id);
                            return (
                                <Box
                                    key={result.step_id}
                                    className={[
                                        'sim-run-step-result',
                                        meta.className,
                                        stillExists ? 'is-clickable' : 'is-orphan',
                                    ].filter(Boolean).join(' ')}
                                    onClick={() => handleResultClick(result)}
                                >
                                    <div className="sim-run-step-result-main">
                                        <span className="sim-run-step-index">{index + 1}</span>
                                        <div className="sim-run-step-result-info">
                                            <Typography variant="body2" className="sim-run-step-result-title">
                                                {formatResultTitle(result)}
                                            </Typography>
                                            <Typography variant="caption" color="text.secondary">
                                                {result.message || meta.label}
                                            </Typography>
                                        </div>
                                        <span className="sim-run-step-status">{meta.label}</span>
                                    </div>

                                    {!stillExists && (
                                        <Typography variant="caption" className="sim-run-step-orphan-hint">
                                            ↪️ 原步骤已被删除，本结果仅作历史记录
                                        </Typography>
                                    )}

                                    {expanded && (
                                        <Box className="sim-run-step-result-detail">
                                            {result.event_id && (
                                                <Typography variant="caption" color="text.secondary">
                                                    📌 事件ID: {result.event_id}
                                                </Typography>
                                            )}
                                            {result.preview_text && (
                                                <pre className="sim-run-preview-text">{result.preview_text}</pre>
                                            )}
                                            {result.error && (
                                                <Typography variant="caption" color="error">
                                                    ❌ {result.error}
                                                </Typography>
                                            )}
                                        </Box>
                                    )}
                                </Box>
                            );
                        })}
                    </Box>
                </Box>
            )}
        </Box>
    );
}

window.SimulationRunConsole = SimulationRunConsole;
