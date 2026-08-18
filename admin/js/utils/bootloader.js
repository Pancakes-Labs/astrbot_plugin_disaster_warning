(function () {
    let hidden = false;
    const start = (window.performance && typeof performance.now === 'function') ? performance.now() : Date.now();
    const MIN_VISIBLE_MS = 280;

    // ===== 阶段时序播放器 =====
    // 无论加载实际多快，阶段指示器按固定时间轴依次播放
    const STAGE_DEFS = [
        { label: '验证身份...' },
        { label: '读取配置...' },
        { label: '同步系统状态...' },
        { label: '数据已就绪...' },
        { label: '系统启动完成' },
    ];
    const STAGE_INTERVAL_MS = 700;
    let stageTimer = null;
    let stageIndex = -1;
    let stageSequenceComplete = false;

    function startStageSequence() {
        if (stageTimer !== null) return;
        stageIndex = -1;
        stageSequenceComplete = false;
        advanceStage();
    }

    function advanceStage() {
        stageIndex++;
        if (stageIndex >= STAGE_DEFS.length) {
            // 所有阶段播放完毕：将所有圆点标记为 done（绿色）
            stageSequenceComplete = true;
            stageTimer = null;
            var dots = document.querySelectorAll('.bl-stage-dot');
            if (dots.length) {
                for (var i = 0; i < dots.length; i++) {
                    dots[i].classList.remove('active');
                    dots[i].classList.add('done');
                }
            }
            return;
        }
        var dots = document.querySelectorAll('.bl-stage-dot');
        if (dots.length) {
            for (var i = 0; i < dots.length; i++) {
                dots[i].classList.toggle('active', i === stageIndex);
                dots[i].classList.toggle('done', i < stageIndex);
            }
        }
        var text = document.getElementById('bl-status-text');
        if (text) text.textContent = STAGE_DEFS[stageIndex].label;
        stageTimer = setTimeout(advanceStage, STAGE_INTERVAL_MS);
    }

    // ===== 进度条引擎 =====
    // 固定时长匀速增长，总时长约 5s
    // 到达 100% 后自动停止 rAF 循环，避免重复执行
    const STAGE_TOTAL_MS = 5000;
    let barRafId = null;
    let barStartTime = 0;

    function startProgressBar() {
        if (barRafId !== null) return;
        barStartTime = performance.now();
        barRafId = requestAnimationFrame(progressLoop);
    }

    function progressLoop() {
        var elapsed = performance.now() - barStartTime;
        var pct = (elapsed / STAGE_TOTAL_MS) * 100;

        if (pct >= 100) {
            applyBarWidth(100);
            barRafId = null;
            return; // 到达 100%，停止循环
        }

        applyBarWidth(pct);
        barRafId = requestAnimationFrame(progressLoop);
    }

    function applyBarWidth(value) {
        var bar = document.getElementById('bl-progress-bar');
        if (bar) bar.style.width = value + '%';
    }

    // ===== 拨云见雾退出动画 =====

    function doCloudClearExit() {
        if (hidden) return;

        // 如果阶段序列还没播完，等待播完再执行退出
        if (!stageSequenceComplete) {
            var checkInterval = setInterval(function () {
                if (stageSequenceComplete) {
                    clearInterval(checkInterval);
                    doCloudClearExit();
                }
            }, 100);
            return;
        }

        hidden = true;

        var skeleton = document.getElementById('loading-skeleton');
        var root = document.getElementById('root');

        // 确保进度条 snap 到 100%（取消 rAF 循环，直接设置宽度）
        if (barRafId) {
            cancelAnimationFrame(barRafId);
            barRafId = null;
        }
        applyBarWidth(100);

        setTimeout(function () {
            if (skeleton) skeleton.classList.add('is-exiting');
            if (root) root.classList.add('root-reveal');

            setTimeout(function () {
                if (skeleton && skeleton.parentNode) skeleton.parentNode.removeChild(skeleton);
                if (root) {
                    root.setAttribute('aria-busy', 'false');
                    root.classList.remove('root-reveal');
                }
            }, 1000);
        }, 350);
    }

    function doHide() {
        doCloudClearExit();
    }

    /**
     * 全局接口：更新加载进度
     * 首次调用时启动阶段时序播放和进度条引擎
     */
    window.__ASTRBOT_UPDATE_PROGRESS = function () {
        if (stageTimer === null) {
            startStageSequence();
            startProgressBar();
        }
    };

    /**
     * 全局接口：标记加载完全就绪
     * 进度条由内部固定时长引擎独立驱动，不受此影响
     * 触发隐藏加载遮罩，等待阶段序列播完 + 拨云见雾
     */
    window.__ASTRBOT_BOOTLOADER_READY = function () {
        setTimeout(window.__ASTRBOT_HIDE_BOOTLOADER, 200);
    };

    /**
     * 全局接口：隐藏加载遮罩
     */
    window.__ASTRBOT_HIDE_BOOTLOADER = function () {
        var now = (window.performance && typeof performance.now === 'function') ? performance.now() : Date.now();
        var delay = Math.max(0, MIN_VISIBLE_MS - (now - start));
        setTimeout(doHide, delay);
    };

    // 监听 window 的 load 加载事件（兜底方案）
    window.addEventListener('load', function () {
        if (window.__ASTRBOT_AUTH_PENDING) return;
        setTimeout(window.__ASTRBOT_HIDE_BOOTLOADER, 1200);
    }, { once: true });
})();
