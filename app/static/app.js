let S = { nodes: [], routing: {}, iwan: {}, service: {} };

const labels = {
  cn: '国内',
  ai: 'AI',
  google: 'Google',
  youtube: 'YouTube',
  netflix: 'Netflix',
  tiktok: 'TikTok',
  telegram: 'Telegram',
  default: '默认出口'
};

const protocolNames = {
  shadowsocks: 'SS',
  vmess: 'VMess',
  vless: 'VLESS',
  trojan: 'Trojan',
  tuic: 'TUIC',
  hysteria2: 'Hysteria2'
};

const $ = id => document.getElementById(id);

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  });
  let body = {};
  try {
    body = await response.json();
  } catch (_) {
    body = {};
  }
  if (!response.ok) throw new Error(body.error || '请求失败');
  return body;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[char]);
}

async function boot() {
  try {
    const me = await api('/api/me');
    if (!me.ok) {
      $('login').classList.remove('hidden');
      return;
    }
    $('app').classList.remove('hidden');
    $('su').value = me.username;
    await load();
  } catch (error) {
    $('login').classList.remove('hidden');
    $('le').textContent = error.message;
  }
}

async function login(event) {
  event.preventDefault();
  $('le').textContent = '';
  try {
    await api('/api/login', {
      method: 'POST',
      body: JSON.stringify({ username: $('lu').value, password: $('lp').value })
    });
    location.reload();
  } catch (error) {
    $('le').textContent = error.message;
  }
}

async function logout() {
  await api('/api/logout', { method: 'POST', body: '{}' });
  location.reload();
}

document.querySelectorAll('nav button').forEach(button => {
  button.onclick = () => {
    document.querySelectorAll('nav button,.page').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    $(button.dataset.p).classList.add('active');
  };
});

function options(value) {
  return ['direct', ...S.nodes.map(node => node.tag)].map(tag => {
    const title = tag === 'direct' ? 'Direct' : tag;
    return `<option value="${esc(tag)}" ${tag === value ? 'selected' : ''}>${esc(title)}</option>`;
  }).join('');
}

async function load() {
  S = await api('/api/state');
  render();
}

function render() {
  const running = !!S.service.singbox;
  $('svc').textContent = running ? '运行中' : '已停止';
  $('hsvc').textContent = running ? '正常' : '异常';
  $('hsvc').style.color = running ? 'var(--ok)' : 'var(--bad)';
  $('hcount').textContent = S.nodes.length;
  $('hport').textContent = S.iwan.port || 8000;
  $('hdefault').textContent = S.routing.default === 'direct' ? 'Direct' : (S.routing.default || 'Direct');

  const routeKeys = ['cn', 'ai', 'google', 'youtube', 'netflix', 'tiktok', 'telegram', 'default'];
  $('hroute').innerHTML = routeKeys.map(key => `
    <div class="row">
      <span>${labels[key]}</span>
      <b>${esc(S.routing[key] === 'direct' ? 'Direct' : (S.routing[key] || '未设置'))}</b>
    </div>
  `).join('');

  $('routeList').innerHTML = routeKeys.map(key => `
    <div class="row">
      <span>${labels[key]}</span>
      <select data-route="${key}">${options(S.routing[key] || 'direct')}</select>
    </div>
  `).join('');

  $('nodeList').innerHTML = S.nodes.length
    ? S.nodes.map((node, index) => nodeHtml(node, index)).join('')
    : '<div class="empty card"><b>还没有节点</b><span>点击右上角“一键导入”，粘贴分享链接或订阅地址。</span></div>';

  $('ie').checked = !!S.iwan.enabled;
  $('il').value = S.iwan.listen || '::';
  $('ip').value = S.iwan.port || 8000;
  $('ipool').value = S.iwan.pool || '10.10.10.0/24';
  $('imtu').value = S.iwan.mtu || 1400;
  $('iu').value = S.iwan.username || '';
  $('iwp').value = '';
}

function nodeHtml(node, index) {
  const protocol = protocolNames[node.type] || node.type.toUpperCase();
  return `
    <article class="node-card card">
      <div class="node-main">
        <span class="protocol">${esc(protocol)}</span>
        <div class="node-title">
          <b>${esc(node.tag)}</b>
          <span>${esc(node.server)}:${esc(node.port)}</span>
        </div>
      </div>
      <div class="node-source" title="${esc(node.source)}">来源：${esc(node.source || '导入')}</div>
      <div class="node-actions">
        <button class="ghost" onclick="testNode(${index},this)">测速</button>
        <button class="ghost" onclick="renameNode(${index})">改名</button>
        <button class="danger" onclick="deleteNode(${index})">删除</button>
      </div>
    </article>
  `;
}

