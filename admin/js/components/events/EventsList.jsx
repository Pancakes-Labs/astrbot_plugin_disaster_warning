const { Typography, Collapse, CircularProgress } = MaterialUI;
const { useState, useMemo } = React;

/**
 * 事件列表页面级主控制器组件 (EventsList)
 * 编排并呈现整个历史灾害事件的列表与过滤跳转页面。
 * 该组件属于典型的容器型页面，其主要的检索和过滤状态代理给 `useEventsQuery` 进行数据响应，
 * 分页逻辑通过 PaginationBar 控制，同一事件的多报折叠聚合则由全局的 EventGrouping 工具做预处理。
 */
function EventsList() {
    // 从上下文获取全局配置的显示时区
    const { state } = useAppContext();
    const displayTimezone = state.config.displayTimezone || 'UTC+8';
    
    // 本地状态：存储当前处于展开时间轴状态下的事件 ID 集合 (Set)
    const [expandedEvents, setExpandedEvents] = useState(new Set());
    
    // 实例化滚动记忆保持 Hook，确保翻页或重载时滚动高度不丢失
    const {
        scrollRef: eventsScrollRef,
        preserveScrollPosition,
    } = usePreservedScroll([]);

    // 注册并驱动查询请求的主事件查询 Query 钩子
    const query = useEventsQuery({
        wsEvents: state.events,
        wsConnected: state.wsConnected,
        preserveScrollPosition,
    });

    // 从 Query 钩子中解构所有控制因子
    const {
        filterType, setFilterType,
        currentPage,
        totalPages,
        total,
        events,
        loading,
        pageSize, setPageSize,
        maxPageSize,
        pageInput, setPageInput,
        sourceFilterMode, setSourceFilterMode,
        selectedSources, setSelectedSources,
        sourceOptions,
        magnitudeFilter, setMagnitudeFilter,
        magnitudeOrder, setMagnitudeOrder,
        keyword, setKeyword,
        windSpeedFilter, setWindSpeedFilter,
        timePreset, setTimePreset,
        timeFrom, setTimeFrom,
        timeTo, setTimeTo,
        depthFilter, setDepthFilter,
        intensityFilter, setIntensityFilter,
        maxPressureFilter, setMaxPressureFilter,
        activeOnly, setActiveOnly,
        resetFilters,
        goToPage,
    } = query;

    // 对获取的单条列表数据进行分析，若在一定时间阈值内且震源相同，自动聚合成带有 updateCount 的事件组
    const groupedEvents = useMemo(() => window.EventGrouping.groupEvents(events), [events]);

    // 对原始的数据源进行标准化映射（过滤多余字段）
    const availableSources = useMemo(() => (
        window.EventFormatters.normalizeSourceOptions(sourceOptions)
    ), [sourceOptions]);

    // 副作用：当多选数据源发生变动时，检查已选数据源的合法性，剔除失效的已选项
    React.useEffect(() => {
        if (selectedSources.length === 0) return;
        const validSet = new Set(availableSources.map((source) => source.value));
        const nextSelected = selectedSources.filter((source) => validSet.has(source));
        if (nextSelected.length !== selectedSources.length) {
            setSelectedSources(nextSelected);
        }
    }, [availableSources, selectedSources, setSelectedSources]);

    // 计算分页规格备选项列表（限制最大 pageSize 不得高过服务端允许的最大极值）
    const pageSizeOptions = useMemo(() => {
        const base = [20, 50, 100, 200].filter((size) => size <= maxPageSize);
        const merged = Array.from(new Set([...base, maxPageSize])).filter((size) => size > 0);
        merged.sort((a, b) => a - b);
        return merged;
    }, [maxPageSize]);

    // 动态生成分页按钮页码序列，中间包含必要的省略号 '…'
    const paginationItems = useMemo(() => {
        if (totalPages <= 0) return [];
        // 若总页数很少，则直接全部渲染
        if (totalPages <= 7) return Array.from({ length: totalPages }, (_, idx) => idx + 1);

        const items = [1];
        const start = Math.max(2, currentPage - 1);
        const end = Math.min(totalPages - 1, currentPage + 1);
        if (start > 2) items.push('ellipsis-left');
        for (let page = start; page <= end; page += 1) items.push(page);
        if (end < totalPages - 1) items.push('ellipsis-right');
        items.push(totalPages);
        return items;
    }, [currentPage, totalPages]);

    // 校验直接输入页码跳转的合法性
    const pageInputNumber = Number(pageInput);
    const canJump = Number.isInteger(pageInputNumber)
        && pageInputNumber >= 1
        && pageInputNumber <= Math.max(totalPages, 1)
        && pageInputNumber !== currentPage;

    /**
     * 页码跳转触发器
     */
    const handlePageJump = () => {
        if (canJump) goToPage(pageInputNumber);
    };

    /**
     * 切换选中事件组的展开/折叠状态
     */
    const toggleEventGroup = (groupId) => {
        setExpandedEvents((prev) => {
            const next = new Set(prev);
            if (next.has(groupId)) next.delete(groupId);
            else next.add(groupId);
            return next;
        });
    };

    /**
     * 数据源单选与多选筛选方式的切换
     */
    const handleSourceFilterModeChange = (mode) => {
        setSourceFilterMode(mode);
        // 如果从多选切换为单选，只保留数组中的第一项作为激活数据源
        if (mode === 'single' && selectedSources.length > 1) {
            setSelectedSources([selectedSources[0]]);
        }
    };

    /**
     * 单选模式切换数据源
     */
    const handleSourceSelectChange = (event) => {
        const value = (event.target.value || '').trim();
        setSelectedSources(value ? [value] : []);
    };

    /**
     * 多选模式勾选或取消勾选单个数据源
     */
    const handleSourceCheckboxToggle = (source) => {
        setSelectedSources((prev) => (
            prev.includes(source)
                ? prev.filter((item) => item !== source)
                : [...prev, source]
        ));
    };

    // 数据源多选文案的简易提取
    const selectedSourceSummary = useMemo(() => {
        if (selectedSources.length === 0) return '全部数据源';
        return `已选 ${selectedSources.length} 个数据源`;
    }, [selectedSources]);

    return (
        <div className="events-list-shell">
            {/* 顶层过滤配置面板 */}
            <EventFilters
                total={total}
                filterType={filterType}
                setFilterType={setFilterType}
                magnitudeFilter={magnitudeFilter}
                setMagnitudeFilter={setMagnitudeFilter}
                magnitudeOrder={magnitudeOrder}
                setMagnitudeOrder={setMagnitudeOrder}
                keyword={keyword}
                setKeyword={setKeyword}
                windSpeedFilter={windSpeedFilter}
                setWindSpeedFilter={setWindSpeedFilter}
                timePreset={timePreset}
                setTimePreset={setTimePreset}
                timeFrom={timeFrom}
                setTimeFrom={setTimeFrom}
                timeTo={timeTo}
                setTimeTo={setTimeTo}
                depthFilter={depthFilter}
                setDepthFilter={setDepthFilter}
                intensityFilter={intensityFilter}
                setIntensityFilter={setIntensityFilter}
                maxPressureFilter={maxPressureFilter}
                setMaxPressureFilter={setMaxPressureFilter}
                activeOnly={activeOnly}
                setActiveOnly={setActiveOnly}
                availableSources={availableSources}
                sourceFilterMode={sourceFilterMode}
                onSourceFilterModeChange={handleSourceFilterModeChange}
                selectedSources={selectedSources}
                onSourceSelectChange={handleSourceSelectChange}
                onSourceCheckboxToggle={handleSourceCheckboxToggle}
                setSelectedSources={setSelectedSources}
                selectedSourceSummary={selectedSourceSummary}
                onResetFilters={resetFilters}
            />

            {/* 加载、空列表与实际列表多态渲染 */}
            {loading ? (
                // A. 服务端正在加载骨架进度，添加好的等待提示文字，消除用户的焦虑感
                <div className="card events-state-card events-loading-card">
                    <CircularProgress size={32} style={{ marginBottom: '12px' }} />
                    <Typography variant="body2" style={{ color: 'var(--md-sys-color-on-surface-variant)' }}>
                        正在检索并整理历史事件列表，请稍等...
                    </Typography>
                </div>
            ) : groupedEvents.length === 0 ? (
                // B. 空列表友好占位
                <div className="card events-state-card events-empty-card">
                    <Typography variant="h2" className="events-empty-icon">📭</Typography>
                    <Typography variant="body1" className="events-empty-title">暂无该类型的事件记录</Typography>
                    <Typography variant="body2" className="events-empty-subtitle">系统正在持续监测中...</Typography>
                </div>
            ) : (
                // C. 实际列表内容呈现
                <>
                    <div className="events-scroll-window" ref={eventsScrollRef}>
                        <div className="events-list">
                            {groupedEvents.map((group) => {
                                const isExpanded = expandedEvents.has(group.id);
                                const latestType = String(group.latestEvent?.type || '').toLowerCase();
                                const weatherDetail = String(group.latestEvent?.weather_detail || '').trim();
                                // 与 EventCard 对齐：earthquake / earthquake_warning 均可展开情报正文
                                const isEarthquakeType = latestType === 'earthquake'
                                    || latestType === 'earthquake_warning';
                                // 气象可回退 description；地震仅认 weather_detail，避免标题误当正文
                                const detailBody = latestType === 'weather_alarm'
                                    ? (weatherDetail || String(group.latestEvent?.description || '').trim())
                                    : (isEarthquakeType ? weatherDetail : '');
                                // 多报更新：真正的同源折叠
                                // 气象预警 / 地震情报补充正文：额外提供正文展开语义
                                const hasMultiReport = group.updateCount > 1;
                                const canExpandDetail = (
                                    (latestType === 'weather_alarm' || isEarthquakeType)
                                    && !!detailBody
                                    && !hasMultiReport
                                );
                                const isExpandable = hasMultiReport || canExpandDetail;
                                const cardEvent = {
                                    ...group.latestEvent,
                                    updateCount: group.updateCount,
                                    _groupType: group.latestEvent.type,
                                    _groupMagnitude: group.latestEvent.magnitude,
                                };
                                const toggleGroup = () => toggleEventGroup(group.id);
                                const onExpandKeyDown = (e) => {
                                    if (!isExpandable) return;
                                    if (e.key === 'Enter' || e.key === ' ') {
                                        e.preventDefault();
                                        toggleGroup();
                                    }
                                };

                                return (
                                    <div key={group.id} className="event-group">
                                        {/* 折叠收起形态：展示带有多报更新数量 / 正文入口的事件概要卡片 */}
                                        <Collapse in={!isExpanded} timeout={220} unmountOnExit>
                                            <div
                                                role={isExpandable ? 'button' : undefined}
                                                tabIndex={isExpandable ? 0 : undefined}
                                                aria-expanded={isExpandable ? false : undefined}
                                                onClick={() => isExpandable && toggleGroup()}
                                                onKeyDown={onExpandKeyDown}
                                            >
                                                <EventCard
                                                    event={cardEvent}
                                                    displayTimezone={displayTimezone}
                                                    isExpandable={hasMultiReport}
                                                    isExpanded={false}
                                                />
                                            </div>
                                        </Collapse>

                                        {/* 展开形态：单一连续卡片壳 = 顶部事件摘要 + 下方时间线 / 正文 */}
                                        <Collapse in={isExpanded} timeout={260} unmountOnExit>
                                            <div className={`event-group-expanded-shell ${hasMultiReport ? 'has-timeline' : 'is-detail-only'}`}>
                                                <div
                                                    className="event-group-sticky-card"
                                                    role="button"
                                                    tabIndex={0}
                                                    aria-expanded={true}
                                                    onClick={toggleGroup}
                                                    onKeyDown={(e) => {
                                                        if (e.key === 'Enter' || e.key === ' ') {
                                                            e.preventDefault();
                                                            toggleGroup();
                                                        }
                                                    }}
                                                >
                                                    <EventCard
                                                        event={cardEvent}
                                                        displayTimezone={displayTimezone}
                                                        isExpandable={hasMultiReport}
                                                        isExpanded={true}
                                                        hideExpandBadge={false}
                                                    />
                                                </div>
                                                {hasMultiReport && (
                                                    <div className="event-group-timeline-section">
                                                        <EventGroupTimeline
                                                            group={group}
                                                            displayTimezone={displayTimezone}
                                                        />
                                                    </div>
                                                )}
                                            </div>
                                        </Collapse>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    {/* 最底部分页导航器 */}
                    <PaginationBar
                        total={total}
                        totalPages={totalPages}
                        currentPage={currentPage}
                        pageSize={pageSize}
                        pageSizeOptions={pageSizeOptions}
                        onPageSizeChange={setPageSize}
                        pageInput={pageInput}
                        onPageInputChange={setPageInput}
                        canJump={canJump}
                        onPageJump={handlePageJump}
                        paginationItems={paginationItems}
                        goToPage={goToPage}
                    />
                </>
            )}
        </div>
    );
}
