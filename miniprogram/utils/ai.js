/**
 * AI 分析模块 — 调用硅基流动 / DeepSeek 等 OpenAI-compatible API
 */
const { getHoldings } = require('./storage');
const { analyzeTrend, computeVote } = require('./analyzer');
const { pickHeatForType } = require('./advisor');
const { todayStr, formatPct, isTradingDay } = require('./market');

/* ====== 提供商配置 ====== */
const AI_PROVIDERS = [
  {
    id: 'siliconflow', name: '硅基流动(免费)', free: true,
    base: 'https://api.siliconflow.cn/v1/chat/completions',
    models: [
      { id: 'deepseek-v3', name: 'DeepSeek-V3', model: 'deepseek-ai/DeepSeek-V3' },
      { id: 'qwen-72b', name: 'Qwen2.5-72B', model: 'Qwen/Qwen2.5-72B-Instruct' },
      { id: 'deepseek-r1', name: 'DeepSeek-R1-32B', model: 'deepseek-ai/DeepSeek-R1-Distill-Qwen-32B' },
    ],
    defaultModel: 'deepseek-ai/DeepSeek-V3',
  },
  {
    id: 'deepseek', name: 'DeepSeek官方', free: false,
    base: 'https://api.deepseek.com/chat/completions',
    models: [
      { id: 'deepseek-chat', name: 'DeepSeek-V3', model: 'deepseek-chat' },
      { id: 'deepseek-reasoner', name: 'DeepSeek-R1', model: 'deepseek-reasoner' },
    ],
    defaultModel: 'deepseek-chat',
  },
];

const AI_KEY = 'fa_mp_ai_key';
const AI_PROVIDER_KEY = 'fa_mp_ai_provider';
const AI_MODEL_KEY = 'fa_mp_ai_model';

/* ====== 存取 ====== */
function getAIConfig() {
  const providerId = wx.getStorageSync(AI_PROVIDER_KEY) || 'siliconflow';
  const key = wx.getStorageSync(AI_KEY) || '';
  const modelId = wx.getStorageSync(AI_MODEL_KEY) || '';
  const provider = AI_PROVIDERS.find(p => p.id === providerId) || AI_PROVIDERS[0];
  const model = modelId || provider.defaultModel;
  return { providerId, key, model, provider };
}

function setAIConfig({ providerId, key, model }) {
  if (providerId !== undefined) wx.setStorageSync(AI_PROVIDER_KEY, providerId);
  if (key !== undefined) wx.setStorageSync(AI_KEY, key);
  if (model !== undefined) wx.setStorageSync(AI_MODEL_KEY, model);
}

/* ====== 系统提示词 ====== */
const SYSTEM_PROMPT = `你是一位专业的基金波段操作+趋势跟踪投资顾问，精通宏观经济分析与板块轮动策略。

核心原则：
1. 顺趋势操作：上升趋势中回调买入，下降趋势中反弹减仓
2. 严格风控：单只基金仓位≤20%，总仓位40-80%
3. 基于数据说话，不做主观臆断
4. 结合大盘走势与宏观环境进行前瞻性判断

## 输出要求（JSON格式）：
{
  "marketSummary": "当前市场整体评估（2-3句话，包含大盘方向、板块轮动、资金面）",
  "marketOutlook": "未来1个月市场展望（3-5句话，包含趋势研判、风险提示、机会方向）",
  "riskLevel": "低风险|中风险|高风险",
  "marketTemperature": 0-100,
  "signals": [
    {
      "code": "基金代码",
      "name": "基金名称",
      "action": "buy|sell|hold",
      "urgency": "高|中|低",
      "confidence": 0-100,
      "reason": "30字以内的简短理由",
      "analysis": "80-150字的详细分析：包含该基金所属板块的近期走势、驱动因素、与大盘的联动关系、技术面信号、以及未来1个月的前瞻判断",
      "targetReturn": "预期收益区间如+3%~+8%或-5%~-2%",
      "riskWarning": "主要风险点（1-2句话）",
      "bestTiming": "最佳操作时机描述"
    }
  ],
  "recommendations": [
    {
      "code": "基金代码",
      "name": "基金名称",
      "type": "基金类型",
      "action": "strong_buy|buy",
      "confidence": 0-100,
      "reason": "推荐理由（30字以内）",
      "analysis": "详细推荐逻辑（80-150字）",
      "expectedReturn": "未来1个月预期收益如+5%~+10%",
      "riskNote": "风险提示",
      "catalyst": "上涨催化剂"
    }
  ],
  "sectorRotation": "板块轮动建议（2-3句话，当前应重配/轻配哪些方向）",
  "overallAdvice": "今日总体操作建议（3-5句话，含仓位控制建议）"
}

## 注意事项：
- 只输出JSON，不要其他文字
- action只能是buy/sell/hold三者之一
- confidence为0-100的整数
- signals覆盖用户所有持仓基金，每只都要给出详细analysis
- recommendations从候选基金库中选出3-5只最值得关注的基金（不要推荐用户已持仓的）
- 推荐基金要考虑：(1)当前市场环境 (2)板块轮动方向 (3)未来1个月催化剂 (4)风险收益比
- marketTemperature: 0=极度恐慌 50=中性 100=极度贪婪`;

