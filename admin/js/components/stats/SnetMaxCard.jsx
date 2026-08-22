const { Typography } = MaterialUI;
const { useMemo } = React;

/**
 * S-Net 历史最大震度卡片 (SnetMaxCard)
 * 展示历史最大震度 Top3 测站（单行：站名 + 震度标签 + 数值），
 * 底部左时间、右数据源。
 *
 * @param {Object} props
 * @param {Object} [props.style]
 * @param {string} [props.className='']
 */
function SnetMaxCard({ style, className = '' }) {
    const { state } = useAppContext();
    const { stats, config } = state;
    const displayTimezone = config.displayTimezone || 'UTC+8';

    const topPeaks = useMemo(() => {
        const raw = Array.isArray(stats?.snetTopPeaks) ? stats.snetTopPeaks : [];
        // 先过滤再取 Top3；过滤后为空才降级到 snetGlobalMax，避免脏数据导致空态。
        const filtered = raw.map((item) => ({
            stationName: String(item.station_name || item.stationName || item.station_id || item.stationId || '').trim(),
            shindo: Number(item.shindo),
            shindoLabel: String(item.shindo_label || item.shindoLabel || '').trim(),
            at: item.at || '',
        })).filter((item) => item.stationName && Number.isFinite(item.shindo));
        if (filtered.length > 0) {
            return filtered.slice(0, 3);
        }

        // 兼容仅有 global_max 的旧载荷
        const globalMax = stats?.snetGlobalMax;
        if (!globalMax) return [];
        const shindo = Number(globalMax.shindo);
        if (!Number.isFinite(shindo)) return [];
        return [{
            stationName: String(globalMax.station_name || globalMax.stationName || globalMax.station_id || '').trim() || '未知测站',
            shindo,
            shindoLabel: String(globalMax.shindo_label || globalMax.shindoLabel || '').trim(),
            at: globalMax.at || '',
        }];
    }, [stats]);

    const footerTime = useMemo(() => {
        // 优先用 Top1 峰值时间；否则回退 last_observation / global_max
        const fromTop = topPeaks[0]?.at;
        const fallback = stats?.snetGlobalMax?.at || stats?.snetLastObservationAt || '';
        const raw = fromTop || fallback;
        if (!raw) return '未知时间';
        return formatTimeWithZone(raw, displayTimezone, true);
    }, [topPeaks, stats, displayTimezone]);

    if (!topPeaks.length) {
        return (
            <div className={`card snet-max-card snet-max-card--empty ${className}`} style={style}>
                <span className="snet-max-card-empty-icon">🌊</span>
                <Typography variant="body2" className="snet-max-card-empty-text">
                    暂无 S-Net 峰值记录
                </Typography>
            </div>
        );
    }

    return (
        <div className={`card snet-max-card ${className}`} style={style}>
            <div className="snet-max-card-watermark">🌊</div>

            <div className="chart-card-header snet-max-card-header">
                <span className="stats-card-header-icon">🌊</span>
                <Typography variant="h6" className="snet-max-card-title">S-Net 历史最大震度</Typography>
            </div>

            <div className="snet-max-card-list">
                {topPeaks.map((item, index) => {
                    // 后端 label 通常是「0以下 / 5強 / 1」等，不含「震度」前缀
                    const rawLabel = item.shindoLabel || '';
                    const label = rawLabel
                        ? (rawLabel.startsWith('震度') ? rawLabel : `震度${rawLabel}`)
                        : `震度${item.shindo.toFixed(3)}`;
                    const valueText = Number.isFinite(item.shindo) ? item.shindo.toFixed(3) : '--';
                    return (
                        <div key={`${item.stationName}-${index}`} className="snet-max-card-row">
                            <span className="snet-max-card-rank" aria-hidden="true">{index + 1}</span>
                            <Typography variant="body2" className="snet-max-card-line" component="div">
                                <span className="snet-max-card-station">{item.stationName}</span>
                                <span className="snet-max-card-label">{label}</span>
                                <span className="snet-max-card-value">({valueText})</span>
                            </Typography>
                        </div>
                    );
                })}
            </div>

            <div className="snet-max-card-footer">
                <Typography variant="caption" className="snet-max-card-time">
                    {footerTime}
                </Typography>
                <Typography variant="caption" className="snet-max-card-source">
                    NIED S-Net
                </Typography>
            </div>
        </div>
    );
}
