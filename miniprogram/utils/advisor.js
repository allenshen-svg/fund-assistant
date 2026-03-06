/**
 * 投资顾问引擎 — 行动指南 + 白话研判
 * 使用 analyzer.js 的技术分析结果
 */
const { analyzeTrend, getTrendLabel, computeVote, buildPlainAdvisor } = require('./analyzer');

/* ====== 板块-基金类型映射 ====== */
const TYPE_TAG_MAP = {
  黄金: ['黄金', '贵金属'],
  有色金属: ['有色金属'],
  'AI/科技': ['AI算力', '人工智能', '半导体', '机器人'],
  '半导体/科技': ['半导体', 'AI算力', '人工智能'],
  半导体: ['半导体'],
  军工: ['军工'],
  新能源: ['新能源', '光伏', '新能源车', '锂电'],
  医药: ['医药'],
  消费: ['消费'],
  '白酒/消费': ['消费', '白酒'],
  债券: ['债券'],
  宽基: ['宽基', '沪深300', 'A500'],
  红利: ['红利'],
  港股科技: ['港股科技'],
  原油: ['原油'],
  蓝筹: ['蓝筹', '消费', '医药'],
  'QDII': ['港股', '美股'],
  '蓝筹/QDII': ['蓝筹', '消费', '港股'],
};

/* ====== 匹配板块热度 ====== */
function pickHeatForType(type, heatmap) {
  const tags = TYPE_TAG_MAP[type] || [type];
  const matched = (heatmap || []).filter(item =>
    tags.some(tag => item.tag.includes(tag) || tag.includes(item.tag))
  );
  if (!matched.length) return { temperature: 50, trend: 'stable', sentiment: 0, tag: '通用' };
  const avgTemp = Math.round(matched.reduce((s, m) => s + Number(m.temperature || 0), 0) / matched.length);
  const up = matched.filter(m => m.trend === 'up').length;
  const down = matched.filter(m => m.trend === 'down').length;
  return {
    temperature: avgTemp,
    trend: up > down ? 'up' : down > up ? 'down' : 'stable',
    sentiment: matched.reduce((s, m) => s + Number(m.sentiment || 0), 0) / matched.length,
    tag: matched[0].tag,
  };
}

/* ====== 板块资金流额外标签（东方财富行业板块名称映射） ====== */
const FLOW_EXTRA_TAGS = {
  黄金: ['有色金属'],
  'AI/科技': ['计算机', '电子', '通信'],
  '半导体/科技': ['计算机', '电子'],
  半导体: ['电子'],
  军工: ['国防军工'],
  新能源: ['电力设备'],
  医药: ['医药生物'],
  消费: ['食品饮料', '商贸零售', '家用电器'],
  '白酒/消费': ['食品饮料'],
  红利: ['银行', '煤炭', '公用事业'],
  港股科技: ['计算机', '电子'],
  原油: ['石油石化'],
  蓝筹: ['银行', '非银金融', '食品饮料'],
  '蓝筹/QDII': ['食品饮料'],
  宽基: ['银行', '非银金融'],
};

/* ====== 基金名称→板块关键词（用于"其他"类型的名称匹配） ====== */
const NAME_FLOW_MAP = [
  { keywords: ['机器人', '人工智能', 'AI', '智能'], sectors: ['计算机', '电子', '机械设备'] },
  { keywords: ['芯片', '半导体', '集成电路'], sectors: ['电子'] },
  { keywords: ['新能源', '光伏', '锂电', '储能', '风电'], sectors: ['电力设备'] },
  { keywords: ['白酒', '食品', '饮料', '消费'], sectors: ['食品饮料'] },
  { keywords: ['医药', '医疗', '生物', '创新药'], sectors: ['医药生物'] },
  { keywords: ['军工', '国防', '航天', '航空'], sectors: ['国防军工'] },
  { keywords: ['黄金', '贵金属', '金ETF'], sectors: ['有色金属'] },
  { keywords: ['有色', '铜', '铝', '锂', '稀土'], sectors: ['有色金属'] },
  { keywords: ['原油', '石油', '油气', '能源'], sectors: ['石油石化'] },
  { keywords: ['银行', '金融'], sectors: ['银行'] },
  { keywords: ['券商', '证券'], sectors: ['非银金融'] },
  { keywords: ['地产', '房地产', '基建'], sectors: ['房地产'] },
  { keywords: ['传媒', '游戏', '影视'], sectors: ['传媒'] },
  { keywords: ['汽车', '新能源车', '智能驾驶'], sectors: ['汽车'] },
  { keywords: ['钢铁', '煤炭', '建材'], sectors: ['钢铁', '煤炭'] },
  { keywords: ['通信', '5G'], sectors: ['通信'] },
  { keywords: ['农业', '养殖', '种业'], sectors: ['农林牧渔'] },
];

