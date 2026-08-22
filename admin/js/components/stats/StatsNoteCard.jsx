const { Typography } = MaterialUI;

/**
 * 统计口径说明小卡片 (StatsNoteCard)
 * 放在总览右侧、事件总数卡片下方，与最大震级 / S-Net 同高。
 *
 * @param {Object} props
 * @param {Object} [props.style]
 * @param {string} [props.className='']
 */
function StatsNoteCard({ style, className = '' }) {
    return (
        <div className={`card stats-note-card ${className}`} style={style}>
            <div className="stats-note-card-watermark" aria-hidden="true">ℹ️</div>

            <div className="chart-card-header stats-note-card-header">
                <span className="stats-card-header-icon">ℹ️</span>
                <Typography variant="h6" className="stats-note-card-title">统计口径说明</Typography>
            </div>

            <div className="stats-note-card-body">
                <ul className="stats-note-card-list">
                    <li className="stats-note-card-item">
                        <span className="stats-note-card-bullet" aria-hidden="true">1</span>
                        <Typography variant="body2" className="stats-note-card-text">
                            震级分布统计口径较宽松，最大地震统计口径更严格。两者可能不一致，这是由于对数据源的筛选逻辑不一样导致的。
                        </Typography>
                    </li>
                    <li className="stats-note-card-item">
                        <span className="stats-note-card-bullet" aria-hidden="true">2</span>
                        <Typography variant="body2" className="stats-note-card-text">
                            S-Net 仅归档测站历史最大震度，不计入事件总数与热力图。
                        </Typography>
                    </li>
                </ul>
            </div>
        </div>
    );
}
