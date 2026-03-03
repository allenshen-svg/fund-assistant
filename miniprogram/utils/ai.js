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
    id: 'zhipu', name: '智谱AI(免费)', free: true,
    base: 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
    models: [
      { id: 'glm-4-flash', name: 'GLM-4-Flash(免费)', model: 'glm-4-flash' },
      { id: 'glm-4-air', name: 'GLM-4-Air', model: 'glm-4-air' },
      { id: 'glm-4-plus', name: 'GLM-4-Plus', model: 'glm-4-plus' },
    ],
    defaultModel: 'glm-4-flash',
  },
  {
    id: 'siliconflow', name: '硅基流动', free: true,
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
    base: 'https://api.deepseek.com/v1/chat/completions',
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
  const providerId = wx.getStorageSync(AI_PROVIDER_KEY) || 'zhipu';
  const key = wx.getStorageSync(AI_KEY) || '';
  const modelId = wx.getStorageSync(AI_MODEL_KEY) || '';
  const provider = AI_PROVIDERS.find(p => p.id === providerId) || AI_PROVIDERS[0];
  // 确保 model 属于当前 provider，否则回退到默认模型
  const validModel = provider.models.some(m => m.model === modelId);
  const model = validModel ? modelId : provider.defaultModel;
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
- **【最重要】signals 必须覆盖用户的每一只持仓基金，一只都不能遗漏！** 用户持仓列表中有几只基金，signals 数组就必须有几个元素，每个元素的 code 必须与持仓基金代码一一对应
- 如果某只基金数据不足，也必须给出 hold 建议并在 analysis 中说明原因
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

  // 热点事件 (大持仓时限制条数减少token)
  const hotEvents = extras && extras.hotEvents;
  const maxEvents = holdings.length >= 8 ? 5 : 8;
  if (hotEvents && hotEvents.length > 0) {
    ctx += `## 近期热点事件\n`;
    hotEvents.slice(0, maxEvents).forEach(ev => {
      ctx += `- [影响${ev.impact >= 0 ? '+' : ''}${ev.impact}] ${ev.title}`;
      if (ev.advice) ctx += ` → ${ev.advice}`;
      ctx += '\n';
    });
    ctx += '\n';
  }

  // 持仓基金分析
  const isLargePortfolio = holdings.length >= 8;
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
      if (!isLargePortfolio) {
        ctx += `- 最高回撤: ${td.drawdownFromHigh ? td.drawdownFromHigh.toFixed(2) + '%' : '--'}, 反弹幅度: ${td.reboundFromLow ? td.reboundFromLow.toFixed(2) + '%' : '--'}\n`;
        ctx += `- 均线状态: ${td.maStatus || '--'}, 波段位置: ${td.swingPos || '--'}\n`;
      }
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
function _doRequest(apiBase, apiKey, model, reqData, timeoutMs) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: apiBase,
      method: 'POST',
      timeout: timeoutMs,
      header: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + apiKey,
      },
      data: reqData,
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
        const msg = err.errMsg || err.message || '';
        if (/timeout/i.test(msg)) {
          reject(new Error('timeout'));
        } else {
          reject(new Error('网络请求失败: ' + msg));
        }
      },
    });
  });
}

async function callAI(apiBase, apiKey, model, systemPrompt, userPrompt, temperature = 0.7) {
  // 构建请求体
  const reqData = {
    model,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userPrompt },
    ],
    temperature,
    max_tokens: 8192,
  };
  // 智谱 / 硅基流动 / DeepSeek-V3 支持 response_format 强制 JSON
  // 避免 R1 推理模型使用 response_format（不兼容）
  const isReasoner = /reasoner|r1/i.test(model);
  if (!isReasoner) {
    reqData.response_format = { type: 'json_object' };
  }

  const TIMEOUT_MS = 180000; // 3分钟超时
  const MAX_RETRIES = 1;     // 超时自动重试1次

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      console.log(`[AI] 请求第${attempt + 1}次, timeout=${TIMEOUT_MS / 1000}s`);
      const content = await _doRequest(apiBase, apiKey, model, reqData, TIMEOUT_MS);
      return content;
    } catch (e) {
      if (e.message === 'timeout' && attempt < MAX_RETRIES) {
        console.warn(`[AI] 第${attempt + 1}次请求超时，自动重试...`);
        continue;
      }
      throw e;
    }
  }
}

