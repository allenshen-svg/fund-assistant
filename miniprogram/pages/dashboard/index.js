const { getHoldings, getSettings } = require('../../utils/storage');
const { fetchHotEvents } = require('../../utils/api');
const { buildPlans, buildOverview } = require('../../utils/advisor');

Page({
  data: {
    sourceLabel: '加载中',
    updatedAt: '--',
    holdingsCount: 0,
    overview: { buy: 0, sell: 0, hold: 0, score: 0, label: '中性观望' },
    plans: [],
    topEvents: [],
    loading: true
  },

  onShow() {
    this.loadAll();
  },

  onPullDownRefresh() {
    this.loadAll().finally(() => wx.stopPullDownRefresh());
  },

  async loadAll() {
    this.setData({ loading: true });
    const holdings = getHoldings();
    const settings = getSettings();

    const { source, data } = await fetchHotEvents(settings);
    const plans = buildPlans(holdings, data.heatmap || []);
    const overview = buildOverview(plans);
    const topEvents = (data.events || []).slice(0, 5).map((item) => ({
      id: item.id,
      title: item.title,
      advice: item.advice || '保持观察',
      impact: Number(item.impact || 0)
    }));

    this.setData({
      sourceLabel: source === 'remote' ? '远程数据' : '本地回退',
      updatedAt: String(data.updated_at || '--').replace('T', ' ').slice(0, 16),
      holdingsCount: holdings.length,
      overview,
      plans,
      topEvents,
      loading: false
    });
  },

  goHoldings() {
    wx.switchTab({ url: '/pages/holdings/index' });
  },

  goSettings() {
    wx.switchTab({ url: '/pages/settings/index' });
  }
});
