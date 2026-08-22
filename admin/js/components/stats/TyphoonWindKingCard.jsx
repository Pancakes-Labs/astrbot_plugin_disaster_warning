const { Typography } = MaterialUI;

/**
 * 台风风王榜卡片组件 (TyphoonWindKingCard)
 * 以排名条形图的形式展示历史最大风速排名前 10 的台风，
 * 风速越高排名越靠前，帮助直观识别最强台风事件。
 * 排行条背景百分比宽度基于当前 Top 1 的最高风速自适应等比算得。
 *
 * @param {Object} props
 * @param {string} [props.className=''] 外部 CSS 类
 * @param {Object} [props.style] 外部自定义样式
 */
function TyphoonWindKingCard({ className = '', style }) {
    const { state } = useAppContext();
    const { stats } = state;

    // 从归一化后的统计状态中提取风王榜数据（含可选气压）
    const rawList = stats && stats.windKingList ? stats.windKingList : [];
    const windKingList = (Array.isArray(rawList) ? rawList : [])
        .map(item => {
            const pressureRaw = item?.pressure;
            const pressure = pressureRaw === null || pressureRaw === undefined || pressureRaw === ''
                ? null
                : Number(pressureRaw);
            return {
                name: item?.name || '未知台风',
                windSpeed: Number(item?.windSpeed) || 0,
                pressure: Number.isFinite(pressure) && pressure > 0 ? pressure : null,
            };
        })
        .filter(item => item.windSpeed > 0);

    // 状态：若无数据，渲染空提示卡片
    if (windKingList.length === 0) {
        return (
            <div className={`card typhoon-wind-king-card ${className}`} style={style}>
                <div className="chart-card-header">
                    <span className="stats-card-header-icon">🏆</span>
                    <Typography variant="h6">风王榜 (TOP 10)</Typography>
                </div>
                <Typography variant="body2" className="typhoon-wind-king-empty-text">
                    暂无数据
                </Typography>
            </div>
        );
    }

    // 获取排第一的最高风速作为 100% 比例分母基底
    const maxWind = Math.max(1, ...windKingList.map(d => d.windSpeed));

    /**
     * 根据风速等级返回对应颜色（阈值集中在 typhoonFormatters）。
     */
    const getWindColor = (windSpeed) => window.DisasterTyphoonFormatters.getTyphoonWindColor(windSpeed);

    return (
        <div className={`card typhoon-wind-king-card ${className}`} style={style}>
            {/* 卡片头部 */}
            <div className="chart-card-header">
                <span className="stats-card-header-icon">🏆</span>
                <Typography variant="h6">风王榜 (TOP 10)</Typography>
            </div>

            {/* 渲染 Top 10 排行项 */}
            <div className="typhoon-wind-king-items">
                {windKingList.map((item, index) => {
                    // 等比换算条形占比
                    const percentage = (item.windSpeed / maxWind) * 100;
                    const windColor = getWindColor(item.windSpeed);

                    return (
                        <div
                            key={index}
                            className="typhoon-wind-king-row"
                            style={{ '--typhoon-wind-king-percent': `calc(${percentage}% + 4px)` }}
                        >
                            {/* 条形填充条 */}
                            <div className="typhoon-wind-king-progress">
                                <div
                                    className="typhoon-wind-king-progress-bar"
                                    style={{ background: windColor }}
                                ></div>
                            </div>

                            {/* 文字与数值区 */}
                            <div className="typhoon-wind-king-content">
                                <div className="typhoon-wind-king-label-wrap">
                                    {/* 前三名冠亚季军特殊渲染 podium 金银铜效果 */}
                                    <div className={`typhoon-wind-king-rank ${index < 3 ? 'typhoon-wind-king-rank--podium' : ''}`}>
                                        {index + 1}
                                    </div>
                                    <Typography variant="body2" noWrap className="typhoon-wind-king-label">
                                        {item.name}
                                    </Typography>
                                </div>
                                <Typography variant="caption" className="typhoon-wind-king-wind">
                                    {item.windSpeed.toFixed(1)} m/s
                                    {item.pressure
                                        ? `（${Number.isInteger(item.pressure) ? item.pressure : item.pressure.toFixed(1)} hPa）`
                                        : ''}
                                </Typography>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}