const { getSettings, setSettings, getHoldings, getWatchlist } = require('../../utils/storage');
const { fetchHotEvents } = require('../../utils/api');
const { getMarketStatus, isTradingDay } = require('../../utils/market');

Page({
  data: {
    useRemote: true,
    apiBase: '',
    status: '',
    statusColor: '',
    // 统计
    holdingsCount: 0,
    watchlistCount: 0,
    marketStatus: '--',
    isTradingDay: false,
    // 关于
    version: '2.0.0',
    updateDate: '2026-02',
  },

  onShow() {
    const settings = getSettings();
    const ms = getMarketStatus();
    this.setData({
      useRemote: !!settings.useRemote,
      apiBase: settings.apiBase || '',
      status: '',
      holdingsCount: getHoldings().length,
      watchlistCount: getWatchlist().length,
      marketStatus: ms.text,
      isTradingDay: isTradingDay(),
    });
  },

  onSwitchRemote(e) {
    this.setData({ useRemote: !!e.detail.value });
  },

  onApiInput(e) {
    this.setData({ apiBase: (e.detail.value || '').trim() });
  },

  save() {
    setSettings({
      useRemote: this.data.useRemote,
      apiBase: this.data.apiBase
    });
    wx.showToast({ title: '设置已保存', icon: 'success' });
  },

  async testConnection() {
    this.setData({ status: '测试中...', statusColor: '' });
    const result = await fetchHotEvents({
      useRemote: this.data.useRemote,
      apiBase: this.data.apiBase
    });

    const count = (result.data.heatmap || []).length;
    if (result.source === 'remote') {
      this.setData({
        status: `✅ 远程连接成功：heatmap ${count} 条`,
        statusColor: 'green'
      });
      wx.showToast({ title: '远程连接成功', icon: 'success' });
    } else {
      this.setData({
        status: `⚠️ 回退到本地：heatmap ${count} 条`,
        statusColor: 'orange'
      });
      wx.showToast({ title: '使用本地回退', icon: 'none' });
    }
  },

  clearCache() {
    wx.showModal({
      title: '清除缓存',
      content: '将清除所有本地缓存数据（设置、持仓等），确定？',
      success(res) {
        if (res.confirm) {
          wx.clearStorageSync();
          wx.showToast({ title: '已清除', icon: 'success' });
          setTimeout(() => {
            wx.reLaunch({ url: '/pages/dashboard/index' });
          }, 1000);
        }
      }
    });
  },

  copyApiBase() {
    wx.setClipboardData({
      data: this.data.apiBase || 'https://allenshen-svg.github.io/fund-assistant',
      success() {
        wx.showToast({ title: '已复制', icon: 'success' });
      }
    });
  },
});
