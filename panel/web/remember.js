'use strict';

(() => {
  const form = document.querySelector('#loginForm');
  const password = document.querySelector('#loginPass');
  if (!form || !password) return;

  const row = document.createElement('label');
  row.className = 'remember-login-row';
  row.innerHTML = '<input id="rememberLogin" type="checkbox"><span>记住登录状态（30 天）</span>';
  password.closest('label')?.insertAdjacentElement('afterend', row);

  const remember = row.querySelector('#rememberLogin');
  remember.checked = localStorage.getItem('iwan-remember-login') !== '0';
  remember.addEventListener('change', () => {
    localStorage.setItem('iwan-remember-login', remember.checked ? '1' : '0');
  });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const button = form.querySelector('button[type="submit"]');
    const errorBox = document.querySelector('#loginError');
    button.disabled = true;
    button.textContent = '登录中…';
    errorBox.textContent = '';

    try {
      const data = await api('/api/login', {
        method: 'POST',
        body: JSON.stringify({
          username: document.querySelector('#loginUser').value,
          password: password.value,
          remember: remember.checked,
        }),
      }, 20000);
      localStorage.setItem('iwan-remember-login', remember.checked ? '1' : '0');
      state.csrf = data.csrf;
      password.value = '';
      showApp();
      await Promise.all([loadConfig(), loadDashboard()]);
    } catch (error) {
      errorBox.textContent = error.message;
    } finally {
      button.disabled = false;
      button.textContent = '登录';
    }
  }, true);
})();
