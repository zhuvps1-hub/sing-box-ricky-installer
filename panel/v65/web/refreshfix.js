'use strict';

(() => {
  const originalLoadConfig = window.loadConfig;
  const originalLoadDashboard = window.loadDashboard;
  const originalToast = window.toast;
  let retryTimer = 0;
  let retryCount = 0;

  function savedButton() {
    const button = document.querySelector('#saveBtn');
    return button && button.textContent.includes('已保存');
  }

  function badge() {
    return document.querySelector('#applyState');
  }

  function mark(text, kind = 'working', autoHide = 0) {
    const element = badge();
    if (!element) return;
    element.textContent = text;
    element.className = `apply-state ${kind}`;
    clearTimeout(element._hideTimer);
    if (autoHide) element._hideTimer = setTimeout(() => element.classList.add('hidden'), autoHide);
  }

  function scheduleConfirm(delay = 2500) {
    if (retryTimer) return;
    retryTimer = setTimeout(async () => {
      retryTimer = 0;
      try {
        const data = await api('/api/apply-status', {}, 12000);
        const job = data.job;
        if (!job) {
          mark('已保存，等待状态同步', 'working');
          return;
        }
        if (job.status === 'queued' || job.status === 'running') {
          mark('已保存，后台应用中', 'working');
          scheduleConfirm(2500);
          return;
        }
        if (job.status === 'failed') {
          mark('应用失败，已回滚', 'failed');
          state.dirty = true;
          setDirty(true);
          originalToast(job.message || '后台应用失败，已恢复旧配置', true);
          return;
        }

        mark('配置已生效', 'success', 7000);
        retryCount = 0;
        try {
          await originalLoadConfig();
        } catch {
          mark('配置已生效，页面数据稍后刷新', 'success', 9000);
        }
        setTimeout(() => originalLoadDashboard(), 1200);
      } catch {
        retryCount += 1;
        mark('已保存，正在重新确认', 'working');
        if (retryCount < 5) scheduleConfirm(Math.min(8000, 2000 + retryCount * 1200));
        else mark('已保存，稍后刷新页面查看', 'success', 10000);
      }
    }, delay);
  }

  if (typeof originalLoadConfig === 'function') {
    window.loadConfig = async (...args) => {
      try {
        return await originalLoadConfig(...args);
      } catch (error) {
        if (savedButton()) {
          mark('配置已生效，页面数据稍后刷新', 'success', 9000);
          scheduleConfirm(3000);
          return null;
        }
        throw error;
      }
    };
  }

  if (typeof originalToast === 'function') {
    window.toast = (message, bad = false) => {
      if (message === '请求超时' && savedButton()) {
        mark('已保存，正在重新确认', 'working');
        scheduleConfirm(1800);
        return;
      }
      return originalToast(message, bad);
    };
  }

  const observer = new MutationObserver(() => {
    const element = badge();
    if (!element || !savedButton()) return;
    if (element.textContent === '状态确认失败') {
      mark('已保存，正在重新确认', 'working');
      scheduleConfirm(1200);
    }
  });
  observer.observe(document.body, { subtree: true, childList: true, characterData: true });
})();
