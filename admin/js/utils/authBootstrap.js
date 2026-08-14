(function () {
    // 标记初始化鉴权状态为挂起
    window.__ASTRBOT_AUTH_PENDING = true;

    // 快捷获取 DOM 元素
    function getElement(id) {
        return document.getElementById(id);
    }

    // 显示登录表单界面，并聚焦到密码输入框
    function showLogin() {
        const loadingEl = getElement('bl-loading');
        const loginEl = getElement('bl-login');
        if (loadingEl) loadingEl.style.display = 'none'; // 隐藏加载状态
        if (loginEl) loginEl.style.display = 'flex';     // 展现登录卡片
        setTimeout(function () {
            const pw = getElement('bl-password');
            if (pw) pw.focus(); // 自动聚焦密码框
        }, 80);
    }

    // 显示登录加载中状态
    function showLoading() {
        const loginEl = getElement('bl-login');
        const loadingEl = getElement('bl-loading');
        if (loginEl) loginEl.style.display = 'none';
        if (loadingEl) loadingEl.style.display = 'flex';
    }

    // 鉴权通过后的前向引导
    function proceedWithAuth() {
        window.__ASTRBOT_AUTH_PENDING = false;
        window.dispatchEvent(new Event('auth-ready')); // 广播就绪信号，告知 React 应用可安全构建组件树
    }

    // 异步校验令牌是否依然有效
    function verifyToken(token) {
        window.DisasterApiClient.request('/status', {
            headers: {
                'Authorization': 'Bearer ' + token,
            },
        })
            .then(function () {
                proceedWithAuth(); // 校验成功，解锁主界面
            })
            .catch(function (error) {
                if (error && error.status === 401) {
                    window.AuthUtil.clearToken(); // 令牌非法或失效，清除并显示登录表单
                    showLogin();
                    return;
                }
                proceedWithAuth(); // 其他网络请求异常不予硬阻断，退化通行
            });
    }

    // 处理表单的登录按钮提交事件
    window.__BL_HANDLE_LOGIN = function (event) {
        event.preventDefault();
        const passwordInput = getElement('bl-password');
        const password = passwordInput ? passwordInput.value : '';
        if (!password) return;

        const errorEl = getElement('bl-login-error');
        const submitBtn = getElement('bl-submit');
        if (errorEl) errorEl.textContent = ''; // 清空先前的错误文案
        if (submitBtn) {
            submitBtn.disabled = true;       // 禁用重复点击
            submitBtn.textContent = '登录中...';
        }

        // 向登录接口发起认证请求
        window.DisasterApiClient.request('/login', {
            method: 'POST',
            body: { password },
        })
            .then(function (data) {
                window.AuthUtil.setToken(data.token); // 缓存令牌
                proceedWithAuth();                    // 鉴权通行
                showLoading();
            })
            .catch(function (error) {
                // 登录失败，还原提交按钮状态并渲染错误提示
                if (errorEl) {
                    errorEl.textContent = (error && error.payload && error.payload.error)
                        || (error && error.message)
                        || '密码错误，请重试';
                }
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = '登录';
                }
            });
    };

    // 绑定密码明文密文切换按钮事件
    function bindPasswordToggle() {
        const toggle = getElement('bl-toggle-pw');
        const passwordInput = getElement('bl-password');
        if (!toggle || !passwordInput) return;
        toggle.addEventListener('click', function () {
            const isVisible = passwordInput.type !== 'password';
            passwordInput.type = isVisible ? 'password' : 'text';
            toggle.textContent = isVisible ? '👁️' : '🙈'; // 切换 Emoji 图标
            // 同步无障碍状态：aria-pressed 标记点击后的当前开关状态，
            // aria-label 描述点击后按钮的目标动作（密码可见→隐藏，密码隐藏→显示）
            toggle.setAttribute('aria-pressed', String(!isVisible));
            toggle.setAttribute('aria-label', isVisible ? '显示密码' : '隐藏密码');
        });
    }

    // 检查宿主系统是否开启了鉴权保护
    function checkAuthRequirement() {
        window.DisasterApiClient.request('/auth-info')
            .then(function (data) {
                // 若未启用鉴权，跳过登录界面
                if (!data.auth_required) {
                    proceedWithAuth();
                    return;
                }

                // 启用鉴权下，检查是否有历史令牌
                const token = window.AuthUtil && window.AuthUtil.getToken();
                if (!token) {
                    showLogin(); // 无令牌则前往登录
                    return;
                }

                verifyToken(token); // 有令牌则异步校验
            })
            .catch(function () {
                proceedWithAuth(); // 网络异常情况下放行，防止面板彻底锁死
            });
    }

    bindPasswordToggle();
    checkAuthRequirement();
})();
