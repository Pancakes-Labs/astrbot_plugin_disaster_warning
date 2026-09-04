/**
 * 模块名称：系统健康与服务状态仪表盘视图
 * 文件路径：admin/js/views/StatusView.jsx
 * 功能描述：作为插件管理端进入的首页面板，提供全局数据监听器的运行状态汇报。
 *           包含：跑马灯滚动通知、系统健康卡片、简明数据量卡片、全局网络连接拓扑图、
 *           地震速报模块，以及快捷维护面板（重连数据源、刷新及重置统计等）。
 */

const { Box, Button, Typography } = MaterialUI;

/**
 * 系统健康与运行状态视图主组件
 */
function StatusView() {
    // 从应用的状态总线订阅全局运行状态
    const { state, refreshData, fetchConnections, fetchConfig } = useAppContext();
    const { status, wsConnected } = state; 
    
    // 本地操作中按钮的交互加载状态
    const [reconnecting, setReconnecting] = React.useState(false);
    const [refreshing, setRefreshing] = React.useState(false);
    const [resettingStats, setResettingStats] = React.useState(false);
    const [reloadingPlugin, setReloadingPlugin] = React.useState(false);
    const [restartingAstrbot, setRestartingAstrbot] = React.useState(false);
    
    // 获取全局 WebSocket 连接发送消息的方法
    const { sendMessage } = useWebSocket(); 
    // 使用全局 Toast 提示系统反馈
    const { showToast } = useToast(); 
    const statusApi = window.DisasterStatusApi;

    /**
     * 执行控制台的全量刷新拉取
     */
    const refreshAll = async () => {
        setRefreshing(true);
        try {
            // 1. 同时拉取三个核心接口，缩短网络阻滞时间
            await Promise.all([
                refreshData(),
                fetchConnections(),
                fetchConfig()
            ]);
            
            // 2. 通过 Web Socket 连接通道发出实时通知同步刷新，若网络不可达则忽略
            if (wsConnected) {
                const sent = sendMessage({ type: 'refresh' });
                if (!sent) {
                    console.warn('[StatusView] WebSocket 未连接，跳过实时刷新请求');
                }
            } else {
                console.warn('[StatusView] WebSocket 未连接，仅通过 HTTP API 刷新');
            }
            
            // 3. 略微延时，防止高频点击产生的闪烁并让旋转动画自然平稳过渡
            await new Promise(resolve => setTimeout(resolve, 500));
        } catch (e) {
            console.error('刷新数据失败:', e);
        } finally {
            // 解锁刷新加载动作
            setRefreshing(false);
        }
    };

    /**
     * 手动触发全部活跃数据接口重新连接
     */
    const handleReconnect = async () => {
        // 安全拦截：若当前所有接入源均显示正常，弹出警告以防误操作中断线上真实订阅
        if (status.activeConnections === status.totalConnections && status.totalConnections > 0) {
            if (!confirm('当前所有连接均正常，确定要强制执行重连操作吗？\n这可能会导致短暂的连接中断。')) {
                return;
            }
        }

        setReconnecting(true);
        try {
            const result = await statusApi.reconnect(state.config.apiUrl || '');

            // 重连需要物理网络建立过程，延迟 1 秒后刷新数据并通知用户
            setTimeout(() => {
                refreshData();
                setReconnecting(false);
                showToast(result?.message || '重连操作已触发', 'success');
            }, 1000);
        } catch (e) {
            console.error('Reconnect failed:', e);
            showToast('请求失败，请检查网络连接', 'error');
            setReconnecting(false);
        }
    };

    /**
     * 手动清除数据库的统计历史信息（高危操作，配有密码保护机制）
     */
    const handleResetStatistics = async () => {
        // 第一道关卡：二次弹窗确认
        const ok = confirm('⚠️ 确定要清除插件统计数据吗？\n\n该操作会重置统计信息、图表数据、事件列表等（不可恢复）。');
        if (!ok) return;

        // 第二道关卡：输入控制台管理密码，增强安全阻断
        const password = prompt('请输入管理端密码以确认本次清除操作：', '');
        if (password === null) return;
        if (!password.trim()) {
            showToast('已取消清除：未输入管理端密码', 'warning');
            return;
        }

        setResettingStats(true);
        try {
            const result = await statusApi.resetStatistics(password);
            // 成功后，将本地数据全部刷空，防止缓存脏数据
            await refreshAll();
            showToast(result?.message || '统计数据已清除', 'success');
        } catch (e) {
            console.error('Reset statistics failed:', e);
            showToast(e.message || '清除失败，请检查网络连接', 'error');
        } finally {
            setResettingStats(false);
        }
    };

    /**
     * 重载灾害预警插件（等价于 /灾害预警重启 指令）
     */
    const handleReloadPlugin = async () => {
        if (!confirm('确定要重载灾害预警插件吗？\n重载过程中服务会短暂中断。')) {
            return;
        }

        setReloadingPlugin(true);
        try {
            const result = await statusApi.reloadPlugin();
            showToast(result?.message || '正在重载灾害预警插件', 'success');
            // 重载会重建 Web 管理端，短暂等待后刷新页面数据
            setTimeout(() => {
                refreshAll();
            }, 3000);
        } catch (e) {
            console.error('Reload plugin failed:', e);
            showToast(e.message || '重载插件失败，请检查网络连接', 'error');
        } finally {
            setReloadingPlugin(false);
        }
    };

    /**
     * 重启 AstrBot 进程（等价于 /重启AstrBot 指令）
     */
    const handleRestartAstrbot = async () => {
        if (!confirm('确定要重启 AstrBot 吗？\n重启期间 WebUI 会短暂不可用。')) {
            return;
        }

        setRestartingAstrbot(true);
        try {
            const result = await statusApi.restartAstrbot();
            showToast(result?.message || '已触发 AstrBot 重启', 'success');
        } catch (e) {
            console.error('Restart AstrBot failed:', e);
            showToast(e.message || '重启 AstrBot 失败，请检查网络连接', 'error');
        } finally {
            setRestartingAstrbot(false);
        }
    };

    return (
        <Box>
            {/* 网格主容器 */}
            <div className="dashboard-grid">
                {/* 顶部跑马灯：滚动呈现最近发生的紧急地震或天气速报信息 */}
                <div className="span-12">
                    <NewsTicker />
                </div>

                {/* 运行健康指标卡片 */}
                <div className="span-4">
                    <StatusCard />
                </div>
                
                {/* 核心频次计数指标卡片 */}
                <div className="span-4">
                    <StatsCard />
                </div>
                
                {/* 右侧：快捷配置与指令维护面板 */}
                <div className="span-4">
                    <div className="card status-quick-actions-card">
                        <Box className="status-card-header">
                            <div className="status-card-icon status-card-icon--actions">🚀</div>
                            <Typography variant="h6" className="status-card-title">快捷操作</Typography>
                        </Box>
                        
                        <Box className="status-quick-actions-list">
                            {/* 1. 强制重启并重新拉取各个数据接收端口 */}
                            <button
                                className={`btn btn-action status-action-button ${!status.running ? 'is-disabled' : ''}`}
                                onClick={handleReconnect}
                                disabled={reconnecting || !status.running}
                                title="强制重连所有已启用但离线的数据源"
                            >
                                {reconnecting ? (
                                    <>
                                        <span className="spinner status-action-spinner"></span>
                                        处理中...
                                    </>
                                ) : (
                                    <>
                                        <span className="status-action-icon">🔌</span>
                                        手动重连数据源
                                    </>
                                )}
                            </button>

                            {/* 2. 主动同步服务端的所有当前缓存与时区设置 */}
                            <button
                                className="btn btn-action status-action-button"
                                onClick={refreshAll}
                                disabled={refreshing}
                            >
                                {refreshing ? (
                                    <>
                                        <span className="spinner status-action-spinner"></span>
                                        刷新中...
                                    </>
                                ) : (
                                    <>
                                        <span className="status-action-icon">🔄</span>
                                        刷新控制台数据
                                    </>
                                )}
                            </button>

                            {/* 3. 清除底层历史统计，重归初始白卷 */}
                            <button
                                className={`btn btn-action status-action-button ${!status.running ? 'is-disabled' : ''}`}
                                onClick={handleResetStatistics}
                                disabled={resettingStats || !status.running}
                                title="清除插件统计数据（等价于 /灾害预警统计清除）"
                            >
                                {resettingStats ? (
                                    <>
                                        <span className="spinner status-action-spinner"></span>
                                        清除中...
                                    </>
                                ) : (
                                    <>
                                        <span className="status-action-icon">🧹</span>
                                        一键清除统计
                                    </>
                                )}
                            </button>

                            {/* 4. 重载灾害预警插件（等价于 /灾害预警重启 指令） */}
                            <button
                                className={`btn btn-action status-action-button ${!status.running ? 'is-disabled' : ''}`}
                                onClick={handleReloadPlugin}
                                disabled={reloadingPlugin || !status.running}
                                title="重载灾害预警插件（等价于 /灾害预警重启）"
                            >
                                {reloadingPlugin ? (
                                    <>
                                        <span className="spinner status-action-spinner"></span>
                                        重载中...
                                    </>
                                ) : (
                                    <>
                                        <span className="status-action-icon">♻️</span>
                                        重载插件
                                    </>
                                )}
                            </button>

                            {/* 5. 重启 AstrBot 进程（等价于 /重启AstrBot 指令） */}
                            <button
                                className={`btn btn-action status-action-button ${!status.running ? 'is-disabled' : ''}`}
                                onClick={handleRestartAstrbot}
                                disabled={restartingAstrbot || !status.running}
                                title="重启 AstrBot 进程（等价于 /重启AstrBot）"
                            >
                                {restartingAstrbot ? (
                                    <>
                                        <span className="spinner status-action-spinner"></span>
                                        重启中...
                                    </>
                                ) : (
                                    <>
                                        <span className="status-action-icon">🔄</span>
                                        重启 AstrBot
                                    </>
                                )}
                            </button>
                        </Box>
                    </div>
                </div>

                {/* 数据源长连接状态网络监控图表 */}
                <div className="span-12">
                    <ConnectionsGrid />
                </div>

                {/* 通道健康：90 天条带 + 历史事故 */}
                <div className="span-12">
                    <ConnectionHealthPanel />
                </div>

                {/* 地震预警（EEW）状态卡片 */}
                <div className="span-12">
                    <EewStatusCard />
                </div>
            </div>
        </Box>
    );
}
