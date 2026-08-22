const { Typography } = MaterialUI;

/**
 * 气象与海啸预警特定层级判色选项配置
 */
const EVENT_LEVEL_FILTER_CONFIG = {
    weather: {
        label: '预警颜色',
        allLabel: '全部颜色',
        options: [
            { value: 'weather_white', label: '白色' },
            { value: 'weather_blue', label: '蓝色' },
            { value: 'weather_yellow', label: '黄色' },
            { value: 'weather_orange', label: '橙色' },
            { value: 'weather_red', label: '红色' },
        ],
    },
    tsunami: {
        label: '海啸级别',
        allLabel: '全部级别',
        options: [
            { value: 'tsunami_info', label: '信息' },
            { value: 'tsunami_warning', label: '预警' },
        ],
    },
    typhoon: {
        label: '台风强度',
        allLabel: '全部强度',
        // 等级选项从 typhoonFormatters 统一导出，保持与后端筛选映射一致
        options: (window.DisasterTyphoonFormatters && window.DisasterTyphoonFormatters.TYPHOON_LEVEL_FILTER_OPTIONS) || [
            { value: 'typhoon_tropical_depression', label: '热带低压' },
            { value: 'typhoon_tropical_storm', label: '热带风暴' },
            { value: 'typhoon_severe_tropical_storm', label: '强热带风暴' },
            { value: 'typhoon', label: '台风' },
            { value: 'typhoon_severe_typhoon', label: '强台风' },
            { value: 'typhoon_super_typhoon', label: '超强台风' },
        ],
    },
};

const TIME_PRESET_OPTIONS = [
    { value: 'all', label: '不限时间' },
    { value: '1h', label: '近 1 小时' },
    { value: '24h', label: '近 24 小时' },
    { value: '7d', label: '近 7 天' },
    { value: '30d', label: '近 30 天' },
    { value: 'custom', label: '自定义' },
];

const DEPTH_FILTER_OPTIONS = [
    { value: 'all', label: '全部深度' },
    { value: 'shallow', label: '浅源 ≤ 70 km' },
    { value: 'intermediate', label: '中源 70–300 km' },
    { value: 'deep', label: '深源 ≥ 300 km' },
    { value: 'lte:30', label: '≤ 30 km' },
    { value: 'lte:10', label: '≤ 10 km' },
    { value: 'gte:100', label: '≥ 100 km' },
];

// 中国烈度（1–12）与 JMA/CWA 震度（1–7）分轨，value 编码：cn:<n> / jma:<n>
const CN_INTENSITY_FILTER_OPTIONS = [
    { value: 'all', label: '全部烈度' },
    { value: 'cn:3', label: '烈度 ≥ 3' },
    { value: 'cn:4', label: '烈度 ≥ 4' },
    { value: 'cn:5', label: '烈度 ≥ 5' },
    { value: 'cn:6', label: '烈度 ≥ 6' },
    { value: 'cn:7', label: '烈度 ≥ 7' },
    { value: 'cn:8', label: '烈度 ≥ 8' },
    { value: 'cn:9', label: '烈度 ≥ 9' },
    { value: 'cn:10', label: '烈度 ≥ 10' },
];

const JMA_SCALE_FILTER_OPTIONS = [
    { value: 'all', label: '全部震度' },
    { value: 'jma:3', label: '震度 ≥ 3' },
    { value: 'jma:4', label: '震度 ≥ 4' },
    { value: 'jma:4.5', label: '震度 ≥ 5弱' },
    { value: 'jma:5', label: '震度 ≥ 5强' },
    { value: 'jma:5.5', label: '震度 ≥ 6弱' },
    { value: 'jma:6', label: '震度 ≥ 6强' },
    { value: 'jma:7', label: '震度 ≥ 7' },
];

/**
 * 事件多维过滤器头部组件 (EventFilters)
 * 渲染于事件记录视图的顶部。提供以下高级过滤功能：
 * 1. 事件大类切换胶囊栏（全部、地震预警、地震速报、气象、海啸、台风）。
 * 2. 时间预设/自定义范围、震级/级别、深度、烈度、台风风速/气压/活跃态。
 * 3. 动态加载可用数据源（支持单选下拉、多选 checkbox 下拉详情菜单）。
 * 4. 全局地点与文本关键字搜索框、筛选摘要条与一键重置。
 */
