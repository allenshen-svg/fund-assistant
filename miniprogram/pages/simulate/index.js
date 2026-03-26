const { getSettings } = require('../../utils/storage');
const { fetchServerSimAutoStatus } = require('../../utils/api');

function todayStr() {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function decorateTradeItem(item) {
  const trade = { ...item };
  if (trade.action === 'buy') {
    const multiplier = Number(trade.sizingMultiplier || 0);
    trade.sizingMultiplier = multiplier > 0 ? multiplier.toFixed(2) : '';
    trade.sizingLabel = multiplier > 0 ? `动态仓位系数 x${multiplier.toFixed(2)}` : '';
    trade.sizingNotesText = Array.isArray(trade.sizingNotes) ? trade.sizingNotes.slice(0, 4).join('，') : '';
  } else {
    trade.sizingMultiplier = '';
    trade.sizingLabel = '';
    trade.sizingNotesText = '';
  }
  return trade;
}

Page({
  data: {
    /* ====== 组合概览 ====== */
    portfolio: null,
    cash: 0,
    totalValue: 0,
    totalCash: 0,
    totalReturn: '0.00%',
    totalReturnClass: 'flat',
    positionList: [],       // 带实时估值的持仓列表

    /* ====== 交易记录 ====== */
    secLog: false,
    tradeLog: [],

    /* ====== 周复盘 ====== */
    secReview: false,
    weeklyReviews: [],

    /* ====== 状态 ====== */
    loading: false,
    refreshing: false,
    syncError: '',
    hasRemoteData: false,

    /* ====== 结算 ====== */
    settleDate: '',         // 最后结算日
    settling: false,        // 结算中
    dailyPnl: '',           // 今日盈亏额
    dailyPnlClass: 'flat',
    dailyPct: '',           // 今日涨跌幅
    settleLog: [],          // 近30天结算记录
    secSettle: false,       // 结算记录展开

    /* ====== 收益日历 ====== */
    calYear: 0,
    calMonth: 0,
    calTitle: '',
    calWeeks: [],           // 日历网格 [[{day,date,pnl,pct,isToday,hasData,inMonth}]]
    calSelectedDate: '',    // 选中的日期
    calSelectedDetail: null,// 选中日期的结算详情
    calMonthPnl: '',        // 本月累计盈亏
    calMonthPnlClass: 'flat',

    /* ====== 服务端自动仓 ====== */
    remoteMode: true,
    autoMeta: null,
    weeklyTradeDetails: [],
    weeklyAttribution: null,
    autoConfigSummary: [],
    latestSizingLabel: '',
    latestSizingNotes: '',
  },

  onShow() {
    this._loadSimData();
  },

  onPullDownRefresh() {
    this._loadServerPortfolio().finally(() => wx.stopPullDownRefresh());
  },

  async _loadSimData() {
    this.setData({ loading: true, syncError: '' });
    await this._loadServerPortfolio();
  },

  async _loadServerPortfolio() {
    const settings = getSettings();
    let payload = await fetchServerSimAutoStatus(settings).catch(() => null);
    // 弱网重试一次
    if (!payload) {
      await new Promise(r => setTimeout(r, 1500));
      payload = await fetchServerSimAutoStatus(settings).catch(() => null);
    }
    if (!payload || !payload.portfolio) {
      this.setData({
        loading: false,
        refreshing: false,
        syncError: '暂时无法获取自动模拟仓数据，请稍后下拉刷新重试。',
        hasRemoteData: false,
      });
      return false;
    }

    const portfolio = payload.portfolio || {};
    const settleLog = (payload.settleLog || []).map(item => ({
      ...item,
      dailyPnl: Number(item.pnl != null ? item.pnl : item.dailyPnl != null ? item.dailyPnl : item.weekPnl || 0),
      dailyPct: Number(item.pct != null ? item.pct : item.dailyPct != null ? item.dailyPct : item.weekPct || 0),
    }));
    const reviews = (payload.weeklyReviews || []).map((item, idx) => {
      const pct = parseFloat(String(item.returnPct || '0').replace('%', '').replace('+', ''));
      return {
        ...item,
        showDetail: idx === 0,  // 最新一条默认展开
        returnClass: pct > 0 ? 'up' : (pct < 0 ? 'down' : 'flat'),
        trades: (item.trades || []).slice(0, 8).map(decorateTradeItem),
      };
    });
    const weeklyTradeDetails = (payload.weeklyTradeDetails || []).slice(0, 12).map(decorateTradeItem);
    const weeklyAttribution = payload.weeklyAttribution || null;
    const autoConfig = ((payload.meta || {}).autoConfig) || {};
    const tradeLog = (payload.tradeLog || []).slice(0, 50).map(decorateTradeItem);
    const latestSizingTrade = tradeLog.find(item => item.action === 'buy' && item.sizingLabel);
    const positionList = (portfolio.positions || []).map(item => ({
      ...item,
      currentPrice: Number(item.currentPrice || 0).toFixed(4),
      confirmedNav: Number(item.confirmedNav || item.currentPrice || 0).toFixed(4),
      currentValue: Number(item.currentValue || 0).toFixed(2),
      profit: Number(item.profit || 0).toFixed(2),
      profitPct: `${Number(item.profitPct || 0) >= 0 ? '+' : ''}${Number(item.profitPct || 0).toFixed(2)}%`,
      profitClass: Number(item.profit || 0) >= 0 ? (Number(item.profit || 0) > 0 ? 'up' : 'flat') : 'down',
    }));
    const latestSettle = settleLog.length > 0 ? settleLog[0] : null;
    const totalReturnPct = Number(portfolio.totalReturnPct || 0);
    const autoConfigSummary = [
      `单次加仓 ${Math.round(Number(autoConfig.targetBuyPct || 0) * 100)}% 目标仓位`,
      `单标的上限 ${Math.round(Number(autoConfig.maxPositionWeight || 0) * 100)}%`,
      `回撤阈值 ${Number(autoConfig.drawdownTriggerPct || 0)}%`,
      `止盈阈值 +${Number(autoConfig.takeProfitTriggerPct || 0)}%`,
    ].filter(Boolean);

    this.setData({
      remoteMode: true,
      autoMeta: payload.meta || null,
      weeklyTradeDetails,
      weeklyAttribution,
      autoConfigSummary,
      latestSizingLabel: latestSizingTrade ? latestSizingTrade.sizingLabel : '',
      latestSizingNotes: latestSizingTrade ? latestSizingTrade.sizingNotesText : '',
      portfolio,
      cash: Number(portfolio.cash || 0).toFixed(2),
      totalCash: Number(portfolio.totalCash || 0).toFixed(2),
      totalValue: Number(portfolio.totalValue || 0).toFixed(2),
      totalReturn: `${totalReturnPct >= 0 ? '+' : ''}${totalReturnPct.toFixed(2)}%`,
      totalReturnClass: totalReturnPct > 0 ? 'up' : (totalReturnPct < 0 ? 'down' : 'flat'),
      positionList,
      tradeLog,
      weeklyReviews: reviews,
      settleDate: latestSettle ? latestSettle.date : '',
      settleLog: settleLog.slice(0, 30),
      dailyPnl: latestSettle ? `${Number(latestSettle.dailyPnl || 0) >= 0 ? '+' : ''}${Number(latestSettle.dailyPnl || 0).toFixed(2)}` : '',
      dailyPnlClass: latestSettle ? (Number(latestSettle.dailyPnl || 0) > 0 ? 'up' : (Number(latestSettle.dailyPnl || 0) < 0 ? 'down' : 'flat')) : 'flat',
      dailyPct: latestSettle ? `${Number(latestSettle.dailyPct || 0) >= 0 ? '+' : ''}${Number(latestSettle.dailyPct || 0).toFixed(2)}%` : '',
      loading: false,
      refreshing: false,
      syncError: '',
      hasRemoteData: true,
    });
    this._buildCalendar();
    return true;
  },
  async manualSettle() {
    if (this.data.settling) return;
    this.setData({ settling: true });
    await this._loadServerPortfolio();
    this.setData({ settling: false });
    wx.showToast({ title: '已刷新自动模拟仓', icon: 'none' });
  },

  /* ================= 收益日历 ================= */
  _buildCalendar(year, month) {
    const now = new Date();
    const y = year || this.data.calYear || now.getFullYear();
    const m = month || this.data.calMonth || (now.getMonth() + 1);

    const settleLog = this.data.settleLog || [];
    // 建 date -> record 映射（daily 优先于 weekly）
    const settleMap = {};
    settleLog.forEach(r => {
      if (!settleMap[r.date] || r.kind === 'daily') settleMap[r.date] = r;
    });

    // 本月第一天和最后一天
    const firstDay = new Date(y, m - 1, 1);
    const lastDay = new Date(y, m, 0);
    const daysInMonth = lastDay.getDate();
    const startWeekday = firstDay.getDay(); // 0=周日

    // 上月补位天数
    const prevMonthLast = new Date(y, m - 1, 0).getDate();

    const weeks = [];
    let week = [];
    let dayCounter = 1;
    let nextMonthDay = 1;

    // 填充6行x7列
    for (let row = 0; row < 6; row++) {
      week = [];
      for (let col = 0; col < 7; col++) {
        const cellIdx = row * 7 + col;
        let cellDate = '';
        let day = 0;
        let inMonth = false;

        if (cellIdx < startWeekday) {
          // 上月
          day = prevMonthLast - startWeekday + cellIdx + 1;
          const pm = m - 1 <= 0 ? 12 : m - 1;
          const py = m - 1 <= 0 ? y - 1 : y;
          cellDate = `${py}-${String(pm).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
        } else if (dayCounter <= daysInMonth) {
          day = dayCounter;
          cellDate = `${y}-${String(m).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
          inMonth = true;
          dayCounter++;
        } else {
          // 下月
          day = nextMonthDay;
          const nm = m + 1 > 12 ? 1 : m + 1;
          const ny = m + 1 > 12 ? y + 1 : y;
          cellDate = `${ny}-${String(nm).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
          nextMonthDay++;
        }

        const record = settleMap[cellDate];
        const todayDate = todayStr();
        week.push({
          day,
          date: cellDate,
          inMonth,
          isToday: cellDate === todayDate,
          isWeekend: col === 0 || col === 6,
          hasData: !!record,
          pnl: record ? record.dailyPnl : 0,
          pct: record ? record.dailyPct : 0,
          pnlStr: record ? ((record.dailyPnl >= 0 ? '+' : '') + record.dailyPnl.toFixed(0)) : '',
          pnlClass: record ? (record.dailyPnl > 0 ? 'up' : (record.dailyPnl < 0 ? 'down' : 'flat')) : '',
        });
      }
      weeks.push(week);
      // 如果已填满本月，后续行不再需要
      if (dayCounter > daysInMonth) break;
    }

    // 如果最后一行全是下月的，去掉
    if (weeks.length > 5 && weeks[5].every(c => !c.inMonth)) {
      weeks.pop();
    }

    // 本月累计盈亏
    const monthPrefix = `${y}-${String(m).padStart(2,'0')}`;
    let monthPnl = 0;
    settleLog.forEach(r => {
      if (r.date && r.date.startsWith(monthPrefix)) {
        monthPnl += r.dailyPnl || 0;
      }
    });

    this.setData({
      calYear: y,
      calMonth: m,
      calTitle: `${y}年${m}月`,
      calWeeks: weeks,
      calMonthPnl: (monthPnl >= 0 ? '+' : '') + monthPnl.toFixed(2),
      calMonthPnlClass: monthPnl > 0 ? 'up' : (monthPnl < 0 ? 'down' : 'flat'),
    });
  },

  calPrevMonth() {
    let { calYear, calMonth } = this.data;
    calMonth--;
    if (calMonth < 1) { calMonth = 12; calYear--; }
    this._buildCalendar(calYear, calMonth);
    this.setData({ calSelectedDate: '', calSelectedDetail: null });
  },

  calNextMonth() {
    let { calYear, calMonth } = this.data;
    calMonth++;
    if (calMonth > 12) { calMonth = 1; calYear++; }
    this._buildCalendar(calYear, calMonth);
    this.setData({ calSelectedDate: '', calSelectedDetail: null });
  },

  calToday() {
    const now = new Date();
    this._buildCalendar(now.getFullYear(), now.getMonth() + 1);
    this.setData({ calSelectedDate: '', calSelectedDetail: null });
  },

  calSelectDate(e) {
    const date = e.currentTarget.dataset.date;
    const hasData = e.currentTarget.dataset.has;
    if (!hasData) {
      this.setData({ calSelectedDate: date, calSelectedDetail: null });
      return;
    }
    const settleLog = this.data.settleLog || [];
    const record = settleLog.find(r => r.date === date);
    this.setData({
      calSelectedDate: date,
      calSelectedDetail: record || null,
    });
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
});
