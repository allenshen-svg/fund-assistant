const TYPE_TAG_MAP = {
  黄金: ['黄金', '贵金属'],
  有色金属: ['有色金属'],
  'AI/科技': ['AI算力', '人工智能', '半导体', '机器人'],
  半导体: ['半导体'],
  军工: ['军工'],
  新能源: ['新能源', '光伏', '新能源车', '锂电'],
  医药: ['医药'],
  消费: ['消费', '白酒'],
  债券: ['债券'],
  宽基: ['宽基'],
  红利: ['红利'],
  港股科技: ['港股科技'],
  原油: ['原油']
};

function pickHeatForType(type, heatmap) {
  const tags = TYPE_TAG_MAP[type] || [type];
  const matched = (heatmap || []).filter((item) =>
    tags.some((tag) => item.tag.includes(tag) || tag.includes(item.tag))
  );
  if (!matched.length) {
    return { temperature: 50, trend: 'stable', sentiment: 0, tag: '通用' };
  }
  const avgTemp = Math.round(
    matched.reduce((sum, item) => sum + Number(item.temperature || 0), 0) / matched.length
  );
  const up = matched.filter((m) => m.trend === 'up').length;
  const down = matched.filter((m) => m.trend === 'down').length;
  return {
    temperature: avgTemp,
    trend: up > down ? 'up' : down > up ? 'down' : 'stable',
    sentiment: matched.reduce((sum, item) => sum + Number(item.sentiment || 0), 0) / matched.length,
    tag: matched[0].tag
  };
}

function actionByHeat(temperature, trend) {
  if (temperature >= 72 && trend !== 'down') return 'buy';
  if (temperature <= 46 || trend === 'down') return 'sell';
  return 'hold';
}

function scoreByAction(action, temperature) {
  if (action === 'buy') return Math.min(95, 50 + Math.round((temperature - 50) * 1.2));
  if (action === 'sell') return Math.max(5, 50 - Math.round((temperature - 50) * 1.1));
  return 50;
}

function actionLabel(action) {
  if (action === 'buy') return '建议加仓';
  if (action === 'sell') return '建议减仓';
  return '持有观察';
}

function riskScore(temperature, trend, sentiment) {
  let score = 0;
  if (temperature >= 80) score += 35;
  else if (temperature >= 70) score += 25;
  else if (temperature >= 60) score += 15;

  if (trend === 'down') score += 25;
  if (sentiment < -0.2) score += 20;
  if (sentiment > 0.6 && temperature > 75) score += 10;

  return Math.min(100, score);
}

function riskLevel(score) {
  if (score >= 55) return '高风险';
  if (score >= 35) return '中风险';
  return '低风险';
}

function reasonText(action, info) {
  if (action === 'buy') {
    return `赛道热度 ${info.temperature}，趋势偏强，当前更适合分批参与。`;
  }
  if (action === 'sell') {
    return `赛道热度 ${info.temperature}，趋势/情绪偏弱，建议优先做风险收缩。`;
  }
  return `赛道热度 ${info.temperature}，方向不明，继续观察等待更清晰信号。`;
}

function buildPlans(holdings, heatmap) {
  const plans = (holdings || []).map((item) => {
    const heatInfo = pickHeatForType(item.type, heatmap);
    const action = actionByHeat(heatInfo.temperature, heatInfo.trend);
    const score = scoreByAction(action, heatInfo.temperature);
    const risk = riskScore(heatInfo.temperature, heatInfo.trend, heatInfo.sentiment);

    return {
      ...item,
      tag: heatInfo.tag,
      temperature: heatInfo.temperature,
      trend: heatInfo.trend,
      sentiment: Number(heatInfo.sentiment.toFixed(2)),
      action,
      actionLabel: actionLabel(action),
      score,
      riskScore: risk,
      riskLevel: riskLevel(risk),
      reason: reasonText(action, heatInfo)
    };
  });

  const order = { sell: 0, buy: 1, hold: 2 };
  plans.sort((a, b) => {
    if (order[a.action] !== order[b.action]) return order[a.action] - order[b.action];
    return b.riskScore - a.riskScore;
  });

  return plans;
}

function buildOverview(plans) {
  const buy = plans.filter((p) => p.action === 'buy').length;
  const sell = plans.filter((p) => p.action === 'sell').length;
  const hold = plans.filter((p) => p.action === 'hold').length;
  const avg = plans.length
    ? Math.round(plans.reduce((sum, p) => sum + p.score, 0) / plans.length)
    : 0;

  let label = '中性观望';
  if (avg >= 70) label = '积极偏多';
  else if (avg >= 58) label = '谨慎偏多';
  else if (avg < 40) label = '防御优先';

  return { buy, sell, hold, score: avg, label };
}

module.exports = {
  buildPlans,
  buildOverview
};
