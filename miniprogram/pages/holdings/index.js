const { getHoldings, setHoldings } = require('../../utils/storage');
const { fetchMultiFundEstimates, fetchMultiFundHistory } = require('../../utils/api');
const { formatPct, pctClass, isTradingDay, todayStr, getPrevTradingDay } = require('../../utils/market');
const { analyzeTrend, computeVote } = require('../../utils/analyzer');
const { pickHeatForType } = require('../../utils/advisor');

const TYPE_OPTIONS = ['宽基', '红利', '黄金', '有色金属', 'AI/科技', '半导体', '军工', '新能源', '白酒/消费', '医药', '债券', '蓝筹', '蓝筹/QDII', '港股科技', '原油', '其他'];

/* ====== 预测追踪 keys ====== */
const PRED_KEY = 'fa_pred_tracker_mp';
const MAX_ENTRIES = 30;

Page({
  data: {
    list: [],
    code: '',
    name: '',
    typeIndex: 15,
    typeOptions: TYPE_OPTIONS,
    showAdd: false,
    loading: false,


    // ====== 波段组合 ======
    secSwing: false,
    swingItems: [],
    swingLoading: false,

    // ====== 预测追踪 ======
    secPred: false,
    predStats: null,
    predLatest: null,
    predHistory: [],
    predTodayDone: false,
    _reminderTimer: null,
  },

  onShow() {
    this.reload();
    this._scheduleReminder();
  },

  onHide() {
    this._clearReminder();
  },

  onUnload() {
    this._clearReminder();
  },

  _scheduleReminder() {
    this._clearReminder();
    const today = todayStr();
    if (!isTradingDay(today)) return;
    const now = new Date();
    const target = new Date(now);
    target.setHours(14, 50, 0, 0);
    const diff = target.getTime() - now.getTime();
    // 如果还没到14:50且距离不超过6小时，设置定时提醒
    if (diff > 0 && diff < 6 * 3600 * 1000) {
      this._reminderTimer = setTimeout(() => {
        wx.vibrateLong({ type: 'heavy' });
        wx.showModal({
          title: '⏰ 快照提醒',
          content: '现在是 14:50，建议立即记录今日预测快照！',
          confirmText: '立即快照',
          cancelText: '稍后',
          success: (res) => {
            if (res.confirm) {
              this.snapshotPrediction();
            }
          },
        });
      }, diff);
    }
  },

  _clearReminder() {
    if (this._reminderTimer) {
      clearTimeout(this._reminderTimer);
      this._reminderTimer = null;
    }
  },

  onPullDownRefresh() {
    this.reload().finally(() => wx.stopPullDownRefresh());
  },

  async reload() {
    this.setData({ loading: true });
    const holdings = getHoldings();
    const codes = holdings.map(h => h.code);
    const estimates = await fetchMultiFundEstimates(codes);

    const list = holdings.map(h => {
      const est = estimates[h.code];
      return {
        ...h,
        pctStr: est ? formatPct(est.pct) : '--',
        pctClass: est ? pctClass(est.pct) : 'flat',
        estimate: est ? est.estimate : null,
        nav: est ? est.nav : null,
        gszzl: est ? est.pct : null,
      };
    });

    this.setData({ list, loading: false });

    // 加载预测追踪
    this._loadPredTracker();

    // 自动快照：交易日 14:50~15:00 且今日未快照
    this._tryAutoSnapshot();
  },

  _tryAutoSnapshot() {
    const today = todayStr();
    if (!isTradingDay(today)) return;
    const now = new Date();
    const hh = now.getHours();
    const mm = now.getMinutes();
    // 14:50 ~ 15:00
    if (hh === 14 && mm >= 50) {
      const tracker = wx.getStorageSync(PRED_KEY) || [];
      if (!tracker.some(e => e.date === today)) {
        wx.showModal({
          title: '自动快照',
          content: '当前为收盘前（14:50~15:00），是否自动记录今日预测快照？',
          success: (res) => {
            if (res.confirm) {
              const t = wx.getStorageSync(PRED_KEY) || [];
              this._doSnapshot(t, today);
            }
          },
        });
      }
    }
  },

  toggleSection(e) {
    const key = e.currentTarget.dataset.key;
    if (key) {
      const val = !this.data[key];
      this.setData({ [key]: val });
      // 展开波段组合时加载数据
      if (key === 'secSwing' && val && this.data.swingItems.length === 0) {
        this._loadSwingData();
      }
    }
  },

  /* ========================================================
   *  波段组合 — 基于持仓基金的波段信号
   * ======================================================== */
  async _loadSwingData() {
    this.setData({ swingLoading: true });
    const holdings = getHoldings();
    const codes = holdings.map(h => h.code);
    const historyMap = await fetchMultiFundHistory(codes);

    const swingItems = holdings.map(h => {
      const navList = historyMap[h.code] || [];
      const td = analyzeTrend(navList);
      if (!td) {
        return {
          ...h,
          hasTrend: false,
          swingSignal: '数据不足',
          swingClass: 'hold',
          trendDir: '--',
          chg5d: '--',
          chg20d: '--',
          drawdown: '--',
          rsi: '--',
        };
      }

      // 波段信号
      let swingSignal, swingClass;
      const sa = td.swingAdvice || '';
      if (/买入|低吸/.test(sa)) { swingSignal = '🟢 波段买入'; swingClass = 'buy'; }
      else if (/止盈|减仓|暂避|勿追/.test(sa)) { swingSignal = '🔴 考虑减仓'; swingClass = 'sell'; }
      else if (td.trendDir === 'up' || td.trendDir === 'strong_up') { swingSignal = '🟡 趋势持有'; swingClass = 'hold'; }
      else if (td.trendDir === 'down' || td.trendDir === 'strong_down') { swingSignal = '🔴 趋势偏弱'; swingClass = 'sell'; }
      else { swingSignal = '🟡 震荡观望'; swingClass = 'hold'; }

      const fmtPct = (v) => v !== null && v !== undefined ? (v >= 0 ? '+' : '') + v.toFixed(1) + '%' : '--';

      return {
        ...h,
        hasTrend: true,
        swingSignal,
        swingClass,
        swingAdvice: sa,
        trendDir: td.trendDir,
        chg5d: fmtPct(td.chg5d),
        chg5dClass: td.chg5d >= 0 ? 'pct-up' : 'pct-down',
        chg20d: fmtPct(td.chg20d),
        chg20dClass: td.chg20d >= 0 ? 'pct-up' : 'pct-down',
        drawdown: td.drawdownFromHigh ? td.drawdownFromHigh.toFixed(1) + '%' : '--',
        rebound: td.reboundFromLow ? td.reboundFromLow.toFixed(1) + '%' : '--',
        rsi: td.rsi ? td.rsi.toFixed(0) : '--',
        maStatus: td.maStatus || '--',
      };
    });

    this.setData({ swingItems, swingLoading: false });
  },

  /* ========================================================
   *  预测追踪
   * ======================================================== */
  _loadPredTracker() {
    const tracker = wx.getStorageSync(PRED_KEY) || [];
    const today = todayStr();

    // 自动验证: 找前一个交易日的未验证条目
    const prevDay = getPrevTradingDay(today);
    if (prevDay && isTradingDay(today)) {
      const idx = tracker.findIndex(e => e.date === prevDay && !e.verified);
      if (idx >= 0) {
        this._verifyEntry(tracker, idx);
        wx.setStorageSync(PRED_KEY, tracker.slice(-MAX_ENTRIES));
      }
    }

    // 统计
    const verified = tracker.filter(e => e.verified && e.verification);
    const stats = this._calcStats(verified);
    const predTodayDone = tracker.some(e => e.date === today);

    // 最后一条已验证的
    const latestVerified = verified.length > 0 ? verified[verified.length - 1] : null;

    // 历史 (最近10条)
    const predHistory = tracker.slice(-10).reverse().map(e => ({
      date: e.date,
      timestamp: e.timestamp || '--',
      overallLabel: e.overallLabel || '--',
      overallScore: e.overallScore || 0,
      verified: e.verified,
      accuracy: e.verification ? e.verification.accuracy : null,
      accuracyStr: e.verification ? e.verification.accuracy.toFixed(0) + '%' : '⏳ 待验证',
    }));

    this.setData({
      predStats: stats,
      predLatest: latestVerified ? this._formatLatest(latestVerified) : null,
      predHistory,
      predTodayDone,
    });
  },

  _calcStats(verified) {
    if (!verified.length) return { totalDays: 0, accuracy: 0, avgReturn: 0, correctCount: 0, wrongCount: 0, neutralCount: 0, totalCount: 0 };
    let correct = 0, wrong = 0, neutral = 0, totalRet = 0, total = 0;
    verified.forEach(e => {
      const v = e.verification;
      correct += v.correctCount || 0;
      wrong += v.wrongCount || 0;
      neutral += v.neutralCount || 0;
      total += v.totalCount || 0;
      totalRet += v.avgPredReturn || 0;
    });
    return {
      totalDays: verified.length,
      accuracy: total > 0 ? (correct / total * 100) : 0,
      avgReturn: verified.length > 0 ? (totalRet / verified.length) : 0,
      correctCount: correct,
      wrongCount: wrong,
      neutralCount: neutral,
      totalCount: total,
    };
  },

  _formatLatest(entry) {
    const v = entry.verification;
    const results = [];
    if (v && v.results) {
      Object.keys(v.results).forEach(code => {
        const r = v.results[code];
        results.push({
          name: r.name || code,
          predLabel: r.predActionLabel || r.predAction,
          nextPct: r.nextDayPct !== undefined ? (r.nextDayPct >= 0 ? '+' : '') + r.nextDayPct.toFixed(2) + '%' : '--',
          nextPctClass: r.nextDayPct >= 0 ? 'pct-up' : 'pct-down',
          verdict: r.verdict,
          verdictIcon: r.verdict === 'correct' ? '✅' : r.verdict === 'wrong' ? '❌' : '➖',
        });
      });
    }
    return {
      date: entry.date,
      verifyDate: v ? v.verifyDate : '--',
      accuracy: v ? v.accuracy.toFixed(0) + '%' : '--',
      accuracyClass: v && v.accuracy >= 60 ? 'good' : v && v.accuracy >= 40 ? 'medium' : 'poor',
      avgReturn: v ? (v.avgPredReturn >= 0 ? '+' : '') + v.avgPredReturn.toFixed(2) + '%' : '--',
      results,
    };
  },

  // 手动快照
  async snapshotPrediction() {
    const today = todayStr();
    if (!isTradingDay(today)) {
      wx.showToast({ title: '非交易日', icon: 'none' }); return;
    }
    const tracker = wx.getStorageSync(PRED_KEY) || [];
    if (tracker.some(e => e.date === today)) {
      wx.showModal({
        title: '今日已有快照',
        content: '确定覆盖今日的预测快照？',
        success: (res) => { if (res.confirm) this._doSnapshot(tracker, today); },
      });
      return;
    }
    this._doSnapshot(tracker, today);
  },

  async _doSnapshot(tracker, today) {
    wx.showLoading({ title: '快照中...' });
    const holdings = getHoldings();
    const codes = holdings.map(h => h.code);
    const [estimates, historyMap] = await Promise.all([
      fetchMultiFundEstimates(codes),
      fetchMultiFundHistory(codes),
    ]);

    const holdingsData = {};
    let totalScore = 0;
    holdings.forEach(h => {
      const navList = historyMap[h.code] || [];
      const td = analyzeTrend(navList);
      const heatInfo = pickHeatForType(h.type, []);
      const vote = computeVote(td, heatInfo, null);
      const est = estimates[h.code];

      holdingsData[h.code] = {
        name: h.name,
        type: h.type,
        action: vote.action,
        actionLabel: vote.label,
        score: vote.score,
        confidence: vote.confidence,
        gszzl: est ? est.pct : null,
        rsi: td ? td.rsi : null,
        swingPos: td ? td.swingPos : null,
        trendDir: td ? td.trendDir : null,
      };
      totalScore += vote.score;
    });

    const avgScore = holdings.length > 0 ? totalScore / holdings.length : 0;
    const overallScore = Math.max(0, Math.min(100, Math.round(50 + avgScore * 50)));
    let overallLabel;
    if (overallScore >= 70) overallLabel = '积极加仓';
    else if (overallScore >= 58) overallLabel = '偏多持有';
    else if (overallScore >= 42) overallLabel = '中性观望';
    else if (overallScore >= 30) overallLabel = '偏空谨慎';
    else overallLabel = '防御减仓';

    const now = new Date();
    const entry = {
      date: today,
      timestamp: `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`,
      holdings: holdingsData,
      overallScore,
      overallLabel,
      verified: false,
      verification: null,
    };

    // 替换或追加
    const idx = tracker.findIndex(e => e.date === today);
    if (idx >= 0) tracker[idx] = entry;
    else tracker.push(entry);

    wx.setStorageSync(PRED_KEY, tracker.slice(-MAX_ENTRIES));
    wx.hideLoading();
    wx.showToast({ title: '快照完成', icon: 'success' });
    this._loadPredTracker();
  },

  // 手动验证
  manualVerify() {
    const tracker = wx.getStorageSync(PRED_KEY) || [];
    const today = todayStr();
    // 找前一个交易日的未验证条目
    const prevDay = getPrevTradingDay(today);
    const idx = tracker.findIndex(e => e.date === prevDay && !e.verified);
    if (idx < 0) {
      wx.showToast({ title: '无待验证条目', icon: 'none' }); return;
    }
    wx.showLoading({ title: '验证中...' });
    this._verifyEntry(tracker, idx);
    wx.setStorageSync(PRED_KEY, tracker.slice(-MAX_ENTRIES));
    wx.hideLoading();
    wx.showToast({ title: '验证完成', icon: 'success' });
    this._loadPredTracker();
  },

  _verifyEntry(tracker, idx) {
    const entry = tracker[idx];
    const holdings = entry.holdings;
    const codes = Object.keys(holdings);
    // 使用当前估值数据作为"次日涨幅"的近似（因为小程序无法拿到精确的次日收盘净值）
    // 从当前 list 中读取 gszzl
    const currentList = this.data.list;
    const results = {};
    let correct = 0, wrong = 0, neutral = 0, totalRet = 0;

    codes.forEach(code => {
      const pred = holdings[code];
      const current = currentList.find(c => c.code === code);
      const nextPct = current && current.gszzl !== null ? current.gszzl : 0;

      let verdict = 'neutral';
      if (pred.action === 'buy') {
        if (nextPct > 0.3) verdict = 'correct';
        else if (nextPct < -0.3) verdict = 'wrong';
      } else if (pred.action === 'sell') {
        if (nextPct < -0.3) verdict = 'correct';
        else if (nextPct > 0.3) verdict = 'wrong';
      } else {
        if (Math.abs(nextPct) < 1) verdict = 'correct';
        else if (nextPct < -1.5) verdict = 'wrong';
      }

      if (verdict === 'correct') correct++;
      else if (verdict === 'wrong') wrong++;
      else neutral++;

      const hypRet = pred.action === 'buy' ? nextPct : pred.action === 'sell' ? -nextPct : 0;
      totalRet += hypRet;

      results[code] = {
        name: pred.name,
        type: pred.type,
        predAction: pred.action,
        predActionLabel: pred.actionLabel,
        predScore: pred.score,
        nextDayPct: nextPct,
        verdict,
        hypotheticalRet: hypRet,
      };
    });

    const total = codes.length;
    entry.verified = true;
    entry.verification = {
      verifyDate: todayStr(),
      results,
      accuracy: total > 0 ? (correct / total * 100) : 0,
      correctCount: correct,
      wrongCount: wrong,
      neutralCount: neutral,
      totalCount: total,
      avgPredReturn: total > 0 ? totalRet / total : 0,
    };
  },

  // ====== 添加相关 ======
  toggleAdd() {
    this.setData({ showAdd: !this.data.showAdd });
  },

  onCodeInput(e) {
    this.setData({ code: e.detail.value.trim() });
  },

  onNameInput(e) {
    this.setData({ name: e.detail.value.trim() });
  },

  onTypeChange(e) {
    this.setData({ typeIndex: Number(e.detail.value) });
  },

  addHolding() {
    const code = this.data.code;
    const name = this.data.name;
    const type = this.data.typeOptions[this.data.typeIndex] || '其他';
    if (!code || !name) {
      wx.showToast({ title: '请填写代码和名称', icon: 'none' });
      return;
    }
    const current = getHoldings();
    if (current.some(item => item.code === code)) {
      wx.showToast({ title: '该基金已存在', icon: 'none' });
      return;
    }
    setHoldings([...current, { code, name, type }]);
    this.setData({ code: '', name: '', showAdd: false });
    wx.showToast({ title: '已添加', icon: 'success' });
    this.reload();
  },

  removeHolding(e) {
    const code = e.currentTarget.dataset.code;
    const name = e.currentTarget.dataset.name;
    wx.showModal({
      title: '确认删除',
      content: `确定删除 ${name}（${code}）？`,
      success: (res) => {
        if (res.confirm) {
          const next = getHoldings().filter(item => item.code !== code);
          setHoldings(next);
          wx.showToast({ title: '已删除', icon: 'success' });
          this.reload();
        }
      }
    });
  },


});
