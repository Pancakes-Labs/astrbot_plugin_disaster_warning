const { Box, Typography, IconButton, Tooltip } = MaterialUI;
const { useState, useRef, useEffect } = React;

/**
 * 模拟事件流步骤列表组件
 * 展示事件流编排中的步骤序列，支持：
 * - 步骤卡片展示（灾种/数据源/报数徽标）
 * - 步骤增删/复制/上移下移/自定义拖拽排序
 *   （Pointer Events 实现：拖拽中渲染不透明的独立跟随卡片，无浏览器半透明虚影）
 * - 同事件键（event_key）步骤自动分组，展示"事件组"标题
 * - 一键归并到上一事件键（与视图工具栏联动）
 *
 * @param {Object} props
 * @param {Array} props.steps 步骤数组（含 step_id / disaster_type / source_id / params 等）
 * @param {Function} props.onChange 步骤变更回调
 * @param {Function} props.onSelect 选中步骤回调
 * @param {Object} props.schema schema 元数据（用于展示灾种/数据源标签）
 * @param {number|null} props.selectedStepId 当前选中的步骤 id
 * @param {Function} props.onMergeWithPrev 一键归并到上一事件键回调（由视图注入）
 */
function SimulationStepList({ steps, onChange, onSelect, schema, selectedStepId, onMergeWithPrev }) {
    const disasterMeta = schema?.disaster_types || {};

    // 自定义 Pointer 拖拽状态：独立跟随卡片实时渲染（无浏览器虚影）
    const [dragIndex, setDragIndex] = useState(null);
    // 拖拽是否已激活：指针移动超过阈值后才置 true，避免"点击选中"误触发
    // 拖拽视觉（卡片变 dashed/出现 ghost），让单击与拖拽界限清晰。
    const [dragActive, setDragActive] = useState(false);
    const [dragPos, setDragPos] = useState(null); // { x, y } 指针屏幕坐标
    const [overIndex, setOverIndex] = useState(null);
    // 指针按下起始坐标：用于计算位移是否达到拖拽激活阈值（6px）
    const startPosRef = useRef({ x: 0, y: 0 });
    const dragActiveRef = useRef(false);
    // 拖拽结束后抑制一次 click：拖拽（激活）后浏览器仍会派发 click 到原卡片，
    // 若不拦截会误触发"点击选中"，导致拖拽后卡片被意外选中。
    const suppressClickRef = useRef(false);
    // 抑制标记的延迟复位定时器：避免快速连点下一张卡时被误抑制
    const suppressTimerRef = useRef(null);
    const itemRefs = useRef([]);
    const dragOffsetRef = useRef({ x: 0, y: 0 });
    // 拖拽卡片原始尺寸：用于 ghost 保持与源卡片一致的大小（无缩放跳变）
    const dragRectRef = useRef({ width: 0, height: 0 });
    const rafRef = useRef(null);
    // 拖拽期间的"最新值"引用：避免 useEffect 闭包捕获过期状态导致
    // pointerup 提交时读到旧 overIndex / 旧 steps，以及监听器被反复重建
    const dragIndexRef = useRef(null);
    const overIndexRef = useRef(null);
    const stepsRef = useRef(steps);
    stepsRef.current = steps;
    const onChangeRef = useRef(onChange);
    onChangeRef.current = onChange;

    /**
     * 解析灾种标签
     */
    const getDisasterLabel = (type) => {
        const meta = disasterMeta[type];
        return meta ? `${meta.icon || ''} ${meta.label || type}` : type;
    };

    /**
     * 解析数据源标签（带 family 后缀）
     */
    const getSourceLabel = (step) => {
        const typeData = disasterMeta[step.disaster_type];
        if (!typeData) return step.source_id;
        const source = (typeData.sources || []).find(s => s.source_id === step.source_id);
        if (!source) return step.source_id;
        const suffix = source.family_label ? ` · ${source.family_label}` : '';
        return `${source.label || source.source_id}${suffix}`;
    };

    /**
     * 判断某数据源是否支持报数语义（用于徽标展示）
     */
    const supportsReportSemantics = (step) => {
        const typeData = disasterMeta[step.disaster_type];
        if (!typeData) return true;
        const source = (typeData.sources || []).find(s => s.source_id === step.source_id);
        return source ? source.supports_report_semantics !== false : true;
    };

    /**
     * 判断某步骤是否属于某个事件键
     */
    const getEventKey = (step) => step.event_key || '';


    /**
     * 移动步骤（上移/下移按钮）
     */
    const moveStep = (index, delta) => {
        const next = [...steps];
        const target = index + delta;
        if (target < 0 || target >= next.length) return;
        const [item] = next.splice(index, 1);
        next.splice(target, 0, item);
        onChange(next);
    };

    /**
     * 删除步骤
     */
    const removeStep = (index) => {
        const next = steps.filter((_, i) => i !== index);
        onChange(next);
    };

    /**
     * 复制步骤
     */
    const duplicateStep = (index) => {
        const source = steps[index];
        const copy = {
            ...source,
            step_id: `${source.step_id || 'step'}_copy_${Date.now()}`,
        };
        const next = [...steps];
        next.splice(index + 1, 0, copy);
        onChange(next);
    };

    /**
     * 归并到上一事件键（行内快捷操作）
     */
    const mergeWithPrev = (index) => {
        if (index <= 0) return;
        const prev = steps[index - 1];
        const curr = steps[index];
        const nextKey = prev.event_key || `ev${Date.now()}`;
        const next = steps.map((s, i) => {
            if (i === index - 1 && !s.event_key) return { ...s, event_key: nextKey };
            if (i === index) return { ...s, event_key: nextKey };
            return s;
        });
        onChange(next);
    };

    // 计算事件键分组边界：当前步骤带事件键且与上一步事件键不同时，视为新事件组的起点
    const isGroupStart = (index) => {
        const currKey = getEventKey(steps[index]);
        if (!currKey) return false;
        if (index === 0) return true;
        return currKey !== getEventKey(steps[index - 1]);
    };

    /**
     * 开始拖拽：仅左键，从序号手柄或卡片主体（除右侧操作区外）触发。
     * 记录指针相对卡片的偏移与卡片原始尺寸，保证 ghost 与原卡大小一致。
     * 同步写入 refs，保证 effect 内的监听器可读取最新拖拽状态。
     * 注意：这里不使用 setPointerCapture —— 捕获会把后续 click 事件的 target
     * 重定向到卡片容器，导致子级 .sim-step-body 的 onClick 永远收不到（整卡都选不中）。
     * 拖拽结束依赖 window/document 双层 pointerup 兜底监听，捕获并非必需。
     */
    const handlePointerDown = (e, index) => {
        if (e.button !== 0) return;
        // 注意：这里不 preventDefault，否则会阻止卡片 body 的 onClick 选中。
        // 文本选择/滚动冲突由 CSS touch-action:none 与后续激活时的 preventDefault 处理。
        const rect = e.currentTarget.getBoundingClientRect();
        dragOffsetRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
        // 记录原卡片宽高（含边框与 padding），ghost 按此尺寸渲染
        dragRectRef.current = { width: rect.width, height: rect.height };
        startPosRef.current = { x: e.clientX, y: e.clientY };
        dragIndexRef.current = index;
        overIndexRef.current = index;
        dragActiveRef.current = false;
        setDragIndex(index);
        setOverIndex(index);
        setDragPos({ x: e.clientX, y: e.clientY });
    };

    // 拖拽期间在 window 挂全局监听（保证指针移出列表也能正确结束）。
    // 注意：effect 只依赖 dragIndex——拖拽过程中 overIndex 变化不会重建监听器，
    // 彻底避免"pointerup 丢失导致拖拽卡死"的问题（原实现把 overIndex 放进依赖，
    // 导致监听器在拖动中反复销毁重建，快速拖动时极易错过 pointerup）。
    useEffect(() => {
        if (dragIndex === null) return;

        const onMove = (e) => {
            const clientX = e.clientX;
            const clientY = e.clientY;
            if (rafRef.current) return;
            rafRef.current = requestAnimationFrame(() => {
                rafRef.current = null;
                // 未达到拖拽激活阈值前不更新 ghost 位置（保持原卡原位）
                if (!dragActiveRef.current) {
                    const dx = clientX - startPosRef.current.x;
                    const dy = clientY - startPosRef.current.y;
                    if (Math.hypot(dx, dy) < 6) return;
                    // 激活拖拽：此刻起 ghost 才跟随、排序才生效；同时阻止文本选择
                    dragActiveRef.current = true;
                    setDragActive(true);
                    if (e.cancelable) e.preventDefault();
                }
                setDragPos({ x: clientX, y: clientY });
                // 根据指针 y 与各卡片垂直中心的关系计算目标插入位置
                const items = itemRefs.current;
                if (!items || items.length === 0) return;
                let target = dragIndexRef.current;
                for (let i = 0; i < items.length; i++) {
                    const el = items[i];
                    if (!el) continue;
                    const rect = el.getBoundingClientRect();
                    if (clientY < rect.top + rect.height / 2) {
                        target = i;
                        break;
                    }
                    target = i;
                }
                if (target !== overIndexRef.current) {
                    overIndexRef.current = target;
                    setOverIndex(target);
                }
            });
        };

        const onUp = () => {
            if (rafRef.current) {
                cancelAnimationFrame(rafRef.current);
                rafRef.current = null;
            }
            const wasActive = dragActiveRef.current;
            const from = dragIndexRef.current;
            const to = overIndexRef.current;
            // 仅当真正拖拽过（激活）才提交排序，避免纯点击也改变顺序
            if (wasActive && from !== null && to !== null && from !== to) {
                const next = [...(stepsRef.current || [])];
                const [item] = next.splice(from, 1);
                next.splice(to, 0, item);
                onChangeRef.current && onChangeRef.current(next);
            }
            // 拖拽激活过则抑制紧随其后的 click（避免拖后误选中）。
            // 清除时机：先用微任务吞掉本轮合成 click，再延迟 80ms 彻底复位，
            // 覆盖浏览器派发 click 的时机差异，同时避免快速连点下一张卡被误抑制。
            suppressClickRef.current = wasActive;
            dragIndexRef.current = null;
            overIndexRef.current = null;
            dragActiveRef.current = false;
            setDragActive(false);
            setDragIndex(null);
            setDragPos(null);
            setOverIndex(null);
            const suppressId = suppressTimerRef.current;
            if (suppressId) clearTimeout(suppressId);
            suppressTimerRef.current = setTimeout(() => {
                suppressClickRef.current = false;
                suppressTimerRef.current = null;
            }, 80);
        };

        const onBlur = () => {
            // 窗口失焦兜底：切换标签页/系统弹窗抢焦点时浏览器不派发 pointerup，
            // 若不结束拖拽，ghost 会一直停留在页面上。此时按未激活处理结束拖拽。
            if (rafRef.current) {
                cancelAnimationFrame(rafRef.current);
                rafRef.current = null;
            }
            dragIndexRef.current = null;
            overIndexRef.current = null;
            dragActiveRef.current = false;
            setDragActive(false);
            setDragIndex(null);
            setDragPos(null);
            setOverIndex(null);
        };

        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
        window.addEventListener('pointercancel', onUp);
        window.addEventListener('blur', onBlur);
        // 兜底：指针可能因 iframe/滚动条等未触发 window 级 pointerup，
        // 再挂一层 document 级监听，保证拖拽必然能结束
        document.addEventListener('pointerup', onUp);
        document.addEventListener('pointercancel', onUp);
        return () => {
            if (rafRef.current) {
                cancelAnimationFrame(rafRef.current);
                rafRef.current = null;
            }
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
            window.removeEventListener('pointercancel', onUp);
            window.removeEventListener('blur', onBlur);
            document.removeEventListener('pointerup', onUp);
            document.removeEventListener('pointercancel', onUp);
        };
    }, [dragIndex]);

    const isDragging = dragIndex !== null && dragActive;

    /**
     * 报数徽标渲染（列表卡片与拖拽跟随卡片共用）。
     * 数据源不支持报次语义时，报次概念不适用，不渲染任何报次相关标签
     * （第 N 报 / 最终报 / 单报）。
     */
    const renderBadges = (step, hasReportSemantics) => {
        if (!hasReportSemantics) return [];
        const badges = [];
        if (step.report_num > 1) {
            badges.push(<span key="n" className="sim-step-badge">第 {step.report_num} 报</span>);
        } else if (step.report_num <= 1 && !step.is_final) {
            badges.push(<span key="1" className="sim-step-badge">第 1 报</span>);
        }
        if (step.is_final) {
            badges.push(<span key="f" className="sim-step-badge sim-step-badge--final">最终报</span>);
        }
        return badges;
    };

    return (
        <Box className={['sim-step-list', isDragging ? 'sim-step-list--dragging' : ''].filter(Boolean).join(' ')}>
            {steps.length === 0 && (
                <Typography variant="body2" color="text.secondary" className="sim-step-empty">
                    暂无步骤，点击右侧"添加步骤"开始编排事件流
                </Typography>
            )}
            {steps.map((step, index) => {
                const eventKey = getEventKey(step);
                const isSelected = selectedStepId === step.step_id;
                const isDraggingItem = isDragging && dragIndex === index;
                const isDragOver = isDragging && overIndex === index && dragIndex !== index;
                const groupStart = isGroupStart(index);
                const hasReportSemantics = supportsReportSemantics(step);
                const sourceLabel = getSourceLabel(step);
                const disasterLabel = getDisasterLabel(step.disaster_type);
                // 实时让位：根据当前拖拽目标与自身位置计算位移方向，
                // 下方卡片上移让位 / 上方卡片下移让位，形成动态穿插预览
                const dragShiftClass = (() => {
                    if (dragIndex === null || overIndex === null || dragIndex === index) return '';
                    if (overIndex > dragIndex && index > dragIndex && index <= overIndex) {
                        return 'is-drag-shift-up';
                    }
                    if (overIndex < dragIndex && index >= overIndex && index < dragIndex) {
                        return 'is-drag-shift-down';
                    }
                    return '';
                })();
                // 完整文本用于 title 悬停提示（解决卡片内文本截断看不全的问题）
                const fullHint = [
                    disasterLabel,
                    sourceLabel,
                    eventKey ? `事件键: ${eventKey}` : '',
                ].filter(Boolean).join(' · ');
                return (
                    <Box key={step.step_id} className="sim-step-group">
                        {groupStart && (
                            <div className="sim-step-group-label" title={`事件组：${eventKey}。该步骤及其后续同事件键步骤属于同一事件的连续报次`}>
                                🔗 事件组: {eventKey}
                            </div>
                        )}
                        <Box
                            ref={(el) => { itemRefs.current[index] = el; }}
                            className={[
                                'sim-step-item',
                                isSelected ? 'is-selected' : '',
                                isDraggingItem ? 'is-dragging' : '',
                                isDragOver ? 'is-drag-over' : '',
                                dragShiftClass,
                            ].filter(Boolean).join(' ')}
                            title={fullHint}
                            role="button"
                            tabIndex={0}
                            onPointerDown={(e) => handlePointerDown(e, index)}
                            onClick={() => {
                                // 整卡点击选中：拖拽激活过则抑制该次 click（拖后不误选）
                                if (suppressClickRef.current) return;
                                onSelect && onSelect(step.step_id);
                            }}
                            onKeyDown={(e) => {
                                // 键盘可访问性：Enter / Space 触发选中（与鼠标点击一致）
                                if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault();
                                    if (suppressClickRef.current) return;
                                    onSelect && onSelect(step.step_id);
                                }
                            }}
                        >
                            {/* 步骤序号（兼作拖拽手柄，点击整卡其他区域同样可拖） */}
                            <div className="sim-step-index" title="按住拖拽排序">
                                <span>{index + 1}</span>
                            </div>

                            {/* 步骤主体：同样可按住拖拽 */}
                            <div className="sim-step-body">
                                <div className="sim-step-title-row">
                                    <Typography variant="subtitle2" className="sim-step-title">
                                        {disasterLabel}
                                    </Typography>
                                    {renderBadges(step, hasReportSemantics)}
                                </div>
                                <Typography variant="caption" color="text.secondary" className="sim-step-source-label" title={sourceLabel}>
                                    {sourceLabel}
                                </Typography>
                                {eventKey && (
                                    <Typography variant="caption" color="primary" className="sim-step-event-key" title={`事件键：${eventKey}`}>
                                        🔗 事件键: {eventKey}
                                    </Typography>
                                )}
                            </div>

                            {/* 操作按钮组：拦截 pointerdown 与 click，避免按钮点击触发卡片拖拽/选中 */}
                            <div
                                className="sim-step-actions"
                                onPointerDown={(e) => e.stopPropagation()}
                                onClick={(e) => e.stopPropagation()}
                            >
                                <Tooltip title="上移">
                                    <IconButton size="small" onClick={() => moveStep(index, -1)} disabled={index === 0}>
                                        ⬆️
                                    </IconButton>
                                </Tooltip>
                                <Tooltip title="下移">
                                    <IconButton size="small" onClick={() => moveStep(index, 1)} disabled={index === steps.length - 1}>
                                        ⬇️
                                    </IconButton>
                                </Tooltip>
                                <Tooltip title="合并为同事件：让当前步骤和上一步共用事件键，作为同一事件的连续报次">
                                    <IconButton
                                        size="small"
                                        onClick={() => mergeWithPrev(index)}
                                        disabled={index === 0}
                                    >
                                        🔗
                                    </IconButton>
                                </Tooltip>
                                <Tooltip title="复制">
                                    <IconButton size="small" onClick={() => duplicateStep(index)}>
                                        📋
                                    </IconButton>
                                </Tooltip>
                                <Tooltip title="删除">
                                    <IconButton size="small" color="error" onClick={() => removeStep(index)}>
                                        🗑️
                                    </IconButton>
                                </Tooltip>
                            </div>
                        </Box>
                    </Box>
                );
            })}

            {/* 拖拽跟随卡片：fixed 定位、按源卡片原尺寸渲染、完全不透明、实时跟随指针 */}
            {isDragging && dragIndex !== null && dragPos && steps[dragIndex] && (() => {
                const step = steps[dragIndex];
                const sourceLabel = getSourceLabel(step);
                const disasterLabel = getDisasterLabel(step.disaster_type);
                const hasReportSemantics = supportsReportSemantics(step);
                const ghostStyle = {
                    left: dragPos.x - dragOffsetRef.current.x,
                    top: dragPos.y - dragOffsetRef.current.y,
                    width: dragRectRef.current.width || undefined,
                    height: dragRectRef.current.height || undefined,
                };
                return (
                    <Box className="sim-step-drag-ghost" style={ghostStyle}>
                        <div className="sim-step-index">
                            <span>{dragIndex + 1}</span>
                        </div>
                        <div className="sim-step-body">
                            <div className="sim-step-title-row">
                                <Typography variant="subtitle2" className="sim-step-title">
                                    {disasterLabel}
                                </Typography>
                                {renderBadges(step, hasReportSemantics)}
                            </div>
                            <Typography variant="caption" color="text.secondary" className="sim-step-source-label">
                                {sourceLabel}
                            </Typography>
                        </div>
                    </Box>
                );
            })()}
        </Box>
    );
}

window.SimulationStepList = SimulationStepList;
