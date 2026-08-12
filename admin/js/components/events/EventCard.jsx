const { Typography } = MaterialUI;

/**
 * 灾害事件单卡片渲染组件 (EventCard)
 * 渲染单个灾害/预警的详细摘要信息，支持地震（速报/预警）、海啸、气象预警等多种灾害类型。
 * 针对不同预警类型，展示对应的专属徽标 (Badge) 以及气象站图标，并支持卡片伸缩折叠展示多报更新。
 *
 * @param {Object} props
 * @param {Object} props.event 事件的核心数据负载对象
 * @param {string} props.displayTimezone 全局配置的展示时区，如 'UTC+8'
 * @param {boolean} [props.isHistory=false] 是否渲染为历史列表小尺寸卡片样式
 * @param {boolean} [props.isExpandable=false] 卡片是否包含多报更新并可展开折叠
 * @param {boolean} [props.isExpanded=false] 当前是否处于展开状态
 * @param {number|null} [props.reportIndex=null] 多报更新时的历史期数标签
 * @param {boolean} [props.hideExpandBadge=false] 隐藏右侧展开/收起控件
 * @param {boolean} [props.hideReportBadge=false] 隐藏标题旁「第N报」徽章（时间线已有 chip 时使用）
 */
function EventCard({
    event,
    displayTimezone,
    isHistory = false,
    isExpandable = false,
    isExpanded = false,
    reportIndex = null,
    hideExpandBadge = false,
    hideReportBadge = false,
}) {
    const formatters = window.EventFormatSerialization || window.EventFormatters || {};
    const {
        buildEarthquakeTitle,
        getEarthquakeBadgeContent,
        buildWeatherIconFallbackHandler: _rawBuildHandler,
        resolveWeatherColor = () => null,
        resolveWeatherIconCode = () => null,
        resolveLocalWeatherIconUrl = () => null,
        resolveWeatherFallbackUrl = () => null,
        buildTsunamiTitle = null,
        buildTsunamiMeta = null,
        resolveEventReportNum = null,
        formatReportLabel = null,
        formatMagnitudeBadge = null,
    } = formatters;
    const buildWeatherIconFallbackHandler = _rawBuildHandler || ((_, cb) => (e) => typeof cb === 'function' && cb(e));
    const evt = event || {};
    const eventType = evt.type || evt._groupType || '';
    
    // 逻辑判别不同的防灾事件类型
    const isEarthquake = eventType === 'earthquake' || eventType === 'earthquake_warning';
    const isTsunami = eventType === 'tsunami';
    const isWeather = eventType === 'weather_alarm';
    const isTyphoon = eventType === 'typhoon';
    
    // 生成动态标题文本：地震调用特定算法，气象与台风优先展示 subtitle，海啸走专用构建器
    const normalizeTyphoonTitle = (value) => {
        const text = String(value || '').trim();
        if (!text) return '';
        // 统一括号风格，避免“巴威（BAVI）”与“巴威 (BAVI)”混杂
        return text
            .replace(/\s*\(\s*/g, '（')
            .replace(/\s*\)\s*/g, '）')
            .replace(/TD\s*No\.?\s*/gi, 'TD');
    };
    // 海啸：专用标题构建（兼容旧库「海啸信息 (信息)」与新结构化字段）
    const resolveTsunamiDisplayTitle = () => {
        if (typeof buildTsunamiTitle === 'function') {
            return buildTsunamiTitle(evt) || '海啸情报';
        }
        return evt.description || evt.subtitle || evt.place_name || '海啸情报';
    };
    const displayTitle = isEarthquake
        ? buildEarthquakeTitle(evt)
        : (isTyphoon
            ? (
                normalizeTyphoonTitle(evt.subtitle)
                || normalizeTyphoonTitle(evt.description)
                || normalizeTyphoonTitle(evt.place_name)
                || normalizeTyphoonTitle(evt.real_event_id)
                || '未知台风'
            )
            : (isTsunami
                ? resolveTsunamiDisplayTitle()
                : ((isWeather)
                    ? (evt.subtitle || evt.description || '未知位置')
                    : (evt.description || '未知位置'))));
    const tsunamiMetaModel = isTsunami && typeof buildTsunamiMeta === 'function'
        ? buildTsunamiMeta(evt)
        : null;
    const tsunamiMeta = (() => {
        if (!tsunamiMetaModel) return null;
        // 兼容旧版返回字符串
        if (typeof tsunamiMetaModel === 'string') {
            return tsunamiMetaModel
                ? { chips: [], sections: [], text: tsunamiMetaModel, tone: 'default' }
                : null;
        }
        const chips = Array.isArray(tsunamiMetaModel.chips) ? tsunamiMetaModel.chips : [];
        const sections = Array.isArray(tsunamiMetaModel.sections) ? tsunamiMetaModel.sections : [];
        if (!chips.length && !sections.length && !tsunamiMetaModel.text) return null;
        return tsunamiMetaModel;
    })();
    const tsunamiToneClass = tsunamiMeta?.tone
        ? `is-tone-${tsunamiMeta.tone}`
        : '';

    // 初始化徽标内容与对应 CSS 类名
    let badgeContent = '❓';
    let badgeClass = 'badge-unknown';
    let weatherIconUrl = null;
    let colorHint = null;
    let earthquakeBadgeMeta = null;

    // 1. 地震：获取烈度/震级元数据与对应危险警示判色
    if (isEarthquake) {
        earthquakeBadgeMeta = getEarthquakeBadgeContent(evt);
        badgeContent = earthquakeBadgeMeta?.text || '--';
        badgeClass = 'badge-earthquake';
    } 
    // 2. 海啸：采用海浪图标 🌊
    else if (isTsunami) {
        badgeContent = '🌊';
        badgeClass = 'badge-tsunami';
    } 
    // 3. 气象预警：采用特定预警图标或 fallback 云朵 ☁️
    else if (isWeather) {
        badgeContent = '☁️';
        badgeClass = 'badge-weather';
        const normalizedIconUrl = typeof evt.icon_url === 'string' ? evt.icon_url.trim() : '';
        const weatherTypeCode = String(evt.weather_type_code || '').trim();
        colorHint = resolveWeatherColor(weatherTypeCode);
        // 本地优先：先按 11B 码解析本地具体预警图标；无本地文件时再用服务端返回的 URL
        const localIconUrl = resolveLocalWeatherIconUrl(weatherTypeCode);
        const effectiveIconUrl = localIconUrl || normalizedIconUrl;
        // 委托共享函数解析本地通用回退路径（按编码颜色 / 标题文本颜色）
        let fallbackUrl = resolveWeatherFallbackUrl(weatherTypeCode);

        // 当编码无法解析颜色时，尝试从标题/级别文本中提取颜色关键词（如"黄色"、"红色"）
        if (!fallbackUrl) {
            const haystack = `${evt.subtitle || ''} ${evt.description || ''} ${evt.level || ''}`;
            const colorMatch = haystack.match(/(红色|橙色|黄色|蓝色)/);
            if (colorMatch) {
                const colorMap = {
                    '红色': 'red',
                    '橙色': 'orange',
                    '黄色': 'yellow',
                    '蓝色': 'blue',
                };
                colorHint = colorMap[colorMatch[1]];
                fallbackUrl = resolveWeatherFallbackUrl(`_${colorHint}`);
            }
        }

        weatherIconUrl = effectiveIconUrl || fallbackUrl || null;
    }
    // 4. 台风：使用专属旋风徽标；风速使用独立 wind_speed 字段（单位 m/s）。
    else if (isTyphoon) {
        badgeContent = '🌀';
        badgeClass = 'badge-typhoon';
    }

    const typhoonWind = Number(evt._snapshot_wind_speed ?? evt.wind_speed);
    const typhoonPressure = evt._snapshot_pressure != null
        ? Number(evt._snapshot_pressure)
        : (evt.pressure != null ? Number(evt.pressure) : null);
    // 区分当前观测强度与历史峰值：level 存峰值，_snapshot_level 存当前观测
    const typhoonCurrentLevel = evt._snapshot_level || '';
    const typhoonPeakLevel = evt.level || '';
    const typhoonShowPeakDiff = typhoonCurrentLevel && typhoonPeakLevel && typhoonCurrentLevel !== typhoonPeakLevel;
    // 台风中心位置 / 编号：复用 typhoonFormatters 的统一格式化工具
    const typhoonFormatters = window.DisasterTyphoonFormatters;
    const typhoonCoords = isTyphoon && typhoonFormatters
        ? typhoonFormatters.formatTyphoonCoords(
            evt._snapshot_latitude ?? evt.latitude,
            evt._snapshot_longitude ?? evt.longitude,
        )
        : '';
    // 短编号字段优先级与查询面板/后端对齐：eqsc_id → typhoon_id → real_event_id → unique_id
    // 注意：绝不把 event_id 作为候选传入——它是数据库自增主键，会被纯数字规则误截断成随机短编号，历史报次快照尤其容易踩坑。
    const typhoonShortId = isTyphoon && typhoonFormatters?.formatTyphoonShortId
        ? typhoonFormatters.formatTyphoonShortId(
            evt.eqsc_id,
            evt.typhoon_id,
            evt.real_event_id,
            evt.unique_id,
        )
        : '';
    const typhoonMeta = isTyphoon
        ? [
            // 小字最前统一附带编号，例如「编号：2609」
            typhoonShortId ? `编号：${typhoonShortId}` : '',
            typhoonCurrentLevel
                ? (typhoonShowPeakDiff
                    ? `当前强度：${typhoonCurrentLevel}（峰值：${typhoonPeakLevel}）`
                    : `强度：${typhoonCurrentLevel}`)
                : (typhoonPeakLevel ? `强度：${typhoonPeakLevel}` : ''),
            typhoonCoords ? `中心位置：(${typhoonCoords})` : '',
            // 过滤 0 / 负数 / 非数字，避免脏数据展示"最大风速：0.0 m/s"
            (Number.isFinite(typhoonWind) && typhoonWind > 0)
                ? `最大风速：${typhoonWind.toFixed(1)} m/s`
                : '',
            // 气压：过滤 0 / 负数 / 非数字
            (Number.isFinite(typhoonPressure) && typhoonPressure > 0)
                ? `中心气压：${typhoonPressure} hPa`
                : '',
        ].filter(Boolean).join(' · ')
        : '';

    // 计算报告第几期 (第几报) 的文案：统一走 resolveEventReportNum，
    // 避免标题内嵌 batch 与 report_num 徽章两套语义打架。
    // hideReportBadge：时间线历史行上方已有序号 chip，禁止再从 batch 回退画徽章。
    const resolvedReportNum = hideReportBadge
        ? null
        : (typeof resolveEventReportNum === 'function'
            ? resolveEventReportNum(evt, reportIndex)
            : (reportIndex !== null && reportIndex > 0
                ? reportIndex
                : (Number(evt.report_num) > 0 ? Number(evt.report_num) : null)));
    const reportLabel = typeof formatReportLabel === 'function'
        ? formatReportLabel(resolvedReportNum)
        : (resolvedReportNum ? `第 ${resolvedReportNum} 报` : '');

    // 地震深度：折叠外也直接可见。
    // 合法深度 >= 0；缺失/负数不展示；0 km 映射为「极浅」。
    const earthquakeDepthRaw = evt.depth;
    const earthquakeDepthNum = Number(earthquakeDepthRaw);
    const hasValidEarthquakeDepth = earthquakeDepthRaw !== null
        && earthquakeDepthRaw !== undefined
        && earthquakeDepthRaw !== ''
        && Number.isFinite(earthquakeDepthNum)
        && earthquakeDepthNum >= 0;
    const earthquakeDepthText = hasValidEarthquakeDepth
        ? (earthquakeDepthNum === 0
            ? '极浅'
            : `${Number.isInteger(earthquakeDepthNum) ? earthquakeDepthNum : earthquakeDepthNum}km`)
        : '';
    const earthquakeMagnitudeText = typeof formatMagnitudeBadge === 'function'
        ? formatMagnitudeBadge(evt.magnitude ?? evt._groupMagnitude)
        : '';

    // 正文展开语义：
    // - 气象预警：weather_detail / description
    // - 地震情报补充正文：weather_detail（P2P 各地震度、CENC 烈度速报、FSSN CMT 等）
    // 注意：地震标题 description 常是「M x.x 地点」，不能当正文回退，否则会误出展开入口。
    const detailBodyText = (() => {
        const weatherDetail = String(evt.weather_detail || '').trim();
        if (isWeather) {
            return weatherDetail || String(evt.description || '').trim();
        }
        if (isEarthquake) {
            return weatherDetail;
        }
        return '';
    })();
    // 列表主卡：可点开展开；时间线历史行：isExpanded 时直接展示正文（无展开控件）
    const canExpandDetail = !!detailBodyText && !isHistory && (isWeather || isEarthquake);
    const showDetailPanel = !!detailBodyText && (isWeather || isEarthquake) && isExpanded;
    const detailPanelTitle = isWeather ? '预警正文' : '情报正文';
    const showExpandControl = (isExpandable || canExpandDetail) && !hideExpandBadge;

    // 拼装卡片的主容器 Class 类名
    const cardClassName = [
        'event-card',
        (isExpandable || canExpandDetail) ? 'clickable' : '',
        isHistory ? 'event-card-history' : '',
        showDetailPanel ? 'is-detail-expanded' : '',
    ].filter(Boolean).join(' ');

    // 拼装左侧徽标容器 Class 类名，关联震度警报的危险背景色类
    const badgeClassName = [
        'mag-badge',
        badgeClass,
        isHistory ? 'mag-badge-history' : '',
        weatherIconUrl ? 'mag-badge-weather-icon' : '',
        earthquakeBadgeMeta ? 'mag-badge-earthquake-meta' : '',
        earthquakeBadgeMeta?.toneClass ? `event-badge-tone-${earthquakeBadgeMeta.toneClass}` : '',
    ].filter(Boolean).join(' ');

    // 针对气象预警特殊重置徽标的高宽与背景样式
    const badgeStyle = weatherIconUrl ? {
        width: isHistory ? '40px' : '56px',
        height: isHistory ? '40px' : '56px',
        padding: 0,
        borderRadius: '0',
        backgroundColor: 'transparent',
        boxShadow: 'none',
        overflow: 'visible',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
    } : undefined;

    return (
        <div className={cardClassName}>
            {/* 左侧：特征标识徽标区 */}
            <div className={badgeClassName} style={badgeStyle}>
                {weatherIconUrl ? (
                        // 渲染气象预警图标
                        <img
                            src={weatherIconUrl}
                            alt={badgeContent}
                            data-color-hint={colorHint}
                            className="event-weather-icon"
                            style={{
                                width: '100%',
                                height: '100%',
                                objectFit: 'contain',
                                objectPosition: 'center',
                                transform: 'scale(1.5)',
                                display: 'block',
                            }}
                            onError={buildWeatherIconFallbackHandler(
                                evt.weather_type_code,
                                (e) => {
                                    // 本地回退也失败：退回到默认 Unicode 文字徽标
                                    const img = e.currentTarget;
                                    img.style.display = 'none';
                                    const badgeEl = img.parentElement;
                                    if (badgeEl) {
                                        badgeEl.classList.add('mag-badge-weather-icon-fallback');
                                        const textNode = document.createTextNode(badgeContent);
                                        badgeEl.appendChild(textNode);
                                    }
                                }
                            )}
                        />
                ) : earthquakeBadgeMeta ? (
                    // 渲染结构化的地震速报标签（如：上方烈度，下方震级）
                    <>
                        <span className="event-earthquake-badge-label">
                            {earthquakeBadgeMeta.label}
                        </span>
                        <span className="event-earthquake-badge-value">
                            {badgeContent}
                        </span>
                    </>
                ) : (
                    // 其他类型展示默认文字/Emoji
                    badgeContent
                )}
            </div>

            {/* 右侧：事件核心文字信息区 */}
            <div className="event-main">
                {/* 第一行：标题与报告期数 */}
                <div className="event-title-row">
                    <Typography 
                        variant={isHistory ? 'body2' : 'h6'} 
                        className="event-title-text"
                    >
                        {displayTitle}
                    </Typography>
                    {reportLabel && (
                        <span className={`event-report-label ${reportIndex !== null && reportIndex > 0 ? 'is-history-report' : ''}`}>
                            {reportLabel}
                        </span>
                    )}
                </div>
                {/* 第二行：发布时间、来源发布机构等元数据 */}
                <div className="event-meta">
                    <span className="event-meta-item">
                        🕒 {formatTimeFriendly(evt.time || evt.timestamp, displayTimezone, evt.source || '')}
                    </span>
                    <span className="event-meta-item">
                        <span className="event-meta-separator">•</span>
                        📡 {(typeof formatEventSourceName === 'function'
                            ? formatEventSourceName(evt)
                            : formatSourceName(evt.source_id || evt.source || evt._groupSource || 'unknown'))}
                    </span>
                </div>
                {isEarthquake && (earthquakeDepthText || (earthquakeMagnitudeText && earthquakeMagnitudeText !== '--')) && (
                    <div className="event-meta event-meta-earthquake">
                        {earthquakeMagnitudeText && earthquakeMagnitudeText !== '--' && (
                            <span className="event-meta-item event-quake-chip">
                                🧭 M {earthquakeMagnitudeText}
                            </span>
                        )}
                        {earthquakeDepthText && (
                            <span className="event-meta-item event-quake-chip">
                                ⬇️ 深度 {earthquakeDepthText}
                            </span>
                        )}
                    </div>
                )}
                {typhoonMeta && (
                    <div className="event-meta event-meta-typhoon">
                        <span className="event-meta-item">{typhoonMeta}</span>
                    </div>
                )}
                {tsunamiMeta && (
                    <div className={`event-tsunami-panel ${tsunamiToneClass} ${isHistory ? 'is-history' : ''}`.trim()}>
                        {Array.isArray(tsunamiMeta.chips) && tsunamiMeta.chips.length > 0 && (
                            <div className="event-tsunami-chips">
                                {tsunamiMeta.chips.map((chip) => (
                                    <span
                                        key={chip.key || chip.label}
                                        className={`event-tsunami-chip is-${chip.tone || 'default'}`}
                                    >
                                        {chip.icon ? <span className="event-tsunami-chip-icon">{chip.icon}</span> : null}
                                        <span className="event-tsunami-chip-label">{chip.label}</span>
                                    </span>
                                ))}
                            </div>
                        )}
                        {/* 主卡片与时间线历史行（isExpanded）都展示 sections，信息密度对齐主卡片 */}
                        {(!isHistory || isExpanded) && Array.isArray(tsunamiMeta.sections) && tsunamiMeta.sections.length > 0 && (
                            <div className="event-tsunami-sections">
                                {tsunamiMeta.sections.map((section) => (
                                    <div
                                        key={section.key || section.title}
                                        className={`event-tsunami-section is-${section.tone || 'default'}`}
                                    >
                                        <div className="event-tsunami-section-head">
                                            {section.icon ? <span className="event-tsunami-section-icon">{section.icon}</span> : null}
                                            <span className="event-tsunami-section-title">{section.title}</span>
                                        </div>
                                        {Array.isArray(section.items) && section.items.length > 0 ? (
                                            <ul className="event-tsunami-section-list">
                                                {section.items.map((item, idx) => (
                                                    <li key={`${section.key || 'sec'}-${idx}`}>{item}</li>
                                                ))}
                                            </ul>
                                        ) : (
                                            section.body ? (
                                                <div className="event-tsunami-section-body">{section.body}</div>
                                            ) : null
                                        )}
                                    </div>
                                ))}
                            </div>
                        )}
                        {(!Array.isArray(tsunamiMeta.chips) || !tsunamiMeta.chips.length)
                            && (!Array.isArray(tsunamiMeta.sections) || !tsunamiMeta.sections.length)
                            && tsunamiMeta.text ? (
                            <div className="event-meta event-meta-tsunami">
                                <span className="event-meta-item">{tsunamiMeta.text}</span>
                            </div>
                        ) : null}
                    </div>
                )}
                {/* 气象预警 / 地震情报补充正文展开面板（主卡展开或时间线历史行） */}
                {showDetailPanel && (
                    <div className="event-weather-detail-panel">
                        <div className="event-weather-detail-head">
                            <span className="event-weather-detail-icon">📝</span>
                            <span className="event-weather-detail-title">{detailPanelTitle}</span>
                        </div>
                        <div className="event-weather-detail-body">{detailBodyText}</div>
                    </div>
                )}
            </div>

            {/* 极右侧：多更新折叠 / 正文展开控制提示 */}
            {showExpandControl && (
                <div className="update-badge">
                    <span className="update-count">
                        {isExpanded
                            ? '收起'
                            : (isExpandable
                                ? `${evt.updateCount || ''} 条更新`
                                : '查看正文')}
                    </span>
                    <span className="update-icon">{isExpanded ? '▲' : '▼'}</span>
                </div>
            )}
        </div>
    );
}
