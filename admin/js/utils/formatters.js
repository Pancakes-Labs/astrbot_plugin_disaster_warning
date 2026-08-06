const JST_SOURCE_KEYWORDS = ['jma', 'p2p', 'wolfx_jma', 'japan'];

/**
 * 数据源展示元数据（唯一事实源）缓存。
 * 由后端 /api/sources/meta 动态拉取，数据源别名与展示名的完整映射
 * 只维护在后端 core/sources/display_registry.py（SOURCE_ALIAS_MAP /
 * SOURCE_DISPLAY_MAP），前端不再复制完整映射表，新增/改名数据源时只需改后端一处。
 *
 * 前端仅保留极小的 SOURCE_DISPLAY_OVERRIDES 场景投影覆盖表：
 * 子源列表口径有意剥掉通道后缀、以及后端未注册的前端专属文案，
 * 属于既有展示设计，勿与后端强行对齐。
 */
let __SOURCE_DISPLAY_META__ = null;
let __SOURCE_DISPLAY_META_LOADING__ = false;

/**
 * 从后端拉取数据源展示元数据（幂等）。
 * 失败时静默置空；展示名/别名会回退为原始输入，不影响功能。
 * 首次调用后缓存结果；并发调用会被 loading 标志去重。
 * @returns {Promise<object>}
 */
function loadSourceDisplayMeta() {
    if (__SOURCE_DISPLAY_META__ !== null || __SOURCE_DISPLAY_META_LOADING__) {
        return Promise.resolve(__SOURCE_DISPLAY_META__);
    }
    __SOURCE_DISPLAY_META_LOADING__ = true;
    return fetch('/api/sources/meta', { headers: { 'Accept': 'application/json' } })
        .then((resp) => (resp && resp.ok) ? resp.json() : {})
        .then((data) => {
            __SOURCE_DISPLAY_META__ = (data && typeof data === 'object') ? data : {};
            return __SOURCE_DISPLAY_META__;
        })
        .catch(() => {
            __SOURCE_DISPLAY_META__ = {};
            return __SOURCE_DISPLAY_META__;
        })
        .finally(() => {
            __SOURCE_DISPLAY_META_LOADING__ = false;
        });
}

/**
 * 确保展示元数据已触发加载（懒加载兜底）。
 * 页面加载时的预加载通常已就绪；若因网络抖动未完成，组件渲染时
 * 调用 formatSourceName / normalizeSourceName 会再次触发加载，
 * 避免整个会话都回退显示原始 key。
 */
function ensureSourceDisplayMetaLoaded() {
    if (__SOURCE_DISPLAY_META__ === null && !__SOURCE_DISPLAY_META_LOADING__) {
        loadSourceDisplayMeta();
    }
}

// 页面加载时预热一次：管理端与后端同源部署，meta 为本地小体积接口，
// 通常在首帧数据（status/events）返回前完成，供 formatSourceName /
// normalizeSourceName 使用。
if (typeof window !== 'undefined' && typeof fetch === 'function') {
    window.__loadSourceDisplayMeta = loadSourceDisplayMeta;
    loadSourceDisplayMeta();
}

/**
 * 判断数据源是否属于 UTC+9 (JST) 体系
 * @param {string} source - 数据源标识
 * @returns {boolean}
 */
function isLikelyJstSource(source = '') {
    const sourceKey = String(source || '').toLowerCase();
    if (!sourceKey) return false;
    return JST_SOURCE_KEYWORDS.some(keyword => sourceKey.includes(keyword));
}

/**
 * 统一解析事件时间，返回标准 Date 对象
 * - 优先遵循字符串内自带时区信息 (Z / +09:00 等)
 * - 对无时区的时间字符串按数据源兜底：JST 源按 UTC+9，其他按 UTC+8
 * @param {string|number|Date} rawTime - 原始时间
 * @param {string} sourceHint - 数据源标识，用于无时区时推断
 * @returns {Date|null}
 */
