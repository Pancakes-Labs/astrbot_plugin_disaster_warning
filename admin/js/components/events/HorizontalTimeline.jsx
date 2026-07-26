const { Typography, Chip, Tooltip } = MaterialUI;
const { useMemo, useRef, useEffect, useState, useCallback } = React;

/**
 * 重大事件横向时间轴组件 (HorizontalTimeline)
 * 从专用端点 /api/events/major 拉取本系统覆盖的所有震级 M>=6.0、
 * 海啸预警或全国红色气象警报等重大防灾事件，并支持以横向时间导轨形式流畅展示。
 * 核心交互特色：
 * 1. 左右大步长滑动导航按钮（支持长按连续滚动，双击直达边界）。
 * 2. 鼠标抓取手势拖拽 (Drag & Grab) 流畅滑行。
 * 3. 动态展示条数选择菜单（包含 20条、50条、100条、不限等，点击外部自动关闭）。
 * 4. 智能判断各类灾害并应用警告判色，鼠标 hover 气泡提示事件详情。
 * 5. 首屏或接收到全新事件时自动滑行至最右端，且具备对不同展示限制切换与组件重载下的横向滚动位置恢复/记忆。
 *
 * @param {Object} props
 * @param {Object} [props.style] 外部注入的自定义样式
 */
