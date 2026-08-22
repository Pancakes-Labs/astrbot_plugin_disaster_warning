const { Typography } = MaterialUI;

/**
 * 台风强度等级环形比例图表组件 (TyphoonLevelCard)
 * 该组件分析系统捕获的台风记录，按每个台风个体的历史最高强度等级
 * （热带低压、热带风暴、强热带风暴、台风、强台风、超强台风等）进行去重归类统计，
 * 并以圆环饼图 (CSS conic-gradient 实现) 与明细列表形式展示各自的比重。
 * 样式参考 WeatherLevelCard，保持整体风格一致。
 *
 * @param {Object} props
 * @param {Object} [props.style] 外部自定义样式
 * @param {string} [props.className=''] 外部类
 */
function TyphoonLevelCard({ style, className = '' }) {
    const { state } = useAppContext();
    const { stats } = state;

    // 获取台风强度等级统计数组（按台风个体最高等级去重），过滤无计数的空档
    const rawTyphoonLevels = stats && stats.typhoonLevels ? stats.typhoonLevels : [];
    const typhoonLevels = (Array.isArray(rawTyphoonLevels) ? rawTyphoonLevels : [])
        .map(item => {
            const count = Number(item?.count);
            return {
                level: item?.level || '未知等级',
                count: Number.isFinite(count) && count > 0 ? count : 0,
            };
        })
        .filter(item => item.count > 0);

    // 状态：若无任何台风统计数据，渲染空卡片
    if (typhoonLevels.length === 0) {
        return (
            <div className={`card typhoon-level-card ${className}`} style={style}>
                <div className="chart-card-header">
                    <span className="stats-card-header-icon">🌀</span>
                    <Typography variant="h6">台风强度等级</Typography>
                </div>
                <Typography variant="body2" className="typhoon-level-card-empty-text">
                    暂无数据
                </Typography>
            </div>
        );
    }

    // 计算当前所有等级台风总条数
    const total = typhoonLevels.reduce((acc, curr) => acc + curr.count, 0);

    // 用于 CSS conic-gradient 绘制时的累加起始度数百分比
    let currentAngle = 0;

    // 状态：若累计条数异常，同样渲染空卡片
    if (total <= 0) {
        return (
            <div className={`card typhoon-level-card ${className}`} style={style}>
                <div className="chart-card-header">
                    <span className="stats-card-header-icon">🌀</span>
                    <Typography variant="h6">台风强度等级</Typography>
                </div>
                <Typography variant="body2" className="typhoon-level-card-empty-text">
                    暂无有效统计数据
                </Typography>
            </div>
        );
    }

    /**
     * 台风强度等级颜色映射：统一走公共色阶工具，避免组件内 hardcode 与匹配顺序漂移。
     */
    const getColor = (level) => window.DisasterTyphoonFormatters.getTyphoonLevelColor(level);

    return (
        <div className={`card typhoon-level-card ${className}`} style={style}>
            {/* 卡片头部 */}
            <div className="chart-card-header">
                <span className="stats-card-header-icon">🌀</span>
                <Typography variant="h6">台风强度等级</Typography>
            </div>

            <div className="typhoon-level-card-body">
                {/* 1. 圆环图区：使用 conic-gradient 累加切分圆环弧度 */}
                <div
                    className="typhoon-level-card-donut"
                    style={{
                        background: `conic-gradient(${typhoonLevels.map(item => {
                            const start = currentAngle;
                            const percentage = (item.count / total) * 100;
                            currentAngle += percentage; // 累加角度百分比
                            return `${getColor(item.level)} ${start}% ${currentAngle}%`;
                        }).join(', ')})`,
                    }}
                >
                    {/* 圆环中间掏空，陈列合计总数 */}
                    <div className="typhoon-level-card-donut-inner">
                        <Typography variant="h5" className="typhoon-level-card-total">
                            {total}
                        </Typography>
                        <Typography variant="caption" className="typhoon-level-card-total-label">
                            台风总数
                        </Typography>
                    </div>
                </div>

                {/* 2. 右侧明细数据列表 */}
                <div className="typhoon-level-card-list">
                    {typhoonLevels.map((item, index) => (
                        <div key={index} className="typhoon-level-card-row">
                            {/* 等级名称以及左侧色条标识 */}
                            <div className="typhoon-level-card-row-label">
                                <span
                                    className="typhoon-level-card-color-dot"
                                    style={{ background: getColor(item.level) }}
                                ></span>
                                <span className="typhoon-level-card-level">{item.level}</span>
                            </div>
                            {/* 条目数及比重 */}
                            <div className="typhoon-level-card-row-metrics">
                                <span className="typhoon-level-card-count">{item.count}</span>
                                <span className="typhoon-level-card-ratio">
                                    {((item.count / total) * 100).toFixed(2)}%
                                </span>
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}