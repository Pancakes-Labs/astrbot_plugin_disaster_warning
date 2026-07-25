(() => {
    /**
     * 系统状态、健康连通性及后端管理操作的接口定义类。
     * 
     * 主要交互逻辑：
     * - getStatus: 心跳检测，获取硬件负载、软件版本及子源检测情况。
     * - getStatistics: 载入全量灾害分析指标。
     * - getConnections: 获取 WebSocket 底层网络拓扑的延迟延迟状态。
     * - sendSimulation: 向后端发送自定义模拟发震或警报测试参数。
     * - getTrend: 拉取指定小时数内发生警报的波动趋势数据。
     * - getHeatmap: 载入指定天数内每日活动频次，用以绘制贡献热力图。
     * - reconnect: 强制后端对发生断线的外部数据源发起断线重连。
     * - openPluginDir / openLogDir: 通知服务器上的操作系统调用资源管理器打开对应的物理文件目录。
     * - resetStatistics: 重置清空历史累计数据统计，需要提供管理员密码进行校验。
     */
    const client = window.DisasterApiClient;

    const statusApi = {
        getStatus: () => client.request('/status'),
        getStatistics: () => client.request('/statistics'),
        getConnections: () => client.request('/connections'),
        /** 连接健康 Statuspage：90 天条带 + 事故 */
        getConnectionHealth: (days = 90) => client.request('/connection-health', {
            query: { days },
        }),
        getConfig: () => client.request('/config'),
        sendSimulation: (data) => client.request('/simulate', {
            method: 'POST',
            body: data,
        }),
        getSimulationParams: () => client.request('/simulation-params'),
        getGeoLocation: () => client.request('/geolocate'),
        getTrend: (hours = 24) => client.request('/trend', { query: { hours } }),
        getHeatmap: (days = 180, year = null) => client.request('/heatmap', { query: { days, year } }),
        reconnect: (baseUrl = '') => client.request('/reconnect', { method: 'POST', baseUrl }),
        openPluginDir: () => client.request('/open-plugin-dir', { method: 'POST', unwrap: false }),
        openLogDir: (baseUrl = '') => client.request('/open-log-dir', { method: 'POST', baseUrl, unwrap: false }),
        resetStatistics: (password = '') => client.request('/statistics/reset', {
            method: 'POST',
            body: { password },
        }),
    };

    window.DisasterStatusApi = statusApi;
})();
