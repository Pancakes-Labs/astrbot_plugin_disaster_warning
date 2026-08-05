const { Box, Typography } = MaterialUI;

/**
 * 地震预警 (EEW) 生效与无预警时长统计卡片 (EewStatusCard)
 * 该组件分析当前长连接推送服务中，各个地震预警机构的发出状态。
 * 核心交互与设计：
 * 1. 本地跳秒定时器：组件内置 1000ms 的跳秒心跳，在“无预警”生效期，本地累加秒数，
 *    实现“xx天xx分xx秒 无 XX预警网”的跑秒效果，避免频繁请求服务器。
 * 2. 状态分流展示：
 *    - active：展示红底高亮警告文案，陈列目前发生地震的地点与震级。
 *    - inactive：展示距离上一次发布预警所度过的精确安全时长。
 *    - no_data：对于全新布署的机构，展示暂无历史计算数据。
 *    - unavailable：提示管理员尚未在配置中勾选此数据源的监听。
 */
function EewStatusCard() {
    const { state } = useAppContext();
    const { status, dataLoaded } = state;
    const eewQueryStatus = status.eewQueryStatus || null;

    // 建立本地秒级定时器，使“无预警” elapsed_seconds 在前端平滑跳动，避免频繁轮询
    const [tickNowMs, setTickNowMs] = React.useState(Date.now());
    
    React.useEffect(() => {
        const timer = setInterval(() => {
            setTickNowMs(Date.now());
        }, 1000);
        return () => clearInterval(timer);
    }, []);

    /**
     * 安全的时间转换器
     */
    const parseDateSafe = React.useCallback((value) => {
        if (!value) return null;
        const d = new Date(value);
        return Number.isNaN(d.getTime()) ? null : d;
    }, []);

    /**
     * 换算秒数为 “天时分秒” 的可读中文时间短语
     */
    const formatElapsed = React.useCallback((seconds) => {
        const total = Math.max(0, Math.floor(Number(seconds) || 0));
        const days = Math.floor(total / 86400);
        const hours = Math.floor((total % 86400) / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const secs = total % 60;

        if (days > 0) return `${days}天${hours}时${minutes}分${secs}秒`;
        if (hours > 0) return `${hours}时${minutes}分${secs}秒`;
        if (minutes > 0) return `${minutes}分${secs}秒`;
        return `${secs}秒`;
    }, []);

    // 状态预处理：分类归纳各防灾发布机构的状态信息
    const renderData = React.useMemo(() => {
        const institutions = Array.isArray(eewQueryStatus?.institutions)
            ? eewQueryStatus.institutions
            : [];

        const activeLines = [];
        const inactiveItems = [];
        const noDataLines = [];
        const unavailableLines = [];

        for (const item of institutions) {
            const displayName = item?.display_name || '未知机构';
            const activeName = item?.active_name || displayName;
            const statusText = item?.status;

            // A. 未启用
            if (statusText === 'unavailable') {
                unavailableLines.push(`- ${displayName}：未启用对应数据源开关，无法计算无 EEW 时间`);
                continue;
            }

            // B. 已启用但无计算数据
            if (statusText === 'no_data') {
                noDataLines.push(`- ${displayName}：已启用数据源，但暂无可计算历史数据`);
                continue;
            }

            // C. 正在发布地震预警（双保险：若后端下发的 expires_at 已过期则视为无生效预警）
            if (statusText === 'active') {
                const expiresAt = parseDateSafe(item?.expires_at);
                const isExpired = expiresAt && tickNowMs > expiresAt.getTime();
                if (!isExpired) {
                    const magnitude = item?.magnitude;
                    const place = item?.place || '未知地点';
                    let magText = '?';
                    if (magnitude !== null && magnitude !== undefined) {
                        const num = Number(magnitude);
                        magText = Number.isFinite(num) ? num.toFixed(1) : String(magnitude);
                    }
                    activeLines.push(`[${activeName}] 当前正在发布地震预警：M ${magText} ${place}`);
                    continue;
                }
                // 已过期：回落到下方“无生效预警”统计分支
            }

            // D. 安全无预警状态下：根据发震时间 issued_at 与本地跳表累加秒数
            const issuedAt = parseDateSafe(item?.issued_at);
            let elapsedSeconds = Number(item?.elapsed_seconds) || 0;
            if (issuedAt) {
                elapsedSeconds = Math.max(0, Math.floor((tickNowMs - issuedAt.getTime()) / 1000));
            }
            inactiveItems.push({
                elapsedSeconds,
                text: `${formatElapsed(elapsedSeconds)} 无 ${displayName}`,
            });
        }

        // 按安全时长升序排列
        inactiveItems.sort((a, b) => a.elapsedSeconds - b.elapsedSeconds);

        return {
            activeLines,
            inactiveLines: inactiveItems.map((x) => x.text),
            noDataLines,
            unavailableLines,
        };
    }, [eewQueryStatus, tickNowMs, formatElapsed, parseDateSafe]);

    // 2. 状态：连接就绪前渲染骨架线
    if (!dataLoaded) {
        return (
            <div className="card status-card-full-height">
                <Box className="status-card-header status-card-header--compact">
                    <div className="status-card-icon status-card-icon--eew">📡</div>
                    <Typography variant="h6" className="status-card-title">地震预警状态</Typography>
                </Box>
                <div className="skeleton eew-skeleton-line"></div>
                <div className="skeleton eew-skeleton-line eew-skeleton-line--wide"></div>
                <div className="skeleton eew-skeleton-line eew-skeleton-line--medium"></div>
            </div>
        );
    }

    const { activeLines, inactiveLines, noDataLines, unavailableLines } = renderData;
    const hasStatusData = Array.isArray(eewQueryStatus?.institutions) && eewQueryStatus.institutions.length > 0;

    return (
        <div className="card status-card-full-height">
            {/* 卡片头部 */}
            <Box className="status-card-header status-card-header--compact">
                <div className="status-card-icon status-card-icon--eew">📡</div>
                <Typography variant="h6" className="status-card-title">地震预警状态</Typography>
            </Box>

            {!hasStatusData ? (
                <Typography variant="body2" className="eew-empty-text">当前暂无地震预警状态数据</Typography>
            ) : (
                <Box className="eew-lines">
                    {/* A. 正在发生的地震紧急警报 (带强提醒效果) */}
                    {activeLines.length === 0 ? (
                        <Typography variant="body2" className="eew-line-strong">当前没有正在生效的地震预警</Typography>
                    ) : (
                        activeLines.map((line, idx) => (
                            <Typography key={`active-${idx}`} variant="body2" className="eew-line-strong">
                                {line}
                            </Typography>
                        ))
                    )}

                    {/* B. 安全状态下度过的时间列表 */}
                    {inactiveLines.length > 0 && (
                        <>
                            <Box className="eew-section-spacer" />
                            {inactiveLines.map((line, idx) => (
                                <Typography key={`inactive-${idx}`} variant="body2" className="eew-line-muted">
                                    {line}
                                </Typography>
                            ))}
                        </>
                    )}

                    {/* C. 暂无历史数据的服务列表 */}
                    {noDataLines.length > 0 && (
                        <>
                            <Box className="eew-section-spacer" />
                            <Typography variant="body2" className="eew-line-strong">以下机构暂无可计算的历史 EEW 数据：</Typography>
                            {noDataLines.map((line, idx) => (
                                <Typography key={`nodata-${idx}`} variant="body2" className="eew-line-subtle">
                                    {line}
                                </Typography>
                            ))}
                        </>
                    )}

                    {/* D. 未启用监听的服务列表 */}
                    {unavailableLines.length > 0 && (
                        <>
                            <Box className="eew-section-spacer" />
                            <Typography variant="body2" className="eew-line-strong">以下机构因数据源开关未启用，无法参与计算：</Typography>
                            {unavailableLines.map((line, idx) => (
                                <Typography key={`unavailable-${idx}`} variant="body2" className="eew-line-subtle">
                                    {line}
                                </Typography>
                            ))}
                        </>
                    )}
                </Box>
            )}
        </div>
    );
}
