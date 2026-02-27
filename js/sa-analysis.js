// =============================================
// KOL vs 散户 情绪博弈分析 - Prompt & Parsing
// =============================================

// ==================== PROMPT ====================
function buildAnalysisPrompt(videoDataStr) {
  const systemPrompt = `# 角色定义 (Role)
你是一位顶尖的"另类数据（Alternative Data）"宏观量化分析师及行为金融学专家。你最强的能力在于：从 KOL（聪明钱/意见领袖）与散户评论区的"情绪背离"中精准识别见顶/见底信号。

你必须严格按照指定格式输出，特别是最后的 JSON 部分必须是合法的 JSON 代码块。`;

  const userPrompt = `# 输入数据格式 (Input Context)
以下是过去 1 小时内，通过 RPA 自动化从【核心财经博主白名单】中提取的最新动态及评论区抽样数据。数据结构包括：博主影响力级别、视频核心文案、点赞增速（动量）、以及高赞评论的情绪倾向。
[当前小时度监控数据 JSON]:
${videoDataStr}

# 分析逻辑与数学框架 (Analytical Framework)
请在内心运行以下逻辑进行评估，无需在输出中展示推导过程：
1. **情绪背离判定**：当 KOL 提示风险，但评论区散户极其亢奋（满仓/冲锋）时，通常是**见顶信号**；当 KOL 绝望或被骂，评论区一片哀嚎割肉时，通常是**见底信号**。
2. **共识过热判定**：如果 KOL 与散户方向高度一致，且情绪极度激烈，说明该交易方向已极度拥挤（Crowded Trade），需警惕踩踏风险。
3. **噪音过滤**：忽略无明确指向性的口水仗，只提取与具体资产（A股、美股、黄金、原油、特定板块）相关的标的信号。

# 输出格式要求 (Output Structure)
请严格按照以下 Markdown 格式输出本小时的"市场情绪快报"，要求冷酷、客观、直指交易。

### 🚨 【当前小时】情绪与预期差雷达
[用一句话（20字以内）总结当前小时内，市场最核心的资金共识或情绪背离点。]

### ⚖️ KOL vs 散户：情绪博弈拆解
[提炼 1-2 个本小时内最具代表性的资产或板块，按以下格式输出]
- **🎯 标的/板块**：[例如：半导体 / 贵金属 / 房地产]
- **🎙️ 聪明钱/KOL 观点**：[总结白名单博主的核心逻辑，是看多还是看空？]
- **🐑 羊群/散户 情绪**：[总结评论区的真实反应，是跟风、质疑、还是恐慌？]
- **⚡ 预期差结论**：[指出此处是否存在反向操作的机会，或者顺势而为的确定性。]

### 💡 极简操作指南 (Action Plan)
- **✅ 胜率较高的方向**：[指出当前情绪面支撑下的建议关注方向]
- **❌ 必须回避的绞肉机**：[指出情绪过热、极度拥挤、随时崩盘的板块]
- **⏱️ 战术纪律**：[给出本小时的防守或进攻底线，例如"不追高任何爆量涨停股"或"耐心等待恐慌盘涌出"。]

### 📊 情绪仪表盘参数 (System Data)
[必须在最末尾输出纯 JSON 代码块，用于前端渲染。参数值需为 0-100 的整数。其中 fomo_level 为错失恐惧度，panic_level 为恐慌度，divergence_index 为博主与散户的意见分歧度。market_temperature 为市场温度 0-100。hot_assets 列出热门资产标的。action_signal 为操作信号文字。]
\`\`\`json
{
  "hourly_dashboard": {
    "market_temperature": <0-100>,
    "fomo_level": <0-100>,
    "panic_level": <0-100>,
    "divergence_index": <0-100>,
    "hot_assets": ["资产1", "资产2"],
    "action_signal": "<Aggressive Buy|Cautious Hold|Defensive|Strong Sell|Wait>"
  }
}
\`\`\``;

  return { systemPrompt, userPrompt };
}

