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
[必须提炼 3-5 个本小时内最具代表性的资产或板块，每个按以下多行格式输出。每个板块的每个字段至少写 2-3 句话，包含具体的数据引用和逻辑推理。]
- **🎯 标的/板块**：[例如：半导体 / 贵金属 / 房地产]
- **🎙️ 聪明钱/KOL 观点**：[详细总结 2-3 位博主的核心逻辑，引用数据中的具体标题，说明他们看多/看空的依据是什么，观点之间是否存在分歧。至少 2-3 句话。]
- **🐑 羊群/散户 情绪**：[详细描述评论区散户的状态：是盲目跟风、理性质疑、恐慌割肉还是抄底进场？从数据情绪分布中找出比例，给出散户行为的画像。至少 2-3 句话。]
- **⚡ 预期差结论**：[明确给出：(1)情绪背离方向（KOL与散户谁对谁错概率更大）；(2)中短期操作建议（加仓/减仓/观望）；(3)关键观察指标（例如量能、政策催化、资金流向）。至少 2-3 句话。]

### 💡 极简操作指南 (Action Plan)
请针对以下常见基金持仓类型，逐一给出明确的操作建议：

#### 📌 各类持仓操作建议
[针对以下 6 类基金类型，每类给出具体的操作建议（加仓/减仓/持有/观望），以及理由]
- **🥇 黄金类基金**：[当前情绪面支持加仓还是减仓？具体理由？]
- **📊 宽基指数（A500/中证500/沪深300）**：[当前情绪面对宽基的影响？]
- **🤖 AI/科技/半导体**：[该方向当前情绪拥挤度如何？操作建议？]
- **💰 红利/价值**：[避险情绪是否利好红利？]
- **⚔️ 军工/新能源/赛道股**：[是否有主题催化？风险点？]
- **🍷 白酒/消费**：[消费情绪的真实反馈？]

#### 🎯 综合建议
- **✅ 胜率较高的方向**：[指出当前情绪面支撑下建议关注的 2-3 个方向，说明源于哪些数据信号]
- **❌ 必须回避的绞肉机**：[指出情绪过热、极度拥挤的板块，给出具体风险点]
- **⏱️ 战术纪律**：[给出具体的防守/进攻底线和止损建议，至少 2 句话]

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
  // Split by "#### 🎯" or "- **🎯" patterns
  const parts = text.split(/(?=(?:####?\s*)?(?:- \*\*)?\u{1F3AF}\s*标的[\/／]板块)/u);
  for(const sec of parts) {
    if(!sec.includes('\u{1F3AF}') || !sec.includes('标的')) continue;
    const target = sec.match(/标的[\/／]板块[\*]*[：:]\s*(.*)/)?.[1]?.replace(/\*+$/,'').trim() || '';
    // Multi-line KOL: content spans until next "- **" or "####"
    let kol = '';
    const kolMatch = sec.match(/(?:聪明钱[\/／])?KOL\s*观点\*\*[：:]\s*\n?\s*([\s\S]*?)(?=\n-\s*\*\*|\n####|$)/);
    if (kolMatch) kol = kolMatch[1].replace(/\s+/g, ' ').trim();
    else { const m = sec.match(/(?:聪明钱[\/／])?KOL\s*观点\*\*[：:]\s*(.*)/); if(m) kol = m[1].trim(); }
    let retail = '';
    const retailMatch = sec.match(/(?:羊群[\/／])?散户\s*情绪\*\*[：:]\s*\n?\s*([\s\S]*?)(?=\n-\s*\*\*|\n####|$)/);
    if (retailMatch) retail = retailMatch[1].replace(/\s+/g, ' ').trim();
    else { const m = sec.match(/(?:羊群[\/／])?散户\s*情绪\*\*[：:]\s*(.*)/); if(m) retail = m[1].trim(); }
    let conclusion = '';
    const concMatch = sec.match(/预期差结论\*\*[：:]\s*\n?\s*([\s\S]*?)(?=\n-\s*\*\*|\n####|\n###|$)/);
    if (concMatch) conclusion = concMatch[1].replace(/\s+/g, ' ').trim();
    else { const m = sec.match(/预期差结论\*\*[：:]\s*(.*)/); if(m) conclusion = m[1].trim(); }
    if(target) sections.push({target, kol, retail, conclusion});
  }
  return sections;
}

function parseActions(text) {
  const s = text.split(/###\s*💡/)?.[1] || '';
  // Parse per-holding-type recommendations
  var holdingActions = [];
  var holdingMatches = s.match(/- \*\*[\S]+\s+([^*]+)\*\*：([^
]+)/g) || [];
  for (var i = 0; i < holdingMatches.length; i++) {
    var hm = holdingMatches[i];
    var labelMatch = hm.match(/\*\*([^*]+)\*\*：(.+)/);
    if (labelMatch) {
      var label = labelMatch[1].trim();
      var advice = labelMatch[2].trim();
      // Skip the summary section items (胜率, 回避, 战术)
      if (/胜率|回避|绞肉|战术纪律/.test(label)) continue;
      holdingActions.push({ label: label, advice: advice });
    }
  }
  return {
    holdingActions: holdingActions,
    bullish: s.match(/胜率较高的方向\*\*[：:]　?\s*(.*)/)?.[1]?.trim() || s.match(/利好板块\/资产\*\*[：:]\s*(.*)/)?.[1]?.trim() || '',
    bearish: s.match(/必须回避的绞肉机\*\*[：:]\s*(.*)/)?.[1]?.trim() || s.match(/高危板块\/资产\*\*[：:]\s*(.*)/)?.[1]?.trim() || '',
    tactical: s.match(/战术纪律\*\*[：:]\s*(.*)/)?.[1]?.trim() || s.match(/一小时战术建议\*\*[：:]\s*(.*)/)?.[1]?.trim() || '',
  };
}

function parseRadarSummary(text) {
  const s = text.split(/###\s*🚨/)?.[1]?.split(/###/)?.[0] || '';
  return s.replace(/【当前小时】情绪与预期差雷达/g,'').replace(/【当前时刻】舆情热度雷达图/g,'').trim();
}
