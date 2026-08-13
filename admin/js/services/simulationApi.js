(() => {
    /**
     * 模拟预警系统 API 服务层
     * 负责与后端 /api/simulation/* 系列接口交互：
     * - getSchema: 拉取全灾种 × 全源参数 Schema（驱动前端动态表单）
     * - listFlows / saveFlow / deleteFlow: 模拟流草稿 CRUD
     * - runFlow: 整流执行（后台任务，返回 run_id）
     * - getRun: 查询执行进度/结果
     * - runStep: 单步试执行（预览或发送）
     * - listRuns: 最近执行记录
     * - suggestWeatherCode: 根据标题自动生成气象预警编码
     */
    const client = window.DisasterApiClient;

    const simulationApi = {
        getSchema: () => client.request('/simulation/schema'),

        listFlows: () => client.request('/simulation/flows'),
        saveFlow: (flow) => client.request('/simulation/flows', {
            method: 'POST',
            body: flow,
        }),
        // 路径参数必须 URL 编码：flowId/runId 若含 / ? # 等字符会破坏请求路径
        deleteFlow: (flowId) => client.request(`/simulation/flows/${encodeURIComponent(flowId)}`, {
            method: 'DELETE',
        }),

        runFlow: (data) => client.request('/simulation/run', {
            method: 'POST',
            body: data,
        }),
        getRun: (runId) => client.request(`/simulation/run/${encodeURIComponent(runId)}`),
        cancelRun: (runId) => client.request(`/simulation/run/${encodeURIComponent(runId)}/cancel`, {
            method: 'POST',
        }),
        listRuns: () => client.request('/simulation/runs'),

        runStep: (data) => client.request('/simulation/run/step', {
            method: 'POST',
            body: data,
        }),

        // 实时推文预览：复用模拟构建 + 消息 + 规则链链路
        preview: (data) => client.request('/simulation/preview', {
            method: 'POST',
            body: data,
        }),

        suggestWeatherCode: (query) => client.request('/simulation/weather-code-suggest', {
            method: 'GET',
            query,
        }),
    };

    window.DisasterSimulationApi = simulationApi;
})();
