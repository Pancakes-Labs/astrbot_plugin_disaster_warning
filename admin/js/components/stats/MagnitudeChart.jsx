const { Box, Typography } = MaterialUI;
const { useMemo } = React;

/**
 * 震级分布条形图表组件 (MagnitudeChart)
 * 展示系统记录的历史地震数据在各震级档（M3以下、M3-M3.9、...、M8以上）的频次分布。
 * 图表条形宽度是基于最大震级档频次进行“自适应等比拉伸”，
 * 并在右侧展现各档频次占所有地震总数的精确百分比比重。
 *
 * @param {Object} props
 * @param {Object} [props.style] 外部样式
 * @param {string} [props.className=''] 额外类名
 */
function MagnitudeChart({ style, className = '', showNote = true }) {
    const { state } = useAppContext();
    
    // 获取后台统计并推送到 state 中的震级分布指标 map
    const magnitudeDistribution = (state && state.magnitudeDistribution && typeof state.magnitudeDistribution === 'object')
        ? state.magnitudeDistribution
        : {};

    // 标准震级区间划分档序列
    const magnitudeOrder = [
        "< M3.0", "M3.0 - M3.9", "M4.0 - M4.9", "M5.0 - M5.9", "M6.0 - M6.9", "M7.0 - M7.9", ">= M8.0"
    ];

    // 数据计算：转换分布指标，统计百分比并换算条形宽度比率
    const chartData = useMemo(() => {
        // 1. 计算总发生地震事件数
        const total = magnitudeOrder.reduce((acc, label) => {
            const value = Number(magnitudeDistribution[label] || 0);
            return acc + (Number.isFinite(value) ? value : 0);
        }, 0);

        // 2. 生成基础结构
        const data = magnitudeOrder.map(label => {
            const value = Number(magnitudeDistribution[label] || 0);
            return {
                label,
                value: Number.isFinite(value) ? value : 0
            };
        });

        // 3. 定位单档频次极高值，用以作为 100% 宽度基底
        const maxValue = Math.max(...data.map(d => d.value), 1);
        
        // 4. 计算条形相对宽度百分比 (percentage) 以及占比百分比 (ratio)
        return data.map(d => ({
            ...d,
            percentage: (d.value / maxValue) * 100,
            ratio: total > 0 ? (d.value / total) * 100 : 0
        }));
    }, [magnitudeDistribution]);

    // 状态：无震级分布指标时渲染空提示
    if (Object.keys(magnitudeDistribution).length === 0) {
        return (
            <div className={`card magnitude-chart-card magnitude-chart-card--empty ${className}`} style={style}>
                <Typography variant="body2" className="magnitude-chart-empty-text">
                    暂无震级统计数据，等待新事件生成
                </Typography>
            </div>
        );
    }

    return (
        <div className={`card magnitude-chart-card ${className}`} style={style}>
            {/* 图表标题栏 */}
            <div className="chart-card-header">
                <span className="stats-card-header-icon">📈</span>
                <Typography variant="h6">震级分布统计</Typography>
            </div>

            {/* 条形统计表主体 */}
            <div className="mag-stats-container">
                {chartData.map((item, index) => (
                    <div key={index} className="mag-row">
                        {/* 左侧：震级区间名称 */}
                        <div className="mag-label">{item.label}</div>
                        
                        {/* 中间：等比伸缩进度条轨道 */}
                        <div className="mag-bar-container">
                            <div
                                className="mag-bar"
                                style={{ width: `${item.percentage}%` }}
                            ></div>
                        </div>
                        
                        {/* 右侧：数值与其占比比重 */}
                        <div className="mag-value-cluster">
                            <div className="mag-value">{item.value}</div>
                            <span className="mag-ratio">
                                ({item.ratio > 0 ? `${item.ratio.toFixed(2)}%` : '0.00%'})
                            </span>
                        </div>
                    </div>
                ))}
            </div>
            
            {/* 底部备注提示区域（可外置到布局层） */}
            {showNote && (
                <div className="magnitude-chart-note-wrap">
                    <div className="magnitude-chart-note">
                        <span className="magnitude-chart-note-icon">ℹ️</span>
                        <Typography variant="body2" className="magnitude-chart-note-text">
                            地震震级分布与最大地震的统计可能会不一致，这是由于对数据源的筛选逻辑不一样导致的，前者比较宽松，后者比较严格。
                        </Typography>
                    </div>
                </div>
            )}
        </div>
    );
}