/* ====== 构建上下文 ====== */
function buildContext(holdings, estimates, historyMap, indices, extras) {
  const today = todayStr();
  let ctx = `## 日期：${today}（${isTradingDay(today) ? '交易日' : '非交易日'}）\n\n`;

  // 指数行情
  if (indices && indices.length > 0) {
    ctx += `## 今日大盘指数\n`;
    indices.forEach(idx => {
      ctx += `- ${idx.name}: ${idx.price || '--'} (${idx.pctStr || '--'})\n`;
    });
    ctx += '\n';
  }

  // 大宗商品行情
  const commodities = extras && extras.commodities;
  if (commodities && commodities.length > 0) {
    ctx += `## 大宗商品行情\n`;
    commodities.forEach(c => {
      const tag = Math.abs(c.pct) >= 2 ? ' ⚠️异动' : '';
      ctx += `- ${c.icon || ''} ${c.name}: ${c.price || '--'} (${c.pctStr || '--'})${tag}\n`;
    });
    ctx += '\n';
  }

  // 板块热力
  const heatmap = extras && extras.heatmap;
  if (heatmap && heatmap.length > 0) {
    ctx += `## 板块热力图（温度0-100，越高越热）\n`;
    heatmap.forEach(h => {
      ctx += `- ${h.tag}: 温度${h.temperature}° 趋势${h.trend || '—'}\n`;
    });
    ctx += '\n';
  }

  // 热点事件
  const hotEvents = extras && extras.hotEvents;
  if (hotEvents && hotEvents.length > 0) {
    ctx += `## 近期热点事件\n`;
    hotEvents.slice(0, 8).forEach(ev => {
      ctx += `- [影响${ev.impact >= 0 ? '+' : ''}${ev.impact}] ${ev.title}`;
      if (ev.advice) ctx += ` → ${ev.advice}`;
      ctx += '\n';
    });
    ctx += '\n';
  }

  // 持仓基金分析
  ctx += `## 持仓基金（共${holdings.length}只）\n`;
  holdings.forEach(h => {
    const est = estimates ? estimates[h.code] : null;
    const navList = historyMap ? historyMap[h.code] : null;
    const td = navList ? analyzeTrend(navList) : null;
    const heatInfo = pickHeatForType(h.type, heatmap || []);
    const vote = td ? computeVote(td, heatInfo, null) : null;

    ctx += `\n### ${h.name}（${h.code}，${h.type}）\n`;
    if (est) {
      ctx += `- 今日估值: ${est.estimate || '--'}, 估算涨幅: ${formatPct(est.pct)}\n`;
    }
    if (td) {
      ctx += `- 趋势方向: ${td.trendDir}, 趋势得分: ${td.trendScore}\n`;
      ctx += `- 5日涨幅: ${td.chg5d ? td.chg5d.toFixed(2) + '%' : '--'}, 20日涨幅: ${td.chg20d ? td.chg20d.toFixed(2) + '%' : '--'}\n`;
      ctx += `- RSI: ${td.rsi ? td.rsi.toFixed(1) : '--'}, 波动率: ${td.vol20d ? td.vol20d.toFixed(2) + '%' : '--'}\n`;
      ctx += `- 最高回撤: ${td.drawdownFromHigh ? td.drawdownFromHigh.toFixed(2) + '%' : '--'}, 反弹幅度: ${td.reboundFromLow ? td.reboundFromLow.toFixed(2) + '%' : '--'}\n`;
      ctx += `- 均线状态: ${td.maStatus || '--'}, 波段位置: ${td.swingPos || '--'}\n`;
      ctx += `- 波段建议: ${td.swingAdvice || '--'}\n`;
    }
    if (heatInfo) {
      ctx += `- 板块热度: ${heatInfo.temperature}°, 板块趋势: ${heatInfo.trend}\n`;
    }
    if (vote) {
      ctx += `- 算法投票: ${vote.label}（得分${vote.score}，置信度${vote.confidence}）\n`;
    }
  });

  // 候选基金库（用于AI推荐）
  const fundDB = extras && extras.fundDB;
  if (fundDB) {
    const holdingCodes = new Set(holdings.map(h => h.code));
    const candidates = Object.entries(fundDB).filter(([code]) => !holdingCodes.has(code));
    if (candidates.length > 0) {
      ctx += `\n## 候选基金库（可供推荐，用户未持仓）\n`;
      candidates.forEach(([code, info]) => {
        ctx += `- ${info.name}（${code}，${info.type}）\n`;
      });
    }
  }

  return ctx;
}

