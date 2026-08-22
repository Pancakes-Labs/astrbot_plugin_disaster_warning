/**
 * @file useStatusUptimeEffect.js
 * @description 服务运行时长本地跳秒定时器 Hook。
 * 
 * 性能优化与交互逻辑说明：
 * 1. 节省带宽：由于防灾服务的运行时长是一个秒级变化的指标，如果通过 API 轮询获取，会给宿主进程带来巨大的网络通信和计算损耗。
 *    因此，我们在初次握手时获取一次精确的启动时间戳 (startTime)，随后在前端利用 setInterval 本地累加计时。
 * 2. 溢出容错：本地时钟和服务器时钟可能存在微小的毫秒级时限差。如果计算出的运行时长差值小于 0，
 *    自动做退化容错处理，展示为“刚刚”以保障用户体验。
 * 3. 动态时间切分：将差值总秒数精密换算为“日、小时、分钟、秒”的多级复合友好展示字符串，并随时更新至全局状态树。
 */
function useStatusUptimeEffect({
    running,   // 服务是否正在执行监听（若已停止，则停止跳秒以保留最后一个存活状态）
    startTime, // Date 实例，服务端返回的插件加载首帧绝对时间
    dispatch,  // 全局 Action 发射器
}) {
    // 使用毫秒时间戳作为 effect 依赖，避免每次 WS/API 推送 new Date() 造成引用变化、计时器反复重启
    const startTimeMs = startTime instanceof Date && !Number.isNaN(startTime.getTime())
        ? startTime.getTime()
        : null;

    React.useEffect(() => {
        // 如果服务器尚未返回启动时间戳，或者防灾主线程已经停止运行，则不激活计时器
        if (!startTimeMs || !running) return;

        const tick = () => {
            const diff = Math.floor((Date.now() - startTimeMs) / 1000); // 换算为秒级差值

            // 时差负数溢出拦截
            if (diff < 0) {
                dispatch({ type: window.AppActionTypes.UPDATE_STATUS, payload: { uptime: '刚刚' } });
                return;
            }

            // 时分秒多级分发算法
            const days = Math.floor(diff / 86400);               // 1天 = 86400秒
            const hours = Math.floor((diff % 86400) / 3600);     // 1小时 = 3600秒
            const minutes = Math.floor((diff % 3600) / 60);      // 1分钟 = 60秒
            const seconds = diff % 60;

            let str = '';
            if (days > 0) str += `${days}天`;
            if (hours > 0) str += `${hours}小时`;
            if (minutes > 0) str += `${minutes}分`;
            str += `${seconds}秒`;

            // 动态注入至状态树，触发头部等 UI 组件的高流畅度无抖动跑秒
            dispatch({ type: window.AppActionTypes.UPDATE_STATUS, payload: { uptime: str } });
        };

        // 立即执行一次，避免 setInterval 首秒空白或依赖服务端 uptime 回填导致跳变
        tick();
        const timer = setInterval(tick, 1000);

        // 卸载或状态解绑时，注销 CPU 定时轮询器
        return () => clearInterval(timer);
    }, [dispatch, running, startTimeMs]);
}
