const { getHoldings, getSettings } = require('../../utils/storage');
const { fetchHotEvents, fetchIndices, fetchMultiFundEstimates, fetchSectorFlows, fetchMultiFundHistory, fetchCommodities, fetchSectorTopFunds, fetchSectorTopStocks, fetchServerFundPick, fetchRealtimeBreaking, getServerBase, fetchSentimentData } = require('../../utils/api');
const { buildPlans, buildOverview, MODEL_PORTFOLIO, matchSectorFlow } = require('../../utils/advisor');
const { getMarketStatus, isMarketOpen, formatPct, pctClass, formatTime, isTradingDay, formatMoney } = require('../../utils/market');
const { runAIAnalysis, runSingleFundAI, getCachedAIResult, getAIConfig, runFundPickAI, getCachedFundPick, saveServerFundPick } = require('../../utils/ai');

function buildAnalystFallback(item) {
  const title = String(item && item.title || '');
  const category = String(item && item.category || '');
  const impact = Number(item && item.impact || 0);
  if (/(伊朗|中东|红海|霍尔木兹|地缘|冲突|战争)/.test(title) || category === 'geopolitics') {
    return '地缘风险升温，优先控制仓位并跟踪油气与黄金联动强度';
  }
  if (/(原油|油气|OPEC|黄金|有色|铜|铝|贵金属|锂)/.test(title) || category === 'commodity' || category === 'commodity_anomaly') {
    return impact >= 0
      ? '短线情绪偏热，建议分批兑现并观察成交持续性'
      : '波动放大期以防守为主，等待价格与资金共振再加仓';
  }
  return impact >= 0
    ? '事件驱动偏强但拥挤度上升，建议回撤分批而非追涨'
    : '风险偏好回落，建议降低杠杆并等待二次确认信号';
}