// ==================== AI CALL ====================
async function callAI(model, systemPrompt, userPrompt, temperature=0.7) {
  if(!_apiKey) throw new Error('请先配置 API Key');
  const resp = await fetch(_provider.base, {
    method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+_apiKey},
    body:JSON.stringify({ model, messages:[{role:'system',content:systemPrompt},{role:'user',content:userPrompt}], temperature, max_tokens:4096 })
  });
  if(!resp.ok) {
    const err = await resp.text().catch(()=>'');
    throw new Error(`API ${resp.status}: ${err.slice(0,200)}`);
  }
  const json = await resp.json();
  return json.choices?.[0]?.message?.content || '';
}

// ==================== PARSING ====================
function extractJSON(text) {
  // Try ```json block first
  const jsonMatch = text.match(/```json\s*([\s\S]*?)\s*```/);
  if(jsonMatch) { try { return JSON.parse(jsonMatch[1]); } catch(e) {} }
  // Try loose match for hourly_dashboard
  const braceMatch = text.match(/\{[\s\S]*"hourly_dashboard"[\s\S]*\}/);
  if(braceMatch) { try { return JSON.parse(braceMatch[0]); } catch(e) {} }
  // Fallback for old format
  const oldMatch = text.match(/\{[\s\S]*"sentiment_factors"[\s\S]*\}/);
  if(oldMatch) {
    try {
      const old = JSON.parse(oldMatch[0]);
      const sf = old.sentiment_factors || {};
      return { hourly_dashboard:{ market_temperature: sf.market_temperature==='Overheated'?90:sf.market_temperature==='Hot'?75:sf.market_temperature==='Warm'?55:sf.market_temperature==='Cold'?25:50, fomo_level:sf.fomo_index||50, panic_level:sf.panic_index||50, divergence_index:50, hot_assets:sf.crowded_trades||[], action_signal:'Cautious Hold' }};
    } catch(e) {}
  }
  return { hourly_dashboard:{ market_temperature:50, fomo_level:50, panic_level:50, divergence_index:50, hot_assets:[], action_signal:'Wait' } };
}

function parseKOLSections(text) {
  const sections = [];
  const parts = text.split(/(?=- \*\*🎯 标的\/板块)/);
  for(const sec of parts) {
    if(!sec.includes('🎯 标的/板块')) continue;
    const target = sec.match(/标的\/板块\*\*[：:]\s*(.*)/)?.[1]?.trim() || '';
    const kol = sec.match(/聪明钱\/KOL\s*观点\*\*[：:]\s*(.*)/)?.[1]?.trim() || sec.match(/KOL\s*观点\*\*[：:]\s*(.*)/)?.[1]?.trim() || '';
    const retail = sec.match(/羊群\/散户\s*情绪\*\*[：:]\s*(.*)/)?.[1]?.trim() || sec.match(/散户\s*情绪\*\*[：:]\s*(.*)/)?.[1]?.trim() || '';
    const conclusion = sec.match(/预期差结论\*\*[：:]\s*(.*)/)?.[1]?.trim() || '';
    if(target) sections.push({target, kol, retail, conclusion});
  }
  return sections;
}

function parseActions(text) {
  const s = text.split(/###\s*💡/)?.[1] || '';
  return {
    bullish: s.match(/胜率较高的方向\*\*[：:]\s*(.*)/)?.[1]?.trim() || s.match(/利好板块\/资产\*\*[：:]\s*(.*)/)?.[1]?.trim() || '',
    bearish: s.match(/必须回避的绞肉机\*\*[：:]\s*(.*)/)?.[1]?.trim() || s.match(/高危板块\/资产\*\*[：:]\s*(.*)/)?.[1]?.trim() || '',
    tactical: s.match(/战术纪律\*\*[：:]\s*(.*)/)?.[1]?.trim() || s.match(/一小时战术建议\*\*[：:]\s*(.*)/)?.[1]?.trim() || '',
  };
}

function parseRadarSummary(text) {
  const s = text.split(/###\s*🚨/)?.[1]?.split(/###/)?.[0] || '';
  return s.replace(/【当前小时】情绪与预期差雷达/g,'').replace(/【当前时刻】舆情热度雷达图/g,'').trim();
}
