'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { csrf: '', config: null, nodes: [], deleted: new Set(), dirty: false, latency: {}, saving: false, dashboardLoading: false, dashboardAbort: null };
const pageMeta = {
  dashboard: ['首页', '实时采样现有服务'],
  nodes: ['节点', '新增、导入和测速'],
  routing: ['分流', '每类业务自由选择出口'],
  iwan: ['iWAN', '修改连接账号并自动重连'],
  logs: ['日志', '按需读取服务和网络信息'],
};

function toast(message, bad = false) {
  const element = $('#toast');
  element.textContent = message;
  element.style.background = bad ? '#c93e4a' : '';
  element.classList.add('show');
  clearTimeout(element._timer);
  element._timer = setTimeout(() => element.classList.remove('show'), 3600);
}

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

async function api(path, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const upstreamSignal = options.signal;
  const relayAbort = () => controller.abort();
  if (upstreamSignal) upstreamSignal.addEventListener('abort', relayAbort, { once: true });
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  options.headers = { ...(options.headers || {}), 'Content-Type': 'application/json' };
  if (options.method && options.method !== 'GET') options.headers['X-CSRF-Token'] = state.csrf;
  options.signal = controller.signal;
  try {
    const response = await fetch(path, options);
    let data = {};
    try { data = await response.json(); } catch {}
    if (response.status === 401) {
      showLogin();
      throw new Error('登录已过期');
    }
    if (!response.ok || data.ok === false) throw new Error(data.error || data.message || `HTTP ${response.status}`);
    return data;
  } catch (error) {
    if (error?.name === 'AbortError') {
      if (upstreamSignal?.aborted) throw error;
      throw new Error('请求超时');
    }
    throw error;
  } finally {
    clearTimeout(timer);
    if (upstreamSignal) upstreamSignal.removeEventListener('abort', relayAbort);
  }
}

function setTheme(value) {
  document.documentElement.dataset.theme = value;
  localStorage.setItem('iwan-theme', value);
  $('#themeSelect').value = value;
}

function formatBytes(number, rate = false) {
  number = Number(number) || 0;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let index = 0;
  while (number >= 1024 && index < units.length - 1) { number /= 1024; index += 1; }
  return `${number >= 100 || index === 0 ? number.toFixed(0) : number.toFixed(2)} ${units[index]}${rate ? '/s' : ''}`;
}