/* ====== 匹配板块资金流 ====== */
function matchSectorFlow(type, sectorFlows, fundName) {
  if (!sectorFlows || !sectorFlows.length) return null;
  const baseTags = TYPE_TAG_MAP[type] || [type];
  const extra = FLOW_EXTRA_TAGS[type] || [];
  let tags = baseTags.concat(extra);
  const normalize = (s) => String(s || '').replace(/[\s\u3000]/g, '');
  const exactTags = new Set(baseTags.map(normalize));
  const extraTags = new Set(extra.map(normalize));

  // 对"其他"或匹配不到时，根据基金名称推断板块
  if (fundName) {
    for (const nm of NAME_FLOW_MAP) {
      if (nm.keywords.some(kw => fundName.includes(kw))) {
        tags = tags.concat(nm.sectors);
        break;
      }
    }
  }

  const allTags = Array.from(new Set(tags)).map(normalize).filter(Boolean);
  if (!allTags.length) return null;

  const tagWeight = (tag) => {
    if (exactTags.has(tag)) return 100;
    if (extraTags.has(tag)) return 70;
    return 85;
  };

  let best = null;
  let bestScore = -1;

  for (const flow of sectorFlows) {
    const flowName = normalize(flow && flow.name);
    if (!flowName) continue;

    let score = -1;
    for (const tag of allTags) {
      if (!tag) continue;
      const base = tagWeight(tag);
      if (flowName === tag) {
        score = Math.max(score, base + 30);
      } else if (flowName.includes(tag) || tag.includes(flowName)) {
        score = Math.max(score, base + 15);
      }
    }

    if (score < 0) continue;

    const mainNetAbs = Math.abs(Number(flow.mainNet || 0));
    const mainPctAbs = Math.abs(Number(flow.mainPct || 0));
    const totalScore = score * 1000000 + Math.round(mainNetAbs) + Math.round(mainPctAbs * 1000);

    if (totalScore > bestScore) {
      bestScore = totalScore;
      best = flow;
    }
  }

  if (best) return best;

  for (const flow of sectorFlows) {
    if (tags.some(tag => flow.name.includes(tag) || tag.includes(flow.name))) {
      return flow;
    }
  }
  return null;
}

/* ====== 优先级排序 ====== */
function priorityLabel(absScore) {
  if (absScore >= 0.35) return '高';
  if (absScore >= 0.18) return '中';
  return '低';
}

