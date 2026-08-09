/**
 * 实时事件三栏竖向滚动跑马灯组件 (NewsTicker)
 * 基于旧版横向跑马灯改造：
 * - 保持单行条状卡片（56px 高度，与旧版一致），左侧「🔔 最新动态」标题固定，
 *   右侧剩余宽度分为三栏：地震 / 气象 / 海啸+台风；
 * - 三栏文字直接在跑马灯条上竖向无缝循环滚动，无底框、无栏目标题；
 * - 时效策略：地震 / 气象 仅播报近 1 小时内事件，海啸单独放宽至近 24 小时；
 * - 台风栏特殊处理：仅保留每个活跃台风的最新一报数据（按 typhoon_id 去重，
 *   同一台风只显示最新观测），避免历史多报堆积刷屏。
 * - 队列排序：渲染前对每栏队列按事件时间降序重排（最新在上），
 *   避免不同入队路径（WS 推送 / 全量快照）带来的时间乱序。
 * - 循环节奏：每轮滚动完整播完一轮后停顿 3 秒再继续，便于阅读。
 *
 * 核心设计：
 * 1. 事件队列处理：新事件（WebSocket 实时推送 lastEvent / 全量快照 events）按类型
 *    增量入队；已消费事件 ID 去重集合避免重复入队；单栏队列封顶防止内存膨胀。
 * 2. 载荷补全：WebSocket 精简载荷（仅 id/type/source/time/weather_emoji）先入队展示，
 *    待全量统计快照（含 description/magnitude 完整字段）到达后，按
 *    「id 精确 → 类型+来源 → 类型+时间临近(±5min)」逐级匹配替换占位条目，
 *    避免长期显示"无详细描述"。
 * 3. 无缝循环播放：每栏轨道内容自我复制双份，CSS 动画 translateY(0 → -50%)
 *    实现首尾相接无断点滚动；通过动态注入关键帧实现"播完停 3 秒再续"。
 * 4. 悬停机制：鼠标悬停卡片暂停全部三栏滚动，方便阅读；移开后恢复播放。
 * 5. S-Net 海底震度推送频繁且描述偏噪声，不进跑马灯以免刷屏。
 *
 * @param {Object} props
 * @param {Object} [props.style] 外部样式
 */
const { Typography, Chip } = MaterialUI;
const { useState, useRef, useEffect, useCallback } = React;

// ===== 模块级常量：不随渲染重建，避免每次渲染创建新引用 =====

// 三栏定义：key / 接受的底层事件类型（宽度由 CSS 网格负责）
const COLUMN_DEFS = [
    { key: 'quake',   types: ['earthquake', 'earthquake_warning'] },
    { key: 'weather', types: ['weather_alarm', 'weather'] },
    { key: 'ocean',   types: ['tsunami', 'typhoon'] },
];

// 每栏队列长度上限：保证滚动条高度可控，同时保留足够的近况条目
const MAX_ITEMS_PER_COLUMN = 12;

// 时效窗口：
// - 地震/气象：近 1 小时
// - 海啸：近 24 小时（影响周期长，放宽避免速报过快过期）
// - 台风：近 6 小时（台风编报稀疏，1 小时太紧会漏掉活跃台风最新状态）
const HOURS = 3600000;
const TSUNAMI_WINDOW_MS = 24 * HOURS;
const TYPHOON_WINDOW_MS = 6 * HOURS;

// 每轮滚动播完后停顿的时长（毫秒），便于阅读
const PAUSE_AFTER_CYCLE_MS = 3000;

// consumedIdsRef 允许的最大容量，超出后清理最旧的一半（事件 id 均为短字符串，内存占用极小）
const CONSUMED_IDS_LIMIT = 256;

// 未来时间容差（毫秒）：事件时间略微超前（时区处理误差、来源时钟偏移）仍视为有效，
// 但超出该容差视为无效/异常数据，不进入跑马灯，避免未来事件永久占据栏目。
const FUTURE_TOLERANCE_MS = 5 * 60 * 1000;

// 「无详细描述」占位条目与全量快照载荷匹配的时间容差（毫秒）
const PLACEHOLDER_TIME_SLACK_MS = 3 * 60 * 1000;

