const { Box, Typography } = MaterialUI;

/**
 * 分页工具条组件 (PaginationBar)
 * 提供通用的列表数据分页与跳转控制器，支持自定义页大小、页码直接跳转及基于省略号的多页码导航展示。
 * 
 * @param {Object} props
 * @param {number} props.total 数据项总条数
 * @param {number} props.totalPages 计算出的总页数
 * @param {number} props.currentPage 当前所处的激活页码 (从1开始计)
 * @param {number} props.pageSize 每页展示的数据量
 * @param {number[]} props.pageSizeOptions 可供选择的每页数据量列表 (如 [10, 20, 50])
 * @param {Function} props.onPageSizeChange 当每页数据量变化时的回调函数，接收新的 pageSize 作为参数
 * @param {string|number} props.pageInput 跳转输入框中当前的文本值
 * @param {Function} props.onPageInputChange 输入框输入内容变化时的回调函数，接收新的文本值
 * @param {boolean} props.canJump 标识当前输入的内容是否满足合法跳转条件
 * @param {Function} props.onPageJump 执行页码跳转的回调函数
 * @param {Array<number|string>} props.paginationItems 分页条的展示序列，包含页码数字和省略号占位符 ('…')
 * @param {Function} props.goToPage 页面跳转触发函数，接收目标页码作为参数
 */
function PaginationBar({ 
    total, 
    totalPages, 
    currentPage, 
    pageSize, 
    pageSizeOptions, 
    onPageSizeChange, 
    pageInput, 
    onPageInputChange, 
    canJump, 
    onPageJump, 
    paginationItems, 
    goToPage 
}) {
    // 如果无任何数据，则不渲染分页栏
    if (total <= 0) return null;

    return (
        <Box className="pagination-bar">
            {/* 分页配置与跳转控制行 */}
            <Box className="pagination-bar__row">
                {/* 左侧：每页条数选择器与当前页数概览 */}
                <Box className="pagination-bar__control-group">
                    <Typography variant="body2" className="pagination-bar__muted" component="label" htmlFor="pagination-page-size">每页</Typography>
                    <select
                        id="pagination-page-size"
                        value={pageSize}
                        onChange={(e) => onPageSizeChange(Number(e.target.value))}
                        className="pagination-input"
                        aria-label="每页条数"
                    >
                        {pageSizeOptions.map((size) => (
                            <option key={size} value={size}>{size} 条</option>
                        ))}
                    </select>
                    <Typography variant="body2" className="pagination-bar__subtle">
                        第 {currentPage} / {Math.max(totalPages, 1)} 页
                    </Typography>
                </Box>

                {/* 右侧：页码直接跳转输入框及跳转按钮 */}
                <Box className="pagination-bar__control-group">
                    <input
                        type="number"
                        min={1}
                        max={Math.max(totalPages, 1)}
                        value={pageInput}
                        onChange={(e) => onPageInputChange(e.target.value)}
                        onKeyDown={(e) => {
                            // 监听回车键，当按下回车时直接执行跳转
                            if (e.key === 'Enter') onPageJump();
                        }}
                        placeholder="跳转页码"
                        aria-label="跳转到指定页"
                        className="pagination-input pagination-input--jump"
                    />
                    <button
                        onClick={onPageJump}
                        disabled={!canJump}
                        className="pagination-button"
                        aria-label="执行页码跳转"
                    >
                        跳转
                    </button>
                </Box>
            </Box>

            {/* 页码选择按钮导航区域（仅在总页数大于 1 时渲染） */}
            {totalPages > 1 && (
                <Box className="pagination-bar__pages" role="navigation" aria-label="分页导航">
                    {/* 上一页按钮 */}
                    <button
                        onClick={() => goToPage(currentPage - 1)}
                        disabled={currentPage <= 1}
                        className="pagination-button"
                        aria-label="上一页"
                    >
                        ‹
                    </button>
                    
                    {/* 页码列表与省略号 */}
                    {paginationItems.map((item, idx) => (
                        typeof item === 'number' ? (
                            <button
                                key={`page-${item}`}
                                onClick={() => goToPage(item)}
                                aria-label={`第 ${item} 页`}
                                aria-current={item === currentPage ? 'page' : undefined}
                                className={`pagination-button pagination-button--page ${item === currentPage ? 'pagination-button--active' : ''}`}
                            >
                                {item}
                            </button>
                        ) : (
                            <span key={`ellipsis-${idx}`} className="pagination-ellipsis" aria-hidden="true">…</span>
                        )
                    ))}
                    
                    {/* 下一页按钮 */}
                    <button
                        onClick={() => goToPage(currentPage + 1)}
                        disabled={currentPage >= totalPages}
                        className="pagination-button"
                        aria-label="下一页"
                    >
                        ›
                    </button>
                </Box>
            )}
        </Box>
    );
}
