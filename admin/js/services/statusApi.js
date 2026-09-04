(() => {
    /**
     * 系统状态、健康连通性及后端管理操作的接口定义类。
     * 
     * 主要交互逻辑：
     * - getStatus: 心跳检测，获取硬件负载、软件版本及子源检测情况。
     * - getStatistics: 载入全量灾害分析指标。
     * - getConnections: 获取 WebSocket 底层网络拓扑的延迟延迟状态。
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
        /**
         * 连接健康 Statuspage
         * @param {number} days 历史窗口天数
         * @param {'full'|'live'} mode full=条带+事故；live=仅实时态
         */
        getConnectionHealth: (days = 90, mode = 'full') => client.request('/connection-health', {
            query: { days, mode },
        }),
        getConfig: () => client.request('/config'),
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
        /**
         * 重载灾害预警插件（等价于 /灾害预警重启 指令）
         */
        reloadPlugin: () => client.request('/plugin/reload', { method: 'POST' }),
        /**
         * 重启 AstrBot 进程（等价于 /重启AstrBot 指令）
         */
        restartAstrbot: () => client.request('/astrbot/restart', { method: 'POST' }),
    };

    window.DisasterStatusApi = statusApi;
})();
