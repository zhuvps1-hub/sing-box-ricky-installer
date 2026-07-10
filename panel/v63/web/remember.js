'use strict';

(() => {
  const form = document.querySelector('#loginForm');
  const remember = document.querySelector('#rememberLogin');
  if (!form || !remember) return;

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
          password: document.querySelector('#loginPass').value,
          remember: remember.checked,
        }),
      }, 20000);
      localStorage.setItem('iwan-remember-login', remember.checked ? '1' : '0');
      state.csrf = data.csrf;
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
