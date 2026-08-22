/**
 * 气象预警快捷查询面板的状态控制钩子。
 * 
 * 核心机制说明：
 * 1. 模式自适应判定：根据用户的检索输入，通过正则匹配是否为气象预警唯一的标识符。
 *    预警标识特征例如：国家代码_发震预发时间序列。如果是预警标识，则走精确单卡检索分流；否则走多维地名模糊过滤分流。
 * 2. 报错细节补充：在没有查询到任何结果时，不仅输出后端返回的错误内容，还会动态提示管理员通过其他官方渠道验证，
 *    并友好渲染当前提交的检索片段细节（如 地区=北京，预警颜色=红），方便管理员纠正录入。
 */
function useWeatherQuery() {
    const eventsApi = window.DisasterEventsApi;
    
    // 查询状态管理
    const [keyword, setKeyword] = React.useState('');         // 精确预警标识或检索省市地名关键字
    const [optionalA, setOptionalA] = React.useState('');     // 辅助备选过滤项（如预警类型）
    const [optionalB, setOptionalB] = React.useState('');     // 辅助备选过滤项（如预警颜色）
    const [timeRange, setTimeRange] = React.useState('');     // 时间范围：''=近72小时 / '全部'=全日期
    const [loading, setLoading] = React.useState(false);       // 查询中加载状态锁
    const [error, setError] = React.useState('');             // 出错时的警告提示文字
    const [result, setResult] = React.useState(null);         // 后端返回的标准化结果对象
    const [page, setPage] = React.useState(1);                 // 多结果集下的表格当前页码
    const [pageSize, setPageSize] = React.useState(20);       // 单页结果显示数

    // 根据输入格式，在输入期间本地实时正则判断是否为预警标识检索模式，用以在前端控制辅助检索面板的显示与隐藏
    const isIdQuery = React.useMemo(() => /^\d+_\d{12,14}$/.test((keyword || '').trim()), [keyword]);

    // 每当返回新结果集或改变单页数大小时，归零页码
    React.useEffect(() => {
        setPage(1);
    }, [result, pageSize]);

    /**
     * 核心查询调度函数
     */
    const searchWeather = React.useCallback(async () => {
        const kw = (keyword || '').trim();
        if (!kw) {
            setError('请输入地区关键词或预警ID');
            setResult(null);
            return;
        }

        setLoading(true);
        setError('');
        try {
            const data = await eventsApi.queryWeather({
                keyword: kw,
                optionalA: (optionalA || '').trim(),
                optionalB: (optionalB || '').trim(),
                filterByTime: !(timeRange === '全部'),
                optionalC: timeRange === '全部' ? '全部' : '',
            });

            // 拦截查无结果的情况，组合展示友好的排查信息
            if (!data?.success) {
                let baseError = String(data?.error || '未查询到结果');
                if (!baseError.includes('官方渠道')) {
                    baseError = `${baseError} 可尝试通过其他官方渠道进行查询`;
                }
                if (data?.query_mode === 'search' && data?.filters) {
                    const segments = [`地区=${data.filters.location || ''}`].filter(Boolean);
                    if (data.filters.type) segments.push(`预警类型=${data.filters.type}`);
                    if (data.filters.color) segments.push(`预警颜色=${data.filters.color}`);
                    if (data.filters.all_date_mode) segments.push('时间范围=全部日期');
                    else if (data.filters.time_window_hours) segments.push(`时间范围=近${data.filters.time_window_hours}小时`);
                    setError(`${baseError}${segments.length ? `\n检索条件：${segments.join('，')}` : ''}`);
                } else {
                    setError(baseError);
                }
                setResult(null);
                return;
            }

            setResult(data);
        } catch (e) {
            console.error('[WeatherQueryPanel] query failed:', e);
            setError(`查询失败：${e?.message || e}`);
            setResult(null);
        } finally {
            setLoading(false);
        }
    }, [eventsApi, keyword, optionalA, optionalB, timeRange]);

    /**
     * 重置表单，擦除搜索足迹
     */
    const resetWeatherQuery = React.useCallback(() => {
        setKeyword('');
        setOptionalA('');
        setOptionalB('');
        setTimeRange('');
        setError('');
        setResult(null);
        setPage(1);
    }, []);

    return {
        keyword, setKeyword,
        optionalA, setOptionalA,
        optionalB, setOptionalB,
        timeRange, setTimeRange,
        loading,
        error,
        result,
        page, setPage,
        pageSize, setPageSize,
        isIdQuery,
        searchWeather,
        resetWeatherQuery,
    };
}

// 绑定全局
window.useWeatherQuery = useWeatherQuery;
