const { Typography } = MaterialUI;

/**
 * 地震或灾害事件多报更新时间轴面板组件 (EventGroupTimeline)
 * 当某次地震存在多次修正速报或更新时，用户点击卡片会展开此组件。
 * 该组件以垂直时间线形式，由新到旧列出该事件组名下接收到的所有历史报告。
 *
 * 海啸/气象/地震/台风统一复用 EventCard 渲染各报详情，
 * 使历史报与主卡片信息密度一致（chips / sections / 深度等）。
 * 收起由顶部主卡片统一提供，本组件不再接收 onCollapse。
 *
 * @param {Object} props
 * @param {Object} props.group 包含同一事件关联的 events 列表与最新一条 event 记录的分组数据
 * @param {string} props.displayTimezone 全局配置的时区，如 'UTC+8'
 */
function EventGroupTimeline({ group, displayTimezone }) {
    const formatters = window.EventFormatters || {};
    const {
        getDisplayTimeValue,
        resolveEventReportNum,
        formatReportLabel,
    } = formatters;
    const totalReports = group.events.length;
    const groupType = group.latestEvent.type || '';
    const isTyphoonGroup = groupType === 'typhoon';

    /**
     * 将历史快照与最新主事件合并。
     * 身份字段（source/type/place）可继承；状态字段（weather_detail/is_cancelled 等）
     * 仅在本报有值或最新行时继承，避免解除报覆盖历史报。
     */
    const mergeTimelineEvent = (evt, isLatestRow = false) => {
        const latest = group.latestEvent || {};
        const hasOwn = (key) => (
            evt[key] !== undefined && evt[key] !== null && evt[key] !== ''
        );

        // 历史快照若无 weather_detail，尽量从 description 推断取消态，避免误继承最新解除
        const ownCancelled = hasOwn('is_cancelled')
            ? Boolean(evt.is_cancelled)
            : (hasOwn('cancelled')
                ? Boolean(evt.cancelled)
                : (String(evt.level || '').includes('解除')
                    || String(evt.description || '').includes('解除')
                    || null));

        return {
            // 先铺本报快照，再显式写身份/状态字段，避免 ...evt 中的 null/'' 冲掉父级身份
            ...evt,
            // 身份字段：本报有值用本报，否则继承最新主事件
            source: evt.source || latest.source,
            source_id: evt.source_id || latest.source_id || latest.source,
            type: evt.type || latest.type,
            info_type: evt.info_type || latest.info_type,
            weather_type_code: evt.weather_type_code || latest.weather_type_code,
            icon_url: evt.icon_url || latest.icon_url,
            description: hasOwn('description') ? evt.description : (isLatestRow ? latest.description : (evt.description || '')),
            subtitle: hasOwn('subtitle') ? evt.subtitle : (isLatestRow ? latest.subtitle : (evt.subtitle || '')),
            level: hasOwn('level') ? evt.level : (isLatestRow ? latest.level : (evt.level || '')),
            magnitude: hasOwn('magnitude') ? evt.magnitude : (isLatestRow ? latest.magnitude : evt.magnitude),
            depth: hasOwn('depth') ? evt.depth : (isLatestRow ? latest.depth : evt.depth),
            report_num: hasOwn('report_num') ? evt.report_num : (isLatestRow ? latest.report_num : evt.report_num),
            time: evt.time || evt.timestamp || (isLatestRow ? latest.time : ''),
            timestamp: evt.timestamp || evt.time || (isLatestRow ? latest.timestamp : ''),
            recorded_at: evt.recorded_at || (isLatestRow ? latest.recorded_at : evt.recorded_at),
            // 详情摘要：有则用本报；最新行可回退主表；历史行不继承最新解除摘要
            weather_detail: hasOwn('weather_detail')
                ? evt.weather_detail
                : (isLatestRow ? (latest.weather_detail || '') : ''),
            place_name: hasOwn('place_name')
                ? evt.place_name
                : (isLatestRow ? latest.place_name : (evt.place_name || '')),
            max_wave_height: hasOwn('max_wave_height')
                ? evt.max_wave_height
                : (isLatestRow ? latest.max_wave_height : null),
            area_count: hasOwn('area_count')
                ? evt.area_count
                : (isLatestRow ? latest.area_count : null),
            immediate_area_count: hasOwn('immediate_area_count')
                ? evt.immediate_area_count
                : (isLatestRow ? latest.immediate_area_count : null),
            is_cancelled: ownCancelled !== null
                ? ownCancelled
                : (isLatestRow ? Boolean(latest.is_cancelled) : false),
            is_training: hasOwn('is_training')
                ? Boolean(evt.is_training)
                : (hasOwn('isTraining')
                    ? Boolean(evt.isTraining)
                    : (isLatestRow ? Boolean(latest.is_training) : false)),
            latitude: hasOwn('latitude') ? evt.latitude : (isLatestRow ? latest.latitude : evt.latitude),
            longitude: hasOwn('longitude') ? evt.longitude : (isLatestRow ? latest.longitude : evt.longitude),
            // 台风快照字段
            _snapshot_level: evt._snapshot_level || evt.level,
            _snapshot_wind_speed: evt._snapshot_wind_speed ?? evt.wind_speed,
            _snapshot_pressure: evt._snapshot_pressure ?? evt.pressure,
            _snapshot_latitude: evt._snapshot_latitude ?? evt.latitude,
            _snapshot_longitude: evt._snapshot_longitude ?? evt.longitude,
            wind_speed: evt.wind_speed ?? (isLatestRow ? latest.wind_speed : evt.wind_speed),
            pressure: evt.pressure ?? (isLatestRow ? latest.pressure : evt.pressure),
            batch: evt.batch ?? (isLatestRow ? latest.batch : evt.batch),
        };
    };

    return (
        <div className="event-group-timeline-panel">
            {/* 收起由顶部主卡片统一提供，这里只保留轻量统计标题，避免重复「收起」 */}
            <div className="event-group-timeline-header">
                <Typography variant="body2" className="event-group-collapse-text">
                    共 {totalReports} 次更新
                </Typography>
            </div>

            {/* 垂直时间线容器 */}
            <div className="event-group-timeline">
                <div className="event-group-timeline-line"></div>

                {group.events.map((evt, idx) => {
                    // 时间线芯片一律按「组内更新序号」编号（最新=N … 最旧=1），
                    // 避免海啸业务 batch 长期停在「第3报」时，9 次系统更新全部显示成同一个号。
                    // 业务报次（batch）仍由主卡片徽章展示，语义分离。
                    const sequenceNum = totalReports - idx;
                    const isLatest = idx === 0;
                    const mergedEvt = mergeTimelineEvent(evt, isLatest);

                    const businessReportNum = typeof resolveEventReportNum === 'function'
                        ? resolveEventReportNum(mergedEvt)
                        : Number(mergedEvt?.report_num);
                    const hasBusinessReportNum = Number.isInteger(businessReportNum) && businessReportNum > 0;
                    // 台风 / 海啸多报：用序号；地震等业务报次若与序号一致或每报不同，仍可用业务号，
                    // 但当多行业务号撞车时强制回退序号。
                    const useSequenceLabel = isTyphoonGroup
                        || groupType === 'tsunami'
                        || !hasBusinessReportNum;
                    const displayReportNum = useSequenceLabel ? sequenceNum : businessReportNum;
                    // 统一写「第 X 报」；海啸业务号不同时另注业务报数
                    const reportLabelText = typeof formatReportLabel === 'function'
                        ? formatReportLabel(displayReportNum)
                        : `第 ${displayReportNum} 报`;
                    const businessHint = (groupType === 'tsunami'
                        && hasBusinessReportNum
                        && businessReportNum !== sequenceNum)
                        ? (typeof formatReportLabel === 'function'
                            ? formatReportLabel(businessReportNum)
                            : `第 ${businessReportNum} 报`)
                        : '';

                    // 展示时间：优先本报更新时间
                    const displayTime = typeof getDisplayTimeValue === 'function'
                        ? getDisplayTimeValue(mergedEvt, true)
                        : (mergedEvt.time || mergedEvt.timestamp || '');

                    return (
                        <div
                            key={idx}
                            className={`event-group-timeline-item ${idx === group.events.length - 1 ? 'is-last' : ''}`}
                        >
                            <div className={`event-group-timeline-dot ${isLatest ? 'is-latest' : ''}`}></div>

                            <div className="event-group-timeline-row event-group-timeline-row--card">
                                <div className="event-group-timeline-main">
                                    {/* 更新序号 + 业务报次提示 + 最新标 + 时间 */}
                                    <div className="event-group-timeline-meta-row">
                                        <span className={`event-group-report-chip ${isLatest ? 'is-latest' : ''}`}>
                                            {reportLabelText}
                                        </span>
                                        {businessHint ? (
                                            <span className="event-group-business-chip" title="源站业务报次">
                                                业务报数 {businessHint}
                                            </span>
                                        ) : null}
                                        {isLatest && <span className="event-group-latest-chip">最新</span>}
                                        <Typography variant="body2" className="event-group-time-text">
                                            🕒 {formatTimeFriendly(
                                                displayTime,
                                                displayTimezone,
                                                mergedEvt.source || group.latestEvent.source || ''
                                            )}
                                        </Typography>
                                    </div>

                                    {/* 复用主卡片同款 EventCard，展示完整 chips / sections / 深度等。
                                        报次已在上方 chip 展示；hideReportBadge 禁止 batch 回退再画徽章。 */}
                                    <div className="event-group-history-card-wrap">
                                        <EventCard
                                            event={mergedEvt}
                                            displayTimezone={displayTimezone}
                                            isHistory={true}
                                            isExpandable={false}
                                            isExpanded={true}
                                            reportIndex={null}
                                            hideExpandBadge={true}
                                            hideReportBadge={true}
                                        />
                                    </div>
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
