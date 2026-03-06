const { getSimPortfolio, setSimPortfolio, getSimTradeLog, addSimTradeLog, getSimWeeklyReviews, addSimWeeklyReview, getSettings, getSimSettleLog, addSimSettleRecord } = require('../../utils/storage');
const { fetchIndices, fetchCommodities, fetchHotEvents, fetchSectorFlows, fetchMultiFundEstimates, fetchFundEstimate } = require('../../utils/api');
const { getCachedFundPick, runSimSellAdvice, runSimWeeklyReview, getAIConfig } = require('../../utils/ai');
const { formatPct, pctClass, todayStr, isTradingDay, formatMoney } = require('../../utils/market');

const app = getApp();

Page({
  data: {
    /* ====== 组合概览 ====== */
    portfolio: null,
    cash: 100000,
    totalValue: 100000,
    totalCash: 100000,
    totalReturn: '0.00%',
    totalReturnClass: 'flat',
    positionList: [],       // 带实时估值的持仓列表

    /* ====== 买入面板 ====== */
    showBuyPanel: false,
    buyTab: 'fund',         // 'fund' | 'stock'
    fundPicks: [],
    stockPicks: [],
    buyPickIdx: -1,         // 选中的推荐标的序号
    buyAmount: '',          // 输入的买入金额
    buyTarget: null,        // 选中的标的对象 { code, name, sector }
    pickTime: '',           // 推荐时间

    /* ====== 卖出面板 ====== */
    showSellPanel: false,
    sellLoading: false,
    sellAdvice: null,       // AI减仓建议
    sellProgress: '',

    /* ====== 交易记录 ====== */
    secLog: false,
    tradeLog: [],

    /* ====== 周复盘 ====== */
    secReview: false,
    reviewLoading: false,
    weeklyReviews: [],
    latestReview: null,

    /* ====== 状态 ====== */
    loading: false,
    refreshing: false,

    /* ====== 结算 ====== */
    settleDate: '',         // 最后结算日
    settling: false,        // 结算中
    dailyPnl: '',           // 今日盈亏额
    dailyPnlClass: 'flat',
    dailyPct: '',           // 今日涨跌幅
    settleLog: [],          // 近30天结算记录
    secSettle: false,       // 结算记录展开
  },

  onShow() {
    this._loadPortfolio();
    this._autoSettle();
  },

  onPullDownRefresh() {
    this._refreshPositions().finally(() => wx.stopPullDownRefresh());
  },

  /* ================= 加载组合 ================= */
  _loadPortfolio() {
    const portfolio = getSimPortfolio();
    const tradeLog = getSimTradeLog();
    const reviews = getSimWeeklyReviews();
    const settleLog = getSimSettleLog();

    this.setData({
      portfolio,
      cash: portfolio.cash,
      totalCash: portfolio.totalCash,
      tradeLog: tradeLog.slice(0, 50),
      weeklyReviews: reviews,
      latestReview: reviews.length > 0 ? reviews[0] : null,
      settleDate: portfolio.lastSettleDate || '',
      settleLog: settleLog.slice(0, 30),
    });

    this._refreshPositions();
    this._loadPicks();
  },

  /* ================= 刷新持仓估值 ================= */
  async _refreshPositions() {
    const portfolio = getSimPortfolio();
    const positions = portfolio.positions || [];
    if (positions.length === 0) {
      this.setData({
        positionList: [],
        totalValue: portfolio.cash,
        totalReturn: '0.00%',
        totalReturnClass: 'flat',
      });
      return;
    }

    this.setData({ refreshing: true });

    try {
      // 获取基金类持仓的估值（包括确认净值 nav 和实时估值 estimate）
      const allCodes = positions.map(p => p.code);
      let fundData = {};
      if (allCodes.length > 0) {
        const results = await fetchMultiFundEstimates(allCodes);
        fundData = results;
      }

      // 构建持仓列表
      let positionValue = 0;
      const positionList = positions.map(pos => {
        const est = fundData[pos.code];
        // 优先用实时估值，其次用确认净值，再次用最后结算价
        const currentNav = est ? (est.estimate || est.nav) : (pos.lastNav || pos.costPrice);
        const confirmedNav = est ? est.nav : (pos.lastNav || pos.costPrice);
        const currentValue = pos.shares * currentNav;
        const profit = currentValue - pos.costTotal;
        const profitPct = pos.costTotal > 0 ? (profit / pos.costTotal * 100) : 0;
        positionValue += currentValue;

        return {
          ...pos,
          currentPrice: currentNav.toFixed(4),
          confirmedNav: confirmedNav.toFixed(4),
          currentValue: currentValue.toFixed(2),
          profit: profit.toFixed(2),
          profitPct: profitPct.toFixed(2) + '%',
          profitClass: profit >= 0 ? 'up' : 'down',
          pctNum: est ? est.pct : 0,
          pctStr: est ? formatPct(est.pct) : '--',
          pctClass: est ? pctClass(est.pct) : 'flat',
        };
      });

      const totalValue = portfolio.cash + positionValue;
      const totalReturn = ((totalValue - portfolio.totalCash) / portfolio.totalCash * 100).toFixed(2);

      this.setData({
        positionList,
        totalValue: totalValue.toFixed(2),
        totalReturn: totalReturn + '%',
        totalReturnClass: totalReturn >= 0 ? (totalReturn > 0 ? 'up' : 'flat') : 'down',
        refreshing: false,
      });
    } catch (e) {
      console.error('刷新持仓估值失败', e);
      this.setData({ refreshing: false });
    }
  },

  /* ================= 每日结算 ================= */
  /**
   * 自动结算：进入页面时检查是否需要结算
   * - 如果今天是交易日且尚未结算，自动执行结算
   * - 结算使用每只基金/股票的确认净值(nav/dwjz)
   */
  async _autoSettle() {
    const portfolio = getSimPortfolio();
    const today = todayStr();
    const positions = portfolio.positions || [];

    // 无持仓 或 今天已结算过 -> 跳过
    if (positions.length === 0) return;
    if (portfolio.lastSettleDate === today) {
      this._loadSettleDisplay();
      return;
    }

    // 非交易日不自动结算（但可以手动触发）
    if (!isTradingDay(today)) {
      this._loadSettleDisplay();
      return;
    }

    await this._doSettle();
  },

  /**
   * 手动触发结算
   */
  async manualSettle() {
    if (this.data.settling) return;
    const portfolio = getSimPortfolio();
    if ((portfolio.positions || []).length === 0) {
      wx.showToast({ title: '暂无持仓', icon: 'none' });
      return;
    }
    await this._doSettle();
  },

  /**
   * 执行结算逻辑
   * - 获取每只持仓的确认净值
   * - 按净值计算每只持仓市值
   * - 记录结算快照（每日净值曲线）
   * - 更新持仓的 lastNav 字段
   */
  async _doSettle() {
    this.setData({ settling: true });
    const portfolio = getSimPortfolio();
    const positions = portfolio.positions || [];
    const today = todayStr();

    // 获取上一次结算的总市值，用于计算日收益
    const settleLog = getSimSettleLog();
    const lastSettle = settleLog.length > 0 ? settleLog[0] : null;
    const prevTotalValue = lastSettle ? lastSettle.totalValue : portfolio.totalCash;

    try {
      // 并行获取所有持仓净值
      const tasks = positions.map(pos => fetchFundEstimate(pos.code));
      const results = await Promise.allSettled(tasks);

      let positionValue = 0;
      const settlePositions = [];

      positions.forEach((pos, i) => {
        const est = results[i].status === 'fulfilled' ? results[i].value : null;
        // 优先用确认净值(dwjz)，其次用估值(gsz)，再次用上次结算净值
        const settleNav = est ? (est.nav || est.estimate || pos.lastNav || pos.costPrice)
                              : (pos.lastNav || pos.costPrice);
        const value = pos.shares * settleNav;
        positionValue += value;

        // 更新持仓的最新净值
        pos.lastNav = settleNav;
        pos.lastNavDate = today;

        settlePositions.push({
          code: pos.code,
          name: pos.name,
          nav: settleNav,
          shares: pos.shares,
          value: parseFloat(value.toFixed(2)),
        });
      });

      const totalValue = portfolio.cash + positionValue;
      const dailyPnl = totalValue - prevTotalValue;
      const dailyPct = prevTotalValue > 0 ? ((dailyPnl / prevTotalValue) * 100) : 0;

      // 保存结算记录
      addSimSettleRecord({
        date: today,
        totalValue: parseFloat(totalValue.toFixed(2)),
        cash: portfolio.cash,
        positionValue: parseFloat(positionValue.toFixed(2)),
        dailyPnl: parseFloat(dailyPnl.toFixed(2)),
        dailyPct: parseFloat(dailyPct.toFixed(2)),
        positions: settlePositions,
      });

      // 更新组合状态
      portfolio.lastSettleDate = today;
      portfolio.lastSettleValue = parseFloat(totalValue.toFixed(2));
      setSimPortfolio(portfolio);

      this.setData({ settling: false });
      this._loadPortfolio();
      wx.showToast({ title: `结算完成 ${dailyPnl >= 0 ? '+' : ''}¥${dailyPnl.toFixed(0)}`, icon: 'none' });
    } catch (e) {
      console.error('结算失败', e);
      this.setData({ settling: false });
      wx.showToast({ title: '结算失败', icon: 'none' });
    }
  },

  /**
   * 加载结算显示数据
   */
  _loadSettleDisplay() {
    const settleLog = getSimSettleLog();
    const latest = settleLog.length > 0 ? settleLog[0] : null;
    if (latest) {
      this.setData({
        dailyPnl: (latest.dailyPnl >= 0 ? '+' : '') + latest.dailyPnl.toFixed(2),
        dailyPnlClass: latest.dailyPnl >= 0 ? (latest.dailyPnl > 0 ? 'up' : 'flat') : 'down',
        dailyPct: (latest.dailyPct >= 0 ? '+' : '') + latest.dailyPct.toFixed(2) + '%',
        settleLog: settleLog.slice(0, 30),
      });
    }
  },

  /* ================= 加载推荐缓存 ================= */
  _loadPicks() {
    const cached = getCachedFundPick();
    if (cached && cached.result) {
      const result = cached.result;
      this.setData({
        fundPicks: (result.fundPicks || []).map((p, i) => ({ ...p, _idx: i })),
        stockPicks: (result.stockPicks || []).map((p, i) => ({ ...p, _idx: i })),
        pickTime: (cached.timestamp || '').replace('T', ' ').slice(0, 16),
      });
    }
  },

  /* ================= 买入面板 ================= */
  openBuyPanel() {
    this._loadPicks();
    if (this.data.fundPicks.length === 0 && this.data.stockPicks.length === 0) {
      wx.showModal({
        title: '暂无推荐',
        content: '请先在首页"选基金/股票"模块获取 AI 推荐',
        confirmText: '去首页',
        success: (res) => {
          if (res.confirm) wx.switchTab({ url: '/pages/dashboard/index' });
        },
      });
      return;
    }
    this.setData({
      showBuyPanel: true,
      buyTab: 'fund',
      buyPickIdx: -1,
      buyAmount: '',
      buyTarget: null,
    });
  },

  closeBuyPanel() {
    this.setData({ showBuyPanel: false });
  },

  switchBuyTab(e) {
    const tab = e.currentTarget.dataset.tab;
    this.setData({ buyTab: tab, buyPickIdx: -1, buyTarget: null, buyAmount: '' });
  },

  selectBuyPick(e) {
    const idx = e.currentTarget.dataset.idx;
    const listKey = this.data.buyTab === 'fund' ? 'fundPicks' : 'stockPicks';
    const list = this.data[listKey];
    const pick = list[idx];
    if (!pick) return;
    this.setData({
      buyPickIdx: idx,
      buyTarget: {
        code: pick.code,
        name: pick.name,
        sector: pick.sector || '',
        type: this.data.buyTab,
        reason: pick.reason || '',
      },
    });
  },

  onBuyAmountInput(e) {
    this.setData({ buyAmount: e.detail.value });
  },

  quickBuyAmount(e) {
    const amount = e.currentTarget.dataset.amount;
    this.setData({ buyAmount: String(amount) });
  },

  confirmBuy() {
    const { buyTarget, buyAmount, cash } = this.data;
    if (!buyTarget) {
      wx.showToast({ title: '请先选择标的', icon: 'none' }); return;
    }
    const amount = parseFloat(buyAmount);
    if (!amount || amount <= 0) {
      wx.showToast({ title: '请输入有效金额', icon: 'none' }); return;
    }
    if (amount > cash) {
      wx.showToast({ title: '余额不足', icon: 'none' }); return;
    }
    if (amount < 100) {
      wx.showToast({ title: '最低买入100元', icon: 'none' }); return;
    }

    const portfolio = getSimPortfolio();
    // 买入时获取实际净值
    const est = await fetchFundEstimate(buyTarget.code);
    // 优先使用确认净值(dwjz)，没有则用估值(gsz)
    const price = est ? (est.nav || est.estimate || 1.0) : 1.0;
    const shares = amount / price;

    // 检查是否已有该持仓, 如有则加仓
    const existIdx = portfolio.positions.findIndex(p => p.code === buyTarget.code);
    if (existIdx >= 0) {
      const old = portfolio.positions[existIdx];
      const newCostTotal = old.costTotal + amount;
      const newShares = old.shares + shares;
      portfolio.positions[existIdx] = {
        ...old,
        shares: newShares,
        costTotal: newCostTotal,
        costPrice: newCostTotal / newShares,
      };
    } else {
      portfolio.positions.push({
        code: buyTarget.code,
        name: buyTarget.name,
        type: buyTarget.type,
        sector: buyTarget.sector,
        shares,
        costPrice: price,
        costTotal: amount,
        buyDate: todayStr(),
      });
    }

    portfolio.cash -= amount;
    setSimPortfolio(portfolio);

    // 添加交易记录
    addSimTradeLog({
      date: todayStr(),
      action: 'buy',
      code: buyTarget.code,
      name: buyTarget.name,
      sector: buyTarget.sector,
      amount,
      price,
      reason: buyTarget.reason,
      aiSource: 'fundPick',
    });

    wx.showToast({ title: `买入 ¥${amount.toFixed(0)}`, icon: 'success' });
    this.setData({ showBuyPanel: false });
    this._loadPortfolio();
  },

  /* ================= 快速加仓已有持仓 ================= */
  addPosition(e) {
    const code = e.currentTarget.dataset.code;
    const pos = (this.data.positionList || []).find(p => p.code === code);
    if (!pos) return;

    wx.showModal({
      title: `加仓 ${pos.name}`,
      content: '请输入加仓金额(元)',
      editable: true,
      placeholderText: '例如: 5000',
      success: (res) => {
        if (!res.confirm || !res.content) return;
        const amount = parseFloat(res.content);
        if (!amount || amount <= 0 || amount > this.data.cash) {
          wx.showToast({ title: amount > this.data.cash ? '余额不足' : '无效金额', icon: 'none' });
          return;
        }

        const portfolio = getSimPortfolio();
        const idx = portfolio.positions.findIndex(p => p.code === code);
        if (idx < 0) return;

        const price = pos.currentPrice ? parseFloat(pos.currentPrice) : portfolio.positions[idx].costPrice;
        const shares = amount / price;

        const old = portfolio.positions[idx];
        portfolio.positions[idx] = {
          ...old,
          shares: old.shares + shares,
          costTotal: old.costTotal + amount,
          costPrice: (old.costTotal + amount) / (old.shares + shares),
        };
        portfolio.cash -= amount;
        setSimPortfolio(portfolio);

        addSimTradeLog({
          date: todayStr(),
          action: 'buy',
          code,
          name: pos.name,
          sector: pos.sector || '',
          amount,
          price,
          reason: '手动加仓',
          aiSource: 'manual',
        });

        wx.showToast({ title: `加仓 ¥${amount.toFixed(0)}`, icon: 'success' });
        this._loadPortfolio();
      },
    });
  },

  /* ================= 卖出/减仓 ================= */
  openSellPanel() {
    if ((this.data.positionList || []).length === 0) {
      wx.showToast({ title: '暂无持仓可卖出', icon: 'none' });
      return;
    }
    this.setData({ showSellPanel: true, sellAdvice: null });
    this._fetchSellAdvice();
  },

  closeSellPanel() {
    this.setData({ showSellPanel: false });
  },

  async _fetchSellAdvice() {
    const cfg = getAIConfig();
    if (!cfg.key) {
      this.setData({
        sellAdvice: { overview: '未配置 AI Key，请在设置页配置后重试', sellAdvice: [], holdAdvice: '', riskAlert: '' },
      });
      return;
    }

    this.setData({ sellLoading: true, sellProgress: '正在分析市场环境...' });
    const progressTimers = [
      setTimeout(() => this.data.sellLoading && this.setData({ sellProgress: '评估持仓风险...' }), 3000),
      setTimeout(() => this.data.sellLoading && this.setData({ sellProgress: 'AI正在生成减仓建议...' }), 8000),
      setTimeout(() => this.data.sellLoading && this.setData({ sellProgress: '即将完成分析...' }), 15000),
    ];

    try {
      const settings = getSettings();
      const [indices, commodities, hotEvents, sectorFlows] = await Promise.all([
        fetchIndices(app.globalData.INDICES),
        fetchCommodities(app.globalData.COMMODITIES || []),
        fetchHotEvents(settings),
        fetchSectorFlows(),
      ]);

      const positions = this.data.positionList.map(p => ({
        code: p.code,
        name: p.name,
        type: p.type,
        sector: p.sector || '',
        costPrice: p.costPrice,
        currentPrice: parseFloat(p.currentPrice),
        profitPct: p.profitPct,
        buyDate: p.buyDate,
      }));

      const result = await runSimSellAdvice({
        positions,
        indices: indices || [],
        commodities: commodities || [],
        hotEvents: (hotEvents && hotEvents.items) ? hotEvents.items.slice(0, 10) : [],
        sectorFlows: sectorFlows || [],
      });

      progressTimers.forEach(t => clearTimeout(t));
      this.setData({ sellLoading: false, sellProgress: '', sellAdvice: result });
    } catch (e) {
      progressTimers.forEach(t => clearTimeout(t));
      this.setData({
        sellLoading: false,
        sellProgress: '',
        sellAdvice: { overview: '分析失败: ' + (e.message || '未知错误'), sellAdvice: [], holdAdvice: '', riskAlert: '' },
      });
    }
  },

  executeSell(e) {
    const code = e.currentTarget.dataset.code;
    const pct = parseFloat(e.currentTarget.dataset.pct) || 100;
    const reason = e.currentTarget.dataset.reason || 'AI减仓建议';

    const portfolio = getSimPortfolio();
    const idx = portfolio.positions.findIndex(p => p.code === code);
    if (idx < 0) {
      wx.showToast({ title: '未找到该持仓', icon: 'none' }); return;
    }

    const pos = portfolio.positions[idx];
    const currentPrice = parseFloat(
      (this.data.positionList.find(p => p.code === code) || {}).currentPrice || pos.costPrice
    );
    const sellShares = pos.shares * (pct / 100);
    const sellAmount = sellShares * currentPrice;

    wx.showModal({
      title: `确认卖出 ${pos.name}`,
      content: `卖出 ${pct}%，约 ¥${sellAmount.toFixed(2)}`,
      success: (res) => {
        if (!res.confirm) return;

        if (pct >= 100) {
          portfolio.positions.splice(idx, 1);
        } else {
          portfolio.positions[idx] = {
            ...pos,
            shares: pos.shares - sellShares,
            costTotal: pos.costTotal * (1 - pct / 100),
          };
        }
        portfolio.cash += sellAmount;
        setSimPortfolio(portfolio);

        addSimTradeLog({
          date: todayStr(),
          action: 'sell',
          code,
          name: pos.name,
          sector: pos.sector || '',
          amount: sellAmount,
          price: currentPrice,
          reason,
          aiSource: 'sellAdvice',
        });

        wx.showToast({ title: `卖出 ¥${sellAmount.toFixed(0)}`, icon: 'success' });
        this.setData({ showSellPanel: false });
        this._loadPortfolio();
      },
    });
  },

  /* ================= 手动卖出 ================= */
  manualSell(e) {
    const code = e.currentTarget.dataset.code;
    const pos = (this.data.positionList || []).find(p => p.code === code);
    if (!pos) return;

    wx.showActionSheet({
      itemList: ['卖出 25%', '卖出 50%', '卖出 75%', '全部卖出'],
      success: (res) => {
        const pctMap = [25, 50, 75, 100];
        const pct = pctMap[res.tapIndex];

        const portfolio = getSimPortfolio();
        const idx = portfolio.positions.findIndex(p => p.code === code);
        if (idx < 0) return;

        const currPos = portfolio.positions[idx];
        const currentPrice = parseFloat(pos.currentPrice) || currPos.costPrice;
        const sellShares = currPos.shares * (pct / 100);
        const sellAmount = sellShares * currentPrice;

        if (pct >= 100) {
          portfolio.positions.splice(idx, 1);
        } else {
          portfolio.positions[idx] = {
            ...currPos,
            shares: currPos.shares - sellShares,
            costTotal: currPos.costTotal * (1 - pct / 100),
          };
        }
        portfolio.cash += sellAmount;
        setSimPortfolio(portfolio);

        addSimTradeLog({
          date: todayStr(),
          action: 'sell',
          code,
          name: pos.name,
          sector: pos.sector || '',
          amount: sellAmount,
          price: currentPrice,
          reason: '手动卖出',
          aiSource: 'manual',
        });

        wx.showToast({ title: `卖出 ¥${sellAmount.toFixed(0)}`, icon: 'success' });
        this._loadPortfolio();
      },
    });
  },

  /* ================= 周复盘 ================= */
  async triggerWeeklyReview() {
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

    if (this.data.reviewLoading) return;
    this.setData({ reviewLoading: true });

    try {
      const portfolio = getSimPortfolio();
      const tradeLog = getSimTradeLog();
      const settings = getSettings();

      // 获取本周交易记录
      const today = new Date();
      const dayOfWeek = today.getDay();
      const weekStart = new Date(today);
      weekStart.setDate(today.getDate() - (dayOfWeek === 0 ? 6 : dayOfWeek - 1));
      const weekStartStr = weekStart.toISOString().slice(0, 10);
      const weekEndStr = today.toISOString().slice(0, 10);

      const weekTrades = tradeLog.filter(t => t.date >= weekStartStr && t.date <= weekEndStr);

      // 获取市场环境
      const [indices, commodities] = await Promise.all([
        fetchIndices(app.globalData.INDICES),
        fetchCommodities(app.globalData.COMMODITIES || []),
      ]);

      const result = await runSimWeeklyReview({
        trades: weekTrades,
        startPortfolio: { cash: portfolio.totalCash, positions: [] },
        endPortfolio: portfolio,
        weekStart: weekStartStr,
        weekEnd: weekEndStr,
        marketContext: {
          indices: indices || [],
          commodities: commodities || [],
        },
      });

      // 保存复盘记录
      addSimWeeklyReview({
        weekStart: weekStartStr,
        weekEnd: weekEndStr,
        startValue: portfolio.totalCash,
        endValue: parseFloat(this.data.totalValue),
        returnPct: this.data.totalReturn,
        aiReview: result,
        trades: weekTrades,
      });

      this.setData({ reviewLoading: false });
      this._loadPortfolio(); // 重新加载以显示新复盘
      wx.showToast({ title: '复盘完成', icon: 'success' });
    } catch (e) {
      this.setData({ reviewLoading: false });
      wx.showModal({
        title: '复盘失败',
        content: e.message || '未知错误',
        showCancel: false,
      });
    }
  },

  /* ================= 折叠/展开 ================= */
  toggleSection(e) {
    const key = e.currentTarget.dataset.key;
    this.setData({ [key]: !this.data[key] });
  },

  toggleReviewDetail(e) {
    const idx = e.currentTarget.dataset.idx;
    const key = `weeklyReviews[${idx}].showDetail`;
    this.setData({ [key]: !this.data.weeklyReviews[idx].showDetail });
  },

  /* ================= 重置模拟仓 ================= */
  resetPortfolio() {
    wx.showModal({
      title: '重置模拟仓',
      content: '确定要重置模拟仓吗？所有持仓和交易记录将被清空，恢复初始10万资金。',
      confirmColor: '#f87171',
      success: (res) => {
        if (res.confirm) {
          setSimPortfolio({
            cash: 100000,
            totalCash: 100000,
            positions: [],
            createdAt: new Date().toISOString(),
          });
          // 清除交易日志
          wx.setStorageSync('fa_sim_trade_log_v1', []);
          wx.setStorageSync('fa_sim_weekly_review_v1', []);
          this._loadPortfolio();
          wx.showToast({ title: '已重置', icon: 'success' });
        }
      },
    });
  },
});
