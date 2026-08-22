const { Typography, CircularProgress } = MaterialUI;

/**
 * 台风信息快捷查询面板组件 (TyphoonQueryPanel)
 * 提供与聊天机器人命令 /台风信息查询 完全一致的可视化检索界面。
 *
 * 支持：
 * 1. 指定台风 ID（4位 EQSC / 6位 Fan）
 * 2. 名称关键词
 * 3. 返回数量
 * 4. 详细程度：当前信息 / 完整路径
 * 5. 仅活跃过滤
 * 6. 多条结果列表分页与路径展开
 */
function TyphoonQueryPanel() {
    const {
        typhoonId, setTyphoonId,
        keyword, setKeyword,
        count, setCount,
        detail, setDetail,
        activeOnly, setActiveOnly,
        loading,
        error,
        result,
        page, setPage,
        pageSize, setPageSize,
        isIdQuery,
        expandedIds,
        toggleExpanded,
        searchTyphoon,
        resetTyphoonQuery,
    } = useTyphoonQuery();

    const typhoonFormatters = window.DisasterTyphoonFormatters;
    const getLevelEmoji = (level) => typhoonFormatters.getTyphoonLevelEmoji(level) || '🌀';
    const formatCoords = (lat, lon) => typhoonFormatters.formatTyphoonCoords(lat, lon);

    const renderTrackBlock = (item) => {
        const track = item?.track_summary || {};
        const historyLines = Array.isArray(track.history_lines) ? track.history_lines : [];
        const futureLines = Array.isArray(track.future_lines) ? track.future_lines : [];
        if (!historyLines.length && !futureLines.length) {
            return (
                <Typography variant="body2" className="typhoon-query-caption-muted">
                    当前数据源未提供完整路径节点
                </Typography>
            );
        }
        const historyTotal = track.history_count || historyLines.length;
        const futureTotal = track.future_count || futureLines.length;
        return (
            <div className="typhoon-query-track-block">
                {historyLines.length > 0 && (
                    <div className="typhoon-query-track-section">
                        <Typography variant="caption" className="typhoon-query-track-title">
                            📜 历史路径（共 {historyTotal} 点）
                        </Typography>
                        <ul className="typhoon-query-track-list">
                            {historyLines.map((line, idx) => (
                                <li key={`h-${idx}`}>{line}</li>
                            ))}
                        </ul>
                    </div>
                )}
                {futureLines.length > 0 && (
                    <div className="typhoon-query-track-section">
                        <Typography variant="caption" className="typhoon-query-track-title">
                            🔮 预报路径（共 {futureTotal} 点）
                        </Typography>
                        <ul className="typhoon-query-track-list">
                            {futureLines.map((line, idx) => (
                                <li key={`f-${idx}`}>{line}</li>
                            ))}
                        </ul>
                    </div>
                )}
            </div>
        );
    };

    const renderWindCircle = (windCircle) => {
        const rows = typhoonFormatters.formatTyphoonWindCircle(windCircle);
        if (!rows.length) return null;
        return (
            <div className="typhoon-query-wind-circle">
                <Typography variant="caption" className="typhoon-query-track-title">🌪️ 风圈半径</Typography>
                {rows.map((row, idx) => (
                    <Typography key={idx} variant="body2" className="typhoon-query-meta-line">{row}</Typography>
                ))}
            </div>
        );
    };

    /**
     * 统一短编号解析：eqsc_id → typhoon_id → real_event_id → unique_id
     * 与后端 _format_typhoon_short_id / EventCard 保持一致。
     */
    const resolveShortId = (item, fallback = '未知') => {
        if (!item) return fallback;
        const resolved = typhoonFormatters?.formatTyphoonShortId
            ? typhoonFormatters.formatTyphoonShortId(
                item.eqsc_id,
                item.typhoon_id,
                item.real_event_id,
                item.unique_id,
            )
            : (item.eqsc_id || item.typhoon_id || item.real_event_id || item.unique_id || '');
        return resolved || fallback;
    };

    const renderDetailCard = (item, {
        showExpand = false,
        expanded = true,
        onToggle = null,
        keyPrefix = 'detail',
        shortId: shortIdProp = null,
        expandKey: expandKeyProp = null,
    } = {}) => {
        if (!item) return null;
        const coords = formatCoords(item.latitude, item.longitude);
        const level = item.typhoon_type || '未知等级';
        // 优先使用调用方传入的 shortId/expandKey，避免列表与详情各自计算导致折叠键不一致
        const shortId = shortIdProp || resolveShortId(item, '未知');
        const expandKey = expandKeyProp || `${keyPrefix}-${shortId}-${item.updated_at || ''}`;

        return (
            <div className="weather-query-result-card typhoon-query-result-card" key={expandKey}>
                <div className="weather-query-result-header">
                    <Typography variant="subtitle1" className="weather-query-result-title">
                        {getLevelEmoji(level)} {item.display_name || '未知台风'}
                    </Typography>
                </div>

                <div className="weather-query-result-body typhoon-query-result-body">
                    {/* 结果正文第一行：编号 */}
                    <Typography variant="body2" className="typhoon-query-meta-line typhoon-query-id-line">
                        编号：{shortId}
                    </Typography>
                    <Typography variant="body2" className="typhoon-query-meta-line">
                        等级：{level}{getLevelEmoji(level)}
                    </Typography>
                    <Typography variant="body2" className="typhoon-query-meta-line">
                        状态：{item.is_active === false ? '已停止编报' : (item.is_active ? '活跃编报中' : '未知')}
                    </Typography>
                    {coords && (
                        <Typography variant="body2" className="typhoon-query-meta-line">
                            中心位置：({coords})
                        </Typography>
                    )}
                    {item.wind_speed != null && (
                        <Typography variant="body2" className="typhoon-query-meta-line">
                            最大风速：{item.wind_speed} m/s{item.power != null ? `（${item.power}级）` : ''}
                        </Typography>
                    )}
                    {item.pressure != null && (
                        <Typography variant="body2" className="typhoon-query-meta-line">
                            中心气压：{item.pressure} hPa
                        </Typography>
                    )}
                    {(item.move_direction || item.move_speed != null) && (
                        <Typography variant="body2" className="typhoon-query-meta-line">
                            移动：{[item.move_direction, item.move_speed != null ? `${item.move_speed} km/h` : '']
                                .filter(Boolean)
                                .join(' · ')}
                        </Typography>
                    )}
                    {item.updated_at_text && (
                        <Typography variant="body2" className="typhoon-query-meta-line">
                            更新时间：{item.updated_at_text}
                        </Typography>
                    )}
                    <Typography variant="body2" className="typhoon-query-meta-line typhoon-query-source-line">
                        数据来源：{item.source_label || item.data_source || '未知'}
                    </Typography>

                    {renderWindCircle(item.wind_circle)}

                    {(item.radius7 != null || item.radius10 != null) && !item.wind_circle?.['30KTS'] && (
                        <div className="typhoon-query-wind-circle">
                            <Typography variant="caption" className="typhoon-query-track-title">🌪️ 风圈半径</Typography>
                            {item.radius7 != null && (
                                <Typography variant="body2" className="typhoon-query-meta-line">7级风圈：{item.radius7} km</Typography>
                            )}
                            {item.radius10 != null && (
                                <Typography variant="body2" className="typhoon-query-meta-line">10级风圈：{item.radius10} km</Typography>
                            )}
                        </div>
                    )}

                    {result?.detail === 'full' && (
                        <div className="typhoon-query-track-wrap">
                            {showExpand ? (
                                <>
                                    <button
                                        className="btn weather-query-btn weather-query-btn-secondary typhoon-query-expand-btn"
                                        onClick={() => onToggle && onToggle(expandKey)}
                                    >
                                        {expanded ? '收起完整路径' : '展开完整路径'}
                                    </button>
                                    {expanded && renderTrackBlock(item)}
                                </>
                            ) : (
                                renderTrackBlock(item)
                            )}
                        </div>
                    )}
                </div>
            </div>
        );
    };

    const renderIdResult = () => {
        const detailItem = result?.data || (Array.isArray(result?.items) ? result.items[0] : null);
        return renderDetailCard(detailItem, { showExpand: false, expanded: true });
    };

    const renderListResult = () => {
        const items = Array.isArray(result?.items) ? result.items : [];
        const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
        const currentPage = Math.min(page, totalPages);
        const startIndex = (currentPage - 1) * pageSize;
        const pagedItems = items.slice(startIndex, startIndex + pageSize);
        const sourceLabel = result?.source === 'eqsc' ? 'EQSC' : (result?.source === 'local' ? '本地数据库' : (result?.source || '未知'));

        return (
            <div className="weather-query-list typhoon-query-list">
                <div className="weather-query-list-toolbar">
                    <Typography variant="caption" className="weather-query-caption-muted">
                        共 {items.length} 条（来源：{sourceLabel}
                        {result?.fallback_from ? '，已从 EQSC 回退' : ''}
                        ），当前第 {currentPage} / {totalPages} 页
                    </Typography>
                    <div className="weather-query-page-size-control">
                        <Typography variant="caption" className="weather-query-caption-muted">每页</Typography>
                        <select
                            value={pageSize}
                            onChange={(e) => setPageSize(Number(e.target.value) || 10)}
                            className="weather-query-input weather-query-page-size-select"
                        >
                            <option value={5}>5</option>
                            <option value={10}>10</option>
                            <option value={20}>20</option>
                        </select>
                    </div>
                </div>

                {pagedItems.map((item, index) => {
                    // 只算一次 shortId/expandKey，并下传给详情卡片，保证折叠状态键一致
                    const shortId = resolveShortId(item, `idx-${startIndex + index}`);
                    const expandKey = `list-${shortId}-${item.updated_at || startIndex + index}`;
                    const expanded = Boolean(expandedIds[expandKey]);
                    return (
                        <div key={expandKey} className="typhoon-query-list-item-wrap">
                            {renderDetailCard(item, {
                                showExpand: result?.detail === 'full',
                                expanded,
                                onToggle: toggleExpanded,
                                keyPrefix: 'list',
                                shortId,
                                expandKey,
                            })}
                        </div>
                    );
                })}

                {items.length > pageSize && (
                    <div className="weather-query-pagination-row">
                        <button
                            className="btn weather-query-btn weather-query-btn-secondary"
                            onClick={() => setPage(Math.max(1, currentPage - 1))}
                            disabled={currentPage <= 1}
                        >
                            上一页
                        </button>
                        <Typography variant="caption" className="weather-query-caption-muted">
                            第 {currentPage} / {totalPages} 页
                        </Typography>
                        <button
                            className="btn weather-query-btn weather-query-btn-secondary"
                            onClick={() => setPage(Math.min(totalPages, currentPage + 1))}
                            disabled={currentPage >= totalPages}
                        >
                            下一页
                        </button>
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="card weather-query-panel typhoon-query-panel">
            <div className="weather-query-header">
                <div className="weather-query-header-main">
                    <span className="weather-query-title-icon">🌀</span>
                    <Typography variant="h6" className="weather-query-title">台风信息快捷查询</Typography>
                </div>
                <Typography variant="caption" className="weather-query-caption-subtle">
                    等价于 /台风信息查询 指令 · 优先 EQSC，失败回退本地
                </Typography>
            </div>

            <div className="weather-query-form typhoon-query-form">
                <input
                    value={typhoonId}
                    onChange={(e) => setTyphoonId(e.target.value)}
                    placeholder="可选：台风ID（4位如2609 / 6位如202609）"
                    className="weather-query-input typhoon-query-id"
                />
                <input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder="可选：台风名称（如 巴威 / BAVI）"
                    className="weather-query-input typhoon-query-keyword"
                    disabled={isIdQuery}
                />
                <div
                    className={`typhoon-query-count-field${isIdQuery ? ' is-disabled' : ''}`}
                    title="返回数量，范围 1-20；指定台风 ID 时此项无效"
                >
                    <span className="typhoon-query-count-label">数量</span>
                    <input
                        type="number"
                        min={1}
                        max={20}
                        step={1}
                        value={count}
                        onChange={(e) => setCount(e.target.value)}
                        placeholder="1-20"
                        className="weather-query-input typhoon-query-count"
                        disabled={isIdQuery}
                        aria-label="返回数量，范围 1 到 20"
                    />
                    <span className="typhoon-query-count-suffix">条</span>
                </div>
                <select
                    value={detail}
                    onChange={(e) => setDetail(e.target.value)}
                    className="weather-query-input typhoon-query-detail"
                >
                    <option value="current">当前信息</option>
                    <option value="full">完整路径</option>
                </select>
                <label className="typhoon-query-active-toggle">
                    <input
                        type="checkbox"
                        checked={activeOnly}
                        onChange={(e) => setActiveOnly(e.target.checked)}
                        disabled={isIdQuery}
                    />
                    <span>仅活跃</span>
                </label>
                <button className="btn weather-query-btn" onClick={searchTyphoon} disabled={loading}>
                    {loading ? '查询中...' : '查询'}
                </button>
                <button
                    className="btn weather-query-btn weather-query-btn-secondary"
                    onClick={resetTyphoonQuery}
                    disabled={loading}
                >
                    清空
                </button>
            </div>

            {loading && (
                <div className="weather-query-loading">
                    <CircularProgress size={24} />
                    <Typography variant="body2" className="weather-query-loading-text">
                        正在查询台风信息，请稍候...
                    </Typography>
                </div>
            )}

            {!loading && error && (
                <div className="weather-query-error">
                    <Typography variant="body2" className="weather-query-error-text">{error}</Typography>
                </div>
            )}

            {!loading && !error && result && (
                <div className="weather-query-result">
                    {result.query_mode === 'id' ? renderIdResult() : renderListResult()}
                </div>
            )}
        </div>
    );
}
