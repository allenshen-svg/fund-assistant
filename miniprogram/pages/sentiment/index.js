const { getSettings } = require('../../utils/storage');
const { fetchAnalysisData, fetchUSMarketData, triggerRefresh, triggerReanalyze, getServerBase, fetchMultiFundHistory, fetchSentimentData } = require('../../utils/api');

const COMMODITY_PROXY_FUNDS = [
  { key: 'gold',       name: '黄金',   code: '518880', color: '#f59e0b' },
  { key: 'silver',     name: '白银',   code: '161226', color: '#94a3b8' },
  { key: 'oil',        name: '石油',   code: '159697', color: '#22c55e' },
  { key: 'nonferrous', name: '有色金属', code: '512400', color: '#3b82f6' },
  { key: 'coal',       name: '煤炭',   code: '515220', color: '#78716c' },
  { key: 'soy',        name: '大豆',   code: '159985', color: '#84cc16' },
  { key: 'chemical',   name: '化工',   code: '159870', color: '#a855f7' },
];

/* ====== 事件语义去重 ====== */
function _normTitle(text) {
  return String(text || '').toLowerCase()
    .replace(/[\s\u3000]+/g, '')
    .replace(/["""'`·•:：，,。！？!？（）()\[\]【】、;；\-—]/g, '');
}

function _toBigrams(s) {
  if (!s || s.length < 2) return s ? new Set([s]) : new Set();
  var grams = new Set();
  for (var i = 0; i < s.length - 1; i++) grams.add(s.slice(i, i + 2));
  return grams;
}

function _bigramOverlap(a, b) {
  var ga = _toBigrams(_normTitle(a));
  var gb = _toBigrams(_normTitle(b));
  if (!ga.size || !gb.size) return 0;
  var inter = 0;
  ga.forEach(function(v) { if (gb.has(v)) inter++; });
  return inter / Math.max(1, Math.min(ga.size, gb.size));
}

function _tokenJaccard(a, b) {
  var raw = function(t) {
    return String(t || '').toLowerCase()
      .replace(/[\s\u3000]+/g, '')
      .replace(/["""'`·•:：，,。！？!？（）()\[\]【】、;；\-—]/g, '');
  };
  var tokA = (raw(a).match(/[\u4e00-\u9fff]{2,}|[a-z0-9]+/g) || []);
  var tokB = (raw(b).match(/[\u4e00-\u9fff]{2,}|[a-z0-9]+/g) || []);
  if (!tokA.length || !tokB.length) return 0;
  var setA = {}; tokA.forEach(function(t) { setA[t] = 1; });
  var setB = {}; tokB.forEach(function(t) { setB[t] = 1; });
  var inter = 0;
  Object.keys(setA).forEach(function(k) { if (setB[k]) inter++; });
  var union = Object.keys(setA).length + Object.keys(setB).length - inter;
  return union > 0 ? inter / union : 0;
}

function _dedupeEventList(items) {
  var kept = [];
  items.forEach(function(evt) {
    var title = evt.title || '';
    var cat = evt.category || '';
    var isDup = false;
    for (var i = 0; i < kept.length; i++) {
      var kt = kept[i].title || '';
      var kcat = kept[i].category || '';
      var norm = _normTitle(title);
      var knorm = _normTitle(kt);

      // 完全相同
      if (norm === knorm) { isDup = true; break; }

      // 子串包含
      if ((norm.includes(knorm) || knorm.includes(norm)) && Math.min(norm.length, knorm.length) >= 6) {
        isDup = true; break;
      }

      var bs = _bigramOverlap(title, kt);
      var tj = _tokenJaccard(title, kt);
      var sameCat = cat && cat === kcat;

      if (sameCat && bs >= 0.55) { isDup = true; break; }
      if (sameCat && tj >= 0.45) { isDup = true; break; }
      if (bs >= 0.50 && tj >= 0.40) { isDup = true; break; }
    }
    if (!isDup) kept.push(evt);
  });
  return kept;
}

Page({
  data: {
    loading: true,
    sourceLabel: '加载中',
    updatedAt: '--',

    // —— 总览卡 ——
    signalText: '等待分析',
    signalIcon: '⏳',
    signalClass: 'wait',
    marketTemp: '--',
    tempLabel: '--',
    tempClass: 'neutral',
    radarSummary: '--',
    hotAssets: [],
    fomoLevel: 0,
    panicLevel: 0,
    divergenceIndex: 0,

    // —— 隔夜美股 ——
    usStocks: [],

    // —— 大宗商品近1月走势 ——
    commodityTrends: [],
    selectedCommodityKey: 'gold',
    secCommodityTrend: true,
    commodityCanvasW: 320,
    commodityCanvasH: 190,

    // —— AI 自动分析状态 ——
    aiAnalyzing: false,
    aiAnalyzingMsg: '',

    // —— 国际热点深度分析 ——
    deepAnalysis: [],

    // —— 操作指南 ——
    holdingActions: [],
    bullish: '',
    bearish: '',
    tactical: '',

    // —— 折叠控制 ——
    secUsMarket: false,
    secAction: true,

    // —— 手动刷新 ——
    refreshing: false,
    refreshMsg: '',
  },

  onLoad() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const canvasW = Math.max(260, (info && info.windowWidth ? info.windowWidth : 375) - 72);
    this.setData({ commodityCanvasW: canvasW, commodityCanvasH: 190 });
  },

  onShow() {
    this.loadAll().then(() => this._autoAnalyzeIfStale());
  },

  onPullDownRefresh() {
    this.loadAll().finally(() => wx.stopPullDownRefresh());
  },

  toggleSection(e) {
    const key = e.currentTarget.dataset.key;
    if (!key) return;
    const nextVal = !this.data[key];
    this.setData({ [key]: nextVal }, () => {
      if (key === 'secCommodityTrend' && nextVal) {
        this._drawCommodityTrendCanvas();
      }
    });
  },

  /* ====== 自动触发 AI 分析（分析数据过期时） ====== */
  async _autoAnalyzeIfStale() {
    if (this.data.aiAnalyzing || this.data.refreshing) return;
    const settings = getSettings();
    const serverBase = getServerBase(settings);
    if (!serverBase) return;

    const serverSettings = { ...settings, apiBase: serverBase };
    const analysisData = await fetchAnalysisData(serverSettings);
    const now = Math.floor(Date.now() / 1000);
    const ts = (analysisData && analysisData.analysis_ts) || 0;
    const age = now - ts;

    if (age > 1800) {
      this.setData({ aiAnalyzing: true, aiAnalyzingMsg: '🧠 AI 正在分析最新事件...' });
      try {
        const res = await triggerReanalyze(settings);
        if (res.status === 'error') {
          this.setData({ aiAnalyzing: false, aiAnalyzingMsg: '' });
          return;
        }
        for (let i = 0; i < 20; i++) {
          await _sleep(3000);
          const fresh = await fetchAnalysisData(serverSettings);
          if (fresh && fresh.analysis_ts && fresh.analysis_ts > ts) {
            await this.loadAll();
            break;
          }
          this.setData({ aiAnalyzingMsg: `🧠 AI 深度分析中... (${(i + 1) * 3}s)` });
        }
      } catch (e) {
        console.error('Auto-analyze failed:', e);
      } finally {
        this.setData({ aiAnalyzing: false, aiAnalyzingMsg: '' });
      }
    }
  },

  /* ====== 手动触发舆情采集 + AI 分析 ====== */
  async onManualRefresh() {
    if (this.data.refreshing) return;
    this.setData({ refreshing: true, refreshMsg: '正在触发采集...' });
    const settings = getSettings();
    // 轮询时必须从 Flask 服务器读取数据，而不是 GitHub Pages
    const serverBase = getServerBase(settings);

    // 检查是否已配置服务器地址
    if (!serverBase) {
      this.setData({
        refreshMsg: '❌ 未配置服务器地址，请在「设置」中填写舆情分析服务器地址'
      });
      setTimeout(() => this.setData({ refreshing: false, refreshMsg: '' }), 4000);
      return;
    }

    const serverSettings = { ...settings, apiBase: serverBase };

    try {
      // 1. 触发后端采集
      const refreshRes = await triggerRefresh(settings);
      if (refreshRes.status === 'busy') {
        this.setData({ refreshMsg: '采集进行中，请稍候...' });
      } else if (refreshRes.status === 'started') {
        this.setData({ refreshMsg: '采集已启动，等待完成...' });
      } else {
        // 触发失败，提前结束
        this.setData({ refreshMsg: '❌ ' + (refreshRes.message || '无法连接后端服务器，请确认 Flask 服务已启动') });
        setTimeout(() => this.setData({ refreshing: false, refreshMsg: '' }), 4000);
        return;
      }

      // 2. 轮询等待采集完成（最多120秒）—— 从 Flask 服务器读取
      let ready = false;
      for (let i = 0; i < 40; i++) {
        await _sleep(3000);
        const data = await fetchSentimentData(serverSettings);
        if (data && data.fetch_ts) {
          const age = Math.floor(Date.now() / 1000) - data.fetch_ts;
          if (age < 120) { ready = true; break; }
        }
        this.setData({ refreshMsg: `采集中... (${(i + 1) * 3}s)` });
      }

      if (!ready) {
        this.setData({ refreshMsg: '采集超时，请稍后重试' });
        setTimeout(() => this.setData({ refreshing: false, refreshMsg: '' }), 3000);
        return;
      }

      // 3. 触发 AI 分析
      this.setData({ refreshMsg: '采集完成，正在 AI 分析...' });
      const analyzeRes = await triggerReanalyze(settings);
      if (analyzeRes.status === 'error') {
        // AI 分析触发失败，仅刷新采集数据
        this.setData({ refreshMsg: '采集完成，AI 分析触发失败' });
        await this.loadAll();
        setTimeout(() => this.setData({ refreshing: false, refreshMsg: '' }), 3000);
        return;
      }

      // 4. 等待分析完成（最多60秒）—— 从 Flask 服务器读取
      for (let i = 0; i < 20; i++) {
        await _sleep(3000);
        const analysis = await fetchAnalysisData(serverSettings);
        if (analysis && analysis.analysis_ts) {
          const age = Math.floor(Date.now() / 1000) - analysis.analysis_ts;
          if (age < 120) break;
        }
        this.setData({ refreshMsg: `AI 分析中... (${(i + 1) * 3}s)` });
      }

      // 5. 刷新页面数据
      this.setData({ refreshMsg: '✅ 更新完成！' });
      await this.loadAll();
    } catch (e) {
      console.error('Manual refresh failed:', e);
      this.setData({ refreshMsg: '❌ 刷新失败: ' + (e.message || e) });
    } finally {
      setTimeout(() => this.setData({ refreshing: false, refreshMsg: '' }), 2500);
    }
  },

  async loadAll() {
    this.setData({ loading: true });
    const settings = getSettings();
    const serverBase = getServerBase(settings);
    const dataSettings = serverBase ? { ...settings, apiBase: serverBase } : settings;

    // 并行获取：AI分析 + 美股 + 热点事件 + 大宗商品 + 实时突发
    const [analysisRes, usRes, commodityHistRes] = await Promise.allSettled([
      fetchAnalysisData(dataSettings),
      fetchUSMarketData(dataSettings),
      fetchMultiFundHistory(COMMODITY_PROXY_FUNDS.map(i => i.code)),
    ]);

    const analysisData = analysisRes.status === 'fulfilled' ? analysisRes.value : null;
    const usData = usRes.status === 'fulfilled' ? usRes.value : null;
    const commodityHist = commodityHistRes.status === 'fulfilled' ? commodityHistRes.value : null;

    const batch = { loading: false };

    // ========== 1. 总览卡 (来自 analysis) ==========
    if (analysisData) {
      const db = (analysisData.dashboard && analysisData.dashboard.hourly_dashboard) || {};
      const temp = db.market_temperature || 50;
      const signal = db.action_signal || 'Wait';

      const sigMap = {
        'Aggressive Buy': { icon: '🟢', text: '积极买入', cls: 'buy' },
        'Cautious Hold':  { icon: '🟡', text: '谨慎持有', cls: 'hold' },
        'Defensive':      { icon: '🟠', text: '防御姿态', cls: 'defensive' },
        'Strong Sell':    { icon: '🔴', text: '强烈卖出', cls: 'sell' },
        'Wait':           { icon: '⏳', text: '等待观望', cls: 'wait' },
      };
      const sig = sigMap[signal] || sigMap['Wait'];
      batch.signalIcon = sig.icon;
      batch.signalText = sig.text;
      batch.signalClass = sig.cls;

      let tempLabel, tempClass;
      if (temp >= 80) { tempLabel = '过热 🔥'; tempClass = 'overheat'; }
      else if (temp >= 65) { tempLabel = '偏热 🌡️'; tempClass = 'hot'; }
      else if (temp >= 45) { tempLabel = '温和 ☀️'; tempClass = 'warm'; }
      else if (temp >= 25) { tempLabel = '中性 ⚖️'; tempClass = 'neutral'; }
      else { tempLabel = '冰冷 ❄️'; tempClass = 'cold'; }
      batch.marketTemp = temp;
      batch.tempLabel = tempLabel;
      batch.tempClass = tempClass;

      batch.fomoLevel = db.fomo_level || 0;
      batch.panicLevel = db.panic_level || 0;
      batch.divergenceIndex = db.divergence_index || 0;
      batch.radarSummary = analysisData.radar_summary || '--';
      batch.hotAssets = (db.hot_assets || []).map(a => ({ name: a }));

      // —— 深度分析（按板块，含完整 markdown→HTML） ——
      const deepRaw = analysisData.deep_analysis || [];
      batch.deepAnalysis = deepRaw.map((d, i) => {
        const fullText = [d.title, d.content, d.strategy].join(' ');
        const linked = _matchCommodities(fullText, commodityHist);
        return {
          id: 'deep_' + i,
          title: d.title || '未知板块',
          contentHtml: _mdToHtml(d.content || ''),
          strategy: d.strategy || '',
          linkedCommodities: linked,
          expanded: i < 2,  // 前两个板块默认展开
        };
      });

      // —— 操作指南 ——
      const acts = analysisData.actions || {};
      batch.holdingActions = (acts.holding_actions || []).map(a => ({
        label: a.label,
        advice: a.advice,
        actClass: classifyAction(a.advice),
      }));
      batch.bullish = acts.bullish || '--';
      batch.bearish = acts.bearish || '--';
      batch.tactical = acts.tactical || '--';

      batch.updatedAt = (analysisData.analysis_time || '--').slice(0, 16);
      batch.sourceLabel = '远程分析';
    }

    // ========== 2. 隔夜美股 ==========
    if (usData && usData.stocks) {
      batch.usStocks = usData.stocks.map(s => ({
        name: s.name,
        symbol: s.symbol,
        price: s.price,
        pct: s.percent,
        pctStr: (s.percent >= 0 ? '+' : '') + s.percent.toFixed(2) + '%',
        pctClass: s.percent >= 0 ? 'pct-up' : 'pct-down',
        amplitude: s.amplitude ? s.amplitude.toFixed(2) + '%' : '--',
      }));
    }

    // ========== 3. 大宗商品近1月走势 ==========
    if (commodityHist) {
      batch.commodityTrends = COMMODITY_PROXY_FUNDS.map(item => {
        const hist = (commodityHist[item.code] || []).slice(-30);
        if (!hist || hist.length < 2) return null;
        const first = Number(hist[0].nav || 0);
        const last = Number(hist[hist.length - 1].nav || 0);
        if (!first || !last) return null;
        const pct = ((last - first) / first) * 100;
        return {
          key: item.key,
          name: item.name,
          code: item.code,
          color: item.color,
          points: hist.map(h => Number(h.nav || 0)).filter(v => v > 0),
          trendPct: pct,
          trendPctStr: `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`,
          trendClass: pct >= 0 ? 'pct-up' : 'pct-down',
        };
      }).filter(Boolean);
      const validKeys = batch.commodityTrends.map(t => t.key);
      if (validKeys.length > 0 && !validKeys.includes(this.data.selectedCommodityKey)) {
        batch.selectedCommodityKey = validKeys[0];
      }
    }

    // ========== 4. 事件数据已整合到板块深度分析中，不再单独展示 ==========

    this.setData(batch, () => {
      if ((this.data.commodityTrends || []).length > 0 && this.data.secCommodityTrend) {
        this._drawCommodityTrendCanvas();
      }
    });
  },

  onSelectCommodity(e) {
    const key = e.currentTarget.dataset.key;
    this.setData({ selectedCommodityKey: key }, () => {
      if (this.data.secCommodityTrend) this._drawCommodityTrendCanvas();
    });
  },

  toggleDeepCard(e) {
    const idx = e.currentTarget.dataset.idx;
    const key = 'deepAnalysis[' + idx + '].expanded';
    this.setData({ [key]: !this.data.deepAnalysis[idx].expanded });
  },

  _drawCommodityTrendCanvas() {
    const trends = this.data.commodityTrends || [];
    if (trends.length === 0) return;
    const selKey = this.data.selectedCommodityKey || (trends[0] && trends[0].key);
    const item = trends.find(t => t.key === selKey) || trends[0];
    if (!item) return;
    const pts = item.points || [];
    if (pts.length < 2) return;

    const width = this.data.commodityCanvasW || 320;
    const height = this.data.commodityCanvasH || 190;
    const ctx = wx.createCanvasContext('commodityTrendCanvas', this);

    // left padding accommodates Y-axis labels (~7chars × ~5.5px + 6px gap = 46)
    const padding = { left: 48, right: 14, top: 18, bottom: 22 };
    const plotW = width - padding.left - padding.right;
    const plotH = height - padding.top - padding.bottom;

    let minVal = Math.min.apply(null, pts);
    let maxVal = Math.max.apply(null, pts);
    if (minVal === maxVal) { maxVal += 1; minVal -= 1; }

    // 辅助：值 → Y坐标
    const toY = function(v) {
      return padding.top + (maxVal - v) * plotH / (maxVal - minVal);
    };
    // 辅助：索引 → X坐标
    const toX = function(i) {
      return padding.left + plotW * i / (pts.length - 1);
    };
    // 辅助：格式化价格（保留3位小数，去掉多余0）
    const fmtV = function(v) {
      const s = v.toFixed(3);
      // 去掉末尾多余的0，但保留至少2位小数
      return s.replace(/(\.\d\d)0+$/, '$1');
    };

    // ===== 清空 =====
    ctx.clearRect(0, 0, width, height);

    // ===== Y轴标签 + 水平网格 =====
    const yTicks = [maxVal, (maxVal + minVal) / 2, minVal];
    ctx.setFontSize(9);
    ctx.setTextAlign('right');
    yTicks.forEach(function(v, idx) {
      const y = toY(v);
      // 网格线
      ctx.setStrokeStyle('rgba(148,163,184,0.22)');
      ctx.setLineWidth(0.8);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      // 刻度文字
      ctx.setFillStyle('rgba(148,163,184,0.85)');
      ctx.fillText(fmtV(v), padding.left - 4, y + 3.5);
    });

    // ===== 起始基准虚线（起点价位） =====
    const baseV = pts[0];
    const baseY = toY(baseV);
    ctx.setStrokeStyle('rgba(148,163,184,0.55)');
    ctx.setLineWidth(1);
    ctx.beginPath();
    for (let x = padding.left; x < width - padding.right; x += 8) {
      ctx.moveTo(x, baseY);
      ctx.lineTo(Math.min(x + 4, width - padding.right), baseY);
    }
    ctx.stroke();

    // ===== 折线 =====
    ctx.setStrokeStyle(item.color || '#3b82f6');
    ctx.setLineWidth(2.5);
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      const x = toX(i);
      const y = toY(pts[i]);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // ===== 标记最高 / 最低点 =====
    let maxIdx = 0, minIdx = 0;
    for (let i = 1; i < pts.length; i++) {
      if (pts[i] > pts[maxIdx]) maxIdx = i;
      if (pts[i] < pts[minIdx]) minIdx = i;
    }
    const markPoint = function(idx, isMax) {
      const mx = toX(idx);
      const my = toY(pts[idx]);
      const label = fmtV(pts[idx]);
      const pct = ((pts[idx] - baseV) / baseV * 100);
      const pctStr = (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%';
      const fullLabel = label + ' (' + pctStr + ')';

      // 小空心圆
      ctx.setStrokeStyle(item.color || '#3b82f6');
      ctx.setFillStyle('#ffffff');
      ctx.setLineWidth(1.5);
      ctx.beginPath();
      ctx.arc(mx, my, 3, 0, 2 * Math.PI);
      ctx.fill();
      ctx.stroke();

      // 文字气泡
      const textW = fullLabel.length * 5.2;
      let tx = mx - textW / 2;
      if (tx < padding.left) tx = padding.left;
      if (tx + textW > width - padding.right) tx = width - padding.right - textW;
      const ty = isMax ? my - 7 : my + 14;

      ctx.setFontSize(9);
      ctx.setTextAlign('left');
      ctx.setFillStyle(isMax ? 'rgba(239,68,68,0.90)' : 'rgba(34,197,94,0.90)');
      ctx.fillText(fullLabel, tx, ty);
    };

    // 避免最高最低点重叠时跳过最低（若索引相差<10%则只标最高）
    markPoint(maxIdx, true);
    if (Math.abs(maxIdx - minIdx) > pts.length * 0.08) {
      markPoint(minIdx, false);
    }

    // ===== 终点圆点 + 当前值标签 =====
    const endX = toX(pts.length - 1);
    const endY = toY(pts[pts.length - 1]);
    ctx.setFillStyle(item.color || '#3b82f6');
    ctx.beginPath();
    ctx.arc(endX, endY, 3.5, 0, 2 * Math.PI);
    ctx.fill();

    // 终点右侧价格标签（靠左若超出右边界）
    const endLabel = fmtV(pts[pts.length - 1]);
    ctx.setFontSize(9);
    ctx.setTextAlign('left');
    ctx.setFillStyle(item.color || '#3b82f6');
    const elx = endX + 5;
    const rightEdge = elx + endLabel.length * 5.5;
    if (rightEdge <= width) {
      ctx.fillText(endLabel, elx, endY + 3.5);
    } else {
      ctx.setTextAlign('right');
      ctx.fillText(endLabel, endX - 5, endY + 3.5);
    }

    // ===== X轴：起止日期提示 =====
    ctx.setFontSize(9);
    ctx.setFillStyle('rgba(148,163,184,0.70)');
    ctx.setTextAlign('left');
    ctx.fillText('30天前', padding.left, height - 4);
    ctx.setTextAlign('right');
    ctx.fillText('今日', width - padding.right, height - 4);

    ctx.draw();
  },
});

/* ====== 辅助函数 ====== */

/* 事件文本 → 关联大宗商品涨跌 */
const COMMODITY_KEYWORDS = {
  'oil':       ['原油','石油','油价','成品油','燃油','OPEC','霍尔木兹','中东','伊朗','海峡封锁','航运'],
  'chemical':  ['化工','乙烯','丙烯','PTA','甲醇','尿素','炼化','石化'],
  'gold':      ['黄金','金价','避险','贵金属','央行购金'],
  'silver':    ['白银','银价','贵金属'],
  'nonferrous':['有色金属','铜','铝','锌','镍','稀土','锂'],
  'coal':      ['煤炭','焦煤','焦炭','动力煤','火电'],
  'soy':       ['大豆','豆粕','农产品','粮食'],
};

function _matchCommodities(text, commodityHist) {
  if (!text || !commodityHist) return [];
  var results = [];
  COMMODITY_PROXY_FUNDS.forEach(function(fund) {
    var keywords = COMMODITY_KEYWORDS[fund.key] || [];
    var matched = keywords.some(function(kw) { return text.indexOf(kw) >= 0; });
    if (!matched) return;
    var hist = (commodityHist[fund.code] || []).slice(-30);
    if (hist.length < 2) return;
    var first = Number(hist[0].nav || 0);
    var last = Number(hist[hist.length - 1].nav || 0);
    if (!first || !last) return;
    var pct = ((last - first) / first) * 100;
    results.push({
      key: fund.key,
      name: fund.name,
      color: fund.color,
      pct: pct,
      pctStr: (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%',
      pctClass: pct >= 0 ? 'pct-up' : 'pct-down',
    });
  });
  return results;
}

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function classifyAction(advice) {
  if (/加仓|买入/.test(advice)) return 'bullish';
  if (/减仓|卖出|回避/.test(advice)) return 'bearish';
  return 'neutral';
}

/* ====== Markdown → HTML (用于 rich-text 渲染，自动适配明暗模式) ====== */
function _getIsDark() {
  try { return wx.getSystemInfoSync().theme === 'dark'; }
  catch(e) { return false; }
}

function _mdToHtml(md) {
  if (!md) return '';
  var dark = _getIsDark();
  var C = dark ? {
    text:'#e2e8f0', heading:'#f1f5f9', bold:'#f1f5f9',
    chainText:'#fbbf24', chainArrow:'#f87171',
    chainBg:'linear-gradient(90deg,rgba(251,191,36,0.15),rgba(239,68,68,0.12))',
    tblText:'#e2e8f0', tblHeadBg:'rgba(148,163,184,0.15)',
    tblAltBg:'rgba(148,163,184,0.06)', tblBorder:'rgba(148,163,184,0.2)',
  } : {
    text:'#334155', heading:'#1e293b', bold:'#1e293b',
    chainText:'#92400e', chainArrow:'#ef4444',
    chainBg:'linear-gradient(90deg,rgba(251,191,36,0.15),rgba(239,68,68,0.10))',
    tblText:'#334155', tblHeadBg:'#f1f5f9',
    tblAltBg:'#fafbfc', tblBorder:'#e2e8f0',
  };
  var html = md;

  // 表格转换: | xxx | yyy | → <table>
  html = html.replace(/((?:\|[^\n]+\|\n)+)/g, function(tableBlock) {
    var rows = tableBlock.trim().split('\n');
    var out = '<table style="width:100%;border-collapse:collapse;font-size:12px;margin:8px 0;color:' + C.tblText + ';">';
    rows.forEach(function(row, ri) {
      // 跳过分隔行 |---|---|
      if (/^\|[\s\-:]+\|/.test(row)) return;
      var cells = row.split('|').filter(function(c, i, a) { return i > 0 && i < a.length - 1; });
      var tag = ri === 0 ? 'th' : 'td';
      var bgStyle = ri === 0 ? 'background:' + C.tblHeadBg + ';font-weight:700;' : (ri % 2 === 0 ? 'background:' + C.tblAltBg + ';' : '');
      out += '<tr>';
      cells.forEach(function(c) {
        var val = c.replace(/\*\*/g, '').trim();
        out += '<' + tag + ' style="border:1px solid ' + C.tblBorder + ';padding:4px 6px;text-align:left;' + bgStyle + '">' + val + '</' + tag + '>';
      });
      out += '</tr>';
    });
    out += '</table>';
    return out;
  });

  // 箭头链传导（单行 xxx → yyy → zzz）
  html = html.replace(/^(.+→.+)$/gm, function(line) {
    return '<div style="background:' + C.chainBg + ';padding:6px 10px;border-radius:6px;font-size:12px;color:' + C.chainText + ';margin:6px 0;line-height:1.6;word-break:break-all;">' + line.replace(/→/g, ' <span style="color:' + C.chainArrow + ';">→</span> ') + '</div>';
  });

  // 标题 **xxx**（独立行）
  html = html.replace(/^\*\*([^*]+)\*\*\s*$/gm, '<div style="font-weight:700;font-size:13px;color:' + C.heading + ';margin:10px 0 4px;">$1</div>');

  // 粗体 inline
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong style="color:' + C.bold + ';">$1</strong>');

  // 无序列表 - xxx
  html = html.replace(/^- (.+)$/gm, '<div style="padding-left:12px;text-indent:-12px;margin:3px 0;line-height:1.6;">• $1</div>');

  // 换行
  html = html.replace(/\n/g, '<br/>');

  return '<div style="font-size:12px;color:' + C.text + ';line-height:1.7;">' + html + '</div>';
}
