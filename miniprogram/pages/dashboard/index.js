const { getHoldings, getSettings } = require('../../utils/storage');
const { fetchHotEvents, fetchIndices, fetchMultiFundEstimates, fetchSectorFlows, fetchMultiFundHistory, fetchCommodities } = require('../../utils/api');
const { buildPlans, buildOverview, MODEL_PORTFOLIO } = require('../../utils/advisor');
const { getMarketStatus, isMarketOpen, formatPct, pctClass, formatTime, isTradingDay, formatMoney } = require('../../utils/market');
const { runAIAnalysis, getCachedAIResult, getAIConfig } = require('../../utils/ai');

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

    // AI 推荐基金
    showRecommendations: true,
    aiRecommendations: [],
    aiMarketOutlook: '',
    aiSectorRotation: '',
    aiMarketTemp: 50,

    // 调试
    debugError: '',
  },

  onLoad() {
    try { this.updateMarketStatus(); } catch(e) { console.error('updateMarketStatus error:', e); this.setData({ debugError: 'updateMarketStatus: ' + (e.message || e) }); }
    this.loadAll().catch(e => { console.error('loadAll error:', e); this.setData({ loading: false, debugError: 'loadAll: ' + (e.message || e) }); });
    try { this._loadAICache(); } catch(e) { console.error('_loadAICache error:', e); this.setData({ debugError: '_loadAICache: ' + (e.message || e) }); }
  },

  onShow() {
    try { this.updateMarketStatus(); } catch(e) { console.error('updateMarketStatus error:', e); }
    this.loadAll().catch(e => { console.error('loadAll error:', e); this.setData({ loading: false, debugError: 'loadAll(onShow): ' + (e.message || e) }); });
    try { this._loadAICache(); } catch(e) { console.error('_loadAICache error:', e); }
    const timer = setInterval(() => {
      try {
        this.updateMarketStatus();
        if (isMarketOpen()) this.refreshQuotes().catch(e => console.error('refreshQuotes error:', e));
      } catch(e) { console.error('timer error:', e); }
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
    try {
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
    // 用真实行情数据修正热力图趋势方向
    const heatmap = this._fixHeatmapTrends((hotData.data.heatmap || []).slice(0, 14), commodities, indices);
    const allEvents = (hotData.data.events || []).map(item => {
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
        id: item.id, title: item.title, advice: item.advice || '保持观察',
        reason: cleanReason,
        analystView,
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
      };
    });
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

    // 合并缓存的 AI 信号到每只基金卡片
    this._mergeAIIntoPlans();
    } catch (e) {
      console.error('[loadAll] error:', e);
      this.setData({ loading: false, debugError: 'loadAll内部: ' + (e.message || String(e)) });
    }
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

    this.setData({ aiLoading: true });

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

      const formatted = this._formatAIResult(result);
      this.setData({
        aiResult: formatted,
        aiTime: new Date().toLocaleString(),
        aiLoading: false,
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
      this.setData({ aiLoading: false });
      const msg = e.message || '未知错误';
      let content = msg;
      if (msg.includes('timeout') || msg.includes('超时')) {
        content = 'AI分析超时，可能原因：\n1. 网络不稳定\n2. AI服务器繁忙\n\n建议：稍后重试，或在设置中切换AI模型';
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

  // 展开/收起AI推荐
  toggleRecommendations() {
    this.setData({ showRecommendations: !this.data.showRecommendations });
  },

  // 展开/收起AI信号详情
  toggleSignalDetail(e) {
    const idx = e.currentTarget.dataset.idx;
    const key = `aiResult.signals[${idx}].showDetail`;
    this.setData({ [key]: !this.data.aiResult.signals[idx].showDetail });
  },

  // 展开/收起推荐基金详情
  toggleRecoDetail(e) {
    const idx = e.currentTarget.dataset.idx;
    const key = `aiRecommendations[${idx}].showDetail`;
    this.setData({ [key]: !this.data.aiRecommendations[idx].showDetail });
  },

  // 添加推荐基金到持仓
  addRecoToHoldings(e) {
    const { code, name, type } = e.currentTarget.dataset;
    const { getHoldings: getH, setHoldings } = require('../../utils/storage');
    const holdings = getH();
    if (holdings.find(h => h.code === code)) {
      wx.showToast({ title: '已在持仓中', icon: 'none' });
      return;
    }
    holdings.push({ code, name, type });
    setHoldings(holdings);
    wx.showToast({ title: '已添加到持仓', icon: 'success' });
    this.loadAll();
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
