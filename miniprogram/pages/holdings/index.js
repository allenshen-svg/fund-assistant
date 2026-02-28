const { getHoldings, setHoldings } = require('../../utils/storage');
const { fetchMultiFundEstimates } = require('../../utils/api');
const { formatPct, pctClass } = require('../../utils/market');

const TYPE_OPTIONS = ['宽基', '红利', '黄金', '有色金属', 'AI/科技', '半导体', '军工', '新能源', '白酒/消费', '医药', '债券', '蓝筹', '蓝筹/QDII', '港股科技', '原油', '其他'];

Page({
  data: {
    list: [],
    code: '',
    name: '',
    typeIndex: 15,
    typeOptions: TYPE_OPTIONS,
    showAdd: false,
    loading: false,
    // 快速添加（从内置数据库中选择）
    quickList: [],
    showQuick: false,
  },

  onShow() {
    this.reload();
  },

  onPullDownRefresh() {
    this.reload().finally(() => wx.stopPullDownRefresh());
  },

  async reload() {
    this.setData({ loading: true });
    const holdings = getHoldings();
    // 获取实时估值
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
      };
    });

    this.setData({ list, loading: false });
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

  // ====== 快速添加（从内置库中选择） ======
  toggleQuick() {
    if (!this.data.showQuick) {
      const app = getApp();
      const db = app.globalData.FUND_DB;
      const current = getHoldings();
      const existCodes = new Set(current.map(h => h.code));
      const quickList = Object.keys(db)
        .filter(code => !existCodes.has(code))
        .map(code => ({ code, name: db[code].name, type: db[code].type }));
      this.setData({ quickList, showQuick: true });
    } else {
      this.setData({ showQuick: false });
    }
  },

  quickAdd(e) {
    const item = e.currentTarget.dataset.item;
    const current = getHoldings();
    if (current.some(h => h.code === item.code)) {
      wx.showToast({ title: '已存在', icon: 'none' });
      return;
    }
    setHoldings([...current, { code: item.code, name: item.name, type: item.type }]);
    wx.showToast({ title: '已添加', icon: 'success' });
    // 更新快速添加列表
    this.setData({
      quickList: this.data.quickList.filter(q => q.code !== item.code)
    });
    this.reload();
  },

  // 重置为默认持仓
  resetToDefault() {
    wx.showModal({
      title: '重置持仓',
      content: '将恢复为默认的12只基金，当前持仓将被清除',
      success: (res) => {
        if (res.confirm) {
          const app = getApp();
          const key = app.globalData.storageKeys.holdings;
          wx.removeStorageSync(key);
          wx.showToast({ title: '已重置', icon: 'success' });
          this.reload();
        }
      }
    });
  },
});
