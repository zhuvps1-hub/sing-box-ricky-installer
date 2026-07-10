'use strict';

(() => {
  let timer = 0;
  let generation = 0;
  let currentJob = null;
  let lastFailureSeq = 0;

  function sleepQuiet(ms) {
    return new Promise(resolve => window.setTimeout(resolve, ms));
  }

  function requestId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID().replace(/-/g, '_');
    return `req_${Date.now()}_${Math.random().toString(36).slice(2, 14)}`;
  }

  function isTransportError(error) {
    return /请求超时|Failed to fetch|NetworkError|Load failed|网络|连接/.test(String(error?.message || error));
  }

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

  function iwanChanged() {
    const current = state.config?.iwan || {};
    let changed = false;
    document.querySelectorAll('[data-iwan]').forEach(input => {
      const key = input.dataset.iwan;
      const value = input.value;
      if (key === 'password') {
        if (value) changed = true;
        return;
      }
      const normalized = ['listen_port', 'mtu'].includes(key) ? String(Number(value || 0)) : String(value || '');
      const existing = String(current[key] ?? '');
      if (normalized !== existing) changed = true;
    });
    return changed;
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

  function markAccepted(job) {
    if (job.generation !== generation || job.superseded) return;
    updateLocalConfig(job.payload);
    state.dirty = iwanChanged();
    setDirty(state.dirty);
  }

  async function readStatus(id) {
    const data = await api(`/api/autosave-status?request_id=${encodeURIComponent(id)}`, {}, 3500);
    return data.autosave || {};
  }

  async function postJob(job) {
    const data = await api('/api/autosave', {
      method: 'POST',
      body: JSON.stringify({ ...job.payload, request_id: job.id }),
    }, 2500);
    job.seq = Number(data.seq || 0);
    job.accepted = job.seq > 0;
    if (job.accepted) markAccepted(job);
  }

  async function runJob(job) {
    let reachableWithoutAcceptance = 0;
    while (!job.superseded) {
      if (!job.accepted) {
        try {
          await postJob(job);
          reachableWithoutAcceptance = 0;
        } catch (error) {
          if (!isTransportError(error)) {
            if (!job.superseded) {
              state.dirty = true;
              setDirty(true);
              toast(error.message || '自动保存失败', true);
            }
            job.finished = true;
            return;
          }
          await sleepQuiet(1800);
          continue;
        }
      }

      try {
        const status = await readStatus(job.id);
        const serverSeq = Number(status.request_seq || 0);
        if (!serverSeq) {
          reachableWithoutAcceptance += 1;
          if (reachableWithoutAcceptance >= 2) {
            job.accepted = false;
            job.seq = 0;
            reachableWithoutAcceptance = 0;
          }
          await sleepQuiet(1300);
          continue;
        }
        job.seq = serverSeq;
        job.accepted = true;
        markAccepted(job);

        if (Number(status.failed_seq || 0) >= job.seq) {
          if (!job.superseded && Number(status.failed_seq) > lastFailureSeq) {
            lastFailureSeq = Number(status.failed_seq);
            state.dirty = true;
            setDirty(true);
            toast(status.message || '自动保存失败，已恢复旧配置', true);
          }
          job.finished = true;
          return;
        }
        if (Number(status.applied_seq || 0) >= job.seq) {
          if (!job.superseded && job.generation === generation) state.deleted.clear();
          job.finished = true;
          return;
        }
      } catch {
        // The control connection may briefly drop while sing-box restarts.
        // Keep the page quiet and confirm again after connectivity returns.
      }
      await sleepQuiet(1500);
    }
    job.finished = true;
  }

  function schedule(delay = 1000) {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      timer = 0;
      if (!state.config || !state.dirty) return;
      if (currentJob) currentJob.superseded = true;
      const job = {
        id: requestId(),
        generation: ++generation,
        payload: routePayload(),
        accepted: false,
        seq: 0,
        superseded: false,
        finished: false,
      };
      currentJob = job;
      runJob(job).finally(() => {
        if (currentJob === job && job.finished) currentJob = null;
      });
    }, delay);
  }

  function afterCurrentHandler(delay) {
    window.setTimeout(() => schedule(delay), 0);
  }

  async function waitCurrentJob(maxMs = 45000) {
    const started = Date.now();
    while (currentJob && !currentJob.finished && Date.now() - started < maxMs) {
      await sleepQuiet(500);
    }
    return !currentJob || currentJob.finished;
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
    if (routeNotice) routeNotice.textContent = '修改会先安全暂存，再在后台校验并应用；连接短暂波动不会重复提交。';

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
        const ready = await waitCurrentJob();
        if (!ready) {
          toast('节点或分流仍在后台应用，请稍后重试', true);
          button.disabled = false;
          button.textContent = '保存并重连';
          return;
        }
        const hiddenSave = document.querySelector('#saveBtn');
        if (!iwanChanged() || !hiddenSave) {
          toast('iWAN 配置没有变化');
          button.disabled = false;
          button.textContent = '保存并重连';
          return;
        }
        state.dirty = true;
        setDirty(true);
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
    if (event.target instanceof Element && event.target.matches('[data-route]')) schedule(850);
  }, true);

  document.addEventListener('submit', event => {
    if (event.target instanceof Element && event.target.closest('#nodeDialog')) afterCurrentHandler(1000);
  }, true);

  document.addEventListener('click', event => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.closest('[data-del-node]')) {
      afterCurrentHandler(1100);
      return;
    }
    if (target.closest('#confirmImportBtn')) afterCurrentHandler(1000);
  }, true);

  installPageUi();
})();
