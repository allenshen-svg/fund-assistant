/* trump-alert.js — 特朗普言论金融预警前端 */

const API_BASE = window.location.origin;

// ==================== API ====================
async function fetchTrumpData() {
  const resp = await fetch(`${API_BASE}/api/trump-alert`);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function triggerRefresh() {
  const resp = await fetch(`${API_BASE}/api/trump-alert/trigger`, { method: 'POST' });
  return resp.json();
}

// ==================== 渲染: 概率卡片 ====================
function renderProbCards(predictions) {
  const grid = document.getElementById('probGrid');
  if (!grid || !predictions) return;
  grid.innerHTML = '';

  const order = [
    'gold', 'crude_oil', 'defense', 'ai_tech',
    'semiconductor', 'robotics', 'new_energy', 'hk_tech',
    'nev', 'lithium_battery', 'nonferrous_metals', 'tech_nasdaq',
    'sp500', 'china_a50', 'natural_gas', 'usd_index',
    'crypto_btc', 'copper', 'treasury_bond', 'eu_stocks'
  ];
  for (const key of order) {
    const p = predictions[key];
    if (!p) continue;
    const prob = p.probability;
    const pct = (prob * 100).toFixed(1);
    let barClass = 'neut', sigClass = 'neutral';
    if (prob >= 0.6) { barClass = 'bull'; sigClass = 'bullish'; }
    else if (prob <= 0.4) { barClass = 'bear'; sigClass = 'bearish'; }

    const card = document.createElement('div');
    card.className = 'prob-card';
    const hitInfo = p.hit_rate != null
      ? `<div class="hit-rate">历史命中 ${(p.hit_rate * 100).toFixed(0)}%</div>`
      : '';
    card.innerHTML = `
      <div class="asset-label">${p.name_en}</div>
      <div class="asset-title">${p.name}</div>
      <span class="signal-badge signal ${sigClass}">${p.signal_zh}</span>
      <div class="prob-bar-wrap">
        <div class="prob-bar ${barClass}" style="width:${pct}%">${pct}%</div>
      </div>
      <div class="logic">${p.logic}</div>
      ${hitInfo}
    `;
    grid.appendChild(card);
  }
}

// ==================== 渲染: 警报横幅 ====================
function renderAlerts(alerts) {
  const el = document.getElementById('alertBanner');
  if (!el) return;
  if (!alerts || alerts.length === 0) {
    el.className = 'alert-banner calm';
    el.innerHTML = '<h3>🟢 当前无重大预警信号</h3><p style="color:var(--muted);font-size:13px">市场处于相对平静状态</p>';
    return;
  }
  el.className = 'alert-banner';
  let html = `<h3>🚨 ${alerts.length} 条预警信号</h3>`;
  for (const a of alerts) {
    const cls = a.probability >= 0.7 ? 'bullish' : 'bearish';
    html += `<div class="alert-item">
      <span class="asset-name">${a.asset}</span>
      <span class="signal ${cls}">${a.signal} (${(a.probability * 100).toFixed(1)}%)</span>
    </div>`;
  }
  el.innerHTML = html;
}

// ==================== 渲染: 言论列表 ====================
function renderStatements(statements) {
  const el = document.getElementById('stmtList');
  if (!el) return;
  if (!statements || statements.length === 0) {
    el.innerHTML = '<div style="color:var(--muted);text-align:center;padding:30px">暂无数据</div>';
    return;
  }
  el.innerHTML = '';
  for (const s of statements) {
    const card = document.createElement('div');
    card.className = 'stmt-card';
    const tagCls = s.sentiment || 'neutral';
    const tagLabel = { hawkish: '鹰派 🦅', dovish: '鸽派 🕊️', neutral: '中性' }[tagCls] || '中性';
    const policyTag = s.is_policy ? '<span class="sentiment-tag" style="background:rgba(251,191,36,0.2);color:var(--gold)">实际政策</span>' : '';
    card.innerHTML = `
      <div class="stmt-title">${escHtml(s.title)}</div>
      <div class="stmt-meta">
        <span>${s.source || ''}</span>
        <span>${formatTime(s.time)}</span>
        <span class="sentiment-tag ${tagCls}">${tagLabel} ${(s.score * 100).toFixed(0)}%</span>
        ${policyTag}
      </div>
      <div class="stmt-summary">${escHtml(s.summary_zh || '')}</div>
    `;
    el.appendChild(card);
  }
}

// ==================== 工具 ====================
function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function formatTime(t) {
  if (!t) return '';
  try {
    const d = new Date(t);
    if (isNaN(d)) return t;
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch { return t; }
}

// ==================== 主流程 ====================
let _refreshing = false;

async function loadData() {
  const loading = document.getElementById('loading');
  const content = document.getElementById('mainContent');
  try {
    const data = await fetchTrumpData();
    if (loading) loading.style.display = 'none';
    if (content) content.style.display = 'block';

    // 更新 meta
    const metaEl = document.getElementById('metaRow');
    if (metaEl && data.updated_at) {
      const t = new Date(data.updated_at).toLocaleString('zh-CN');
      metaEl.innerHTML = `
        <span class="meta-pill">📡 ${data.statement_count || 0} 条言论</span>
        <span class="meta-pill">🚨 ${data.alert_count || 0} 条警报</span>
        <span class="meta-pill">🕐 ${t}</span>
      `;
    }
    renderAlerts(data.alerts);
    renderProbCards(data.predictions);
    renderStatements(data.statements);
  } catch (e) {
    if (loading) loading.innerHTML = `<p style="color:var(--red)">加载失败: ${e.message}</p><p style="color:var(--muted);font-size:13px;margin-top:8px">请稍后刷新或点击手动触发</p>`;
  }
}

async function handleRefresh() {
  if (_refreshing) return;
  _refreshing = true;
  const btn = document.getElementById('btnRefresh');
  if (btn) { btn.disabled = true; btn.textContent = '分析中...'; }
  try {
    await triggerRefresh();
    // 等待后端处理
    await new Promise(r => setTimeout(r, 5000));
    await loadData();
  } catch (e) {
    alert('触发失败: ' + e.message);
  } finally {
    _refreshing = false;
    if (btn) { btn.disabled = false; btn.textContent = '🔄 手动触发分析'; }
  }
}

// 页面加载
document.addEventListener('DOMContentLoaded', () => {
  loadData();
  // 每5分钟自动刷新
  setInterval(loadData, 5 * 60 * 1000);
});
