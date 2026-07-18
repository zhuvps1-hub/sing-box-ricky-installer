'use strict';

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  function ensureRememberControl() {
    if ($('#rememberLogin')) return;
    const password = $('#loginPass');
    if (!password) return;
    const row = document.createElement('label');
    row.className = 'remember-login-row';
    row.innerHTML = '<input id="rememberLogin" type="checkbox" checked><span>记住登录状态（30 天）</span>';
    password.closest('label')?.insertAdjacentElement('afterend', row);
  }

  function runtimeBox() {
    let box = $('#v8Runtime');
    if (box) return box;
    const card = $('#page-dashboard .metric-grid');
    if (!card) return null;
    box = document.createElement('section');
    box.id = 'v8Runtime';
    box.className = 'panel-card';
    box.innerHTML = `
      <div class="panel-head"><div><h3>链路与策略状态</h3><p>显示真实监听、在线客户端和现代分流规则</p></div><span id="v8RouteBadge" class="v8-route-badge">检查中</span></div>
      <div class="v8-runtime">
        <div><span>iWAN 监听</span><strong id="v8Listen">检查中</strong></div>
        <div><span>在线客户端</span><strong id="v8Clients">0</strong></div>
        <div><span>分流规则</span><strong id="v8Rules">检查中</strong></div>
      </div>
      <div id="v8Peers" class="hint" style="margin-top:12px"></div>`;
    card.insertAdjacentElement('afterend', box);
    return box;
  }

  function updateRuntime(data) {
    runtimeBox();
    const iwan = data?.config?.iwan_runtime || {};
    const routing = data?.config?.routing_runtime || {};
    const listen = $('#v8Listen');
    const clients = $('#v8Clients');
    const rules = $('#v8Rules');
    const badge = $('#v8RouteBadge');
    const peers = $('#v8Peers');
    if (listen) {
      listen.textContent = iwan.listening ? `${iwan.port}/TCP+UDP` : '未监听';
      listen.className = iwan.listening ? 'v8-ok' : 'v8-bad';
    }
    if (clients) {
      clients.textContent = String(iwan.client_count || 0);
      clients.className = iwan.client_count > 0 ? 'v8-ok' : '';
    }
    if (rules) {
      rules.textContent = routing.effective ? `${routing.modern_rule_count || 0} 条生效` : '未生效';
      rules.className = routing.effective ? 'v8-ok' : 'v8-bad';
    }
    if (badge) {
      badge.textContent = routing.effective ? '策略已生效' : '需要重新保存';
      badge.classList.toggle('v8-bad', !routing.effective);
    }
    if (peers) {
      peers.textContent = iwan.peers?.length ? `客户端：${iwan.peers.join('、')}` : '尚未检测到已连接的 iWAN 客户端。';
    }
    const stateText = $('#iwanState');
    if (stateText) stateText.textContent = iwan.listening ? (iwan.client_count ? `${iwan.client_count} 台在线` : '等待连接') : '未监听';
  }

  async function fetchRuntime() {
    try {
      const response = await fetch('/api/dashboard', { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) return;
      const data = await response.json();
      updateRuntime(data);
    } catch {}
  }

  function markRouteSelections() {
    $$('[data-route]').forEach(select => {
      const label = select.closest('label');
      if (!label) return;
      label.dataset.selected = select.value || '未设置';
      select.addEventListener('change', () => { label.dataset.selected = select.value || '未设置'; });
    });
  }

  function compactHeader() {
    const version = $('#versionText');
    if (version) version.textContent = 'v8';
    const hero = $('#page-dashboard .hero-card h2');
    if (hero) hero.textContent = '网关运行中心';
    const heroText = $('#page-dashboard .hero-card p');
    if (heroText) heroText.textContent = '实时查看 iWAN 链路、出口节点、业务分流和系统资源；修改后统一校验、应用并自动回滚。';
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
      if (url.includes('/api/dashboard') && response.ok) {
        response.clone().json().then(updateRuntime).catch(() => {});
      }
    } catch {}
    return response;
  };

  window.addEventListener('DOMContentLoaded', () => {
    ensureRememberControl();
    compactHeader();
    runtimeBox();
    markRouteSelections();
    fetchRuntime();
    setInterval(() => { if (!document.hidden) fetchRuntime(); }, 10000);
    const observer = new MutationObserver(() => markRouteSelections());
    const routing = $('#page-routing');
    if (routing) observer.observe(routing, { childList: true, subtree: true });
  });
})();
