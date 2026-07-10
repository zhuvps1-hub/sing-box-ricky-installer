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

  async function readAutosaveStatus() {
    const data = await api('/api/autosave-status', {}, 4000);
    return data.autosave || {};
  }

  async function watchStatus() {
    if (watching || !lastSubmitted) return;
    watching = true;
    try {
      while (lastSubmitted) {
        let status = null;
        try {
          status = await readAutosaveStatus();
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
          lastSubmitted = 0;
          return;
        }
        if (Number(status.applied_seq || 0) >= lastSubmitted) {
          state.deleted.clear();
          lastSubmitted = 0;
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
    if (!state.config || !state.dirty) return;

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

  async function waitAutosaveIdle(maxMs = 30000) {
    const started = Date.now();
    while (Date.now() - started < maxMs) {
      if (!sending && !timer) {
        try {
          const status = await readAutosaveStatus();
          if (!['queued', 'running'].includes(status.state)) return true;
        } catch {}
      }
      await sleep(600);
    }
    return false;
  }

  function installPageUi() {
    document.documentElement.classList.add('silent-autosave');
    document.querySelector('#routeTools')?.remove();
    document.querySelector('#applyState')?.remove();

    const quickHint = document.querySelector('#page-dashboard .action-grid')?.closest('.panel-card')?.querySelector('.panel-head p');
    if (quickHint) quickHint.textContent = '节点和分流自动保存；iWAN 与 DNS 在各自页面确认';
    const nodeHint = document.querySelector('#page-nodes .panel-head p');
    if (nodeHint) nodeHint.textContent = '新增、编辑、删除或导入后自动保存';
    const routeNotice = document.querySelector('#page-routing .notice');
    if (routeNotice) routeNotice.textContent = '修改后自动校验并在后台应用；失败会自动回滚并提示。';

    const iwanPage = document.querySelector('#page-iwan .panel-card');
    const iwanForm = document.querySelector('#iwanForm');
    if (iwanPage && iwanForm && !document.querySelector('#iwanSaveBtn')) {
      const actions = document.createElement('div');
      actions.className = 'dialog-actions iwan-save-actions';
      actions.innerHTML = '<button type="button" id="iwanSaveBtn" class="btn primary">保存并重连</button>';
      iwanForm.insertAdjacentElement('afterend', actions);
      const notice = iwanPage.querySelector('.notice');
      if (notice) notice.textContent = '密码不会在页面回显。留空表示保留当前密码；点击“保存并重连”后自动校验并重新连接。';
      document.querySelector('#iwanSaveBtn').onclick = async event => {
        const button = event.currentTarget;
        button.disabled = true;
        button.textContent = '准备保存…';
        const idle = await waitAutosaveIdle();
        if (!idle) {
          toast('节点或分流仍在后台应用，请稍后重试', true);
          button.disabled = false;
          button.textContent = '保存并重连';
          return;
        }
        const hiddenSave = document.querySelector('#saveBtn');
        if (!state.dirty || !hiddenSave || hiddenSave.disabled) {
          toast('iWAN 配置没有变化');
          button.disabled = false;
          button.textContent = '保存并重连';
          return;
        }
        hiddenSave.click();
        const reset = window.setInterval(() => {
          if (!state.saving) {
            window.clearInterval(reset);
            button.disabled = false;
            button.textContent = '保存并重连';
          }
        }, 400);
      };
    }
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

  installPageUi();
})();
