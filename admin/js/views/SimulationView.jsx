const { Box, Typography, Button, TextField, Select, MenuItem, FormControl, InputLabel, Divider, IconButton, Tooltip, Dialog, DialogTitle, DialogContent, DialogActions, Chip } = MaterialUI;
const { useState, useEffect, useCallback, useRef } = React;
const { useToast } = window;

// localStorage 即时草稿键（防抖自动保存，刷新不丢）
const SIM_DRAFT_LS_KEY = 'disaster_simulation_draft';
const SIM_DRAFT_DEBOUNCE_MS = 1200;

/**
 * 模拟预警系统主视图
 * 提供完整的事件流编排能力：
 * - 步骤列表（增删/排序/复制/归并，拖拽排序）
 * - 步骤编辑器（灾种 → 数据源 → 动态参数表单）
 * - 模板一键操作（快速生成典型模拟流）
 * - 草稿箱（新建/保存/加载/删除，支持 localStorage 即时草稿）
 * - 执行控制台（整流/单步/预览/取消）
 * - 事件键分组辅助（多报数混排）
 *
 * 主入口：侧边栏"模拟预警"导航项。
 */
function SimulationView() {
    const simulationApi = window.DisasterSimulationApi;
    const { showToast } = useToast();

    // Schema 与草稿状态
    const [schema, setSchema] = useState(null);
    const [flows, setFlows] = useState([]);
    const [currentFlow, setCurrentFlow] = useState(null); // SimulationFlow 对象
    const [selectedStepId, setSelectedStepId] = useState(null);
    const [execRunId, setExecRunId] = useState(null);
    const [loading, setLoading] = useState(false);

    // 草稿保存对话框
    const [saveDialogOpen, setSaveDialogOpen] = useState(false);
    const [flowName, setFlowName] = useState('');

    // 一键模板下拉选中值（受控；选择后立即重置回占位，允许连续选同一模板）
    const [templatePickValue, setTemplatePickValue] = useState('');

    // 单步预览对话框
    const [previewDialog, setPreviewDialog] = useState(null); // { title, text }

    // 目标会话（从 schema 拉取）
    const targetSessions = schema?.target_sessions || [];

    // localStorage 即时草稿（防抖保存）
    const draftTimerRef = useRef(null);

    /**
     * 防抖写入 localStorage 即时草稿
     */
    const persistDraft = useCallback((flow) => {
        if (!flow) return;
        if (draftTimerRef.current) clearTimeout(draftTimerRef.current);
        draftTimerRef.current = setTimeout(() => {
            try {
                localStorage.setItem(SIM_DRAFT_LS_KEY, JSON.stringify(flow));
            } catch (e) {
                // 忽略 localStorage 满/禁用等异常
                console.warn('保存模拟即时草稿失败', e);
            }
        }, SIM_DRAFT_DEBOUNCE_MS);
    }, []);

    /**
     * 读取 localStorage 即时草稿
     */
    const loadLocalDraft = useCallback(() => {
        try {
            const raw = localStorage.getItem(SIM_DRAFT_LS_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            return parsed && parsed.steps ? parsed : null;
        } catch (e) {
            return null;
        }
    }, []);

    /**
     * 清空 localStorage 即时草稿
     */
    const clearLocalDraft = useCallback(() => {
        try {
            localStorage.removeItem(SIM_DRAFT_LS_KEY);
        } catch (e) {
            // 忽略
        }
    }, []);

    /**
     * 初始化：拉取 schema 与草稿列表
     */
    const bootstrap = useCallback(async () => {
        setLoading(true);
        try {
            const [schemaResult, flowsResult] = await Promise.all([
                simulationApi.getSchema(),
                simulationApi.listFlows(),
            ]);
            setSchema(schemaResult);
            const flowList = flowsResult?.flows || [];
            setFlows(flowList);
            // 优先恢复 localStorage 即时草稿；否则加载最近一个后端草稿
            const localDraft = loadLocalDraft();
            if (localDraft) {
                setCurrentFlow({
                    ...localDraft,
                    steps: localDraft.steps || [],
                });
                setSelectedStepId(localDraft.steps?.[0]?.step_id || null);
            } else if (flowList.length > 0) {
                loadFlow(flowList[0]);
            }
        } catch (e) {
            console.error('初始化模拟预警失败', e);
            showToast('初始化模拟预警失败: ' + (e.message || e), 'error');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { bootstrap(); }, []);

    // 当前流变化时防抖持久化到 localStorage
    useEffect(() => {
        if (!currentFlow) return;
        persistDraft(currentFlow);
    }, [currentFlow, persistDraft]);

    // 卸载时清空定时器
    useEffect(() => () => {
        if (draftTimerRef.current) clearTimeout(draftTimerRef.current);
    }, []);

    // 编排字段：属于 SimulationStep 顶层字段，不进入 params
    const ORCHESTRATION_KEYS = ['report_num', 'event_key', 'is_final'];

    /**
     * 生成新步骤（用指定灾种/源 + 默认参数）
     */
    const createStep = (disasterTypeKey, sourceIdKey) => {
        const typeData = schema?.disaster_types?.[disasterTypeKey];
        if (!typeData) return null;
        const source = (typeData.sources || []).find(s => s.source_id === sourceIdKey) || typeData.sources?.[0];
        if (!source) return null;

        const params = {};
        const topLevel = {};
        (source.fields || []).forEach(f => {
            if (f.default === undefined) return;
            if (ORCHESTRATION_KEYS.includes(f.key)) {
                topLevel[f.key] = f.default;
            } else {
                params[f.key] = f.default;
            }
        });

        return {
            step_id: `step_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
            disaster_type: disasterTypeKey,
            source_id: source.source_id,
            params,
            report_num: topLevel.report_num ?? 1,
            is_final: Boolean(topLevel.is_final),
            event_key: topLevel.event_key ?? '',
        };
    };

    /**
     * 生成新步骤（用首个灾种/源 + 默认参数）
     */
    const createNewStep = () => {
        const disasterKeys = Object.keys(schema?.disaster_types || {});
        if (disasterKeys.length === 0) return null;
        const firstType = disasterKeys[0];
        return createStep(firstType, null);
    };

    /**
     * 新建空白草稿（清空当前编辑与 localStorage 即时草稿）
     */
    const handleNewFlow = () => {
        if ((currentFlow?.steps?.length || 0) > 0 && !confirm('当前编辑内容未保存，确定新建空白草稿吗？')) return;
        const newFlow = {
            flow_id: `flow_${Date.now()}`,
            name: '未命名模拟流',
            description: '',
            target_session: '',
            steps: [],
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
        };
        setCurrentFlow(newFlow);
        setSelectedStepId(null);
        clearLocalDraft();
        showToast('已新建空白草稿', 'success');
    };

    /**
     * 清空当前流的所有步骤
     */
    const handleClearSteps = () => {
        if ((currentFlow?.steps?.length || 0) === 0) return;
        if (!confirm('确定清空当前草稿的全部步骤吗？')) return;
        setCurrentFlow((prev) => ({
            ...(prev || {}),
            steps: [],
            updated_at: new Date().toISOString(),
        }));
        setSelectedStepId(null);
    };

    /**
     * 添加新步骤到当前流
     * 未指定灾种时回退到首个可用灾种（保证工具栏"添加步骤"始终可用）
     */
    const handleAddStep = (disasterTypeKey, sourceIdKey) => {
        let typeKey = disasterTypeKey;
        if (!typeKey) {
            const keys = Object.keys(schema?.disaster_types || {});
            typeKey = keys[0] || '';
        }
        const step = createStep(typeKey, sourceIdKey);
        if (!step) {
            showToast('无可用灾种/数据源，无法添加步骤', 'warning');
            return;
        }
        const nextFlow = currentFlow
            ? { ...currentFlow, steps: [...(currentFlow.steps || []), step] }
            : {
                flow_id: `flow_${Date.now()}`,
                name: '未命名模拟流',
                description: '',
                target_session: '',
                steps: [step],
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
            };
        setCurrentFlow(nextFlow);
        setSelectedStepId(step.step_id);
    };

    /**
     * 一键插入模板：生成典型模拟流（如"地震多报演进"）
     *
     * 模板绑定具体数据源，基础数据取自该源 schema 默认值（即 docs 文档示例），
     * 保证不同模板生成的内容与对应数据源的真实推文一致。
     *
     * @param {string} templateId 模板标识
     */
    const handleInsertTemplate = (templateId) => {
        const tpl = templateMeta[templateId];
        if (!tpl) return;
        // 一次性取默认步骤，避免重复调用 createStep 产生多余 step_id。
        // 每次 makeStep 都会生成全新 step_id，避免多步复用同一标识导致 React key 冲突。
        const baseStep = createStep(tpl.type, tpl.source);
        const makeStep = (overrides = {}) => {
            if (!baseStep) return null;
            return {
                ...baseStep,
                ...overrides,
                step_id: `step_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
            };
        };

        let newSteps = [];
        const now = new Date();
        const ymd = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}${String(now.getDate()).padStart(2, '0')}`;
        const randSuffix = () => String(Math.floor(Math.random() * 100)).padStart(2, '0');

        switch (templateId) {
            case 'eq_multi_report': {
                // 典型地震多报：同事件键 3 报递进（第1报→第2报→最终报）。
                // 基础数据取自 CEA 文档示例（雅江县 M4.0），各报震级递增演进。
                const key = `${tpl.prefix}${ymd}${randSuffix()}`;
                const baseMag = Number(baseStep?.params?.magnitude ?? 4.0);
                newSteps = [
                    makeStep({ report_num: 1, event_key: key }),
                    makeStep({
                        report_num: 2, event_key: key,
                        params: { ...(baseStep?.params || {}), magnitude: Math.round((baseMag + 0.5) * 10) / 10 },
                    }),
                    makeStep({
                        report_num: 3, event_key: key, is_final: true,
                        params: { ...(baseStep?.params || {}), magnitude: Math.round((baseMag + 1.0) * 10) / 10 },
                    }),
                ].filter(Boolean);
                break;
            }
            case 'eq_final': {
                // 单次地震最终测定（CENC 正式测定，文档示例：堪察加东岸 M6.1）
                const key = `${tpl.prefix}${ymd}${randSuffix()}`;
                const step = makeStep({ report_num: 1, event_key: key, is_final: true });
                newSteps = step ? [step] : [];
                break;
            }
            case 'typhoon_track': {
                // 台风路径演进：历史轨迹 + 当前 + 最终（文档示例：202609 巴威）
                const key = `${tpl.prefix}${ymd}${randSuffix()}`;
                newSteps = [
                    makeStep({ report_num: 1, event_key: key }),
                    makeStep({ report_num: 2, event_key: key, is_final: true }),
                ].filter(Boolean);
                break;
            }
            default:
                break;
        }

        if (newSteps.length === 0) {
            showToast('模板生成失败（缺少可用数据源）', 'warning');
            return;
        }

        const baseFlow = currentFlow || {
            flow_id: `flow_${Date.now()}`,
            name: '未命名模拟流',
            description: '',
            target_session: '',
            created_at: new Date().toISOString(),
        };
        setCurrentFlow({
            ...baseFlow,
            steps: [...(baseFlow.steps || []), ...newSteps],
            updated_at: new Date().toISOString(),
        });
        setSelectedStepId(newSteps[newSteps.length - 1].step_id);
        showToast(`已插入模板：${templateMeta[templateId]?.label || templateId}（${newSteps.length} 步）`, 'success');
    };

    // 模板元数据：type/source 决定生成步骤使用的数据源（基础数据=该源文档示例）
    const templateMeta = {
        eq_multi_report: {
            label: '地震多报演进', icon: '🌍',
            desc: '同事件键 3 报递进',
            type: 'earthquake', source: 'cea_fanstudio', prefix: 'eq',
        },
        eq_final: {
            label: '地震最终测定', icon: '📋',
            desc: '单次 CENC 正式测定报',
            type: 'earthquake', source: 'cenc_fanstudio', prefix: 'eq',
        },
        typhoon_track: {
            label: '台风路径演进', icon: '🌀',
            desc: '台风轨迹 + 最终强度',
            type: 'typhoon', source: 'typhoon_fanstudio', prefix: 'ty',
        },
    };

    /**
     * 更新步骤列表
     */
    const handleStepsChange = (nextSteps) => {
        if (!currentFlow) return;
        setCurrentFlow({ ...currentFlow, steps: nextSteps, updated_at: new Date().toISOString() });
    };

    /**
     * 更新当前选中步骤
     */
    const handleStepChange = (updatedStep) => {
        if (!currentFlow) return;
        const nextSteps = (currentFlow.steps || []).map(s =>
            s.step_id === updatedStep.step_id ? updatedStep : s
        );
        setCurrentFlow({ ...currentFlow, steps: nextSteps, updated_at: new Date().toISOString() });
        setSelectedStepId(updatedStep.step_id);
    };

    /**
     * 加载草稿（同时清除 localStorage 即时草稿，避免旧草稿持续恢复）
     */
    const loadFlow = (flow) => {
        setCurrentFlow({
            ...flow,
            steps: flow.steps || [],
        });
        setSelectedStepId(flow.steps?.[0]?.step_id || null);
        clearLocalDraft();
    };

    /**
     * 保存草稿到后端
     */
    const handleSaveFlow = async () => {
        if (!currentFlow) return;
        const flowToSave = { ...currentFlow, name: flowName || currentFlow.name || '未命名模拟流' };
        try {
            const result = await simulationApi.saveFlow(flowToSave);
            showToast('草稿已保存', 'success');
            setSaveDialogOpen(false);
            setFlowName('');
            const flowsResult = await simulationApi.listFlows();
            setFlows(flowsResult?.flows || []);
            setCurrentFlow(result?.flow || flowToSave);
            clearLocalDraft();
        } catch (e) {
            showToast('保存草稿失败: ' + (e.message || e), 'error');
        }
    };

    /**
     * 删除草稿
     */
    const handleDeleteFlow = async (flowId) => {
        if (!flowId) return;
        if (!confirm('确定删除该模拟流草稿吗？')) return;
        try {
            await simulationApi.deleteFlow(flowId);
            showToast('草稿已删除', 'success');
            const flowsResult = await simulationApi.listFlows();
            setFlows(flowsResult?.flows || []);
            if (currentFlow?.flow_id === flowId) {
                setCurrentFlow(null);
                setSelectedStepId(null);
                clearLocalDraft();
            }
        } catch (e) {
            showToast('删除草稿失败: ' + (e.message || e), 'error');
        }
    };

    /**
     * 执行事件流（整流）
     */
    const handleRunFlow = async (mode) => {
        if (!currentFlow || (currentFlow.steps || []).length === 0) {
            showToast('请先添加模拟步骤', 'warning');
            return;
        }
        try {
            const result = await simulationApi.runFlow({
                flow: currentFlow,
                mode,
            });
            setExecRunId(result?.run_id || null);
            showToast(mode === 'preview' ? '预览执行已启动' : '事件流执行已启动', 'success');
        } catch (e) {
            showToast('启动执行失败: ' + (e.message || e), 'error');
        }
    };

    /**
     * 单步执行（当前选中步骤）
     */
    const handleRunStep = async (mode) => {
        const step = (currentFlow?.steps || []).find(s => s.step_id === selectedStepId);
        if (!step) {
            showToast('请先选择要执行的步骤', 'warning');
            return;
        }
        try {
            const result = await simulationApi.runStep({
                step,
                mode,
                target_session: currentFlow?.target_session || '',
            });
            if (mode === 'preview') {
                setPreviewDialog({
                    title: `预览：${step.disaster_type} · ${step.source_id}`,
                    text: result?.preview_text || '（无预览文本）',
                });
            } else {
                showToast(result?.message || '单步执行完成', result?.success ? 'success' : 'warning');
            }
        } catch (e) {
            showToast('单步执行失败: ' + (e.message || e), 'error');
        }
    };

    /**
     * 归并选中步骤到上一事件键（一键操作：把连续步骤合并为同一事件）
     */
    const handleMergeWithPrev = () => {
        const steps = currentFlow?.steps || [];
        const idx = steps.findIndex(s => s.step_id === selectedStepId);
        if (idx <= 0) {
            showToast('无法归并：需要至少一个前置步骤', 'warning');
            return;
        }
        const prev = steps[idx - 1];
        const curr = steps[idx];
        const nextKey = prev.event_key || `ev${Date.now()}`;
        // 若前一步无事件键，先给它补一个
        const merged = steps.map((s, i) => {
            if (i === idx - 1 && !s.event_key) return { ...s, event_key: nextKey };
            if (i === idx) return { ...s, event_key: nextKey };
            return s;
        });
        handleStepsChange(merged);
        showToast(`已将当前步骤归并到事件键「${nextKey}」`, 'success');
    };

    const selectedStep = (currentFlow?.steps || []).find(s => s.step_id === selectedStepId) || null;

    return (
        <Box className="simulation-view">
            {/* 顶部工具栏：仅保留编排/编辑类操作；执行类操作下沉到下方控制台 */}
            <Box className="sim-view-toolbar">
                <Box className="sim-view-toolbar-left">
                    <Typography variant="h5" className="sim-view-title">
                        🧪 模拟预警
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                        事件流编排 · 多数据源 · 多报数混排
                    </Typography>
                </Box>
                <Box className="sim-view-toolbar-actions">
                    {/* 编辑操作组 */}
                    <Box className="sim-view-toolbar-group sim-view-toolbar-group--edit">
                        <Button variant="outlined" size="small" onClick={handleNewFlow}>
                            ✨ 新建草稿
                        </Button>
                        <Button variant="outlined" size="small" onClick={() => handleAddStep(null, null)}>
                            ＋ 添加步骤
                        </Button>
                        <Button variant="outlined" size="small" color="error" onClick={handleClearSteps} disabled={!(currentFlow?.steps?.length > 0)}>
                            🧹 清空步骤
                        </Button>
                        <Button variant="outlined" size="small" color="primary" onClick={() => setSaveDialogOpen(true)} disabled={!(currentFlow?.steps?.length > 0)}>
                            💾 保存草稿
                        </Button>
                    </Box>

                    <Divider orientation="vertical" flexItem className="sim-view-toolbar-divider" />

                    {/* 模板一键操作组 */}
                    <Box className="sim-view-toolbar-group sim-view-toolbar-group--template">
                        <Select
                            size="small"
                            value={templatePickValue}
                            displayEmpty
                            className="sim-view-template-select"
                            MenuProps={{ autoFocus: false, disableAutoFocusItem: true }}
                            onChange={(e) => {
                                if (e.target.value) {
                                    handleInsertTemplate(e.target.value);
                                    // 重置回占位，允许连续选择同一模板
                                    setTemplatePickValue('');
                                }
                            }}
                            renderValue={() => <span className="sim-view-template-placeholder">📦 一键模板</span>}
                        >
                            <MenuItem value="" disabled>
                                <em>选择模板插入</em>
                            </MenuItem>
                            {Object.entries(templateMeta).map(([id, meta]) => (
                                <MenuItem key={id} value={id}>
                                    <span className="sim-view-template-option">
                                        <span>{meta.icon} {meta.label}</span>
                                        <span className="sim-view-template-desc">{meta.desc}</span>
                                    </span>
                                </MenuItem>
                            ))}
                        </Select>
                    </Box>
                </Box>
            </Box>

            {/* 草稿选择器 */}
            <Box className="sim-view-draft-bar">
                <FormControl size="small" className="sim-view-draft-select">
                    <InputLabel>草稿箱</InputLabel>
                    <Select
                        value={currentFlow?.flow_id || ''}
                        label="草稿箱"
                        displayEmpty
                        onChange={(e) => {
                            const flow = flows.find(f => f.flow_id === e.target.value);
                            if (flow) loadFlow(flow);
                        }}
                    >
                        {flows.length === 0 ? (
                            <MenuItem value="" disabled>
                                <em>暂无草稿，点击"新建草稿"或"保存草稿"创建</em>
                            </MenuItem>
                        ) : (
                            flows.map(flow => (
                                <MenuItem key={flow.flow_id} value={flow.flow_id}>
                                    {flow.name || '未命名模拟流'}
                                </MenuItem>
                            ))
                        )}
                    </Select>
                </FormControl>
                <Box className="sim-view-flow-meta">
                    {currentFlow && (
                        <>
                            <TextField
                                size="small"
                                label="流名称"
                                value={currentFlow.name || ''}
                                onChange={(e) => setCurrentFlow({ ...currentFlow, name: e.target.value })}
                                className="sim-view-flow-name"
                            />
                            <FormControl size="small" className="sim-view-flow-session">
                                <InputLabel>目标会话</InputLabel>
                                <Select
                                    value={currentFlow.target_session || ''}
                                    label="目标会话"
                                    onChange={(e) => setCurrentFlow({ ...currentFlow, target_session: e.target.value })}
                                >
                                    <MenuItem value="">
                                        <em>默认会话</em>
                                    </MenuItem>
                                    {targetSessions.map((session, idx) => {
                                        const value = typeof session === 'string' ? session : (session.session || '');
                                        const label = typeof session === 'string' ? session : (session.session_display_name || session.session_name || value);
                                        return <MenuItem key={idx} value={value}>{label}</MenuItem>;
                                    })}
                                </Select>
                            </FormControl>
                            <Chip
                                size="small"
                                label={`${currentFlow.steps?.length || 0} 步`}
                                variant="outlined"
                                className="sim-view-flow-count"
                            />
                            <Tooltip title="删除当前草稿">
                                <IconButton size="small" color="error" onClick={() => handleDeleteFlow(currentFlow.flow_id)}>
                                    🗑️
                                </IconButton>
                            </Tooltip>
                        </>
                    )}
                </Box>
            </Box>

            {/* 主编辑区：左步骤列表 + 右步骤编辑器 */}
            <Box className="sim-view-main">
                <Box className="sim-view-left">
                    <Typography variant="subtitle1" className="sim-view-section-title">
                        事件流步骤 ({currentFlow?.steps?.length || 0})
                    </Typography>
                    <window.SimulationStepList
                        steps={currentFlow?.steps || []}
                        schema={schema}
                        selectedStepId={selectedStepId}
                        onChange={handleStepsChange}
                        onSelect={setSelectedStepId}
                        onMergeWithPrev={handleMergeWithPrev}
                    />
                </Box>
                <Box className="sim-view-right">
                    <Typography variant="subtitle1" className="sim-view-section-title">
                        步骤编辑
                    </Typography>
                    {selectedStep ? (
                        <window.SimulationStepEditor
                            schema={schema}
                            step={selectedStep}
                            onChange={handleStepChange}
                        />
                    ) : (
                        <Typography variant="body2" color="text.secondary" className="sim-view-editor-empty">
                            请选择或添加一个步骤进行编辑
                        </Typography>
                    )}
                </Box>
            </Box>

            {/* 执行控制台（含整流/单步执行操作条） */}
            <Box className="sim-view-console">
                <window.SimulationRunConsole
                    runId={execRunId}
                    steps={currentFlow?.steps || []}
                    selectedStepId={selectedStepId}
                    schema={schema}
                    onRunStep={handleRunStep}
                    onRunFlow={handleRunFlow}
                    onMergeWithPrev={handleMergeWithPrev}
                    onCancel={(runId) => simulationApi.cancelRun(runId)}
                    onSelectStep={(stepId) => setSelectedStepId(stepId)}
                />
            </Box>

            {/* 保存草稿对话框 */}
            <Dialog open={saveDialogOpen} onClose={() => setSaveDialogOpen(false)} maxWidth="xs" fullWidth>
                <DialogTitle>保存模拟流草稿</DialogTitle>
                <DialogContent>
                    <TextField
                        fullWidth
                        size="small"
                        label="流名称"
                        value={flowName || currentFlow?.name || ''}
                        onChange={(e) => setFlowName(e.target.value)}
                        autoFocus
                    />
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setSaveDialogOpen(false)}>取消</Button>
                    <Button color="primary" variant="contained" onClick={handleSaveFlow}>保存</Button>
                </DialogActions>
            </Dialog>

            {/* 单步预览对话框*/}
            <Dialog open={Boolean(previewDialog)} onClose={() => setPreviewDialog(null)} maxWidth="md" fullWidth>
                <DialogTitle>{previewDialog?.title || '预览'}</DialogTitle>
                <DialogContent>
                    <pre className="sim-view-preview-dialog-text">{previewDialog?.text || ''}</pre>
                </DialogContent>
                <DialogActions>
                    <Button onClick={() => setPreviewDialog(null)}>关闭</Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
}

window.SimulationView = SimulationView;