async function testNode(index, button) {
  const node = S.nodes[index];
  if (!node) return;
  const original = button.textContent;
  button.disabled = true;
  button.textContent = '测试中';
  try {
    const result = await api('/api/latency', {
      method: 'POST',
      body: JSON.stringify({ tag: node.tag })
    });
    button.textContent = result.ok ? `${result.ms}ms` : '失败';
    button.title = result.mode || result.error || '';
  } catch (error) {
    button.textContent = '失败';
    button.title = error.message;
  }
  setTimeout(() => {
    button.disabled = false;
    button.textContent = original;
  }, 1800);
}

async function renameNode(index) {
  const node = S.nodes[index];
  if (!node) return;
  const next = prompt('输入新的节点名称', node.tag);
  if (!next || next.trim() === node.tag) return;
  try {
    await api('/api/rename-node', {
      method: 'POST',
      body: JSON.stringify({ tag: node.tag, new: next.trim() })
    });
    $('msg').textContent = '✓ 节点已改名并生效';
    await load();
  } catch (error) {
    alert(error.message);
  }
}

async function deleteNode(index) {
  const node = S.nodes[index];
  if (!node || !confirm(`删除节点“${node.tag}”？`)) return;
  try {
    await api('/api/delete-node', {
      method: 'POST',
      body: JSON.stringify({ tag: node.tag })
    });
    $('msg').textContent = '✓ 节点已删除并生效';
    await load();
  } catch (error) {
    alert(error.message);
  }
}

function openImport() {
  $('importError').textContent = '';
  $('replaceNodes').checked = false;
  $('importModal').classList.remove('hidden');
  setTimeout(() => $('importText').focus(), 50);
}

function closeImport() {
  $('importModal').classList.add('hidden');
}

function modalBackdrop(event) {
  if (event.target === $('importModal')) closeImport();
}

async function importNodes() {
  const source = $('importText').value.trim();
  const button = $('importButton');
  $('importError').textContent = '';
  if (!source) {
    $('importError').textContent = '请先粘贴分享链接、订阅地址或配置内容';
    return;
  }
  button.disabled = true;
  button.textContent = '识别并导入中…';
  try {
    const result = await api('/api/import', {
      method: 'POST',
      body: JSON.stringify({ source, replace: $('replaceNodes').checked })
    });
    const warningText = result.warnings && result.warnings.length
      ? `，${result.warnings.length} 条内容未导入`
      : '';
    $('msg').textContent = `✓ 导入 ${result.added} 个，跳过重复 ${result.skipped} 个${warningText}`;
    $('importText').value = '';
    closeImport();
    await load();
  } catch (error) {
    $('importError').textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = '开始导入';
  }
}

async function applyAll() {
  document.querySelectorAll('[data-route]').forEach(element => {
    S.routing[element.dataset.route] = element.value;
  });
  S.iwan = {
    enabled: $('ie').checked,
    listen: $('il').value.trim(),
    port: Number($('ip').value),
    pool: $('ipool').value.trim(),
    mtu: Number($('imtu').value),
    username: $('iu').value.trim(),
    password: $('iwp').value
  };

  const button = $('apply');
  button.disabled = true;
  button.textContent = '应用中…';
  $('msg').textContent = '正在检查配置并快速重载';
  try {
    await api('/api/apply', {
      method: 'POST',
      body: JSON.stringify({ routing: S.routing, iwan: S.iwan })
    });
    $('msg').textContent = '✓ 已生效';
    await load();
  } catch (error) {
    $('msg').textContent = `失败：${error.message}`;
  } finally {
    setTimeout(() => {
      button.disabled = false;
      button.textContent = '应用配置';
    }, 900);
  }
}

async function changePassword() {
  try {
    await api('/api/password', {
      method: 'POST',
      body: JSON.stringify({ username: $('su').value, old: $('so').value, new: $('sn').value })
    });
    alert('账号已保存');
    $('so').value = '';
    $('sn').value = '';
  } catch (error) {
    alert(error.message);
  }
}

document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeImport();
});

boot();
