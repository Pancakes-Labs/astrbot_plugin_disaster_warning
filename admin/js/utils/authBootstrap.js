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
        if (loadingEl) loadingEl.style.display = 'none';
        if (loginEl) loginEl.style.display = 'flex';
        setTimeout(function () {
            const pw = getElement('bl-password');
            if (pw) pw.focus();
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
        window.dispatchEvent(new Event('auth-ready'));
    }

    // 异步校验令牌是否依然有效
    function verifyToken(token) {
        // 启动加载进度（首次调用会触发阶段播放和进度条）
        if (typeof window.__ASTRBOT_UPDATE_PROGRESS === 'function') {
            window.__ASTRBOT_UPDATE_PROGRESS();
        }
        window.DisasterApiClient.request('/status', {
            headers: {
                'Authorization': 'Bearer ' + token,
            },
        })
            .then(function () {
                proceedWithAuth();
            })
            .catch(function (error) {
                if (error && error.status === 401) {
                    window.AuthUtil.clearToken();
                    showLogin();
                    return;
                }
                proceedWithAuth();
            });
    }

    // 处理表单的登录按钮提交事件
    window.__BL_HANDLE_LOGIN = function (event) {
        event.preventDefault();
        const passwordInput = getElement('bl-password');
        const password = passwordInput ? passwordInput.value : '';
        if (!password) return;

        if (typeof window.__ASTRBOT_UPDATE_PROGRESS === 'function') {
            window.__ASTRBOT_UPDATE_PROGRESS();
        }

        const errorEl = getElement('bl-login-error');
        const submitBtn = getElement('bl-submit');
        if (errorEl) errorEl.textContent = '';
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = '登录中...';
        }

        window.DisasterApiClient.request('/login', {
            method: 'POST',
            body: { password },
        })
            .then(function (data) {
                window.AuthUtil.setToken(data.token);
                proceedWithAuth();
                showLoading();
            })
            .catch(function (error) {
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
            toggle.textContent = isVisible ? '👁️' : '🙈';
            toggle.setAttribute('aria-pressed', String(!isVisible));
            toggle.setAttribute('aria-label', isVisible ? '显示密码' : '隐藏密码');
        });
    }

    // 检查宿主系统是否开启了鉴权保护
    function checkAuthRequirement() {
        window.DisasterApiClient.request('/auth-info')
            .then(function (data) {
                if (!data.auth_required) {
                    if (typeof window.__ASTRBOT_UPDATE_PROGRESS === 'function') {
                        window.__ASTRBOT_UPDATE_PROGRESS();
                    }
                    proceedWithAuth();
                    return;
                }

                const token = window.AuthUtil && window.AuthUtil.getToken();
                if (!token) {
                    showLogin();
                    return;
                }

                verifyToken(token);
            })
            .catch(function () {
                if (typeof window.__ASTRBOT_UPDATE_PROGRESS === 'function') {
                    window.__ASTRBOT_UPDATE_PROGRESS();
                }
                proceedWithAuth();
            });
    }

    bindPasswordToggle();
    checkAuthRequirement();
})();
