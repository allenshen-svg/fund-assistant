const { getSettings, getSimPortfolio, setSimPortfolio, addSimTradeLog } = require('../../utils/storage');
const { fetchServerStockScreen, triggerServerStockScreen, subscribeStockScreenNotice, getServerBase, fetchMultiStockQuotes } = require('../../utils/api');
const { formatPct, pctClass, todayStr } = require('../../utils/market');

const POLL_INTERVAL = 4000;
const MAX_POLL_COUNT = 240;

function formatScreenPick(pick) {
  const latestPct = typeof pick.latestPct === 'number' ? pick.latestPct : parseFloat(pick.latestPct || 0);
  const rrRatio = typeof pick.rrRatio === 'number' ? pick.rrRatio : parseFloat(pick.rrRatio || 0);
  return {
    ...pick,
    sector: pick.sector || pick.sector_name || '',
    latestPctClass: pick.latestPctClass || pctClass(latestPct),
    latestPctStr: pick.latestPctStr || formatPct(latestPct),
    signalText: pick.signalText || ((pick.signals || []).slice(0, 5).join(' / ')),
    riskText: pick.riskText || ((pick.riskFlags || []).slice(0, 3).join('；')) || '量价关系与趋势结构相对健康',
    advice: pick.advice || '仅列入观察，不建议急追',
    buyZone: pick.buyZone || pick.entryHint || '--',
    stopLossText: typeof pick.stopLoss === 'number' ? `¥${pick.stopLoss}` : '--',
    targetPriceText: typeof pick.targetPrice === 'number' ? `¥${pick.targetPrice}` : '--',
    rrRatioText: rrRatio > 0 ? `${rrRatio}` : '--',
    planType: pick.planType || '--',
    planNote: pick.planNote || '',
  };
}

function formatTimestamp(value) {
  return String(value || '').replace('T', ' ').slice(0, 16);
}

