const { getHoldings, setHoldings } = require('../../utils/storage');

const TYPE_OPTIONS = ['宽基', '红利', '黄金', '有色金属', 'AI/科技', '半导体', '军工', '新能源', '医药', '消费', '债券', '港股科技', '原油', '其他'];

Page({
  data: {
    list: [],
    code: '',
    name: '',
    typeIndex: 13,
    typeOptions: TYPE_OPTIONS
  },

  onShow() {
    this.reload();
  },

  reload() {
    this.setData({ list: getHoldings() });
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
    if (current.some((item) => item.code === code)) {
      wx.showToast({ title: '该基金已存在', icon: 'none' });
      return;
    }

    const next = setHoldings([...current, { code, name, type }]);
    this.setData({ list: next, code: '', name: '' });
    wx.showToast({ title: '已添加', icon: 'success' });
  },

  removeHolding(e) {
    const code = e.currentTarget.dataset.code;
    const next = getHoldings().filter((item) => item.code !== code);
    setHoldings(next);
    this.setData({ list: next });
    wx.showToast({ title: '已删除', icon: 'success' });
  }
});
