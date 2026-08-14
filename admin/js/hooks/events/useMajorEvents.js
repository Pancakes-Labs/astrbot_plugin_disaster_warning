/**
 * 重大灾害事件列表拉取与监听钩子。
 * 
 * 业务细节说明：
 * - 聚合最近发生的强震、海啸预警或红色气象预警，主要用于大屏横向时间轴导轨组件的数据供给。
 * - 支持外部刷新信号，当长连接收到重大事件上报时，可强行重载大屏导轨。
 */
function useMajorEvents(displayLimit, refreshSignal) {
    const eventsApi = window.DisasterEventsApi;
    const [majorEvents, setMajorEvents] = React.useState([]); // 重大灾害队列
    const [loading, setLoading] = React.useState(false);       // 重大事件加载状态
    // 记录上一次已消费的刷新信号：displayLimit 变化会重建 fetchMajorEvents，
    // 导致「挂载/上限变化」与「刷新信号」两个 effect 同时触发。
    // 仅在刷新信号「后续发生变化且非 null」时才执行静默请求，避免首帧/重复请求。
    // 注意：ref 只能在 effect 内更新，若在渲染期同步会导致 effect 内
    // 比较永远相等、静默请求永不触发。
    const lastSignalRef = React.useRef(refreshSignal);

    /**
     * 并发拉取最近发生的重大事件
     * @param {boolean} [silent=false] 是否为后台静默刷新，静默刷新时不开启 loading 骨架屏以保留当前 DOM 挂载和滚动位置
     */
    const fetchMajorEvents = React.useCallback((silent = false) => {
        if (!silent) {
            setLoading(true);
        }
        eventsApi.getMajorEvents(displayLimit)
            .then((data) => {
                if (Array.isArray(data.events)) {
                    setMajorEvents((prev) => {
                        // 精细化比对数据，若完全一致则不触发 React 引用更新，防范无意义的子组件重绘
                        const isIdentical = prev.length === data.events.length &&
                            prev.every((evt, idx) => (evt.id || evt.event_id) === (data.events[idx].id || data.events[idx].event_id));
                        if (isIdentical) {
                            return prev;
                        }
                        return data.events;
                    });
                }
                setLoading(false);
            })
            .catch((err) => {
                console.error('Failed to fetch major events:', err);
                setLoading(false);
            });
    }, [eventsApi, displayLimit]);

    // 挂载或上限设置改变时触发载入（非静默，显示加载状态）
    React.useEffect(() => {
        fetchMajorEvents(false);
    }, [fetchMajorEvents]);

    // 当触发外部业务刷新信号时触发静默重拉，不让界面闪烁或导致 Scroll Container 被卸载。
    // - 挂载首帧 refreshSignal 为 null（mount 时 lastEvent 尚无值）时跳过本次调用；
    // - 仅处理「后续发生变化」的信号：displayLimit 变化会重建 fetchMajorEvents，
    //   导致两个 effect 同时触发，这里通过 lastSignalRef 判别真正的信号变化
    //   （ref 与当前信号相同则跳过），避免与上方「挂载/上限变化」的非静默拉取重复请求。
    React.useEffect(() => {
        if (refreshSignal == null) return;
        if (refreshSignal === lastSignalRef.current) return;
        lastSignalRef.current = refreshSignal;
        fetchMajorEvents(true);
    }, [refreshSignal, fetchMajorEvents]);

    return {
        majorEvents,                     // 过滤出的危险事件队列
        loading,                         // 加载状态
        refreshMajorEvents: fetchMajorEvents // 手动触发强制更新的方法
    };
}

// 挂载至全局，供外部大屏与分析视图直接消费
window.useMajorEvents = useMajorEvents;