Page({
  data: {
    loading: true,
    running: false,
    statusText: '进入页面后将自动开始选股',
    pollText: '',
    serverConfigured: true,
    showResult: false,
    screenSummary: '',
    screenStrategy: '',
    screenPicks: [],
    screenTime: '',
    screenScanned: 0,
    screenQualified: 0,
    baselineTimestamp: '',
    lastVisibleTimestamp: '',
    notifyAvailable: false,
    notifyTemplateId: '',
    notifyPage: 'pages/stock-screen/index',
    notifyReason: '',
    notifySubscribed: false,
    notifyLoading: false,
    notifyStatusText: '',
  },

  onShow() {
    this._enterAndRun();
  },

  onHide() {
    this._clearPollTimer();
  },

  onUnload() {
    this._clearPollTimer();
  },

  onPullDownRefresh() {
    this._enterAndRun(true).finally(() => wx.stopPullDownRefresh());
  },

  async _enterAndRun(force) {
    this._clearPollTimer();
    const settings = getSettings();
    const serverBase = getServerBase(settings);

    if (!serverBase) {
      this.setData({
        loading: false,
        running: false,
        serverConfigured: false,
        showResult: false,
        statusText: '未配置后端服务器地址，无法自动选股',
        pollText: '',
      });
      return;
    }

    this.setData({
      loading: true,
      running: true,
      serverConfigured: true,
      statusText: '正在连接后台选股服务...',
      pollText: '',
    });

    const currentData = await fetchServerStockScreen(settings);
    this._applyNotifyMeta(currentData && currentData.notify);
    const baselineTimestamp = currentData && currentData.timestamp ? currentData.timestamp : '';
    this.setData({ baselineTimestamp });

    if (currentData && currentData.result) {
      this._applyServerResult(
        currentData,
        currentData.running ? '云端任务进行中，先展示最近一次结果' : '已读取云端最近一次结果',
        { keepRunning: !!currentData.running, preservePollText: true }
      );
    }

    if (!force && currentData && currentData.running) {
      this.setData({
        loading: !this.data.showResult,
        running: true,
        statusText: '后台已有选股任务在云端运行，可退出页面，稍后再看结果',
        pollText: '任务在服务器持续执行，关闭小程序后下次进入仍可查看结果',
      });
      this._startPolling(settings, baselineTimestamp, 0);
      return;
    }

    const triggerRes = await triggerServerStockScreen(settings);
    if (triggerRes && (triggerRes.status === 'started' || triggerRes.status === 'busy')) {
      this.setData({
        loading: !this.data.showResult,
        running: true,
        statusText: triggerRes.status === 'busy' ? '后台任务已在运行，正在等待结果...' : '已进入后台自动选股，结果生成后将自动显示',
        pollText: this.data.showResult
          ? '新一轮任务正在云端运行，你可以先离开页面，稍后再回来查看'
          : '首次筛选通常需要数分钟，任务会在云端继续运行',
      });
      this._startPolling(settings, baselineTimestamp, 0);
      return;
    }

    if (currentData && currentData.result) {
      this._applyServerResult(currentData, '本次自动触发失败，先展示最近一次结果');
      return;
    }

    this.setData({
      loading: false,
      running: false,
      showResult: false,
      statusText: (triggerRes && triggerRes.message) || '自动触发失败，请稍后下拉重试',
      pollText: '',
    });
  },

  _startPolling(settings, baselineTimestamp, pollCount) {
    this._clearPollTimer();
    this._pollTimer = setTimeout(async () => {
      const nextCount = pollCount + 1;
      const serverData = await fetchServerStockScreen(settings);
      const hasVisibleResult = !!this.data.showResult;

      if (!serverData) {
        if (nextCount >= MAX_POLL_COUNT) {
          this.setData({
            loading: false,
            running: false,
            statusText: hasVisibleResult ? '等待超时，先保留最近一次结果' : '轮询超时，请下拉刷新重试',
            pollText: '',
          });
          return;
        }
        this.setData({ pollText: `后台连接中... 已等待 ${nextCount * (POLL_INTERVAL / 1000)} 秒` });
        this._startPolling(settings, baselineTimestamp, nextCount);
        return;
      }

      const nextTimestamp = serverData.timestamp || '';
      const hasFreshResult = !!(serverData.result && !serverData.running && nextTimestamp && nextTimestamp !== baselineTimestamp);
      const hasFirstResult = !!(serverData.result && !serverData.running && !baselineTimestamp);

      if (hasFreshResult || hasFirstResult) {
        this._applyServerResult(serverData, '');
        return;
      }

      if (nextCount >= MAX_POLL_COUNT) {
        if (serverData.result) {
          this._applyServerResult(serverData, '等待超时，先展示最近一次结果');
        } else {
          this.setData({
            loading: false,
            running: false,
            statusText: '等待结果超时，请稍后再试',
            pollText: '',
          });
        }
        return;
      }

      this.setData({
        loading: !hasVisibleResult,
        running: !!serverData.running,
        statusText: serverData.running ? '后台选股正在云端进行中...' : '等待后台写入最新结果...',
        pollText: hasVisibleResult
          ? `当前先展示上次结果，云端已运行 ${nextCount * (POLL_INTERVAL / 1000)} 秒`
          : `已等待 ${nextCount * (POLL_INTERVAL / 1000)} 秒`,
      });
      this._startPolling(settings, baselineTimestamp, nextCount);
    }, POLL_INTERVAL);
  },

  _applyServerResult(serverData, statusText, options) {
    const nextOptions = options || {};
    if (!nextOptions.preservePollText) {
      this._clearPollTimer();
    }
    this._applyNotifyMeta(serverData && serverData.notify);
    const result = (serverData && serverData.result) || {};
    const picks = (result.picks || []).map(formatScreenPick);
    this.setData({
      loading: false,
      running: !!nextOptions.keepRunning,
      showResult: true,
      statusText: statusText || '最新选股结果已生成',
      pollText: nextOptions.preservePollText ? this.data.pollText : '',
      screenSummary: result.marketSummary || '',
      screenStrategy: result.strategyNote || '',
      screenPicks: picks,
      screenTime: formatTimestamp(serverData.timestamp),
      screenScanned: result.scannedCount || 0,
      screenQualified: result.qualifiedCount || 0,
      lastVisibleTimestamp: serverData.timestamp || '',
    });
    // 用实时行情刷新价格和涨跌幅
    this._refreshPickQuotes(picks);
  },

  async _refreshPickQuotes(picks) {
    if (!picks || picks.length === 0) return;
    const codes = picks.map(p => p.code).filter(Boolean);
    if (codes.length === 0) return;
    try {
      const quotes = await fetchMultiStockQuotes(codes);
      if (!quotes || Object.keys(quotes).length === 0) return;
      const updated = this.data.screenPicks.map(p => {
        const q = quotes[p.code];
        if (!q) return p;
        const pct = typeof q.pct === 'number' ? q.pct : parseFloat(q.pct || 0);
        return {
          ...p,
          latestPrice: q.nav || q.estimate || p.latestPrice,
          latestPct: pct,
          latestPctStr: formatPct(pct),
          latestPctClass: pctClass(pct),
        };
      });
      this.setData({ screenPicks: updated });
    } catch (e) {}
  },

  _applyNotifyMeta(notifyMeta) {
    if (!notifyMeta) return;
    this.setData({
      notifyAvailable: !!notifyMeta.available,
      notifyTemplateId: notifyMeta.templateId || '',
      notifyPage: notifyMeta.page || 'pages/stock-screen/index',
      notifyReason: notifyMeta.reason || '',
      notifyStatusText: notifyMeta.available
        ? (this.data.notifySubscribed ? this.data.notifyStatusText : '可订阅结果完成通知，任务结束后微信会提醒你')
        : (notifyMeta.reason || '服务器暂未开启订阅消息'),
    });
  },

  subscribeResultNotice() {
    if (this.data.notifyLoading) return;
    if (!this.data.notifyAvailable || !this.data.notifyTemplateId) {
      wx.showToast({ title: this.data.notifyReason || '通知暂不可用', icon: 'none' });
      return;
    }
    if (typeof wx.requestSubscribeMessage !== 'function') {
      wx.showToast({ title: '当前环境不支持订阅消息', icon: 'none' });
      return;
    }

    const settings = getSettings();
    this.setData({ notifyLoading: true, notifyStatusText: '正在请求通知授权...' });

    wx.requestSubscribeMessage({
      tmplIds: [this.data.notifyTemplateId],
      success: (subscribeRes) => {
        const decision = subscribeRes[this.data.notifyTemplateId];
        if (decision !== 'accept') {
          this.setData({
            notifyLoading: false,
            notifySubscribed: false,
            notifyStatusText: decision === 'reject' ? '你已拒绝本次通知授权，可稍后再试' : '未完成通知授权',
          });
          return;
        }

        wx.login({
          success: async (loginRes) => {
            if (!loginRes.code) {
              this.setData({
                notifyLoading: false,
                notifySubscribed: false,
                notifyStatusText: '微信登录态获取失败，请稍后重试',
              });
              return;
            }

            const result = await subscribeStockScreenNotice(settings, {
              code: loginRes.code,
              templateId: this.data.notifyTemplateId,
              page: this.data.notifyPage,
            });

            this.setData({
              notifyLoading: false,
              notifySubscribed: result && result.status === 'subscribed',
              notifyStatusText: (result && result.message) || '登记通知失败，请稍后重试',
            });

            if (!result || result.status !== 'subscribed') {
              wx.showToast({ title: (result && result.message) || '登记失败', icon: 'none' });
              return;
            }

            wx.showToast({ title: '已登记通知', icon: 'success' });
          },
          fail: () => {
            this.setData({
              notifyLoading: false,
              notifySubscribed: false,
              notifyStatusText: '微信登录失败，请稍后重试',
            });
          },
        });
      },
      fail: () => {
        this.setData({
          notifyLoading: false,
          notifySubscribed: false,
          notifyStatusText: '通知授权请求失败，请稍后重试',
        });
      },
    });
  },

  _clearPollTimer() {
    if (this._pollTimer) {
      clearTimeout(this._pollTimer);
      this._pollTimer = null;
    }
  },

  goSettings() {
    wx.switchTab({ url: '/pages/settings/index' });
  },

  addToSimPortfolio(e) {
    const code = String(e.currentTarget.dataset.code || '');
    const name = String(e.currentTarget.dataset.name || '');
    const sector = String(e.currentTarget.dataset.sector || '');
    const price = parseFloat(e.currentTarget.dataset.price || 0);
    const reason = String(e.currentTarget.dataset.reason || '自动选股加入模拟仓');
    if (!code || !name || !price || price <= 0) {
      wx.showToast({ title: '价格数据无效', icon: 'none' });
      return;
    }

    const portfolio = getSimPortfolio();
    const options = [5000, 10000, 20000].filter((amount) => amount <= portfolio.cash);
    if (options.length === 0) {
      wx.showToast({ title: '模拟仓余额不足', icon: 'none' });
      return;
    }

    wx.showActionSheet({
      itemList: options.map((amount) => `买入 ¥${amount}`),
      success: (res) => {
        const amount = options[res.tapIndex];
        if (!amount) return;
        const latestPortfolio = getSimPortfolio();
        if (amount > latestPortfolio.cash) {
          wx.showToast({ title: '余额不足', icon: 'none' });
          return;
        }

        const shares = amount / price;
        const existIdx = latestPortfolio.positions.findIndex((item) => item.code === code);
        if (existIdx >= 0) {
          const old = latestPortfolio.positions[existIdx];
          const newCostTotal = old.costTotal + amount;
          const newShares = old.shares + shares;
          latestPortfolio.positions[existIdx] = {
            ...old,
            shares: newShares,
            costTotal: newCostTotal,
            costPrice: newCostTotal / newShares,
            sector: sector || old.sector || '',
          };
        } else {
          latestPortfolio.positions.push({
            code,
            name,
            type: 'stock',
            sector,
            shares,
            costPrice: price,
            costTotal: amount,
            buyDate: todayStr(),
          });
        }

        latestPortfolio.cash -= amount;
        setSimPortfolio(latestPortfolio);
        addSimTradeLog({
          date: todayStr(),
          action: 'buy',
          code,
          name,
          sector,
          amount,
          price,
          reason,
          aiSource: 'stockScreen',
        });
        wx.showToast({ title: `已加入 ¥${amount}`, icon: 'success' });
      },
    });
  },
});