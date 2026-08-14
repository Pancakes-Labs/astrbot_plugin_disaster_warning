/**
 * @file PushPreviewPanel.jsx
 * @description 实时推文预览共享面板组件。
 *
 * 两种场景模式（通过 variant 区分）：
 * 1. variant="config"（配置管理页）：
 *    - 顶部灾种 Tab 导航（地震/海啸/气象/台风 小胶囊按钮）
 *    - 下方数据源横向滑条：切换灾种后动态显示该灾种的数据源长方块按钮
 *    - 用户切换数据源 → 用该源示例参数 + 当前配置草稿实时生成推文
 * 2. variant="plain"（模拟预警页单步预览）：
 *    - 纯展示：直接渲染传入的推文文本与过滤判定，不渲染导航
 *    - 数据源/参数完全由调用方（SimulationView 已有步骤编辑器）决定
 *
 * 共用部分：过滤判定徽章 + 推文展示 + 图片降级提示。
 */
(function () {
    const { Box, Typography, Chip, CircularProgress, Button } = MaterialUI;
    const { useState, useMemo, useEffect } = React;

    /**
     * 过滤判定徽章子组件（两种场景共用）
     * 文案：通过 → "规则链通过 · 配置将允许此推文推送"
     *       拦截 → "规则链拦截 · {reason}（{detail}）"
     */
    function DecisionBadge({ decision }) {
        if (!decision) return null;
        const accepted = decision.accepted !== false;
        const detail = decision.detail ? ` · ${decision.detail}` : '';
        return (
            <Box className={`pp-decision ${accepted ? 'pp-decision--ok' : 'pp-decision--blocked'}`}>
                <span className="pp-decision-icon">{accepted ? '✅' : '❌'}</span>
                <span className="pp-decision-text">
                    {accepted
                        ? '规则链通过 · 此推文将正常推送'
                        : `规则链拦截 · ${decision.reason || '未通过'}${detail}`}
                </span>
            </Box>
        );
    }

    /**
     * 数据源两级导航（仅 config 场景）
     * - 顶层：灾种 Tab 胶囊（按 schema 的 disaster_types 顺序）
     * - 下层：当前灾种的数据源横向滑条（nowrap + overflow-x: auto）
     */
    function SourceNavigator({ sourceList, selectedSourceId, onSelect }) {
        const [activeType, setActiveType] = useState(null);
        const stripRef = React.useRef(null);

        // 滚轮横滚支持：鼠标滚轮在滑条上滚动时转为横向滚动。
        // 不使用 scroll-snap 避免吸附回弹；累积 delta 保证连续滚动不掉帧。
        useEffect(() => {
            const el = stripRef.current;
            if (!el) return;
            const handleWheel = (e) => {
                if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
                    e.preventDefault();
                    // 归一化 delta：触控板/鼠标滚轮步进不一致时仍可平滑连续滚动
                    const step = Math.abs(e.deltaY) < 1 ? e.deltaY * 16 : e.deltaY;
                    el.scrollLeft += step;
                }
            };
            el.addEventListener('wheel', handleWheel, { passive: false });
            return () => el.removeEventListener('wheel', handleWheel);
        }, [activeType]);

        // 按灾种分组（保持 schema 的 disaster_types 顺序）
        const groups = useMemo(() => {
            const groupMap = {};
            const ordered = [];
            (sourceList || []).forEach((item) => {
                if (!groupMap[item.typeKey]) {
                    groupMap[item.typeKey] = {
                        typeKey: item.typeKey,
                        typeLabel: item.typeLabel,
                        typeIcon: item.typeIcon,
                        items: [],
                    };
                    ordered.push(groupMap[item.typeKey]);
                }
                groupMap[item.typeKey].items.push(item);
            });
            return ordered;
        }, [sourceList]);

        // 灾种跟随采用"受控派生值"模式，不再写回 state：
        // - activeType 仅记录用户显式点击的灾种（用户意图覆盖）
        // - 若当前选中源不在该灾种下（如父级 onSelect 同步选中源），
        //   自动跟随选中源所在灾种作为渲染用灾种，避免 state 同步回环与回拉风险。
        const selectedGroup = (groups || []).find((g) => g.items.some((i) => i.sourceId === selectedSourceId));
        const activeGroup = selectedGroup || (activeType ? groups.find((g) => g.typeKey === activeType) || groups[0] : groups[0]);
        // 仅初始化时兜底设置 activeType（用户点击前无显式意图）
        useEffect(() => {
            if (groups.length > 0 && (!activeType || !groups.some((g) => g.typeKey === activeType))) {
                setActiveType(groups[0].typeKey);
            }
        }, [groups, activeType]);

        if (groups.length === 0) return null;

        return (
            <Box className="pp-source-slider">
                {/* 顶层：灾种 Tab 胶囊 */}
                <Box className="pp-type-tabs">
                    {groups.map((group) => {
                        const active = group.typeKey === activeGroup.typeKey;
                        return (
                            <button
                                key={group.typeKey}
                                type="button"
                                className={`pp-type-tab ${active ? 'is-active' : ''}`}
                                onClick={() => {
                                    setActiveType(group.typeKey);
                                    // 切换到该灾种首个数据源并触发预览
                                    if (group.items[0]) onSelect && onSelect(group.items[0].sourceId);
                                }}
                            >
                                <span className="pp-type-tab-icon">{group.typeIcon}</span>
                                <span className="pp-type-tab-label">{group.typeLabel}</span>
                            </button>
                        );
                    })}
                </Box>

                {/* 下层：当前灾种的数据源横向滑条*/}
                <Box className="pp-source-row">
                    <Box ref={stripRef} className="pp-source-strip">
                        {activeGroup.items.map((item) => {
                            const active = item.sourceId === selectedSourceId;
                            const familySuffix = item.familyLabel ? ` · ${item.familyLabel}` : '';
                            return (
                                <button
                                    key={item.sourceId}
                                    type="button"
                                    className={`pp-source-btn ${active ? 'is-active' : ''}`}
                                    title={`${item.label}${familySuffix}`}
                                    onClick={() => onSelect && onSelect(item.sourceId)}
                                >
                                    <span className="pp-source-btn-label">{item.label}</span>
                                    {item.familyLabel && (
                                        <span className="pp-source-btn-family">{item.familyLabel}</span>
                                    )}
                                </button>
                            );
                        })}
                    </Box>
                </Box>
            </Box>
        );
    }

    /**
     * 预览内容主体（两种场景共用）
     */
    function PreviewBody({ preview, loading, error, showBadge, onReloadSchema }) {
        if (loading) {
            return (
                <Box className="pp-loading">
                    <CircularProgress size={22} />
                    <Typography variant="body2" color="text.secondary">正在生成预览...</Typography>
                </Box>
            );
        }
        if (error) {
            return (
                <Box className="pp-error">
                    <Typography variant="body2" color="error">⚠️ {error}</Typography>
                    {typeof onReloadSchema === 'function' && (
                        <Button
                            size="small"
                            variant="outlined"
                            startIcon={<span>🔄</span>}
                            onClick={onReloadSchema}
                        >
                            重新加载推文预览
                        </Button>
                    )}
                </Box>
            );
        }

        const decision = preview?.decision || null;
        const previewText = preview?.preview_text || '';
        const mediaNotice = preview?.media_notice || '';

        return (
            <>
                {/* 推文内容（带入场动画） */}
                {previewText ? (
                    <Box className="pp-tweet pp-animate-in">
                        <pre className="pp-tweet-text">{previewText}</pre>
                    </Box>
                ) : (
                    <Box className="pp-empty pp-animate-in">
                        <Typography variant="body2" color="text.secondary">
                            暂无预览内容
                        </Typography>
                    </Box>
                )}

                {/* 图片降级提示（仅当开启图片渲染时出现） */}
                {mediaNotice && (
                    <Box className="pp-media-notice pp-animate-in">
                        <Chip
                            size="small"
                            variant="outlined"
                            color="info"
                            label={mediaNotice.replace(/^\n+/, '')}
                        />
                    </Box>
                )}

                {/* 过滤判定徽章（位于推文下方） */}
                {showBadge && <DecisionBadge decision={decision} />}
            </>
        );
    }

    /**
     * 实时推文预览面板主组件
     *
     * @param {Object} props
     * @param {string} props.variant 场景模式：'config'（配置页，含两级导航）| 'plain'（纯展示）
     * @param {Array}  props.sourceList 数据源列表（含 typeKey/typeLabel/typeIcon/region 等）
     * @param {string} props.selectedSourceId 当前选中数据源
     * @param {Function} props.onSelectSource 数据源切换回调
     * @param {Object|null} props.preview 预览结果 { preview_text, decision, media_notice, ... }
     * @param {boolean} props.loading 是否加载中
     * @param {string} props.error 错误信息
     * @param {Function} [props.onReloadSchema] Schema 独立重试回调（config 场景错误态展示）
     * @param {string} props.title 面板标题
     * @param {boolean} props.showBadge 是否展示过滤判定徽章（plain 场景可关闭）
     */
    function PushPreviewPanel({
        variant = 'config',
        sourceList = [],
        selectedSourceId = '',
        onSelectSource,
        preview = null,
        loading = false,
        error = '',
        onReloadSchema,
        title = '实时推文预览',
        showBadge = true,
    }) {
        const isConfigMode = variant === 'config';

        return (
            <Box className={`pp-panel pp-panel--${variant}`}>
                {/* 面板头 */}
                <Box className="pp-header">
                    <Typography variant="subtitle1" className="pp-title">
                        📡 {title}
                    </Typography>
                    {isConfigMode && (
                        <Typography variant="caption" color="text.secondary" className="pp-subtitle">
                            根据模拟数据与当前会话过滤配置实时预览推文
                        </Typography>
                    )}
                </Box>

                {/* 数据源两级导航（仅配置页场景） */}
                {isConfigMode && (
                    <SourceNavigator
                        sourceList={sourceList}
                        selectedSourceId={selectedSourceId}
                        onSelect={onSelectSource}
                    />
                )}

                {/* 预览内容区 */}
                <Box className="pp-body">
                    <PreviewBody
                        preview={preview}
                        loading={loading}
                        error={error}
                        showBadge={showBadge}
                        onReloadSchema={onReloadSchema}
                    />
                </Box>
            </Box>
        );
    }

    window.PushPreviewPanel = PushPreviewPanel;
})();
