const fallbackHotEvents = {
  updated_at: '1970-01-01T00:00:00+08:00',
  heatmap: [
    { tag: 'AI算力', temperature: 88, sentiment: 0.85, trend: 'up' },
    { tag: '人工智能', temperature: 85, sentiment: 0.8, trend: 'stable' },
    { tag: '半导体', temperature: 82, sentiment: 0.75, trend: 'up' },
    { tag: '黄金', temperature: 80, sentiment: 0.7, trend: 'stable' },
    { tag: '军工', temperature: 78, sentiment: 0.75, trend: 'up' },
    { tag: '新能源', temperature: 70, sentiment: 0.65, trend: 'stable' },
    { tag: '有色金属', temperature: 70, sentiment: 0.6, trend: 'up' },
    { tag: '医药', temperature: 62, sentiment: 0.5, trend: 'up' },
    { tag: '消费', temperature: 58, sentiment: 0.45, trend: 'up' },
    { tag: '债券', temperature: 50, sentiment: 0.3, trend: 'up' },
    { tag: '宽基', temperature: 45, sentiment: 0.2, trend: 'stable' }
  ],
  events: [
    {
      id: 'fallback_evt_1',
      title: '本地回退：宏观地缘事件加载中',
      impact: 0,
      confidence: 0.5,
      reason: '远程数据不可用时使用本地样本',
      advice: '请在设置页配置可访问的数据地址'
    }
  ]
};

module.exports = {
  fallbackHotEvents
};