function parseEventTimeToDate(rawTime, sourceHint = '') {
    if (rawTime === null || rawTime === undefined || rawTime === '') return null;

    if (rawTime instanceof Date) {
        return Number.isNaN(rawTime.getTime()) ? null : new Date(rawTime.getTime());
    }

    if (typeof rawTime === 'number') {
        const dateFromTs = new Date(rawTime);
        return Number.isNaN(dateFromTs.getTime()) ? null : dateFromTs;
    }

    const raw = String(rawTime).trim();
    if (!raw) return null;

    // 已携带时区：直接按标准时间解析
    if (/([zZ]|[+\-]\d{2}:?\d{2})$/.test(raw)) {
        const directDate = new Date(raw);
        return Number.isNaN(directDate.getTime()) ? null : directDate;
    }

    // 尝试解析不带时区的标准日期时间
    const normalized = raw.replace(' ', 'T');
    const match = normalized.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/);
    if (match) {
        const [, y, m, d, hh, mm, ss = '0', ms = '0'] = match;
        const offsetHours = isLikelyJstSource(sourceHint) ? 9 : 8;
        const utcMs = Date.UTC(
            Number(y),
            Number(m) - 1,
            Number(d),
            Number(hh) - offsetHours,
            Number(mm),
            Number(ss),
            Number(ms.padEnd(3, '0'))
        );
        const parsed = new Date(utcMs);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }

    // 兜底走浏览器原生解析
    const fallbackDate = new Date(raw);
    return Number.isNaN(fallbackDate.getTime()) ? null : fallbackDate;
}

/**
 * 格式化时间为友好显示字符串（如"刚刚"、"xx分钟前"）
 * @param {string} isoString - ISO 8601 格式的时间字符串
 * @param {string} timeZone - 目标时区 (例如: 'UTC+8', 'Asia/Shanghai')
 * @param {string} sourceHint - 数据源标识，用于无时区时间解析
 * @returns {string} 格式化后的时间字符串
 */
function formatTimeFriendly(isoString, timeZone = 'UTC+8', sourceHint = '') {
    if (!isoString) return '--';
    const date = parseEventTimeToDate(isoString, sourceHint);
    if (!date) return '--';

    const diffMs = Date.now() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);

    if (diffMins < 1) return '刚刚';
    if (diffMins < 60) return `${diffMins}分钟前`;

    return formatTimeWithZone(isoString, timeZone, false, sourceHint);
}

/**
 * 将时间字符串格式化为指定时区的时间
 * @param {string} isoString - ISO 8601 时间字符串
 * @param {string} timeZone - 目标时区 (例如: 'UTC+8', 'Asia/Shanghai')
 * @param {boolean} includeYear - 是否包含年份
 * @param {string} sourceHint - 数据源标识，用于无时区时间解析
 * @returns {string} 格式化后的时间字符串 (e.g., "02-13 14:30")
 */
function formatTimeWithZone(isoString, timeZone = 'UTC+8', includeYear = false, sourceHint = '') {
    if (!isoString) return '--';
    try {
        const date = parseEventTimeToDate(isoString, sourceHint);
        if (!date) return '--';

        // 处理 UTC+X / UTC-X 格式
        if (timeZone.toUpperCase().startsWith('UTC')) {
            const offsetStr = timeZone.substring(3);
            const offsetHours = parseFloat(offsetStr);
            if (!isNaN(offsetHours)) {
                const targetTime = new Date(date.getTime() + (3600000 * offsetHours));

                const month = (targetTime.getUTCMonth() + 1).toString().padStart(2, '0');
                const day = targetTime.getUTCDate().toString().padStart(2, '0');
                const hours = targetTime.getUTCHours().toString().padStart(2, '0');
                const mins = targetTime.getUTCMinutes().toString().padStart(2, '0');

                if (includeYear) {
                     return `${targetTime.getUTCFullYear()}-${month}-${day} ${hours}:${mins}`;
                }
                return `${month}-${day} ${hours}:${mins}`;
            }
        }

        // 使用 Intl.DateTimeFormat 处理 IANA 时区 (Asia/Shanghai 等)
        const options = {
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
            timeZone: timeZone
        };

        if (includeYear) {
            options.year = 'numeric';
        }

        const formatter = new Intl.DateTimeFormat('zh-CN', options);
        const parts = formatter.formatToParts(date);

        let y, m, d, h, min;
        parts.forEach(({ type, value }) => {
            if (type === 'year') y = value;
            if (type === 'month') m = value;
            if (type === 'day') d = value;
            if (type === 'hour') h = value;
            if (type === 'minute') min = value;
        });

        if (includeYear) {
            return `${y}-${m}-${d} ${h}:${min}`;
        }
        return `${m}-${d} ${h}:${min}`;

    } catch (e) {
        console.error('Time formatting error:', e);
        return isoString; // Fallback
    }
}

