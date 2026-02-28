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
const SYSTEM_PROMPT = `你是一位专业的基金波段操作+趋势跟踪投资顾问。

核心原则：
1. 顺趋势操作：上升趋势中回调买入，下降趋势中反弹减仓
2. 严格风控：单只基金仓位≤20%，总仓位40-80%
3. 基于数据说话，不做主观臆断

输出要求（JSON格式）：
{
  "marketSummary": "一句话市场概述",
  "riskLevel": "低风险|中风险|高风险",
  "signals": [
    {
      "code": "基金代码",
      "name": "基金名称",
      "action": "buy|sell|hold",
      "urgency": "高|中|低",
      "confidence": 0-100,
      "reason": "简短理由"
    }
  ],
  "overallAdvice": "今日总体操作建议（2-3句话）"
}

注意事项：
- 只输出JSON，不要其他文字
- action只能是buy/sell/hold三者之一
- confidence为0-100的整数
- reason不超过30字`;

/* ====== 构建上下文 ====== */
function buildContext(holdings, estimates, historyMap, indices) {
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

  // 持仓基金分析
  ctx += `## 持仓基金（共${holdings.length}只）\n`;
  holdings.forEach(h => {
    const est = estimates ? estimates[h.code] : null;
    const navList = historyMap ? historyMap[h.code] : null;
    const td = navList ? analyzeTrend(navList) : null;
    const heatInfo = pickHeatForType(h.type, []);
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
    if (vote) {
      ctx += `- 算法投票: ${vote.label}（得分${vote.score}，置信度${vote.confidence}）\n`;
    }
  });

  return ctx;
}

/* ====== 调用 AI ====== */
function callAI(apiBase, apiKey, model, systemPrompt, userPrompt, temperature = 0.7) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: apiBase,
      method: 'POST',
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
async function runAIAnalysis({ holdings, estimates, historyMap, indices }) {
  const config = getAIConfig();
  if (!config.key) throw new Error('请先在设置中配置 AI API Key');

  const context = buildContext(holdings, estimates, historyMap, indices);
  const userPrompt = context + '\n\n请基于以上数据和你的专业知识，给出每只基金的操作建议。';

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
};
