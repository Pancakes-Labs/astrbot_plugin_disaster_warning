/**
 * @file useBootloaderDismiss.js
 * @description 主应用就绪后隐藏启动加载遮罩的副作用钩子。
 * 
 * 性能优化细节：
 * 1. 双重动画帧等待 (Double RequestAnimationFrame)：为了确保骨架屏和 DOM 树已经在浏览器中完成了首次完整的绘制 (Paint) 
 *    并呈现在物理屏幕上，我们等待两个连续的渲染帧周期，然后在第二帧里执行隐藏遮罩的回调函数。
 * 2. 回退机制：对于缺少 requestAnimationFrame 支持的老旧 WebView，回退为秒级 `setTimeout(..., 0)` 执行。
 * 3. 进度联动：React 渲染就绪时标记加载完成，进度条会 natural 走完或 snap 到 100%。
 *    阶段指示器由 bootloader.js 的时序播放器独立控制，不受此影响。
 */
function useBootloaderDismiss() {
    React.useEffect(() => {
        // 确保 bootloader 已启动（阶段播放 + 进度条）
        if (typeof window.__ASTRBOT_UPDATE_PROGRESS === 'function') {
            window.__ASTRBOT_UPDATE_PROGRESS();
        }

        const hideBootloader = () => {
            if (typeof window.__ASTRBOT_BOOTLOADER_READY === 'function') {
                window.__ASTRBOT_BOOTLOADER_READY();
            } else if (typeof window.__ASTRBOT_HIDE_BOOTLOADER === 'function') {
                window.__ASTRBOT_HIDE_BOOTLOADER();
            }
        };

        if (typeof window.requestAnimationFrame === 'function') {
            window.requestAnimationFrame(() => {
                window.requestAnimationFrame(hideBootloader);
            });
        } else {
            setTimeout(hideBootloader, 0);
        }
    }, []);
}
