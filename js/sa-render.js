// =============================================
// KOL vs 散户 情绪博弈分析 - Rendering
// =============================================

// ==================== DASHBOARD RENDERING ====================
function renderDashboard(videoData, report, factors) {
  const hd = factors?.hourly_dashboard || {};

  // 4 Gauges: FOMO, PANIC, Divergence, Market Temperature
  renderGauge('gauge-fomo', hd.fomo_level || 50, '#ef4444');
  renderGauge('gauge-panic', hd.panic_level || 50, '#3b82f6');
  renderGauge('gauge-divergence', hd.divergence_index || 50, '#8b5cf6');
  renderGauge('gauge-mkttemp', hd.market_temperature || 50, '#f59e0b');

  document.getElementById('fomo-val').textContent = hd.fomo_level ?? '--';
  document.getElementById('panic-val').textContent = hd.panic_level ?? '--';
  document.getElementById('divergence-val').textContent = hd.divergence_index ?? '--';
  document.getElementById('mkttemp-val').textContent = hd.market_temperature ?? '--';

  document.getElementById('fomo-desc').textContent = fomoLabel(hd.fomo_level);
  document.getElementById('panic-desc').textContent = panicLabel(hd.panic_level);
  document.getElementById('divergence-desc').textContent = divergenceLabel(hd.divergence_index);
  document.getElementById('mkttemp-desc').textContent = tempLabel(hd.market_temperature);

  // Market temperature badge (numeric)
  const t = hd.market_temperature || 50;
  const tempCfg = t>=80?{text:'🔥 过热',cls:'overheated'}:t>=65?{text:'🌡️ 偏热',cls:'hot'}:t>=45?{text:'☀️ 温和',cls:'warm'}:t>=25?{text:'⚖️ 中性',cls:'neutral'}:{text:'❄️ 冰冷',cls:'cold'};
  document.getElementById('market-temp-badge').innerHTML = `<span class="temp-badge ${tempCfg.cls}">${tempCfg.text} ${t}</span>`;

  // Hot assets
  document.getElementById('hot-assets').innerHTML = (hd.hot_assets || []).map(a => `<span class="crowd-pill hot">🔥 ${a}</span>`).join('') || '<span style="font-size:10px;color:var(--sub)">无</span>';

  // Action signal
  const signalMap = {'Aggressive Buy':{text:'🟢 积极买入',color:'#16a34a'},'Cautious Hold':{text:'🟡 谨慎持有',color:'#d97706'},'Defensive':{text:'🟠 防御姿态',color:'#ea580c'},'Strong Sell':{text:'🔴 强烈卖出',color:'#dc2626'},'Wait':{text:'⏳ 等待观望',color:'#6366f1'}};
  const sig = signalMap[hd.action_signal] || signalMap['Wait'];
  document.getElementById('action-signal').innerHTML = `<span style="color:${sig.color};font-size:14px;font-weight:800">${sig.text}</span>`;

  // KOL vs Retail divergence cards
  const kolSections = parseKOLSections(report);
  const hb = document.getElementById('hotspot-body');
  hb.innerHTML = kolSections.length > 0 ? kolSections.map(s => {
    const divCls = classifyDivergence(s.conclusion);
    return `<div class="hotspot-card ${divCls}">
      <div class="hs-title">🎯 ${s.target}</div>
      <div class="hs-row"><span class="hs-icon">🎙️</span><span class="hs-label">KOL观点</span><span class="hs-text">${s.kol}</span></div>
      <div class="hs-row"><span class="hs-icon">🐑</span><span class="hs-label">散户情绪</span><span class="hs-text">${s.retail}</span></div>
      <div class="hs-row"><span class="hs-icon">⚡</span><span class="hs-label">预期差</span><span class="hs-text" style="font-weight:700;color:#1e1b4b">${s.conclusion}</span></div>
    </div>`;
  }).join('') : '<div style="font-size:12px;color:var(--sub)">请查看下方完整报告。</div>';

  // Radar summary
  const radarText = parseRadarSummary(report) || '请查看完整报告。';
  document.getElementById('radar-summary').textContent = radarText;
  renderHeatbar(videoData);

  // Action plan cards
  const actions = parseActions(report);
  document.getElementById('action-body').innerHTML = `
    <div class="action-card bullish"><div class="ac-title">✅ 胜率较高的方向</div><div class="ac-body">${actions.bullish||'见完整报告'}</div></div>
    <div class="action-card bearish"><div class="ac-title">❌ 必须回避的绞肉机</div><div class="ac-body">${actions.bearish||'见完整报告'}</div></div>
    <div class="action-card tactical"><div class="ac-title">⏱️ 战术纪律</div><div class="ac-body">${actions.tactical||'见完整报告'}</div></div>`;

  document.getElementById('raw-report').innerHTML = renderMarkdown(report);

  // Render factor cards (replaces raw JSON)
  renderFactorCards(factors);
}