function fmtUptime(seconds) {
  seconds = Number(seconds) || 0;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor(seconds % 86400 / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return days ? `${days}天 ${hours}时` : `${hours}时 ${minutes}分`;
}

function setDirty(value = true) {
  state.dirty = value;
  const button = $('#saveBtn');
  if (state.saving) return;
  button.disabled = !value;
  button.textContent = value ? '保存并应用' : '已保存';
}

function setSaving(label = '应用中…') {
  state.saving = true;
  const button = $('#saveBtn');
  button.disabled = true;
  button.textContent = label;
}

function endSaving() {
  state.saving = false;
  setDirty(state.dirty);
}

function showLogin() { $('#app').classList.add('hidden'); $('#login').classList.remove('hidden'); }
function showApp() { $('#login').classList.add('hidden'); $('#app').classList.remove('hidden'); }

function servicePill(id, online) {
  const element = $(id);
  element.classList.toggle('online', !!online);
  element.classList.toggle('offline', !online);
  const name = element.dataset.name || element.textContent.split(' ')[0].split('·')[0].trim();
  element.dataset.name = name;
  element.textContent = `${name} · ${online ? '在线' : '离线'}`;
}

function routeName(key) {
  return { netflix: 'Netflix', ai: 'ChatGPT / Claude', youtube: 'YouTube', telegram: 'Telegram', default: '其他流量' }[key] || key;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
}

function renderRouteSummary(config) {
  const items = [
    ['国内网站', 'direct'],
    ['Netflix', config.mappings.netflix || '未识别'],
    ['ChatGPT / Claude', config.mappings.ai || '未识别'],
    ['YouTube', config.mappings.youtube || '未识别'],
    ['Telegram', config.mappings.telegram || '未识别'],
    ['其他流量', config.default || '未识别'],
  ];
  $('#routeSummary').innerHTML = items.map(([name, outbound]) => `<div class="route-chip"><span>${escapeHtml(name)}</span><strong>${escapeHtml(outbound)}</strong></div>`).join('');
}

function drawChart(history) {
  const canvas = $('#trafficChart');
  if (!canvas || !canvas.isConnected) return;
  const context = canvas.getContext('2d');
  const ratio = devicePixelRatio || 1;
  const width = canvas.clientWidth || 600;
  const height = canvas.clientHeight || 220;
  if (canvas.width !== width * ratio || canvas.height !== height * ratio) { canvas.width = width * ratio; canvas.height = height * ratio; }
  context.setTransform(ratio, 0, 0, ratio, 0, 0); context.clearRect(0, 0, width, height);
  context.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--line'); context.lineWidth = 1;
  for (let index = 1; index < 4; index += 1) { context.beginPath(); context.moveTo(0, height * index / 4); context.lineTo(width, height * index / 4); context.stroke(); }
  const values = (history || []).slice(-80); const maximum = Math.max(1, ...values.flatMap(item => [item.up, item.down]));
  function line(key, color) {
    context.strokeStyle = color; context.lineWidth = 2; context.beginPath();
    values.forEach((item, index) => { const x = index / Math.max(1, values.length - 1) * width; const y = height - (item[key] / maximum) * (height - 10) - 5; index ? context.lineTo(x, y) : context.moveTo(x, y); });
    context.stroke();
  }
  line('down', '#2f8cf5'); line('up', '#17b67a');
}

async function loadDashboard({ silent = false } = {}) {
  if (state.dashboardLoading) state.dashboardAbort?.abort();
  state.dashboardLoading = true;
  state.dashboardAbort = new AbortController();
  $$('[data-refresh]').forEach(button => { button.disabled = true; });
  try {
    const data = await api('/api/dashboard', { signal: state.dashboardAbort.signal }, 12000);
    $('#versionText').textContent = `v${data.version}`;
    servicePill('#pillIwan', !!data.config.iwan && data.services['sing-box']); servicePill('#pillSing', data.services['sing-box']); servicePill('#pillMos', data.services.mosdns);
    $('#iwanState').textContent = data.config.iwan ? '已识别' : '未识别'; $('#iwanPort').textContent = data.config.iwan.listen_port || '—'; $('#iwanPool').textContent = `地址池 ${data.config.iwan.address_pool || data.config.iwan.address || '—'}`;
    $('#singState').textContent = data.services['sing-box'] ? '运行中' : '已停止'; $('#mosState').textContent = data.services.mosdns ? '运行中' : '已停止';
    $('#defaultNode').textContent = data.config.default || '未识别'; $('#nodeCount').textContent = `${data.config.nodes.length} 个节点`;
    $('#cpuText').textContent = `${data.system.cpu}%`; $('#memText').textContent = `${data.system.memory}%`; $('#cpuBar').style.width = `${data.system.cpu}%`; $('#memBar').style.width = `${data.system.memory}%`;
    $('#uptimeText').textContent = fmtUptime(data.system.uptime); $('#uploadText').textContent = formatBytes(data.system.upload_bps, true); $('#downloadText').textContent = formatBytes(data.system.download_bps, true); $('#totalText').textContent = formatBytes(data.system.total_bytes);
    renderRouteSummary(data.config); drawChart(data.history);
  } catch (error) { if (error?.name !== 'AbortError' && !silent) toast(error.message, true); }
  finally { state.dashboardLoading = false; $$('[data-refresh]').forEach(button => { button.disabled = false; }); }
}

async function loadConfig(resetDirty = true) {
  const config = await api('/api/config');
  state.config = config; state.nodes = (config.nodes || []).map(node => ({ ...node, password: '' })); state.deleted.clear();
  renderNodes(); renderRouteForm(); renderIwanForm(); renderRouteSummary(config);
  if (resetDirty) setDirty(false);
  return config;
}

function nodeUses(tag) {
  if (!state.config) return '';
  const uses = []; for (const [key, value] of Object.entries(state.config.mappings || {})) if (value === tag) uses.push(routeName(key));
  if (state.config.default === tag) uses.push('其他流量'); return uses.join('、') || '未分配';
}

function renderNodes() {
  const box = $('#nodesList');
  if (!state.nodes.length) { box.innerHTML = '<div class="hint">尚未识别 Shadowsocks 节点，点击“一键导入”或“新增节点”。</div>'; return; }
  box.innerHTML = state.nodes.map((node, index) => {
    const latency = state.latency[node.tag];
    return `<div class="node-card"><strong>${escapeHtml(node.tag)}</strong><div class="node-meta">${escapeHtml(node.server)}:${escapeHtml(node.server_port)}</div><div class="latency ${latency?.ok ? 'good' : latency ? 'bad' : ''}">${latency ? (latency.ok ? `${latency.latency_ms} ms` : '不可用') : '未测速'}</div><div><div class="node-use">${escapeHtml(nodeUses(node.tag))}</div><div class="node-method">${escapeHtml(node.method || '')}</div></div><div class="node-actions"><button class="btn ghost small" data-edit-node="${index}">编辑</button><button class="btn ghost small" data-del-node="${index}">删除</button></div></div>`;
  }).join('');
  $$('[data-edit-node]').forEach(button => { button.onclick = () => openNode(Number(button.dataset.editNode)); });
  $$('[data-del-node]').forEach(button => { button.onclick = () => deleteNode(Number(button.dataset.delNode)); });
}

function routeOptions(selected = '') { return '<option value="">不设置</option>' + state.nodes.map(node => `<option value="${escapeHtml(node.tag)}" ${node.tag === selected ? 'selected' : ''}>${escapeHtml(node.tag)}</option>`).join(''); }
function renderRouteForm() { $$('[data-route]').forEach(select => { const key = select.dataset.route; const value = key === 'default' ? (state.config?.default || '') : (state.config?.mappings?.[key] || ''); select.innerHTML = routeOptions(value); select.onchange = () => setDirty(); }); }

function renderIwanForm() {
  const box = $('#iwanForm'); const iwan = state.config?.iwan || {}; const poolKey = Object.prototype.hasOwnProperty.call(iwan, 'address_pool') ? 'address_pool' : 'address';
  const fields = [['listen', '监听地址', iwan.listen || '::', 'text'], ['listen_port', '监听端口', iwan.listen_port || 8000, 'number'], [poolKey, '地址池', iwan.address_pool || iwan.address || '', 'text'], ['mtu', 'MTU', iwan.mtu || 1400, 'number'], ['username', '用户名', iwan.username || '', 'text'], ['password', '新密码', '', 'password']];
  box.innerHTML = fields.map(([key, name, value, type]) => { const placeholder = key === 'password' ? (iwan.has_password ? '留空保留当前密码' : '请输入 iWAN 密码') : ''; return `<label>${name}<input data-iwan="${key}" type="${type}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}"></label>`; }).join('');
  $$('[data-iwan]').forEach(input => { input.oninput = () => setDirty(); });
}

function ensureMethodOption(method) {
  const select = $('#nodeMethod'); $$('option[data-custom]', select).forEach(option => option.remove());
  if (method && ![...select.options].some(option => option.value === method)) { const option = document.createElement('option'); option.value = method; option.textContent = `${method}（现有）`; option.dataset.custom = '1'; select.appendChild(option); }
  select.value = method || 'aes-128-gcm';
}

function openNode(index = -1) {
  $('#nodeDialogTitle').textContent = index < 0 ? '新增节点' : '编辑节点'; $('#nodeIndex').value = index;
  const node = index < 0 ? { method: 'aes-128-gcm' } : state.nodes[index]; $('#nodeTag').value = node.tag || ''; $('#nodeServer').value = node.server || ''; $('#nodePort').value = node.server_port || ''; ensureMethodOption(node.method || 'aes-128-gcm'); $('#nodePassword').value = ''; $('#nodePlugin').value = node.plugin || ''; $('#nodePluginOpts').value = node.plugin_opts || ''; $('#nodeDialog').showModal();
}

function deleteNode(index) { const node = state.nodes[index]; if (!confirm(`删除节点 ${node.tag}？保存前不会影响当前配置。`)) return; state.deleted.add(node.tag); state.nodes.splice(index, 1); setDirty(); renderNodes(); renderRouteForm(); }
function page(name) {
  if (!pageMeta[name]) return;
  $$('.page').forEach(element => element.classList.toggle('active', element.id === `page-${name}`));
  $$('[data-page]').forEach(element => element.classList.toggle('active', element.dataset.page === name));
  $('#pageTitle').textContent = pageMeta[name][0]; $('#pageSub').textContent = pageMeta[name][1];
  if (name === 'dashboard') loadDashboard({ silent: true });
}

function desiredPayload() {
  const mappings = {}; $$('[data-route]').forEach(select => { if (select.dataset.route !== 'default') mappings[select.dataset.route] = select.value; });
  const iwan = {}; $$('[data-iwan]').forEach(input => { let value = input.value; if (['listen_port', 'mtu'].includes(input.dataset.iwan)) value = Number(value); iwan[input.dataset.iwan] = value; });
  return { nodes: state.nodes, deleted_tags: [...state.deleted], mappings, default: $('[data-route="default"]').value, iwan };
}

function configMatches(config, desired) {
  if (!config) return false;
  if ((config.default || '') !== (desired.default || '')) return false;
  for (const key of ['netflix', 'ai', 'youtube', 'telegram']) if ((config.mappings?.[key] || '') !== (desired.mappings?.[key] || '')) return false;
  const tags = new Set((config.nodes || []).map(node => node.tag));
  for (const node of desired.nodes || []) if (!tags.has(node.tag)) return false;
  for (const tag of desired.deleted_tags || []) if (tags.has(tag)) return false;
  const currentIwan = config.iwan || {}; const wantedIwan = desired.iwan || {};
  for (const key of ['listen', 'listen_port', 'address', 'address_pool', 'mtu', 'username']) {
    if (wantedIwan[key] !== undefined && wantedIwan[key] !== '' && String(currentIwan[key] ?? '') !== String(wantedIwan[key])) return false;
  }
  return true;
}

async function waitForApplied(desired, maxMs = 60000) {
  const started = Date.now(); let lastError = '';
  while (Date.now() - started < maxMs) {
    const elapsed = Math.floor((Date.now() - started) / 1000); setSaving(`确认中 ${elapsed}s`);
    try {
      const config = await api('/api/config', {}, 5000);
      if (configMatches(config, desired)) {
        state.config = config; state.nodes = (config.nodes || []).map(node => ({ ...node, password: '' })); state.deleted.clear(); renderNodes(); renderRouteForm(); renderIwanForm(); renderRouteSummary(config); state.dirty = false;
        return true;
      }
    } catch (error) { lastError = error.message; }
    await sleep(1800);
  }
  throw new Error(lastError ? `应用状态确认超时：${lastError}` : '应用状态确认超时，请到日志页查看 sing-box 日志');
}

async function save() {
  if (!state.config || !state.dirty || state.saving) return;
  const desired = desiredPayload(); setSaving('提交中…');
  try {
    let response = null;
    try { response = await api('/api/save', { method: 'POST', body: JSON.stringify(desired) }, 10000); }
    catch (error) {
      if (!/请求超时/.test(error.message)) throw error;
      toast('服务正在重启，后台继续应用，正在确认结果');
    }
    if (response?.message) toast(response.message);
    await waitForApplied(desired);
    state.dirty = false; await loadDashboard(); toast('分流已应用，sing-box 已恢复运行');
  } catch (error) {
    state.dirty = true; toast(error.message, true);
  } finally { endSaving(); }
}

async function action(actionName, service = '', trigger = null) {
  try {
    if (trigger) trigger.disabled = true;
    const data = await api('/api/action', { method: 'POST', body: JSON.stringify({ action: actionName, service }) }, 25000);
    toast(data.message || '完成');
    setTimeout(() => loadDashboard({ silent: true }), 800);
  } catch (error) { toast(error.message, true); }
  finally { if (trigger) trigger.disabled = false; }
}
async function testNodes() { try { $('#testNodesBtn').disabled = true; const data = await api('/api/latency', { method: 'POST', body: JSON.stringify({ nodes: state.nodes }) }, 30000); state.latency = Object.fromEntries(data.results.map(result => [result.tag, result])); renderNodes(); toast('测速完成'); } catch (error) { toast(error.message, true); } finally { $('#testNodesBtn').disabled = false; } }

async function importNodes() {
  const text = $('#importText').value.trim(); if (!text) { $('#importResult').textContent = '请先粘贴节点内容'; return; }
  try {
    const data = await api('/api/import-nodes', { method: 'POST', body: JSON.stringify({ text }) });
    for (const node of data.nodes) { const index = state.nodes.findIndex(existing => existing.tag === node.tag); index >= 0 ? state.nodes[index] = { ...state.nodes[index], ...node } : state.nodes.push(node); }
    $('#importResult').textContent = data.errors?.length ? `已加入 ${data.nodes.length} 个节点\n${data.errors.join('\n')}` : `已加入 ${data.nodes.length} 个节点`;
    setDirty(); renderNodes(); renderRouteForm(); toast(`已解析 ${data.nodes.length} 个节点`); setTimeout(() => $('#importDialog').close(), 650);
  } catch (error) { $('#importResult').textContent = error.message; }
}

async function loadLogs() { try { $('#logsText').textContent = '读取中…'; const data = await api(`/api/logs?service=${encodeURIComponent($('#logService').value)}`); $('#logsText').textContent = data.logs || '暂无日志'; } catch (error) { $('#logsText').textContent = error.message; } }
async function loadNetwork() { try { $('#networkText').textContent = '读取中…'; const data = await api('/api/network'); $('#networkText').textContent = `[路由]\n${data.routes}\n\n[监听端口]\n${data.ports}`; } catch (error) { $('#networkText').textContent = error.message; } }

async function loadDiagnostics() {
  const box = $('#diagnosticsBox');
  box.innerHTML = '<div class="hint">体检中…</div>';
  try {
    const data = await api('/api/diagnostics', {}, 8000);
    const checks = data.checks.map(item => `<li class="${item.ok ? 'ok' : 'warn'}"><b>${item.ok ? '✓' : '!'}</b><span>${escapeHtml(item.name)}</span><em>${escapeHtml(item.detail)}</em></li>`).join('');
    const steps = data.next_steps.map(step => `<li>${escapeHtml(step)}</li>`).join('');
    box.innerHTML = `<div class="health-score"><strong>${data.score}</strong><span>完整度</span></div><ul class="check-list">${checks}</ul><div class="next-steps"><b>建议</b><ol>${steps}</ol></div>`;
  } catch (error) {
    box.innerHTML = `<div class="error-text">${escapeHtml(error.message)}</div>`;
  }
}

async function boot() { setTheme(localStorage.getItem('iwan-theme') || 'system'); try { const session = await api('/api/session'); if (!session.authenticated) { showLogin(); return; } state.csrf = session.csrf; showApp(); await Promise.all([loadConfig(), loadDashboard()]); } catch { showLogin(); } }

$('#loginForm').addEventListener('submit', async event => { event.preventDefault(); try { const data = await api('/api/login', { method: 'POST', body: JSON.stringify({ username: $('#loginUser').value, password: $('#loginPass').value }) }); state.csrf = data.csrf; showApp(); await Promise.all([loadConfig(), loadDashboard()]); } catch (error) { $('#loginError').textContent = error.message; } });
$('#themeSelect').onchange = event => setTheme(event.target.value);
document.addEventListener('click', event => {
  const pageButton = event.target.closest('[data-page]');
  if (pageButton) { page(pageButton.dataset.page); return; }
  const refreshButton = event.target.closest('[data-refresh]');
  if (refreshButton) { loadDashboard(); return; }
  const actionButton = event.target.closest('[data-action]');
  if (actionButton) action(actionButton.dataset.action, actionButton.dataset.service || '', actionButton);
});
$('#logoutBtn').onclick = async () => { try { await api('/api/logout', { method: 'POST', body: '{}' }); } finally { showLogin(); } };
$('#saveBtn').onclick = save; $('#addNodeBtn').onclick = () => openNode(); $('#testNodesBtn').onclick = testNodes;
$('#openImportBtn').onclick = () => { $('#importText').value = ''; $('#importResult').textContent = ''; $('#importDialog').showModal(); };
$('#closeNodeDialog').onclick = $('#cancelNodeBtn').onclick = () => $('#nodeDialog').close(); $('#closeImportDialog').onclick = $('#cancelImportBtn').onclick = () => $('#importDialog').close();
$('#nodeForm').addEventListener('submit', event => { event.preventDefault(); const index = Number($('#nodeIndex').value); const node = { tag: $('#nodeTag').value.trim(), server: $('#nodeServer').value.trim(), server_port: Number($('#nodePort').value), method: $('#nodeMethod').value, password: $('#nodePassword').value, plugin: $('#nodePlugin').value.trim(), plugin_opts: $('#nodePluginOpts').value.trim() }; if (index < 0) state.nodes.push(node); else state.nodes[index] = { ...state.nodes[index], ...node }; $('#nodeDialog').close(); setDirty(); renderNodes(); renderRouteForm(); });
$('#importForm').addEventListener('submit', event => { event.preventDefault(); importNodes(); }); $('#loadLogsBtn').onclick = loadLogs; $('#loadNetworkBtn').onclick = loadNetwork;
$('#diagnoseBtn').onclick = loadDiagnostics;
setInterval(() => { if (!document.hidden && $('#page-dashboard').classList.contains('active') && !state.saving) loadDashboard({ silent: true }); }, 15000);
window.addEventListener('beforeunload', event => { if (state.dirty && !state.saving) { event.preventDefault(); event.returnValue = ''; } });
boot();
