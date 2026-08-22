const { Typography } = MaterialUI;

/**
 * 会话推送统计排行榜卡片组件 (SessionPushCard)
 * 以排名条形图的形式展示推送数量最多的前 10 个会话，
 * 帮助运维人员快速识别高频推送目标。
 * 排行条背景百分比宽度基于当前 Top 1 的最高推送数自适应等比算得。
 *
 * 展示规则：
 * 1. 会话标识截断为 UMO 尾部 session_id，避免完整 UMO 过长；
 * 2. 若存在备注名，则按 `session_id (备注名)` 展示；
 * 3. 计数仅展示实际推送次数，不做 pushed/received 对比。
 *
 * @param {Object} props
 * @param {string} [props.className=''] 外部 CSS 类
 * @param {Object} [props.style] 外部自定义样式
 */
function SessionPushCard({ className = '', style }) {
    const { state } = useAppContext();
    const { stats } = state;

    // 从归一化后的统计状态中提取会话推送排行数据
    const topSessions = stats && stats.topSessions ? stats.topSessions : [];

    // 状态：若无数据，渲染空提示卡片
    if (!topSessions || topSessions.length === 0) {
        return (
            <div className={`card session-push-card ${className}`} style={style}>
                <div className="chart-card-header">
                    <span className="stats-card-header-icon">👥</span>
                    <Typography variant="h6">会话推送统计 (TOP 10)</Typography>
                </div>
                <Typography variant="body2" className="session-push-empty-text">
                    暂无数据
                </Typography>
            </div>
        );
    }

    // 获取排第一的最高推送数作为 100% 比例分母基底
    const maxPushed = Math.max(1, ...topSessions.map(s => Number(s.pushed) || 0));

    return (
        <div className={`card session-push-card ${className}`} style={style}>
            {/* 卡片头部 */}
            <div className="chart-card-header">
                <span className="stats-card-header-icon">👥</span>
                <Typography variant="h6">会话推送统计 (TOP 10)</Typography>
            </div>

            {/* 渲染 Top 10 排行项 */}
            <div className="session-push-items">
                {topSessions.map((item, index) => {
                    const pushed = Number(item.pushed) || 0;
                    // 等比换算条形占比，微调基数防止在极小比例下彩条不可见
                    const percentage = (pushed / maxPushed) * 100;
                    const displayName = item.displayName
                        || (typeof formatSessionDisplayLabel === 'function'
                            ? formatSessionDisplayLabel(item)
                            : (item.sessionId || item.session || '未知会话'));
                    const fullUmo = item.session || displayName;

                    return (
                        <div
                            key={`${fullUmo}-${index}`}
                            className="session-push-row"
                            style={{ '--session-push-percent': `calc(${percentage}% + 4px)` }}
                            title={fullUmo}
                        >
                            {/* 条形填充条 */}
                            <div className="session-push-progress">
                                <div className="session-push-progress-bar"></div>
                            </div>

                            {/* 文字与数值区 */}
                            <div className="session-push-content">
                                <div className="session-push-label-wrap">
                                    {/* 前三名冠亚季军特殊渲染 podium 金银铜效果 */}
                                    <div className={`session-push-rank ${index < 3 ? 'session-push-rank--podium' : ''}`}>
                                        {index + 1}
                                    </div>
                                    <Typography variant="body2" noWrap className="session-push-label">
                                        {displayName}
                                    </Typography>
                                </div>
                                <div className="session-push-metrics">
                                    <span className="session-push-pushed">{pushed}</span>
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
