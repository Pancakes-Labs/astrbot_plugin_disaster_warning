/**
 * 模块名称：灾害与天气事件历史视图组件
 * 功能描述：此视图用作事件记录面板的顶层组件。它将横向时间轴、
 *          天气预警查询接口和详尽的灾害事件筛选列表聚合展示在统一的界面中。
 */

const { Box } = MaterialUI;

/**
 * 灾害与天气事件视图主组件
 * 采用流式垂直布局展示时间轴、天气面板和主列表
 */
function EventsView() {
    return (
        // 使用 MaterialUI 的 Box 基础容器包裹整体结构
        <Box>
            {/* 顶部的横向滚动时间轴区块，展示近期主要灾害的发生顺序与时序流 */}
            <div className="events-view-section">
                <HorizontalTimeline />
            </div>
            
            {/* 中间的天气查询区块，支持手动搜索和定位特定地区的气象警告信息 */}
            <div className="events-view-section">
                <WeatherQueryPanel />
            </div>

            {/* 台风信息快捷查询：与 /台风信息查询 指令等价，优先 EQSC 并支持本地回退 */}
            <div className="events-view-section">
                <TyphoonQueryPanel />
            </div>
            
            {/* 底部的灾害事件主列表组件，包含强大的多维度组合筛选及事件折叠折叠面板 */}
            <EventsList />
        </Box>
    );
}
