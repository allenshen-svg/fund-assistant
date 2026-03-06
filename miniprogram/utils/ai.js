/**
 * AI 分析模块 — 调用硅基流动 / DeepSeek 等 OpenAI-compatible API
 * 真机环境通过服务器代理 (/api/ai-proxy) 调用，避免域名白名单限制
 */
const { getHoldings, getSettings } = require('./storage');
const { analyzeTrend, computeVote } = require('./analyzer');
const { pickHeatForType } = require('./advisor');
const { todayStr, formatPct, isTradingDay } = require('./market');
const { getServerBase } = require('./api');

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
      { id: 'deepseek-chat', name: 'DeepSeek-V3.2', model: 'deepseek-chat' },
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
  const providerId = wx.getStorageSync(AI_PROVIDER_KEY) || 'deepseek';
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

/**
 * 通过服务器代理调用 AI（解决微信真机域名白名单限制）
 */
function _doProxyRequest(serverBase, providerId, apiKey, apiBase, reqData, timeoutMs) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: serverBase + '/api/ai-proxy',
      method: 'POST',
      timeout: timeoutMs,
      header: { 'Content-Type': 'application/json' },
      data: {
        provider: providerId,
        api_key: apiKey,
        api_base: apiBase,  // 自定义 provider 时传完整 URL
        body: reqData,
      },
      success(res) {
        if (res.statusCode !== 200) {
          const detail = (res.data && (res.data.error || res.data.detail)) || JSON.stringify(res.data);
          reject(new Error(`代理请求失败 ${res.statusCode}: ${detail}`));
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
          reject(new Error('代理网络失败: ' + msg));
        }
      },
    });
  });
}

/**
 * 直接调用 AI API（仅开发工具 / 域名已白名单时可用）
 */
