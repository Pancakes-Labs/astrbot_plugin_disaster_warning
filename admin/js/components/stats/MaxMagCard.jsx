const { Typography } = MaterialUI;

/**
 * 历史最大地震信息卡片组件 (MaxMagCard)
 * 与 S-Net 卡片并排：标题 + 震级大数 + 地点 + 底部左时间/右来源。
 *
 * @param {Object} props
 * @param {Object} [props.style] 外部自定义样式
 * @param {string} [props.className=''] 额外类名
 */
function MaxMagCard({ style, className = '' }) {
    const { state } = useAppContext();
    const { stats, config } = state;
    const displayTimezone = config.displayTimezone || 'UTC+8';

    const maxMag = stats && stats.maxMagnitude ? stats.maxMagnitude : null;
    const magValue = Number(maxMag?.value);

    const displayMag = Number.isFinite(magValue) ? magValue.toFixed(1) : '--';
    const displayPlace = maxMag?.place_name || '暂无震中信息';
    const sourceLabel = maxMag?.source ? formatSourceName(maxMag.source) : '';

    /**
     * 格式化震中发震时间
     */
    const formatTime = (time) => {
        if (!time) return '未知时间';
        return formatTimeWithZone(time, displayTimezone, true);
    };

    // 状态：若尚无记录（如初次布署尚未拦截地震数据），展示空卡片提示
    if (!maxMag) {
        return (
            <div className={`card max-mag-card max-mag-card--empty ${className}`} style={style}>
                <span className="max-mag-card-empty-icon">📉</span>
                <Typography variant="body2" className="max-mag-card-empty-text">暂无最大震级记录</Typography>
            </div>
        );
    }

    return (
        <div className={`card max-mag-card ${className}`} style={style}>
            {/* 卡片右上角的大号火球半透明背景水印 */}
            <div className="max-mag-card-watermark">🔥</div>

            {/* 卡片头部 */}
            <div className="chart-card-header max-mag-card-header">
                <span className="stats-card-header-icon">🔥</span>
                <Typography variant="h6" className="max-mag-card-title">历史最大地震</Typography>
            </div>

            <div className="max-mag-card-body">
                <div className="max-mag-card-mag-row">
                    <Typography variant="h3" className="max-mag-card-mag-value">
                        <span className="max-mag-card-mag-prefix">M</span>{displayMag}
                    </Typography>
                </div>

                <Typography variant="body1" className="max-mag-card-place">
                    {displayPlace}
                </Typography>
            </div>

            <div className="max-mag-card-footer">
                <Typography variant="body2" className="max-mag-card-time">
                    {formatTime(maxMag?.time)}
                </Typography>
                {sourceLabel && (
                    <Typography variant="caption" className="max-mag-card-source">
                        {sourceLabel}
                    </Typography>
                )}
            </div>
        </div>
    );
}
