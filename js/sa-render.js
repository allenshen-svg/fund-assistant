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
        '<div class="hs-row"><span class="hs-icon">🎙️</span><span class="hs-label">KOL观点</span><div class="hs-text">' + formatKolText(s.kol) + '</div></div>' +
        '<div class="hs-row"><span class="hs-icon">🐑</span><span class="hs-label">散户情绪</span><div class="hs-text">' + formatKolText(s.retail) + '</div></div>' +
        '<div class="hs-row hs-conclusion"><span class="hs-icon">⚡</span><span class="hs-label">预期差</span><div class="hs-text">' + formatKolText(s.conclusion) + '</div></div>' +
        '</div>';
    }).join('');
  } else {
    hb.innerHTML = '<div class="placeholder">请查看下方完整报告。</div>';
  }

  // --- Heatbar ---
  renderHeatbar(videoData);

  // --- Action plan cards (with per-holding recommendations) ---
  var actions = parseActions(report);
  var actionHtml = '';

  // Per-holding-type action cards
  if (actions.holdingActions && actions.holdingActions.length > 0) {
    actionHtml += '<div class="holding-actions-grid">';
    for (var hi = 0; hi < actions.holdingActions.length; hi++) {
      var ha = actions.holdingActions[hi];
      var haClass = 'neutral';
      if (/加仓|进场|买入|看多|继续持有/.test(ha.advice)) haClass = 'bullish';
      else if (/减仓|止损|观望|谨慎|回避|卖出/.test(ha.advice)) haClass = 'bearish';
      actionHtml += '<div class="holding-action-card ' + haClass + '">' +
        '<div class="ha-label">' + ha.label + '</div>' +
        '<div class="ha-advice">' + ha.advice + '</div></div>';
    }
    actionHtml += '</div>';
  }

  // Summary action cards
  actionHtml +=
    '<div class="action-card bullish"><div class="ac-title">✅ 胜率较高的方向</div><div class="ac-body">' + (actions.bullish || '见完整报告') + '</div></div>' +
    '<div class="action-card bearish"><div class="ac-title">❌ 必须回避的绞肉机</div><div class="ac-body">' + (actions.bearish || '见完整报告') + '</div></div>' +
    '<div class="action-card tactical"><div class="ac-title">⏱️ 战术纪律</div><div class="ac-body">' + (actions.tactical || '见完整报告') + '</div></div>';

  document.getElementById('action-body').innerHTML = actionHtml;

  // --- Raw report (strip trailing JSON dashboard block) ---
  var cleanReport = report.replace(/###\s*📊\s*情绪仪表盘参数[\s\S]*$/, '').trim();
  document.getElementById('raw-report').innerHTML = renderMarkdown(cleanReport);
}

// ==================== US MARKET ====================
function renderUSMarket(usData) {
  var grid = document.getElementById('us-grid');
  var placeholder = document.getElementById('us-placeholder');
  var badge = document.getElementById('us-update-time');

  if (!usData || !usData.stocks || usData.stocks.length === 0) {
    if (placeholder) placeholder.textContent = '美股行情暂无数据';
    return;
  }
  if (placeholder) placeholder.style.display = 'none';

  // Show fetch time
  if (badge && usData.fetch_time) {
    badge.textContent = usData.fetch_time.split(' ')[1] || usData.fetch_time;
  }

  grid.innerHTML = usData.stocks.map(function(s) {
    var isUp = s.percent >= 0;
    var cls = isUp ? 'up' : 'down';
    var arrow = isUp ? '▲' : '▼';
    var pctStr = (isUp ? '+' : '') + s.percent.toFixed(2) + '%';
    return '<div class="us-card ' + cls + '">' +
      '<div class="us-name">' + s.name + '</div>' +
      '<div class="us-sym">' + s.symbol + '</div>' +
      '<div class="us-price">' + (s.price || '--') + '</div>' +
      '<div class="us-change">' + arrow + ' ' + pctStr + '</div>' +
      (s.amplitude ? '<div class="us-amp">振幅 ' + s.amplitude + '%</div>' : '') +
      '</div>';
  }).join('');
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
  // Use sqrt scale to compress extreme differences between bars
  var sqrtMax = Math.sqrt(maxH);
  var colors = ['#ef4444','#f97316','#f59e0b','#eab308','#84cc16','#22c55e','#06b6d4','#6366f1'];
  var chart = document.getElementById('heatbar-chart');
  chart.innerHTML = sorted.map(function(item, i) {
    var name = item[0], heat = item[1];
    // sqrt normalization: makes bars more proportional, avoids first=100% rest=tiny
    var pct = Math.round(Math.sqrt(heat) / sqrtMax * 100);
    // Floor at 15% so even small entries are visible
    if (pct < 15) pct = 15;
    return '<div class="heatbar-row"><div class="heatbar-label">' + name + '</div><div class="heatbar-track"><div class="heatbar-fill" style="width:' + pct + '%;background:' + (colors[i] || '#94a3b8') + '"><span>' + Math.round(heat) + '万</span></div></div><div class="heatbar-val">' + pct + '%</div></div>';
  }).join('');
}

