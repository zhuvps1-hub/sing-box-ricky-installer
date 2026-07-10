'use strict';

(() => {
  let timer = 0;
  let sending = false;
  let sendAgain = false;
  let lastSubmitted = 0;
  let watching = false;
  let lastFailure = 0;

  function routePayload() {
    const mappings = {};
    document.querySelectorAll('[data-route]').forEach(select => {
      if (select.dataset.route !== 'default') mappings[select.dataset.route] = select.value;
    });
    return {
      nodes: state.nodes.map(node => ({ ...node })),
      deleted_tags: [...state.deleted],
      mappings,
      default: document.querySelector('[data-route="default"]')?.value || '',
      iwan: {},
    };
  }

  function updateLocalConfig(payload) {
    if (!state.config) return;
    state.config = {
      ...state.config,
      nodes: payload.nodes.map(node => ({ ...node, password: '' })),
      mappings: { ...payload.mappings },
      default: payload.default,
    };
    try { renderNodes(); } catch {}
    try { refreshRouteCards(); } catch {}
  }

  async function watchStatus() {
    if (watching || !lastSubmitted) return;
    watching = true;
    try {
      while (lastSubmitted) {
        let status = null;
        try {
          const data = await api('/api/autosave-status', {}, 4000);
          status = data.autosave || {};
        } catch {
          await sleep(1800);
          continue;
        }

        if (Number(status.failed_seq || 0) >= lastSubmitted) {
          if (Number(status.failed_seq) > lastFailure) {
            lastFailure = Number(status.failed_seq);
            state.dirty = true;
            setDirty(true);
            toast(status.message || '自动保存失败，已恢复旧配置', true);
          }
          return;
        }
        if (Number(status.applied_seq || 0) >= lastSubmitted) {
          state.deleted.clear();
          return;
        }
        await sleep(1300);
      }
    } finally {
      watching = false;
      if (lastSubmitted) {
        window.setTimeout(() => {
          if (!watching) watchStatus();
        }, 1200);
      }
    }
  }

  async function sendLatest() {
    timer = 0;
    if (sending) {
      sendAgain = true;
      return;
    }
    if (!state.config) return;

    sending = true;
    const payload = routePayload();
    try {
      const data = await api('/api/autosave', {
        method: 'POST',
        body: JSON.stringify(payload),
      }, 5000);
      lastSubmitted = Number(data.seq || 0);
      state.dirty = false;
      setDirty(false);
      updateLocalConfig(payload);
      watchStatus();
    } catch (error) {
      state.dirty = true;
      setDirty(true);
      toast(error.message || '自动保存失败', true);
    } finally {
      sending = false;
      if (sendAgain) {
        sendAgain = false;
        schedule(500);
      }
    }
  }

  function schedule(delay = 1200) {
    window.clearTimeout(timer);
    timer = window.setTimeout(sendLatest, delay);
  }

  function afterCurrentHandler(delay) {
    window.setTimeout(() => schedule(delay), 0);
  }

  document.addEventListener('change', event => {
    if (event.target instanceof Element && event.target.matches('[data-route]')) {
      schedule(900);
    }
  }, true);

  document.addEventListener('submit', event => {
    if (event.target instanceof Element && event.target.closest('#nodeDialog')) {
      afterCurrentHandler(1100);
    }
  }, true);

  document.addEventListener('click', event => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.closest('[data-del-node]')) {
      afterCurrentHandler(1200);
      return;
    }
    if (target.closest('#confirmImportBtn')) {
      afterCurrentHandler(1100);
    }
  }, true);

  document.documentElement.classList.add('silent-autosave');
  document.querySelector('#routeTools')?.remove();
  document.querySelector('#applyState')?.remove();
})();
