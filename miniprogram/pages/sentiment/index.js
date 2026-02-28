const { getSettings } = require('../../utils/storage');
const { fetchHotEvents } = require('../../utils/api');

Page({
  data: {
    // 热力图
    heatmap: [],
    // 事件列表
    events: [],
    // 展望
    outlook: null,
    // 数据源
    sourceLabel: '加载中',
    updatedAt: '--',
    loading: true,
    // 筛选
    activeFilter: 'all',
    filters: [
      { key: 'all', label: '全部' },
      { key: 'positive', label: '利好' },
      { key: 'negative', label: '利空' },
      { key: 'policy', label: '政策' },
      { key: 'technology', label: '科技' },
      { key: 'geopolitics', label: '地缘' },
      { key: 'commodity', label: '商品' },
    ],
  },

  onLoad() {
    this.loadData();
  },

  onShow() {
    // 如果已有数据不重复加载
    if (this.data.events.length === 0) this.loadData();
  },

  onPullDownRefresh() {
    this.loadData().finally(() => wx.stopPullDownRefresh());
  },

  async loadData() {
    this.setData({ loading: true });
    const settings = getSettings();
    const { source, data } = await fetchHotEvents(settings);

    const heatmap = (data.heatmap || []).map(item => ({
      ...item,
      tempClass: item.temperature > 70 ? 'hot' : item.temperature > 50 ? 'warm' : 'cool',
      trendIcon: item.trend === 'up' ? '↑' : item.trend === 'down' ? '↓' : '→',
      sentimentPct: Math.round((item.sentiment || 0) * 100),
    }));

    const events = (data.events || []).map(item => ({
      ...item,
      impactClass: Number(item.impact || 0) >= 0 ? 'up' : 'down',
      impactStr: (Number(item.impact || 0) >= 0 ? '+' : '') + (item.impact || 0),
      category: item.category || '其他',
      sentimentLabel: this._sentimentLabel(item.sentiment),
      sectorsPos: (item.sectors_positive || []).join('、') || '--',
      sectorsNeg: (item.sectors_negative || []).join('、') || '--',
    }));

    const outlook = data.outlook || null;

    this.setData({
      heatmap,
      events,
      outlook,
      sourceLabel: source === 'remote' ? '远程数据' : '本地回退',
      updatedAt: String(data.updated_at || '--').replace('T', ' ').slice(0, 16),
      loading: false,
    });
  },

  _sentimentLabel(val) {
    const s = parseFloat(val || 0);
    if (s > 0.3) return '偏多';
    if (s < -0.3) return '偏空';
    return '中性';
  },

  onFilterTap(e) {
    this.setData({ activeFilter: e.currentTarget.dataset.key });
  },

  filteredEvents() {
    const f = this.data.activeFilter;
    if (f === 'all') return this.data.events;
    if (f === 'positive') return this.data.events.filter(e => Number(e.impact) > 0);
    if (f === 'negative') return this.data.events.filter(e => Number(e.impact) < 0);
    return this.data.events.filter(e => (e.category || '').includes(f));
  },
});