// ==================== VIDEO TABLE ====================
function renderVideoTable(videoData) {
  // Client-side finance keyword filter — matches backend FINANCE_KW
  var financeKW = [
    'A股','股市','大盘','沪指','上证','深成','创业板','科创板','沪深300','恒生','港股','美股','纳斯达克',
    'AI','人工智能','算力','芯片','半导体','光模块','CPO','大模型','DeepSeek',
    '机器人','自动驾驶','新能源','光伏','锂电','碳酸锂','储能',
    '军工','国防','航天','白酒','消费','医药','创新药','CXO',
    '黄金','金价','原油','油价','有色金属','铜','铝','稀土',
    '红利','高股息','银行','保险','券商','地产','房价','楼市','房地产',
    '央行','降息','降准','LPR','利率','通胀','CPI','GDP','PMI',
    '美联储','加息','国债','债券','汇率','人民币',
    '关税','贸易战','制裁',
    '基金','ETF','牛市','熊市','涨停','跌停','抄底','追高',
    '仓位','加仓','减仓','定投','主力','资金流','北向资金',
    '茅台','比亚迪','宁德','英伟达','NVIDIA','特斯拉','格力','万达',
    'IPO','分红','回购','并购','重组','减持','增持',
    '板块','指数','概念股','题材','龙头股','主线','赛道',
    '净利润','营收','业绩','净值','估值','市盈率','市值',
    '电力','农业','春耕',
    '私募','公募','期货','期权','基民','股民','散户',
    '目标价','评级','买入','卖出','持有','看多','看空',
    '投资者','融资','融券','杠杆','做空','做多','止损',
    '证券','上市','港交所','外汇','利润','亏损','盈利','资产','负债',
    '经济','金融'
  ];
  function isFinance(text) {
    if (!text) return false;
    var t = text.toLowerCase();
    for (var i = 0; i < financeKW.length; i++) {
      if (t.includes(financeKW[i].toLowerCase())) return true;
    }
    return false;
  }

  // Filter: only show finance-related items
  var filtered = videoData.filter(function(v) {
    return isFinance((v.title || '') + (v.summary || ''));
  });

  document.getElementById('video-count').textContent = filtered.length + '条';
  var tbody = document.getElementById('video-tbody');
  tbody.innerHTML = filtered.slice(0, 50).map(function(v) {
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

function formatKolText(text) {
  if (!text) return '';
  // Split on '- ' at the start or after whitespace to find bullet items
  var parts = text.split(/(?:^|\s)- /).filter(function(p) { return p.trim(); });
  if (parts.length <= 1) {
    // No bullet structure, render as paragraph with highlighted citations
    return '<p class="hs-para">' + highlightCitations(text) + '</p>';
  }
  return '<ul class="hs-list">' + parts.map(function(p) {
    return '<li>' + highlightCitations(p.trim()) + '</li>';
  }).join('') + '</ul>';
}

function highlightCitations(text) {
  // Highlight 《title》 citations
  text = text.replace(/《([^》]+)》/g, '<span class="hs-cite">《$1》</span>');
  // Highlight (数字万/k点赞) patterns
  text = text.replace(/[\(\uff08]([\d.]+万?[\u70b9\u8d5e\u64ad\u653e]*)[\)\uff09]/g, '<span class="hs-stat">($1)</span>');
  return text;
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
    // 代码块（机制链图等）— 箭头高亮
    .replace(/```json\s*([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
    .replace(/```[\w]*\n?([\s\S]*?)```/g, function(_, code) {
      return '<pre class="chain-diagram"><code>' + code
        .replace(/→/g, '<span class="chain-arrow">→</span>')
        .replace(/↘/g, '<span class="chain-arrow">↘</span>')
        .replace(/↗/g, '<span class="chain-arrow">↗</span>')
        .replace(/\[([^\]]+)\]/g, '<span class="chain-node">[$1]</span>')
        + '</code></pre>';
    })
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^- (.*)/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>')
    .replace(/^\d+\.\s+(.*)/gm, '<li class="ol-item">$1</li>')
    .replace(/\n\n/g, '<br><br>')
    .replace(/\n/g, '<br>');
}
