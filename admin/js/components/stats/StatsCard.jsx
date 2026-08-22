const { Box, Typography } = MaterialUI;

/**
 * 总体统计信息卡片组件 (StatsCard)
 * 展示系统记录并持久化的各类防灾事件累计总数，以及细分的二级大类数量。
 * 大类涵盖：地震速报、地震预警 (EEW)、气象灾害预警、海啸预警。
 *
 * @param {Object} props
 * @param {string} [props.className=''] 额外注入的样式类
 * @param {Object} [props.style] 自定义样式对象
 */
function StatsCard({ className = '', style }) {
    const { state } = useAppContext();
    const { stats, dataLoaded } = state;
    
    // 初始化安全兜底值，避免 stats 为空或网络接口断线时界面崩溃挂起
    const safeStats = {
        totalEvents: 0,
        earthquakeCount: 0,
        warningCount: 0,
        weatherCount: 0,
        tsunamiCount: 0,
        typhoonCount: 0,
        ...(stats || {})
    };

    // 1. 状态：异步数据包尚未加载完毕前，渲染与卡片物理结构相同的骨架屏占位
    if (!dataLoaded) {
        return (
            <div className={`card stats-card ${className}`} style={style}>
                <div className="stats-card-head">
                    <div className="stats-card-icon">📊</div>
                    <Typography variant="h6" className="stats-card-title">事件统计</Typography>
                </div>
                <div className="stats-card-main">
                    <div className="skeleton stats-card-total-skeleton"></div>
                    <div className="stats-card-breakdown-grid">
                        <div className="skeleton stats-card-breakdown-skeleton"></div>
                        <div className="skeleton stats-card-breakdown-skeleton"></div>
                        <div className="skeleton stats-card-breakdown-skeleton"></div>
                        <div className="skeleton stats-card-breakdown-skeleton"></div>
                        <div className="skeleton stats-card-breakdown-skeleton"></div>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className={`card stats-card ${className}`} style={style}>
            {/* 头部区 */}
            <div className="stats-card-head">
                <div className="stats-card-icon">📊</div>
                <Typography variant="h6" className="stats-card-title">事件统计</Typography>
            </div>

            {/* 中部核心累计数值区 */}
            <div className="stats-card-main">
                <Typography variant="h2" className="stats-card-total">
                    {safeStats.totalEvents}
                </Typography>
                <Typography variant="body2" className="stats-card-total-label">
                    事件总数
                </Typography>
            </div>

            {/* 底部细分子类别统计网格 */}
            <Box className="stats-card-breakdown-grid">
                {/* 1. 地震速报数量 */}
                <Box className="stats-card-breakdown-item">
                    <Typography variant="h6" className="stats-card-breakdown-value">
                        {safeStats.earthquakeCount}
                    </Typography>
                    <Typography variant="caption" className="stats-card-breakdown-label">地震事件</Typography>
                </Box>
                
                {/* 2. 地震预警 (EEW) 触发总次数 */}
                <Box className="stats-card-breakdown-item">
                    <Typography variant="h6" className="stats-card-breakdown-value">
                        {(safeStats.warningCount !== undefined && safeStats.warningCount !== null) ? safeStats.warningCount : '-'}
                    </Typography>
                    <Typography variant="caption" className="stats-card-breakdown-label">地震预警</Typography>
                </Box>
                
                {/* 3. 气象警报触发次数 */}
                <Box className="stats-card-breakdown-item">
                    <Typography variant="h6" className="stats-card-breakdown-value">
                        {safeStats.weatherCount}
                    </Typography>
                    <Typography variant="caption" className="stats-card-breakdown-label">气象预警</Typography>
                </Box>
                
                {/* 4. 海啸警报触发次数 */}
                <Box className="stats-card-breakdown-item">
                    <Typography variant="h6" className="stats-card-breakdown-value">
                        {safeStats.tsunamiCount}
                    </Typography>
                    <Typography variant="caption" className="stats-card-breakdown-label">海啸预警</Typography>
                </Box>

                {/* 5. 台风事件按台风 ID 去重后的累计数量 */}
                <Box className="stats-card-breakdown-item">
                    <Typography variant="h6" className="stats-card-breakdown-value">
                        {safeStats.typhoonCount}
                    </Typography>
                    <Typography variant="caption" className="stats-card-breakdown-label">台风信息</Typography>
                </Box>
            </Box>
        </div>
    );
}
