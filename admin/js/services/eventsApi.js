(() => {
    /**
     * 事件列表模块的接口定义类。
     *
     * 主要交互逻辑：
     * 1. 规整化过滤器 (normalizeEventQuery)：将前端胶囊类型的分类（如 weather 气象）
     *    转换映射为后端 API 接收的底层英文标识（如 weather_alarm）。
     * 2. 查询参数安全归一：自动将无震级概念的气象和海啸查询条件转化为空字符上传，防止后端接收报错，
     *    对空字符串及排序参数进行拦截。
     * 3. 扩展多维筛选：时间范围、深度、烈度/震度、台风气压与活跃态。
     */
    const client = window.DisasterApiClient;

    // 前后端胶囊类别和底层灾害标识的映射字典
    const TYPE_MAP = {
        earthquake_warning: 'earthquake_warning',
        earthquake: 'earthquake',
        tsunami: 'tsunami',
        weather: 'weather_alarm',
        typhoon: 'typhoon',
    };

    function normalizeOptionalNumber(value) {
        // 无值时返回 undefined，配合 apiClient 省略该 query 键，避免传空串。
        if (value === null || value === undefined || value === '') return undefined;
        const num = Number(value);
        return Number.isFinite(num) ? num : undefined;
    }

    /**
     * 将前端的多维过滤状态变量规整化为符合后端格式的请求参数字典
     */
    function normalizeEventQuery(params = {}) {
        const type = params.type === 'all' ? '' : (TYPE_MAP[params.type] || params.type || '');
        const sources = Array.isArray(params.sources)
            ? params.sources.map((source) => String(source || '').trim()).filter(Boolean)
            : [];
        const minMagnitude = params.minMagnitude;
        const magnitudeOrder = String(params.magnitudeOrder || '').toLowerCase();
        const keyword = String(params.keyword || '').trim();
        const levelFilter = String(params.levelFilter || '').trim();
        const minWindSpeed = params.minWindSpeed;
        const timeFrom = String(params.timeFrom || '').trim();
        const timeTo = String(params.timeTo || '').trim();
        const minDepth = params.minDepth;
        const maxDepth = params.maxDepth;
        const minIntensity = params.minIntensity;
        const intensitySystem = String(params.intensitySystem || '').trim().toLowerCase();
        const maxPressure = params.maxPressure;
        const activeOnly = Boolean(params.activeOnly);

        return {
            page: params.page || 1,
            limit: params.limit || 50,
            type,
            source: sources.length > 0 ? sources.join(',') : '',
            min_magnitude: normalizeOptionalNumber(minMagnitude),
            magnitude_order: ['asc', 'desc'].includes(magnitudeOrder) ? magnitudeOrder : '',
            keyword,
            level_filter: levelFilter,
            min_wind_speed: normalizeOptionalNumber(minWindSpeed),
            time_from: timeFrom,
            time_to: timeTo,
            min_depth: normalizeOptionalNumber(minDepth),
            max_depth: normalizeOptionalNumber(maxDepth),
            min_intensity: normalizeOptionalNumber(minIntensity),
            intensity_system: ['cn', 'jma'].includes(intensitySystem) ? intensitySystem : '',
            max_pressure: normalizeOptionalNumber(maxPressure),
            // FastAPI bool 查询参数需要明确 true/false，空串会校验失败
            active_only: activeOnly ? 'true' : 'false',
        };
    }

    const eventsApi = {
        // 分页获取历史事件列表，保留外部传入的 options 取消信号等
        getEvents: (params = {}, options = {}) => client.request('/events', {
            ...options,
            query: normalizeEventQuery(params),
            unwrap: false, // 禁用自动解包，保留 total 计数等元数据信息
        }),
        // 获取特大灾害警报，用于横向时间导轨
        getMajorEvents: (limit = 50, options = {}) => client.request('/events/major', {
            ...options,
            query: { limit: limit === 'all' ? 0 : limit },
            unwrap: false,
        }),
        // 气象精确 ID 或地区预警检索接口
        // filterByTime 关闭后端 72 小时时间过滤；optionalC 传“全部/全日期”时同样关闭时间窗口。
        queryWeather: ({ keyword, optionalA = '', optionalB = '', filterByTime = true, optionalC = '' } = {}, options = {}) => client.request('/weather/query', {
            ...options,
            query: {
                keyword,
                optional_a: optionalA,
                optional_b: optionalB,
                filter_by_time: filterByTime ? 'true' : 'false',
                optional_c: optionalC,
            },
            unwrap: false,
        }),
        // 台风信息查询（优先 EQSC，失败回退本地数据库）
        queryTyphoon: ({
            typhoonId = '',
            keyword = '',
            count = 1,
            detail = 'current',
            activeOnly = false,
        } = {}, options = {}) => client.request('/typhoon/query', {
            ...options,
            query: {
                typhoon_id: typhoonId,
                keyword,
                count,
                detail,
                active_only: activeOnly ? 'true' : 'false',
            },
            unwrap: false,
        }),
    };

    window.DisasterEventsApi = eventsApi;
})();
