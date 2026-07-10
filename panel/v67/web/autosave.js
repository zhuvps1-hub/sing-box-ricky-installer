'use strict';

(() => {
  let timer = 0;
  let armed = false;
  let silentCycle = false;
  let observer = null;

  const originalToast = window.toast;

  function isNodeOrRouteTarget(target) {
    if (!(target instanceof Element)) return false;
    return Boolean(
      target.closest('[data-route]') ||
      target.closest('#nodeDialog') ||
      target.closest('#importDialog') ||
      target.closest('[data-edit-node]') ||
      target.closest('[data-del-node]') ||
      target.closest('#openImportBtn') ||
      target.closest('#addNodeBtn')
    );
  }

  function suppressSuccessMessages() {
    if (typeof originalToast !== 'function') return;
    window.toast = (message, bad = false) => {
      if (silentCycle && !bad) return;
      return originalToast(message, bad);
    };
  }

  function hideSuccessBadge() {
    const badge = document.querySelector('#applyState');
    if (!badge || !silentCycle) return;
    const text = badge.textContent || '';
    if (/已保存|后台应用中|配置已生效|正在重新确认|等待状态同步/.test(text)) {
      badge.classList.add('hidden');
    }
  }

  function tryAutosave() {
    timer = 0;
    if (!armed) return;
    const button = document.querySelector('#saveBtn');
    if (!button || button.disabled || !state?.dirty || state?.saving) {
      timer = window.setTimeout(tryAutosave, 500);
      return;
    }
    armed = false;
    silentCycle = true;
    button.click();
    window.setTimeout(() => { silentCycle = false; }, 15000);
  }

  function scheduleAutosave(delay = 900) {
    armed = true;
    window.clearTimeout(timer);
    timer = window.setTimeout(tryAutosave, delay);
  }

  document.addEventListener('change', event => {
    if (event.target instanceof Element && event.target.matches('[data-route]')) {
      scheduleAutosave(700);
    }
  }, true);

  document.addEventListener('submit', event => {
    if (event.target instanceof Element && event.target.closest('#nodeDialog')) {
      scheduleAutosave(900);
    }
  }, true);

  document.addEventListener('click', event => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target || !isNodeOrRouteTarget(target)) return;
    if (target.closest('[data-del-node]')) {
      scheduleAutosave(1100);
      return;
    }
    if (target.closest('#confirmImportBtn')) {
      scheduleAutosave(900);
      return;
    }
    if (target.closest('#nodeDialog button[type="submit"], #nodeDialog .primary')) {
      scheduleAutosave(900);
    }
  }, true);

  suppressSuccessMessages();
  observer = new MutationObserver(hideSuccessBadge);
  observer.observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true });
})();
