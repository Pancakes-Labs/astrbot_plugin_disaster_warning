/**
 * @file useThemeSync.js
 * @description 应用全局视觉主题的副作用同步钩子。
 * 
 * 视觉交互与防闪烁设计细节：
 * 1. 样式表类名路由：通过动态添加/删除根元素 <html> 的 theme-dark 以及 <body> 的 dark-theme 类名，
 *    无缝映射对应的全局 CSS 变量配色。
 * 2. 渐变防闪烁：为了防止在主题切换时 Material-UI 组件发生硬性闪烁或颜色跳跃，我们为根元素注入了 theme-switching 临时类，
 *    在其中绑定了过渡平滑 transition CSS，并在 360ms 后（过渡动画完成时）将此标志类名擦除，还原页面的高帧率流畅物理响应。
 */
function useThemeSync(theme) {
    React.useEffect(() => {
        const isDark = theme === 'dark';
        const rootEl = document.documentElement;
        const bodyEl = document.body;

        // 注入临时“正在切换”状态，用于在过渡动画运行期间平滑过滤重绘毛刺
        rootEl.classList.add('theme-switching');
        bodyEl.classList.toggle('dark-theme', isDark);
        rootEl.classList.toggle('theme-dark', isDark);
        // 同步 data-theme 属性：供 simulation.css 等使用 [data-theme='dark'] 选择器的样式生效
        // （此前该属性仅挂在 Sidebar 的 GitHub 链接上，导致模拟页暗色适配完全失效）
        rootEl.setAttribute('data-theme', isDark ? 'dark' : 'light');
        bodyEl.setAttribute('data-theme', isDark ? 'dark' : 'light');

        // 持久化同步，以便在系统重新加载时实现偏好恢复
        localStorage.setItem('theme', theme);

        const clearThemeSwitching = () => {
            rootEl.classList.remove('theme-switching');
        };
        // transition 过渡期为 360ms 
        const timer = window.setTimeout(clearThemeSwitching, 360);

        return () => {
            window.clearTimeout(timer);
            clearThemeSwitching();
        };
    }, [theme]);
}
