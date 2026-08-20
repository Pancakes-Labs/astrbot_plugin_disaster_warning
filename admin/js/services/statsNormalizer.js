(() => {
    /**
     * 对后台统计的大屏分析数据进行归一化及排序规整的工具类。
     * 
     * 核心逻辑解析：
     * 1. 数组结构规整 (entriesToSortedList)：将后端传回的键值对形式的对象数据（如 区域名: 发生次数），
     *    转化为前端表格和柱状图可直接遍历的数组对象形式，并按照计数值从大到小降序排列，以便前台展示 Top10 榜单。
     * 2. 数据防抖与保护：对后端因时段无数据返回的未定义字段进行合理的默认零值拦截，
     *    防止前台大屏图表发生致命错误。
     */

    /**
     * 将对象键值对映射转换为降序排列的数组对象列表
     */
    function entriesToSortedList(source, keyName) {
        if (!source || typeof source !== 'object') {
            return [];
        }
        return Object.entries(source)
            .map(([key, count]) => ({ [keyName]: key, count }))
            .sort((a, b) => b.count - a.count); // 按发生频数由高到低降序重排
    }

    /**
     * 将后端复杂的嵌套统计对象整合并转化输出为前端标准的格式
     */
    function normalizeStatsPayload(stats = {}) {
        const earthquakeStats = stats.earthquake_stats || {};
        const weatherStats = stats.weather_stats || {};
        const typhoonStats = stats.typhoon_stats || {};
        const snetStats = stats.snet_stats || {};
        const byType = stats.by_type || {};

        // 会话推送统计：从 session_stats.top_sessions 提取并排序
        // 展示侧只保留短 session_id + 备注名，计数直接使用 pushed
        const sessionStats = stats.session_stats || {};
        const rawTopSessions = Array.isArray(sessionStats.top_sessions) ? sessionStats.top_sessions : [];
        const topSessions = rawTopSessions
            .map(item => {
                const session = String(item?.session || '').trim() || '未知会话';
                const sessionId = String(
                    item?.session_id
                    || (typeof formatSessionIdFromUmo === 'function'
                        ? formatSessionIdFromUmo(session)
                        : session)
                    || session
                ).trim() || session;
                const sessionName = String(item?.session_name || '').trim();
                const displayName = sessionName
                    ? `${sessionId} (${sessionName})`
                    : sessionId;

                return {
                    session,
                    sessionId,
                    sessionName,
                    displayName,
                    pushed: Number(item?.pushed) || 0,
                };
            })
            .sort((a, b) => b.pushed - a.pushed)
            .slice(0, 10);

        // 台风强度等级分布（按台风个体最高等级去重）：按数量降序排列
        const typhoonLevels = entriesToSortedList(typhoonStats.by_max_level, 'level');

        // 风王榜：兼容旧结构 number，以及新结构 {wind_speed, pressure, display_name}
        const rawWindKing = typhoonStats.max_wind_typhoons || {};
        const windKingList = Object.entries(rawWindKing)
            .map(([key, entry]) => {
                if (entry && typeof entry === 'object') {
                    const windSpeed = Number(entry.wind_speed ?? entry.windSpeed) || 0;
                    const pressureRaw = entry.pressure;
                    const pressure = pressureRaw === null || pressureRaw === undefined || pressureRaw === ''
                        ? null
                        : Number(pressureRaw);
                    return {
                        // 优先取条目内展示名（身份键结构），兼容旧结构（key 即展示名）
                        name: String(entry.display_name || '').trim() || key,
                        windSpeed,
                        pressure: Number.isFinite(pressure) && pressure > 0 ? pressure : null,
                    };
                }
                return {
                    name: key,
                    windSpeed: Number(entry) || 0,
                    pressure: null,
                };
            })
            .filter(item => item.windSpeed > 0)
            .sort((a, b) => b.windSpeed - a.windSpeed)
            .slice(0, 10);

        // 最低气压榜：数值越低越强；兼容旧结构 float，以及新结构 {pressure, display_name}
        const rawPressureKing = typhoonStats.min_pressure_typhoons || {};
        const pressureKingList = Object.entries(rawPressureKing)
            .map(([key, entry]) => {
                let pressure = 0;
                let name = key;
                if (entry && typeof entry === 'object') {
                    const pressureRaw = entry.pressure;
                    pressure = Number(pressureRaw) || 0;
                    name = String(entry.display_name || '').trim() || key;
                } else {
                    pressure = Number(entry) || 0;
                }
                return { name, pressure };
            })
            .filter(item => item.pressure > 0)
            .sort((a, b) => a.pressure - b.pressure)
            .slice(0, 10);

        return {
            stats: {
                totalEvents: stats.total_events || 0,                         // 捕捉事件总数
                earthquakeCount: byType.earthquake || 0,                     // 地震事件总数
                warningCount: typeof byType.earthquake_warning !== 'undefined'
                    ? Number(byType.earthquake_warning)
                    : 0,                                                      // 预警事件总数
                tsunamiCount: byType.tsunami || 0,                           // 海啸预警总数
                weatherCount: byType.weather_alarm || 0,                     // 气象灾害总数
                typhoonCount: byType.typhoon || 0,                           // 台风事件总数
                maxMagnitude: earthquakeStats.max_magnitude || null,         // 周期内全球最大震级极值
                snetGlobalMax: snetStats.global_max || null,                 // S-Net 全网历史最大震度
                snetTopPeaks: Array.isArray(snetStats.top_peaks)
                    ? snetStats.top_peaks.slice(0, 3)
                    : [],                                                    // S-Net 历史最大震度 Top3
                snetStationCount: Number(snetStats.station_count) || 0,      // S-Net 已归档测站数
                snetLastObservationAt: snetStats.last_observation_at || null,
                earthquakeRegions: entriesToSortedList(earthquakeStats.by_region, 'region'), // 地震多发地 Top10 排行数据
                weatherRegions: entriesToSortedList(weatherStats.by_region, 'region'),       // 气象多发地 Top10 排行数据
                weatherTypes: entriesToSortedList(weatherStats.by_type, 'type'),             // 气象细分类别分布
                weatherLevels: entriesToSortedList(weatherStats.by_level, 'level'),          // 气象预警颜色分级占比
                dataSources: entriesToSortedList(stats.by_source, 'source'),                 // 三方数据源警报占比
                logStats: stats.log_stats || null,                                           // 日志拦截分析统计
                topSessions,                                                                  // 会话推送统计 Top10
                typhoonLevels,                                                                // 台风强度等级分布
                windKingList,                                                                 // 风王榜 Top10（含气压）
                pressureKingList,                                                             // 最低气压榜 Top10
            },
            events: stats.recent_pushes || [],                               // 历史推送详情队列
            magnitudeDistribution: earthquakeStats.by_magnitude || {},        // 地震震级区间分布映射表
        };
    }

    window.StatsNormalizer = {
        entriesToSortedList,
        normalizeStatsPayload,
    };
})();