/**
 * 根据震级获取对应的 CSS 类名
 * @param {number} mag - 地震震级
 * @returns {string} CSS 类名
 */
function getMagColorClass(mag) {
    if (mag >= 7) return 'mag-high';
    if (mag >= 5) return 'mag-medium';
    return 'mag-low';
}

/**
 * 根据震级获取对应的颜色值（Hex）
 * @param {number} mag - 地震震级
 * @returns {string} 颜色 Hex 值
 */
function getMagnitudeColor(mag) {
    if (mag >= 7) return '#ef4444';
    if (mag >= 5) return '#f97316';
    if (mag >= 3) return '#eab308';
    return '#3b82f6';
}

/**
 * 根据气象预警描述获取对应的颜色类名（解析红色、橙色、黄色、蓝色等关键字）
 * @param {string} description - 预警描述文本
 * @returns {string} CSS 类名
 */
function getWeatherColorClass(description) {
    if (!description) return 'weather-blue';
    if (description.includes('红色')) return 'weather-red';
    if (description.includes('橙色')) return 'weather-orange';
    if (description.includes('黄色')) return 'weather-yellow';
    return 'weather-blue';
}

/**
 * 统一规范化后端重构后的数据源标识
 * 唯一事实源：后端 /api/sources/meta 的 source_alias_map
 * （对应 core/sources/display_registry.py 的 SOURCE_ALIAS_MAP）。
 * meta 未就绪或拉取失败时回退为小写标准形态，与历史行为一致。
 * @param {string} source
 * @returns {string}
 */
function normalizeSourceName(source) {
    ensureSourceDisplayMetaLoaded();
    if (!source) return 'unknown';

    const rawSource = String(source).trim();
    if (!rawSource) return 'unknown';

    const lowerSource = rawSource.toLowerCase();
    const metaAliasMap = (__SOURCE_DISPLAY_META__ && __SOURCE_DISPLAY_META__.source_alias_map) || {};
    return metaAliasMap[rawSource] || metaAliasMap[lowerSource] || lowerSource;
}

/**
 * 从完整 UMO 中截取尾部 session_id。
 * 例：aiocqhttp:GroupMessage:123456 -> 123456
 * @param {string} umo - 统一消息来源标识
 * @returns {string}
 */
function formatSessionIdFromUmo(umo = '') {
    const raw = String(umo || '').trim();
    if (!raw) return '';

    const knownTypes = ['FriendMessage', 'GroupMessage', 'PrivateMessage', 'GuildMessage'];
    for (const msgType of knownTypes) {
        const marker = `:${msgType}:`;
        const idx = raw.indexOf(marker);
        if (idx !== -1) {
            const sessionId = raw.slice(idx + marker.length).trim();
            return sessionId || raw;
        }
    }

    const parts = raw.split(':');
    if (parts.length >= 3) {
        return (parts[parts.length - 1] || '').trim() || raw;
    }
    return raw;
}

/**
 * 将会话 UMO 格式化为短展示名。
 * 优先使用 session_id；若存在备注名则附加括号。
 * @param {string|Object} sessionOrUmo - UMO 字符串，或含 session/session_id/session_name 的对象
 * @returns {string}
 */
function formatSessionDisplayLabel(sessionOrUmo) {
    if (sessionOrUmo && typeof sessionOrUmo === 'object') {
        const sessionId = String(
            sessionOrUmo.session_id
            || sessionOrUmo.sessionId
            || formatSessionIdFromUmo(sessionOrUmo.session || '')
            || sessionOrUmo.session
            || ''
        ).trim();
        const sessionName = String(
            sessionOrUmo.session_name || sessionOrUmo.sessionName || ''
        ).trim();
        if (!sessionId) {
            return sessionName || '未知会话';
        }
        return sessionName ? `${sessionId} (${sessionName})` : sessionId;
    }

    return formatSessionIdFromUmo(sessionOrUmo) || String(sessionOrUmo || '').trim() || '未知会话';
}

/**
 * 数据源展示名场景投影覆盖表（唯一真源之外的少量有意差异）。
 * 前端仅在此覆盖个别场景展示口径：
 * - cenc_ir_fanstudio：子源列表展示口径有意剥掉通道后缀 "- Fan"，
 *   与后端完整通道名存在既有差异，勿强行对齐；
 * - snet_msil：后端 SOURCE_DISPLAY_MAP 未注册该键，保留前端既有文案，
 *   避免回退显示原始内部 key。
 * 其余 key 全部由 meta 提供，新增/改名数据源只需改后端一处。
 */
