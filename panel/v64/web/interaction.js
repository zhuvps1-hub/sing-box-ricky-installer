'use strict';

(() => {
  const asyncUi = { jobId: '', preview: [], errors: [], polling: false };

  function ensureApplyState() {
    let badge = document.querySelector('#applyState');
    if (!badge) {
      badge = document.createElement('span');
      badge.id = 'applyState';
      badge.className = 'apply-state hidden';
      document.querySelector('.status-strip')?.appendChild(badge);
    }
    return badge;
  }

  function showApplyState(text, kind = 'working', autoHide = 0) {
    const badge = ensureApplyState();
    badge.textContent = text;
    badge.className = `apply-state ${kind}`;
    clearTimeout(badge._hideTimer);
    if (autoHide) badge._hideTimer = setTimeout(() => badge.classList.add('hidden'), autoHide);
  }

  function buildPayload() {
    const mappings = {};
    document.querySelectorAll('[data-route]').forEach(select => {
      if (select.dataset.route !== 'default') mappings[select.dataset.route] = select.value;
    });
    const iwan = {};
    document.querySelectorAll('[data-iwan]').forEach(input => {
      let value = input.value;
      if (['listen_port', 'mtu'].includes(input.dataset.iwan)) value = Number(value);
      iwan[input.dataset.iwan] = value;
    });
    return {
      nodes: state.nodes,
      deleted_tags: [...state.deleted],
      mappings,
      default: document.querySelector('[data-route="default"]')?.value || '',
      iwan,
    };
  }

  async function pollApply(jobId, silent = false) {
    if (!jobId || asyncUi.polling) return;
    asyncUi.polling = true;
    try {
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const data = await api(`/api/apply-status?id=${encodeURIComponent(jobId)}`, {}, 7000);
        const job = data.job;
        if (!job) return;
        if (job.status === 'queued' || job.status === 'running') {
          showApplyState(job.status === 'queued' ? '等待后台应用' : '后台应用中', 'working');
          await sleep(1000);
          continue;
        }
        if (job.status === 'succeeded') {
          showApplyState('配置已生效', 'success', 7000);
          if (!silent) toast(job.message || '配置已生效');
          await Promise.all([loadConfig(), loadDashboard()]);
          return;
        }
        showApplyState('应用失败，已回滚', 'failed');
        state.dirty = true;
        setDirty(true);
        toast(job.message || '后台应用失败', true);
        return;
      }
      showApplyState('后台任务仍在运行', 'working');
    } catch (error) {
      showApplyState('状态确认失败', 'failed', 7000);
      if (!silent) toast(error.message, true);
    } finally {
      asyncUi.polling = false;
    }
  }

  async function saveAsync() {
    if (!state.config || !state.dirty || state.saving) return;
    const button = document.querySelector('#saveBtn');
    state.saving = true;
    button.disabled = true;
    button.textContent = '保存中…';
    try {
      const data = await api('/api/save', {
        method: 'POST',
        body: JSON.stringify(buildPayload()),
      }, 7000);
      asyncUi.jobId = data.job_id;
      state.dirty = false;
      button.textContent = '已保存';
      button.disabled = true;
      showApplyState('已保存，后台应用中', 'working');
      toast('已保存，后台自动校验并应用');
      pollApply(asyncUi.jobId);
    } catch (error) {
      state.dirty = true;
      setDirty(true);
      showApplyState('提交失败', 'failed', 7000);
      toast(error.message, true);
    } finally {
      state.saving = false;
      if (state.dirty) {
        button.disabled = false;
        button.textContent = '保存并应用';
      }
    }
  }

  function routeHelp(key) {
    return {
      netflix: 'Netflix 独立出口',
      ai: 'ChatGPT、Claude、Gemini 独立出口',
      youtube: 'YouTube 独立出口',
      telegram: 'Telegram 独立出口',
      default: '未命中以上规则的独立默认出口',
    }[key] || '';
  }

  function refreshRouteCards() {
    document.querySelectorAll('.routing-form label').forEach(label => {
      const select = label.querySelector('select');
      if (!select) return;
      label.classList.add('route-setting-card');
      let meta = label.querySelector('.route-card-meta');
      if (!meta) {
        meta = document.createElement('small');
        meta.className = 'route-card-meta';
        label.appendChild(meta);
      }
      const key = select.dataset.route || 'direct';
      const selected = select.value || '未设置';
      meta.textContent = key === 'direct' ? '国内网站固定直连' : `${routeHelp(key)} · 当前：${selected}`;
    });
  }

  function enhanceRouting() {
    const form = document.querySelector('.routing-form');
    if (!form || document.querySelector('#routeTools')) return;
    const tools = document.createElement('div');
    tools.id = 'routeTools';
    tools.className = 'route-tools';
    tools.innerHTML = '<button type="button" class="btn ghost small" id="routeReload">放弃未保存修改</button>';
    form.parentElement.insertBefore(tools, form);
    form.addEventListener('change', refreshRouteCards);
    document.querySelector('#routeReload').onclick = async () => {
      if (!state.dirty || confirm('放弃当前未保存修改并重新读取服务器配置？')) {
        await loadConfig();
        refreshRouteCards();
      }
    };
    setTimeout(refreshRouteCards, 0);
  }

  function importModePlaceholder(mode) {
    if (mode === 'ss') return '每行粘贴一个 ss:// 节点链接';
    if (mode === 'json') return '粘贴节点 JSON 数组，或完整 sing-box 配置';
    return '自动识别多行 ss://、节点 JSON 或 sing-box outbounds';
  }

  function renderImportPreview() {
    const box = document.querySelector('#importPreview');
    const confirmButton = document.querySelector('#confirmImportBtn');
    if (!asyncUi.preview.length) {
      box.innerHTML = '<div class="hint">解析后会先显示节点预览，不会立即修改当前配置。</div>';
      confirmButton.disabled = true;
      return;
    }
    box.innerHTML = asyncUi.preview.map(node => {
      const duplicate = state.nodes.some(existing => existing.tag === node.tag);
      return `<div class="import-preview-card ${duplicate ? 'duplicate' : ''}">
        <div><strong>${escapeHtml(node.tag)}</strong><span>${escapeHtml(node.server)}:${escapeHtml(node.server_port)}</span></div>
        <code>${escapeHtml(node.method)}</code>
        <em>${duplicate ? '名称重复' : '可加入'}</em>
      </div>`;
    }).join('') + (asyncUi.errors.length ? `<pre class="import-errors">${escapeHtml(asyncUi.errors.join('\n'))}</pre>` : '');
    confirmButton.disabled = false;
  }

  function installImportUi() {
    const form = document.querySelector('#importForm');
    if (!form || form.dataset.finalUi === '1') return;
    form.dataset.finalUi = '1';
    form.innerHTML = `
      <div class="dialog-head"><div><h3>一键导入节点</h3><p>粘贴、预览、处理重复后再加入</p></div><button type="button" id="closeImportDialog" class="icon-btn">×</button></div>
      <div class="import-modes">
        <button type="button" class="active" data-import-mode="auto">自动识别</button>
        <button type="button" data-import-mode="ss">ss:// 链接</button>
        <button type="button" data-import-mode="json">JSON</button>
      </div>
      <textarea id="importText" rows="8" placeholder="${importModePlaceholder('auto')}"></textarea>
      <div class="import-controls">
        <label>重复节点<select id="duplicatePolicy"><option value="replace">覆盖同名节点</option><option value="skip">跳过同名节点</option><option value="rename">自动重命名</option></select></label>
        <button type="submit" class="btn soft">解析预览</button>
      </div>
      <div id="importPreview" class="import-preview"></div>
      <div id="importResult" class="hint"></div>
      <div class="dialog-actions"><button type="button" id="cancelImportBtn" class="btn ghost">取消</button><button type="button" id="confirmImportBtn" class="btn primary" disabled>确认加入</button></div>`;

    document.querySelectorAll('[data-import-mode]').forEach(button => {
      button.onclick = () => {
        document.querySelectorAll('[data-import-mode]').forEach(item => item.classList.toggle('active', item === button));
        document.querySelector('#importText').placeholder = importModePlaceholder(button.dataset.importMode);
      };
    });
    const close = () => document.querySelector('#importDialog').close();
    document.querySelector('#closeImportDialog').onclick = close;
    document.querySelector('#cancelImportBtn').onclick = close;
    document.querySelector('#confirmImportBtn').onclick = confirmImport;
    form.addEventListener('submit', parseImportPreview, true);
    renderImportPreview();
  }

  async function parseImportPreview(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    const text = document.querySelector('#importText').value.trim();
    if (!text) { document.querySelector('#importResult').textContent = '请先粘贴节点内容'; return; }
    const submit = document.querySelector('#importForm button[type="submit"]');
    submit.disabled = true;
    submit.textContent = '解析中…';
    try {
      const data = await api('/api/import-nodes', { method: 'POST', body: JSON.stringify({ text }) }, 20000);
      asyncUi.preview = data.nodes || [];
      asyncUi.errors = data.errors || [];
      document.querySelector('#importResult').textContent = `识别到 ${asyncUi.preview.length} 个节点`;
      renderImportPreview();
    } catch (error) {
      asyncUi.preview = [];
      asyncUi.errors = [];
      document.querySelector('#importResult').textContent = error.message;
      renderImportPreview();
    } finally {
      submit.disabled = false;
      submit.textContent = '解析预览';
    }
  }

  function uniqueTag(base) {
    let index = 2;
    let candidate = base;
    while (state.nodes.some(node => node.tag === candidate)) candidate = `${base}-${index++}`;
    return candidate;
  }

  function confirmImport() {
    const policy = document.querySelector('#duplicatePolicy').value;
    let added = 0;
    let skipped = 0;
    for (const raw of asyncUi.preview) {
      const node = { ...raw };
      const index = state.nodes.findIndex(existing => existing.tag === node.tag);
      if (index < 0) {
        state.nodes.push(node);
        added += 1;
      } else if (policy === 'replace') {
        state.nodes[index] = { ...state.nodes[index], ...node };
        added += 1;
      } else if (policy === 'rename') {
        node.tag = uniqueTag(node.tag);
        state.nodes.push(node);
        added += 1;
      } else {
        skipped += 1;
      }
    }
    document.querySelector('#importDialog').close();
    asyncUi.preview = [];
    asyncUi.errors = [];
    setDirty();
    renderNodes();
    renderRouteForm();
    refreshRouteCards();
    toast(`已加入 ${added} 个节点${skipped ? `，跳过 ${skipped} 个` : ''}；点击保存后生效`);
  }

  async function resumeLatestJob() {
    try {
      const data = await api('/api/apply-status', {}, 7000);
      if (data.job && ['queued', 'running'].includes(data.job.status)) pollApply(data.job.id, true);
    } catch {}
  }

  function init() {
    const saveButton = document.querySelector('#saveBtn');
    if (saveButton) saveButton.onclick = saveAsync;
    ensureApplyState();
    enhanceRouting();
    installImportUi();
    if (document.querySelector('#openImportBtn')) {
      document.querySelector('#openImportBtn').onclick = () => {
        asyncUi.preview = [];
        asyncUi.errors = [];
        document.querySelector('#importText').value = '';
        document.querySelector('#importResult').textContent = '';
        renderImportPreview();
        document.querySelector('#importDialog').showModal();
      };
    }
    setTimeout(() => { refreshRouteCards(); resumeLatestJob(); }, 500);
  }

  init();
})();