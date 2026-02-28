/**
 * 技术分析引擎 — 趋势/RSI/波动率/波段/多因子投票
 * 移植自 H5 index.html 的 analyzeTrend / computeTriEngineVote
 */

/* ====== 移动平均 ====== */
function calcMA(prices, period) {
  const result = [];
  for (let i = 0; i < prices.length; i++) {
    if (i < period - 1) { result.push(null); continue; }
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += prices[j];
    result.push(sum / period);
  }
  return result;
}

/* ====== RSI (Wilder) ====== */
function calcRSI(prices, period) {
  if (prices.length < period + 1) return [];
  const rsi = new Array(prices.length).fill(null);
  let gainSum = 0, lossSum = 0;
  for (let i = 1; i <= period; i++) {
    const diff = prices[i] - prices[i - 1];
    if (diff > 0) gainSum += diff; else lossSum -= diff;
  }
  let avgGain = gainSum / period;
  let avgLoss = lossSum / period;
  rsi[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < prices.length; i++) {
    const diff = prices[i] - prices[i - 1];
    avgGain = (avgGain * (period - 1) + (diff > 0 ? diff : 0)) / period;
    avgLoss = (avgLoss * (period - 1) + (diff < 0 ? -diff : 0)) / period;
    rsi[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return rsi;
}

/* ====== 波动率 (20日年化) ====== */
function calcVolatility(prices) {
  if (prices.length < 21) return 0;
  const recent = prices.slice(-21);
  const returns = [];
  for (let i = 1; i < recent.length; i++) {
    returns.push((recent[i] - recent[i - 1]) / recent[i - 1]);
  }
  const mean = returns.reduce((s, r) => s + r, 0) / returns.length;
  const variance = returns.reduce((s, r) => s + (r - mean) ** 2, 0) / returns.length;
  return Math.sqrt(variance) * Math.sqrt(250) * 100; // 年化 %
}

/* ====== 趋势分析（核心） ====== */
function analyzeTrend(navList) {
  // navList: [{date, nav}] 按时间升序
  if (!navList || navList.length < 5) return null;

  const prices = navList.map(n => n.nav);
  const latest = prices[prices.length - 1];
  const len = prices.length;

  // 涨跌幅
  const chg = (n) => len > n ? ((latest - prices[len - 1 - n]) / prices[len - 1 - n] * 100) : null;
  const chg5d = chg(5);
  const chg20d = chg(20);
  const chg60d = chg(60);
  const chg120d = chg(120);
  const chg250d = chg(250);

  // 均线
  const ma5arr = calcMA(prices, 5);
  const ma20arr = calcMA(prices, 20);
  const ma60arr = calcMA(prices, 60);
  const ma5 = ma5arr[len - 1];
  const ma20 = ma20arr[len - 1];
  const ma60 = ma60arr[len - 1];

  // RSI
  const rsiArr = calcRSI(prices, 14);
  const rsi = rsiArr[rsiArr.length - 1] || 50;

  // 高低点
  const high1y = Math.max(...prices);
  const low1y = Math.min(...prices);
  const drawdownFromHigh = ((latest - high1y) / high1y * 100);
  const reboundFromLow = ((latest - low1y) / low1y * 100);

  // 波动率
  const volatility = calcVolatility(prices);

  // === 趋势方向评分 ===
  let trendScore = 0;
  // MA排列
  if (ma5 && ma20 && ma60) {
    if (latest > ma5 && ma5 > ma20 && ma20 > ma60) trendScore += 30;
    else if (latest < ma5 && ma5 < ma20 && ma20 < ma60) trendScore -= 30;
  }
  // 60日动量
  if (chg60d !== null) {
    if (chg60d > 10) trendScore += 25;
    else if (chg60d > 3) trendScore += 15;
    else if (chg60d < -10) trendScore -= 25;
    else if (chg60d < -3) trendScore -= 15;
  }
  // 250日动量
  if (chg250d !== null) {
    if (chg250d > 15) trendScore += 20;
    else if (chg250d < -15) trendScore -= 20;
  }
  // 价格位置
  if (ma20 && latest > ma20 * 1.02) trendScore += 10;
  if (ma20 && latest < ma20 * 0.98) trendScore -= 10;

  let trendDir;
  if (trendScore >= 25) trendDir = 'strong_up';
  else if (trendScore >= 10) trendDir = 'up';
  else if (trendScore <= -25) trendDir = 'strong_down';
  else if (trendScore <= -10) trendDir = 'down';
  else trendDir = 'sideways';

  // === 波段位置 ===
  let swingPos;
  if (chg5d !== null) {
    if (chg5d <= -3) swingPos = 'deep_dip';
    else if (chg5d <= -1.5) swingPos = 'dip';
    else if (chg5d >= 3) swingPos = 'surge';
    else if (chg5d >= 1.5) swingPos = 'rally';
    else swingPos = 'mid';
  } else {
    swingPos = 'mid';
  }

  // === MA 状态 ===
  let maStatus = '交织';
  if (ma5 && ma20 && ma60) {
    if (latest > ma5 && ma5 > ma20 && ma20 > ma60) maStatus = '多头排列';
    else if (latest < ma5 && ma5 < ma20 && ma20 < ma60) maStatus = '空头排列';
  }

  // === 波段建议 (trendDir × swingPos) ===
  let swingAdvice = '观望';
  if (trendDir === 'strong_up' || trendDir === 'up') {
    if (swingPos === 'deep_dip' || swingPos === 'dip') swingAdvice = '波段买入机会';
    else if (swingPos === 'surge') swingAdvice = '冲顶止盈';
    else if (swingPos === 'rally') swingAdvice = '加速持有';
    else swingAdvice = '趋势持有';
  } else if (trendDir === 'strong_down' || trendDir === 'down') {
    if (swingPos === 'rally' || swingPos === 'surge') swingAdvice = '反弹减仓';
    else if (swingPos === 'deep_dip') swingAdvice = '超跌勿追';
    else swingAdvice = '暂避风险';
  } else {
    if (swingPos === 'deep_dip') swingAdvice = '低吸机会';
    else if (swingPos === 'surge') swingAdvice = '冲高减仓';
    else swingAdvice = '震荡观望';
  }

  return {
    latest, chg5d, chg20d, chg60d, chg120d, chg250d,
    ma5, ma20, ma60, rsi, volatility,
    high1y, low1y, drawdownFromHigh, reboundFromLow,
    trendDir, trendScore, swingPos, maStatus, swingAdvice,
  };
}

/* ====== 趋势标签映射 ====== */
function getTrendLabel(td) {
  if (!td) return { dirIcon: '—', dirText: '数据不足', dirColor: '#8b8fa3', swingIcon: '—', swingText: '—', swingColor: '#8b8fa3' };

  const DIR_MAP = {
    strong_up:   { icon: '🔥', text: '强势上攻', color: '#ef4444' },
    up:          { icon: '📈', text: '趋势向上', color: '#f97316' },
    sideways:    { icon: '↔️', text: '横盘震荡', color: '#eab308' },
    down:        { icon: '📉', text: '趋势走弱', color: '#22c55e' },
    strong_down: { icon: '⚠️', text: '强势下跌', color: '#16a34a' },
  };
  const SWING_MAP = {
    deep_dip: { icon: '💎', text: '深度回调', color: '#22c55e' },
    dip:      { icon: '🔻', text: '短期回调', color: '#4ade80' },
    mid:      { icon: '●',  text: '中位运行', color: '#eab308' },
    rally:    { icon: '🔺', text: '短期反弹', color: '#fb923c' },
    surge:    { icon: '🚀', text: '短期冲高', color: '#ef4444' },
  };

  const dir = DIR_MAP[td.trendDir] || DIR_MAP.sideways;
  const swing = SWING_MAP[td.swingPos] || SWING_MAP.mid;
  return {
    dirIcon: dir.icon, dirText: dir.text, dirColor: dir.color,
    swingIcon: swing.icon, swingText: swing.text, swingColor: swing.color,
    maStatus: td.maStatus,
  };
}

/* ====== 估值分桶 ====== */
function valuationBucket(td, heat) {
  if (!td) return { label: '数据不足', color: '#8b8fa3' };
  if (td.drawdownFromHigh <= -25 && heat <= 60)
    return { label: '极度低估', color: '#22c55e' };
  if (td.drawdownFromHigh <= -15 && heat <= 50)
    return { label: '明显低估', color: '#4ade80' };
  if (td.drawdownFromHigh >= -8 || heat >= 75)
    return { label: '估值偏高', color: '#ef4444' };
  return { label: '估值合理', color: '#eab308' };
}

/* ====== 趋势分桶 ====== */
function trendBucket(td) {
  if (!td) return { label: '数据不足', color: '#8b8fa3' };
  if (td.trendDir === 'strong_down' || td.trendDir === 'down')
    return { label: '跌跌不休', color: '#22c55e' };
  if (td.trendDir === 'strong_up' || td.trendDir === 'up')
    return { label: '强势上涨', color: '#ef4444' };
  return { label: '筑底震荡', color: '#eab308' };
}

/* ====== 简化三因子投票 (BT + 趋势 + 板块) ====== */
function computeVote(td, heatInfo, sectorFlow) {
  if (!td) return { action: 'hold', label: '持有观望', confidence: 30, score: 0, factors: [] };

  const factors = [];
  let score = 0;
  let totalWeight = 0;

  // --- 因子1: RSI + 回测指标 (权重 35%) ---
  let btVote = 0, btConf = 50;
  if (td.rsi < 30) { btVote = 1; btConf = 85; factors.push({ name: 'RSI超卖', val: td.rsi.toFixed(0), dir: 'buy' }); }
  else if (td.rsi < 35) { btVote = 1; btConf = 70; factors.push({ name: 'RSI偏低', val: td.rsi.toFixed(0), dir: 'buy' }); }
  else if (td.rsi > 80) { btVote = -1; btConf = 85; factors.push({ name: 'RSI超买', val: td.rsi.toFixed(0), dir: 'sell' }); }
  else if (td.rsi > 75) { btVote = -1; btConf = 70; factors.push({ name: 'RSI偏高', val: td.rsi.toFixed(0), dir: 'sell' }); }
  else { btConf = 40; factors.push({ name: 'RSI中性', val: td.rsi.toFixed(0), dir: 'hold' }); }
  score += btVote * (btConf / 100) * 0.35;
  totalWeight += 0.35;

  // --- 因子2: 趋势动量 (权重 40%) ---
  let trendVote = 0, trendConf = 50;
  if (td.trendDir === 'strong_up') { trendVote = 1; trendConf = 80; }
  else if (td.trendDir === 'up') { trendVote = 1; trendConf = 65; }
  else if (td.trendDir === 'strong_down') { trendVote = -1; trendConf = 80; }
  else if (td.trendDir === 'down') { trendVote = -1; trendConf = 65; }
  else { trendConf = 40; }
  factors.push({ name: '趋势', val: getTrendLabel(td).dirText, dir: trendVote > 0 ? 'buy' : trendVote < 0 ? 'sell' : 'hold' });
  score += trendVote * (trendConf / 100) * 0.40;
  totalWeight += 0.40;

  // --- 因子3: 板块热度 (权重 25%) ---
  let heatVote = 0, heatConf = 50;
  const temp = heatInfo ? heatInfo.temperature : 50;
  if (temp >= 72 && heatInfo.trend !== 'down') { heatVote = 1; heatConf = 70; }
  else if (temp <= 35) { heatVote = -1; heatConf = 70; }
  else if (temp <= 46 || (heatInfo && heatInfo.trend === 'down')) { heatVote = -1; heatConf = 60; }
  else { heatConf = 40; }
  factors.push({ name: '板块热度', val: temp + '°', dir: heatVote > 0 ? 'buy' : heatVote < 0 ? 'sell' : 'hold' });
  score += heatVote * (heatConf / 100) * 0.25;
  totalWeight += 0.25;

  // --- 修正: 波段止盈抑制 ---
  if (td.swingPos === 'surge' && td.chg5d >= 3) { score -= 0.15; factors.push({ name: '冲高止盈', val: '+' + td.chg5d.toFixed(1) + '%', dir: 'sell' }); }
  if (td.drawdownFromHigh > -3 && td.chg20d > 10) { score -= 0.1; factors.push({ name: '距高点近', val: td.drawdownFromHigh.toFixed(1) + '%', dir: 'sell' }); }

  // --- 修正: 深度回调加分 ---
  if (td.swingPos === 'deep_dip' && td.trendDir !== 'strong_down') {
    score += 0.1;
    factors.push({ name: '深度回调', val: td.chg5d.toFixed(1) + '%', dir: 'buy' });
  }

  // --- 修正: 板块资金流 ---
  if (sectorFlow) {
    const net = sectorFlow.mainNet;
    if (net > 5e8) { score += 0.04; factors.push({ name: '板块资金流入', val: (net / 1e8).toFixed(1) + '亿', dir: 'buy' }); }
    else if (net < -5e8) { score -= 0.04; factors.push({ name: '板块资金流出', val: (net / 1e8).toFixed(1) + '亿', dir: 'sell' }); }
  }

  // --- 反拥挤 ---
  let crowding = '';
  if (temp > 80 && td.rsi > 70) { score *= 0.6; crowding = '过热拥挤'; }
  else if (temp < 20 && td.rsi < 30) { score *= 1.3; crowding = '逆向机会'; }

  // --- 最终决策 ---
  const finalScore = totalWeight > 0 ? score / totalWeight : 0;
  const effConf = Math.min(95, Math.round(Math.abs(finalScore) * 100 + 30));

  let action, label;
  if (finalScore > 0.18 && effConf >= 50) { action = 'buy'; label = finalScore > 0.35 ? '建议加仓' : '偏多持有'; }
  else if (finalScore < -0.18 && effConf >= 45) { action = 'sell'; label = finalScore < -0.35 ? '建议减仓' : '偏空持有'; }
  else { action = 'hold'; label = '持有观望'; }

  // 投票统计
  const buyVotes = factors.filter(f => f.dir === 'buy').length;
  const sellVotes = factors.filter(f => f.dir === 'sell').length;
  let consensus;
  if (buyVotes >= 3 && sellVotes === 0) consensus = '共识看多';
  else if (sellVotes >= 3 && buyVotes === 0) consensus = '共识看空';
  else if (buyVotes > sellVotes) consensus = '偏多';
  else if (sellVotes > buyVotes) consensus = '偏空';
  else consensus = '分歧';

  return {
    action, label, confidence: effConf, score: finalScore,
    buyVotes, sellVotes, consensus, crowding,
    factors,
    swingAdvice: td.swingAdvice,
  };
}

/* ====== 白话研判 ====== */
function buildPlainAdvisor(fund, td, heatInfo, vote) {
  if (!td) {
    return {
      code: fund.code, name: fund.name, type: fund.type,
      riskScore: 50, riskLevel: '数据不足', valuation: { label: '--', color: '#8b8fa3' },
      trendLabel: { label: '--', color: '#8b8fa3' }, windDir: '未知',
      biggestRisk: '数据不足', tldr: '暂无足够数据进行分析',
      operation: '观望', tactics: '等待更多数据', stopLoss: '--',
      radar: { valuation: 50, momentum: 50, macro: 50, defense: 50, sentiment: 50 },
    };
  }

  const heat = heatInfo ? heatInfo.temperature : 50;
  const val = valuationBucket(td, heat);
  const trB = trendBucket(td);

  // 风向
  let windDir = '混沌';
  if (heatInfo && heatInfo.sentiment > 0.3 && heat >= 60) windDir = '顺风';
  else if (heatInfo && (heatInfo.sentiment < -0.2 || heat < 35)) windDir = '逆风';

  // 最大隐患
  let biggestRisk = '暂无明显风险';
  if (heat >= 80) biggestRisk = '赛道拥挤，小心踩踏';
  else if (windDir === '逆风') biggestRisk = '情绪/宏观逆风';
  else if (td.volatility > 30) biggestRisk = '波动率偏高';
  else if (td.drawdownFromHigh < -20) biggestRisk = '深度套牢区';

  // 风险分数
  let riskScore = 0;
  if (heat >= 80) riskScore += 30; else if (heat >= 65) riskScore += 15;
  if (td.trendDir === 'strong_down' || td.trendDir === 'down') riskScore += 25;
  if (td.volatility > 25) riskScore += 15; else if (td.volatility > 18) riskScore += 8;
  if (td.drawdownFromHigh < -20) riskScore += 15;
  if (heatInfo && heatInfo.sentiment < -0.3) riskScore += 15;
  riskScore = Math.min(100, riskScore);
  const riskLevel = riskScore >= 55 ? '高风险' : riskScore >= 30 ? '中风险' : '低风险';

  // 一句话诊断
  let tldr;
  if (val.label.includes('低估') && trB.label !== '跌跌不休') tldr = '便宜区间，可慢慢买';
  else if (val.label === '估值偏高' && heat >= 75) tldr = '热度偏高，防回调';
  else if (trB.label === '跌跌不休') tldr = '下行未止，先观望';
  else if (trB.label === '强势上涨') tldr = '趋势好，顺势持有';
  else tldr = '不上不下，耐心等待方向';

  // 操作建议
  let operation, tactics, stopLoss;
  if (vote.action === 'buy') {
    operation = '定投 / 分批买入';
    tactics = `当前趋势偏强，可分2-3次建仓。5日涨跌${td.chg5d ? td.chg5d.toFixed(1) : '--'}%，回调时优先加仓。`;
    stopLoss = td.ma20 ? `MA20: ${td.ma20.toFixed(4)}` : '跌破5日最低-5%';
  } else if (vote.action === 'sell') {
    operation = '减仓 / 暂停定投';
    tactics = `趋势或估值偏弱，建议逢高分批减仓。`;
    stopLoss = td.ma20 ? `MA20: ${td.ma20.toFixed(4)}` : '跌破-5%止损';
  } else {
    operation = '持有观望';
    tactics = `方向不明朗，保持现有仓位。关注MA20和板块热度变化。`;
    stopLoss = td.ma20 ? `MA20: ${td.ma20.toFixed(4)}` : '设定-5%止损';
  }

  // 雷达图数据 (0-100)
  const radar = {
    valuation: val.label.includes('低估') ? 85 : val.label === '估值偏高' ? 25 : 55,
    momentum: td.trendDir === 'strong_up' ? 90 : td.trendDir === 'up' ? 70 : td.trendDir === 'down' ? 30 : td.trendDir === 'strong_down' ? 15 : 50,
    macro: windDir === '顺风' ? 80 : windDir === '逆风' ? 25 : 50,
    defense: td.volatility > 30 ? 20 : td.volatility > 20 ? 45 : 75,
    sentiment: heat >= 70 ? Math.min(90, heat) : heat <= 30 ? Math.max(10, heat) : 50,
  };

  return {
    code: fund.code, name: fund.name, type: fund.type,
    riskScore, riskLevel, valuation: val, trendLabel: trB, windDir,
    biggestRisk, tldr, operation, tactics, stopLoss, radar,
    // 原始数据展示
    rawData: {
      todayPct: fund.pctStr || '--',
      chg20d: td.chg20d ? td.chg20d.toFixed(1) + '%' : '--',
      drawdown: td.drawdownFromHigh ? td.drawdownFromHigh.toFixed(1) + '%' : '--',
      volatility: td.volatility ? td.volatility.toFixed(1) + '%' : '--',
      rsi: td.rsi ? td.rsi.toFixed(0) : '--',
      heat: heat + '°',
    },
  };
}

module.exports = {
  calcMA,
  calcRSI,
  calcVolatility,
  analyzeTrend,
  getTrendLabel,
  valuationBucket,
  trendBucket,
  computeVote,
  buildPlainAdvisor,
};
