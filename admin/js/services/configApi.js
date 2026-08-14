(() => {
    /**
     * 配置管理模块的接口定义类。
     * 
     * 主要方法：
     * - getConfigSchema: 获取表单中每个项的中文说明、控件类型、默认值及折叠嵌套路径信息。
     * - getFullConfig: 获取全局配置参数。
     * - updateConfig: 全量更新并保存全局配置。
     * - listSessionConfigs: 拉取所有配置了覆盖参数的群组列表。
     * - getSessionConfig: 拉取特定群组的覆盖配置详情。
     * - updateSessionConfig: 保存特定群组的覆盖配置。
     * - resetSessionConfig: 一键删除特定群组的覆盖配置，回归继承全局。
     * - exportFullBackup: 导出全量/自定义备份压缩包 (ZIP)。
     * - importFullBackup: 导入全量备份压缩包 (ZIP)。
     * - exportSessionOverrides: 导出仅会话差异配置 (JSON)。
     * - importSessionOverrides: 导入会话差异配置 (JSON)。
     */
    const client = window.DisasterApiClient;

    const configApi = {
        getConfigSchema: () => client.request('/config-schema'),
        getFullConfig: () => client.request('/full-config'),
        updateConfig: (config) => client.request('/full-config', {
            method: 'POST',
            body: config,
        }),
        listSessionConfigs: () => client.request('/session-config/sessions'),
        getSessionConfig: (umo) => client.request(`/session-config/${encodeURIComponent(umo)}`),
        updateSessionConfig: (umo, payload) => client.request(`/session-config/${encodeURIComponent(umo)}`, {
            method: 'POST',
            body: payload,
        }),
        resetSessionConfig: (umo) => client.request(`/session-config/${encodeURIComponent(umo)}`, {
            method: 'DELETE',
        }),
        /**
         * 导出备份压缩包 (ZIP)
         * @param {string[]} targets 可选备份项
         * @returns {Promise<Blob>}
         */
        exportFullBackup: (targets = null) => {
            const query = targets ? { targets: targets.join(',') } : null;
            return client.request('/backup/export', { responseType: 'blob', query });
        },
        /**
         * 导入全量备份压缩包 (ZIP)
         * @param {File} file 
         * @returns {Promise<any>}
         */
        importFullBackup: (file) => {
            const formData = new FormData();
            formData.append('file', file);
            return client.request('/backup/import', {
                method: 'POST',
                body: formData,
                headers: {}
            });
        },
        /**
         * 导出仅会话差异配置 (JSON)
         * @returns {Promise<any>}
         */
        exportSessionOverrides: () => client.request('/backup/session-overrides'),
        /**
         * 导入会话差异配置 (JSON)
         * @param {any} payload 
         * @param {boolean} merge 
         * @returns {Promise<any>}
         */
        importSessionOverrides: (payload, merge = true) => client.request(`/backup/session-overrides?merge=${merge}`, {
            method: 'POST',
            body: payload
        })
    };

    window.DisasterConfigApi = configApi;
})();