// ==================== FACTOR CARDS (replaces raw JSON) ====================
function renderFactorCards(factors) {
  const hd = factors?.hourly_dashboard || {};
  const container = document.getElementById('json-output');

  // Color helpers
  function valColor(v, invert) {
    if(invert) v = 100 - v;
    if(v >= 75) return '#dc2626';
    if(v >= 50) return '#f59e0b';
    if(v >= 25) return '#22c55e';
    return '#3b82f6';
  }

  // Signal config
  const signalCfg = {
    'Aggressive Buy':  {text:'🟢 积极买入', bg:'linear-gradient(135deg,#ecfdf5,#d1fae5)', border:'#86efac'},
    'Cautious Hold':   {text:'🟡 谨慎持有', bg:'linear-gradient(135deg,#fffbeb,#fef3c7)', border:'#fde68a'},
    'Defensive':       {text:'🟠 防御姿态', bg:'linear-gradient(135deg,#fff7ed,#ffedd5)', border:'#fed7aa'},
    'Strong Sell':     {text:'🔴 强烈卖出', bg:'linear-gradient(135deg,#fef2f2,#fecaca)', border:'#fca5a5'},
    'Wait':            {text:'⏳ 等待观望', bg:'linear-gradient(135deg,#eef2ff,#e0e7ff)', border:'#c7d2fe'},
  };
  const sc = signalCfg[hd.action_signal] || signalCfg['Wait'];

  // Build gauge cards
  const metrics = [
    {label:'🌡️ 市场温度', value:hd.market_temperature, color:valColor(hd.market_temperature), desc:tempLabel(hd.market_temperature)},
    {label:'😱 FOMO 指数', value:hd.fomo_level, color:valColor(hd.fomo_level), desc:fomoLabel(hd.fomo_level)},
    {label:'😰 恐慌指数', value:hd.panic_level, color:valColor(hd.panic_level), desc:panicLabel(hd.panic_level)},
    {label:'⚡ 分歧指数', value:hd.divergence_index, color:valColor(hd.divergence_index), desc:divergenceLabel(hd.divergence_index)},
  ];

  let html = '<div class="factor-grid">';
  for(const m of metrics) {
    const v = m.value ?? 0;
    html += `<div class="factor-card">
      <div class="fc-label">${m.label}</div>
      <div class="fc-value" style="color:${m.color}">${v}</div>
      <div style="font-size:9px;color:var(--sub);margin-top:2px">${m.desc}</div>
      <div class="fc-bar"><div class="fc-bar-fill" style="width:${v}%;background:${m.color}"></div></div>
    </div>`;
  }
  html += '</div>';

  // Hot assets
  if(hd.hot_assets && hd.hot_assets.length > 0) {
    html += '<div class="factor-assets">';
    for(const a of hd.hot_assets) {
      html += `<span class="crowd-pill hot">🔥 ${a}</span>`;
    }
    html += '</div>';
  }

  // Action signal
  html += `<div class="factor-signal" style="background:${sc.bg};border:1px solid ${sc.border}">${sc.text}</div>`;

  // Toggle for raw JSON
  html += `<div class="factor-raw-toggle" onclick="toggleRawJSON()">📄 查看原始 JSON</div>`;
  html += `<div class="factor-raw-block" id="factor-raw-block"><div class="json-block">${syntaxHighlight(JSON.stringify(factors, null, 2))}</div></div>`;

  container.innerHTML = html;
}

function toggleRawJSON() {
  document.getElementById('factor-raw-block').classList.toggle('show');
}

// ==================== GAUGE RENDERING ====================
function renderGauge(svgId, value, color) {
  const svg = document.getElementById(svgId);
  const v = Math.max(0, Math.min(100, value || 0));
  const cx=60, cy=58, r=44;
  const startA=-Math.PI, endA=0;
  const valA = startA + (endA-startA)*(v/100);
  const bg = describeArc(cx,cy,r,startA,endA);
  const fg = describeArc(cx,cy,r,startA,valA);
  const nx = cx+((r-8)*Math.cos(valA)), ny = cy+((r-8)*Math.sin(valA));
  svg.innerHTML = `<path d="${bg}" fill="none" stroke="#e2e8f0" stroke-width="10" stroke-linecap="round"/>
    <path d="${fg}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round"/>
    <circle cx="${nx}" cy="${ny}" r="4" fill="${color}" opacity="0.8"/>`;
}

function describeArc(cx,cy,r,sa,ea) {
  const s={x:cx+r*Math.cos(sa),y:cy+r*Math.sin(sa)}, e={x:cx+r*Math.cos(ea),y:cy+r*Math.sin(ea)};
  return `M ${s.x} ${s.y} A ${r} ${r} 0 ${ea-sa>Math.PI?1:0} 1 ${e.x} ${e.y}`;
}

