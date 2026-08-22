const { Snackbar, Alert, IconButton } = MaterialUI;
const { useState, useEffect, createContext, useContext } = React;

/**
 * Toast 通知组件 (Toast Component & Context)
 * 采用全局 Context 的形式提供统一的页面级轻量提示系统，用于替代原生 alert() 等阻塞性弹窗。
 * 支持多种预设颜色等级（成功、错误、警告、信息），并伴随淡入淡出动效。
 */

// 创建 Toast 通信上下文，供 useToast() 钩子获取方法
const ToastContext = createContext();

/**
 * Toast 上下文提供者组件 (ToastProvider)
 * 包含通知队列状态的管理逻辑，负责通知创建、销毁及定时器自动清除。
 *
 * @param {Object} props
 * @param {React.ReactNode} props.children 包裹的子组件树
 */
function ToastProvider({ children }) {
    // 存储当前活跃的 Toast 通知项序列
    const [toasts, setToasts] = useState([]);

    /**
     * 向队列末尾推入一个全新的 Toast 提示
     *
     * @param {string} message 提示的文本内容
     * @param {string} [severity='info'] 警告级别，可选值包括: 'success' | 'error' | 'warning' | 'info'
     * @param {number} [duration=4000] 显示的持续毫秒数，设为 0 时则需手动关闭
     */
    const showToast = (message, severity = 'info', duration = 4000) => {
        // 生成唯一标识符以防列表渲染冲突
        const id = Date.now() + Math.random();
        const newToast = {
            id,
            message,
            severity, 
            duration,
            open: true // 默认状态为开启
        };
        
        // 追加到通知队列中
        setToasts(prev => [...prev, newToast]);

        // 若设置了正数时间，则启动定时器在延迟后自动触发关闭动作
        if (duration > 0) {
            setTimeout(() => {
                closeToast(id);
            }, duration);
        }
    };

    /**
     * 将指定 ID 的 Toast 设置为关闭状态，并延迟从 DOM 结构中移除以保证退出动画流畅
     *
     * @param {number} id 对应 Toast 的唯一标识符
     */
    const closeToast = (id) => {
        // 1. 首先修改 open 状态为 false，触发 Material-UI 的 Snackbar 隐藏动画
        setToasts(prev => prev.map(t => 
            t.id === id ? { ...t, open: false } : t
        ));
        
        // 2. 延迟 200ms 等待渐变动画完全播放完毕后，再将数据项彻底从数组过滤清除
        setTimeout(() => {
            setToasts(prev => prev.filter(t => t.id !== id));
        }, 200);
    };

    return (
        <ToastContext.Provider value={{ showToast }}>
            {/* 注入子组件内容 */}
            {children}
            
            {/* 顶部悬浮的 Toast 容器栈 */}
            <div className="toast-stack" role="region" aria-label="通知提示">
                {toasts.map(toast => (
                    <Snackbar
                        key={toast.id}
                        open={toast.open}
                        anchorOrigin={{ vertical: 'top', horizontal: 'right' }}
                        className="toast-snackbar"
                    >
                        <Alert
                            severity={toast.severity}
                            onClose={() => closeToast(toast.id)}
                            variant="filled"
                            className="toast-alert"
                            role="alert"
                        >
                            {toast.message}
                        </Alert>
                    </Snackbar>
                ))}
            </div>
        </ToastContext.Provider>
    );
}

/**
 * 自定义钩子 (useToast)
 * 便捷获取全局 Toast 控制能力，必须在 ToastProvider 的作用域树下调用。
 * 
 * @returns {{ showToast: (message: string, severity?: string, duration?: number) => void }}
 */
function useToast() {
    const context = useContext(ToastContext);
    if (!context) {
        throw new Error('useToast must be used within ToastProvider');
    }
    return context;
}

// 暴露出全局接口以供未通过打包工具引用的 React 代码直接获取
window.ToastProvider = ToastProvider;
window.useToast = useToast;
