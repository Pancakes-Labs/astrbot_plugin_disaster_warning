const { Box, Typography } = MaterialUI;
const { useMemo, useState } = React;

/**
 * 连接状态网格组件 (ConnectionsGrid)
 * 显示主流数据源（Jian Project / P2P / Wolfx / PancakesAPI）与 HTTP 辅助通道
 * （EQSC API、NIED S-Net）的实时连接状态、协议信息及子源分布。
 *
 * 布局策略：
 * - 大屏（>= 1024px）：固定 4 列均匀分布
 * - - 第 1 列：Jian Project（可翻转：正面沿用原 FAN Studio 子源顺序，背面展示扩接剩余源）
 * - - 第 2 列：P2P 地震情报 + NIED S-Net 上下堆叠
 * - - 第 3 列：Wolfx
 * - - 第 4 列：PancakesAPI + EQSC API 上下堆叠
 * 延迟评级：
 * - < 150ms  fast (绿色)
 * - < 460ms  medium (黄色)
 * - 其它 评为 slow (红色)
 */
function ConnectionsGrid() {
    const { state } = useAppContext();
    const { connections, dataLoaded } = state;
    // Jian Project 卡片翻转：仅点击触发；正面=原 FAN Studio 顺序，背面=扩接剩余源
    const [jianFlipped, setJianFlipped] = useState(false);

    // 正面顺序对齐历史 FAN Studio 主通道子源顺序
    const JIAN_FRONT_ORDER = [
        'china_earthquake_warning',
        'china_earthquake_warning_provincial',
        'taiwan_cwa_earthquake',
        'taiwan_cwa_report',
        // CWA 报告下方接 JMA 紧急地震速报，其下接震度情报与长周期
        'japan_jma_eew',
        'japan_jma_earthquake',
        'japan_jma_lpgm',
        'china_cenc_earthquake',
        // CENC 下方紧接四个地方局速报
        'yunnan_earthquake',
        'shanxi_earthquake',
        'beijing_earthquake',
        'ningxia_earthquake',
        'usgs_earthquake',
        'usa_shakealert',
        'china_weather_alarm',
        'china_tsunami',
        'china_typhoon',
    ];
    // 背面：其余扩接子源
    const JIAN_BACK_ORDER = [
        'japan_jma_tsunami',
        'emsc_earthquake',
    ];
    const JIAN_KEY_ALIASES = {
        cea_jianproject: 'china_earthquake_warning',
        cea_pr_jianproject: 'china_earthquake_warning_provincial',
        cwa_jianproject: 'taiwan_cwa_earthquake',
        cwa_report_jianproject: 'taiwan_cwa_report',
        cenc_jianproject: 'china_cenc_earthquake',
        usgs_jianproject: 'usgs_earthquake',
        sa_jianproject: 'usa_shakealert',
        china_weather_jianproject: 'china_weather_alarm',
        china_tsunami_jianproject: 'china_tsunami',
        typhoon_jianproject: 'china_typhoon',
        jma_jianproject: 'japan_jma_eew',
        jma_tsunami_jianproject: 'japan_jma_tsunami',
        jma_report_jianproject: 'japan_jma_earthquake',
        jma_lpgm_jianproject: 'japan_jma_lpgm',
        yunnan_jianproject: 'yunnan_earthquake',
        shanxi_jianproject: 'shanxi_earthquake',
        beijing_jianproject: 'beijing_earthquake',
        ningxia_jianproject: 'ningxia_earthquake',
        emsc_jianproject: 'emsc_earthquake',
    };

    const canonicalizeJianKey = (key) => {
        const raw = String(key || '').trim();
        if (!raw) return raw;
        if (JIAN_KEY_ALIASES[raw]) return JIAN_KEY_ALIASES[raw];
        return raw;
    };

    const splitJianSubSources = (subSources) => {
        const entries = Object.entries(subSources || {});
        const front = {};
        const back = {};
        const frontRank = new Map(JIAN_FRONT_ORDER.map((k, i) => [k, i]));
        const backRank = new Map(JIAN_BACK_ORDER.map((k, i) => [k, i]));
        const frontItems = [];
        const backItems = [];
        entries.forEach(([key, enabled]) => {
            const canon = canonicalizeJianKey(key);
            if (frontRank.has(canon)) {
                frontItems.push([key, enabled, frontRank.get(canon)]);
            } else {
                const rank = backRank.has(canon) ? backRank.get(canon) : 1000 + backItems.length;
                backItems.push([key, enabled, rank]);
            }
        });
        frontItems.sort((a, b) => a[2] - b[2]);
        backItems.sort((a, b) => a[2] - b[2]);
        frontItems.forEach(([key, enabled]) => { front[key] = enabled; });
        backItems.forEach(([key, enabled]) => { back[key] = enabled; });
        return { front, back };
    };

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
        let activeServer = undefined;
        matchedEntries.forEach(([, info]) => {
            if (info.sub_sources) {
                Object.assign(allSubSources, info.sub_sources);
            }
            // 提取活跃服务器信息（若有主备标签）
            if (info.active_server) {
                activeServer = info.active_server;
            } else if (info.active_server_label) {
                activeServer = info.active_server_label;
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
            active_server: activeServer,
        };
    };

    // 解析过滤 connections 数据
    const displayColumns = useMemo(() => {
        const targets = [
            {
                id: 'jian_project',
                displayName: 'Jian Project',
                matcher: (key) => {
                    const k = String(key || '').toLowerCase().trim();
                    const normalized = k.replace(/[\s_-]+/g, '');
                    return (
                        normalized === 'jianprojectall'
                        || normalized === 'jianproject'
                        || normalized.includes('jianproject')
                        || k.includes('sismotide')
                    );
                },
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
                displayName: 'PancakesAPI',
                matcher: (key) => {
                    const k = String(key || '').toLowerCase();
                    // PancakesAPI 连接组 key 为 pancakes_api；兼容历史 openquake_api / global_quake / gq
                    return (
                        k === 'pancakes_api'
                        || k === 'openquake_api'
                        || k === 'global_quake'
                        || k === 'gq'
                        || k.includes('pancakes')
                        || k.includes('openquake')
                        || k.includes('global_quake')
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

        // 按 id 建立展示模型索引：列布局直接按 id 取用，
        // 避免与 targets 数组下标耦合（新增数据源时下标漂移会静默丢卡片）。
        const connById = {};
        normalized.forEach((conn) => {
            connById[conn.id] = conn;
        });

        // Jian Project：按 FAN Studio 顺序拆成正/背面
        const jianConn = connById.jian_project;
        if (jianConn) {
            const { front, back } = splitJianSubSources(jianConn.sub_sources);
            const backConn = {
                ...jianConn,
                id: 'jian_project_extra',
                name: 'Jian Project（扩展子源）',
                sub_sources: back,
            };
            connById.jian_project = {
                ...jianConn,
                sub_sources: front,
                flippable: true,
                flipSide: backConn,
            };
        }

        // 第 1 列 Jian Project（主源，可翻转）；第 2 列 P2P + S-Net；
        // 第 3 列 Wolfx；第 4 列 PancakesAPI + EQSC
        return [
            { type: 'single', items: [connById.jian_project] },
            { type: 'stack', items: [connById.p2p, connById.snet] },
            { type: 'single', items: [connById.wolfx] },
            { type: 'stack', items: [connById.gq, connById.eqsc] },
        ];
    }, [connections]);

    /**
     * 网络延迟区间着色器类映射
     */
    const getLatencyTone = (latency) => {
        if (latency < 150) return 'fast';
        if (latency < 460) return 'medium';
        return 'slow';
    };

    /**
     * 内部子数据源 ID => 中文可读机构对照字典（子源列表场景投影）。
     * 注意：这是连接面板子源明细的专用展示口径，与后端命令文本存在既有差异，属有意设计，
     * 修改展示名时请同时核对 display_registry.py 与 plugin_admin_command_service.py 的投影表。
     */
    const getScopedSourceName = (sourceKey, connectionName) => {
        const rawKey = String(sourceKey || '').trim();
        if (!rawKey) return rawKey;

        const scopedSourceMap = {
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
            PancakesAPI: {
                global_quake: 'Global Quake',
                japan_jma_eew: '日本气象厅: 紧急地震速报',
                jma_pancakes: '日本气象厅: 紧急地震速报',
                japan_jma_earthquake: '日本气象厅: 地震情报',
                jma_eqlist_pancakes: '日本气象厅: 地震情报',
                usgs_earthquake: '美国地质调查局 (USGS)',
                usgs_pancakes: '美国地质调查局 (USGS)',
            },
            OpenQuakeAPI: {
                global_quake: 'Global Quake',
                japan_jma_eew: '日本气象厅: 紧急地震速报',
                jma_pancakes: '日本气象厅: 紧急地震速报',
                japan_jma_earthquake: '日本气象厅: 地震情报',
                jma_eqlist_pancakes: '日本气象厅: 地震情报',
                usgs_earthquake: '美国地质调查局 (USGS)',
                usgs_pancakes: '美国地质调查局 (USGS)',
            },
            'EQSC API': {
                china_typhoon: '中国气象局：实时活跃台风',
                // 与 P2P 子源展示名一致，仅展示一个海啸入口
                jma_tsunami: '日本气象厅: 海啸予报',
                japan_jma_tsunami: '日本气象厅: 海啸予报',
                // EQSC HTTP 轮询的 CENC 烈度速报
                china_cenc_intensity_report: '中国地震台网 (CENC) 烈度速报',
                cenc_ir_eqsc: '中国地震台网 (CENC) 烈度速报',
            },
            'Jian Project': {
                // 正面：对齐历史 FAN Studio 主通道顺序
                china_earthquake_warning: '中国地震预警网 (CEA)',
                china_earthquake_warning_provincial: '中国地震预警网 (省级)',
                taiwan_cwa_earthquake: '台湾中央气象署: 强震即时警报',
                taiwan_cwa_report: '台湾中央气象署: 地震报告',
                japan_jma_eew: '日本气象厅: 紧急地震速报',
                japan_jma_earthquake: '日本气象厅: 震源·震度情报',
                japan_jma_lpgm: '日本气象厅: 长周期地震动',
                china_cenc_earthquake: '中国地震台网 (CENC)',
                yunnan_earthquake: '云南地震局速报',
                shanxi_earthquake: '山西地震局速报',
                beijing_earthquake: '北京地震局速报',
                ningxia_earthquake: '宁夏地震局速报',
                usgs_earthquake: '美国地质调查局 (USGS)',
                usa_shakealert: '美国 ShakeAlert 地震预警',
                china_weather_alarm: '中国气象局: 气象预警',
                china_tsunami: '自然资源部海啸预警中心',
                china_typhoon: '中国气象局：实时活跃台风',
                // 背面：扩接剩余源
                japan_jma_tsunami: '日本气象厅: 海啸情报',
                emsc_earthquake: '欧洲地中海地震中心 (EMSC)',
                // source_id 形态
                cea_jianproject: '中国地震预警网 (CEA)',
                cea_pr_jianproject: '中国地震预警网 (省级)',
                cwa_jianproject: '台湾中央气象署: 强震即时警报',
                cwa_report_jianproject: '台湾中央气象署: 地震报告',
                cenc_jianproject: '中国地震台网 (CENC)',
                usgs_jianproject: '美国地质调查局 (USGS)',
                sa_jianproject: '美国 ShakeAlert 地震预警',
                china_weather_jianproject: '中国气象局: 气象预警',
                china_tsunami_jianproject: '自然资源部海啸预警中心',
                typhoon_jianproject: '中国气象局：实时活跃台风',
                jma_jianproject: '日本气象厅: 紧急地震速报',
                jma_tsunami_jianproject: '日本气象厅: 海啸情报',
                yunnan_jianproject: '云南地震局速报',
                shanxi_jianproject: '山西地震局速报',
                beijing_jianproject: '北京地震局速报',
                ningxia_jianproject: '宁夏地震局速报',
                jma_report_jianproject: '日本气象厅: 震源·震度情报',
                jma_lpgm_jianproject: '日本气象厅: 长周期地震动',
                emsc_jianproject: '欧洲地中海地震中心 (EMSC)',
            },
            'Jian Project（扩展子源）': {
                japan_jma_tsunami: '日本气象厅: 海啸情报',
                emsc_earthquake: '欧洲地中海地震中心 (EMSC)',
                jma_tsunami_jianproject: '日本气象厅: 海啸情报',
                emsc_jianproject: '欧洲地中海地震中心 (EMSC)',
            },

        };

        const scopedName = scopedSourceMap[connectionName]?.[rawKey];
        if (scopedName) return scopedName;

        const formattedName = window.formatSourceName
            ? window.formatSourceName(rawKey)
            : rawKey;

        return String(formattedName)
            .replace(/\s+-\s+(Jian|P2P|Wolfx|EQSC|S-Net|SNET)$/i, '')
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
                                // Jian Project：正面按 FAN Studio 顺序，背面按扩接顺序
                                if (
                                    conn.name === 'Jian Project'
                                    || displayName === 'Jian Project'
                                    || conn.name === 'Jian Project（扩展子源）'
                                    || displayName === 'Jian Project（扩展子源）'
                                ) {
                                    const order = (
                                        displayName === 'Jian Project（扩展子源）'
                                        || conn.name === 'Jian Project（扩展子源）'
                                    ) ? JIAN_BACK_ORDER : JIAN_FRONT_ORDER;
                                    const rank = new Map(order.map((k, i) => [k, i]));
                                    const a = rank.get(canonicalizeJianKey(keyA));
                                    const b = rank.get(canonicalizeJianKey(keyB));
                                    if (a !== undefined || b !== undefined) {
                                        return (a ?? 999) - (b ?? 999);
                                    }
                                }
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
    const renderServerBadge = (conn) => {
        // 仅在仍带主备标签的连接上显示
        if (!conn.name) return null;
        if (!conn.active_server && !conn.active_server_label) return null;
        const activeServer = conn.active_server || conn.active_server_label;
        if (!activeServer) return null;
        const isPrimary = activeServer === 'primary' || activeServer === '主服务器';
        const badgeClass = isPrimary ? 'server-badge server-badge--primary' : 'server-badge server-badge--backup';
        const badgeLabel = isPrimary ? '主服务器' : '备用服务器';
        return (
            <Typography variant="caption" className={badgeClass}>
                {badgeLabel}
            </Typography>
        );
    };

    const renderCardHeader = (conn, {
        showFlipButton = false,
        flipTitle = '',
        onFlip = null,
        flipButtonTabbable = true,
    } = {}) => (
        <Box className="connection-card-header">
            <Typography className="connection-title">
                {conn.name}
                {renderServerBadge(conn)}
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
     * Jian Project 可翻转卡片：正面=原 FAN Studio 顺序，背面=扩接剩余子源
     */
    const renderJianFlipCard = (conn) => {
        const back = conn.flipSide || {
            id: 'jian_project_extra',
            name: 'Jian Project（扩展子源）',
            status: conn.status,
            status_label: conn.status_label,
            retry_count: 0,
            sub_sources: {},
            latency: conn.latency,
            connection_type: conn.connection_type,
            circuit_open: conn.circuit_open,
            active_server: conn.active_server,
        };
        const visibleStatus = jianFlipped ? back.status : conn.status;
        const flipTitle = jianFlipped
            ? '返回 Jian Project 主通道子源'
            : '查看扩接剩余子源';
        const handleFlip = () => setJianFlipped((prev) => !prev);

        return (
            <Box
                key={conn.id || conn.name}
                className={`connection-item connection-item-${visibleStatus} connection-item--flippable`}
            >
                <div className={`connection-flip-inner${jianFlipped ? ' is-flipped' : ''}`}>
                    <div className={`connection-flip-face connection-flip-face--front connection-item-${conn.status}`}>
                        {renderCardBody(conn, {
                            showFlipButton: true,
                            flipTitle,
                            onFlip: handleFlip,
                            flipButtonTabbable: !jianFlipped,
                        })}
                    </div>
                    <div className={`connection-flip-face connection-flip-face--back connection-item-${back.status}`}>
                        {renderCardBody(back, {
                            showFlipButton: true,
                            flipTitle,
                            onFlip: handleFlip,
                            flipButtonTabbable: jianFlipped,
                            nameOverride: 'Jian Project（扩展子源）',
                            extraNote: '背面展示扩接子源',
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
        if (conn && conn.flippable) {
            return renderJianFlipCard(conn);
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

    // 骨架屏：第 1/3 列单卡，第 2/4 列双卡堆叠（P2P+S-Net / PancakesAPI+EQSC）
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
