const { getSettings } = require('../../utils/storage');
const { fetchHotEvents, fetchSentimentData, fetchAnalysisData, fetchUSMarketData, triggerRefresh, triggerReanalyze, getServerBase } = require('../../utils/api');

/* ====== 金融关键词 (与 H5 sa-config 同步) ====== */
const FINANCE_KW = [
  'A股','股市','大盘','沪指','上证','深成','创业板','科创板','沪深300','恒生','港股','美股','纳斯达克',
  'AI','人工智能','算力','芯片','半导体','光模块','CPO','大模型','DeepSeek',
  '机器人','自动驾驶','新能源','光伏','锂电','碳酸锂','储能',
  '军工','国防','航天','白酒','消费','医药','创新药','CXO',
  '黄金','金价','原油','油价','有色金属','铜','铝','稀土',
  '红利','高股息','银行','保险','券商','地产',
  '央行','降息','降准','LPR','利率','通胀','CPI','GDP','PMI',
  '美联储','加息','国债','债券','汇率','人民币',
  '关税','贸易战','制裁','地缘','中东','俄乌',
  '基金','ETF','牛市','熊市','涨停','跌停','抄底','追高',
  '仓位','加仓','减仓','定投','主力','资金','北向',
  '茅台','比亚迪','宁德','英伟达','NVIDIA','特斯拉',
  'IPO','分红','回购','并购','重组','股','基','市场','经济','投资','收益','行情',
  '板块','指数','概念','题材','龙头','主线','赛道',
  '盘','散户','机构','债市',
];
const _kwRe = new RegExp(FINANCE_KW.join('|'), 'i');
function isFinance(text) { return _kwRe.test(text || ''); }

/* ====== 噪音检测 ====== */
const NOISE_RE = /震惊|全仓梭哈|赶紧|速看|神秘主力|涨疯了|暴涨|必看|百倍|日赚|翻倍|内幕|绝密|私募推荐/;

/* ====== 热度雷达关键词 (与 H5 sa-render 同步) ====== */
const HEAT_KEYWORDS = [
  'AI算力','人工智能','半导体','军工','黄金','碳酸锂','新能源','港股','机器人',
  '消费','医药','原油','白酒','芯片','锂电','红利','ETF','基金','券商','银行',
  '地产','光伏','储能','稀土','CXO','关税','自动驾驶',
  '有色金属','铜','铝','创新药','保险','国债','债券',
  '大模型','DeepSeek','比亚迪','宁德','英伟达','特斯拉','茅台',
  '降息','降准','美联储','通胀','汇率','人民币',
  '贸易战','制裁','中东','俄乌',
  '科创板','创业板','北向','主力','龙头',
];