/* ====== 清理 JSON 字符串中的常见问题 ====== */
function _cleanJsonStr(s) {
  // 去掉 <think>...</think> 块（DeepSeek-R1 推理过程）
  s = s.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
  // 去掉 markdown 代码块标记
  s = s.replace(/```json\s*/gi, '').replace(/```\s*/g, '').trim();
  // 去掉行首 JSON 之前的说明文字（如 "以下是分析结果："）
  const firstBrace = s.indexOf('{');
  if (firstBrace > 0) {
    const prefix = s.slice(0, firstBrace).trim();
    // 如果前缀不含 { 或 [ 说明是纯文字前缀，可以去掉
    if (!/[\{\[]/.test(prefix)) {
      s = s.slice(firstBrace);
    }
  }
  // 去掉不可见控制字符（保留 \n \r \t）
  s = s.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F]/g, '');
  // 修复常见 JSON 语法问题
  s = s.replace(/,\s*([\]\}])/g, '$1');       // 尾逗号
  s = s.replace(/([\{\[,])\s*,/g, '$1');       // 连续逗号
  s = s.replace(/\\'/g, "'");                  // 错误转义单引号
  return s;
}

/* ====== 解析 AI 返回的 JSON ====== */
function parseAIResponse(raw) {
  if (!raw) return null;
  let cleaned = _cleanJsonStr(raw);

  // 策略1: 直接解析
  try {
    return JSON.parse(cleaned);
  } catch (e) {
    console.warn('[AI] 策略1 直接解析失败:', e.message);
  }

  // 策略2: 提取第一个 { ... }（贪婪匹配最外层）
  const m = cleaned.match(/\{[\s\S]*\}/);
  if (m) {
    try { return JSON.parse(m[0]); } catch (_) {
      // 也做一次清理
      try { return JSON.parse(_cleanJsonStr(m[0])); } catch (__) {
        console.warn('[AI] 策略2 提取JSON对象失败');
      }
    }
  }

  // 策略3: 括号补全修复截断的 JSON
  const jsonStart = cleaned.indexOf('{');
  if (jsonStart >= 0) {
    const lastBrace = cleaned.lastIndexOf('}');
    if (lastBrace > jsonStart) {
      let truncated = cleaned.slice(jsonStart, lastBrace + 1);
      truncated = _cleanJsonStr(truncated);
      let openBraces = 0, openBrackets = 0;
      for (const ch of truncated) {
        if (ch === '{') openBraces++;
        else if (ch === '}') openBraces--;
        else if (ch === '[') openBrackets++;
        else if (ch === ']') openBrackets--;
      }
      while (openBrackets > 0) { truncated += ']'; openBrackets--; }
      while (openBraces > 0) { truncated += '}'; openBraces--; }
      try {
        const obj = JSON.parse(truncated);
        console.warn('[AI] 策略3 括号补全成功');
        return obj;
      } catch (_) {}
    }

    // 策略4: 暴力补全（从 jsonStart 开始逐步加括号）
    let partial = cleaned.slice(jsonStart);
    partial = _cleanJsonStr(partial);
    const suffixes = [']}]}', ']}', ']}}}', '}}', '}'];
    for (const suf of suffixes) {
      try {
        const obj = JSON.parse(partial + suf);
        console.warn('[AI] 策略4 暴力补全成功:', suf);
        return obj;
      } catch (_) {}
    }
    // 策略5: 去掉最后一个不完整的数组元素后再补全
    // 找最后一个 },{ 或 },\n{ 的位置
    const lastObjSep = Math.max(
      partial.lastIndexOf('},{'),
      partial.lastIndexOf('},\n{'),
      partial.lastIndexOf('},\r\n{')
    );
    if (lastObjSep > 0) {
      let cut = partial.slice(0, lastObjSep + 1); // 保留到 } 为止
      let ob = 0, obk = 0;
      for (const ch of cut) {
        if (ch === '{') ob++; else if (ch === '}') ob--;
        if (ch === '[') obk++; else if (ch === ']') obk--;
      }
      while (obk > 0) { cut += ']'; obk--; }
      while (ob > 0) { cut += '}'; ob--; }
      try {
        const obj = JSON.parse(cut);
        console.warn('[AI] 策略5 截断尾部补全成功');
        return obj;
      } catch (_) {}
    }
  }

  console.error('[AI] 所有解析策略均失败');
  return null;
}

/* ====== 主入口：运行 AI 分析 ====== */
async function runAIAnalysis({ holdings, estimates, historyMap, indices, commodities, heatmap, hotEvents, fundDB }) {
  const config = getAIConfig();
  if (!config.key) throw new Error('请先在设置中配置 AI API Key');

  const context = buildContext(holdings, estimates, historyMap, indices, {
    commodities, heatmap, hotEvents, fundDB,
  });
  // 根据持仓数量动态调整提示词的详细程度，减少输出token数
  const isLarge = holdings.length >= 8;
  const analysisHint = isLarge
    ? '含analysis字段，40-80字简明分析'
    : '含analysis字段，80-150字深度分析';

  const userPrompt = context + `

⚠️ 重要提醒：用户共持有 ${holdings.length} 只基金，代码分别为：${holdings.map(h => h.code).join('、')}。
signals 数组必须包含这 ${holdings.length} 只基金的分析结果，不可遗漏任何一只！

请基于以上全部数据和你的专业知识：

1. 对每只持仓基金给出AI分析（${analysisHint}），包括板块行情、技术面信号、操作建议

2. 从候选基金库中推荐2-3只值得入手的基金（recommendations）

3. 给出市场展望（marketOutlook）和板块轮动建议（sectorRotation）

再次强调：signals 必须包含全部 ${holdings.length} 只基金：${holdings.map(h => `${h.name}(${h.code})`).join('、')}
请用中文回复。`;

  const raw = await callAI(
    config.provider.base,
    config.key,
    config.model,
    SYSTEM_PROMPT,
    userPrompt,
    0.7
  );

  const result = parseAIResponse(raw);
  if (!result) {
    // 保存原始响应以便debug
    console.error('[AI] 解析失败，原始nraw长度:', raw ? raw.length : 0);
    console.error('[AI] raw前500字符:', raw ? raw.substring(0, 500) : '(empty)');
    console.error('[AI] raw后500字符:', raw ? raw.substring(Math.max(0, raw.length - 500)) : '(empty)');
    wx.setStorageSync('fa_mp_ai_debug', {
      date: todayStr(),
      rawLength: raw ? raw.length : 0,
      rawHead: raw ? raw.substring(0, 1000) : '',
      rawTail: raw ? raw.substring(Math.max(0, raw.length - 500)) : '',
    });
    const hint = raw && raw.length > 7000
      ? 'AI输出过长被截断，建议减少持仓数量或切换模型后重试'
      : 'AI返回内容无法解析为JSON，请重试或切换模型';
    throw new Error(hint);
  }

  // ====== 自动补全：检查并填充 AI 遗漏的基金 ======
  if (result.signals && holdings.length > 0) {
    const coveredCodes = new Set(result.signals.map(s => s.code));
    const missing = holdings.filter(h => !coveredCodes.has(h.code));
    if (missing.length > 0) {
      console.warn(`[AI] AI遗漏了 ${missing.length} 只基金，自动补全: ${missing.map(m => m.code).join(',')}`);
      missing.forEach(h => {
        const est = estimates ? estimates[h.code] : null;
        const navList = historyMap ? historyMap[h.code] : null;
        const td = navList ? analyzeTrend(navList) : null;
        const heatInfo = pickHeatForType(h.type, heatmap || []);
        const vote = td ? computeVote(td, heatInfo, null) : null;

        result.signals.push({
          code: h.code,
          name: h.name,
          action: vote ? vote.action : 'hold',
          urgency: '中',
          confidence: vote ? vote.confidence : 40,
          reason: vote ? `算法自动补全: ${vote.label}` : '数据不足，建议观望',
          analysis: vote
            ? `AI未覆盖此基金，由本地算法补全。趋势方向: ${td.trendDir}，RSI: ${td.rsi ? td.rsi.toFixed(0) : '--'}，波段位置: ${td.swingPos}。板块热度${heatInfo.temperature}°。${vote.consensus}，建议${vote.label}。`
            : `AI未覆盖此基金且历史净值不足，暂按持有观望处理。板块热度${heatInfo.temperature}°，建议等待更多数据后再判断。`,
          targetReturn: '--',
          riskWarning: '此为算法自动补全，仅供参考',
          bestTiming: '等待AI下次完整分析',
          _autoFilled: true,
        });
      });
    }
  }

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
