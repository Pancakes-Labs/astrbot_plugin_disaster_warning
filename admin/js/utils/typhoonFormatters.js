/**
 * 台风前端展示工具。
 * 统一 emoji、色阶、风速阈值与风圈格式化，避免卡片组件硬编码业务阈值。
 */

(function initTyphoonFormatters(global) {
    const TYPHOON_LEVEL_EMOJI = {
        热带低压: '🔵',
        热带风暴: '🟢',
        强热带风暴: '🟡',
        台风: '🟠',
        强台风: '🔴',
        超强台风: '🟣',
    };

    // 与后端等级序一致：从高到低匹配，避免“强台风”被“台风”抢先命中
    const LEVEL_MATCH_ORDER = [
        '超强台风',
        '强台风',
        '强热带风暴',
        '热带风暴',
        '热带低压',
        '台风',
    ];

    const LEVEL_COLORS = {
        超强台风: 'var(--typhoon-level-color-super, #B71C1C)',
        强台风: 'var(--typhoon-level-color-severe, #E53935)',
        台风: 'var(--typhoon-level-color-typhoon, #FB8C00)',
        强热带风暴: 'var(--typhoon-level-color-sts, #FDD835)',
        热带风暴: 'var(--typhoon-level-color-ts, #43A047)',
        热带低压: 'var(--typhoon-level-color-td, #00ACC1)',
        default: 'var(--typhoon-level-color-default, #78909C)',
    };

    // 风速阈值 (m/s) 对应强度色阶，与气象业务阈值对齐
    const WIND_SPEED_THRESHOLDS = [
        { min: 51.0, color: 'var(--typhoon-wind-color-super, #B71C1C)', label: '超强台风' },
        { min: 41.5, color: 'var(--typhoon-wind-color-severe, #E53935)', label: '强台风' },
        { min: 32.7, color: 'var(--typhoon-wind-color-typhoon, #FB8C00)', label: '台风' },
        { min: 24.5, color: 'var(--typhoon-wind-color-sts, #FDD835)', label: '强热带风暴' },
        { min: 17.2, color: 'var(--typhoon-wind-color-ts, #43A047)', label: '热带风暴' },
        { min: 10.8, color: 'var(--typhoon-wind-color-td, #00ACC1)', label: '热带低压' },
    ];
    // 事件列表筛选用历史峰值风速选项（含“全部”）
    // 注意：筛的是主表历史最大风速，不是卡片当前观测风速。
    const WIND_SPEED_FILTER_OPTIONS = [
        { value: 'all', label: '全部峰值风速' },
        ...WIND_SPEED_THRESHOLDS
            .slice()
            .reverse()
            .map((item) => ({
                value: String(item.min),
                label: `峰值 ≥ ${Number(item.min).toFixed(1)} m/s`,
            })),
    ];
    // 事件列表筛选用历史最低中心气压选项（气压越低通常越强）
    // 注意：筛的是主表历史最低气压，不是卡片当前观测气压。
    const PRESSURE_FILTER_OPTIONS = [
        { value: 'all', label: '全部峰值气压' },
        { value: '1000', label: '历史最低 ≤ 1000 hPa' },
        { value: '990', label: '历史最低 ≤ 990 hPa' },
        { value: '980', label: '历史最低 ≤ 980 hPa' },
        { value: '970', label: '历史最低 ≤ 970 hPa' },
        { value: '960', label: '历史最低 ≤ 960 hPa' },
        { value: '950', label: '历史最低 ≤ 950 hPa' },
        { value: '940', label: '历史最低 ≤ 940 hPa' },
        { value: '920', label: '历史最低 ≤ 920 hPa' },
    ];
    const WIND_SPEED_DEFAULT_COLOR = 'var(--typhoon-wind-color-default, #78909C)';

    // 台风强度等级筛选选项（与后端 DatabaseManager._append_level_filter_clause 对齐）
    const TYPHOON_LEVEL_FILTER_OPTIONS = [
        { value: 'typhoon_tropical_depression', label: '热带低压' },
        { value: 'typhoon_tropical_storm', label: '热带风暴' },
        { value: 'typhoon_severe_tropical_storm', label: '强热带风暴' },
        { value: 'typhoon', label: '台风' },
        { value: 'typhoon_severe_typhoon', label: '强台风' },
        { value: 'typhoon_super_typhoon', label: '超强台风' },
    ];

    const WIND_CIRCLE_LABELS = {
        '30KTS': '7级风圈',
        '50KTS': '10级风圈',
        '64KTS': '12级风圈',
    };
    const WIND_QUADRANT_LABELS = {
        NE: '东北',
        SE: '东南',
        SW: '西南',
        NW: '西北',
    };

    function matchLevelKey(level) {
        const text = String(level || '').trim();
        if (!text) return '';
        if (TYPHOON_LEVEL_EMOJI[text]) return text;
        for (const key of LEVEL_MATCH_ORDER) {
            if (text.includes(key) || (key === '超强台风' && text.includes('超强'))) {
                return key;
            }
        }
        return '';
    }

    function getTyphoonLevelEmoji(level) {
        const key = matchLevelKey(level);
        return key ? (TYPHOON_LEVEL_EMOJI[key] || '') : '';
    }

    function getTyphoonLevelColor(level) {
        const key = matchLevelKey(level);
        if (!key) return LEVEL_COLORS.default;
        return LEVEL_COLORS[key] || LEVEL_COLORS.default;
    }

    function getTyphoonWindColor(windSpeed) {
        const value = Number(windSpeed);
        if (!Number.isFinite(value)) return WIND_SPEED_DEFAULT_COLOR;
        for (const item of WIND_SPEED_THRESHOLDS) {
            if (value >= item.min) return item.color;
        }
        return WIND_SPEED_DEFAULT_COLOR;
    }

    function formatTyphoonWindCircle(windCircle) {
        if (!windCircle || typeof windCircle !== 'object') return [];
        const rows = [];
        Object.keys(WIND_CIRCLE_LABELS).forEach((circleKey) => {
            const circle = windCircle[circleKey];
            if (!circle || typeof circle !== 'object') return;
            const parts = [];
            Object.keys(WIND_QUADRANT_LABELS).forEach((q) => {
                const radius = circle[q];
                if (radius == null || radius === '' || Number(radius) <= 0) return;
                parts.push(`${WIND_QUADRANT_LABELS[q]}${radius}km`);
            });
            if (parts.length > 0) {
                rows.push(`${WIND_CIRCLE_LABELS[circleKey]}：${parts.join(' / ')}`);
            }
        });
        return rows;
    }

    function formatTyphoonCoords(lat, lon) {
        if (lat == null || lon == null || Number.isNaN(Number(lat)) || Number.isNaN(Number(lon))) {
            return '';
        }
        const latNum = Number(lat);
        const lonNum = Number(lon);
        const latDir = latNum >= 0 ? 'N' : 'S';
        const lonDir = lonNum >= 0 ? 'E' : 'W';
        return `${Math.abs(latNum).toFixed(1)}°${latDir}, ${Math.abs(lonNum).toFixed(1)}°${lonDir}`;
    }

    /**
     * 统一输出台风短编号（优先 4 位 EQSC 形态，如 2609）。
     * 兼容 6 位 Fan 编号、NAMELESS 无名低压，以及 eqsc_id / typhoon_id / real_event_id 等字段。
     *
     * 注意：NAMELESS_2604 不能再抽成 2604，否则会与正式编号冲突。
     *
     * 重要：event_id 是数据库自增主键，并非台风业务编号，
     * 严禁传入本函数，否则纯数字规则会被误命中并截断成错误的短编号。
     * @param {...(string|number|null|undefined)} candidates
     * @returns {string}
     */
    function formatTyphoonShortId(...candidates) {
        for (const candidate of candidates) {
            const text = String(candidate ?? '').trim();
            if (!text) continue;
            if (text.toLowerCase() === 'unknown' || text === '未知') continue;

            // 纯数字官方编号：202609 -> 2609，2609 -> 2609
            if (/^\d{4,}$/.test(text)) {
                return text.slice(-4);
            }

            // 无名热带低压：NAMELESS / NAMELESS_03 / NAMELESS_2604 -> TD / TD03 / TD2604
            // 保留 TD 前缀，避免与同年正式台风编号撞车；裸 NAMELESS 也统一为 TD
            const namelessMatch = text.match(/^NAMELESS(?:[_-]?(.*))?$/i);
            if (namelessMatch) {
                const suffix = String(namelessMatch[1] || '').trim();
                if (!suffix) return 'TD';
                if (/^\d+$/.test(suffix)) return `TD${suffix}`;
                return suffix.toUpperCase().startsWith('TD') ? suffix.toUpperCase() : `TD${suffix}`;
            }

            // 已是 TD 形态则规范化大小写
            if (/^TD[_-]?\d+/i.test(text)) {
                return text.replace(/^TD[_-]?/i, 'TD');
            }

            // 其他非标准编号原样返回，绝不从混合文本里硬抠 4 位数字
            return text;
        }
        return '';
    }

    const api = {
        TYPHOON_LEVEL_EMOJI,
        TYPHOON_LEVEL_FILTER_OPTIONS,
        WIND_SPEED_THRESHOLDS,
        WIND_SPEED_FILTER_OPTIONS,
        PRESSURE_FILTER_OPTIONS,
        getTyphoonLevelEmoji,
        getTyphoonLevelColor,
        getTyphoonWindColor,
        formatTyphoonWindCircle,
        formatTyphoonCoords,
        formatTyphoonShortId,
        matchLevelKey,
    };

    global.DisasterTyphoonFormatters = api;
})(window);
