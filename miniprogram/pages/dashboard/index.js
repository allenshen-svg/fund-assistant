const { getHoldings, getSettings } = require('../../utils/storage');
const { fetchHotEvents, fetchIndices, fetchMultiFundEstimates } = require('../../utils/api');
const { buildPlans, buildOverview } = require('../../utils/advisor');
const { getMarketStatus, isMarketOpen, formatPct, pctClass, formatTime, isTradingDay } = require('../../utils/market');

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

    // 热点事件
    topEvents: [],
    heatmap: [],

    // 数据源
    sourceLabel: '加载中',
    updatedAt: '--',
    loading: true,

    // 定时器
    _timer: null,
  },

  onLoad() {
    this.updateMarketStatus();
    this.loadAll();
  },

  onShow() {
    this.updateMarketStatus();
    this.loadAll();
    // 每30秒刷新一次
    const timer = setInterval(() => {
      this.updateMarketStatus();
      if (isMarketOpen()) {
        this.refreshQuotes();
      }
    }, 30000);
    this.setData({ _timer: timer });
  },

  onHide() {
    if (this.data._timer) {
      clearInterval(this.data._timer);
      this.setData({ _timer: null });
    }
  },

  onUnload() {
    if (this.data._timer) {
      clearInterval(this.data._timer);
    }
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
      marketStatus: ms.text,
      marketDot: dot,
      currentTime: formatTime(),
      tradingDay: isTradingDay(),
    });
  },

  async loadAll() {
    this.setData({ loading: true });
    const holdings = getHoldings();
    const settings = getSettings();
    const app = getApp();

    // 并行获取: 指数行情 + 热点事件 + 基金估值
    const [indicesData, hotResult, estimates] = await Promise.allSettled([
      fetchIndices(app.globalData.INDICES),
      fetchHotEvents(settings),
      fetchMultiFundEstimates(holdings.map(h => h.code)),
    ]);

    // 指数行情
    const rawIndices = indicesData.status === 'fulfilled' ? indicesData.value : [];
    const indices = rawIndices.map(idx => ({
      ...idx,
      pctStr: formatPct(idx.pct),
      pctClass: pctClass(idx.pct),
    }));

    // 热点事件
    const hotData = hotResult.status === 'fulfilled' ? hotResult.value : { source: 'local', data: { heatmap: [], events: [] } };
    const heatmap = (hotData.data.heatmap || []).slice(0, 12);
    const topEvents = (hotData.data.events || []).slice(0, 5).map(item => ({
      id: item.id,
      title: item.title,
      advice: item.advice || '保持观察',
      impact: Number(item.impact || 0),
      impactClass: Number(item.impact || 0) >= 0 ? 'up' : 'down',
    }));

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
    let totalPct = '--';
    let totalPctClass = 'flat';
    if (validPcts.length > 0) {
      const avg = validPcts.reduce((s, h) => s + h.pct, 0) / validPcts.length;
      totalPct = formatPct(avg);
      totalPctClass = pctClass(avg);
    }

    // 行动指南
    const plans = buildPlans(holdings, hotData.data.heatmap || []);
    const overview = buildOverview(plans);

    this.setData({
      indices,
      holdings: holdingsWithEst,
      totalPct,
      totalPctClass,
      overview,
      plans: plans.slice(0, 6),
      topEvents,
      heatmap,
      sourceLabel: hotData.source === 'remote' ? '远程数据' : '本地回退',
      updatedAt: String(hotData.data.updated_at || '--').replace('T', ' ').slice(0, 16),
      loading: false,
    });
  },

  async refreshQuotes() {
    const app = getApp();
    const holdings = getHoldings();

    const [indicesData, estimates] = await Promise.allSettled([
      fetchIndices(app.globalData.INDICES),
      fetchMultiFundEstimates(holdings.map(h => h.code)),
    ]);

    const rawIndices = indicesData.status === 'fulfilled' ? indicesData.value : [];
    const indices = rawIndices.map(idx => ({
      ...idx,
      pctStr: formatPct(idx.pct),
      pctClass: pctClass(idx.pct),
    }));

    const estData = estimates.status === 'fulfilled' ? estimates.value : {};
    const holdingsWithEst = holdings.map(h => {
      const est = estData[h.code];
      return {
        ...h,
        pct: est ? est.pct : null,
        pctStr: est ? formatPct(est.pct) : '待开盘',
        pctClass: est ? pctClass(est.pct) : 'flat',
        estimate: est ? est.estimate : null,
      };
    });

    const validPcts = holdingsWithEst.filter(h => h.pct !== null);
    let totalPct = '--';
    let totalPctClass = 'flat';
    if (validPcts.length > 0) {
      const avg = validPcts.reduce((s, h) => s + h.pct, 0) / validPcts.length;
      totalPct = formatPct(avg);
      totalPctClass = pctClass(avg);
    }

    this.setData({
      indices,
      holdings: holdingsWithEst,
      totalPct,
      totalPctClass,
      currentTime: formatTime(),
    });
  },

  goHoldings() {
    wx.switchTab({ url: '/pages/holdings/index' });
  },

  goSentiment() {
    wx.switchTab({ url: '/pages/sentiment/index' });
  },

  goSettings() {
    wx.switchTab({ url: '/pages/settings/index' });
  },
});