/* ====== 调用 AI ====== */
function callAI(apiBase, apiKey, model, systemPrompt, userPrompt, temperature = 0.7) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: apiBase,
      method: 'POST',
      timeout: 120000, // 2分钟超时，AI深度分析需要较长时间
      header: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + apiKey,
      },
      data: {
        model,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
        temperature,
        max_tokens: 4096,
      },
      success(res) {
        if (res.statusCode !== 200) {
          reject(new Error(`API错误 ${res.statusCode}: ${JSON.stringify(res.data)}`));
          return;
        }
        const content = res.data && res.data.choices && res.data.choices[0] && res.data.choices[0].message
          ? res.data.choices[0].message.content : '';
        resolve(content);
      },
      fail(err) {
        reject(new Error('网络请求失败: ' + (err.errMsg || err.message || '')));
      },
    });
  });
}

/* ====== 解析 AI 返回的 JSON ====== */
function parseAIResponse(raw) {
  if (!raw) return null;
  // 去掉 <think>...</think> 块
  let cleaned = raw.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
  // 去掉 markdown 代码块
  cleaned = cleaned.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
  try {
    return JSON.parse(cleaned);
  } catch (e) {
    // 尝试提取第一个 { ... }
    const m = cleaned.match(/\{[\s\S]*\}/);
    if (m) {
      try { return JSON.parse(m[0]); } catch (_) {}
    }
    return null;
  }
}

/* ====== 主入口：运行 AI 分析 ====== */
async function runAIAnalysis({ holdings, estimates, historyMap, indices, commodities, heatmap, hotEvents, fundDB }) {
  const config = getAIConfig();
  if (!config.key) throw new Error('请先在设置中配置 AI API Key');

  const context = buildContext(holdings, estimates, historyMap, indices, {
    commodities, heatmap, hotEvents, fundDB,
  });
  const userPrompt = context + `

请基于以上全部数据（大盘指数、大宗商品、板块热力、热点事件、持仓基金技术面）和你的专业知识：

1. 对每只持仓基金给出详细的AI分析（含analysis字段，80-150字深度分析），包括：
   - 该基金所属板块的近期行情与驱动因素
   - 与大盘指数/大宗商品的联动关系
   - 技术面信号解读
   - 未来1个月的方向性判断与操作建议

2. 从候选基金库中，基于当前大盘趋势和未来1个月的前瞻研判，推荐3-5只最值得入手的基金（recommendations），考虑：
   - 当前市场风格轮动（成长vs价值、大盘vs中小盘）
   - 政策催化与事件驱动
   - 风险收益比与回撤保护
   - 与现有持仓的互补性

3. 给出未来1个月的市场展望（marketOutlook），以及板块轮动建议（sectorRotation）`;

  const raw = await callAI(
    config.provider.base,
    config.key,
    config.model,
    SYSTEM_PROMPT,
    userPrompt,
    0.7
  );

  const result = parseAIResponse(raw);
  if (!result) throw new Error('AI 返回格式异常，请重试');

  // 缓存结果
  wx.setStorageSync('fa_mp_ai_result', {
    date: todayStr(),
    timestamp: new Date().toISOString(),
    result,
    raw,
  });

  return result;
}

/* ====== 获取缓存的 AI 结果 ====== */
function getCachedAIResult() {
  const cached = wx.getStorageSync('fa_mp_ai_result');
  if (cached && cached.date === todayStr()) return cached;
  return null;
}

module.exports = {
  AI_PROVIDERS,
  getAIConfig,
  setAIConfig,
  runAIAnalysis,
  getCachedAIResult,
  callAI,
};
