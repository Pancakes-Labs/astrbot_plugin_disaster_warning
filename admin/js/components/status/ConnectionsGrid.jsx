const { Box, Typography } = MaterialUI;
const { useMemo, useState, useCallback } = React;

/**
 * 连接状态网格组件 (ConnectionsGrid)
 * 显示主流数据源（FAN Studio / P2P / Wolfx / OpenQuakeAPI）与 HTTP 辅助通道
 * EQSC、NIED S-Net 的实时连接情况、TCP 延迟、重试次数以及启用的子数据源明细。
 *
 * 布局：
 * - 第 1 列：FAN Studio（可翻转：正面主通道 / 背面 CENC 烈度速报独立 WS）
 * - 第 2 列：P2P + NIED S-Net 上下堆叠（connection-stack）
 * - 第 3 列：Wolfx
 * - 第 4 列：OpenQuakeAPI + EQSC API 上下堆叠
 *
 * 延迟评级：
 * - < 150ms  fast (绿色)
 * - < 460ms  medium (黄色)
 * - 其它 评为 slow (红色)
 */
function ConnectionsGrid() {
    const { state } = useAppContext();
    const { connections, dataLoaded } = state;
    // FAN 卡片翻转：仅点击触发，默认正面展示主通道子源
    const [fanFlipped, setFanFlipped] = useState(false);

    /**
     * 烈度速报相关子源 / 连接键识别集合。
     * 烈度速报走独立 WS（fan_studio_cenc_ir），不应混入 FAN 主通道正面列表。
     */
    const INTENSITY_SUB_SOURCE_KEYS = useMemo(() => new Set([
        'china_cenc_intensity_report',
        'cenc_ir_fanstudio',
        'cenc_ir',
        'cenc-ir',
        'fan_studio_cenc_ir',
    ]), []);

    const isFanIntensityConnectionKey = useCallback((key) => {
        const k = String(key || '').toLowerCase().trim();
        if (!k) return false;
        if (k.includes('烈度')) return true;
        if (k.includes('cenc_ir') || k.includes('cenc-ir')) return true;
        if (k.includes('intensity') && k.includes('fan')) return true;
        return false;
    }, []);

    const isFanPrimaryConnectionKey = useCallback((key) => {
        const k = String(key || '').toLowerCase().trim();
        if (!k) return false;
        if (!k.includes('fan') || k.includes('eqsc')) return false;
        return !isFanIntensityConnectionKey(k);
    }, [isFanIntensityConnectionKey]);

    /**
     * 将后端连接条目规范为前端展示模型。
     */
    const normalizeConnection = (target, matchedEntries) => {
        let status = 'disabled';
        let statusLabel = null;
        let circuitOpen = false;
        let connectionType = target.connectionType || 'websocket';

        if (matchedEntries.length > 0) {
            // 多条命中时优先选“更像真实运行态”的条目，避免 catalog 占位
            // （status=未连接 / 无 connection_type）盖过 HTTP 通道的正式状态。
            const rankedEntries = matchedEntries.slice().sort((a, b) => {
                const infoA = a[1] || {};
                const infoB = b[1] || {};
                const score = (info) => {
                    let value = 0;
                    const statusText = String(info.status || '');
                    if (info.connection_type === 'http') value += 8;
                    if (statusText && statusText !== '未连接') value += 4;
                    if (Object.prototype.hasOwnProperty.call(info, 'access_token_valid')) value += 2;
                    if (info.connected) value += 1;
                    // 明确降权“未连接”占位，防止它抢到 primary
                    if (statusText === '未连接') value -= 10;
                    return value;
                };
                return score(infoB) - score(infoA);
            });

            const primary = rankedEntries[0][1] || {};
            connectionType = primary.connection_type || connectionType;
            circuitOpen = !!primary.circuit_open;
            if (primary.status) {
                statusLabel = String(primary.status);
            }

            // EQSC：若正式条目里带有 access_token_valid，强制用鉴权语义覆盖“未连接”占位
            if (target.id === 'eqsc') {
                const authEntry = rankedEntries.find(([, info]) =>
                    info && Object.prototype.hasOwnProperty.call(info, 'access_token_valid')
                );
                if (authEntry) {
                    const authInfo = authEntry[1] || {};
                    connectionType = authInfo.connection_type || 'http';
                    circuitOpen = !!authInfo.circuit_open;
                    if (authInfo.status) {
                        statusLabel = String(authInfo.status);
                    } else if (authInfo.access_token_valid) {
                        statusLabel = '可用';
                    } else if (authInfo.enabled) {
                        statusLabel = circuitOpen ? '熔断中' : '鉴权失效';
                    }
                }
            }

            const isEnabled = rankedEntries.some(([, info]) => !!info.enabled);
            if (isEnabled) {
                const isConnected = rankedEntries.some(([, info]) => !!info.connected);
                status = isConnected ? 'online' : 'offline';
            }

            // 后续 sub_sources / latency 也基于排序后的结果合并，避免脏占位优先。
            matchedEntries = rankedEntries;
        }

        const retryCount = matchedEntries.reduce(
            (max, [, info]) => Math.max(max, info.retry_count || 0),
            0
        );

        const allSubSources = {};
        matchedEntries.forEach(([, info]) => {
            if (info.sub_sources) {
                Object.assign(allSubSources, info.sub_sources);
            }
        });

        // EQSC 只展示业务子开关。
        // catalog 占位可能带上 jma_tsunami_eqsc / cenc_ir_eqsc（source_id），需要过滤掉，避免重复条目。
        if (target.id === 'eqsc') {
            const eqscAllowedKeys = new Set([
                'china_typhoon',
                'jma_tsunami',
                'japan_jma_tsunami',
                'china_cenc_intensity_report',
            ]);
            Object.keys(allSubSources).forEach((key) => {
                if (!eqscAllowedKeys.has(key)) {
                    delete allSubSources[key];
                }
            });
        }

        // FAN 主通道正面：剔除烈度速报子源（其属于独立 WS，展示在卡片背面）
        if (target.id === 'fan') {
            Object.keys(allSubSources).forEach((key) => {
                const normalized = String(key || '').trim().toLowerCase();
                if (
                    INTENSITY_SUB_SOURCE_KEYS.has(normalized)
                    || INTENSITY_SUB_SOURCE_KEYS.has(key)
                    || normalized.includes('intensity')
                    || normalized.includes('cenc_ir')
                    || String(key || '').includes('烈度')
                ) {
                    delete allSubSources[key];
                }
            });
        }

        const rawLatency = matchedEntries.length > 0
            ? (matchedEntries[0][1].latency
                ?? matchedEntries[0][1].latency_ms
                ?? matchedEntries[0][1].ping)
            : undefined;
        let latency = undefined;
        if (rawLatency === null) {
            latency = null;
        } else if (rawLatency !== undefined && rawLatency !== '') {
            const normalizedLatency = Number(rawLatency);
            latency = Number.isFinite(normalizedLatency) ? normalizedLatency : null;
        }

        // HTTP 通道（EQSC / S-Net）优先使用后端状态文案（可用 / 轮询中 / 离线 / 未启用）
        if (!statusLabel) {
            if (status === 'online') {
                statusLabel = connectionType === 'http' ? '可用' : '在线';
            } else if (status === 'offline') {
                statusLabel = circuitOpen ? '熔断中' : '离线';
            } else {
                statusLabel = '未启用';
            }
        }

        return {
            id: target.id,
            name: target.displayName,
            status,
            status_label: statusLabel,
            retry_count: retryCount,
            sub_sources: allSubSources,
            latency,
            connection_type: connectionType,
            circuit_open: circuitOpen,
            compact: !!target.compact,
        };
    };

    // 解析过滤 connections 数据
    const displayColumns = useMemo(() => {
        const targets = [
            {
                id: 'fan',
                displayName: 'FAN Studio',
                // 仅匹配主通道；烈度速报独立连接单独解析为 flipSide
                matcher: isFanPrimaryConnectionKey,
            },
            {
                id: 'p2p',
                displayName: 'P2P地震情報',
                matcher: (key) => String(key || '').toLowerCase().includes('p2p'),
                compact: true,
            },
            {
                id: 'snet',
                displayName: 'NIED S-Net',
                connectionType: 'http',
                matcher: (key) => {
                    const k = String(key || '').toLowerCase();
                    return k.includes('s-net') || k.includes('snet') || k.includes('nied');
                },
                compact: true,
            },
            {
                id: 'wolfx',
                displayName: 'Wolfx',
                matcher: (key) => {
                    const k = String(key || '').toLowerCase();
                    return key === 'wolfx_all' || k.includes('wolfx');
                },
            },
            {
                id: 'gq',
                displayName: 'OpenQuakeAPI',
                matcher: (key) => {
                    const k = String(key || '').toLowerCase();
                    return (
                        k.includes('global')
                        || k.includes('openquake')
                        || k === 'gq'
                    ) && !k.includes('eqsc');
                },
                compact: true,
            },
            {
                id: 'eqsc',
                displayName: 'EQSC API',
                connectionType: 'http',
                matcher: (key) => {
                    const k = String(key || '').toLowerCase().trim();
                    // 优先匹配展示名；兼容历史原始键 eqsc，但排除其它误匹配。
                    return k === 'eqsc api' || k === 'eqsc';
                },
                compact: true,
            },
        ];

        const normalized = targets.map((target) => {
            const matchedEntries = Object.entries(connections || {}).filter(([key]) =>
                target.matcher(key)
            );
            return normalizeConnection(target, matchedEntries);
        });

        // 解析 CENC 烈度速报独立连接，挂到 FAN 卡片背面
        const intensityEntries = Object.entries(connections || {}).filter(([key]) =>
            isFanIntensityConnectionKey(key)
        );
        const intensityConn = normalizeConnection(
            {
                id: 'fan_cenc_ir',
                displayName: 'FAN Studio（烈度速报）',
                connectionType: 'websocket',
            },
            intensityEntries
        );

        // 背面若无任何 sub_sources，补一条展示项，避免空白
        if (
            (!intensityConn.sub_sources || Object.keys(intensityConn.sub_sources).length === 0)
            && intensityConn.status !== 'disabled'
        ) {
            intensityConn.sub_sources = {
                cenc_ir_fanstudio: intensityConn.status === 'online' || intensityConn.status === 'offline',
            };
        } else if (
            (!intensityConn.sub_sources || Object.keys(intensityConn.sub_sources).length === 0)
            && intensityEntries.length === 0
        ) {
            // 配置侧可能仍有开关，但连接尚未出现在 payload：给禁用占位
            intensityConn.sub_sources = {
                cenc_ir_fanstudio: false,
            };
        }

        normalized[0] = {
            ...normalized[0],
            flippable: true,
            flipSide: intensityConn,
        };

        // 第 2 列：P2P 上 + S-Net 下；第 4 列：GQ 上 + EQSC 下
        return [
            { type: 'single', items: [normalized[0]] },
            { type: 'stack', items: [normalized[1], normalized[2]] },
            { type: 'single', items: [normalized[3]] },
            { type: 'stack', items: [normalized[4], normalized[5]] },
        ];
    }, [connections, isFanPrimaryConnectionKey, isFanIntensityConnectionKey, INTENSITY_SUB_SOURCE_KEYS]);

    /**
     * 网络延迟区间着色器类映射
     */
    const getLatencyTone = (latency) => {
        if (latency < 150) return 'fast';
        if (latency < 460) return 'medium';
        return 'slow';
    };

    /**
     * 内部子数据源 ID => 中文可读机构对照字典
     */
    const getScopedSourceName = (sourceKey, connectionName) => {
        const rawKey = String(sourceKey || '').trim();
        if (!rawKey) return rawKey;

        const scopedSourceMap = {
            'FAN Studio': {
                china_earthquake_warning: '中国地震预警网 (CEA)',
                china_earthquake_warning_provincial: '中国地震预警网 (省级)',
                taiwan_cwa_earthquake: '台湾中央气象署: 强震即时警报',
                taiwan_cwa_report: '台湾中央气象署: 地震报告',
                china_cenc_earthquake: '中国地震台网 (CENC)',
                china_cenc_intensity_report: '中国地震台网 (CENC) 烈度速报',
                cenc_ir_fanstudio: '中国地震台网 (CENC) 烈度速报',
                usgs_earthquake: '美国地质调查局 (USGS)',
                usa_shakealert: '美国 ShakeAlert 地震预警',
                fssn_cmt: 'FSSN 矩心矩张量解 (CMT)',
                china_weather_alarm: '中国气象局: 气象预警',
                china_tsunami: '自然资源部海啸预警中心',
                china_typhoon: '中国气象局：实时活跃台风',
                typhoon_fanstudio: '中国气象局：实时活跃台风',
                japan_jma_eew: '日本气象厅: 紧急地震速报',
                // source_id 形态（后端 connection group status 使用）
                cea_fanstudio: '中国地震预警网 (CEA)',
                cea_pr_fanstudio: '中国地震预警网 (省级)',
                cwa_fanstudio: '台湾中央气象署: 强震即时警报',
                cwa_fanstudio_report: '台湾中央气象署: 地震报告',
                cenc_fanstudio: '中国地震台网 (CENC)',
                usgs_fanstudio: '美国地质调查局 (USGS)',
                sa_fanstudio: '美国 ShakeAlert 地震预警',
                fssn_cmt_fanstudio: 'FSSN 矩心矩张量解 (CMT)',
                china_weather_fanstudio: '中国气象局: 气象预警',
                china_tsunami_fanstudio: '自然资源部海啸预警中心',
                jma_fanstudio: '日本气象厅: 紧急地震速报',
            },
            'FAN Studio（烈度速报）': {
                china_cenc_intensity_report: '中国地震台网 (CENC) 烈度速报',
                cenc_ir_fanstudio: '中国地震台网 (CENC) 烈度速报',
                cenc_ir: '中国地震台网 (CENC) 烈度速报',
                'cenc-ir': '中国地震台网 (CENC) 烈度速报',
            },
            'P2P地震情報': {
                japan_jma_eew: '日本气象厅: 紧急地震速报',
                japan_jma_earthquake: '日本气象厅: 地震情报',
                japan_jma_tsunami: '日本气象厅: 海啸予报',
            },
            'NIED S-Net': {
                snet_msil: '日本海沟 S-Net 海底震度计',
            },
            Wolfx: {
                japan_jma_eew: '日本气象厅: 紧急地震速报',
                china_cenc_eew: '中国地震预警网 (CEA)',
                taiwan_cwa_eew: '台湾中央气象署: 强震即时警报',
                japan_jma_earthquake: '日本气象厅地震情报',
                china_cenc_earthquake: '中国地震台网地震测定',
            },
            OpenQuakeAPI: {
                global_quake: 'Global Quake',
            },
            'EQSC API': {
                china_typhoon: '中国气象局：实时活跃台风',
                // 与 P2P 子源展示名一致，仅展示一个海啸入口
                jma_tsunami: '日本气象厅: 海啸予报',
                japan_jma_tsunami: '日本气象厅: 海啸予报',
                // EQSC HTTP 轮询的 CENC 烈度速报（与 FAN 独立 WS 并列）
                china_cenc_intensity_report: '中国地震台网 (CENC) 烈度速报',
                cenc_ir_eqsc: '中国地震台网 (CENC) 烈度速报',
            },
        };

        const scopedName = scopedSourceMap[connectionName]?.[rawKey];
        if (scopedName) return scopedName;

        const formattedName = window.formatSourceName
            ? window.formatSourceName(rawKey)
            : rawKey;

        return String(formattedName)
            .replace(/\s+-\s+(Fan|P2P|Wolfx|EQSC|S-Net|SNET)$/i, '')
            .trim();
    };

    /**
     * 渲染延迟行
     */
    const renderLatencyLine = (conn) => {
        if (conn.status === 'disabled') return null;
        return (
            <Typography className={`connection-latency-line ${conn.latency === undefined || conn.latency === null ? 'is-pending' : ''}`}>
                <span className="connection-latency-icon">⏱</span>
                延迟:
                {conn.latency !== undefined && conn.latency !== null ? (
                    <span className={`connection-latency-value connection-latency-value--${getLatencyTone(conn.latency)}`}>
                        {conn.latency.toFixed(0)}ms
                    </span>
                ) : conn.latency === null ? (
                    <span>无法测量</span>
                ) : (
                    <span>测量中...</span>
                )}
            </Typography>
        );
    };

    /**
     * 渲染子数据源列表
     */
    const renderSubSources = (conn, nameOverride) => {
        const displayName = nameOverride || conn.name;
        if (conn.sub_sources && Object.keys(conn.sub_sources).length > 0) {
            return (
                <Box className="connection-sub-source-section">
                    <Box className="connection-sub-source-header">
                        <Typography variant="caption" className="connection-sub-source-title">
                            启用的子数据源详情
                        </Typography>
                        <Typography variant="caption" className="connection-sub-source-count">
                            {Object.values(conn.sub_sources).filter(Boolean).length} / {Object.keys(conn.sub_sources).length}
                        </Typography>
                    </Box>
                    <Box className="connection-sub-source-list">
                        {Object.entries(conn.sub_sources)
                            .sort(([keyA, enabledA], [keyB, enabledB]) => {
                                // EQSC：台风 → 海啸 → CENC 烈度速报；其余仍优先展示已启用项
                                if (conn.name === 'EQSC API' || displayName === 'EQSC API') {
                                    const eqscOrder = {
                                        china_typhoon: 0,
                                        jma_tsunami: 1,
                                        japan_jma_tsunami: 1,
                                        china_cenc_intensity_report: 2,
                                        cenc_ir_eqsc: 2,
                                    };
                                    const orderA = eqscOrder[keyA];
                                    const orderB = eqscOrder[keyB];
                                    if (orderA !== undefined || orderB !== undefined) {
                                        return (orderA ?? 99) - (orderB ?? 99);
                                    }
                                }
                                return enabledA === enabledB ? 0 : enabledA ? -1 : 1;
                            })
                            .map(([key, enabled]) => {
                                const friendlyName = getScopedSourceName(key, displayName);
                                return (
                                    <Box
                                        key={key}
                                        className={`connection-sub-source-item ${enabled ? '' : 'is-disabled'}`}
                                    >
                                        <Box className="connection-sub-source-dot" />
                                        <Typography className="connection-sub-source-name">
                                            {friendlyName}
                                        </Typography>
                                        {!enabled && (
                                            <Typography className="connection-sub-source-off-badge">
                                                OFF
                                            </Typography>
                                        )}
                                    </Box>
                                );
                            })}
                    </Box>
                </Box>
            );
        }

        if (conn.status !== 'disabled') {
            return (
                <Typography variant="caption" className="connection-empty-detail">
                    无详细子数据源信息
                </Typography>
            );
        }
        return null;
    };

    /**
     * 渲染卡片顶栏（标题 / 重试 / 可选翻转按钮 / 状态灯）
     */
    const renderCardHeader = (conn, {
        showFlipButton = false,
        flipTitle = '',
        onFlip = null,
        flipButtonTabbable = true,
    } = {}) => (
        <Box className="connection-card-header">
            <Typography className="connection-title">
                {conn.name}
            </Typography>

            <Box className="connection-status-cluster">
                {conn.retry_count > 0 && conn.status !== 'disabled' && conn.connection_type !== 'http' && (
                    <Typography variant="caption" className="connection-retry-count">
                        重试: {conn.retry_count}
                    </Typography>
                )}
                {conn.circuit_open && conn.status !== 'disabled' && (
                    <Typography variant="caption" className="connection-retry-count">
                        熔断
                    </Typography>
                )}
                {showFlipButton && (
                    <button
                        type="button"
                        className="connection-flip-toggle"
                        onClick={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            if (typeof onFlip === 'function') onFlip();
                        }}
                        title={flipTitle}
                        aria-label={flipTitle}
                        tabIndex={flipButtonTabbable ? 0 : -1}
                        aria-hidden={flipButtonTabbable ? undefined : true}
                    >
                        ⇋
                    </button>
                )}
                <div className="connection-indicator"></div>
            </Box>
        </Box>
    );

    /**
     * 渲染单面内容（不含外层 connection-item 壳）
     */
    const renderCardBody = (conn, options = {}) => (
        <>
            {renderCardHeader(conn, options)}
            <Box className="connection-summary">
                <Typography className="connection-status-label">
                    {conn.status_label}
                </Typography>
                {renderLatencyLine(conn)}
            </Box>
            {options.extraNote ? (
                <Typography variant="caption" className="connection-flip-note">
                    {options.extraNote}
                </Typography>
            ) : null}
            {renderSubSources(conn, options.nameOverride)}
        </>
    );

    /**
     * FAN Studio 可翻转卡片：正面主通道，背面 CENC 烈度速报独立连接
     */
    const renderFanFlipCard = (conn) => {
        const back = conn.flipSide || {
            id: 'fan_cenc_ir',
            name: 'FAN Studio（烈度速报）',
            status: 'disabled',
            status_label: '未启用',
            retry_count: 0,
            sub_sources: { cenc_ir_fanstudio: false },
            latency: undefined,
            connection_type: 'websocket',
            circuit_open: false,
        };

        // 外壳状态色跟随当前可见面，避免翻转后色调与内容不一致。
        // 正/背面各自独立 normalize：主通道只看 fan_studio_all，烈度速报只看 fan_studio_cenc_ir。
        const visibleStatus = fanFlipped ? back.status : conn.status;
        const flipTitle = fanFlipped
            ? '返回 FAN Studio 主通道'
            : '查看 FAN Studio（烈度速报）独立连接';

        const handleFlip = () => setFanFlipped((prev) => !prev);

        return (
            <Box
                key={conn.id || conn.name}
                className={`connection-item connection-item-${visibleStatus} connection-item--flippable`}
            >
                <div className={`connection-flip-inner${fanFlipped ? ' is-flipped' : ''}`}>
                    {/* 正面：FAN 主通道 */}
                    <div className={`connection-flip-face connection-flip-face--front connection-item-${conn.status}`}>
                        {renderCardBody(conn, {
                            showFlipButton: true,
                            flipTitle,
                            onFlip: handleFlip,
                            // 仅当前可见面的 ⇋ 进入 Tab 序，避免隐藏面焦点“消失”
                            flipButtonTabbable: !fanFlipped,
                        })}
                    </div>

                    {/* 背面：FAN Studio（烈度速报）独立 WS */}
                    <div className={`connection-flip-face connection-flip-face--back connection-item-${back.status}`}>
                        {renderCardBody(back, {
                            showFlipButton: true,
                            flipTitle,
                            onFlip: handleFlip,
                            nameOverride: 'FAN Studio（烈度速报）',
                            flipButtonTabbable: fanFlipped,
                        })}
                    </div>
                </div>
            </Box>
        );
    };

    /**
     * 渲染单张连接卡片
     */
    const renderConnectionCard = (conn) => {
        if (conn.flippable && conn.flipSide) {
            return renderFanFlipCard(conn);
        }

        const compactClass = conn.compact ? ' connection-item--compact' : '';
        return (
            <Box
                key={conn.id || conn.name}
                className={`connection-item connection-item-${conn.status}${compactClass}`}
            >
                {renderCardBody(conn)}
            </Box>
        );
    };

    // 骨架屏：第 1/3 列单卡，第 2/4 列双卡堆叠（P2P+S-Net / GQ+EQSC）
    if (!dataLoaded) {
        return (
            <div className="connections-grid status-connections-grid">
                <div className="status-connection-skeleton-card">
                    <div className="status-skeleton-row">
                        <div className="skeleton status-skeleton-title"></div>
                        <div className="skeleton status-skeleton-badge"></div>
                    </div>
                    <div className="skeleton status-skeleton-subtitle"></div>
                    <div className="skeleton status-skeleton-subtitle status-skeleton-subtitle--short"></div>
                </div>
                <div className="connection-stack">
                    {[1, 2].map((i) => (
                        <div key={`stack-p2p-${i}`} className="status-connection-skeleton-card status-connection-skeleton-card--compact">
                            <div className="status-skeleton-row">
                                <div className="skeleton status-skeleton-title"></div>
                                <div className="skeleton status-skeleton-badge"></div>
                            </div>
                            <div className="skeleton status-skeleton-subtitle status-skeleton-subtitle--short"></div>
                        </div>
                    ))}
                </div>
                <div className="status-connection-skeleton-card">
                    <div className="status-skeleton-row">
                        <div className="skeleton status-skeleton-title"></div>
                        <div className="skeleton status-skeleton-badge"></div>
                    </div>
                    <div className="skeleton status-skeleton-subtitle"></div>
                    <div className="skeleton status-skeleton-subtitle status-skeleton-subtitle--short"></div>
                </div>
                <div className="connection-stack">
                    {[1, 2].map((i) => (
                        <div key={`stack-gq-${i}`} className="status-connection-skeleton-card status-connection-skeleton-card--compact">
                            <div className="status-skeleton-row">
                                <div className="skeleton status-skeleton-title"></div>
                                <div className="skeleton status-skeleton-badge"></div>
                            </div>
                            <div className="skeleton status-skeleton-subtitle status-skeleton-subtitle--short"></div>
                        </div>
                    ))}
                </div>
            </div>
        );
    }

    return (
        <div className="connections-grid status-connections-grid">
            {displayColumns.map((column, columnIndex) => {
                if (column.type === 'stack') {
                    return (
                        <div key={`column-stack-${columnIndex}`} className="connection-stack">
                            {column.items.map((conn) => renderConnectionCard(conn))}
                        </div>
                    );
                }
                return column.items.map((conn) => renderConnectionCard(conn));
            })}
        </div>
    );
}
