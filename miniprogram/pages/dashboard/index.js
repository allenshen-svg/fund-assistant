const { getHoldings, getSettings } = require('../../utils/storage');
const { fetchHotEvents, fetchIndices, fetchMultiFundEstimates, fetchSectorFlows, fetchMultiFundHistory, fetchCommodities } = require('../../utils/api');
const { buildPlans, buildOverview, MODEL_PORTFOLIO } = require('../../utils/advisor');
const { getMarketStatus, isMarketOpen, formatPct, pctClass, formatTime, isTradingDay, formatMoney } = require('../../utils/market');
const { runAIAnalysis, getCachedAIResult, getAIConfig } = require('../../utils/ai');

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

    // 热点事件
    topEvents: [],
    heatmap: [],

    // 数据源
    sourceLabel: '加载中',
    updatedAt: '--',
    loading: true,

    // 定时器
    _timer: null,

    // AI 分析
    showAI: false,
    aiLoading: false,
    aiResult: null,
    aiTime: '',
    aiHasKey: false,
  },

  onLoad() {
    this.updateMarketStatus();
    this.loadAll();
    this._loadAICache();
  },

  onShow() {
    this.updateMarketStatus();
    this.loadAll();
    this._loadAICache();
    const timer = setInterval(() => {
      this.updateMarketStatus();
      if (isMarketOpen()) this.refreshQuotes();
    }, 30000);
    this.setData({ _timer: timer });
  },

  onHide() {
    if (this.data._timer) { clearInterval(this.data._timer); this.setData({ _timer: null }); }
  },

  onUnload() {
    if (this.data._timer) clearInterval(this.data._timer);
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
    const holdings = getHoldings();
    const settings = getSettings();
    const app = getApp();
    const codes = holdings.map(h => h.code);

    // 并行获取: 指数 + 商品 + 热点 + 估值 + 历史净值 + 板块资金流
    const [indicesData, commodityData, hotResult, estimates, historyData, sectorData] = await Promise.allSettled([
      fetchIndices(app.globalData.INDICES),
      fetchCommodities(app.globalData.COMMODITIES || []),
      fetchHotEvents(settings),
      fetchMultiFundEstimates(codes),
      fetchMultiFundHistory(codes),
      fetchSectorFlows(),
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
    const heatmap = (hotData.data.heatmap || []).slice(0, 14);
    const allEvents = (hotData.data.events || []).map(item => ({
      id: item.id, title: item.title, advice: item.advice || '保持观察',
      reason: item.reason || '',
      category: item.category || '',
      concepts: item.concepts || [],
      sectorsPos: (item.sectors_positive || []).join('、'),
      sectorsNeg: (item.sectors_negative || []).join('、'),
      impact: Number(item.impact || 0),
      impactClass: Number(item.impact || 0) >= 0 ? 'up' : 'down',
      impactAbs: Math.abs(Number(item.impact || 0)),
      confidence: Number(item.confidence || 0),
      isGeo: item.category === 'geopolitics',
      isCommodity: item.category === 'commodity',
    }));
    const topEvents = allEvents.slice(0, 5);

    // 热点异动: 地缘政治+商品事件 + 商品价格异动
    const breakingEvents = allEvents.filter(e => e.isGeo || e.isCommodity || e.impactAbs >= 10);
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
    }));
    const hotBreaking = [...breakingEvents, ...commodityAnomalies]
      .sort((a, b) => b.impactAbs - a.impactAbs)
      .slice(0, 8);

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
      topEvents, heatmap,
      sourceLabel: hotData.source === 'remote' ? '远程数据' : '本地回退',
      updatedAt: String(hotData.data.updated_at || '--').replace('T', ' ').slice(0, 16),
      loading: false,
    });
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

  // ====== AI 分析 ======
  _loadAICache() {
    const cfg = getAIConfig();
    this.setData({ aiHasKey: !!cfg.key });
    const cached = getCachedAIResult();
    if (cached && cached.result) {
      this.setData({
        aiResult: this._formatAIResult(cached.result),
        aiTime: (cached.timestamp || '').replace('T', ' ').slice(0, 16),
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

    this.setData({ aiLoading: true, showAI: true });
    wx.showLoading({ title: 'AI 分析中...' });

    try {
      const holdings = getHoldings();
      const codes = holdings.map(h => h.code);
      const [estimates, historyMap] = await Promise.all([
        require('../../utils/api').fetchMultiFundEstimates(codes),
        require('../../utils/api').fetchMultiFundHistory(codes),
      ]);

      const result = await runAIAnalysis({
        holdings,
        estimates,
        historyMap,
        indices: this.data.indices,
      });

      this.setData({
        aiResult: this._formatAIResult(result),
        aiTime: new Date().toLocaleString(),
        aiLoading: false,
      });
      wx.hideLoading();
      wx.showToast({ title: 'AI 分析完成', icon: 'success' });
    } catch (e) {
      this.setData({ aiLoading: false });
      wx.hideLoading();
      wx.showModal({ title: 'AI 分析失败', content: e.message || '未知错误' });
    }
  },

  _formatAIResult(r) {
    if (!r) return null;
    const signals = (r.signals || []).map(s => ({
      ...s,
      actionLabel: s.action === 'buy' ? '🟢 买入' : s.action === 'sell' ? '🔴 卖出' : '🟡 持有',
      actionClass: s.action === 'buy' ? 'buy' : s.action === 'sell' ? 'sell' : 'hold',
      confidenceStr: (s.confidence || 0) + '%',
    }));
    return {
      marketSummary: r.marketSummary || '',
      riskLevel: r.riskLevel || '中风险',
      riskClass: (r.riskLevel || '').includes('高') ? 'high' : (r.riskLevel || '').includes('低') ? 'low' : 'mid',
      overallAdvice: r.overallAdvice || '',
      signals,
    };
  },
});