const SOURCE_DISPLAY_OVERRIDES = {
    'cenc_ir_fanstudio': '中国地震台网 (CENC) - 烈度速报',
    'snet_msil': '日本海沟 S-Net 海底震度计',
};

/**
 * 将数据源代码转换为用户友好的显示名称
 * 唯一事实源：后端 /api/sources/meta 的 source_display_map；
 * 场景投影覆盖见 SOURCE_DISPLAY_OVERRIDES。
 * @param {string} source - 数据源代码
 * @returns {string} 友好的中文名称
 */
function formatSourceName(source) {
    ensureSourceDisplayMetaLoaded();
    const normalizedSource = normalizeSourceName(source);
    const metaDisplayMap = (__SOURCE_DISPLAY_META__ && __SOURCE_DISPLAY_META__.source_display_map) || {};
    return SOURCE_DISPLAY_OVERRIDES[normalizedSource]
        || metaDisplayMap[normalizedSource]
        || String(source || '').trim() || '未知来源';
}

/**
 * 解析台风数据形态标签。
 * @param {string|Object} infoTypeOrEvent - info_type 或事件对象
 * @returns {'fan'|'enriched'|'eqsc_rebuild'|''}
 */
function resolveTyphoonDataMode(infoTypeOrEvent) {
    let raw = '';
    if (infoTypeOrEvent && typeof infoTypeOrEvent === 'object') {
        // 后端已注入 data_mode 时直接使用，避免前端重复解析
        if (infoTypeOrEvent.data_mode) {
            return String(infoTypeOrEvent.data_mode);
        }
        raw = String(
            infoTypeOrEvent.info_type
            || infoTypeOrEvent.infoType
            || infoTypeOrEvent.data_source
            || infoTypeOrEvent.dataSource
            || ''
        ).trim();
    } else {
        raw = String(infoTypeOrEvent || '').trim();
    }

    const key = raw.toLowerCase();
    if (!key) return '';
    if (['enriched', 'fan+eqsc', 'fan_eqsc', 'eqsc_enriched'].includes(key)) {
        return 'enriched';
    }
    if (['eqsc_rebuild', 'eqsc', 'eqsc_history', 'history_rebuild', 'rebuild'].includes(key)) {
        return 'eqsc_rebuild';
    }
    if (['fan', 'fan_studio', 'fanstudio'].includes(key)) {
        return 'fan';
    }
    return '';
}

/**
 * 台风来源展示名：统一 source_id=typhoon_fanstudio，按数据形态追加后缀。
 * @param {string} source
 * @param {string|Object} [infoTypeOrEvent]
 * @returns {string}
 */
function formatTyphoonSourceName(source, infoTypeOrEvent) {
    // 事件详情按数据形态细分；贡献榜请用 formatSourceName（中性实时名）
    const normalized = normalizeSourceName(source || 'typhoon_fanstudio');
    const mode = resolveTyphoonDataMode(infoTypeOrEvent);
    if (mode === 'enriched') {
        return '中国气象局：实时活跃台风 - Fan+EQSC';
    }
    if (mode === 'eqsc_rebuild' || normalized === 'typhoon_eqsc_rebuild') {
        return '中国气象局：台风历史 - EQSC';
    }
    // fan 或缺省：事件详情仍标明 Fan 触发
    return '中国气象局：实时活跃台风 - Fan';
}

/**
 * 事件级来源展示名（台风会按 info_type 细分数据形态）。
 * @param {Object|string} eventOrSource
 * @returns {string}
 */
function formatEventSourceName(eventOrSource) {
    if (eventOrSource && typeof eventOrSource === 'object') {
        // 后端已注入 source_label 时直接使用，避免前端重复解析 mode
        if (eventOrSource.source_label) {
            return String(eventOrSource.source_label);
        }
        const source = eventOrSource.source_id || eventOrSource.source || '';
        const normalized = normalizeSourceName(source);
        const type = String(eventOrSource.type || eventOrSource.event_type || '').toLowerCase();
        if (
            normalized === 'typhoon_fanstudio'
            || normalized === 'typhoon_eqsc_rebuild'
            || type === 'typhoon'
        ) {
            return formatTyphoonSourceName(source || 'typhoon_fanstudio', eventOrSource);
        }
        return formatSourceName(source);
    }
    return formatSourceName(eventOrSource);
}
