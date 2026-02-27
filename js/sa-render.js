// =============================================
// KOL vs 散户 情绪博弈 - Rendering (Clean)
// =============================================

// ==================== TOGGLE SECTION ====================
function toggleSection(bodyId) {
  const body = document.getElementById(bodyId);
  const toggle = document.getElementById('toggle-' + bodyId);
  if (!body) return;
  body.classList.toggle('collapsed');
  if (toggle) toggle.textContent = body.classList.contains('collapsed') ? '▸' : '▾';
}

// ==================== DASHBOARD RENDERING ====================
function renderDashboard(videoData, report, factors) {
  const hd = factors?.hourly_dashboard || {};

  // --- Action signal ---
  const signalMap = {
    'Aggressive Buy':  { icon: '🟢', text: '积极买入', color: '#16a34a' },
    'Cautious Hold':   { icon: '🟡', text: '谨慎持有', color: '#d97706' },
    'Defensive':       { icon: '🟠', text: '防御姿态', color: '#ea580c' },
    'Strong Sell':     { icon: '🔴', text: '强烈卖出', color: '#dc2626' },
    'Wait':            { icon: '⏳', text: '等待观望', color: '#6366f1' }
  };
  const sig = signalMap[hd.action_signal] || signalMap['Wait'];
  document.getElementById('signal-icon').textContent = sig.icon;
  document.getElementById('signal-text').innerHTML =
    '<span style="color:' + sig.color + '">' + sig.text + '</span>';

  // --- Market temperature ---
  const t = hd.market_temperature || 50;
  const tempEl = document.getElementById('meta-temp');
  let tempCls = 'neutral', tempTxt = '中性';
  if (t >= 80) { tempCls = 'overheated'; tempTxt = '过热 🔥'; }
  else if (t >= 65) { tempCls = 'hot'; tempTxt = '偏热 🌡️'; }
  else if (t >= 45) { tempCls = 'warm'; tempTxt = '温和 ☀️'; }
  else if (t >= 25) { tempCls = 'neutral'; tempTxt = '中性 ⚖️'; }
  else { tempCls = 'cold'; tempTxt = '冰冷 ❄️'; }
  tempEl.className = 'meta-temp ' + tempCls;
  tempEl.textContent = tempTxt + ' ' + t;

  // --- Radar summary ---
  const radarText = parseRadarSummary(report) || '请查看完整报告';
  document.getElementById('meta-radar').textContent = radarText;

  // --- Hot assets ---
  const assetsEl = document.getElementById('summary-assets');
  assetsEl.innerHTML = (hd.hot_assets || [])
    .map(function(a) { return '<span class="asset-pill">🔥 ' + a + '</span>'; })
    .join('') || '';

  // --- KOL vs Retail divergence cards ---
  const kolSections = parseKOLSections(report);
  var hb = document.getElementById('hotspot-body');
  if (kolSections.length > 0) {
    hb.innerHTML = kolSections.map(function(s) {
      var divCls = classifyDivergence(s.conclusion);
      return '<div class="hotspot-card ' + divCls + '">' +
        '<div class="hs-title">🎯 ' + s.target + '</div>' +
        '<div class="hs-row"><span class="hs-icon">🎙️</span><span class="hs-label">KOL观点</span><span class="hs-text">' + s.kol + '</span></div>' +
        '<div class="hs-row"><span class="hs-icon">🐑</span><span class="hs-label">散户情绪</span><span class="hs-text">' + s.retail + '</span></div>' +
        '<div class="hs-row"><span class="hs-icon">⚡</span><span class="hs-label">预期差</span><span class="hs-text" style="font-weight:700;color:#1e1b4b">' + s.conclusion + '</span></div>' +
        '</div>';
    }).join('');
  } else {
    hb.innerHTML = '<div class="placeholder">请查看下方完整报告。</div>';
  }

  // --- Heatbar ---
  renderHeatbar(videoData);

  // --- Action plan cards ---
  var actions = parseActions(report);
  document.getElementById('action-body').innerHTML =
    '<div class="action-card bullish"><div class="ac-title">✅ 胜率较高的方向</div><div class="ac-body">' + (actions.bullish || '见完整报告') + '</div></div>' +
    '<div class="action-card bearish"><div class="ac-title">❌ 必须回避的绞肉机</div><div class="ac-body">' + (actions.bearish || '见完整报告') + '</div></div>' +
    '<div class="action-card tactical"><div class="ac-title">⏱️ 战术纪律</div><div class="ac-body">' + (actions.tactical || '见完整报告') + '</div></div>';

  // --- Raw report (strip trailing JSON dashboard block) ---
  var cleanReport = report.replace(/###\s*📊\s*情绪仪表盘参数[\s\S]*$/, '').trim();
  document.getElementById('raw-report').innerHTML = renderMarkdown(cleanReport);
}

// ==================== HEATBAR ====================
function renderHeatbar(videoData) {
  var topicHeat = {};
  // 全量关键词列表，与后端 collector FINANCE_KW 保持同步
  var keywords = [
    'AI算力','人工智能','半导体','军工','黄金','碳酸锂','新能源','港股','机器人',
    '消费','医药','原油','白酒','芯片','锂电','红利','ETF','基金','券商','银行',
    '地产','光伏','储能','稀土','CXO','关税','自动驾驶',
    '有色金属','铜','铝','创新药','保险','国债','债券',
    '大模型','DeepSeek','比亚迪','宁德','英伟达','特斯拉','茅台',
    '降息','降准','美联储','通胀','汇率','人民币',
    '贸易战','制裁','中东','俄乌',
    '科创板','创业板','北向','主力','龙头'
  ];
  for (var i = 0; i < videoData.length; i++) {
    var v = videoData[i];
    var text = (v.title || '') + (v.summary || '');
    for (var j = 0; j < keywords.length; j++) {
      var kw = keywords[j];
      if (text.includes(kw)) {
        topicHeat[kw] = (topicHeat[kw] || 0) + Math.max(1, (v.likes || 0) / 10000);
      }
    }
  }
  var sorted = Object.entries(topicHeat).sort(function(a, b) { return b[1] - a[1]; }).slice(0, 8);
  var maxH = sorted[0] ? sorted[0][1] : 1;
  var colors = ['#ef4444','#f97316','#f59e0b','#eab308','#84cc16','#22c55e','#06b6d4','#6366f1'];
  var chart = document.getElementById('heatbar-chart');
  chart.innerHTML = sorted.map(function(item, i) {
    var name = item[0], heat = item[1];
    var pct = Math.round(heat / maxH * 100);
    return '<div class="heatbar-row"><div class="heatbar-label">' + name + '</div><div class="heatbar-track"><div class="heatbar-fill" style="width:' + pct + '%;background:' + (colors[i] || '#94a3b8') + '"><span>' + Math.round(heat) + '万</span></div></div><div class="heatbar-val">' + pct + '%</div></div>';
  }).join('');
}

// ==================== VIDEO TABLE ====================
function renderVideoTable(videoData) {
  document.getElementById('video-count').textContent = videoData.length + '条';
  var tbody = document.getElementById('video-tbody');
  tbody.innerHTML = videoData.slice(0, 40).map(function(v) {
    var s = v.sentiment || '';
    var sentCls = /看多|乐观|追多|贪婪|狂热|偏多/.test(s) ? 'pos' : /悲观|恐慌|谨慎|看空|偏空/.test(s) ? 'neg' : 'neu';
    var noiseFlag = v.noise_flag || /震惊|全仓梭哈|赶紧|速看|神秘主力/.test(v.title || '');
    return '<tr' + (noiseFlag ? ' style="opacity:.5"' : '') + '>' +
      '<td class="vt-title' + (noiseFlag ? ' vt-noise' : '') + '" title="' + (v.title || '').replace(/"/g, '&quot;') + '">' + (v.title || '--') + (noiseFlag ? '<span class="vt-noise-tag">噪音</span>' : '') + '</td>' +
      '<td class="vt-likes">' + formatNum(v.likes) + '</td>' +
      '<td><span class="vt-sentiment ' + sentCls + '">' + (s || '--') + '</span></td>' +
      '<td style="font-size:9px;color:var(--sub);white-space:nowrap">' + (v.platform || '--') + '</td></tr>';
  }).join('');
}

// ==================== HELPERS ====================
function formatNum(n) {
  if (!n) return '--';
  if (n >= 10000) return (n / 10000).toFixed(1) + '万';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

function classifyDivergence(t) {
  if (!t) return 'neutral';
  if (/逆向|抄底|做多|低估|反转|背离做多|散户恐慌.*KOL看多/.test(t)) return 'fomo';
  if (/见顶|泡沫|过热|高估|回撤|背离做空|散户狂热.*KOL谨慎/.test(t)) return 'panic';
  return 'neutral';
}

// ==================== MARKDOWN ====================
function renderMarkdown(md) {
  if (!md) return '';
  return md
    .replace(/### (.*)/g, '<h3>$1</h3>')
    .replace(/## (.*)/g, '<h2>$1</h2>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/```json\s*([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/^- (.*)/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>')
    .replace(/\n\n/g, '<br><br>')
    .replace(/\n/g, '<br>');
}
