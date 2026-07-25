const { Box, Typography } = MaterialUI;
const { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } = React;

/**
 * 连接健康 Statuspage 面板
 * - 总横幅（全部通道正常 / 部分异常 / 核心中断）
 * - 各连接组 90 天可用性条带
 * - Past Incidents（通道事故，非地震事件）
 *
 * 轮询策略：
 * - 首屏 full 拉一次（实时 + 历史）
 * - 可见时 live 每 15s 刷新实时态
 * - full 历史每 5 分钟刷新；document.hidden 时暂停
 */
function ConnectionHealthPanel() {
    const statusApi = window.DisasterStatusApi;
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [hoverTip, setHoverTip] = useState(null);
    const tipRef = useRef(null);
    const dataRef = useRef(null);
    const lastFullAtRef = useRef(0);

    const LIVE_INTERVAL_MS = 15000;
    const FULL_INTERVAL_MS = 5 * 60 * 1000;

    const mergeLiveIntoData = useCallback((prev, livePayload) => {
        if (!livePayload) return prev;
        if (!prev) {
            return {
                ...livePayload,
                components: Array.isArray(livePayload.components)
                    ? livePayload.components.map((c) => ({
                        ...c,
                        days: Array.isArray(c.days) ? c.days : [],
                        uptime_ratio: c.uptime_ratio ?? null,
                        uptime_percent: c.uptime_percent ?? null,
                    }))
                    : [],
                incidents: Array.isArray(livePayload.incidents) ? livePayload.incidents : [],
                incidents_by_day: Array.isArray(livePayload.incidents_by_day)
                    ? livePayload.incidents_by_day
                    : [],
            };
        }

        const liveByKey = {};
        (Array.isArray(livePayload.components) ? livePayload.components : []).forEach((c) => {
            if (c && c.group_key) liveByKey[c.group_key] = c;
        });

        const prevComponents = Array.isArray(prev.components) ? prev.components : [];
        const mergedComponents = prevComponents.map((comp) => {
            const live = liveByKey[comp.group_key];
            if (!live) return comp;
            return {
                ...comp,
                name: live.name || comp.name,
                current_state: live.current_state,
                current_label: live.current_label,
                enabled: live.enabled,
                connected: live.connected,
                latency_ms: live.latency_ms,
                retry_count: live.retry_count,
                circuit_open: live.circuit_open,
            };
        });

        // 若 live 出现 prev 没有的组件，追加（通常不会）
        Object.keys(liveByKey).forEach((key) => {
            if (!mergedComponents.some((c) => c.group_key === key)) {
                const live = liveByKey[key];
                mergedComponents.push({
                    ...live,
                    days: [],
                    uptime_ratio: null,
                    uptime_percent: null,
                });
            }
        });

        return {
            ...prev,
            overall: livePayload.overall || prev.overall,
            legend: livePayload.legend || prev.legend,
            meta: {
                ...(prev.meta || {}),
                ...(livePayload.meta || {}),
                // 保留历史窗口元信息
                days: (prev.meta && prev.meta.days) || (livePayload.meta && livePayload.meta.days) || 90,
                mode: 'full',
            },
            components: mergedComponents,
        };
    }, []);

    const loadFull = useCallback(async ({ silent = false } = {}) => {
        if (!statusApi || typeof statusApi.getConnectionHealth !== 'function') {
            setError('连接健康接口不可用');
            setLoading(false);
            return;
        }
        try {
            if (!silent) setError('');
            const payload = await statusApi.getConnectionHealth(90, 'full');
            setData(payload || null);
            dataRef.current = payload || null;
            lastFullAtRef.current = Date.now();
        } catch (e) {
            console.error('[ConnectionHealthPanel] full load failed:', e);
            if (!dataRef.current) {
                setError(e?.message || '加载连接健康数据失败');
            }
        } finally {
            setLoading(false);
        }
    }, [statusApi]);

    const loadLive = useCallback(async () => {
        if (!statusApi || typeof statusApi.getConnectionHealth !== 'function') {
            return;
        }
        try {
            const payload = await statusApi.getConnectionHealth(90, 'live');
            setData((prev) => {
                const next = mergeLiveIntoData(prev, payload);
                dataRef.current = next;
                return next;
            });
        } catch (e) {
            // live 失败不打断历史展示
            console.warn('[ConnectionHealthPanel] live load failed:', e);
        }
    }, [statusApi, mergeLiveIntoData]);

    useEffect(() => {
        let cancelled = false;
        let liveTimer = null;
        let fullTimer = null;

        const clearTimers = () => {
            if (liveTimer) {
                clearInterval(liveTimer);
                liveTimer = null;
            }
            if (fullTimer) {
                clearInterval(fullTimer);
                fullTimer = null;
            }
        };

        const tickLive = () => {
            if (cancelled) return;
            if (typeof document !== 'undefined' && document.hidden) return;
            loadLive();
        };

        const tickFull = () => {
            if (cancelled) return;
            if (typeof document !== 'undefined' && document.hidden) return;
            loadFull({ silent: true });
        };

        const startTimers = () => {
            clearTimers();
            if (typeof document !== 'undefined' && document.hidden) return;
            liveTimer = setInterval(tickLive, LIVE_INTERVAL_MS);
            fullTimer = setInterval(tickFull, FULL_INTERVAL_MS);
        };

        const onVisibility = () => {
            if (typeof document === 'undefined') return;
            if (document.hidden) {
                clearTimers();
                return;
            }
            // 回到前台：立即补一次 live；若 full 过旧则补 full
            loadLive();
            if (Date.now() - lastFullAtRef.current >= FULL_INTERVAL_MS) {
                loadFull({ silent: true });
            }
            startTimers();
        };

        // 首屏 full
        loadFull({ silent: false }).then(() => {
            if (cancelled) return;
            startTimers();
        });

        if (typeof document !== 'undefined') {
            document.addEventListener('visibilitychange', onVisibility);
        }

        return () => {
            cancelled = true;
            clearTimers();
            if (typeof document !== 'undefined') {
                document.removeEventListener('visibilitychange', onVisibility);
            }
        };
    }, [loadFull, loadLive]);

    // tooltip：left/top 为浮层左上角（禁止 translate 居中），贴边不裁切
    // 使用 useLayoutEffect，避免 paint 后才定位导致首帧闪左上角
    useLayoutEffect(() => {
        if (!hoverTip || !tipRef.current) return;
        const el = tipRef.current;
        el.style.transform = 'none';
        el.classList.remove('is-below');

        const vw = typeof window !== 'undefined' ? window.innerWidth : 800;
        const vh = typeof window !== 'undefined' ? window.innerHeight : 600;
        const pad = 8;
        const tipW = Math.min(el.offsetWidth || 220, vw - pad * 2);
        const tipH = el.offsetHeight || 80;
        const barH = hoverTip.barHeight || 28;

        let top = hoverTip.y - tipH - 8;
        let below = false;
        if (top < pad) {
            below = true;
            top = hoverTip.y + barH + 8;
        }
        if (top + tipH > vh - pad) {
            top = Math.max(pad, vh - tipH - pad);
        }

        let left = hoverTip.x - tipW / 2;
        left = Math.min(Math.max(left, pad), Math.max(pad, vw - tipW - pad));

        el.style.left = `${Math.round(left)}px`;
        el.style.top = `${Math.round(top)}px`;
        el.style.transform = 'none';
        el.classList.toggle('is-below', below);
    }, [hoverTip]);

    const overallClass = useMemo(() => {
        const state = data?.overall?.state || 'not_monitored';
        return `connection-health-banner connection-health-banner--${state}`;
    }, [data]);

    const formatDuration = (seconds) => {
        const total = Math.max(0, Number(seconds) || 0);
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        if (h <= 0 && m <= 0) return `${total} 秒`;
        if (h <= 0) return `${m} 分钟`;
        if (m <= 0) return `${h} 小时`;
        return `${h} 小时 ${m} 分钟`;
    };

    const formatMinutes = (minutes) => {
        const m = Math.max(0, Number(minutes) || 0);
        if (m < 1) return '< 1 分钟';
        if (m < 60) return `${Math.round(m)} 分钟`;
        const h = Math.floor(m / 60);
        const rest = Math.round(m % 60);
        return rest ? `${h} 小时 ${rest} 分钟` : `${h} 小时`;
    };

    /** 后端 ISO → 本地可读时间（避免直接 slice UTC） */
    const formatDateTime = (value) => {
        const text = String(value || '').trim();
        if (!text) return '';
        // 优先用后端已格式化的 display 字段
        if (data?.overall?.updated_at_display && value === data.overall.updated_at) {
            return data.overall.updated_at_display;
        }
        try {
            const dt = new Date(text);
            if (Number.isNaN(dt.getTime())) {
                return text.replace('T', ' ').slice(0, 19);
            }
            const pad = (n) => String(n).padStart(2, '0');
            return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())} ${pad(dt.getHours())}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())}`;
        } catch (e) {
            return text.replace('T', ' ').slice(0, 19);
        }
    };

    const stateLabel = (state) => {
        const map = {
            operational: '正常',
            degraded: '降级',
            partial_outage: '部分中断',
            major_outage: '中断',
            maintenance: '维护',
            not_monitored: '未启用',
        };
        return map[state] || state || '未知';
    };

    const openTip = (event, comp, day) => {
        const rect = event.currentTarget.getBoundingClientRect();
        setHoverTip({
            component: comp,
            day,
            x: rect.left + rect.width / 2,
            y: rect.top,
            barHeight: rect.height,
        });
    };

    const renderBarTooltip = (component, day) => {
        if (!day) return null;
        const lines = [];
        if (day.state === 'not_monitored' || !day.minutes_monitored) {
            lines.push('未纳入监控 / 无采样');
        } else {
            if (day.minutes_major > 0) {
                lines.push(`中断 ${formatMinutes(day.minutes_major)}`);
            }
            if (day.minutes_partial > 0) {
                lines.push(`部分中断 ${formatMinutes(day.minutes_partial)}`);
            }
            if (day.minutes_degraded > 0) {
                lines.push(`降级 ${formatMinutes(day.minutes_degraded)}`);
            }
            if (
                day.minutes_major <= 0
                && day.minutes_partial <= 0
                && day.minutes_degraded <= 0
            ) {
                lines.push('全天正常');
            }
            if (day.uptime_ratio != null) {
                lines.push(`当日可用性 ${(Number(day.uptime_ratio) * 100).toFixed(2)}%`);
            }
        }
        return (
            <div className="connection-health-tooltip" role="tooltip">
                <div className="connection-health-tooltip__title">{component.name}</div>
                <div className="connection-health-tooltip__day">{day.day}</div>
                {lines.map((line, idx) => (
                    <div key={`${day.day}-L${idx}`} className="connection-health-tooltip__line">
                        {line}
                    </div>
                ))}
            </div>
        );
    };

    if (loading && !data) {
        return (
            <div className="card connection-health-panel">
                <Box className="status-card-header status-card-header--compact">
                    <div className="status-card-icon status-card-icon--service">📡</div>
                    <Typography variant="h6" className="status-card-title">通道健康</Typography>
                </Box>
                <div className="connection-health-skeleton">
                    <div className="skeleton connection-health-skeleton-banner"></div>
                    <div className="skeleton connection-health-skeleton-row"></div>
                    <div className="skeleton connection-health-skeleton-row"></div>
                    <div className="skeleton connection-health-skeleton-row"></div>
                </div>
            </div>
        );
    }

    if (error && !data) {
        return (
            <div className="card connection-health-panel">
                <Box className="status-card-header status-card-header--compact">
                    <div className="status-card-icon status-card-icon--service">📡</div>
                    <Typography variant="h6" className="status-card-title">通道健康</Typography>
                </Box>
                <Typography variant="body2" className="connection-health-error">
                    {error}
                </Typography>
            </div>
        );
    }

    const overall = data?.overall || {};
    const meta = data?.meta || {};
    const components = Array.isArray(data?.components) ? data.components : [];
    const incidentsByDay = Array.isArray(data?.incidents_by_day) ? data.incidents_by_day : [];
    const legend = Array.isArray(data?.legend) ? data.legend : [];
    const updatedDisplay = overall.updated_at_display
        || formatDateTime(overall.updated_at);

    return (
        <div className="card connection-health-panel">
            <Box className="status-card-header status-card-header--compact">
                <div className="status-card-icon status-card-icon--service">📡</div>
                <Box>
                    <Typography variant="h6" className="status-card-title">通道健康</Typography>
                    <Typography variant="caption" className="connection-health-subtitle">
                        近 {meta.days || 90} 天数据通道可用性
                    </Typography>
                </Box>
            </Box>

            <div className={overallClass} role="status" aria-live="polite">
                <span className="connection-health-banner__label">
                    {overall.label || '状态未知'}
                </span>
                <span className="connection-health-banner__meta">
                    {overall.running === false ? '服务未运行 · ' : ''}
                    {updatedDisplay ? `更新于 ${updatedDisplay}` : ''}
                </span>
            </div>

            {legend.length > 0 && (
                <div className="connection-health-legend" aria-label="状态图例">
                    {legend.map((item) => (
                        <span key={item.state} className="connection-health-legend__item">
                            <span className={`connection-health-legend__swatch is-${item.state}`} />
                            {item.label}
                        </span>
                    ))}
                </div>
            )}

            <div className="connection-health-list">
                {components.map((comp) => {
                    const days = Array.isArray(comp.days) ? comp.days : [];
                    const uptimeText = comp.uptime_percent != null
                        ? `${Number(comp.uptime_percent).toFixed(2)}% uptime`
                        : '暂无足够样本';
                    return (
                        <div key={comp.group_key} className="connection-health-row">
                            <div className="connection-health-row__header">
                                <Typography className="connection-health-row__name">
                                    {comp.name}
                                </Typography>
                                <Typography className={`connection-health-row__state is-${comp.current_state}`}>
                                    {comp.current_label || stateLabel(comp.current_state)}
                                </Typography>
                            </div>

                            <div
                                className="connection-health-bars"
                                aria-label={`${comp.name} 近 ${days.length} 天可用性`}
                            >
                                {days.map((day) => (
                                    <span
                                        key={`${comp.group_key}-${day.day}`}
                                        className={`connection-health-bar is-${day.state || 'not_monitored'}`}
                                        role="img"
                                        aria-label={`${comp.name} ${day.day} ${stateLabel(day.state)}`}
                                        tabIndex={-1}
                                        onMouseEnter={(e) => openTip(e, comp, day)}
                                        onMouseLeave={() => setHoverTip(null)}
                                    />
                                ))}
                            </div>

                            <div className="connection-health-row__footer">
                                <span className="connection-health-row__edge">{(meta.days || 90)} 天前</span>
                                <span className="connection-health-row__rule" aria-hidden="true" />
                                <span className="connection-health-row__uptime">{uptimeText}</span>
                                <span className="connection-health-row__rule" aria-hidden="true" />
                                <span className="connection-health-row__edge">今天</span>
                            </div>
                        </div>
                    );
                })}
            </div>

            <div className="connection-health-incidents">
                <Typography variant="h6" className="connection-health-incidents__title">
                    历史通道事故
                </Typography>
                {incidentsByDay.length === 0 ? (
                    <Typography variant="body2" className="connection-health-incidents__empty">
                        暂无事故记录
                    </Typography>
                ) : (
                    incidentsByDay.map((group) => (
                        <div key={group.day} className="connection-health-incident-day">
                            <Typography className="connection-health-incident-day__label">
                                {group.label || group.day}
                            </Typography>
                            {(!group.incidents || group.incidents.length === 0) ? (
                                <Typography variant="body2" className="connection-health-incidents__empty">
                                    {group.empty_text || '无通道事故'}
                                </Typography>
                            ) : (
                                group.incidents.map((inc) => (
                                    <div key={inc.id || `${inc.group_key}-${inc.started_at}`} className="connection-health-incident">
                                        <div className={`connection-health-incident__title is-${inc.severity}`}>
                                            {inc.title || `${inc.component_name} 异常`}
                                        </div>
                                        <div className="connection-health-incident__meta">
                                            <strong>
                                                {inc.status === 'resolved' ? '已解决' : '调查中'}
                                            </strong>
                                            {inc.severity_label ? ` · ${inc.severity_label}` : ''}
                                            {inc.duration_seconds != null
                                                ? ` · ${formatDuration(inc.duration_seconds)}`
                                                : ''}
                                        </div>
                                        <div className="connection-health-incident__time">
                                            {formatDateTime(inc.started_at)}
                                            {inc.ended_at
                                                ? ` → ${formatDateTime(inc.ended_at)}`
                                                : ' → 进行中'}
                                        </div>
                                        {Array.isArray(inc.timeline) && inc.timeline.length > 0 && (
                                            <ul className="connection-health-incident__timeline">
                                                {inc.timeline.slice().reverse().map((node, idx) => (
                                                    <li key={`${inc.id}-tl-${idx}`}>
                                                        <strong>{node.status || ''}</strong>
                                                        {node.message ? ` — ${node.message}` : ''}
                                                        {node.at
                                                            ? `（${formatDateTime(node.at).slice(0, 16)}）`
                                                            : ''}
                                                    </li>
                                                ))}
                                            </ul>
                                        )}
                                    </div>
                                ))
                            )}
                        </div>
                    ))
                )}
            </div>

            {hoverTip && (
                <div ref={tipRef} className="connection-health-tooltip-layer">
                    {renderBarTooltip(hoverTip.component, hoverTip.day)}
                </div>
            )}
        </div>
    );
}