/* ====== 构建完整行动指南 ====== */
function buildPlans(holdings, heatmap, historyMap, sectorFlows) {
  historyMap = historyMap || {};
  sectorFlows = sectorFlows || [];

  const plans = (holdings || []).map((item, idx) => {
    const heatInfo = pickHeatForType(item.type, heatmap);
    const navList = historyMap[item.code] || [];
    const td = analyzeTrend(navList);
    const sectorFlow = matchSectorFlow(item.type, sectorFlows, item.name);
    const vote = computeVote(td, heatInfo, sectorFlow);
    const trendLabel = getTrendLabel(td);
    const plainAdvisor = buildPlainAdvisor(item, td, heatInfo, vote);

    return {
      ...item,
      // 板块
      tag: heatInfo.tag,
      temperature: heatInfo.temperature,
      heatTrend: heatInfo.trend,
      sentiment: Number((heatInfo.sentiment || 0).toFixed(2)),
      // 投票结果
      action: vote.action,
      actionLabel: vote.label,
      score: vote.score,
      confidence: vote.confidence,
      consensus: vote.consensus,
      crowding: vote.crowding,
      buyVotes: vote.buyVotes,
      sellVotes: vote.sellVotes,
      factors: vote.factors || [],
      swingAdvice: vote.swingAdvice || '—',
      // 趋势标签
      dirIcon: trendLabel.dirIcon,
      dirText: trendLabel.dirText,
      dirColor: trendLabel.dirColor,
      swingIcon: trendLabel.swingIcon,
      swingText: trendLabel.swingText,
      swingColor: trendLabel.swingColor,
      maStatus: trendLabel.maStatus || '—',
      // 关键数据
      rsi: td ? td.rsi.toFixed(0) : '--',
      chg5d: td && td.chg5d !== null ? (td.chg5d >= 0 ? '+' : '') + td.chg5d.toFixed(1) + '%' : '--',
      chg20d: td && td.chg20d !== null ? (td.chg20d >= 0 ? '+' : '') + td.chg20d.toFixed(1) + '%' : '--',
      drawdown: td ? td.drawdownFromHigh.toFixed(1) + '%' : '--',
      rebound: td ? td.reboundFromLow.toFixed(1) + '%' : '--',
      volatility: td ? td.volatility.toFixed(1) + '%' : '--',
      showDrawdown: td && td.drawdownFromHigh < -5,
      showRebound: td && td.reboundFromLow > 5,
      hasTrend: !!td,
      // 资金流
      sectorFlowText: sectorFlow ? ((sectorFlow.mainNet >= 0 ? '+' : '') + (sectorFlow.mainNet / 1e8).toFixed(1) + '亿') : null,
      sectorFlowDir: sectorFlow ? (sectorFlow.mainNet >= 0 ? 'in' : 'out') : null,
      // 风险
      riskScore: plainAdvisor.riskScore,
      riskLevel: plainAdvisor.riskLevel,
      // 优先级
      priority: idx + 1,
      urgency: priorityLabel(Math.abs(vote.score)),
      urgencyClass: priorityLabel(Math.abs(vote.score)) === '高' ? 'high' : priorityLabel(Math.abs(vote.score)) === '中' ? 'mid' : 'low',
      // 白话研判
      advisor: plainAdvisor,
      // 详情展开
      expanded: false,
    };
  });

  // 排序: 卖出 > 买入 > 持有，同类按风险分降序
  const order = { sell: 0, buy: 1, hold: 2 };
  plans.sort((a, b) => {
    if (order[a.action] !== order[b.action]) return order[a.action] - order[b.action];
    return b.riskScore - a.riskScore;
  });

  // 重编优先级
  plans.forEach((p, i) => { p.priority = i + 1; });

  return plans;
}

/* ====== 概览统计 ====== */
function buildOverview(plans) {
  const buy = plans.filter(p => p.action === 'buy').length;
  const sell = plans.filter(p => p.action === 'sell').length;
  const hold = plans.filter(p => p.action === 'hold').length;
  const avg = plans.length
    ? Math.round(plans.reduce((s, p) => s + (p.confidence || 50), 0) / plans.length)
    : 0;

  let label = '中性观望';
  if (buy > sell + hold) label = '积极偏多';
  else if (buy > sell) label = '谨慎偏多';
  else if (sell > buy + hold) label = '防御优先';
  else if (sell > buy) label = '谨慎偏空';

  return { buy, sell, hold, score: avg, label };
}

/* ====== 模型组合 ====== */
const MODEL_PORTFOLIO = {
  core: [
    { code: '022430', name: '华夏中证A500ETF联接A', type: '宽基', weight: 20 },
    { code: '007339', name: '易方达沪深300联接C', type: '宽基', weight: 15 },
    { code: '000216', name: '华安黄金ETF联接A', type: '黄金', weight: 15 },
    { code: '021418', name: '泰康红利低波联接C', type: '红利', weight: 10 },
  ],
  satellite: [
    { code: '016708', name: '华夏有色金属联接C', type: '有色金属', weight: 10 },
    { code: '008586', name: '华夏人工智能联接C', type: 'AI/科技', weight: 8 },
    { code: '008887', name: '华夏半导体芯片联接A', type: '半导体', weight: 8 },
    { code: '005693', name: '广发军工联接C', type: '军工', weight: 7 },
    { code: '007993', name: '华夏证券公司联接C', type: '券商', weight: 7 },
  ],
};

module.exports = {
  pickHeatForType,
  matchSectorFlow,
  buildPlans,
  buildOverview,
  MODEL_PORTFOLIO,
};
