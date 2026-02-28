const { getSettings, setSettings, getHoldings, getWatchlist } = require('../../utils/storage');
const { fetchHotEvents } = require('../../utils/api');
const { getMarketStatus, isTradingDay } = require('../../utils/market');
const { AI_PROVIDERS, getAIConfig, setAIConfig } = require('../../utils/ai');

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
    // AI 设置
    aiProviders: AI_PROVIDERS,
    aiProviderIndex: 0,
    aiKey: '',
    aiModels: [],
    aiModelIndex: 0,
    aiStatus: '',
    // 关于
    version: '2.0.0',
    updateDate: '2026-02',
  },

  onShow() {
    const settings = getSettings();
    const ms = getMarketStatus();
    const aiCfg = getAIConfig();
    const providerIdx = AI_PROVIDERS.findIndex(p => p.id === aiCfg.providerId);
    const provider = AI_PROVIDERS[providerIdx >= 0 ? providerIdx : 0];
    const modelIdx = provider.models.findIndex(m => m.model === aiCfg.model);
    this.setData({
      useRemote: !!settings.useRemote,
      apiBase: settings.apiBase || '',
      status: '',
      holdingsCount: getHoldings().length,
      watchlistCount: getWatchlist().length,
      marketStatus: ms.text,
      isTradingDay: isTradingDay(),
      aiProviderIndex: providerIdx >= 0 ? providerIdx : 0,
      aiKey: aiCfg.key || '',
      aiModels: provider.models,
      aiModelIndex: modelIdx >= 0 ? modelIdx : 0,
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

  // ====== AI 设置 ======
  onAIProviderChange(e) {
    const idx = Number(e.detail.value);
    const provider = AI_PROVIDERS[idx];
    setAIConfig({ providerId: provider.id, model: provider.defaultModel });
    this.setData({
      aiProviderIndex: idx,
      aiModels: provider.models,
      aiModelIndex: 0,
    });
  },

  onAIKeyInput(e) {
    this.setData({ aiKey: (e.detail.value || '').trim() });
  },

  saveAIKey() {
    setAIConfig({ key: this.data.aiKey });
    wx.showToast({ title: 'Key 已保存', icon: 'success' });
  },

  onAIModelChange(e) {
    const idx = Number(e.detail.value);
    const model = this.data.aiModels[idx];
    if (model) {
      setAIConfig({ model: model.model });
      this.setData({ aiModelIndex: idx });
    }
  },

  async testAIConnection() {
    if (!this.data.aiKey) {
      wx.showToast({ title: '请先填写API Key', icon: 'none' });
      return;
    }
    // 先自动保存 key，确保 storage 与页面一致
    setAIConfig({ key: this.data.aiKey });

    this.setData({ aiStatus: '测试中...' });
    const aiCfg = getAIConfig();
    const apiKey = this.data.aiKey;
    try {
      wx.request({
        url: aiCfg.provider.base,
        method: 'POST',
        timeout: 30000,
        header: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + apiKey,
        },
        data: {
          model: aiCfg.model,
          messages: [{ role: 'user', content: '你好，请回复ok' }],
          max_tokens: 10,
        },
        success: (res) => {
          if (res.statusCode === 200) {
            this.setData({ aiStatus: '✅ AI连接成功' });
            wx.showToast({ title: 'AI连接成功', icon: 'success' });
          } else if (res.statusCode === 403) {
            const errMsg = (res.data && (res.data.message || res.data.error && res.data.error.message)) || '';
            this.setData({ aiStatus: '❌ 403 权限被拒: ' + (errMsg || 'Key无效或额度不足') });
            wx.showModal({
              title: 'API Key 权限错误 (403)',
              content: '请检查：\n1. API Key 是否正确复制（完整，无多余空格）\n2. Key 是否已过期或额度用完\n3. 当前模型是否有权限使用\n\n' + (errMsg ? '服务端返回: ' + errMsg : ''),
              showCancel: false,
            });
          } else {
            const errDetail = typeof res.data === 'object' ? JSON.stringify(res.data).slice(0, 200) : String(res.data || '');
            this.setData({ aiStatus: '❌ 错误 ' + res.statusCode + ': ' + errDetail });
          }
        },
        fail: (err) => {
          this.setData({ aiStatus: '❌ 网络失败: ' + (err.errMsg || '') });
        },
      });
    } catch (e) {
      this.setData({ aiStatus: '❌ ' + e.message });
    }
  },
});