function HorizontalTimeline({ style }) {
    // 从上下文状态解构时区设置
    const { state } = useAppContext();
    const { config } = state;
    const displayTimezone = config.displayTimezone || 'UTC+8';

    // 状态：展示的条数限制，默认 50 条
    const [displayLimit, setDisplayLimit] = useState('50');
    // 状态：条数限制下拉菜单是否打开
    const [isLimitMenuOpen, setIsLimitMenuOpen] = useState(false);
    
    // 自定义 Hook 获取重大事件源，内部响应式侦听 events 列表实时推送
    const { majorEvents, loading } = useMajorEvents(displayLimit, state.events);

    // 将拉取到的事件按发生时间正序重排列（旧 -> 新），以符合时间轴从左往右的顺序
    const timelineItems = useMemo(() => {
        return majorEvents
            .slice()
            .sort((a, b) => {
                const timeA = parseEventTimeToDate(a.time || a.timestamp, a.source || '')?.getTime() || 0;
                const timeB = parseEventTimeToDate(b.time || b.timestamp, b.source || '')?.getTime() || 0;
                return timeA - timeB;
            });
    }, [majorEvents]);

    // 滚动区域与控制菜单 DOM Ref
    const scrollContainerRef = useRef(null);
    const limitMenuRef = useRef(null);
    
    // 交互状态锁定：标记用户当前是否正在手动滚动或拖拽，防止被自动滚动重置
    const isUserScrolling = useRef(false);
    // 交互状态锁定：下拉菜单开启时，挂起自动滚动以避免错位
    const isInteractingWithLimitSelect = useRef(false);

    // 长按与步进滚动常量配置
    const BUTTON_SCROLL_STEP = 420;     // 点击一次按钮的滚动像素
    const HOLD_SCROLL_STEP = 140;       // 长按时单次递增像素
    const HOLD_SCROLL_INTERVAL = 45;    // 长按触发频度毫秒数
    const HOLD_START_DELAY = 220;       // 按下后延迟多少毫秒判定为长按

    // 存储长按所需的 Timeout 与 Interval 指针
    const holdStartTimerRef = useRef(null);
    const holdIntervalRef = useRef(null);

    /**
     * 终止一切连续滚动定时器，并释用户手动交互状态
     */
    const stopContinuousScroll = useCallback(() => {
        if (holdStartTimerRef.current) {
            clearTimeout(holdStartTimerRef.current);
            holdStartTimerRef.current = null;
        }
        if (holdIntervalRef.current) {
            clearInterval(holdIntervalRef.current);
            holdIntervalRef.current = null;
        }
        // 稍作延时，确保顺滑动画播放完毕后再恢复自动滚动机制
        setTimeout(() => {
            isUserScrolling.current = false;
        }, 120);
    }, []);

    /**
     * 按单步步长滑动容器
     * 
     * @param {number} direction 滚动方向因子 (-1 代表往左，1 代表往右)
     */
    const scrollByStep = useCallback((direction) => {
        if (!scrollContainerRef.current) return;
        isUserScrolling.current = true;
        scrollContainerRef.current.scrollBy({
            left: direction * BUTTON_SCROLL_STEP,
            behavior: 'smooth'
        });
        setTimeout(() => {
            isUserScrolling.current = false;
        }, 420);
    }, []);

    /**
     * 双击直达左右边界
     * 
     * @param {boolean} toRight 是否跳转至最右侧
     */
    const scrollToEdge = useCallback((toRight) => {
        if (!scrollContainerRef.current) return;
        isUserScrolling.current = true;
        scrollContainerRef.current.scrollTo({
            left: toRight ? scrollContainerRef.current.scrollWidth : 0,
            behavior: 'smooth'
        });
        setTimeout(() => {
            isUserScrolling.current = false;
        }, 520);
    }, []);

    /**
     * 启动连续长按滑动滚动
     * 
     * @param {number} direction 滚动方向 (-1 往左，1 往右)
     */
    const startContinuousScroll = useCallback((direction) => {
        if (!scrollContainerRef.current) return;

        // 清理已有定时器，防抖防重
        stopContinuousScroll();
        isUserScrolling.current = true;

        // 启动延迟器，判定长按行为
        holdStartTimerRef.current = setTimeout(() => {
            holdIntervalRef.current = setInterval(() => {
                if (!scrollContainerRef.current) return;
                scrollContainerRef.current.scrollBy({
                    left: direction * HOLD_SCROLL_STEP,
                    behavior: 'auto' // 长按时采用直切无动画，以保证高流畅与无延迟
                });
            }, HOLD_SCROLL_INTERVAL);
        }, HOLD_START_DELAY);
    }, [stopContinuousScroll]);

    // 组件卸载防护：确保清除所有可能的宏任务定时器
    useEffect(() => {
        return () => {
            stopContinuousScroll();
        };
    }, [stopContinuousScroll]);

    // 用于追踪记录上一次事件项的数目，以此精确定位是有新事件推入还是数据源初始化
    const prevItemsLengthRef = useRef(0);

    // 数据变动监听：当有新的大事件加入或首次初始化时，自动滚动到最右端最新事件上，或者还原记忆的滚动位置
    useEffect(() => {
        const hasNewItems = timelineItems.length > prevItemsLengthRef.current;
        const isFirstRender = prevItemsLengthRef.current === 0;

        // 更新历史快照数目
        prevItemsLengthRef.current = timelineItems.length;

        if (scrollContainerRef.current) {
            const el = scrollContainerRef.current;
            const scrollKey = `astrbot_scroll_horizontal_timeline_${displayLimit}`;
            const savedScrollLeft = localStorage.getItem(scrollKey);

            if (savedScrollLeft !== null) {
                // 如果本地有保存的滚动条位置，直接恢复它
                const targetScrollLeft = parseInt(savedScrollLeft, 10);
                // 采用双重 requestAnimationFrame 确保 DOM 挂载和排版渲染完毕
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        if (el) {
                            el.scrollLeft = targetScrollLeft;
                        }
                    });
                });
            } else if (!isUserScrolling.current && !isInteractingWithLimitSelect.current) {
                // 如果没有记忆位置，且是首次加载或检测到新数据追加，平滑/直接滑动到最右侧终点（最新事件处）
                if (isFirstRender || hasNewItems) {
                    setTimeout(() => {
                        if (!isUserScrolling.current && el) {
                            el.scrollLeft = el.scrollWidth;
                        }
                    }, 100);
                }
            }
        }
    }, [timelineItems, displayLimit]);

    // 滚动条事件监听：在用户主动拖拽/滚动时，使用防抖技术实时记忆滚动位置
    useEffect(() => {
        const el = scrollContainerRef.current;
        if (!el) return;

        let writeTimeout = null;
        const handleScrollMemory = () => {
            if (!el) return;
            const scrollKey = `astrbot_scroll_horizontal_timeline_${displayLimit}`;
            // 写入本地存储
            localStorage.setItem(scrollKey, el.scrollLeft);
        };

        const debouncedScroll = () => {
            clearTimeout(writeTimeout);
            writeTimeout = setTimeout(handleScrollMemory, 100); // 100ms 防抖
        };

        el.addEventListener('scroll', debouncedScroll);
        return () => {
            if (el) {
                el.removeEventListener('scroll', debouncedScroll);
            }
            clearTimeout(writeTimeout);
        };
    }, [displayLimit, timelineItems]);

    // 条数限制下拉菜单配置列表
    const LIMIT_OPTIONS = [
        { value: '20', label: '20 条' },
        { value: '50', label: '50 条' },
        { value: '100', label: '100 条' },
        { value: '200', label: '200 条' },
        { value: '500', label: '500 条' },
        { value: 'all', label: '不限' },
    ];

    const selectedLimitOption = LIMIT_OPTIONS.find((option) => option.value === displayLimit) || LIMIT_OPTIONS[1];

    /**
     * 开启下拉条数选择菜单
     */
    const handleLimitMenuOpen = useCallback((event) => {
        event?.stopPropagation?.();
        isInteractingWithLimitSelect.current = true;
        isUserScrolling.current = true;
        setIsLimitMenuOpen(true);
    }, []);

    /**
     * 关闭下拉条数选择菜单
     */
    const handleLimitMenuClose = useCallback(() => {
        setIsLimitMenuOpen(false);
        window.setTimeout(() => {
            isInteractingWithLimitSelect.current = false;
            isUserScrolling.current = false;
        }, 120);
    }, []);

    /**
     * 选定限制条数并重载
     */
    const handleLimitOptionSelect = useCallback((nextValue) => {
        setDisplayLimit(nextValue);
        handleLimitMenuClose();
    }, [handleLimitMenuClose]);

    // 交互监听：下拉菜单开启时，绑定全局点击外部及 Esc 按键事件以触发关闭动作
    useEffect(() => {
        if (!isLimitMenuOpen) return undefined;

        const handlePointerDownOutside = (event) => {
            if (!limitMenuRef.current?.contains(event.target)) {
                handleLimitMenuClose();
            }
        };

        const handleEscape = (event) => {
            if (event.key === 'Escape') {
                handleLimitMenuClose();
            }
        };

        document.addEventListener('mousedown', handlePointerDownOutside);
        document.addEventListener('keydown', handleEscape);
        return () => {
            document.removeEventListener('mousedown', handlePointerDownOutside);
            document.removeEventListener('keydown', handleEscape);
        };
    }, [handleLimitMenuClose, isLimitMenuOpen]);

    /**
     * 时间轴头部渲染
     */
    const TimelineHeader = () => (
        <div className="chart-card-header horizontal-timeline-header">
            <div className="horizontal-timeline-title-row">
                <span className="horizontal-timeline-title-icon">⏳</span>
                <Typography variant="h6">重大事件回溯</Typography>
            </div>
            {/* 菜单展示限制条数选择 */}
            <div
                ref={limitMenuRef}
                className="horizontal-timeline-display-control horizontal-timeline-display-control-menu"
                onMouseDown={(event) => event.stopPropagation()}
            >
                <Typography variant="caption" className="horizontal-timeline-display-label">展示</Typography>
                <button
                    type="button"
                    className={`horizontal-timeline-limit-select horizontal-timeline-limit-trigger ${isLimitMenuOpen ? 'is-open' : ''}`}
                    aria-haspopup="menu"
                    aria-expanded={isLimitMenuOpen}
                    onClick={(event) => {
                        event.stopPropagation();
                        if (isLimitMenuOpen) handleLimitMenuClose();
                        else handleLimitMenuOpen(event);
                    }}
                    onKeyDown={(event) => {
                        event.stopPropagation();
                        if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            if (isLimitMenuOpen) handleLimitMenuClose();
                            else handleLimitMenuOpen(event);
                        }
                        if (event.key === 'Escape') {
                            handleLimitMenuClose();
                        }
                    }}
                >
                    <span>{selectedLimitOption.label}</span>
                    <span className="horizontal-timeline-limit-trigger-icon" aria-hidden="true">▾</span>
                </button>
                {/* 下拉菜单弹出层 */}
                {isLimitMenuOpen && (
                    <div className="horizontal-timeline-limit-menu" role="menu" onMouseDown={(event) => event.stopPropagation()}>
                        {LIMIT_OPTIONS.map((option) => (
                            <button
                                key={option.value}
                                type="button"
                                role="menuitemradio"
                                aria-checked={displayLimit === option.value}
                                className={`horizontal-timeline-limit-option ${displayLimit === option.value ? 'is-selected' : ''}`}
                                onClick={(event) => {
                                    event.stopPropagation();
                                    handleLimitOptionSelect(option.value);
                                }}
                            >
                                <span>{option.label}</span>
                                {displayLimit === option.value && <span className="horizontal-timeline-limit-option-check">✓</span>}
                            </button>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );

    // 3. 状态：正在拉取或数据重构中，渲染精美骨架屏/加载指示器，给用户良好的交互回馈
    if (loading) {
        return (
            <div className="card horizontal-timeline-card horizontal-timeline-loading-card">
                <TimelineHeader />
                <div className="horizontal-timeline-loading-body">
                    <CircularProgress size={28} className="horizontal-timeline-loading-spinner" />
                    <Typography variant="body2" className="horizontal-timeline-loading-text">
                        正在努力加载重大事件记录，请稍候...
                    </Typography>
                </div>
            </div>
        );
    }

    // 4. 状态：无大事件回溯数据时渲染空模板
    if (timelineItems.length === 0) {
        return (
            <div className="card horizontal-timeline-card horizontal-timeline-empty-card">
                <TimelineHeader />
                <div className="horizontal-timeline-empty-body">
                    <Typography variant="body2">近期无重大事件</Typography>
                </div>
            </div>
        );
    }

    /**
     * 格式化展示时间，去除年份，用 / 替代 - 以节约横向排版高度
     */
    const formatTime = (isoString, source) => {
        if (!isoString) return '';
        try {
            const formatted = formatTimeWithZone(isoString, displayTimezone, false, source || '');
            return formatted.replace('-', '/');
        } catch (e) {
            return '';
        }
    };

    /**
     * 核心预警等级着色评级：根据地震震级、海啸警报级别或气象级别生成危险配色样式类
     */
    const getEventToneClass = (event) => {
        // S-Net 测站峰值：按震度阶级着色（>=5弱 才进入重大事件）
        if (event.type === 'snet_peak') {
            const shindo = Number(event.shindo);
            if (Number.isFinite(shindo)) {
                if (shindo >= 6.5) return 'is-purple';
                if (shindo >= 6.0) return 'is-red';
                if (shindo >= 5.5) return 'is-orange';
                if (shindo >= 4.5) return 'is-yellow';
            }
            const level = String(event.level || '');
            if (level.includes('7') || level.includes('6強') || level.includes('6+')) return 'is-red';
            if (level.includes('6弱') || level.includes('6-') || level.includes('5強') || level.includes('5+')) return 'is-orange';
            return 'is-yellow';
        }

        // A. 地震：根据震级进行判色
        if (event.type === 'earthquake' || event.type === 'earthquake_warning') {
            const sourceText = String(event?.source_id || event?.source || '').toLowerCase();
            const eventTypeText = String(event?.type || '').toLowerCase();
            const isJmaLike = sourceText.includes('jma') || sourceText.includes('p2p');
            const isJmaEew = isJmaLike && eventTypeText === 'earthquake_warning';

            // 日本气象厅 EEW 特殊阈值限制
            if (isJmaEew) {
                if (event.magnitude >= 7.0) return 'is-red';
                if (event.magnitude >= 6.0) return 'is-orange';
                return 'is-yellow';
            }

            // 通用地震速报级别配色
            if (event.magnitude >= 8.0) return 'is-purple';
            if (event.magnitude >= 7.0) return 'is-red';
            if (event.magnitude >= 6.0) return 'is-orange';
            return 'is-yellow';
        }

        // B. 台风：强台风使用红色，超强台风使用紫色
        if (event.type === 'typhoon') {
            const level = String(event._snapshot_level || event.level || '');
            if (level.includes('超强台风')) return 'is-purple';
            if (level.includes('强台风')) return 'is-red';
            return 'is-primary';
        }

        // C. 海啸：按级别或描述关键字判色
        if (event.type === 'tsunami') {
            const level = event.level || '';
            const desc = event.description || '';
            if (level.includes('红') || level.includes('Major') || desc.includes('红')) return 'is-red';
            if (level.includes('橙') || level.includes('Warning') || desc.includes('橙')) return 'is-orange';
            if (level.includes('黄') || level.includes('Watch') || desc.includes('黄')) return 'is-yellow';
            return 'is-blue';
        }

        // D. 气象预警：按级别中英文字段判色
        if (event.level) {
            if (event.level.includes('红')) return 'is-red';
            if (event.level.includes('橙')) return 'is-orange';
            if (event.level.includes('黄')) return 'is-yellow';
            if (event.level.includes('蓝')) return 'is-blue';
        }

        const desc = event.description || '';
        if (desc.includes('红')) return 'is-red';
        if (desc.includes('橙')) return 'is-orange';
        if (desc.includes('黄')) return 'is-yellow';
        if (desc.includes('蓝')) return 'is-blue';

        return 'is-primary';
    };

    /**
     * 获取数据源规范化中文名
     * 台风会按 info_type 细分为 Fan / Fan+EQSC / EQSC
     */
    const getSourceLabel = (event) => {
        const payload = event && typeof event === 'object'
            ? {
                ...event,
                source: event.source || event.source_id || event._groupSource || '',
                source_id: event.source_id || event.source || event._groupSource || '',
            }
            : event;
        if (typeof formatEventSourceName === 'function') {
            return formatEventSourceName(payload || 'unknown');
        }
        return formatSourceName(
            (payload && typeof payload === 'object'
                ? (payload.source_id || payload.source)
                : payload) || 'unknown'
        );
    };

    return (
        <div className="card horizontal-timeline-card">
            {/* 时间轴标题行 */}
            <div className="horizontal-timeline-header-wrap">
                <TimelineHeader />
            </div>

            {/* 时间轴主体容器 */}
            <div className="horizontal-timeline-body">
                {/* 左滑导航按钮 */}
                <div className="horizontal-timeline-nav-wrap horizontal-timeline-nav-wrap-left">
                    <button
                        type="button"
                        className="horizontal-timeline-nav-btn"
                        onClick={() => scrollByStep(-1)}
                        onDoubleClick={() => scrollToEdge(false)}
                        onMouseDown={() => startContinuousScroll(-1)}
                        onMouseUp={stopContinuousScroll}
                        onMouseLeave={stopContinuousScroll}
                        onTouchStart={() => startContinuousScroll(-1)}
                        onTouchEnd={stopContinuousScroll}
                        onTouchCancel={stopContinuousScroll}
                        title="单击：向左快速移动｜长按：连续移动｜双击：跳到最左"
                        aria-label="向左浏览重大事件"
                    >
                        <span className="horizontal-timeline-nav-icon" aria-hidden="true">‹</span>
                    </button>
                </div>

                {/* 右滑导航按钮 */}
                <div className="horizontal-timeline-nav-wrap horizontal-timeline-nav-wrap-right">
                    <button
                        type="button"
                        className="horizontal-timeline-nav-btn"
                        onClick={() => scrollByStep(1)}
                        onDoubleClick={() => scrollToEdge(true)}
                        onMouseDown={() => startContinuousScroll(1)}
                        onMouseUp={stopContinuousScroll}
                        onMouseLeave={stopContinuousScroll}
                        onTouchStart={() => startContinuousScroll(1)}
                        onTouchEnd={stopContinuousScroll}
                        onTouchCancel={stopContinuousScroll}
                        title="单击：向右快速移动｜长按：连续移动｜双击：跳到最右"
                        aria-label="向右浏览重大事件"
                    >
                        <span className="horizontal-timeline-nav-icon" aria-hidden="true">›</span>
                    </button>
                </div>

                {/* 滑动导轨主视区 (支持鼠标水平拖拽) */}
                <div
                    ref={scrollContainerRef}
                    className="horizontal-timeline-scroll-container"
                    onMouseDown={(e) => {
                        if (isInteractingWithLimitSelect.current) return;
                        isUserScrolling.current = true; // 锁定自动滚动
                        const ele = e.currentTarget;
                        ele.classList.add('is-dragging'); // 注入抓取手势 grab Class

                        let pos = {
                            left: ele.scrollLeft,
                            x: e.clientX,
                        };

                        const mouseMoveHandler = (e) => {
                            const dx = e.clientX - pos.x;
                            ele.scrollLeft = pos.left - dx;
                        };

                        const mouseUpHandler = () => {
                            isUserScrolling.current = false;
                            ele.classList.remove('is-dragging');
                            document.removeEventListener('mousemove', mouseMoveHandler);
                            document.removeEventListener('mouseup', mouseUpHandler);
                        };

                        document.addEventListener('mousemove', mouseMoveHandler);
                        document.addEventListener('mouseup', mouseUpHandler);
                    }}
                    onTouchStart={() => { isUserScrolling.current = true; }}
                    onTouchEnd={() => { isUserScrolling.current = false; }}
                >
                    {/* 时间轴水平轨线包装 */}
                    <div className="horizontal-timeline-inner">
                        {/* 轴线背景导轨 */}
                        <div className="horizontal-timeline-axis"></div>

                        {/* 循环渲染大事件节点项 */}
                        {timelineItems.map((item, index) => {
                            const toneClass = getEventToneClass(item);
                            const sourceLabel = getSourceLabel(item);

                            return (
                                <div key={index} className="horizontal-timeline-item">
                                    {/* 上方：简短发生时间 */}
                                    <div className="horizontal-timeline-time-wrap">
                                        <Typography variant="caption" className="horizontal-timeline-time-label">
                                            {formatTime(item.time || item.timestamp, item.source)}
                                        </Typography>
                                    </div>

                                    {/* 中间：着色时间点圆圈 */}
                                    <div className={`horizontal-timeline-dot ${toneClass}`}></div>

                                    {/* 下方：卡片标题、地点与数据源摘要 */}
                                    <Tooltip title={item.description} arrow placement="bottom">
                                        <div className="horizontal-timeline-content">
                                            {/* A. 结构化大标题 */}
                                            <Typography variant="body2" className={`horizontal-timeline-node-title ${toneClass}`}>
                                                {(() => {
                                                    if (item.type === 'snet_peak') {
                                                        const level = String(item.level || '').trim();
                                                        if (level) return `震度${level}`;
                                                        const shindo = Number(item.shindo);
                                                        if (Number.isFinite(shindo)) {
                                                            return `震度${shindo.toFixed(1)}`;
                                                        }
                                                        return 'S-Net';
                                                    }
                                                    if (item.type === 'earthquake') {
                                                        const mag = Number(item.magnitude);
                                                        if (Number.isFinite(mag)) {
                                                            return Number.isInteger(mag) ? `M ${mag}.0` : `M ${mag}`;
                                                        }
                                                        const desc = String(item.description || '').trim();
                                                        const match = desc.match(/M\s*([\d.]+)/i);
                                                        if (match && match[1]) {
                                                            return `M ${match[1]}`;
                                                        }
                                                        return '地震';
                                                    } else if (item.type === 'tsunami') {
                                                        const fmt = window.EventFormatters || {};
                                                        if (typeof fmt.buildTsunamiTimelineTitle === 'function') {
                                                            return fmt.buildTsunamiTimelineTitle(item);
                                                        }
                                                        return item.level || item.title || item.description || '海啸预警';
                                                    } else {
                                                        // 气象预警正则压缩标题：提取“发布...信号”中的具体核心词
                                                        const match = item.description ? item.description.match(/发布(.*?)信号/) : null;
                                                        if (match && match[1]) {
                                                            return match[1];
                                                        }
                                                        return (item.description || '未知事件').split(' ')[0].slice(0, 8);
                                                    }
                                                })()}
                                            </Typography>
                                            
                                            {/* B. 地点与辅助描述 */}
                                            <Typography variant="caption" className="horizontal-timeline-node-subtitle">
                                                {(() => {
                                                    const desc = String(item.description || '').trim();
                                                    if (item.type === 'snet_peak') {
                                                        const station = String(item.place_name || item.place || '').trim();
                                                        return station || 'S-Net 测站';
                                                    }
                                                    if (item.type === 'earthquake') {
                                                        const structuredPlace = String(item.place_name || item.place || '').trim();
                                                        if (structuredPlace) {
                                                            return structuredPlace;
                                                        }
                                                        const normalizedDesc = desc.replace(/^M\s*[\d.]+\s*/i, '').trim();
                                                        return normalizedDesc || '未知地点';
                                                    }
                                                    if (item.type === 'tsunami') {
                                                        const fmt = window.EventFormatters || {};
                                                        if (typeof fmt.buildTsunamiTimelineSubtitle === 'function') {
                                                            return fmt.buildTsunamiTimelineSubtitle(item);
                                                        }
                                                        const place = String(item.place_name || item.subtitle || '').trim();
                                                        if (place) return place;
                                                        return desc.length > 12 ? `${desc.substring(0, 12)}...` : (desc || '海啸');
                                                    }
                                                    return desc.length > 12 ? desc.substring(0, 12) + '...' : desc;
                                                })()}
                                            </Typography>
                                            
                                            {/* C. 发布数据源 */}
                                            <Typography variant="caption" className="horizontal-timeline-source-label">
                                                📡 {sourceLabel}
                                            </Typography>
                                        </div>
                                    </Tooltip>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
}
