/**
 * 历史灾害事件列表的多维过滤、分页查询与实时推送同步钩子。
 *
 * 核心机制说明：
 * 1. 自动请求终止：在用户频繁点选切换灾害大类或者修改搜索关键词时，
 *    通过请求中止控制器提前中断先前未响应的网络请求，避免前后两次网络数据返回冲突。
 * 2. 滚动状态保护：在实时推送新事件发生或长连接网络重连时，如果直接刷新列表会导致滚动条强行弹回顶部。
 *    这里通过保存滚动高度的方法，静默拉取数据并更新列表，从而保留用户的滑屏视野。
 * 3. 动态过滤路由：自动纠正气象预警和海啸预警等没有常规震级数值的事件分类，
 *    自动将震级过滤条件转化为气象或海啸的级别过滤。
 * 4. 关键词防抖 + localStorage 筛选状态持久化，降低无效请求并提升值守体验。
 */
function useEventsQuery({ wsEvents, wsConnected, preserveScrollPosition }) {
    const eventsApi = window.DisasterEventsApi;
    const STORAGE_KEY = 'astrbot_events_list_filters_v1';
    const KEYWORD_DEBOUNCE_MS = 320;

    const DEFAULT_FILTERS = {
        filterType: 'all',
        pageSize: 50,
        sourceFilterMode: 'single',
        selectedSources: [],
        magnitudeFilter: 'all',
        magnitudeOrder: 'default',
        keyword: '',
        windSpeedFilter: 'all',
        timePreset: 'all',
        timeFrom: '',
        timeTo: '',
        depthFilter: 'all',
        intensityFilter: 'all',
        maxPressureFilter: 'all',
        activeOnly: false,
    };

    // intensityFilter 编码：all | cn:<n> | jma:<n>
    // 中国烈度 1-12 与 JMA/CWA 震度 1-7 分轨，避免混比。

    const loadPersistedFilters = () => {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return { ...DEFAULT_FILTERS };
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== 'object') return { ...DEFAULT_FILTERS };
            return {
                ...DEFAULT_FILTERS,
                ...parsed,
                selectedSources: Array.isArray(parsed.selectedSources)
                    ? parsed.selectedSources.map((item) => String(item || '').trim()).filter(Boolean)
                    : [],
                activeOnly: Boolean(parsed.activeOnly),
            };
        } catch (_error) {
            return { ...DEFAULT_FILTERS };
        }
    };

    const initialFilters = React.useMemo(() => loadPersistedFilters(), []);

    // 列表过滤与分页控制状态
    const [filterType, setFilterType] = React.useState(initialFilters.filterType);
    const [currentPage, setCurrentPage] = React.useState(1);
    const [totalPages, setTotalPages] = React.useState(0);
    const [total, setTotal] = React.useState(0);
    const [events, setEvents] = React.useState([]);
    const [loading, setLoading] = React.useState(false);
    const [pageSize, setPageSize] = React.useState(initialFilters.pageSize);
    const [maxPageSize, setMaxPageSize] = React.useState(200);
    const [pageInput, setPageInput] = React.useState('');
    const [sourceFilterMode, setSourceFilterMode] = React.useState(initialFilters.sourceFilterMode);
    const [selectedSources, setSelectedSources] = React.useState(initialFilters.selectedSources);
    const [sourceOptions, setSourceOptions] = React.useState([]);
    const [magnitudeFilter, setMagnitudeFilter] = React.useState(initialFilters.magnitudeFilter);
    const [magnitudeOrder, setMagnitudeOrder] = React.useState(initialFilters.magnitudeOrder);
    const [keyword, setKeyword] = React.useState(initialFilters.keyword);
    const [debouncedKeyword, setDebouncedKeyword] = React.useState(initialFilters.keyword);
    const [windSpeedFilter, setWindSpeedFilter] = React.useState(initialFilters.windSpeedFilter);
    const [timePreset, setTimePreset] = React.useState(initialFilters.timePreset);
    const [timeFrom, setTimeFrom] = React.useState(initialFilters.timeFrom);
    const [timeTo, setTimeTo] = React.useState(initialFilters.timeTo);
    const [depthFilter, setDepthFilter] = React.useState(initialFilters.depthFilter);
    const [intensityFilter, setIntensityFilter] = React.useState(initialFilters.intensityFilter);
    const [maxPressureFilter, setMaxPressureFilter] = React.useState(initialFilters.maxPressureFilter);
    const [activeOnly, setActiveOnly] = React.useState(Boolean(initialFilters.activeOnly));

    // 跨渲染周期的最新状态引用，以供异步事件拉取时获取最新快照
    const abortControllerRef = React.useRef(null);
    // 记录上次已处理的事件列表头部事件 id，用于判别是否真正出现新事件（避免心跳广播重复拉取）
    const wsHeadEventIdRef = React.useRef(null);
    const filterTypeRef = React.useRef(filterType);
    const pageSizeRef = React.useRef(pageSize);
    const selectedSourcesRef = React.useRef(selectedSources);
    const currentPageRef = React.useRef(currentPage);
    const magnitudeFilterRef = React.useRef(magnitudeFilter);
    const magnitudeOrderRef = React.useRef(magnitudeOrder);
    const keywordRef = React.useRef(debouncedKeyword);
    const windSpeedFilterRef = React.useRef(windSpeedFilter);
    const timePresetRef = React.useRef(timePreset);
    const timeFromRef = React.useRef(timeFrom);
    const timeToRef = React.useRef(timeTo);
    const depthFilterRef = React.useRef(depthFilter);
    const intensityFilterRef = React.useRef(intensityFilter);
    const maxPressureFilterRef = React.useRef(maxPressureFilter);
    const activeOnlyRef = React.useRef(activeOnly);

    // 关键词输入防抖，避免每敲一键都触发全量查询
    React.useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedKeyword(keyword);
        }, KEYWORD_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [keyword]);

    // 筛选状态持久化
    React.useEffect(() => {
        const payload = {
            filterType,
            pageSize,
            sourceFilterMode,
            selectedSources,
            magnitudeFilter,
            magnitudeOrder,
            keyword,
            windSpeedFilter,
            timePreset,
            timeFrom,
            timeTo,
            depthFilter,
            intensityFilter,
            maxPressureFilter,
            activeOnly,
        };
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
        } catch (_error) {
            // localStorage 不可用时静默忽略
        }
    }, [
        filterType,
        pageSize,
        sourceFilterMode,
        selectedSources,
        magnitudeFilter,
        magnitudeOrder,
        keyword,
        windSpeedFilter,
        timePreset,
        timeFrom,
        timeTo,
        depthFilter,
        intensityFilter,
        maxPressureFilter,
        activeOnly,
    ]);

    const formatDateTimeLocal = React.useCallback((date) => {
        const pad = (value) => String(value).padStart(2, '0');
        return [
            `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
            `${pad(date.getHours())}:${pad(date.getMinutes())}`,
        ].join('T');
    }, []);

    /**
     * 统一装配事件查询过滤参数，避免 useEffect / goToPage / WS 刷新三处重复。
     */
    const buildEventQueryFilters = React.useCallback((snapshot) => {
        const type = snapshot.filterType;
        const magnitudeValue = snapshot.magnitudeFilter;
        const orderValue = snapshot.magnitudeOrder;
        const windSpeedValue = snapshot.windSpeedFilter;
        const depthValue = snapshot.depthFilter;
        const intensityValue = snapshot.intensityFilter;
        const maxPressureValue = snapshot.maxPressureFilter;
        const usesLevelFilter = ['weather', 'tsunami', 'typhoon'].includes(type);
        const isEarthquakeLike = type === 'all' || type === 'earthquake' || type === 'earthquake_warning';

        const minMagnitude = usesLevelFilter || magnitudeValue === 'all' ? null : Number(magnitudeValue);
        const levelFilter = usesLevelFilter && magnitudeValue !== 'all' ? magnitudeValue : '';
        const magnitudeSort = usesLevelFilter || orderValue === 'default' ? '' : orderValue;
        const minWindSpeed = type === 'typhoon' && windSpeedValue !== 'all'
            ? Number(windSpeedValue)
            : null;

        let minDepth = null;
        let maxDepth = null;
        if (isEarthquakeLike && depthValue && depthValue !== 'all') {
            if (depthValue === 'shallow') {
                maxDepth = 70;
            } else if (depthValue === 'intermediate') {
                minDepth = 70;
                maxDepth = 300;
            } else if (depthValue === 'deep') {
                minDepth = 300;
            } else if (depthValue.startsWith('lte:')) {
                maxDepth = Number(depthValue.slice(4));
            } else if (depthValue.startsWith('gte:')) {
                minDepth = Number(depthValue.slice(4));
            }
        }

        let minIntensity = null;
        let intensitySystem = '';
        if (isEarthquakeLike && intensityValue && intensityValue !== 'all') {
            const text = String(intensityValue);
            if (text.startsWith('cn:')) {
                intensitySystem = 'cn';
                minIntensity = Number(text.slice(3));
            } else if (text.startsWith('jma:')) {
                intensitySystem = 'jma';
                minIntensity = Number(text.slice(4));
            } else {
                // 兼容旧持久化值：纯数字默认按中国烈度处理
                intensitySystem = 'cn';
                minIntensity = Number(text);
            }
        }

        const maxPressure = type === 'typhoon' && maxPressureValue !== 'all'
            ? Number(maxPressureValue)
            : null;

        let resolvedTimeFrom = '';
        let resolvedTimeTo = '';
        const now = new Date();
        if (snapshot.timePreset === '1h') {
            resolvedTimeFrom = formatDateTimeLocal(new Date(now.getTime() - 3600 * 1000));
        } else if (snapshot.timePreset === '24h') {
            resolvedTimeFrom = formatDateTimeLocal(new Date(now.getTime() - 24 * 3600 * 1000));
        } else if (snapshot.timePreset === '7d') {
            resolvedTimeFrom = formatDateTimeLocal(new Date(now.getTime() - 7 * 24 * 3600 * 1000));
        } else if (snapshot.timePreset === '30d') {
            resolvedTimeFrom = formatDateTimeLocal(new Date(now.getTime() - 30 * 24 * 3600 * 1000));
        } else if (snapshot.timePreset === 'custom') {
            resolvedTimeFrom = String(snapshot.timeFrom || '').trim();
            resolvedTimeTo = String(snapshot.timeTo || '').trim();
        }

        return {
            usesLevelFilter,
            minMagnitude: Number.isFinite(minMagnitude) ? minMagnitude : null,
            levelFilter,
            magnitudeSort,
            minWindSpeed: Number.isFinite(minWindSpeed) ? minWindSpeed : null,
            minDepth: Number.isFinite(minDepth) ? minDepth : null,
            maxDepth: Number.isFinite(maxDepth) ? maxDepth : null,
            minIntensity: Number.isFinite(minIntensity) ? minIntensity : null,
            intensitySystem,
            maxPressure: Number.isFinite(maxPressure) ? maxPressure : null,
            activeOnly: type === 'typhoon' ? Boolean(snapshot.activeOnly) : false,
            timeFrom: resolvedTimeFrom,
            timeTo: resolvedTimeTo,
        };
    }, [formatDateTimeLocal]);

    /**
     * 核心拉取函数：装配并提交多维过滤参数，控制加载逻辑
     */
    const fetchEvents = React.useCallback((page, type, limit, sources = [], queryFilters = {}, options = {}) => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
        }
        const controller = new AbortController();
        abortControllerRef.current = controller;

        const safeLimit = Number(limit) > 0 ? Number(limit) : 50;
        const preserveScroll = Boolean(options?.preserveScroll);
        const shouldToggleLoading = !preserveScroll;

        if (preserveScroll && typeof preserveScrollPosition === 'function') {
            preserveScrollPosition();
        }
        if (shouldToggleLoading) {
            setLoading(true);
        }

        // 返回 { ok } 结果对象：供调用方（如 WS 静默刷新）判断是否成功后再推进状态，
        // 避免抛出 Promise rejection 引发未处理告警。
        return eventsApi.getEvents({
            page,
            limit: safeLimit,
            type,
            sources,
            minMagnitude: queryFilters.minMagnitude,
            magnitudeOrder: queryFilters.magnitudeSort,
            keyword: queryFilters.searchKeyword,
            levelFilter: queryFilters.levelFilter,
            minWindSpeed: queryFilters.minWindSpeed,
            timeFrom: queryFilters.timeFrom,
            timeTo: queryFilters.timeTo,
            minDepth: queryFilters.minDepth,
            maxDepth: queryFilters.maxDepth,
            minIntensity: queryFilters.minIntensity,
            intensitySystem: queryFilters.intensitySystem,
            maxPressure: queryFilters.maxPressure,
            activeOnly: queryFilters.activeOnly,
        }, { signal: controller.signal })
            .then((data) => {
                setEvents(Array.isArray(data.events) ? data.events : []);
                setTotal(data.total || 0);
                setTotalPages(data.total_pages || 0);
                setSourceOptions(Array.isArray(data.source_options) ? data.source_options : []);

                const apiMaxLimit = Number(data.max_limit);
                if (Number.isFinite(apiMaxLimit) && apiMaxLimit > 0) {
                    setMaxPageSize(Math.floor(apiMaxLimit));
                }
                if (shouldToggleLoading) setLoading(false);
                return { ok: true };
            })
            .catch((err) => {
                if (shouldToggleLoading) setLoading(false);
                if (err.name !== 'AbortError') {
                    console.error('Failed to fetch events:', err);
                }
                return { ok: false };
            });
    }, [eventsApi, preserveScrollPosition]);

    /**
     * 统一构造查询快照并触发拉取，避免 useEffect / goToPage / WS 三处重复。
     * @param {number} page 目标页码
     * @param {object} [options]
     * @param {boolean} [options.preserveScroll]
     * @param {object} [options.snapshot] 显式传入筛选快照；缺省时读取当前 state
     * @param {boolean} [options.useRefs] 使用 ref 快照（WS 静默刷新场景）
     */
    const executeQuery = React.useCallback((page, options = {}) => {
        const useRefs = Boolean(options.useRefs);
        const snapshot = options.snapshot || (useRefs
            ? {
                filterType: filterTypeRef.current,
                magnitudeFilter: magnitudeFilterRef.current,
                magnitudeOrder: magnitudeOrderRef.current,
                windSpeedFilter: windSpeedFilterRef.current,
                depthFilter: depthFilterRef.current,
                intensityFilter: intensityFilterRef.current,
                maxPressureFilter: maxPressureFilterRef.current,
                activeOnly: activeOnlyRef.current,
                timePreset: timePresetRef.current,
                timeFrom: timeFromRef.current,
                timeTo: timeToRef.current,
                searchKeyword: keywordRef.current,
                pageSize: pageSizeRef.current,
                selectedSources: selectedSourcesRef.current,
            }
            : {
                filterType,
                magnitudeFilter,
                magnitudeOrder,
                windSpeedFilter,
                depthFilter,
                intensityFilter,
                maxPressureFilter,
                activeOnly,
                timePreset,
                timeFrom,
                timeTo,
                searchKeyword: debouncedKeyword,
                pageSize,
                selectedSources,
            });

        const queryFilters = {
            ...buildEventQueryFilters(snapshot),
            searchKeyword: snapshot.searchKeyword,
        };

        // 透传 fetchEvents 的结果（{ ok }），供 WS 静默刷新等调用方判定成功与否
        return fetchEvents(
            page,
            snapshot.filterType,
            snapshot.pageSize,
            snapshot.selectedSources,
            queryFilters,
            { preserveScroll: Boolean(options.preserveScroll) },
        );
    }, [
        buildEventQueryFilters,
        fetchEvents,
        filterType,
        magnitudeFilter,
        magnitudeOrder,
        windSpeedFilter,
        depthFilter,
        intensityFilter,
        maxPressureFilter,
        activeOnly,
        timePreset,
        timeFrom,
        timeTo,
        debouncedKeyword,
        pageSize,
        selectedSources,
    ]);

    // 异步加载所有可用数据源以作下拉筛选。
    // 主查询 executeQuery 的成功回调中已经会回填 source_options，
    // 这里仅在主查询尚未成功（sourceOptions 为空）时兜底拉取一次，避免首屏冗余并发请求。
    React.useEffect(() => {
        if (sourceOptions.length > 0) return;
        eventsApi.getEvents({ page: 1, limit: 1 })
            .then((data) => {
                if (Array.isArray(data.source_options) && data.source_options.length > 0) {
                    setSourceOptions(data.source_options);
                }
            })
            .catch(() => {});
    }, [eventsApi, sourceOptions]);

    // 监听过滤参数变化：重置当前页码为第一页并重新加载数据
    React.useEffect(() => {
        setCurrentPage(1);
        setPageInput('');
        executeQuery(1);
    }, [
        filterType,
        pageSize,
        selectedSources,
        magnitudeFilter,
        magnitudeOrder,
        debouncedKeyword,
        windSpeedFilter,
        depthFilter,
        intensityFilter,
        maxPressureFilter,
        activeOnly,
        timePreset,
        timeFrom,
        timeTo,
        executeQuery,
    ]);

    // 限制单页显示数不超出接口允许的最大上限
    React.useEffect(() => {
        if (pageSize > maxPageSize) setPageSize(maxPageSize);
    }, [pageSize, maxPageSize]);

    // 每次渲染结束后，将最新状态同步至引用对象中
    React.useEffect(() => {
        filterTypeRef.current = filterType;
        pageSizeRef.current = pageSize;
        selectedSourcesRef.current = selectedSources;
        currentPageRef.current = currentPage;
        magnitudeFilterRef.current = magnitudeFilter;
        magnitudeOrderRef.current = magnitudeOrder;
        keywordRef.current = debouncedKeyword;
        windSpeedFilterRef.current = windSpeedFilter;
        timePresetRef.current = timePreset;
        timeFromRef.current = timeFrom;
        timeToRef.current = timeTo;
        depthFilterRef.current = depthFilter;
        intensityFilterRef.current = intensityFilter;
        maxPressureFilterRef.current = maxPressureFilter;
        activeOnlyRef.current = activeOnly;
    });

    // 响应长连接的实时事件推送。
    // 通过记忆「上一次处理的头部事件指纹」判定是否真的出现新事件（新事件总是被 unshift 到头部）：
    // 若头部事件未变化（如 WS 心跳广播仅刷新统计、无新警报），则跳过静默拉取，
    // 避免每 30 秒一次的广播把整个列表反复重拉，拖慢页面切换与值守体验。
    // 指纹口径与 appReducer.appendUniqueEvent 保持一致：优先 id/event_id，
    // 缺失时回退为「event_time + type」复合指纹，避免无 id 事件（Idless）推送后永不刷新。
    // 仅在静默刷新成功后记录指纹；请求失败时不推进，保证下次同指纹心跳可重试。
    React.useEffect(() => {
        if (!wsConnected) return;
        const headEvent = wsEvents?.[0];
        if (!headEvent) return;
        const headFingerprint = (
            headEvent.id
            ?? headEvent.event_id
            ?? `${headEvent.event_time || headEvent.time || headEvent.timestamp || ''}:${headEvent.type || headEvent.event_type || ''}`
        );
        if (!headFingerprint || headFingerprint === wsHeadEventIdRef.current) return;
        executeQuery(currentPageRef.current, {
            preserveScroll: true,
            useRefs: true,
        }).then((result) => {
            if (result?.ok) {
                wsHeadEventIdRef.current = headFingerprint;
            }
        });
    }, [wsEvents, wsConnected, executeQuery]);

    // 组件卸载时自动终止未完成的网络请求
    React.useEffect(() => {
        return () => {
            if (abortControllerRef.current) {
                abortControllerRef.current.abort();
            }
        };
    }, []);

    /**
     * 强类型跳页控制函数
     */
    const goToPage = React.useCallback((targetPage) => {
        if (totalPages <= 0) return;
        const safePage = Math.max(1, Math.min(totalPages, targetPage));
        if (safePage === currentPage) return;

        setCurrentPage(safePage);
        setPageInput('');
        executeQuery(safePage);
    }, [currentPage, totalPages, executeQuery]);

    /**
     * 一键重置全部筛选条件
     */
    const resetFilters = React.useCallback(() => {
        setFilterType(DEFAULT_FILTERS.filterType);
        setMagnitudeFilter(DEFAULT_FILTERS.magnitudeFilter);
        setMagnitudeOrder(DEFAULT_FILTERS.magnitudeOrder);
        setKeyword(DEFAULT_FILTERS.keyword);
        setDebouncedKeyword(DEFAULT_FILTERS.keyword);
        setWindSpeedFilter(DEFAULT_FILTERS.windSpeedFilter);
        setSourceFilterMode(DEFAULT_FILTERS.sourceFilterMode);
        setSelectedSources([]);
        setTimePreset(DEFAULT_FILTERS.timePreset);
        setTimeFrom(DEFAULT_FILTERS.timeFrom);
        setTimeTo(DEFAULT_FILTERS.timeTo);
        setDepthFilter(DEFAULT_FILTERS.depthFilter);
        setIntensityFilter(DEFAULT_FILTERS.intensityFilter);
        setMaxPressureFilter(DEFAULT_FILTERS.maxPressureFilter);
        setActiveOnly(DEFAULT_FILTERS.activeOnly);
        setCurrentPage(1);
        setPageInput('');
    }, []);

    return {
        filterType, setFilterType,
        currentPage, setCurrentPage,
        totalPages, total,
        events, loading,
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
        fetchEvents,
        goToPage,
    };
}