function normalizeBreakingTitle(text) {
  const stopWords = [
    '美国对', '消息', '快讯', 'live', '最新', 'breaking',
    '引发', '导致', '面临', '可能', '造成',
  ];
  let normalized = String(text || '')
    .toLowerCase()
    .replace(/\s+/g, '')
    .replace(/[“”"'`·•:：，,。！？!？（）()【】\[\]、;；\-—]/g, '');
  stopWords.forEach(word => {
    normalized = normalized.replace(new RegExp(word, 'g'), '');
  });
  return normalized;
}

function toBigrams(text) {
  const clean = normalizeBreakingTitle(text);
  if (!clean) return new Set();
  if (clean.length < 2) return new Set([clean]);
  const grams = new Set();
  for (let i = 0; i < clean.length - 1; i += 1) {
    grams.add(clean.slice(i, i + 2));
  }
  return grams;
}

function setOverlapRatio(aSet, bSet) {
  if (!aSet || !bSet || aSet.size === 0 || bSet.size === 0) return 0;
  let overlap = 0;
  aSet.forEach(v => { if (bSet.has(v)) overlap += 1; });
  const denom = Math.max(1, Math.min(aSet.size, bSet.size));
  return overlap / denom;
}

function buildThemeSet(item) {
  const values = [];
  const concepts = Array.isArray(item && item.concepts) ? item.concepts : [];
  const sectorsPos = String(item && item.sectorsPos || '').split('、').filter(Boolean);
  const sectorsNeg = String(item && item.sectorsNeg || '').split('、').filter(Boolean);

  values.push(String(item && item.category || ''));
  values.push(String(item && item.reason || ''));
  values.push(String(item && item.title || ''));
  values.push.apply(values, concepts);
  values.push.apply(values, sectorsPos);
  values.push.apply(values, sectorsNeg);

  const set = new Set();
  values.forEach(v => {
    const key = normalizeBreakingTitle(v);
    if (key && key.length >= 2) set.add(key);
  });
  return set;
}

function buildBreakingDedupMeta(item) {
  return {
    titleKey: normalizeBreakingTitle(item && item.title || ''),
    titleBigrams: toBigrams(item && item.title || ''),
    titleRaw: String(item && item.title || ''),
    themeSet: buildThemeSet(item),
    category: String(item && item.category || ''),
    eventTs: parseEventTs(item && item.eventTime),
  };
}

function tokenJaccard(a, b) {
  var rawA = String(a || '').toLowerCase().replace(/[\s\u3000]+/g, '').replace(/["""'`·•:：，,。！？!？（）()【】\[\]、;；\-—]/g, '');
  var rawB = String(b || '').toLowerCase().replace(/[\s\u3000]+/g, '').replace(/["""'`·•:：，,。！？!？（）()【】\[\]、;；\-—]/g, '');
  if (!rawA || !rawB) return 0;
  var tokA = rawA.match(/[\u4e00-\u9fff]{2,}|[a-z0-9]+/g) || [];
  var tokB = rawB.match(/[\u4e00-\u9fff]{2,}|[a-z0-9]+/g) || [];
  if (!tokA.length || !tokB.length) return 0;
  var setA = {}; tokA.forEach(function(t) { setA[t] = 1; });
  var setB = {}; tokB.forEach(function(t) { setB[t] = 1; });
  var inter = 0;
  Object.keys(setA).forEach(function(k) { if (setB[k]) inter++; });
  var union = Object.keys(setA).length + Object.keys(setB).length - inter;
  return union > 0 ? inter / union : 0;
}

function isBreakingEventDuplicate(item, seenMetas) {
  const current = buildBreakingDedupMeta(item);
  if (!current.titleKey) return true;

  for (let i = 0; i < seenMetas.length; i += 1) {
    const prev = seenMetas[i];
    if (!prev || !prev.titleKey) continue;

    if (prev.titleKey === current.titleKey) return true;

    const minLen = Math.min(prev.titleKey.length, current.titleKey.length);
    if ((prev.titleKey.includes(current.titleKey) || current.titleKey.includes(prev.titleKey)) && minLen >= 6) {
      const closeTime = Math.abs((prev.eventTs || 0) - (current.eventTs || 0)) <= 48 * 3600 * 1000;
      if (closeTime || !prev.eventTs || !current.eventTs) return true;
    }

    const titleSim = setOverlapRatio(prev.titleBigrams, current.titleBigrams);
    const themeSim = setOverlapRatio(prev.themeSet, current.themeSet);
    const tokenSim = tokenJaccard(prev.titleRaw, current.titleRaw);
    const sameCategory = prev.category && current.category && prev.category === current.category;
    const timeGap = Math.abs((prev.eventTs || 0) - (current.eventTs || 0));
    const in24h = timeGap <= 24 * 3600 * 1000;
    const in48h = timeGap <= 48 * 3600 * 1000;

    if (sameCategory && titleSim >= 0.55 && (in24h || !prev.eventTs || !current.eventTs)) return true;
    if (sameCategory && tokenSim >= 0.45 && (in48h || !prev.eventTs || !current.eventTs)) return true;
    if (titleSim >= 0.50 && themeSim >= 0.45 && (in48h || !prev.eventTs || !current.eventTs)) return true;
    if (tokenSim >= 0.55 && themeSim >= 0.40 && (in48h || !prev.eventTs || !current.eventTs)) return true;
  }
  return false;
}

function parseEventTs(value) {
  if (!value) return 0;
  const ts = Date.parse(String(value));
  return Number.isNaN(ts) ? 0 : ts;
}

function compareBreakingPriority(a, b) {
  const impactDiff = Number(b.impactAbs || 0) - Number(a.impactAbs || 0);
  if (impactDiff !== 0) return impactDiff;
  const timeDiff = parseEventTs(b.eventTime) - parseEventTs(a.eventTime);
  if (timeDiff !== 0) return timeDiff;
  return Number(!!b.isRealtime) - Number(!!a.isRealtime);
}

Page({
  data: {
    // 市场状态
    marketStatus: '加载中...',
    marketDot: 'gray',
    currentTime: '--:--:--',
    tradingDay: false,

    // 指数行情
    indices: [],

    // 持仓估值
    holdings: [],
    totalPct: '--',
    totalPctClass: 'flat',

    // 行动指南
    overview: { buy: 0, sell: 0, hold: 0, score: 0, label: '加载中...' },
    plans: [],

    // 白话研判
    showAdvisor: false,

    // 模型组合
    modelCore: MODEL_PORTFOLIO.core,
    modelSat: MODEL_PORTFOLIO.satellite,
    showModel: false,

    // 大宗商品
    commodities: [],

    // 热点异动事件 (置顶地缘/商品)
    hotBreaking: [],
    breakingGroups: [],
    expandedCats: {},
    rtUpdatedAt: '',
    rtCountdown: '',

    // 热点事件
    topEvents: [],
    heatmap: [],
    sectorFlows: [],

    // 数据源
    sourceLabel: '加载中',
    updatedAt: '--',
    loading: true,

    // 定时器
    _timer: null,
    _rtTimer: null,

    // AI 分析
    showAI: false,
    aiLoading: false,
    aiProgress: '',
    aiResult: null,
    aiTime: '',
    aiHasKey: false,

    // AI 推荐基金
    showRecommendations: true,
    aiRecommendations: [],
    aiMarketOutlook: '',
    aiSectorRotation: '',
    aiMarketTemp: 50,

    // 单基金 AI 分析
    singleAILoading: '',  // 正在分析的基金代码，空串表示未在分析
    singleAIProgress: '',
    singleAIResult: null,
    singleAITime: '',
    showSingleAI: false,

    // 选基金/股票
    fundPickLoading: false,
    fundPickProgress: '',
    fundPickResult: null,
    fundPickTime: '',

    // 调试
    debugError: '',
  },

  onLoad() {
    try { this.updateMarketStatus(); } catch(e) { console.error('updateMarketStatus error:', e); this.setData({ debugError: 'updateMarketStatus: ' + (e.message || e) }); }
    this.loadAll().catch(e => { console.error('loadAll error:', e); this.setData({ loading: false, debugError: 'loadAll: ' + (e.message || e) }); });
    try { this._loadAICache(); } catch(e) { console.error('_loadAICache error:', e); this.setData({ debugError: '_loadAICache: ' + (e.message || e) }); }
    try { this._loadFundPickCache(); } catch(e) { console.error('_loadFundPickCache error:', e); }
  },

  onShow() {
    try { this.updateMarketStatus(); } catch(e) { console.error('updateMarketStatus error:', e); }
    this.loadAll().catch(e => { console.error('loadAll error:', e); this.setData({ loading: false, debugError: 'loadAll(onShow): ' + (e.message || e) }); });
    try { this._loadAICache(); } catch(e) { console.error('_loadAICache error:', e); }
    try { this._loadFundPickCache(); } catch(e) { console.error('_loadFundPickCache error:', e); }
    const timer = setInterval(() => {
      try {
        this.updateMarketStatus();
        if (isMarketOpen()) this.refreshQuotes().catch(e => console.error('refreshQuotes error:', e));
      } catch(e) { console.error('timer error:', e); }
    }, 30000);
    this.setData({ _timer: timer });

    // 实时热点全天候高频轮询 (60秒)
    this._rtNextRefresh = Date.now() + 60000;
    const rtTimer = setInterval(() => {
      try {
        const remain = Math.max(0, Math.ceil((this._rtNextRefresh - Date.now()) / 1000));
        if (remain > 0) {
          this.setData({ rtCountdown: remain + 's' });
        } else {
          this.setData({ rtCountdown: '刷新中...' });
          this.refreshBreaking().catch(e => console.error('refreshBreaking error:', e));
          this._rtNextRefresh = Date.now() + 60000;
        }
      } catch(e) { console.error('rtTimer error:', e); }
    }, 1000);
    this.setData({ _rtTimer: rtTimer });
  },

  onHide() {
    if (this.data._timer) { clearInterval(this.data._timer); this.setData({ _timer: null }); }
    if (this.data._rtTimer) { clearInterval(this.data._rtTimer); this.setData({ _rtTimer: null }); }
  },

  onUnload() {
    if (this.data._timer) clearInterval(this.data._timer);
    if (this.data._rtTimer) clearInterval(this.data._rtTimer);
  },

  onPullDownRefresh() {
    this.loadAll().finally(() => wx.stopPullDownRefresh());
  },

  updateMarketStatus() {
    const ms = getMarketStatus();
    let dot = 'gray';
    if (ms.status === 'open') dot = 'green';
    else if (ms.status === 'break') dot = 'orange';
    else if (ms.status === 'pre') dot = 'blue';
    this.setData({
      marketStatus: ms.text, marketDot: dot,
      currentTime: formatTime(), tradingDay: isTradingDay(),
    });
  },

  async loadAll() {
    this.setData({ loading: true });
    try {
    const holdings = getHoldings();
    const settings = getSettings();
    const app = getApp();
    const codes = holdings.map(h => h.code);

    // 实时突发优先从 Flask 服务器获取（与舆情页保持一致）
    const serverBase = getServerBase(settings);
    const rtSettings = serverBase ? { ...settings, apiBase: serverBase } : settings;

    // 并行获取: 指数 + 商品 + 热点 + 估值 + 历史净值 + 板块资金流 + 实时突发 + 舆情
    const [indicesData, commodityData, hotResult, estimates, historyData, sectorData, realtimeData, sentimentResult] = await Promise.allSettled([
      fetchIndices(app.globalData.INDICES),
      fetchCommodities(app.globalData.COMMODITIES || []),
      fetchHotEvents(rtSettings),
      fetchMultiFundEstimates(codes),
      fetchMultiFundHistory(codes),
      fetchSectorFlows(),
      fetchRealtimeBreaking(rtSettings),
      fetchSentimentData(rtSettings),
    ]);

    // 指数行情
    const rawIndices = indicesData.status === 'fulfilled' ? indicesData.value : [];
    const indices = rawIndices.map(idx => ({
      ...idx, pctStr: formatPct(idx.pct), pctClass: pctClass(idx.pct),
    }));

    // 大宗商品
    const rawCommodities = commodityData.status === 'fulfilled' ? commodityData.value : [];
    const commodities = rawCommodities.map(c => ({
      ...c, pctStr: formatPct(c.pct), pctClass: pctClass(c.pct),
      anomaly: Math.abs(c.pct) >= 2,
    }));

    // 热点事件
    const hotData = hotResult.status === 'fulfilled' ? hotResult.value : { source: 'local', data: { heatmap: [], events: [] } };
    // 用真实行情数据修正热力图趋势方向
    const heatmap = this._fixHeatmapTrends((hotData.data.heatmap || []).slice(0, 14), commodities, indices);
    const allEvents = (hotData.data.events || []).map((item, idx) => {
      const rawReason = item.reason || '';
      const reasonHasAnalyst = rawReason.indexOf('分析师观点：') >= 0;
      const analystViewFromReason = reasonHasAnalyst
        ? rawReason.split('分析师观点：').slice(1).join('分析师观点：').trim()
        : '';
      const cleanReason = reasonHasAnalyst
        ? rawReason.split('分析师观点：')[0].replace(/[；;，,\s]+$/, '')
        : rawReason;

      const analystView = item.analyst_view || analystViewFromReason || buildAnalystFallback(item);

      return {
        id: item.id || ('evt_hot_' + idx), title: item.title, advice: item.advice || '保持观察',
        reason: cleanReason,
        analystView,
        category: item.category || '',
        source: item.source || '市场事件',
        concepts: item.concepts || [],
        sectorsPos: (item.sectors_positive || []).join('、'),
        sectorsNeg: (item.sectors_negative || []).join('、'),
        impact: Number(item.impact || 0),
        impactClass: Number(item.impact || 0) >= 0 ? 'up' : 'down',
        impactAbs: Math.abs(Number(item.impact || 0)),
        confidence: Number(item.confidence || 0),
        eventTime: item.time || item.timestamp || hotData.data.updated_at || '',
        isGeo: item.category === 'geopolitics',
        isCommodity: item.category === 'commodity',
        isTemplate: !!item.is_template,
      };
    });
    // 动态事件优先，模板事件排在最后
    const dynamicEvents = allEvents.filter(e => !e.isTemplate);
    const templateEvents = allEvents.filter(e => e.isTemplate);
    const sortedEvents = [...dynamicEvents, ...templateEvents];
    const topEvents = sortedEvents.slice(0, 5);

    // 热点异动: 并入“市场事件”动态流（排除静态模板）
    const marketEvents = allEvents.filter(e => !e.isTemplate).map(e => ({
      ...e,
      catIcon: e.category === 'geopolitics'
        ? '🌍'
        : e.category === 'monetary'
        ? '🏦'
        : e.category === 'technology'
        ? '🤖'
        : e.category === 'market'
        ? '📊'
        : e.category === 'policy'
        ? '📜'
        : '📦',
      isRealtime: false,
      fromMarketEvent: true,
    }));
    const commodityAnomalies = commodities.filter(c => Math.abs(c.pct) >= 1.5).map(c => ({
      id: 'anom_' + c.code,
      title: c.icon + ' ' + c.name + (c.pct >= 0 ? '大涨' : '大跌') + ' ' + c.pctStr,
      reason: Math.abs(c.pct) >= 3 ? '超常波动，关注相关持仓' : '显著异动，留意联动',
      impact: Math.round(c.pct * 2),
      impactClass: c.pct >= 0 ? 'up' : 'down',
      impactAbs: Math.abs(Math.round(c.pct * 2)),
      category: 'commodity_anomaly',
      isGeo: false, isCommodity: true,
      concepts: [c.name],
      advice: Math.abs(c.pct) >= 3 ? '关注偏离修复机会' : '观察后续走势',
      eventTime: hotData.data.updated_at || '',
      fromMarketEvent: false,
    }));

    // ====== 实时突发新闻（路透社/彭博社/CNBC等）+ 全球市场异动 ======
    const rtData = realtimeData.status === 'fulfilled' ? realtimeData.value : null;
    const realtimeBreakingItems = [];
    const realtimeAnomalyItems = [];
    const rtUpdatedAt = (rtData && rtData.updated_at) ? String(rtData.updated_at).replace('T', ' ').slice(0, 16) : '';

    if (rtData && Array.isArray(rtData.breaking)) {
      rtData.breaking.forEach(item => {
        const catIconMap = {
          'geopolitics': '🌍', 'commodity': '📦', 'monetary': '🏦',
          'technology': '🤖', 'market': '📊', 'policy': '📜',
        };
        const cat = item.category || 'market';
        const isGeo = cat === 'geopolitics';
        const isCommodity = cat === 'commodity' || cat === 'commodity_anomaly';
        realtimeBreakingItems.push({
          id: item.id || 'rtb_' + Math.random().toString(36).slice(2, 8),
          title: item.title || '',
          reason: item.reason || '',
          analystView: item.analystView || '',
          source: item.source || '',
          category: cat,
          catIcon: catIconMap[cat] || '📰',
          impact: Number(item.impact || 0),
          impactClass: Number(item.impact || 0) >= 0 ? 'up' : 'down',
          impactAbs: Math.abs(Number(item.impact || 0)),
          isGeo,
          isCommodity,
          isRealtime: true,
          sectorsPos: (item.sectors_positive || []).join('、'),
          sectorsNeg: (item.sectors_negative || []).join('、'),
          advice: item.advice || '保持观察',
          eventTime: item.timestamp || rtData.updated_at || '',
          fromMarketEvent: false,
        });
      });
    }

    if (rtData && Array.isArray(rtData.anomalies)) {
      rtData.anomalies.forEach(a => {
        const pct = Number(a.pct || 0);
        realtimeAnomalyItems.push({
          id: a.id || 'anom_rt_' + Math.random().toString(36).slice(2, 8),
          title: a.alert || (a.icon + ' ' + a.name + (pct >= 0 ? '大涨' : '大跌') + Math.abs(pct).toFixed(1) + '%'),
          reason: a.level + '异动 · ' + a.fullName,
          impact: Math.round(pct * 2),
          impactClass: pct >= 0 ? 'up' : 'down',
          impactAbs: Math.abs(Math.round(pct * 2)),
          category: a.type === 'index' ? 'market' : 'commodity_anomaly',
          catIcon: a.type === 'index' ? '📊' : '📦',
          isGeo: false,
          isCommodity: a.type !== 'index',
          isRealtime: true,
          source: '行情监控',
          concepts: a.tag ? [a.tag] : [a.name],
          advice: Math.abs(pct) >= 3 ? '关注偏离修复机会' : '观察后续走势',
          eventTime: a.timestamp || rtData.updated_at || '',
          fromMarketEvent: false,
        });
      });
    }

    // ====== 舆情社媒趋势 → 事件化 ======
    const sentimentTrendItems = [];
    const rawSentiment = sentimentResult.status === 'fulfilled' ? sentimentResult.value : null;
    if (rawSentiment && Array.isArray(rawSentiment.trends)) {
      const trendCatMap = {
        geopolitics: 'geopolitics', 'military': 'geopolitics',
        gold: 'commodity', oil: 'commodity', nonferrous: 'commodity', commodity: 'commodity',
        ai: 'technology', tech: 'technology',
        macro: 'monetary', policy: 'monetary', bond: 'monetary',
        stock: 'market', real_estate: 'market', consumer: 'market', fund: 'market',
        energy: 'commodity', hk_us: 'market', dividend: 'market',
      };
      rawSentiment.trends
        .filter(t => (t.heat_score || 0) >= 100 && (t.sample_titles || []).length > 0)
        .forEach(t => {
          const bestTitle = t.sample_titles[0] || t.name;
          const cat = trendCatMap[t.id] || 'market';
          const catIconMap = { geopolitics: '🌍', commodity: '📦', monetary: '🏦', technology: '🤖', market: '📊', policy: '📜' };
          const sentimentImpact = /偏多|看多/.test(t.sentiment) ? 3 : /偏空|看空/.test(t.sentiment) ? -3 : 0;
          sentimentTrendItems.push({
            id: 'sm_' + (t.id || Math.random().toString(36).slice(2, 8)),
            title: bestTitle,
            reason: t.icon + ' 社媒热度' + (t.heat_score || 0).toFixed(0) + '° · ' + (t.mention_count || 0) + '条讨论 · 情绪' + (t.sentiment || '中性'),
            analystView: '',
            source: '社媒舆情',
            category: cat,
            catIcon: catIconMap[cat] || '📊',
            impact: sentimentImpact,
            impactClass: sentimentImpact >= 0 ? 'up' : 'down',
            impactAbs: Math.abs(sentimentImpact),
            isGeo: cat === 'geopolitics',
            isCommodity: cat === 'commodity',
            isRealtime: false,
            fromMarketEvent: false,
            fromSentiment: true,
            concepts: (t.keywords_hit || []).slice(0, 5),
            sectorsPos: '',
            sectorsNeg: '',
            advice: '关注社媒声量变化，结合基本面判断',
            eventTime: (rawSentiment.fetch_time || '').replace('T', ' ').slice(0, 16),
          });
        });
    }

    // 合并所有来源：实时突发 > 市场事件 > 商品异动 > 实时异动 > 舆情趋势
    // 语义去重，避免同一事件不同表述重复出现
    const seenMetas = [];
    const allBreakingRaw = [...realtimeBreakingItems, ...marketEvents, ...commodityAnomalies, ...realtimeAnomalyItems, ...sentimentTrendItems];
    const allBreakingDeduped = [];
    allBreakingRaw.forEach(item => {
      if (!isBreakingEventDuplicate(item, seenMetas)) {
        seenMetas.push(buildBreakingDedupMeta(item));
        allBreakingDeduped.push(item);
      }
    });

    const sortedBreaking = allBreakingDeduped.sort(compareBreakingPriority);
    // 不设硬上限，展示全部去重后的事件
    let hotBreaking = sortedBreaking;

    const hotUpdatedRaw = hotData && hotData.data ? hotData.data.updated_at : '';
    const mergedRtUpdatedAt = parseEventTs(rtData && rtData.updated_at) >= parseEventTs(hotUpdatedRaw)
      ? rtUpdatedAt
      : String(hotUpdatedRaw || '').replace('T', ' ').slice(0, 16);

    // 基金估值
    const estData = estimates.status === 'fulfilled' ? estimates.value : {};
    const holdingsWithEst = holdings.map(h => {
      const est = estData[h.code];
      return {
        ...h,
        pct: est ? est.pct : null,
        pctStr: est ? formatPct(est.pct) : '待开盘',
        pctClass: est ? pctClass(est.pct) : 'flat',
        estimate: est ? est.estimate : null,
        nav: est ? est.nav : null,
        time: est ? est.time : null,
      };
    });

    // 计算总涨跌
    const validPcts = holdingsWithEst.filter(h => h.pct !== null);
    let totalPct = '--', totalPctClass = 'flat';
    if (validPcts.length > 0) {
      const avg = validPcts.reduce((s, h) => s + h.pct, 0) / validPcts.length;
      totalPct = formatPct(avg); totalPctClass = pctClass(avg);
    }

    // 历史数据 & 板块资金流
    const historyMap = historyData.status === 'fulfilled' ? historyData.value : {};
    const sectorFlows = sectorData.status === 'fulfilled' ? sectorData.value : [];

    // 行动指南 (使用完整分析引擎)
    const plans = buildPlans(holdingsWithEst, hotData.data.heatmap || [], historyMap, sectorFlows);
    const overview = buildOverview(plans);

    this.setData({
      indices, commodities, hotBreaking,
      holdings: holdingsWithEst, totalPct, totalPctClass,
      overview, plans,
      topEvents, heatmap, sectorFlows,
      sourceLabel: hotData.source === 'remote' ? '远程数据' : '本地回退',
      updatedAt: String(hotData.data.updated_at || '--').replace('T', ' ').slice(0, 16),
      rtUpdatedAt: mergedRtUpdatedAt,
      loading: false,
    });
    this._buildBreakingGroups(hotBreaking);

    // 合并缓存的 AI 信号到每只基金卡片
    this._mergeAIIntoPlans();
    } catch (e) {
      console.error('[loadAll] error:', e);
      this.setData({ loading: false, debugError: 'loadAll内部: ' + (e.message || String(e)) });
    }
  },

  async refreshBreaking() {
    const settings = getSettings();
    const serverBase = getServerBase(settings);
    const rtSettings = serverBase ? { ...settings, apiBase: serverBase } : settings;

    const rtData = await fetchRealtimeBreaking(rtSettings);
    if (!rtData || !Array.isArray(rtData.breaking)) return;

    const realtimeBreakingItems = [];
    rtData.breaking.forEach(function(item) {
      var catIconMap = {
        'geopolitics': '🌍', 'commodity': '📦', 'monetary': '🏦',
        'technology': '🤖', 'market': '📊', 'policy': '📜',
      };
      var cat = item.category || 'market';
      realtimeBreakingItems.push({
        id: item.id || 'rtb_' + Math.random().toString(36).slice(2, 8),
        title: item.title || '',
        reason: item.reason || '',
        analystView: item.analystView || '',
        source: item.source || '',
        category: cat,
        catIcon: catIconMap[cat] || '📰',
        impact: Number(item.impact || 0),
        impactClass: Number(item.impact || 0) >= 0 ? 'up' : 'down',
        impactAbs: Math.abs(Number(item.impact || 0)),
        isGeo: cat === 'geopolitics',
        isCommodity: cat === 'commodity' || cat === 'commodity_anomaly',
        isRealtime: true,
        sectorsPos: (item.sectors_positive || []).join('、'),
        sectorsNeg: (item.sectors_negative || []).join('、'),
        advice: item.advice || '保持观察',
        eventTime: item.timestamp || rtData.updated_at || '',
        fromMarketEvent: false,
      });
    });

    var realtimeAnomalyItems = [];
    if (Array.isArray(rtData.anomalies)) {
      rtData.anomalies.forEach(function(a) {
        var pct = Number(a.pct || 0);
        realtimeAnomalyItems.push({
          id: a.id || 'anom_rt_' + Math.random().toString(36).slice(2, 8),
          title: a.alert || (a.icon + ' ' + a.name + (pct >= 0 ? '大涨' : '大跌') + Math.abs(pct).toFixed(1) + '%'),
          reason: a.level + '异动 · ' + a.fullName,
          impact: Math.round(pct * 2),
          impactClass: pct >= 0 ? 'up' : 'down',
          impactAbs: Math.abs(Math.round(pct * 2)),
          category: a.type === 'index' ? 'market' : 'commodity_anomaly',
          catIcon: a.type === 'index' ? '📊' : '📦',
          isGeo: false,
          isCommodity: a.type !== 'index',
          isRealtime: true,
          source: '行情监控',
          concepts: a.tag ? [a.tag] : [a.name],
          advice: Math.abs(pct) >= 3 ? '关注偏离修复机会' : '观察后续走势',
          eventTime: a.timestamp || rtData.updated_at || '',
          fromMarketEvent: false,
        });
      });
    }

    // 保留现有 marketEvent 项（来自 hot_events），与新实时数据合并去重
    var existingMarket = (this.data.hotBreaking || []).filter(function(i) { return i.fromMarketEvent; });
    var allBreakingRaw = [].concat(realtimeBreakingItems, existingMarket, realtimeAnomalyItems);
    var seenMetas = [];
    var allBreakingDeduped = [];
    allBreakingRaw.forEach(function(item) {
      if (!isBreakingEventDuplicate(item, seenMetas)) {
        seenMetas.push(buildBreakingDedupMeta(item));
        allBreakingDeduped.push(item);
      }
    });

    var hotBreaking = allBreakingDeduped.sort(compareBreakingPriority).slice(0, 12);
    var rtUpdatedAt = String(rtData.updated_at || '').replace('T', ' ').slice(0, 16);
    this.setData({ hotBreaking: hotBreaking, rtUpdatedAt: rtUpdatedAt });
    this._buildBreakingGroups(hotBreaking);
  },

  async refreshQuotes() {
    const app = getApp();
    const holdings = getHoldings();
    const [indicesData, commodityData, estimates] = await Promise.allSettled([
      fetchIndices(app.globalData.INDICES),
      fetchCommodities(app.globalData.COMMODITIES || []),
      fetchMultiFundEstimates(holdings.map(h => h.code)),
    ]);
    const rawIndices = indicesData.status === 'fulfilled' ? indicesData.value : [];
    const rawComm = commodityData.status === 'fulfilled' ? commodityData.value : [];
    const commodities = rawComm.map(c => ({
      ...c, pctStr: formatPct(c.pct), pctClass: pctClass(c.pct),
      anomaly: Math.abs(c.pct) >= 2,
    }));
    const indices = rawIndices.map(idx => ({
      ...idx, pctStr: formatPct(idx.pct), pctClass: pctClass(idx.pct),
    }));
    const estData = estimates.status === 'fulfilled' ? estimates.value : {};
    const holdingsWithEst = holdings.map(h => {
      const est = estData[h.code]; return {
        ...h, pct: est ? est.pct : null, pctStr: est ? formatPct(est.pct) : '待开盘',
        pctClass: est ? pctClass(est.pct) : 'flat', estimate: est ? est.estimate : null,
      };
    });
    const validPcts = holdingsWithEst.filter(h => h.pct !== null);
    let totalPct = '--', totalPctClass = 'flat';
    if (validPcts.length > 0) {
      const avg = validPcts.reduce((s, h) => s + h.pct, 0) / validPcts.length;
      totalPct = formatPct(avg); totalPctClass = pctClass(avg);
    }
    this.setData({ indices, commodities, holdings: holdingsWithEst, totalPct, totalPctClass, currentTime: formatTime() });
  },

  // 展开/收起基金详情
  togglePlan(e) {
    const idx = e.currentTarget.dataset.idx;
    const key = `plans[${idx}].expanded`;
    this.setData({ [key]: !this.data.plans[idx].expanded });
  },

  toggleBreakingGroup(e) {
    const cat = e.currentTarget.dataset.cat;
    const key = 'expandedCats.' + cat;
    this.setData({ [key]: !this.data.expandedCats[cat] });
  },

  _buildBreakingGroups(items) {
    var CAT_ORDER = ['geopolitics', 'monetary', 'policy', 'market', 'technology', 'commodity', 'commodity_anomaly'];
    var CAT_LABELS = {
      geopolitics: '地缘', monetary: '央行', policy: '政策',
      market: '市场', technology: '科技', commodity: '商品', commodity_anomaly: '商品异动',
    };
    var CAT_ICONS = {
      geopolitics: '🌍', monetary: '🏦', policy: '📜',
      market: '📊', technology: '🤖', commodity: '📦', commodity_anomaly: '📦',
    };
    var grouped = {};
    (items || []).forEach(function(item) {
      var cat = item.category || 'market';
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(item);
    });
    var groups = [];
    CAT_ORDER.forEach(function(cat) {
      if (grouped[cat] && grouped[cat].length > 0) {
        groups.push({
          cat: cat,
          label: CAT_LABELS[cat] || cat,
          icon: CAT_ICONS[cat] || '📰',
          count: grouped[cat].length,
          items: grouped[cat],
        });
      }
    });
    // 不在预设列表中的类别追加到最后
    Object.keys(grouped).forEach(function(cat) {
      if (CAT_ORDER.indexOf(cat) === -1 && grouped[cat].length > 0) {
        groups.push({
          cat: cat,
          label: CAT_LABELS[cat] || cat,
          icon: CAT_ICONS[cat] || '📰',
          count: grouped[cat].length,
          items: grouped[cat],
        });
      }
    });
    // 默认展开第一个分组
    var expandedCats = this.data.expandedCats || {};
    var hasAnyExpanded = false;
    groups.forEach(function(g) { if (expandedCats[g.cat]) hasAnyExpanded = true; });
    if (!hasAnyExpanded && groups.length > 0) {
      expandedCats[groups[0].cat] = true;
    }
    this.setData({ breakingGroups: groups, expandedCats: expandedCats });
  },

  // 展开/收起白话研判
  toggleAdvisor() {
    this.setData({ showAdvisor: !this.data.showAdvisor });
  },

  // 展开/收起模型组合
  toggleModel() {
    this.setData({ showModel: !this.data.showModel });
  },

  goHoldings() { wx.switchTab({ url: '/pages/holdings/index' }); },
  goSentiment() { wx.switchTab({ url: '/pages/sentiment/index' }); },
  goSettings() { wx.switchTab({ url: '/pages/settings/index' }); },

  // ====== 单基金 AI 分析 ======
  async triggerSingleFundAI(e) {
    const { code, name, type } = e.currentTarget.dataset;
    if (!code) return;

    const cfg = getAIConfig();
    if (!cfg.key) {
      wx.showModal({
        title: '未配置 API Key',
        content: '请先在"设置"页配置 AI API Key',
        confirmText: '去设置',
        success: (res) => {
          if (res.confirm) wx.switchTab({ url: '/pages/settings/index' });
        },
      });
      return;
    }

    if (this.data.singleAILoading) return; // 防止重复

    this.setData({
      singleAILoading: code,
      singleAIProgress: `正在分析 ${name}...`,
    });

    // 进度提示
    const t1 = setTimeout(() => {
      if (this.data.singleAILoading === code) {
        this.setData({ singleAIProgress: 'AI 正在深度分析走势原因...' });
      }
    }, 5000);
    const t2 = setTimeout(() => {
      if (this.data.singleAILoading === code) {
        this.setData({ singleAIProgress: '分析即将完成，请稍候...' });
      }
    }, 15000);
    const clearTimers = () => { clearTimeout(t1); clearTimeout(t2); };

    try {
      const holdings = getHoldings();
      const fund = holdings.find(h => h.code === code) || { code, name, type };
      const codes = [code];
      const [estimates, historyMap, sectorFlowsData] = await Promise.all([
        require('../../utils/api').fetchMultiFundEstimates(codes),
        require('../../utils/api').fetchMultiFundHistory(codes),
        this.data.sectorFlows && this.data.sectorFlows.length > 0
          ? Promise.resolve(this.data.sectorFlows)
          : require('../../utils/api').fetchSectorFlows(),
      ]);

      const result = await runSingleFundAI({
        fund,
        estimates,
        historyMap,
        indices: this.data.indices,
        commodities: this.data.commodities,
        heatmap: this.data.heatmap,
        hotEvents: this.data.topEvents,
        sectorFlows: sectorFlowsData,
      });

      // 附加板块资金流原始数据到结果
      const sf = matchSectorFlow(fund.type, sectorFlowsData, fund.name);
      if (sf) {
        const flowPct = Number(sf.pct || 0);
        const flowMainPct = Number(sf.mainPct || 0);
        const flowMainNet = Number(sf.mainNet || 0);
        result._sectorFlow = {
          name: sf.name,
          mainNet: flowMainNet,
          mainPct: Number.isFinite(flowMainPct) ? flowMainPct.toFixed(2) : '0.00',
          pct: Number.isFinite(flowPct) ? flowPct.toFixed(2) : '0.00',
          flowText: (flowMainNet >= 0 ? '+' : '') + (flowMainNet / 1e8).toFixed(2) + '亿',
          flowDir: flowMainNet >= 0 ? 'in' : 'out',
        };
      }

      clearTimers();
      this.setData({
        singleAILoading: '',
        singleAIProgress: '',
        singleAIResult: result,
        singleAITime: new Date().toLocaleString(),
        showSingleAI: true,
      });
    } catch (e) {
      clearTimers();
      this.setData({ singleAILoading: '', singleAIProgress: '' });
      const msg = e.message || '未知错误';
      let content = msg;
      if (msg.includes('timeout') || msg.includes('超时')) {
        content = 'AI分析超时，请稍后重试';
      }
      wx.showModal({ title: 'AI 分析失败', content, showCancel: false });
    }
  },

  closeSingleAI() {
    this.setData({ showSingleAI: false });
  },

  // ====== 选基金/股票 ======
  _loadFundPickCache() {
    const cached = getCachedFundPick();
    if (cached && cached.result) {
      this.setData({
        fundPickResult: cached.result,
        fundPickTime: (cached.timestamp || '').replace('T', ' ').slice(0, 16),
      });
      return;
    }
    // 本地无缓存，尝试从服务器获取（14:50 自动生成）
    const settings = getSettings();
    fetchServerFundPick(settings).then(serverData => {
      if (serverData && serverData.result) {
        saveServerFundPick(serverData);
        this.setData({
          fundPickResult: serverData.result,
          fundPickTime: (serverData.timestamp || '').replace('T', ' ').slice(0, 16),
        });
      }
    }).catch(() => {});
  },

  async triggerFundPick() {
    const cfg = getAIConfig();
    if (!cfg.key) {
      wx.showModal({
        title: '未配置 API Key',
        content: '请先在"设置"页配置 AI API Key',
        confirmText: '去设置',
        success: (res) => {
          if (res.confirm) wx.switchTab({ url: '/pages/settings/index' });
        },
      });
      return;
    }

    if (this.data.fundPickLoading) return;

    this.setData({ fundPickLoading: true, fundPickProgress: '正在分析板块资金流向...' });

    const progressMsgs = [
      { t: 3000, msg: '筛选增长最强的板块...' },
      { t: 8000, msg: '获取板块内TOP基金和个股...' },
      { t: 15000, msg: 'AI基金经理正在深度分析...' },
      { t: 30000, msg: '正在生成投资策略建议...' },
      { t: 60000, msg: '分析即将完成，请稍候...' },
    ];
    const progressTimers = progressMsgs.map(p =>
      setTimeout(() => {
        if (this.data.fundPickLoading) this.setData({ fundPickProgress: p.msg });
      }, p.t)
    );
    const clearProgress = () => progressTimers.forEach(t => clearTimeout(t));

    try {
      // 获取板块资金流向
      let sectorFlows = this.data.sectorFlows;
      if (!sectorFlows || sectorFlows.length === 0) {
        sectorFlows = await fetchSectorFlows();
      }

      // 选取涨幅+资金流入最强的 5 个板块
      const scoredSectors = sectorFlows.map(s => ({
        ...s,
        score: (s.pct || 0) * 2 + (s.mainNet > 0 ? Math.log10(Math.max(s.mainNet, 1)) : -1),
      }));
      scoredSectors.sort((a, b) => b.score - a.score);
      const topSectors = scoredSectors.slice(0, 5);

      this.setData({ fundPickProgress: '获取板块内TOP基金...' });

      // 并行获取各板块TOP基金和个股
      const fundTasks = topSectors.map(s =>
        fetchSectorTopFunds(s.name, 4).then(funds => ({ sector: s.name, code: s.code, funds }))
      );
      const stockTasks = topSectors.map(s =>
        fetchSectorTopStocks(s.code, 4).then(stocks => ({ sector: s.name, stocks }))
      );

      const [fundResults, stockResults] = await Promise.all([
        Promise.allSettled(fundTasks),
        Promise.allSettled(stockTasks),
      ]);

      const topSectorFunds = fundResults
        .filter(r => r.status === 'fulfilled')
        .map(r => r.value);
      const topSectorStocks = stockResults
        .filter(r => r.status === 'fulfilled')
        .map(r => r.value);

      this.setData({ fundPickProgress: 'AI基金经理正在深度分析...' });

      // 调用AI分析
      const result = await runFundPickAI({
        sectorFlows,
        topSectorFunds,
        topSectorStocks,
        indices: this.data.indices,
        commodities: this.data.commodities,
        hotEvents: this.data.topEvents,
        heatmap: this.data.heatmap,
      });

      clearProgress();
      this.setData({
        fundPickLoading: false,
        fundPickProgress: '',
        fundPickResult: result,
        fundPickTime: new Date().toLocaleString(),
      });

      const fundCount = (result.fundPicks || []).length;
      const stockCount = (result.stockPicks || []).length;
      wx.showToast({ title: `精选 ${fundCount}基金+${stockCount}股票`, icon: 'success' });
    } catch (e) {
      clearProgress();
      this.setData({ fundPickLoading: false, fundPickProgress: '' });
      wx.showModal({
        title: 'AI选股失败',
        content: e.message || '未知错误',
        showCancel: false,
      });
    }
  },

  togglePickDetail(e) {
    const idx = e.currentTarget.dataset.idx;
    const listType = e.currentTarget.dataset.list || 'fundPicks'; // 'fundPicks' or 'stockPicks'
    const key = `fundPickResult.${listType}[${idx}].showDetail`;
    const list = this.data.fundPickResult && this.data.fundPickResult[listType];
    const current = list && list[idx];
    this.setData({ [key]: !(current && current.showDetail) });
  },

  addPickToHoldings(e) {
    const { code, name, type } = e.currentTarget.dataset;
    const { getHoldings: getH, setHoldings } = require('../../utils/storage');
    const holdings = getH();
    if (holdings.find(h => h.code === code)) {
      wx.showToast({ title: '已在持仓中', icon: 'none' });
      return;
    }
    holdings.push({ code, name, type: type || '其他' });
    setHoldings(holdings);
    wx.showToast({ title: '已添加到持仓', icon: 'success' });
    this.loadAll();
  },

  // ====== AI 分析 ======
  _loadAICache() {
    const cfg = getAIConfig();
    this.setData({ aiHasKey: !!cfg.key });
    const cached = getCachedAIResult();
    if (cached && cached.result) {
      const formatted = this._formatAIResult(cached.result);
      this.setData({
        aiResult: formatted,
        aiTime: (cached.timestamp || '').replace('T', ' ').slice(0, 16),
        aiRecommendations: formatted.recommendations || [],
        aiMarketOutlook: cached.result.marketOutlook || '',
        aiSectorRotation: cached.result.sectorRotation || '',
        aiMarketTemp: cached.result.marketTemperature || 50,
      });
    }
  },

  toggleAI() {
    this.setData({ showAI: !this.data.showAI });
  },

  async triggerAI() {
    const cfg = getAIConfig();
    if (!cfg.key) {
      wx.showModal({
        title: '未配置 API Key',
        content: '请先在“设置”页配置 AI API Key',
        confirmText: '去设置',
        success: (res) => {
          if (res.confirm) wx.switchTab({ url: '/pages/settings/index' });
        },
      });
      return;
    }

    if (this.data.aiLoading) return; // 防止重复点击

    this.setData({ aiLoading: true, aiProgress: '正在获取持仓数据...' });

    // 进度提示定时器
    const progressMsgs = [
      { t: 3000, msg: '正在获取基金估值与历史数据...' },
      { t: 8000, msg: 'AI 正在深度分析持仓基金...' },
      { t: 30000, msg: 'AI 分析中，大约还需30秒...' },
      { t: 60000, msg: 'AI 仍在分析中，请耐心等待...' },
      { t: 120000, msg: 'AI 分析耗时较长，即将完成...' },
    ];
    const progressTimers = progressMsgs.map(p =>
      setTimeout(() => {
        if (this.data.aiLoading) this.setData({ aiProgress: p.msg });
      }, p.t)
    );
    const clearProgress = () => progressTimers.forEach(t => clearTimeout(t));

    try {
      const holdings = getHoldings();
      const codes = holdings.map(h => h.code);
      const [estimates, historyMap] = await Promise.all([
        require('../../utils/api').fetchMultiFundEstimates(codes),
        require('../../utils/api').fetchMultiFundHistory(codes),
      ]);

      const app = getApp();
      const result = await runAIAnalysis({
        holdings,
        estimates,
        historyMap,
        indices: this.data.indices,
        commodities: this.data.commodities,
        heatmap: this.data.heatmap,
        hotEvents: this.data.topEvents,
        fundDB: app.globalData.FUND_DB,
      });

      clearProgress();
      const formatted = this._formatAIResult(result);
      this.setData({
        aiResult: formatted,
        aiTime: new Date().toLocaleString(),
        aiLoading: false,
        aiProgress: '',
        showAI: true,
        aiRecommendations: formatted.recommendations || [],
        aiMarketOutlook: result.marketOutlook || '',
        aiSectorRotation: result.sectorRotation || '',
        aiMarketTemp: result.marketTemperature || 50,
      });
      this._mergeAIIntoPlans();
      const sigCount = (formatted.signals || []).length;
      const recoCount = (formatted.recommendations || []).length;
      wx.showModal({
        title: '✅ AI 分析完成',
        content: '已分析 ' + sigCount + ' 只持仓基金' + (recoCount > 0 ? '，并推荐 ' + recoCount + ' 只潜力基金' : '') + '。\n\n结果已自动合并到每只基金的行动方案中，可展开查看「AI 加减仓方案」。',
        showCancel: false,
        confirmText: '知道了',
      });
    } catch (e) {
      clearProgress();
      this.setData({ aiLoading: false, aiProgress: '' });
      const msg = e.message || '未知错误';
      let content = msg;
      if (msg.includes('timeout') || msg.includes('超时')) {
        content = 'AI分析超时（已自动重试1次）\n\n可能原因：\n1. 持仓基金数量较多，AI生成耗时长\n2. 网络不稳定或AI服务器繁忙\n\n建议：稍后重试';
      } else if (msg.includes('截断') || msg.includes('过长')) {
        content = msg + '\n\n提示：持仓基金数量较多时，AI输出可能超长被截断。可尝试：\n1. 重新运行（通常第二次会成功）\n2. 在设置中切换更强的AI模型';
      } else if (msg.includes('无法解析') || msg.includes('格式')) {
        content = msg + '\n\n可尝试：重新运行AI分析，或切换AI模型';
      }
      wx.showModal({ title: 'AI 分析失败', content });
    }
  },

  _formatAIResult(r) {
    if (!r) return null;
    const signals = (r.signals || []).map(s => ({
      ...s,
      actionLabel: s.action === 'buy' ? '🟢 买入' : s.action === 'sell' ? '🔴 卖出' : '🟡 持有',
      actionClass: s.action === 'buy' ? 'buy' : s.action === 'sell' ? 'sell' : 'hold',
      confidenceStr: (s.confidence || 0) + '%',
      hasAnalysis: !!(s.analysis),
      showDetail: false,
    }));
    const recommendations = (r.recommendations || []).map(rec => ({
      ...rec,
      actionLabel: rec.action === 'strong_buy' ? '🔥 强烈推荐' : '🟢 推荐买入',
      actionClass: rec.action === 'strong_buy' ? 'strong-buy' : 'buy',
      confidenceStr: (rec.confidence || 0) + '%',
      showDetail: false,
    }));
    return {
      marketSummary: r.marketSummary || '',
      marketOutlook: r.marketOutlook || '',
      sectorRotation: r.sectorRotation || '',
      marketTemperature: r.marketTemperature || 50,
      riskLevel: r.riskLevel || '中风险',
      riskClass: (r.riskLevel || '').includes('高') ? 'high' : (r.riskLevel || '').includes('低') ? 'low' : 'mid',
      overallAdvice: r.overallAdvice || '',
      signals,
      recommendations,
    };
  },

  _mergeAIIntoPlans() {
    const ai = this.data.aiResult;
    if (!ai || !ai.signals || !ai.signals.length) return;
    const plans = this.data.plans;
    if (!plans || !plans.length) return;

    const signalMap = {};
    ai.signals.forEach(s => { signalMap[s.code] = s; });

    const updates = {};
    plans.forEach((p, i) => {
      const sig = signalMap[p.code];
      if (sig) {
        updates[`plans[${i}].aiSignal`] = sig;
        // 生成综合加减仓方案
        updates[`plans[${i}].aiPlan`] = this._buildAIPlan(p, sig);
      }
    });
    this.setData(updates);
  },

  _buildAIPlan(plan, sig) {
    // 算法建议
    const algoAction = plan.action; // buy/sell/hold
    const aiAction = sig.action;    // buy/sell/hold
    const autoFilled = !!sig._autoFilled;

    // 综合判定
    let finalAction, finalLabel, posAdj, reason;
    if (algoAction === aiAction) {
      // 算法与AI一致 → 高置信度
      if (aiAction === 'buy') {
        finalAction = 'buy'; finalLabel = '✅ 加仓';
        posAdj = sig.urgency === '高' ? '加仓 15-20%' : sig.urgency === '中' ? '加仓 10-15%' : '加仓 5-10%';
        reason = '算法+AI共识看多';
      } else if (aiAction === 'sell') {
        finalAction = 'sell'; finalLabel = '✅ 减仓';
        posAdj = sig.urgency === '高' ? '减仓 20-30%' : sig.urgency === '中' ? '减仓 10-20%' : '减仓 5-10%';
        reason = '算法+AI共识看空';
      } else {
        finalAction = 'hold'; finalLabel = '✅ 持有观望';
        posAdj = '维持现有仓位';
        reason = '算法+AI共识观望';
      }
    } else if ((algoAction === 'buy' && aiAction === 'hold') || (algoAction === 'hold' && aiAction === 'buy')) {
      finalAction = 'buy'; finalLabel = '🟡 小幅加仓';
      posAdj = '加仓 5-8%';
      reason = '信号偏多但未完全一致';
    } else if ((algoAction === 'sell' && aiAction === 'hold') || (algoAction === 'hold' && aiAction === 'sell')) {
      finalAction = 'sell'; finalLabel = '🟡 小幅减仓';
      posAdj = '减仓 5-10%';
      reason = '信号偏空但未完全一致';
    } else {
      // 完全冲突 buy vs sell
      finalAction = 'hold'; finalLabel = '⚠️ 信号冲突';
      posAdj = '暂不操作，等待信号明确';
      reason = '算法与AI判断相反，以稳为主';
    }

    return {
      finalAction,
      finalLabel,
      posAdj,
      reason,
      autoFilled,
      algoLabel: algoAction === 'buy' ? '加仓' : algoAction === 'sell' ? '减仓' : '持有',
      aiLabel: sig.actionLabel,
      aiConfidence: sig.confidenceStr,
      aiReason: sig.reason || '',
      aiUrgency: sig.urgency || '中',
    };
  },

  // 展开/收起AI信号详情
  toggleSignalDetail(e) {
    const idx = e.currentTarget.dataset.idx;
    const key = `aiResult.signals[${idx}].showDetail`;
    this.setData({ [key]: !this.data.aiResult.signals[idx].showDetail });
  },

  /**
   * 用真实行情数据修正热力图趋势方向
   * 优先使用服务端已附加的 real_pct，其次用客户端商品/ETF数据
   */
  _fixHeatmapTrends(heatmap, commodities, indices) {
    // 建立 tag → 实际涨跌幅 的映射（客户端数据作为兜底）
    const realPct = {};

    // 从大宗商品提取涨跌
    const commMap = {
      '金': '黄金', '银': '白银', '铜': '有色金属', '铝': '有色金属',
      '锌': '有色金属', '镍': '有色金属', '油': '原油', '燃': '原油',
      '钢': '基建', '铁': '基建',
    };
    (commodities || []).forEach(c => {
      const tag = commMap[c.short];
      if (tag && c.pct != null) {
        if (!realPct[tag] || Math.abs(c.pct) > Math.abs(realPct[tag])) {
          realPct[tag] = c.pct;
        }
      }
    });

    // 从指数ETF提取涨跌
    const idxMap = {
      '黄金ETF': '黄金', '白银LOF': '白银', '有色金属': '有色金属',
      '油气ETF': '原油',
    };
    (indices || []).forEach(idx => {
      const tag = idxMap[idx.name];
      if (tag && idx.pct != null) {
        realPct[tag] = idx.pct;
      }
    });

    // 贵金属联动
    if (realPct['黄金'] && !realPct['贵金属']) {
      realPct['贵金属'] = realPct['黄金'];
    }

    // 修正heatmap trend
    return heatmap.map(h => {
      // 优先使用服务端已有的 real_pct
      let pct = h.real_pct != null ? h.real_pct : realPct[h.tag];
      if (pct != null) {
        const trend = pct > 0.5 ? 'up' : (pct < -0.5 ? 'down' : 'stable');
        let temp = h.temperature;
        if (Math.abs(pct) >= 2) temp = Math.min(100, Math.max(temp, 75));
        else if (Math.abs(pct) >= 1) temp = Math.min(100, Math.max(temp, 60));
        return { ...h, trend, temperature: temp, realPct: Math.round(pct * 100) / 100 };
      }
      return { ...h, realPct: null };
    });
  },
});