// ==================== LABEL HELPERS ====================
function fomoLabel(v){if(v>=80)return'极度贪婪';if(v>=60)return'偏贪婪';if(v>=40)return'中性';if(v>=20)return'偏谨慎';return'极度保守'}
function panicLabel(v){if(v>=80)return'极度恐慌';if(v>=60)return'偏恐慌';if(v>=40)return'中性';if(v>=20)return'偏乐观';return'极度乐观'}
function divergenceLabel(v){if(v>=80)return'严重背离·逆向信号';if(v>=60)return'显著背离';if(v>=40)return'轻微背离';if(v>=20)return'基本一致';return'完全共识'}
function tempLabel(v){if(v>=80)return'严重过热';if(v>=65)return'偏热';if(v>=45)return'温和';if(v>=25)return'偏冷';return'冰冷'}
function classifyDivergence(t){if(!t)return'neutral';if(/逆向|抄底|做多|低估|反转|背离做多|散户恐慌.*KOL看多/.test(t))return'fomo';if(/见顶|泡沫|过热|高估|回撤|背离做空|散户狂热.*KOL谨慎/.test(t))return'panic';return'neutral'}

// ==================== HEATBAR ====================
function renderHeatbar(videoData) {
  const topicHeat = {};
  const keywords = ['AI算力','人工智能','半导体','军工','黄金','碳酸锂','新能源','港股','机器人','消费','医药','原油','白酒','芯片','锂电','红利','ETF','基金','券商','银行','地产','光伏','储能','稀土','CXO','关税','自动驾驶'];
  for(const v of videoData) {
    const text = (v.title||'')+(v.summary||'');
    for(const kw of keywords) {
      if(text.includes(kw)) topicHeat[kw] = (topicHeat[kw]||0) + Math.max(1, (v.likes||0)/10000);
    }
  }
  const sorted = Object.entries(topicHeat).sort((a,b)=>b[1]-a[1]).slice(0,10);
  const maxH = sorted[0]?.[1]||1;
  const colors = ['#ef4444','#f97316','#f59e0b','#eab308','#84cc16','#22c55e','#06b6d4','#6366f1','#8b5cf6','#ec4899'];
  document.getElementById('heatbar-chart').innerHTML = sorted.map(([name,heat],i) => {
    const pct = Math.round(heat/maxH*100);
    return `<div class="heatbar-row"><div class="heatbar-label">${name}</div><div class="heatbar-track"><div class="heatbar-fill" style="width:${pct}%;background:${colors[i]||'#94a3b8'}"><span>${Math.round(heat)}万</span></div></div><div class="heatbar-val">${pct}%</div></div>`;
  }).join('');
}

// ==================== VIDEO TABLE ====================
function renderVideoTable(videoData) {
  document.getElementById('video-count').textContent = videoData.length + '条';
  const tbody = document.getElementById('video-tbody');
  tbody.innerHTML = videoData.slice(0,40).map(v => {
    const s = v.sentiment || '';
    const sentCls = /看多|乐观|追多|贪婪|狂热|偏多/.test(s)?'pos':/悲观|恐慌|谨慎|看空|偏空/.test(s)?'neg':'neu';
    const noiseFlag = v.noise_flag || /震惊|全仓梭哈|赶紧|速看|神秘主力/.test(v.title||'');
    return `<tr${noiseFlag?' style="opacity:.5"':''}>
      <td class="vt-title${noiseFlag?' vt-noise':''}" title="${(v.title||'').replace(/"/g,'&quot;')}">${v.title||'--'}${noiseFlag?'<span class="vt-noise-tag">噪音</span>':''}</td>
      <td class="vt-likes">${formatNum(v.likes)}</td>
      <td><span class="vt-sentiment ${sentCls}">${s||'--'}</span></td>
      <td style="font-size:9px;color:var(--sub);white-space:nowrap">${v.platform||'--'}</td>
    </tr>`;
  }).join('');
}

function formatNum(n){if(!n)return'--';if(n>=10000)return(n/10000).toFixed(1)+'万';if(n>=1000)return(n/1000).toFixed(1)+'k';return String(n)}

// ==================== MARKDOWN & JSON ====================
function renderMarkdown(md) {
  if(!md) return '';
  return md.replace(/### (.*)/g,'<h3>$1</h3>').replace(/## (.*)/g,'<h2>$1</h2>')
    .replace(/\*\*(.*?)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/```json\s*([\s\S]*?)```/g,'<pre><code>$1</code></pre>').replace(/```([\s\S]*?)```/g,'<pre><code>$1</code></pre>')
    .replace(/^- (.*)/gm,'<li>$1</li>').replace(/(<li>.*<\/li>)/gs,'<ul>$1</ul>')
    .replace(/\n\n/g,'<br><br>').replace(/\n/g,'<br>');
}

function syntaxHighlight(j) {
  return j.replace(/("(\\u[\da-fA-F]{4}|\\[^u]|[^\\"])*"(\s*:)?)/g,m=>/:$/.test(m)?`<span class="json-key">${m}</span>`:`<span class="json-str">${m}</span>`)
    .replace(/\b(\d+)\b/g,'<span class="json-num">$1</span>');
}
