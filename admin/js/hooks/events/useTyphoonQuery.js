/**
 * 台风信息快捷查询面板的状态控制钩子。
 *
 * 与 /台风信息查询 指令保持同一套查询语义：
 * 1. 优先 EQSC，失败或未配置时回退本地数据库。
 * 2. 支持按台风 ID、名称关键词、数量、详细程度（当前/完整路径）、仅活跃过滤。
 * 3. 结果模式：id（单条详情）/ list|search（列表）。
 */
function useTyphoonQuery() {
    const eventsApi = window.DisasterEventsApi;

    const [typhoonId, setTyphoonId] = React.useState('');
    const [keyword, setKeyword] = React.useState('');
    const [count, setCount] = React.useState(1);
    const [detail, setDetail] = React.useState('current');
    const [activeOnly, setActiveOnly] = React.useState(false);
    const [loading, setLoading] = React.useState(false);
    const [error, setError] = React.useState('');
    const [result, setResult] = React.useState(null);
    const [page, setPage] = React.useState(1);
    const [pageSize, setPageSize] = React.useState(10);
    const [expandedIds, setExpandedIds] = React.useState({});

    const isIdQuery = React.useMemo(
        () => /^\d{4}$|^\d{6}$/.test((typhoonId || '').trim()),
        [typhoonId]
    );

    React.useEffect(() => {
        setPage(1);
        setExpandedIds({});
    }, [result, pageSize]);

    const searchTyphoon = React.useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            // 数量输入框允许临时空值；提交时收敛到 1..20，默认 1
            let normalizedCount = Number(count);
            if (!Number.isFinite(normalizedCount) || normalizedCount <= 0) {
                normalizedCount = 1;
            }
            normalizedCount = Math.max(1, Math.min(20, Math.floor(normalizedCount)));

            const data = await eventsApi.queryTyphoon({
                typhoonId: (typhoonId || '').trim(),
                keyword: (keyword || '').trim(),
                count: normalizedCount,
                detail: detail || 'current',
                activeOnly: Boolean(activeOnly),
            });

            if (!data?.success) {
                let baseError = String(data?.error || '未查询到结果');
                if (!baseError.includes('官方渠道') && !baseError.includes('EQSC')) {
                    baseError = `${baseError} 可尝试配置 EQSC 或通过其他官方渠道查询`;
                }
                if (data?.filters) {
                    const segments = [];
                    if (data.filters.keyword) segments.push(`关键词=${data.filters.keyword}`);
                    if (data.filters.active_only) segments.push('仅活跃=是');
                    if (data.filters.count) segments.push(`数量=${data.filters.count}`);
                    setError(`${baseError}${segments.length ? `\n检索条件：${segments.join('，')}` : ''}`);
                } else {
                    setError(baseError);
                }
                setResult(null);
                return;
            }

            setResult(data);
        } catch (e) {
            console.error('[TyphoonQueryPanel] query failed:', e);
            setError(`查询失败：${e?.message || e}`);
            setResult(null);
        } finally {
            setLoading(false);
        }
    }, [eventsApi, typhoonId, keyword, count, detail, activeOnly]);

    const resetTyphoonQuery = React.useCallback(() => {
        setTyphoonId('');
        setKeyword('');
        setCount(1);
        setDetail('current');
        setActiveOnly(false);
        setError('');
        setResult(null);
        setPage(1);
        setExpandedIds({});
    }, []);

    const toggleExpanded = React.useCallback((key) => {
        setExpandedIds((prev) => ({
            ...prev,
            [key]: !prev[key],
        }));
    }, []);

    return {
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
    };
}

window.useTyphoonQuery = useTyphoonQuery;
