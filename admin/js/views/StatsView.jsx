/**
 * 模块名称：数据统计仪表盘视图
 * 文件路径：admin/js/views/StatsView.jsx
 * 功能描述：作为灾害与预警系统的核心报表中心。使用网格布局汇集了系统内的各种多维度可视化组件：
 *           包含震级区间分布图、系统总体数据摘要卡片、历史最强震级归档、
 *           活动频度年度热力图、高发地域/预警类型/数据源贡献排名的 Top 10 榜单以及后端资源统计等。
 */

const { Box } = MaterialUI;

/**
 * 数据统计视图主组件
 * 基于 React Context 拉取全局统计包并分发，构建网格化的统计图表面板
 */
function StatsView() {
    // 从应用的状态总线中提取解耦后的统计信息状态
    const { state } = useAppContext();
    const { stats } = state;
    
    // 安全提取底层各个维度的排行分析数组，若数据未加载完成则降级为防崩空数组
    const sources = stats && stats.dataSources ? stats.dataSources : [];
    const eqRegions = stats && stats.earthquakeRegions ? stats.earthquakeRegions : [];
    const weatherTypes = stats && stats.weatherTypes ? stats.weatherTypes : [];
    const weatherRegions = stats && stats.weatherRegions ? stats.weatherRegions : [];
    const topSessions = stats && stats.topSessions ? stats.topSessions : [];

    return (
        <Box>
            {/* 网格化大盒子，定义行高比例与自适应折叠布局 */}
            <div className="dashboard-grid">
                {/* 第一栏大板块：地震震级历史占比与近期核心指标 */}
                <div className="stats-overview-grid">
                    {/* 左侧：震级分布主图 + 下方并排极值卡片 */}
                    <div className="stats-overview-main">
                        <div className="stats-fill-column stats-overview-main-stack">
                            <MagnitudeChart className="stats-flex-fill" showNote={false} />
                            <div className="stats-peak-pair">
                                <div className="stats-peak-pair-item">
                                    <MaxMagCard />
                                </div>
                                <div className="stats-peak-pair-item">
                                    <SnetMaxCard />
                                </div>
                            </div>
                        </div>
                    </div>
                    {/* 右侧：总览指标 + 统计口径说明（与下方极值卡片等高） */}
                    <div className="stats-overview-side">
                        <div className="stats-fill-column stats-flex-fill">
                            <StatsCard className="stats-flex-fill" />
                        </div>
                        <div className="stats-fill-column stats-note-slot">
                            <StatsNoteCard className="stats-flex-fill" />
                        </div>
                    </div>
                </div>

                {/* 时间趋势统计：历史走势折线图 */}
                <div className="span-12">
                    <TrendChart className="stats-chart-tall" />
                </div>
                
                {/* 时间频度统计：全年度的日历打点活跃热力图 */}
                <div className="span-12">
                    <CalendarHeatmap className="stats-chart-heatmap" />
                </div>

                {/* 排行榜单模块（第二行）：三等分网格卡片 */}
                {/* 1. 地震高发地区统计 */}
                <div className="span-4">
                    <TopListCard title="国内地震高发地 (TOP 10)" icon="📍" data={eqRegions} tone="earthquake" />
                </div>
                {/* 2. 天气警报类型频率统计 */}
                <div className="span-4">
                    <TopListCard title="气象预警类型 (TOP 10)" icon="⛈️" data={weatherTypes} tone="weather" />
                </div>
                {/* 3. 各大开放 API 数据源提供的数据份额贡献度统计 */}
                <div className="span-4">
                    <TopListCard title="数据源贡献 (TOP 10)" icon="📡" data={sources} tone="source" />
                </div>

                {/* 排行榜单模块（第三行）：包含气象区域、气象警报级别和会话推送统计 */}
                {/* 1. 气象预警频发地理区域统计 */}
                <div className="span-4">
                    <TopListCard title="气象预警地区分布 (TOP 10)" icon="🗺️" data={weatherRegions} tone="region" />
                </div>
                {/* 2. 气象警报按级别（蓝、黄、橙、红）数量占比柱图 */}
                <div className="span-4">
                    <WeatherLevelCard />
                </div>
                {/* 3. 会话推送统计 Top 10 */}
                <div className="span-4">
                    <SessionPushCard />
                </div>

                {/* 排行榜单模块（第四行）：风王榜、台风强度等级分布等 */}
                {/* 1. 台风风王榜 Top 10 */}
                <div className="span-4">
                    <TyphoonWindKingCard />
                </div>
                {/* 2. 台风强度等级环形比例图 */}
                <div className="span-4">
                    <TyphoonLevelCard />
                </div>
                {/* 3. 系统后台运行日志大小、占用空间与存储信息统计 */}
                <div className="span-4">
                    <LogStatsCard />
                </div>

                {/* 底部备注与说明横栏 */}
                <div className="span-12">
                    <div className="card stats-summary-card">
                        <h4 className="stats-summary-title">📊 数据摘要</h4>
                        <p className="stats-summary-text">
                            统计信息会自动实时更新。您可以从这些图表中直观的观察到灾害活动的强度分布和频率。
                        </p>
                    </div>
                </div>
            </div>
        </Box>
    );
}
