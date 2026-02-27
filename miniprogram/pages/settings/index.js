const { getSettings, setSettings } = require('../../utils/storage');
const { fetchHotEvents } = require('../../utils/api');

Page({
  data: {
    useRemote: true,
    apiBase: '',
    status: ''
  },

  onShow() {
    const settings = getSettings();
    this.setData({
      useRemote: !!settings.useRemote,
      apiBase: settings.apiBase || '',
      status: ''
    });
  },

  onSwitchRemote(e) {
    this.setData({ useRemote: !!e.detail.value });
  },

  onApiInput(e) {
    this.setData({ apiBase: (e.detail.value || '').trim() });
  },

  save() {
    const next = setSettings({
      useRemote: this.data.useRemote,
      apiBase: this.data.apiBase
    });
    this.setData({
      useRemote: next.useRemote,
      apiBase: next.apiBase
    });
    wx.showToast({ title: '设置已保存', icon: 'success' });
  },

  async testConnection() {
    this.setData({ status: '测试中...' });
    const result = await fetchHotEvents({
      useRemote: this.data.useRemote,
      apiBase: this.data.apiBase
    });

    const count = (result.data.heatmap || []).length;
    if (result.source === 'remote') {
      this.setData({ status: `远程可用：heatmap ${count} 条` });
      wx.showToast({ title: '远程连接成功', icon: 'success' });
      return;
    }

    this.setData({ status: `回退本地：heatmap ${count} 条` });
    wx.showToast({ title: '当前使用本地回退', icon: 'none' });
  }
});
