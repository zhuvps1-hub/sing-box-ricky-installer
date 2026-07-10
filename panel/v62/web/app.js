'use strict';

let mosdnsCache = null;

function dnsFormatBytes(number) {
  number = Number(number) || 0;
  const units = ['B','KB','MB','GB','TB']; let index = 0;
  while (number >= 1024 && index < units.length - 1) { number /= 1024; index += 1; }
  return `${number >= 100 || index === 0 ? number.toFixed(0) : number.toFixed(2)} ${units[index]}`;
}
function dnsFmtTime(timestamp) { return timestamp ? new Date(timestamp * 1000).toLocaleString() : '—'; }
function dnsEscape(value) { return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])); }

function showDnsPage() {
  document.querySelectorAll('.page').forEach(element => element.classList.remove('active'));
  document.querySelectorAll('[data-page]').forEach(element => element.classList.remove('active'));
  document.querySelector('#dnsNavBtn').classList.add('active');
  document.querySelector('#page-mosdns').classList.add('active');
  document.querySelector('#pageTitle').textContent = 'DNS';
  document.querySelector('#pageSub').textContent = '完整管理 mosdns 配置与规则文件';
  loadMosdns(false);
}

document.querySelector('#dnsNavBtn').addEventListener('click', showDnsPage);
document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => document.querySelector('#dnsNavBtn').classList.remove('active')));

function renderMosdns(data) {
  const summary = data.summary || {};
  document.querySelector('#dnsServiceState').textContent = data.service_active ? '运行中' : '已停止';
  document.querySelector('#dnsConfigSize').textContent = data.available ? dnsFormatBytes(data.size) : '未找到';
  document.querySelector('#dnsPluginCount').textContent = summary.plugins ?? 0;
  document.querySelector('#dnsBackupCount').textContent = data.backups?.length ?? 0;
  document.querySelector('#dnsConfigPath').textContent = data.path || '/etc/mosdns/config.yaml';
  const addresses = (summary.addresses || []).join('、') || '未识别';
  const upstreams = (summary.upstreams || []).join('、') || '未识别';
  document.querySelector('#dnsSummary').innerHTML = `
    <div class="summary-item"><span>配置行数</span><strong>${summary.lines || 0}</strong></div>
    <div class="summary-item"><span>监听/地址</span><strong>${dnsEscape(addresses)}</strong></div>
    <div class="summary-item"><span>上游地址</span><strong>${dnsEscape(upstreams)}</strong></div>
    <div><span class="hint">插件标签</span><div class="summary-tags">${(summary.tags || []).map(tag => `<code>${dnsEscape(tag)}</code>`).join('') || '<span class="hint">未识别</span>'}</div></div>`;
  document.querySelector('#dnsBackupSelect').innerHTML = (data.backups || []).length
    ? data.backups.map(item => `<option value="${dnsEscape(item.name)}">${dnsEscape(item.name)} · ${dnsFormatBytes(item.size)} · ${dnsFmtTime(item.mtime)}</option>`).join('')
    : '<option value="">暂无备份</option>';
  document.querySelector('#dnsFileSelect').innerHTML = (data.files || []).length
    ? data.files.map(item => `<option value="${dnsEscape(item.name)}">${dnsEscape(item.name)} · ${dnsFormatBytes(item.size)}</option>`).join('')
    : '<option value="">暂无可编辑文本文件</option>';
}

async function loadMosdns(force = false) {
  if (mosdnsCache && !force) return;
  const editor = document.querySelector('#dnsConfigEditor'); editor.placeholder = '读取中…';
  try {
    const data = await api('/api/mosdns', {}, 20000);
    mosdnsCache = data; editor.value = data.config || ''; editor.placeholder = data.error || 'mosdns config.yaml'; renderMosdns(data);
  } catch (error) { toast(error.message, true); editor.placeholder = error.message; }
}

async function saveMosdns() {
  if (!confirm('保存后会重启 mosdns，启动失败将自动回滚。继续吗？')) return;
  const button = document.querySelector('#dnsSaveBtn'); button.disabled = true; button.textContent = '应用中…';
  try {
    const data = await api('/api/mosdns/save', {method:'POST', body:JSON.stringify({config:document.querySelector('#dnsConfigEditor').value})}, 75000);
    toast(data.message || 'mosdns 已应用'); mosdnsCache = null; await loadMosdns(true); await loadDashboard();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = '保存并重启'; }
}

async function mosdnsAction(actionName, extra = {}) {
  try {
    const data = await api('/api/mosdns/action', {method:'POST', body:JSON.stringify({action:actionName, ...extra})}, 75000);
    toast(data.message || '完成'); mosdnsCache = null; await loadMosdns(true); await loadDashboard();
  } catch (error) { toast(error.message, true); }
}

async function loadMosdnsFile() {
  const name = document.querySelector('#dnsFileSelect').value || document.querySelector('#dnsFileName').value.trim();
  if (!name) { toast('请选择或填写文件名', true); return; }
  try {
    const data = await api(`/api/mosdns/file?name=${encodeURIComponent(name)}`, {}, 20000);
    document.querySelector('#dnsFileName').value = data.name; document.querySelector('#dnsFileEditor').value = data.content;
    document.querySelector('#dnsFileHint').textContent = `${data.name} · ${dnsFormatBytes(data.size)} · ${dnsFmtTime(data.mtime)}`;
  } catch (error) { toast(error.message, true); }
}

async function saveMosdnsFile() {
  const name = document.querySelector('#dnsFileName').value.trim() || document.querySelector('#dnsFileSelect').value;
  if (!name) { toast('请填写文件名', true); return; }
  try {
    const data = await api('/api/mosdns/file/save', {method:'POST', body:JSON.stringify({name, content:document.querySelector('#dnsFileEditor').value})}, 30000);
    toast(data.message); mosdnsCache = null; await loadMosdns(true); document.querySelector('#dnsFileName').value = name;
  } catch (error) { toast(error.message, true); }
}

document.querySelector('#dnsReloadBtn').onclick = () => { mosdnsCache = null; loadMosdns(true); };
document.querySelector('#dnsSaveBtn').onclick = saveMosdns;
document.querySelector('#dnsBackupBtn').onclick = () => mosdnsAction('backup');
document.querySelector('#dnsRestartBtn').onclick = () => mosdnsAction('restart');
document.querySelector('#dnsRestoreBtn').onclick = () => {
  const name = document.querySelector('#dnsBackupSelect').value;
  if (name && confirm(`恢复备份 ${name}？`)) mosdnsAction('restore', {name});
};
document.querySelector('#dnsLoadFileBtn').onclick = loadMosdnsFile;
document.querySelector('#dnsSaveFileBtn').onclick = saveMosdnsFile;
document.querySelector('#dnsNewFileBtn').onclick = () => {
  document.querySelector('#dnsFileName').value = ''; document.querySelector('#dnsFileEditor').value = '';
  document.querySelector('#dnsFileHint').textContent = '填写相对路径，例如 rules/custom.txt'; document.querySelector('#dnsFileName').focus();
};
document.querySelector('#dnsFileSelect').onchange = () => { document.querySelector('#dnsFileName').value = document.querySelector('#dnsFileSelect').value; };