function _doDirectRequest(apiBase, apiKey, reqData, timeoutMs) {
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

  // 判断是否通过服务器代理
  const settings = getSettings();
  const serverBase = getServerBase(settings);
  const useProxy = !!serverBase;
  const config = getAIConfig();

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      console.log(`[AI] 第${attempt + 1}次请求, ${useProxy ? '代理模式' : '直连模式'}, timeout=${TIMEOUT_MS / 1000}s`);
      let content;
      if (useProxy) {
        content = await _doProxyRequest(serverBase, config.providerId, apiKey, apiBase, reqData, TIMEOUT_MS);
      } else {
        content = await _doDirectRequest(apiBase, apiKey, reqData, TIMEOUT_MS);
      }
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

/* ====== 单只基金 AI 分析 ====== */
const SINGLE_FUND_SYSTEM_PROMPT = `你是一位专业的基金投资分析师，擅长通过多维度因果链条深入分析基金走势。

## 核心分析方法论
你必须采用**多层因果链推导**来分析走势，而不是简单罗列现象。
例如分析黄金基金下跌：
"伊朗封锁霍尔木兹海峡 → 全球油价飙升 → 通胀预期骤升 → 美联储加息概率上升 → 美元走强+实际利率上行 → 黄金承压下跌"
例如分析半导体基金上涨：
"美国加码芯片出口限制 → 国产替代预期强化 → 政策端加大半导体扶持 → 资金涌入国产半导体 → 板块资金净流入扩大 → 半导体基金走强"

## 输出要求（JSON格式）：
{
  "fundName": "基金名称",
  "fundCode": "基金代码",
  "trendSummary": "当前走势总结（1-2句话，如：近5日上涨3.2%，延续反弹趋势）",
  "weekTrend": "过去一周走势形态（1句话，如'连涨4日后回调''震荡筑底''放量突破'）",
  "deepAnalysis": "深度因果分析（5-8句话，必须使用'A→B→C→D'的因果链条格式分析。从全球宏观事件/地缘政治出发，层层推导到宏观经济变量（利率/汇率/通胀/流动性），再到板块/行业影响，最终到该基金的具体走势。要求至少包含2条不同维度的因果链。例：'① 事件链：中东紧张→油价上涨→通胀预期→加息预期→金价承压；② 资金链：美元走强→资金回流美元资产→新兴市场资金流出→A股承压→相关板块调整'）",
  "whyTrend": "走势归因总结（2-3句话，基于deepAnalysis的因果链，归纳最核心的1-2个驱动因素）",
  "sectorAnalysis": "板块资金面分析（2-3句话，结合板块资金流入流出数据，分析主力资金动向对该基金的影响，判断资金趋势是否持续）",
  "technicalView": "技术面分析（2-3句话，RSI、均线、支撑/压力位等）",
  "keyEvents": "关键事件影响（2-3句话，从提供的热点事件中挑出与该基金最相关的1-2个事件，分析其对基金的传导路径和影响程度）",
  "action": "buy|sell|hold|add|reduce",
  "todayAdvice": "今日操作建议（2-3句话，基于过去一周走势给出今天具体该怎么操作，须引用净值/指标数据）",
  "timing": "操作时机（如'开盘即可''等回调至1.05再入''尾盘操作''观望不动'）",
  "riskWarning": "风险提示（1-2句话）",
  "outlook": "未来1-2周展望（2-3句话）"
}

## 注意：
- 只输出JSON，不要其他文字
- action含义：buy=建仓买入, sell=清仓卖出, hold=持有不动, add=加仓, reduce=减仓
- **deepAnalysis 是最重要的字段**：必须使用因果链条(→)格式，不能只是罗列现象，要体现"因为A所以B所以C"的逻辑推导
- 每条因果链应从具体事件/数据出发，经过宏观变量传导，最终落到该基金走势上
- keyEvents 必须从提供的热点事件中选取最相关的，分析传导路径
- 板块资金面分析要结合实际资金流向数据给出判断
- todayAdvice 必须引用具体数据，给出明确可执行的当天操作
- 用中文回复`;

async function runSingleFundAI({ fund, estimates, historyMap, indices, commodities, heatmap, hotEvents, sectorFlows }) {
  const config = getAIConfig();
  if (!config.key) throw new Error('请先在设置中配置 AI API Key');

  const today = todayStr();
  const est = estimates ? estimates[fund.code] : null;
  const navList = historyMap ? historyMap[fund.code] : null;
  const td = navList ? analyzeTrend(navList) : null;
  const heatInfo = pickHeatForType(fund.type, heatmap || []);
  const vote = td ? computeVote(td, heatInfo, null) : null;

  let ctx = `## 日期：${today}（${isTradingDay(today) ? '交易日' : '非交易日'}）\n\n`;

  // 大盘环境
  if (indices && indices.length > 0) {
    ctx += `## 今日大盘指数\n`;
    indices.forEach(idx => {
      ctx += `- ${idx.name}: ${idx.price || '--'} (${idx.pctStr || '--'})\n`;
    });
    ctx += '\n';
  }

  // 大宗商品
  if (commodities && commodities.length > 0) {
    ctx += `## 大宗商品行情\n`;
    commodities.forEach(c => {
      ctx += `- ${c.icon || ''} ${c.name}: ${c.price || '--'} (${c.pctStr || '--'})${Math.abs(c.pct) >= 2 ? ' ⚠️异动' : ''}\n`;
    });
    ctx += '\n';
  }

  // 板块热力
  if (heatmap && heatmap.length > 0) {
    ctx += `## 板块热力图\n`;
    heatmap.forEach(h => {
      ctx += `- ${h.tag}: 温度${h.temperature}° 趋势${h.trend || '—'}\n`;
    });
    ctx += '\n';
  }

  // 热点事件（提供完整信息用于因果链分析）
  if (hotEvents && hotEvents.length > 0) {
    ctx += `## 近期热点事件（请从中挑选与该基金最相关的事件进行因果链分析）\n`;
    hotEvents.slice(0, 8).forEach(ev => {
      ctx += `- [影响${ev.impact >= 0 ? '+' : ''}${ev.impact}] ${ev.title}`;
      if (ev.category) ctx += ` [类别:${ev.category}]`;
      if (ev.reason) ctx += `\n  原因: ${ev.reason}`;
      if (ev.sectors_positive && ev.sectors_positive.length > 0) ctx += `\n  利好板块: ${(Array.isArray(ev.sectors_positive) ? ev.sectors_positive : []).join('、')}`;
      if (ev.sectors_negative && ev.sectors_negative.length > 0) ctx += `\n  利空板块: ${(Array.isArray(ev.sectors_negative) ? ev.sectors_negative : []).join('、')}`;
      if (ev.concepts && ev.concepts.length > 0) ctx += `\n  关联概念: ${(Array.isArray(ev.concepts) ? ev.concepts : []).join('、')}`;
      ctx += '\n';
    });
    ctx += '\n';
  }

  // 板块资金流向
  if (sectorFlows && sectorFlows.length > 0) {
    // 找到与该基金类型匹配的板块资金流
    const FLOW_TAG_MAP = {
      黄金: ['黄金', '贵金属', '有色金属'], 有色金属: ['有色金属'],
      'AI/科技': ['AI算力', '人工智能', '半导体', '机器人', '计算机', '电子', '通信'],
      '半导体/科技': ['半导体', 'AI算力', '人工智能', '计算机', '电子'],
      半导体: ['半导体', '电子'], 军工: ['军工', '国防军工'],
      新能源: ['新能源', '光伏', '新能源车', '锂电', '电力设备'],
      医药: ['医药', '医药生物'], 消费: ['消费', '食品饮料', '商贸零售', '家用电器'],
      '白酒/消费': ['消费', '白酒', '食品饮料'], 债券: ['债券'],
      宽基: ['宽基', '沪深300', 'A500', '银行', '非银金融'],
      红利: ['红利', '银行', '煤炭', '公用事业'], 港股科技: ['港股科技', '计算机', '电子'],
      原油: ['原油', '石油石化'], 蓝筹: ['蓝筹', '消费', '医药', '银行', '非银金融', '食品饮料'],
      '蓝筹/QDII': ['蓝筹', '消费', '港股', '食品饮料'],
    };
    const tags = FLOW_TAG_MAP[fund.type] || [fund.type];
    const matchedFlows = sectorFlows.filter(f =>
      tags.some(tag => f.name.includes(tag) || tag.includes(f.name))
    );
    // 展示匹配的板块 + 涨幅前10的板块
    const topFlows = sectorFlows.slice(0, 10);
    const allDisplay = [...matchedFlows];
    topFlows.forEach(f => { if (!allDisplay.find(d => d.code === f.code)) allDisplay.push(f); });

    ctx += `## 板块资金流向（主力净流入，单位：元）\n`;
    allDisplay.slice(0, 12).forEach(f => {
      const netStr = f.mainNet >= 0 ? '+' : '';
      const inBillions = (f.mainNet / 1e8).toFixed(2);
      const mark = matchedFlows.find(m => m.code === f.code) ? ' ← 该基金所属板块' : '';
      ctx += `- ${f.name}: 涨幅${f.pct >= 0 ? '+' : ''}${f.pct}%, 主力净流入${netStr}${inBillions}亿 (占比${f.mainPct}%)${mark}\n`;
    });
    ctx += '\n';
  }

  // 目标基金详细数据
  ctx += `## 分析目标基金\n`;
  ctx += `### ${fund.name}（${fund.code}，${fund.type}）\n`;

  // 过去一周逐日净值
  if (navList && navList.length > 0) {
    const recent = navList.slice(-7);
    const weekStart = recent[0];
    const weekEnd = recent[recent.length - 1];
    const weekChg = ((weekEnd.nav - weekStart.nav) / weekStart.nav * 100).toFixed(2);
    ctx += `- 一周净值: ${weekStart.date}=${weekStart.nav} → ${weekEnd.date}=${weekEnd.nav}, 周涨幅=${weekChg}%\n`;
    ctx += `- 逐日净值: ${recent.map(r => `${r.date.slice(5)}:${r.nav}`).join(' → ')}\n`;
  }

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

  const userPrompt = ctx + `\n请深入分析 ${fund.name}（${fund.code}）的当前走势。
重点要求：
1. **deepAnalysis（最重要）**：用"A→B→C→D"因果链格式，从全球宏观事件/地缘政治出发，经过利率/汇率/通胀/流动性等宏观变量传导，最终落到该基金所属板块和净值走势上。至少给出2条不同维度的因果链。
2. **keyEvents**：从提供的热点事件中挑选与该基金最相关的1-2个事件，分析传导路径和影响程度。
3. 结合过去一周逐日净值走势形态，给出**今天**具体该如何操作的建议（含操作时机）。
用中文回复，只输出JSON。`;

  const raw = await callAI(
    config.provider.base,
    config.key,
    config.model,
    SINGLE_FUND_SYSTEM_PROMPT,
    userPrompt,
    0.7
  );

  const result = parseAIResponse(raw);
  if (!result) {
    throw new Error('AI返回内容无法解析，请重试');
  }

  // 缓存单基金结果
  const cacheKey = 'fa_mp_ai_single_' + fund.code;
  wx.setStorageSync(cacheKey, {
    date: todayStr(),
    timestamp: new Date().toISOString(),
    result,
  });

  return result;
}

/* ====== 今日操作建议 ====== */

const DAILY_ADVICE_SYSTEM_PROMPT = `你是一位专业的基金投资顾问。根据用户持仓基金过去一周（5-7个交易日）的净值走势，结合当前市场环境，给出今天的具体操作建议。

## 输出要求（JSON格式）：
{
  "marketBrief": "今日市场一句话定调（15-25字）",
  "riskLevel": "低风险|中风险|高风险",
  "funds": [
    {
      "code": "基金代码",
      "name": "基金名称",
      "weekChange": "近一周涨跌幅（如+2.3%）",
      "weekTrend": "过去一周走势简述（1句话，如'连涨4日后回调'）",
      "action": "buy|sell|hold|add|reduce",
      "reason": "今日给出该操作的理由（2-3句话，须引用具体净值/技术指标数据）",
      "timing": "操作时机建议（如'开盘即可''等回调至1.05''尾盘操作'）",
      "confidence": 0-100
    }
  ]
}

## 注意事项：
- 只输出JSON，不要其他文字
- action含义：buy=建仓买入, sell=清仓卖出, hold=持有不动, add=加仓, reduce=减仓
- funds数组必须覆盖用户全部持仓基金
- 重点分析过去一周净值的走势形态（连涨/连跌/震荡/反弹等），基于趋势给出今天该怎么操作
- confidence为0-100的整数
- 用中文回复`;

/**
 * 今日操作建议 — 基于过去一周净值走势 + 当前局势 → 给出今天的操作
 */
async function runDailyAdvice({ holdings, estimates, historyMap, indices, commodities, heatmap, hotEvents }) {
  const config = getAIConfig();
  if (!config.key) throw new Error('请先在设置中配置 AI API Key');

  const today = todayStr();
  let ctx = `## 日期：${today}\n\n`;

  // 大盘指数
  if (indices && indices.length > 0) {
    ctx += `## 今日大盘指数\n`;
    indices.forEach(idx => {
      ctx += `- ${idx.name}: ${idx.price || '--'} (${idx.pctStr || '--'})\n`;
    });
    ctx += '\n';
  }

  // 大宗商品
  if (commodities && commodities.length > 0) {
    ctx += `## 大宗商品行情\n`;
    commodities.forEach(c => {
      ctx += `- ${c.icon || ''} ${c.name}: ${c.price || '--'} (${c.pctStr || '--'})${Math.abs(c.pct) >= 2 ? ' ⚠️异动' : ''}\n`;
    });
    ctx += '\n';
  }

  // 板块热力
  if (heatmap && heatmap.length > 0) {
    ctx += `## 板块热力图\n`;
    heatmap.forEach(h => {
      ctx += `- ${h.tag}: 温度${h.temperature}° 趋势${h.trend || '—'}\n`;
    });
    ctx += '\n';
  }

  // 热点事件
  if (hotEvents && hotEvents.length > 0) {
    ctx += `## 近期热点事件\n`;
    hotEvents.slice(0, 8).forEach(ev => {
      ctx += `- [影响${ev.impact >= 0 ? '+' : ''}${ev.impact}] ${ev.title}`;
      if (ev.advice) ctx += ` → ${ev.advice}`;
      ctx += '\n';
    });
    ctx += '\n';
  }

  // 每只基金的一周净值数据
  ctx += `## 持仓基金一周净值变化（共${holdings.length}只）\n`;
  holdings.forEach(h => {
    const navList = historyMap ? historyMap[h.code] : null;
    const td = navList ? analyzeTrend(navList) : null;
    const est = estimates ? estimates[h.code] : null;

    ctx += `\n### ${h.name}（${h.code}，${h.type}）\n`;

    // 取最近7个交易日的净值
    if (navList && navList.length > 0) {
      const recent = navList.slice(-7);
      const weekStart = recent[0];
      const weekEnd = recent[recent.length - 1];
      const weekChg = ((weekEnd.nav - weekStart.nav) / weekStart.nav * 100).toFixed(2);
      ctx += `- 一周净值: ${weekStart.date}=${weekStart.nav} → ${weekEnd.date}=${weekEnd.nav}, 周涨幅=${weekChg}%\n`;
      ctx += `- 逐日净值: ${recent.map(r => `${r.date.slice(5)}:${r.nav}`).join(' → ')}\n`;
    }

    if (est) {
      ctx += `- 今日估值: ${est.estimate || '--'}, 估算涨幅: ${formatPct(est.pct)}\n`;
    }
    if (td) {
      ctx += `- 趋势方向: ${td.trendDir}, 5日涨幅: ${td.chg5d ? td.chg5d.toFixed(2) + '%' : '--'}, 20日涨幅: ${td.chg20d ? td.chg20d.toFixed(2) + '%' : '--'}\n`;
      ctx += `- RSI: ${td.rsi ? td.rsi.toFixed(1) : '--'}, 回撤: ${td.drawdownFromHigh ? td.drawdownFromHigh.toFixed(2) + '%' : '--'}\n`;
      ctx += `- 均线状态: ${td.maStatus || '--'}, 波段建议: ${td.swingAdvice || '--'}\n`;
    }
  });

  const userPrompt = ctx + `

⚠️ 用户共持有 ${holdings.length} 只基金：${holdings.map(h => `${h.name}(${h.code})`).join('、')}。
funds 数组必须包含全部 ${holdings.length} 只基金。

请基于以上过去一周的净值走势和今日市场环境：
1. 分析每只基金过去一周的走势形态（连涨/连跌/震荡/突破等）
2. 结合当前市场局势，给出**今天**应该如何操作（买入/卖出/加仓/减仓/持有）
3. 每只基金的建议须引用具体的净值数据或技术指标作为依据
4. 给出操作时机建议（开盘/尾盘/等回调等）

用中文回复。`;

  const raw = await callAI(
    config.provider.base,
    config.key,
    config.model,
    DAILY_ADVICE_SYSTEM_PROMPT,
    userPrompt,
    0.7
  );

  const result = parseAIResponse(raw);
  if (!result) {
    throw new Error('AI返回内容无法解析，请重试');
  }

  // 确保 funds 数组覆盖全部持仓
  if (result.funds && holdings.length > 0) {
    const covered = new Set(result.funds.map(f => f.code));
    holdings.forEach(h => {
      if (!covered.has(h.code)) {
        const navList = historyMap ? historyMap[h.code] : null;
        const recent = navList ? navList.slice(-7) : [];
        const weekChg = recent.length >= 2
          ? ((recent[recent.length - 1].nav - recent[0].nav) / recent[0].nav * 100).toFixed(2) + '%'
          : '--';
        result.funds.push({
          code: h.code,
          name: h.name,
          weekChange: weekChg,
          weekTrend: '数据不足',
          action: 'hold',
          reason: '数据不足，AI未覆盖此基金，建议持有观望',
          timing: '--',
          confidence: 40,
          _autoFilled: true,
        });
      }
    });
  }

  // 缓存
  wx.setStorageSync('fa_mp_daily_advice', {
    date: todayStr(),
    timestamp: new Date().toISOString(),
    result,
  });

  return result;
}

/** 获取缓存的今日操作建议 */
function getCachedDailyAdvice() {
  const cached = wx.getStorageSync('fa_mp_daily_advice');
  if (cached && cached.date === todayStr()) return cached;
  return null;
}

/* ====== 选基金/股票 — 基金经理视角 ====== */

const FUND_PICK_SYSTEM_PROMPT = `你是一位管理百亿规模基金的资深基金经理，擅长从板块轮动和资金流向中发现投资机会。

## 你的任务
根据当前增长良好的板块数据（涨幅、资金净流入）、板块内TOP基金和领涨个股，站在专业基金经理角度，选出最值得投资的基金和股票。

## 分析方法论
1. 先判断板块景气度：资金面（主力净流入持续性）+ 基本面（行业增长逻辑）+ 催化剂（政策/事件驱动）
2. 再从板块中精选标的：基金看3个月业绩支撑+规模适中+跟踪误差小；股票看业绩增速+估值合理+资金关注度
3. 用因果链分析WHY：为什么这个板块现在值得配置，是短期情绪还是中期趋势

## 输出要求（JSON格式）：
{
  "sectorOverview": "当前市场板块轮动总览（3-5句话，哪些板块在领涨，资金在向哪里流动，背后的宏观逻辑是什么）",
  "picks": [
    {
      "type": "fund 或 stock",
      "code": "代码",
      "name": "名称",
      "sector": "所属板块",
      "rating": "strong_buy|buy|accumulate",
      "confidence": 60-95,
      "whyThisSector": "为什么看好这个板块（2-3句话，用因果链A→B→C格式）",
      "whyThisPick": "为什么选这只（2-3句话，基金看业绩/规模/跟踪精度，股票看业绩/估值/资金）",
      "supportAnalysis": "过去3个月的支撑面分析（2-3句话，分析近3月表现、支撑逻辑、是否可持续）",
      "riskPoints": "主要风险点（1-2句话）",
      "strategy": "建议买入策略（如：分3批建仓，首批仓位30%，回调至XX再加仓）",
      "targetReturn": "预期收益区间（如：未来1-3个月 8-15%）"
    }
  ],
  "marketRisk": "当前市场整体风险提示（2-3句话）",
  "allocationAdvice": "资金配置建议（2-3句话，建议多少仓位配置在推荐标的上）"
}

## 注意：
- 只输出JSON，不要其他文字
- picks 数组推荐 4-6 只标的，基金和股票混合推荐
- rating含义：strong_buy=强烈推荐(高确定性), buy=推荐买入, accumulate=逢低吸纳
- 重点分析"为什么现在是买入时机"，而不只是罗列数据
- whyThisSector 必须包含因果链推导
- supportAnalysis 要分析过去3个月的趋势是否有持续性
- 用中文回复`;

async function runFundPickAI({ sectorFlows, topSectorFunds, topSectorStocks, indices, commodities, hotEvents, heatmap }) {
  const config = getAIConfig();
  if (!config.key) throw new Error('请先在设置中配置 AI API Key');

  const today = todayStr();
  let ctx = `## 日期：${today}\n\n`;

  // 大盘环境
  if (indices && indices.length > 0) {
    ctx += `## 今日大盘指数\n`;
    indices.forEach(idx => {
      ctx += `- ${idx.name}: ${idx.price || '--'} (${idx.pctStr || (idx.pct != null ? (idx.pct >= 0 ? '+' : '') + idx.pct + '%' : '--')})\n`;
    });
    ctx += '\n';
  }

  // 大宗商品
  if (commodities && commodities.length > 0) {
    ctx += `## 大宗商品\n`;
    commodities.forEach(c => {
      ctx += `- ${c.icon || ''} ${c.name}: ${c.price || '--'} (${c.pctStr || '--'})\n`;
    });
    ctx += '\n';
  }

  // 热点事件
  if (hotEvents && hotEvents.length > 0) {
    ctx += `## 近期热点事件\n`;
    hotEvents.slice(0, 6).forEach(ev => {
      ctx += `- [影响${ev.impact >= 0 ? '+' : ''}${ev.impact}] ${ev.title}`;
      if (ev.reason) ctx += ` | ${ev.reason}`;
      ctx += '\n';
    });
    ctx += '\n';
  }

  // 板块热力
  if (heatmap && heatmap.length > 0) {
    ctx += `## 板块热力图\n`;
    heatmap.forEach(h => {
      ctx += `- ${h.tag}: 温度${h.temperature}° 趋势${h.trend || '—'}`;
      if (h.realPct != null) ctx += ` 实际涨幅${h.realPct >= 0 ? '+' : ''}${h.realPct}%`;
      ctx += '\n';
    });
    ctx += '\n';
  }

  // 板块资金流向 TOP 板块
  if (sectorFlows && sectorFlows.length > 0) {
    ctx += `## 板块资金流向（按主力净流入排序）\n`;
    // 涨幅 Top 15
    const topByPct = [...sectorFlows].sort((a, b) => (b.pct || 0) - (a.pct || 0)).slice(0, 15);
    ctx += `### 涨幅领先板块\n`;
    topByPct.forEach(f => {
      const netBillion = ((f.mainNet || 0) / 1e8).toFixed(2);
      ctx += `- ${f.name}: 涨幅${f.pct >= 0 ? '+' : ''}${f.pct}%, 主力净流入${netBillion}亿 (占比${f.mainPct || 0}%)\n`;
    });
    // 资金流入 Top 10
    const topByFlow = [...sectorFlows].sort((a, b) => (b.mainNet || 0) - (a.mainNet || 0)).slice(0, 10);
    ctx += `### 资金净流入领先板块\n`;
    topByFlow.forEach(f => {
      const netBillion = ((f.mainNet || 0) / 1e8).toFixed(2);
      ctx += `- ${f.name}: 主力净流入${netBillion}亿, 涨幅${f.pct >= 0 ? '+' : ''}${f.pct}%\n`;
    });
    ctx += '\n';
  }

  // 各板块TOP基金
  if (topSectorFunds && topSectorFunds.length > 0) {
    ctx += `## 增长板块内的TOP基金（按近3月业绩排序）\n`;
    topSectorFunds.forEach(sf => {
      ctx += `### ${sf.sector}板块\n`;
      if (sf.funds && sf.funds.length > 0) {
        sf.funds.forEach(f => {
          ctx += `- ${f.name}（${f.code}）类型:${f.type || '未知'}\n`;
        });
      } else {
        ctx += `- 暂无匹配基金\n`;
      }
    });
    ctx += '\n';
  }

  // 各板块领涨个股
  if (topSectorStocks && topSectorStocks.length > 0) {
    ctx += `## 增长板块内的领涨个股\n`;
    topSectorStocks.forEach(ss => {
      ctx += `### ${ss.sector}板块\n`;
      if (ss.stocks && ss.stocks.length > 0) {
        ss.stocks.forEach(s => {
          const capStr = s.marketCap ? (s.marketCap / 1e8).toFixed(0) + '亿' : '--';
          ctx += `- ${s.name}（${s.code}）价格:${s.price} 涨幅:${s.pct >= 0 ? '+' : ''}${s.pct}% 市值:${capStr} PE:${s.pe || '--'}\n`;
        });
      } else {
        ctx += `- 暂无数据\n`;
      }
    });
    ctx += '\n';
  }

  const userPrompt = ctx + `\n请站在百亿基金经理的角度，从以上增长良好的板块中，精选4-6只最值得投资的基金和股票。
要求：
1. 优先选择涨幅+资金净流入双强的板块
2. 每只标的都要有过去3个月的支撑面分析
3. 用因果链分析为什么看好这个板块
4. 给出具体的买入策略和预期收益
用中文回复，只输出JSON。`;

  const raw = await callAI(
    config.provider.base,
    config.key,
    config.model,
    FUND_PICK_SYSTEM_PROMPT,
    userPrompt,
    0.7
  );

  const result = parseAIResponse(raw);
  if (!result) {
    throw new Error('AI返回内容无法解析，请重试');
  }

  // 缓存
  wx.setStorageSync('fa_mp_fund_pick', {
    date: todayStr(),
    timestamp: new Date().toISOString(),
    result,
  });

  return result;
}

function getCachedFundPick() {
  const cached = wx.getStorageSync('fa_mp_fund_pick');
  if (cached && cached.date === todayStr()) return cached;
  return null;
}

module.exports = {
  AI_PROVIDERS,
  getAIConfig,
  setAIConfig,
  runAIAnalysis,
  runSingleFundAI,
  getCachedAIResult,
  runDailyAdvice,
  getCachedDailyAdvice,
  callAI,
  runFundPickAI,
  getCachedFundPick,
};
