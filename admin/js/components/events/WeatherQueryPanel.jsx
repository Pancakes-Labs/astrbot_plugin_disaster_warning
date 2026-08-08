const { Typography, CircularProgress } = MaterialUI;
const { useState, useMemo, useEffect } = React;
const { buildWeatherIconFallbackHandler: _rawBuildHandler, isWeatherImageInvalid: _rawInvalidCheck } = window.EventFormatters || {};
const buildWeatherIconFallbackHandler = _rawBuildHandler || ((_, cb) => (e) => typeof cb === 'function' && cb(e));
const isWeatherImageInvalid = _rawInvalidCheck || ((el) => Boolean(el) && el.naturalWidth === 0 && el.naturalHeight === 0);

/**
 * 气象预警快捷查询面板组件 (WeatherQueryPanel)
 * 提供与聊天机器人命令 `/气象预警查询` 完全一致的可视化检索配置界面。
 * 输入流程包含：
 * 1. 关键字（当输入合规的预警 ID 时，组件自动进入详情模式渲染，若输入地区名称如“北京”则自动进入近72小时检索列表模式）。
 * 2. 预警气象类型过滤输入框。
 * 3. 预警危险警报颜色下拉菜单。
 * 4. 支持列表模式分页展示（10, 20, 50条），以及友好加载骨架和错误渲染。
 */