/**
 * 判断事件时间是否位于时效窗口内（含未来时间容差）。
 * 地震/气象近 1 小时、海啸近 24 小时、台风近 6 小时。
 * 入队与渲染前过滤共用同一口径，保证展示不会超出时效窗口。
 * @param {number} timeMs 事件时间戳（毫秒）
 * @param {string} type 规范化后的事件类型
 * @returns {boolean} 是否在窗口内
 */
const isTimeWithinWindow = (timeMs, type) => {
    if (!timeMs) return false;
    let windowMs = HOURS;
    if (type === 'tsunami') windowMs = TSUNAMI_WINDOW_MS;
    else if (type === 'typhoon') windowMs = TYPHOON_WINDOW_MS;
    const now = Date.now();
    return timeMs > now - windowMs && timeMs <= now + FUTURE_TOLERANCE_MS;
};

/**
 * 动态注入「播完停 3 秒」的关键帧（幂等）。
 * 仅在 effect 副作用中调用，渲染阶段不做任何 DOM 写入。
 * 注入前先按 data-ticker-keyframes 查询已存在的 <style> 节点，复用避免累积。
 * @param {number} scrollSeconds 单轮滚动时长（秒）
 * @param {Set<string>} injectedSet 已注入时长集合（组件实例级）
 */
const injectKeyframesIfNeeded = (scrollSeconds, injectedSet) => {
    const key = `${scrollSeconds}`;
    if (injectedSet.has(key)) return;

    // 组件卸载再挂载后 ref 会重置，但上一轮注入的 style 节点仍在 document.head 中；
    // 按属性查询已存在的同名关键帧节点并复用，避免重复累积。
    const existing = document.querySelector(`style[data-ticker-keyframes="${key}"]`);
    if (existing) {
        injectedSet.add(key);
        return;
    }

    const totalMs = scrollSeconds * 1000 + PAUSE_AFTER_CYCLE_MS;
    const scrollPct = ((scrollSeconds * 1000) / totalMs) * 100;
    const scrollPctSafe = Math.max(0.5, Math.min(99.5, scrollPct));

    const styleEl = document.createElement('style');
    styleEl.setAttribute('data-ticker-keyframes', key);
    styleEl.textContent = `
        @keyframes ticker-scroll-${key} {
            0% { transform: translateY(0); }
            ${scrollPctSafe}% { transform: translateY(-50%); }
            100% { transform: translateY(-50%); }
        }
    `;
    document.head.appendChild(styleEl);
    injectedSet.add(key);
};

/**
 * 合并两个 ticker 条目：优先保留带描述/带震级的一侧，
 * 避免 WS 精简摘要与统计投影相互覆盖造成"无详细描述"或丢失震级。
 * 模块级纯函数，不依赖组件作用域。
 *
 * 合并方向：以 nextItem（全量快照载荷）为基础，显式字段逐一取有值一侧；
 * 不整体展开 prevItem，避免新旧字段的合并方向被隐式展开顺序掩盖。
 */
const mergeTickerItem = (prevItem, nextItem) => {
    const desc = (nextItem.desc && nextItem.desc !== '无详细描述')
        ? nextItem.desc
        : (prevItem.desc && prevItem.desc !== '无详细描述' ? prevItem.desc : '无详细描述');
    return {
        id: nextItem.id || prevItem.id,
        timeMs: nextItem.timeMs || prevItem.timeMs,
        time: nextItem.time || prevItem.time,
        type: nextItem.type || prevItem.type,
        source: nextItem.source || prevItem.source,
        desc,
        mag: nextItem.mag || prevItem.mag || null,
        typhoonKey: nextItem.typhoonKey || prevItem.typhoonKey || '',
        emoji: nextItem.emoji || prevItem.emoji || '',
        // 气象预警颜色 Emoji。后端按 COLOR_LEVEL_EMOJI 解析下发
        colorEmoji: nextItem.colorEmoji || prevItem.colorEmoji || '',
    };
};