Page({
  data: {
    loading: true,
    sourceLabel: '加载中',
    updatedAt: '--',

    // —— 总览卡 ——
    signalText: '等待分析',
    signalIcon: '⏳',
    signalClass: 'wait',
    marketTemp: '--',
    tempLabel: '--',
    tempClass: 'neutral',
    radarSummary: '--',
    hotAssets: [],
    fomoLevel: 0,
    panicLevel: 0,
    divergenceIndex: 0,

    // —— 数据源统计 ——
    sourcePills: [],
    totalItems: 0,

    // —— 隔夜美股 ——
    usStocks: [],

    // —— KOL vs 散户 ——
    kolSections: [],

    // —— 操作指南 ——
    holdingActions: [],
    bullish: '',
    bearish: '',
    tactical: '',

    // —— 原始数据流 ——
    videoItems: [],

    // —— AI 完整报告 ——
    aiReport: '',

    // —— 原有热力图 + 事件 ——
    heatmap: [],
    events: [],
    outlook: null,
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

    // —— 折叠控制 ——
    secUsMarket: false,
    secKol: true,
    secAction: true,
    secVideos: false,
    secReport: false,
    secHeatmap: true,
    secEvents: true,

    // —— 手动刷新 ——
    refreshing: false,
    refreshMsg: '',
  },

  onLoad() {},

  onShow() {
    this.loadAll();
  },

  onPullDownRefresh() {
    this.loadAll().finally(() => wx.stopPullDownRefresh());
  },

  toggleSection(e) {
    const key = e.currentTarget.dataset.key;
    if (key) this.setData({ [key]: !this.data[key] });
  },

  /* ====== 手动触发舆情采集 + AI 分析 ====== */
  async onManualRefresh() {
    if (this.data.refreshing) return;
    this.setData({ refreshing: true, refreshMsg: '正在触发采集...' });
    const settings = getSettings();
    // 轮询时必须从 Flask 服务器读取数据，而不是 GitHub Pages
    const serverBase = getServerBase(settings);

    // 检查是否已配置服务器地址
    if (!serverBase) {
      this.setData({
        refreshMsg: '❌ 未配置服务器地址，请在「设置」中填写舆情分析服务器地址'
      });
      setTimeout(() => this.setData({ refreshing: false, refreshMsg: '' }), 4000);
      return;
    }

    const serverSettings = { ...settings, apiBase: serverBase };

    try {
      // 1. 触发后端采集
      const refreshRes = await triggerRefresh(settings);
      if (refreshRes.status === 'busy') {
        this.setData({ refreshMsg: '采集进行中，请稍候...' });
      } else if (refreshRes.status === 'started') {
        this.setData({ refreshMsg: '采集已启动，等待完成...' });
      } else {
        // 触发失败，提前结束
        this.setData({ refreshMsg: '❌ ' + (refreshRes.message || '无法连接后端服务器，请确认 Flask 服务已启动') });
        setTimeout(() => this.setData({ refreshing: false, refreshMsg: '' }), 4000);
        return;
      }

      // 2. 轮询等待采集完成（最多120秒）—— 从 Flask 服务器读取
      let ready = false;
      for (let i = 0; i < 40; i++) {
        await _sleep(3000);
        const data = await fetchSentimentData(serverSettings);
        if (data && data.fetch_ts) {
          const age = Math.floor(Date.now() / 1000) - data.fetch_ts;
          if (age < 120) { ready = true; break; }
        }
        this.setData({ refreshMsg: `采集中... (${(i + 1) * 3}s)` });
      }

      if (!ready) {
        this.setData({ refreshMsg: '采集超时，请稍后重试' });
        setTimeout(() => this.setData({ refreshing: false, refreshMsg: '' }), 3000);
        return;
      }

      // 3. 触发 AI 分析
      this.setData({ refreshMsg: '采集完成，正在 AI 分析...' });
      const analyzeRes = await triggerReanalyze(settings);
      if (analyzeRes.status === 'error') {
        // AI 分析触发失败，仅刷新采集数据
        this.setData({ refreshMsg: '采集完成，AI 分析触发失败' });
        await this.loadAll();
        setTimeout(() => this.setData({ refreshing: false, refreshMsg: '' }), 3000);
        return;
      }

      // 4. 等待分析完成（最多60秒）—— 从 Flask 服务器读取
      for (let i = 0; i < 20; i++) {
        await _sleep(3000);
        const analysis = await fetchAnalysisData(serverSettings);
        if (analysis && analysis.analysis_ts) {
          const age = Math.floor(Date.now() / 1000) - analysis.analysis_ts;
          if (age < 120) break;
        }
        this.setData({ refreshMsg: `AI 分析中... (${(i + 1) * 3}s)` });
      }

      // 5. 刷新页面数据
      this.setData({ refreshMsg: '✅ 更新完成！' });
      await this.loadAll();
    } catch (e) {
      console.error('Manual refresh failed:', e);
      this.setData({ refreshMsg: '❌ 刷新失败: ' + (e.message || e) });
    } finally {
      setTimeout(() => this.setData({ refreshing: false, refreshMsg: '' }), 2500);
    }
  },

  async loadAll() {
    this.setData({ loading: true });
    const settings = getSettings();
    // 如果配置了服务器地址，优先从 Flask 服务器获取最新数据
    // 否则从 GitHub Pages（apiBase）读取静态缓存
    const serverBase = getServerBase(settings);
    const dataSettings = serverBase ? { ...settings, apiBase: serverBase } : settings;

    // 并行获取：舆情 + AI分析 + 美股 + 热点事件
    const [sentimentRes, analysisRes, usRes, hotRes] = await Promise.allSettled([
      fetchSentimentData(dataSettings),
      fetchAnalysisData(dataSettings),
      fetchUSMarketData(dataSettings),
      fetchHotEvents(settings),
    ]);

    const sentimentData = sentimentRes.status === 'fulfilled' ? sentimentRes.value : null;
    const analysisData = analysisRes.status === 'fulfilled' ? analysisRes.value : null;
    const usData = usRes.status === 'fulfilled' ? usRes.value : null;
    const hotData = hotRes.status === 'fulfilled' ? hotRes.value : null;

    const batch = { loading: false };

    // ========== 1. 总览卡 (来自 analysis) ==========
    if (analysisData) {
      const db = (analysisData.dashboard && analysisData.dashboard.hourly_dashboard) || {};
      const temp = db.market_temperature || 50;
      const signal = db.action_signal || 'Wait';

      const sigMap = {
        'Aggressive Buy': { icon: '🟢', text: '积极买入', cls: 'buy' },
        'Cautious Hold':  { icon: '🟡', text: '谨慎持有', cls: 'hold' },
        'Defensive':      { icon: '🟠', text: '防御姿态', cls: 'defensive' },
        'Strong Sell':    { icon: '🔴', text: '强烈卖出', cls: 'sell' },
        'Wait':           { icon: '⏳', text: '等待观望', cls: 'wait' },
      };
      const sig = sigMap[signal] || sigMap['Wait'];
      batch.signalIcon = sig.icon;
      batch.signalText = sig.text;
      batch.signalClass = sig.cls;

      let tempLabel, tempClass;
      if (temp >= 80) { tempLabel = '过热 🔥'; tempClass = 'overheat'; }
      else if (temp >= 65) { tempLabel = '偏热 🌡️'; tempClass = 'hot'; }
      else if (temp >= 45) { tempLabel = '温和 ☀️'; tempClass = 'warm'; }
      else if (temp >= 25) { tempLabel = '中性 ⚖️'; tempClass = 'neutral'; }
      else { tempLabel = '冰冷 ❄️'; tempClass = 'cold'; }
      batch.marketTemp = temp;
      batch.tempLabel = tempLabel;
      batch.tempClass = tempClass;

      batch.fomoLevel = db.fomo_level || 0;
      batch.panicLevel = db.panic_level || 0;
      batch.divergenceIndex = db.divergence_index || 0;
      batch.radarSummary = analysisData.radar_summary || '--';
      batch.hotAssets = (db.hot_assets || []).map(a => ({ name: a }));

      // —— KOL sections ——
      batch.kolSections = (analysisData.kol_sections || []).map(s => ({
        target: s.target || '未知',
        kol: s.kol || '--',
        retail: s.retail || '--',
        conclusion: s.conclusion || '--',
        divClass: classifyDivergence(s.conclusion || ''),
      }));

      // —— 操作指南 ——
      const acts = analysisData.actions || {};
      batch.holdingActions = (acts.holding_actions || []).map(a => ({
        label: a.label,
        advice: a.advice,
        actClass: classifyAction(a.advice),
      }));
      batch.bullish = acts.bullish || '--';
      batch.bearish = acts.bearish || '--';
      batch.tactical = acts.tactical || '--';

      // —— AI 报告 ——
      batch.aiReport = analysisData.raw_text || '';
      batch.updatedAt = (analysisData.analysis_time || '--').slice(0, 16);
      batch.sourceLabel = '远程分析';
    }

    // ========== 2. 数据源统计 + 舆情数据 ==========
    if (sentimentData) {
      const sc = sentimentData.source_counts || {};
      const platforms = ['抖音','微博','东方财富','财联社','新浪财经','知乎','百度','B站','小红书'];
      batch.sourcePills = platforms.map(p => ({
        name: p,
        short: p.replace('东方财富','东财').replace('新浪财经','新浪'),
        count: sc[p] || 0,
      })).filter(p => p.count > 0);
      batch.totalItems = sentimentData.total || sentimentData.items.length;

      const deduped = dedup(sentimentData.items || []);
      deduped.sort((a, b) => (b.likes || 0) - (a.likes || 0));
      const top100 = deduped.slice(0, 100);
      const finItems = top100.filter(v => isFinance(v.title || v.summary || ''));

      batch.videoItems = finItems.slice(0, 50).map(v => ({
        title: v.title || v.summary || '--',
        likes: formatNum(v.likes || 0),
        platform: v.platform || '未知',
        sentiment: v.sentiment || '中性',
        sentClass: sentClass(v.sentiment),
        isNoise: NOISE_RE.test(v.title || ''),
      }));
    }

    // ========== 3. 隔夜美股 ==========
    if (usData && usData.stocks) {
      batch.usStocks = usData.stocks.map(s => ({
        name: s.name,
        symbol: s.symbol,
        price: s.price,
        pct: s.percent,
        pctStr: (s.percent >= 0 ? '+' : '') + s.percent.toFixed(2) + '%',
        pctClass: s.percent >= 0 ? 'pct-up' : 'pct-down',
        amplitude: s.amplitude ? s.amplitude.toFixed(2) + '%' : '--',
      }));
    }

    // ========== 4. 热点事件 (原有) ==========
    if (hotData) {
      const hd = hotData.data || {};
      batch.heatmap = (hd.heatmap || []).map(item => ({
        ...item,
        tempClass: item.temperature > 70 ? 'hot' : item.temperature > 50 ? 'warm' : 'cool',
        trendIcon: item.trend === 'up' ? '↑' : item.trend === 'down' ? '↓' : '→',
      }));
      batch.events = (hd.events || []).map(item => ({
        ...item,
        impactClass: Number(item.impact || 0) >= 0 ? 'up' : 'down',
        impactStr: (Number(item.impact || 0) >= 0 ? '+' : '') + (item.impact || 0),
        category: item.category || '其他',
        sentimentLabel: this._sentimentLabel(item.sentiment),
        sectorsPos: (item.sectors_positive || []).join('、') || '--',
        sectorsNeg: (item.sectors_negative || []).join('、') || '--',
      }));
      batch.outlook = hd.outlook || null;

      // ========== 5. 补充热力图板块到 KOL 博弈拆解 ==========
      const existingTargets = new Set((batch.kolSections || []).map(s => s.target));
      const topHeat = (hd.heatmap || []).slice(0, 10);
      const heatKols = topHeat
        .filter(h => !existingTargets.has(h.tag))
        .map(h => {
          const trendText = h.trend === 'up' ? '热度上升' : h.trend === 'down' ? '热度回落' : '热度持平';
          const tempText = h.temperature >= 80 ? '极度拥挤' : h.temperature >= 60 ? '偏热' : '适中';
          const advice = h.temperature >= 80
            ? `${h.tag}板块热度${h.temperature}°，交易拥挤度高，追涨风险大，建议等回调再介入。`
            : h.temperature >= 60
            ? `${h.tag}关注度${trendText}，当前热度${h.temperature}°，可适度参与但注意仓位控制。`
            : `${h.tag}热度${h.temperature}°，关注度一般，${h.trend === 'up' ? '但有升温趋势可关注' : '暂无明显机会'}。`;
          return {
            target: h.tag,
            kol: `板块热度 ${h.temperature}°，${trendText}，市场关注度${tempText}。`,
            retail: h.temperature >= 70 ? '散户讨论度较高，跟风情绪明显。' : '散户关注度一般，情绪中性。',
            conclusion: advice,
            divClass: '',
          };
        });
      batch.kolSections = (batch.kolSections || []).concat(heatKols);
    }

    this.setData(batch);
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
});

/* ====== 辅助函数 ====== */

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function dedup(items) {
  const seen = new Set();
  return items.filter(v => {
    const key = (v.title || '').replace(/[\s\W]/g, '').slice(0, 20);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function formatNum(n) {
  if (n >= 10000) return (n / 10000).toFixed(1) + '万';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

function sentClass(s) {
  if (!s) return 'neu';
  if (/看多|偏多|乐观|积极|追多|贪婪|狂热|极度看多/.test(s)) return 'pos';
  if (/看空|偏空|悲观|恐慌|谨慎|极度悲观/.test(s)) return 'neg';
  return 'neu';
}

function classifyDivergence(text) {
  if (/逆向|抄底|做多|低估|反转|背离做多|散户恐慌.*KOL看多/.test(text)) return 'fomo';
  if (/见顶|泡沫|过热|高估|回撤|背离做空|散户狂热.*KOL谨慎|亢奋|追高|警惕回调|FOMO/.test(text)) return 'panic';
  return 'neutral';
}

function classifyAction(advice) {
  if (/加仓|买入/.test(advice)) return 'bullish';
  if (/减仓|卖出|回避/.test(advice)) return 'bearish';
  return 'neutral';
}