function WeatherQueryPanel() {
    // 导入专门管理天气查询状态的 Hook
    const {
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
    } = useWeatherQuery();

    /**
     * A. 渲染详情模式卡片：用于展示某个特定 ID 预警的全文通告和气象防范指南
     */
    const renderIdResult = () => {
        const detail = result || {};
        const titleText = detail.title_text || detail.headline_text || '气象预警详情';
        const bodyText = detail.body_text || '暂无详细描述';
        const guidelineText = detail.guideline_text || '';

        return (
            <div className="weather-query-result-card">
                {/* 详情标题与唯一 ID */}
                <div className="weather-query-result-header">
                    <Typography variant="subtitle1" className="weather-query-result-title">
                        {titleText}{detail.color_emoji || ''}
                    </Typography>
                    {detail.alarm_id && (
                        <Typography variant="caption" className="weather-query-result-meta">
                            ID: {detail.alarm_id}
                        </Typography>
                    )}
                </div>

                {/* 详细文字内容与防灾预警防御指南 */}
                <div className="weather-query-result-body">
                    <Typography variant="body2" className="weather-query-result-text">
                        {bodyText}
                    </Typography>
                    {guidelineText && (
                        <Typography 
                            variant="body2" 
                            className="weather-query-result-text weather-query-result-text--guideline"
                        >
                            {guidelineText}
                        </Typography>
                    )}
                </div>

                {/* 右侧展示气象信号图标 */}
                {detail.icon_url && (
                    <div className="weather-query-icon-wrap">
                        <div className="weather-query-icon-card">
                            <img
                                src={detail.icon_url}
                                alt={detail.weather_type_code || 'weather-icon'}
                                className="weather-query-icon"
                                loading="lazy"
                                onError={buildWeatherIconFallbackHandler(
                                    detail.weather_type_code,
                                    (e) => e.currentTarget.classList.add('is-hidden')
                                )}
                                onLoad={(e) => {
                                    // 伪图片（HTTP 200 的 HTML）不会触发 onError，
                                    // 通过 naturalWidth=0 识别无效内容并触发同一条回退链
                                    if (isWeatherImageInvalid(e.currentTarget)) {
                                        buildWeatherIconFallbackHandler(
                                            detail.weather_type_code,
                                            (ev) => ev.currentTarget.classList.add('is-hidden')
                                        )(e);
                                    }
                                }}
                            />
                        </div>
                    </div>
                )}
            </div>
        );
    };

    /**
     * B. 渲染检索列表模式卡片：渲染多条气象预警列表，并支持本地前端分页过滤展示
     */
    const renderSearchResult = () => {
        const items = Array.isArray(result?.items) ? result.items : [];
        const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
        const currentPage = Math.min(page, totalPages);
        const startIndex = (currentPage - 1) * pageSize;
        const pagedItems = items.slice(startIndex, startIndex + pageSize);

        return (
            <div className="weather-query-list">
                {/* 列表控制子菜单：展示总数与单页容量设置 */}
                <div className="weather-query-list-toolbar">
                    <Typography variant="caption" className="weather-query-caption-muted">
                        共 {items.length} 条，当前第 {currentPage} / {totalPages} 页
                    </Typography>
                    <div className="weather-query-page-size-control">
                        <Typography variant="caption" className="weather-query-caption-muted">每页</Typography>
                        <select
                            value={pageSize}
                            onChange={(e) => setPageSize(Number(e.target.value) || 20)}
                            className="weather-query-input weather-query-page-size-select"
                        >
                            <option value={10}>10</option>
                            <option value={20}>20</option>
                            <option value={50}>50</option>
                        </select>
                    </div>
                </div>

                {/* 循环遍历渲染单页预警项 */}
                {pagedItems.map((item, index) => (
                    <div 
                        className="weather-query-list-item" 
                        key={`${item.alarm_id || 'unknown'}-${startIndex + index}`}
                    >
                        {/* 左侧：预警信号代表图标 */}
                        {item.icon_url && (
                            <img
                                src={item.icon_url}
                                alt={item.weather_type_code || 'weather-icon'}
                                className="weather-query-list-item-image"
                                loading="lazy"
                                onError={buildWeatherIconFallbackHandler(
                                    item.weather_type_code,
                                    (e) => e.currentTarget.classList.add('is-hidden')
                                )}
                                onLoad={(e) => {
                                    // 伪图片（HTTP 200 的 HTML）不会触发 onError，
                                    // 通过 naturalWidth=0 识别无效内容并触发同一条回退链
                                    if (isWeatherImageInvalid(e.currentTarget)) {
                                        buildWeatherIconFallbackHandler(
                                            item.weather_type_code,
                                            (ev) => ev.currentTarget.classList.add('is-hidden')
                                        )(e);
                                    }
                                }}
                            />
                        )}
                        {/* 右侧：单条列表详细元数据 */}
                        <div className="weather-query-list-item-main">
                            <Typography variant="body2">发布时间：{item.issue_time || '未知时间'}</Typography>
                            <Typography variant="body2">ID：{item.alarm_id || '未知ID'}</Typography>
                            <Typography variant="body2">发布机构：{item.publish_org || '未知发布机构'}</Typography>
                            <Typography variant="body2">预警类型：{item.weather_type_line || '未知类型预警'}</Typography>
                        </div>
                    </div>
                ))}

                {/* 如果总项目数超出了单页上限，则渲染分页跳转控制器 */}
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
        <div className="card weather-query-panel">
            {/* 面板大标题 */}
            <div className="weather-query-header">
                <div className="weather-query-header-main">
                    <span className="weather-query-title-icon">🌦️</span>
                    <Typography variant="h6" className="weather-query-title">气象预警快捷查询</Typography>
                </div>
                <Typography variant="caption" className="weather-query-caption-subtle">
                    等价于 /气象预警查询 指令
                </Typography>
            </div>

            {/* 查询表单 */}
            <div className="weather-query-form">
                {/* 1. 主搜索输入（支持地区关键字和 ID） */}
                <input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder="输入地区关键词（如 山西）或预警ID（如 36042941600000_20260314235956）"
                    className="weather-query-input weather-query-keyword"
                />

                {/* 2. 可选：预警类型（如 大风、暴雨），仅在非 ID 直查下启用 */}
                <input
                    value={optionalA}
                    onChange={(e) => setOptionalA(e.target.value)}
                    placeholder="可选：预警类型（如 大风）"
                    className="weather-query-input"
                    disabled={isIdQuery}
                />

                {/* 3. 可选：预警级别颜色，仅在非 ID 直查下启用 */}
                <select
                    value={optionalB}
                    onChange={(e) => setOptionalB(e.target.value)}
                    className="weather-query-input"
                    disabled={isIdQuery}
                >
                    <option value="">可选：预警颜色</option>
                    <option value="红色">红色</option>
                    <option value="橙色">橙色</option>
                    <option value="黄色">黄色</option>
                    <option value="蓝色">蓝色</option>
                    <option value="白色">白色</option>
                </select>

                {/* 3.5 可选：时间范围（默认近72小时，可选全日期），仅在非 ID 直查下启用 */}
                <select
                    value={timeRange}
                    onChange={(e) => setTimeRange(e.target.value)}
                    className="weather-query-input"
                    disabled={isIdQuery}
                >
                    <option value="">时间范围：近72小时</option>
                    <option value="全部">时间范围：全部日期</option>
                </select>

                {/* 4. 执行与重置按钮 */}
                <button className="btn weather-query-btn" onClick={searchWeather} disabled={loading}>
                    {loading ? '查询中...' : '查询'}
                </button>
                <button 
                    className="btn weather-query-btn weather-query-btn-secondary" 
                    onClick={resetWeatherQuery} 
                    disabled={loading}
                >
                    清空
                </button>
            </div>

            {/* 服务端异步数据获取中的加载指示器 */}
            {loading && (
                <div className="weather-query-loading">
                    <CircularProgress size={24} />
                    <Typography variant="body2" className="weather-query-loading-text">
                        正在查询，请稍候...
                    </Typography>
                </div>
            )}

            {/* 失败重试信息提示 */}
            {!loading && error && (
                <div className="weather-query-error">
                    <Typography variant="body2" className="weather-query-error-text">{error}</Typography>
                </div>
            )}

            {/* 根据模式分发渲染结果 */}
            {!loading && !error && result && (
                <div className="weather-query-result">
                    {result.query_mode === 'id' ? renderIdResult() : renderSearchResult()}
                </div>
            )}
        </div>
    );
}