function NewsTicker({ style }) {
    const { state } = useAppContext();
    const { events, config, dataLoaded, lastEvent } = state;
    const displayTimezone = config.displayTimezone || 'UTC+8';

    // 状态：控制跑马灯是否由于鼠标悬停而暂停滚动
    const [paused, setPaused] = useState(false);
    const isDark = state.theme === 'dark';

    // 三栏事件队列（新事件头部插入，尾部淘汰）
    const [columns, setColumns] = useState(() => ({
        quake: [],
        weather: [],
        ocean: [],
    }));
    // columns 的最新快照：供快照同步 effect 在 setState 之外做纯计算，
    // 避免在 updater 内写入 ref（React 并发/StrictMode 可能重放 updater 导致丢事件）。
    const columnsRef = useRef(columns);
    columnsRef.current = columns;

    // 已消费事件 ID 集合：同一事件无论从 WS 推送还是全量快照进入均只入队一次
    // 该集合只增不减，长期运行会随事件总数增长；通过封顶清理兜底防膨胀。
    const consumedIdsRef = useRef(new Set());
    // 已注入动态关键帧的时长集合（幂等，避免重复注入 style）
    const injectedKeyframesRef = useRef(new Set());

    /**
     * 解析事件时间戳（毫秒），失败返回 0
     */
    const getEventTimeMs = (event) => (
        parseEventTimeToDate(
            event.time || event.timestamp || event.event_time || event.updated_at,
            event.source_id || event.source || ''
        )?.getTime() || 0
    );

    /**
     * 生成事件唯一 ID：优先显式 ID，缺失时回退为「时间-类型」指纹（与全局排重口径一致）
     */
    const getEventId = (event) => (
        event.event_id
        || event.id
        || `${event.time || event.timestamp || event.event_time || ''}-${event.type || event.event_type || ''}`
    );

    /**
     * S-Net 海底震度推送频繁且描述偏噪声，不进跑马灯以免刷屏。
     * 仅按 source / source_id 精确识别，避免描述关键字误伤其他事件。
     */
    const isSnetEvent = (event) => {
        const source = String(event?.source_id || event?.source || '').toLowerCase();
        return (
            source.includes('snet')
            || source.includes('s-net')
            || source === 'snet_msil'
            || source.includes('nied s-net')
            || source.includes('nied snet')
        );
    };

    /**
     * 规范化事件类型：历史遗留的 weather 归一为 weather_alarm，便于归类
     */
    const normalizeType = (rawType) => {
        const type = String(rawType || '').toLowerCase();
        if (type === 'weather') return 'weather_alarm';
        return type;
    };

    /**
     * 将事件归类到对应栏目（地震 / 气象 / 海啸+台风）
     * @returns {string|null} 栏目 key，未知类型返回 null 不进跑马灯
     */
    const classifyEvent = (event) => {
        const type = normalizeType(event?.type || event?.event_type || '');
        for (const col of COLUMN_DEFS) {
            if (col.types.includes(type)) return col.key;
        }
        return null;
    };

    /**
     * 提取台风个体 ID：优先读取 ticker item 的 typhoonKey，
     * 其次读取原始事件的 typhoon_id / eqsc_id / real_event_id，
     * 兼容 4 位 EQSC / 6 位 Fan 编号，NAMELESS 前缀保留避免与正式编号冲突。
     */
    const getTyphoonKey = (event) => {
        const raw = String(
            event?.typhoonKey
            || event?.typhoon_id
            || event?.eqsc_id
            || event?.real_event_id
            || event?.typhoonId
            || ''
        ).trim();
        if (!raw) return '';
        if (/^\d{4,}$/.test(raw)) return raw.slice(-4);
        return raw;
    };

    /**
     * 时效判定：按类型区分窗口（含未来时间容差，复用模块级口径）
     * - 海啸（tsunami）：近 24 小时
     * - 台风（typhoon）：近 6 小时
     * - 其他（地震/气象）：近 1 小时
     */
    const isWithinWindow = (event) => {
        const t = getEventTimeMs(event);
        if (!t) return false;
        const type = normalizeType(event?.type || event?.event_type || '');
        return isTimeWithinWindow(t, type);
    };

    /**
     * 将原始事件规整化为跑马灯条目
     */
    const toTickerItem = (event) => ({
        id: getEventId(event),
        timeMs: getEventTimeMs(event),
        time: event.time || event.timestamp || event.event_time || event.updated_at || '',
        type: normalizeType(event.type || event.event_type || ''),
        source: event.source_id || event.source || '',
        desc: event.description || event.place_name || '无详细描述',
        mag: event.magnitude,
        // 台风额外保留个体标识，用于去重。
        // 兼容后端 WS 摘要仅带 id（=台风编号）而不带 typhoon_id 字段的情况：
        // 台风事件 id 本身就是台风编号，直接回退取 id。
        typhoonKey: event.typhoon_id
            || event.eqsc_id
            || event.real_event_id
            || (normalizeType(event.type || event.event_type || '') === 'typhoon' ? getEventId(event) : ''),
        // 后端已按 WEATHER_EMOJI_MAP 唯一映射表解析好气象 Emoji，前端直接消费无需重复维护
        emoji: event.weather_emoji || '',
        // 后端按 COLOR_LEVEL_EMOJI 解析的气象预警颜色 Emoji。无则空串
        colorEmoji: event.weather_color_emoji || '',
    });

    /**
     * 事件增量入队：时效过滤 -> id 去重 -> 归类 -> 头部插入对应栏队列 -> 封顶截断。
     * 事件队列处理细节：
     * - 已消费 ID 集合（Set）保证同一事件仅入队一次，避免 WS 推送与全量快照重复；
     *   仅按 id 精确去重，绝不按时间相近合并——同一来源 1 分钟内可能连续发生
     *   多起不同事件（如 GlobalQuake 逐条推送），按时间合并会误杀导致队列坍缩；
     * - 队列严格单调增长：只插入、绝不删除已有条目，保证事件流更新时平滑过渡，
     *   不会因台风预去重 / 快照重建把队列清成一条；
     * - 台风"每台风只留最新一报"由渲染前 dedupeTyphoon 兜底完成，入队不干预；
     * - 头部插入让最新速报立即出现在滚动区顶部，视觉上「新事件优先」；
     * - 单栏封顶 MAX_ITEMS_PER_COLUMN，旧的自动淘汰，防止内存膨胀与滚动条过长。
     */
    const enqueueEvent = useCallback((rawEvent) => {
        if (!rawEvent || typeof rawEvent !== 'object') return;
        if (isSnetEvent(rawEvent)) return;
        if (!isWithinWindow(rawEvent)) return;
        const id = getEventId(rawEvent);
        if (!id || consumedIdsRef.current.has(id)) return;
        const colKey = classifyEvent(rawEvent);
        if (!colKey) return;

        // 容量管控：超出阈值时清理最旧的一半，防止长期运行内存无限膨胀
        if (consumedIdsRef.current.size >= CONSUMED_IDS_LIMIT) {
            const ids = Array.from(consumedIdsRef.current);
            const toRemove = ids.slice(0, Math.floor(ids.length / 2));
            toRemove.forEach((oldId) => consumedIdsRef.current.delete(oldId));
        }
        consumedIdsRef.current.add(id);
        const item = toTickerItem(rawEvent);

        setColumns((prev) => {
            const queue = prev[colKey] || [];
            // 队列内去重兜底：consumedIdsRef 清理最旧一半后同一事件重推会再次到达，
            // 若队列已含该 id 则直接忽略，避免重复入队。
            if (queue.some((it) => it.id === id)) return prev;
            return {
                ...prev,
                [colKey]: [item, ...queue].slice(0, MAX_ITEMS_PER_COLUMN),
            };
        });
    }, []);

    /**
     * 实时事件推送增量监听：WebSocket 新事件（lastEvent）变化即入队，
     * 保证新增速报零延迟出现在对应栏顶部。
     */
    useEffect(() => {
        if (lastEvent) enqueueEvent(lastEvent);
    }, [lastEvent, enqueueEvent]);

    /**
     * 全量快照同步：每次 events（历史/统计投影）更新时，按时间倒序扫描，
     * 采用「纯增量合并」策略——绝不删除/重建已有队列：
     * - 未入队的新事件：直接入队（覆盖页面首次加载的历史回填）；
     * - 已入队的条目：按 id / 台风个体 / 占位三级匹配后合并载荷（升级占位）。
     * 台风去重完全交给渲染前 dedupeTyphoon 兜底，这里不做删除，
     * 保证事件流（WS 推送 + 快照）交替到达时队列平滑增长，不会突然坍缩成一条。
     *
     * 并发安全说明：
     * - 使用函数式 setColumns(prev => ...)：React 会按调用顺序链式应用 updater，
     *   同帧内 WS 入队（enqueueEvent）的更新先于本快照应用，快照基于最新 prev 合并，
     *   不会用过期快照整体替换状态、丢弃刚入队的 WS 事件。
     * - consumedIdsRef 记账移到 updater 之外预计算：避免在 updater 内写 ref
     *   （React 并发/StrictMode 可能重放 updater，导致新事件永久不入队）。
     */
    useEffect(() => {
        if (!dataLoaded || !Array.isArray(events) || events.length === 0) return;

        // 时效过滤 + 按时间倒序排列，保证新事件优先处理
        const sorted = [...events]
            .filter((e) => !isSnetEvent(e) && isWithinWindow(e))
            .sort((a, b) => getEventTimeMs(b) - getEventTimeMs(a));

        // 预计算本帧快照中将作为「真新增」插入的条目 id（基于最新 columnsRef 快照），
        // 统一在 setState 之外记账：即使 updater 被 StrictMode 重放，记账也只会执行一次。
        const idsToConsume = [];
        sorted.forEach((e) => {
            const colKey = classifyEvent(e);
            if (!colKey) return;
            const fullItem = toTickerItem(e);
            const id = fullItem.id;
            if (!id) return;
            if (consumedIdsRef.current.has(id)) return;
            const queue = columnsRef.current[colKey] || [];
            const alreadyInQueue = queue.some((it) => it.id === id)
                || (colKey === 'ocean' && queue.some((it) => getTyphoonKey(it) === getTyphoonKey(fullItem)));
            if (!alreadyInQueue) {
                idsToConsume.push(id);
            }
        });
        idsToConsume.forEach((id) => consumedIdsRef.current.add(id));

        if (sorted.length === 0) return;

        // 函数式 updater：基于最新 prev 合并，绝不整体替换，避免覆盖同帧 WS 入队结果。
        setColumns((prev) => {
            let changed = false;
            const next = { ...prev };

            sorted.forEach((e) => {
                const colKey = classifyEvent(e);
                if (!colKey) return;
                const fullItem = toTickerItem(e);
                const id = fullItem.id;
                if (!id) return;

                let queue = next[colKey] || [];

                // 1) id 精确匹配
                let idx = queue.findIndex((it) => it.id === id);

                // 2) 台风个体匹配（同 typhoonKey，覆盖 WS id 与快照 id 不一致）
                if (idx === -1 && colKey === 'ocean') {
                    const key = getTyphoonKey(fullItem);
                    if (key) {
                        idx = queue.findIndex((it) => getTyphoonKey(it) === key);
                    }
                }

                // 3) 同类型同来源且时间临近的「无详细描述」占位条目
                //    （时间约束复用下方容差：避免同一来源多条占位时把描述错贴到别的预警）
                if (idx === -1 && fullItem.desc !== '无详细描述') {
                    idx = queue.findIndex(
                        (it) => it.desc === '无详细描述'
                            && normalizeType(it.type) === fullItem.type
                            && it.source === fullItem.source
                            && it.timeMs > 0
                            && fullItem.timeMs > 0
                            && Math.abs(it.timeMs - fullItem.timeMs) <= PLACEHOLDER_TIME_SLACK_MS
                    );
                }

                // 4) 同类型且时间临近（±3 分钟）的「无详细描述」占位条目（不限来源，兜底）
                if (idx === -1 && fullItem.desc !== '无详细描述') {
                    idx = queue.findIndex(
                        (it) => it.desc === '无详细描述'
                            && normalizeType(it.type) === fullItem.type
                            && it.timeMs > 0
                            && fullItem.timeMs > 0
                            && Math.abs(it.timeMs - fullItem.timeMs) <= PLACEHOLDER_TIME_SLACK_MS
                    );
                }

                if (idx === -1) {
                    // 真新增才插入；prev 已含该 id（WS 已入队）时 findIndex 必命中，不会走到这里。
                    queue = [fullItem, ...queue].slice(0, MAX_ITEMS_PER_COLUMN);
                    changed = true;
                } else {
                    // 已存在：合并载荷（优先保留带描述的一侧），避免占位残留
                    const newQueue = [...queue];
                    newQueue[idx] = mergeTickerItem(queue[idx], fullItem);
                    queue = newQueue;
                    changed = true;
                }

                next[colKey] = queue;
            });

            if (changed) {
                // 幂等同步最新快照，供下一次预计算使用。
                columnsRef.current = next;
                return next;
            }
            return prev;
        });
    }, [events, dataLoaded]);

    /**
     * 关键帧注入副作用：渲染保持纯函数，DOM 写入（document.head <style>）只在此执行。
     * 依赖 columns 变化时重算各栏 duration 并幂等注入；injectKeyframesIfNeeded 会复用
     * 已存在的 data-ticker-keyframes 节点，避免组件重挂载后 style 节点累积。
     */
    useEffect(() => {
        if (!dataLoaded) return;
        COLUMN_DEFS.forEach((col) => {
            const rawItems = dedupeTyphoon(columns[col.key] || [], col.key);
            const items = sortByTimeAsc(rawItems);
            const isStatic = items.length <= 2;
            if (!isStatic) {
                injectKeyframesIfNeeded(
                    getDuration(items.length),
                    injectedKeyframesRef.current
                );
            }
        });
    }, [columns, dataLoaded]);

    /**
     * 周期刷新：渲染前会按时效过滤过期条目；定时触发一次重渲染，
     * 让超过时效窗口的旧事件自动从栏目消失。
     */
    useEffect(() => {
        const timer = setInterval(() => {
            setColumns((prev) => ({ ...prev }));
        }, 60 * 1000);
        return () => clearInterval(timer);
    }, []);

    // 1. 状态：网络数据未就绪，渲染跑马灯骨架屏结构
    if (!dataLoaded) {
        return (
            <div className={`card news-ticker-card news-ticker-card--loading ${isDark ? 'is-dark' : 'is-light'}`} style={style}>
                <div className="news-ticker-head news-ticker-head--loading">
                    <span className="news-ticker-head__icon news-ticker-head__icon--large">📡</span>
                    <span>实时动态</span>
                </div>
                <div className="skeleton news-ticker-skeleton"></div>
            </div>
        );
    }

    /**
     * 格式化预警时间，保留 HH:mm 格式
     */
    const formatTime = (isoString, source) => {
        if (!isoString) return '';
        try {
            const formatted = formatTimeWithZone(isoString, displayTimezone, false, source || '');
            return formatted.split(' ')[1]; // 拆分日期，仅获取时间部分
        } catch (e) {
            return '';
        }
    };

    /**
     * 根据灾害类型获取 Emoji 视觉标示
     */
    const getIcon = (type, emoji = '') => {
        // 气象预警优先使用后端统一解析的 Emoji（覆盖具体灾害类型）
        if (emoji) return emoji;
        if (!type) return '📢';
        if (type.includes('earthquake')) return '🌍';
        if (type.includes('tsunami')) return '🌊';
        if (type.includes('weather')) return '⛈️';
        if (type.includes('typhoon')) return '🌀';
        return '📢';
    };

    /**
     * 根据条数动态计算单轮竖向滚动时长（秒）：
     * 每条约 3 秒保证足够阅读时间；最少 8 秒、最多 24 秒，防止条数过少时闪跳过快。
     */
    const getDuration = (count) => {
        if (count <= 0) return 8;
        return Math.min(24, Math.max(8, count * 3));
    };

    /**
     * 将队列按事件时间升序重排（最新在下）。
     * 竖向滚动时轨道从底部向上推入，最新的速报最后出现在底部，视觉上自然下沉。
     * 注意：返回新数组，不修改原状态。
     */
    const sortByTimeAsc = (items) => (
        [...items].sort((a, b) => a.timeMs - b.timeMs)
    );

    /**
     * 渲染前兜底去重：对海洋栏按台风个体（typhoonKey）去重，
     * 每个台风只保留最新一条（同一 typhoonKey 中 timeMs 最大者）。
     * 台风条目识别：typhoonKey 非空，或类型为 typhoon（此时 id 即台风编号）。
     * 海啸等无台风 key 的条目按 id 保留，不受影响。
     * 无论入队/快照路径如何交错，最终展示始终是"每个活跃台风一个最新快照"。
     */
    const dedupeTyphoon = (items, colKey) => {
        if (colKey !== 'ocean') return items;
        const seen = new Map();
        for (const item of items) {
            const key = getTyphoonKey(item);
            const isTyphoon = item.type === 'typhoon';
            if (!key && !isTyphoon) {
                seen.set(`__id__${item.id}`, item); // 海啸等非台风条目按 id 保留
                continue;
            }
            const dedupeKey = key || `__typhoon_id__${item.id}`; // 台风无 key 时用 id 兜底
            const existing = seen.get(dedupeKey);
            if (!existing || item.timeMs > existing.timeMs) {
                seen.set(dedupeKey, item); // 同台风保留最新
            }
        }
        return Array.from(seen.values());
    };


    /**
     * 渲染单个跑马灯条目（静态分支与滚动分支共用，避免两处 JSX 重复维护）。
     * @param {Object} item ticker 条目
     * @param {string} key React key
     */
    const renderItem = (item, key) => (
        <div key={key} className="news-ticker-v-item">
            <span className={`news-ticker-time ${isDark ? 'is-dark' : 'is-light'}`}>
                {formatTime(item.time, item.source)}
            </span>
            <span className="news-ticker-type-icon">{getIcon(item.type, item.emoji)}</span>

            {item.mag && (
                <Chip
                    label={Number.isInteger(item.mag) ? `M ${item.mag}.0` : `M ${item.mag}`}
                    size="small"
                    className={`news-ticker-mag-chip ${isDark ? 'is-dark' : 'is-light'}`}
                />
            )}

            <Typography
                component="span"
                variant="body2"
                className="news-ticker-desc"
            >
                {item.desc.replace(/^M[\d.]+\s*/, '')}
            </Typography>
            {/* 气象预警颜色等级 Emoji：对齐文本展示器「标题 + 颜色」结构，放在描述之后 */}
            {item.colorEmoji && (
                <span className="news-ticker-color-icon">{item.colorEmoji}</span>
            )}
        </div>
    );

    return (
        <div
            className={`card news-ticker-card ${isDark ? 'is-dark' : 'is-light'}`}
            style={style}
            tabIndex={0}
            onMouseEnter={() => setPaused(true)}
            onMouseLeave={() => setPaused(false)}
            onFocus={() => setPaused(true)}
            onBlur={() => setPaused(false)}
        >
            {/* 左侧固定标题（与三栏同一行） */}
            <div className="news-ticker-head">
                <span className="news-ticker-head__icon">🔔</span>
                <Typography variant="subtitle2" className="news-ticker-head__title">最新动态</Typography>
            </div>

            {/* 三栏竖向滚动区：占满标题右侧剩余宽度并三等分 */}
            <div className="news-ticker-v-grid">
                {COLUMN_DEFS.map((col) => {
                    // 1) 台风按个体去重（每个台风只保留最新一报）
                    // 2) 渲染前按时效再次过滤：入队后可能已过期，保证展示不超出时效窗口
                    // 3) 按时间升序重排（最新在下，滚动时最新沉到底部）
                    const rawItems = dedupeTyphoon(columns[col.key] || [], col.key);
                    const freshItems = rawItems.filter((it) => isTimeWithinWindow(it.timeMs, it.type));
                    const items = sortByTimeAsc(freshItems);
                    const isStatic = items.length <= 2; // 事件过少不滚动，静态展示
                    const duration = getDuration(items.length);
                    const totalDuration = duration + PAUSE_AFTER_CYCLE_MS / 1000;
                    return (
                        <div className="news-ticker-v-column" key={col.key}>
                            <div className="news-ticker-v-mask">
                                {items.length === 0 ? (
                                    <div className="news-ticker-v-empty">
                                        <span>暂无近期事件</span>
                                    </div>
                                ) : isStatic ? (
                                    /* 事件过少（≤2 条）时静态展示：不复制、不滚动 */
                                    <div className="news-ticker-v-static">
                                        {items.map((item) => renderItem(item, item.id))}
                                    </div>
                                ) : (
                                    /* 滚动长轴轨道（内容复制双份，动态关键帧实现"播完停 3 秒再续"）
                                       关键帧 0%→P% 停留在 -50% 起始位，P%→100% 滚回 0%，
                                       视觉上轨道从底部向上推入，最新事件沉在底部 */
                                    <div
                                        className={`news-ticker-v-track ${paused ? 'is-paused' : ''}`}
                                        style={{
                                            animationName: `ticker-scroll-${duration}`,
                                            animationDuration: `${totalDuration}s`,
                                        }}
                                    >
                                        {[...items, ...items].map((item, index) => renderItem(item, `${item.id}-${index}`))}
                                    </div>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
