const { useEffect, useRef } = React;

/**
 * @file useWebSocket.js
 * @description 管理控制台与 AstrBot 宿主后端 WebSocket 全双工实时通信信道的 React 钩子。
 * 
 * 核心架构与业务协议解析：
 * 1. 引用稳定性闭包 (Ref Callback Pattern)：通过 useRef(null) 缓存 WS 消息分发函数 handleWsMessageRef，
 *    在每次渲染时更新此引用，使得 WS 的 onMessage 订阅句柄始终能闭包读取到最新的 React 状态，
 *    同时避免了订阅函数频繁销毁重建导致的网络断线与重连风暴。
 * 2. 多态通信协议解分：
 *    - event (带 new_event 字段)：表示发生了实时灾害警报（如最新一报地震速报），触发 ADD_EVENT 排重压入。
 *    - full_update / update：服务端心跳或数据更新。
 *    解构为运行心跳与 EEW 本地轮询状态、图表大屏统计、网络拓扑质量与管理员信箱。实现一次性分流分发。
 */
function useWebSocket() {
    const { state, dispatch } = useAppContext();
    const handleWsMessageRef = useRef(null);
    
    // 将实际消息处理逻辑赋予可变引用，保证订阅机制只需注册一次即可响应最新状态
    handleWsMessageRef.current = (msg) => {
        if (msg.type === 'full_update' || msg.type === 'update' || msg.type === 'event') {
            const data = msg.data;
            
            // 实时灾害强预警通知拦截
            if (msg.type === 'event' && msg.new_event) {
                console.log('[WS] 收到新事件:', msg.new_event);
                dispatch({ type: window.AppActionTypes.ADD_EVENT, payload: msg.new_event });
            }
            if (!data) return;

            // 1. 同步系统负载与运行态
            // 传入 previousStartTime，避免每次推送重建 startTime 引用并覆盖本地 uptime 跳秒
            if (data.status) {
                dispatch({
                    type: window.AppActionTypes.UPDATE_STATUS,
                    payload: window.toStatusUpdate(
                        data.status,
                        state.status.version,
                        state.status.startTime,
                    ),
                });
            }
            // 2. 同步图表多维分析数据
            if (data.statistics) dispatch({ type: window.AppActionTypes.UPDATE_STATS, payload: data.statistics });
            // 3. 同步三方连通性与网络延迟指标
            if (data.connections) dispatch({ type: window.AppActionTypes.UPDATE_CONNECTIONS, payload: data.connections });
            // 4. 同步管理员消息盒子列表与元数据
            if (data.notifications) {
                dispatch({ type: window.AppActionTypes.SET_NOTIFICATIONS, payload: data.notifications.items || [] });
                dispatch({ type: window.AppActionTypes.SET_NOTIFICATIONS_META, payload: data.notifications.meta || null });
            }
            if (data.notificationsMeta) {
                dispatch({ type: window.AppActionTypes.SET_NOTIFICATIONS_META, payload: data.notificationsMeta });
            }
        }
    };

    useEffect(() => {
        // 向全局 WebSocket 客户端客户端订阅心跳与状态监听事件
        const unsubscribe = window.WebSocketClient.subscribe({
            onConnected: () => dispatch({ type: window.AppActionTypes.SET_WS_CONNECTED, payload: true }),
            onDisconnected: () => dispatch({ type: window.AppActionTypes.SET_WS_CONNECTED, payload: false }),
            onMessage: (msg) => handleWsMessageRef.current(msg),
        });
        
        // 返回取消订阅的回调，防止重复添加句柄引起的推送串流
        return unsubscribe;
    }, [dispatch]);

    return {
        wsConnected: state.wsConnected, // 供 Header 组件渲染 WebSocket 连接芯片状态
        events: state.events,           // 供时间线与地图模块渲染实时事件
        sendMessage: window.WebSocketClient.send, // 发送自定义模拟测试协议或交互命令的入口
    };
}