function EventFilters({
    total,
    filterType,
    setFilterType,
    magnitudeFilter,
    setMagnitudeFilter,
    magnitudeOrder,
    setMagnitudeOrder,
    keyword,
    setKeyword,
    windSpeedFilter,
    setWindSpeedFilter,
    timePreset,
    setTimePreset,
    timeFrom,
    setTimeFrom,
    timeTo,
    setTimeTo,
    depthFilter,
    setDepthFilter,
    intensityFilter,
    setIntensityFilter,
    maxPressureFilter,
    setMaxPressureFilter,
    activeOnly,
    setActiveOnly,
    availableSources,
    sourceFilterMode,
    onSourceFilterModeChange,
    selectedSources,
    onSourceSelectChange,
    onSourceCheckboxToggle,
    setSelectedSources,
    selectedSourceSummary,
    onResetFilters,
}) {
    // 指向多选 dropdown details 元素的 ref，以便点击页面外部时自动折叠下拉框
    const detailsRef = React.useRef(null);

    React.useEffect(() => {
        const handleClickOutside = (event) => {
            if (detailsRef.current && !detailsRef.current.contains(event.target)) {
                detailsRef.current.removeAttribute('open');
            }
        };

        document.addEventListener('click', handleClickOutside);
        return () => {
            document.removeEventListener('click', handleClickOutside);
        };
    }, []);

    // 顶层事件胶囊大类定义
    const eventTypes = [
        { id: 'all', label: '全部' },
        { id: 'earthquake_warning', label: '地震预警' },
        { id: 'earthquake', label: '地震情报' },
        { id: 'weather', label: '气象预警' },
        { id: 'tsunami', label: '海啸预警' },
        { id: 'typhoon', label: '台风信息' },
    ];

    const isEarthquakeLike = filterType === 'all' || filterType === 'earthquake' || filterType === 'earthquake_warning';
    const isTyphoon = filterType === 'typhoon';

    // 根据所选模式，动态映射右侧多维过滤选项
    const levelFilterConfig = EVENT_LEVEL_FILTER_CONFIG[filterType] || null;
    const magnitudeFilterLabel = levelFilterConfig ? levelFilterConfig.label : '震级';
    const magnitudeFilterOptions = levelFilterConfig
        ? [{ value: 'all', label: levelFilterConfig.allLabel }, ...levelFilterConfig.options]
        : [
            { value: 'all', label: '全部震级' },
            { value: '3', label: 'M ≥ 3.0' },
            { value: '4', label: 'M ≥ 4.0' },
            { value: '5', label: 'M ≥ 5.0' },
            { value: '6', label: 'M ≥ 6.0' },
            { value: '7', label: 'M ≥ 7.0' },
            { value: '8', label: 'M ≥ 8.0' },
        ];

    const windOptions = (window.DisasterTyphoonFormatters && window.DisasterTyphoonFormatters.WIND_SPEED_FILTER_OPTIONS) || [
        { value: 'all', label: '全部峰值风速' },
    ];
    const pressureOptions = (window.DisasterTyphoonFormatters && window.DisasterTyphoonFormatters.PRESSURE_FILTER_OPTIONS) || [
        { value: 'all', label: '全部峰值气压' },
    ];

    const handleTypeChange = (nextType) => {
        setFilterType(nextType);
        setMagnitudeFilter('all');
        setMagnitudeOrder('default');
        setWindSpeedFilter('all');
        setDepthFilter('all');
        setIntensityFilter('all');
        setMaxPressureFilter('all');
        setActiveOnly(false);
    };

    const activeFilterChips = React.useMemo(() => {
        const chips = [];
        const typeLabel = eventTypes.find((item) => item.id === filterType)?.label || filterType;
        if (filterType !== 'all') {
            chips.push({ key: 'type', label: `类型：${typeLabel}` });
        }

        const timeLabel = TIME_PRESET_OPTIONS.find((item) => item.value === timePreset)?.label || timePreset;
        if (timePreset !== 'all') {
            if (timePreset === 'custom') {
                const fromText = timeFrom || '…';
                const toText = timeTo || '…';
                chips.push({ key: 'time', label: `时间：${fromText} ~ ${toText}` });
            } else {
                chips.push({ key: 'time', label: `时间：${timeLabel}` });
            }
        }

        if (magnitudeFilter !== 'all') {
            const magLabel = magnitudeFilterOptions.find((item) => item.value === magnitudeFilter)?.label || magnitudeFilter;
            chips.push({ key: 'magnitude', label: `${magnitudeFilterLabel}：${magLabel}` });
        }

        if (!levelFilterConfig && magnitudeOrder !== 'default') {
            chips.push({
                key: 'order',
                label: magnitudeOrder === 'desc' ? '排序：震级降序' : '排序：震级升序',
            });
        }

        if (isEarthquakeLike && depthFilter !== 'all') {
            const depthLabel = DEPTH_FILTER_OPTIONS.find((item) => item.value === depthFilter)?.label || depthFilter;
            chips.push({ key: 'depth', label: `深度：${depthLabel}` });
        }

        if (isEarthquakeLike && intensityFilter !== 'all') {
            const intensityLabel = (
                CN_INTENSITY_FILTER_OPTIONS.find((item) => item.value === intensityFilter)
                || JMA_SCALE_FILTER_OPTIONS.find((item) => item.value === intensityFilter)
            )?.label || intensityFilter;
            const isJma = String(intensityFilter).startsWith('jma:');
            chips.push({
                key: 'intensity',
                label: isJma ? `震度：${intensityLabel}` : `烈度：${intensityLabel}`,
            });
        }

        if (isTyphoon && windSpeedFilter !== 'all') {
            const windLabel = windOptions.find((item) => item.value === windSpeedFilter)?.label || windSpeedFilter;
            chips.push({ key: 'wind', label: `峰值风速：${windLabel}` });
        }

        if (isTyphoon && maxPressureFilter !== 'all') {
            const pressureLabel = pressureOptions.find((item) => item.value === maxPressureFilter)?.label || maxPressureFilter;
            chips.push({ key: 'pressure', label: `峰值气压：${pressureLabel}` });
        }

        if (isTyphoon && activeOnly) {
            chips.push({ key: 'active', label: '仅活跃台风' });
        }

        if (selectedSources.length > 0) {
            chips.push({ key: 'source', label: selectedSourceSummary });
        }

        if (String(keyword || '').trim()) {
            chips.push({ key: 'keyword', label: `关键词：${String(keyword).trim()}` });
        }

        return chips;
    }, [
        filterType,
        timePreset,
        timeFrom,
        timeTo,
        magnitudeFilter,
        magnitudeFilterOptions,
        magnitudeFilterLabel,
        magnitudeOrder,
        levelFilterConfig,
        isEarthquakeLike,
        depthFilter,
        intensityFilter,
        isTyphoon,
        windSpeedFilter,
        windOptions,
        maxPressureFilter,
        pressureOptions,
        activeOnly,
        selectedSources,
        selectedSourceSummary,
        keyword,
    ]);

    const hasActiveFilters = activeFilterChips.length > 0;

    return (
        <div className="events-filters-header">
            {/* 顶排：标题与匹配总数 */}
            <div className="events-filters-title-row">
                <div className="events-filters-title-group">
                    <Typography variant="h5" className="events-filters-title">最近事件记录</Typography>
                    {total > 0 && <Typography variant="body2" className="events-filters-total">共 {total} 条</Typography>}
                </div>
            </div>

            {/* 下排：过滤器交互控制台 */}
            <div className="event-filters-toolbar">
                {/* 1. 过滤流：事件大类胶囊过滤排（radio 语义：aria-pressed 标注选中态） */}
                <div className="event-filters-primary-row">
                    <div className="event-filters-primary-label">事件类型</div>
                    <div className="filter-group event-filter-group-nowrap event-filter-type-group" role="group" aria-label="事件类型">
                        {eventTypes.map((item) => (
                            <button
                                key={item.id}
                                type="button"
                                className={`btn-filter event-filter-pill ${filterType === item.id ? 'active' : ''}`}
                                onClick={() => handleTypeChange(item.id)}
                                aria-pressed={filterType === item.id}
                            >
                                {filterType === item.id && <span className="event-filter-checkmark" aria-hidden="true">✓</span>}
                                {item.label}
                            </button>
                        ))}
                    </div>
                </div>

                {/* 2. 时间维度（预设 + 自定义） */}
                <div className="event-filters-time-row">
                    <div className={`filter-group event-filter-group-nowrap event-filter-field-group event-filter-field-card event-filter-field-card-time ${timePreset === 'custom' ? 'is-custom' : ''}`}>
                        <Typography variant="body2" className="event-filter-label">时间范围</Typography>
                        <div className="event-filter-inline-controls event-filter-inline-controls-time">
                            <div className="event-filter-time-presets" role="group" aria-label="时间范围">
                                {TIME_PRESET_OPTIONS.map((option) => (
                                    <button
                                        key={option.value}
                                        type="button"
                                        className={`event-filter-time-chip ${timePreset === option.value ? 'active' : ''}`}
                                        onClick={() => setTimePreset(option.value)}
                                        aria-pressed={timePreset === option.value}
                                    >
                                        {option.label}
                                    </button>
                                ))}
                            </div>
                            {timePreset === 'custom' && (
                                <div className="event-filter-custom-time" aria-label="自定义时间范围">
                                    <label className="event-filter-datetime-field">
                                        <span>开始</span>
                                        <input
                                            type="datetime-local"
                                            value={timeFrom}
                                            onChange={(e) => setTimeFrom(e.target.value)}
                                            className="event-filter-select event-filter-datetime-input"
                                        />
                                    </label>
                                    <span className="event-filter-datetime-separator">至</span>
                                    <label className="event-filter-datetime-field">
                                        <span>结束</span>
                                        <input
                                            type="datetime-local"
                                            value={timeTo}
                                            onChange={(e) => setTimeTo(e.target.value)}
                                            className="event-filter-select event-filter-datetime-input"
                                        />
                                    </label>
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                {/* 3. 细粒度过滤与搜索排 */}
                <div className="event-filters-secondary-row">
                    {/* A. 震级限制与排序 / 颜色预警过滤选择器 */}
                    <div className="filter-group event-filter-group-nowrap event-filter-field-group event-filter-field-card event-filter-field-card-magnitude">
                        <Typography variant="body2" className="event-filter-label" id="event-filter-magnitude-label">{magnitudeFilterLabel}</Typography>
                        <div className="event-filter-inline-controls">
                            <select aria-labelledby="event-filter-magnitude-label" value={magnitudeFilter} onChange={(e) => setMagnitudeFilter(e.target.value)} className="event-filter-select event-filter-select-md">
                                {magnitudeFilterOptions.map((option) => (
                                    <option key={option.value} value={option.value}>{option.label}</option>
                                ))}
                            </select>
                            {/* 仅在地震类型下显示震级排序选择器 */}
                            {!levelFilterConfig && (
                                <select value={magnitudeOrder} onChange={(e) => setMagnitudeOrder(e.target.value)} className="event-filter-select event-filter-select-md" aria-label="震级排序">
                                    <option value="default">默认排序</option>
                                    <option value="desc">震级降序</option>
                                    <option value="asc">震级升序</option>
                                </select>
                            )}
                        </div>
                    </div>

                    {/* B1. 地震深度 */}
                    {isEarthquakeLike && (
                        <div className="filter-group event-filter-group-nowrap event-filter-field-group event-filter-field-card event-filter-field-card-depth">
                            <Typography variant="body2" className="event-filter-label" id="event-filter-depth-label">震源深度</Typography>
                            <div className="event-filter-inline-controls">
                                <select aria-labelledby="event-filter-depth-label" value={depthFilter} onChange={(e) => setDepthFilter(e.target.value)} className="event-filter-select event-filter-select-md">
                                    {DEPTH_FILTER_OPTIONS.map((option) => (
                                        <option key={option.value} value={option.value}>{option.label}</option>
                                    ))}
                                </select>
                            </div>
                        </div>
                    )}

                    {/* B2. 烈度 / 震度：同一卡片内分轨，标签在上、下拉在下，避免窄宽截断 */}
                    {isEarthquakeLike && (
                        <div className="filter-group event-filter-group-nowrap event-filter-field-group event-filter-field-card event-filter-field-card-intensity">
                            <Typography variant="body2" className="event-filter-label">烈度 / 震度</Typography>
                            <div className="event-filter-intensity-stack">
                                <label className="event-filter-intensity-row">
                                    <span className="event-filter-intensity-rail">中国烈度（1–12）</span>
                                    <select
                                        value={String(intensityFilter).startsWith('cn:') ? intensityFilter : 'all'}
                                        onChange={(e) => setIntensityFilter(e.target.value)}
                                        className="event-filter-select event-filter-select-md"
                                        aria-label="中国烈度筛选"
                                    >
                                        {CN_INTENSITY_FILTER_OPTIONS.map((option) => (
                                            <option key={option.value} value={option.value}>{option.label}</option>
                                        ))}
                                    </select>
                                </label>
                                <label className="event-filter-intensity-row">
                                    <span className="event-filter-intensity-rail">JMA / CWA 震度（1–7）</span>
                                    <select
                                        value={String(intensityFilter).startsWith('jma:') ? intensityFilter : 'all'}
                                        onChange={(e) => setIntensityFilter(e.target.value)}
                                        className="event-filter-select event-filter-select-md"
                                        aria-label="JMA/CWA 震度筛选"
                                    >
                                        {JMA_SCALE_FILTER_OPTIONS.map((option) => (
                                            <option key={option.value} value={option.value}>{option.label}</option>
                                        ))}
                                    </select>
                                </label>
                            </div>
                        </div>
                    )}

                    {/* B3. 台风历史峰值风速（主表 wind_speed） */}
                    {isTyphoon && (
                        <div className="filter-group event-filter-group-nowrap event-filter-field-group event-filter-field-card event-filter-field-card-wind-speed">
                            <Typography variant="body2" className="event-filter-label">历史峰值风速</Typography>
                            <div className="event-filter-inline-controls">
                                <select value={windSpeedFilter} onChange={(e) => setWindSpeedFilter(e.target.value)} className="event-filter-select event-filter-select-md">
                                    {windOptions.map((option) => (
                                        <option key={option.value} value={option.value}>{option.label}</option>
                                    ))}
                                </select>
                            </div>
                        </div>
                    )}

                    {/* B4. 台风历史最低中心气压（主表 pressure） */}
                    {isTyphoon && (
                        <div className="filter-group event-filter-group-nowrap event-filter-field-group event-filter-field-card event-filter-field-card-pressure">
                            <Typography variant="body2" className="event-filter-label">历史最低气压</Typography>
                            <div className="event-filter-inline-controls">
                                <select value={maxPressureFilter} onChange={(e) => setMaxPressureFilter(e.target.value)} className="event-filter-select event-filter-select-md">
                                    {pressureOptions.map((option) => (
                                        <option key={option.value} value={option.value}>{option.label}</option>
                                    ))}
                                </select>
                            </div>
                        </div>
                    )}

                    {/* B5. 仅活跃台风 */}
                    {isTyphoon && (
                        <div className="filter-group event-filter-group-nowrap event-filter-field-group event-filter-field-card event-filter-field-card-active">
                            <Typography variant="body2" className="event-filter-label">活跃状态</Typography>
                            <div className="event-filter-inline-controls">
                                <label className={`event-filter-toggle ${activeOnly ? 'active' : ''}`}>
                                    <input
                                        type="checkbox"
                                        checked={Boolean(activeOnly)}
                                        onChange={(e) => setActiveOnly(e.target.checked)}
                                    />
                                    <span>仅显示活跃台风</span>
                                </label>
                            </div>
                        </div>
                    )}

                    {/* C. 数据源过滤 (支持单选与多选模式) */}
                    {availableSources.length > 0 && (
                        <div className="filter-group event-filter-group-nowrap event-filter-field-group event-filter-field-card event-filter-field-card-source">
                            <Typography variant="body2" className="event-filter-label" id="event-filter-source-label">数据源</Typography>
                            <div className="event-filter-inline-controls event-filter-inline-controls-source">
                                <select aria-label="数据源选择模式" value={sourceFilterMode} onChange={(e) => onSourceFilterModeChange(e.target.value)} className="event-filter-select event-filter-select-sm">
                                    <option value="single">单选</option>
                                    <option value="multi">多选</option>
                                </select>

                                {sourceFilterMode === 'single' ? (
                                    <select aria-labelledby="event-filter-source-label" value={selectedSources[0] || ''} onChange={onSourceSelectChange} className="event-filter-select event-filter-source-select">
                                        <option value="">全部数据源</option>
                                        {availableSources.map((source) => (
                                            <option key={source.normalizedKey} value={source.value} title={source.label}>
                                                {source.label}
                                            </option>
                                        ))}
                                    </select>
                                ) : (
                                    <details ref={detailsRef} className="event-filter-source-details">
                                        <summary className="event-filter-source-summary">
                                            {selectedSourceSummary}
                                        </summary>
                                        <div className="event-filter-source-menu" role="group" aria-label="选择数据源">
                                            <label className="event-filter-checkbox-label">
                                                <input type="checkbox" checked={selectedSources.length === 0} onChange={() => setSelectedSources([])} />
                                                全部数据源
                                            </label>
                                            {availableSources.map((source) => (
                                                <label key={source.normalizedKey} className="event-filter-checkbox-label event-filter-checkbox-label-lg">
                                                    <input
                                                        type="checkbox"
                                                        checked={selectedSources.includes(source.value)}
                                                        onChange={() => onSourceCheckboxToggle(source.value)}
                                                    />
                                                    {source.label}
                                                </label>
                                            ))}
                                        </div>
                                    </details>
                                )}
                            </div>
                        </div>
                    )}

                    {/* D. 文本/地点关键字模糊检索输入框 */}
                    <div className="filter-group event-filter-group-nowrap event-filter-field-group event-filter-field-card event-filter-field-card-keyword">
                        <Typography variant="body2" className="event-filter-label" id="event-filter-keyword-label">关键词</Typography>
                        <div className="event-filter-inline-controls event-filter-inline-controls-keyword">
                            <input
                                value={keyword}
                                onChange={(e) => setKeyword(e.target.value)}
                                placeholder={filterType === 'typhoon' ? '搜索台风名称、编号、来源...' : '搜索地点、标题、来源...'}
                                className="event-filter-select event-filter-keyword-input"
                                aria-labelledby="event-filter-keyword-label"
                            />
                        </div>
                    </div>
                </div>

                {/* 4. 当前生效筛选摘要 + 重置（放在工具栏右下角，更显眼） */}
                <div className="event-filters-footer-row">
                    <div className="event-filters-summary-row">
                        {hasActiveFilters ? (
                            <>
                                <div className="event-filters-summary-label">当前筛选</div>
                                <div className="event-filters-summary-chips">
                                    {activeFilterChips.map((chip) => (
                                        <span key={chip.key} className="event-filter-summary-chip">{chip.label}</span>
                                    ))}
                                </div>
                            </>
                        ) : (
                            <Typography variant="caption" className="event-filters-footer-hint">
                                未启用额外筛选
                            </Typography>
                        )}
                    </div>
                    <div className="event-filters-footer-actions">
                        {hasActiveFilters && (
                            <Typography variant="caption" className="events-filters-active-count">
                                已启用 {activeFilterChips.length} 项
                            </Typography>
                        )}
                        <button
                            type="button"
                            className="event-filter-reset-btn"
                            onClick={onResetFilters}
                            disabled={!hasActiveFilters}
                            title="重置全部筛选条件"
                        >
                            重置筛选
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
