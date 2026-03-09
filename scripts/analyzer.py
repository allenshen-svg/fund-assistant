#!/usr/bin/env python3
"""
AI 舆情分析模块 — 调用大模型进行 KOL vs 散户情绪博弈分析
采集完成后自动运行，结果缓存到 data/analysis_cache.json
"""

import json, re, os, time
from datetime import datetime
import requests

# 用于构建美股摘要
US_MARKET_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'us_market_cache.json'
)
REALTIME_BREAKING_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'realtime_breaking.json'
)
HOT_EVENTS_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'hot_events.json'
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
ANALYSIS_CACHE = os.path.join(DATA_DIR, 'analysis_cache.json')

# ==================== AI 配置 ====================
AI_PROVIDERS = {
    'zhipu': {
        'name': '智谱AI',
        'base': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
        'models': ['GLM-4-Flash', 'GLM-4-Air', 'GLM-4'],
    },
    'siliconflow': {
        'name': '硅基流动(免费)',
        'base': 'https://api.siliconflow.cn/v1/chat/completions',
        'models': ['deepseek-ai/DeepSeek-V3', 'Qwen/Qwen2.5-72B-Instruct'],
    },
    'deepseek': {
        'name': 'DeepSeek官方',
        'base': 'https://api.deepseek.com/chat/completions',
        'models': ['deepseek-chat'],
    },
    '302ai': {
        'name': '302.AI',
        'base': 'https://api.302.ai/v1/chat/completions',
        'models': ['deepseek-r1', 'doubao-1.5-pro-32k'],
    },
}

DEFAULT_PROVIDER = os.environ.get('AI_PROVIDER', 'zhipu')
DEFAULT_API_KEY = os.environ.get('AI_API_KEY', '4511f9dee1e64b7da49a539ddef85dfd.Z6HgN8s8cDhL2LeQ')
DEFAULT_MODEL = os.environ.get('AI_MODEL', 'GLM-4-Flash')

# ==================== Prompt ====================
SYSTEM_PROMPT = """# 角色定义 (Role)
你是一位顶尖的国际宏观局势分析师、地缘政治与大宗商品全产业链影响专家。

## 核心能力
1. 对国际热点事件进行【按行业板块】的深层产业链冲击推演
2. 每个板块的分析必须深入、详尽、有数据、有逻辑链，内容不少于500字
3. 必须涵盖原油/能源板块（这是市场最关注的核心板块）
4. 必须涵盖化工/化肥板块（能源危机对化工的二阶传导是核心分析点）

## 硬性质量要求
- 每个板块分析必须包含：关键影响概览(至少5个要点)、产业链传导路径(多级箭头链)、量化影响预测表格(至少4行)、投资策略
- 表格必须有具体数字（百分比、金额、产量变动等），不能用"上涨趋势"等模糊词
- 产业链传导必须是多级链条（至少3级），不能是简单的 A→B→C
- 关键影响概览中每个要点必须包含具体数据（百分比、价格、成本占比等）
- 你的输出总长度必须超过3000字，如果不到说明你的分析不够深入

你必须严格按照指定格式输出。最后的 JSON 部分必须是合法的 JSON 代码块。
你的分析必须有干货、有数据、有逻辑链条，绝不能输出空洞占位符。
🔥 板块深度影响分析 是你最核心的输出，必须按板块逐一深入分析，这是你存在的全部价值。"""

def _load_us_market_summary():
    """读取美股缓存并生成摘要文本"""
    try:
        if not os.path.exists(US_MARKET_CACHE):
            return ''
        with open(US_MARKET_CACHE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        stocks = cache.get('stocks', [])
        if not stocks:
            return ''
        lines = ['[隔夜美股行情（北京时间今早收盘）]:']
        for s in stocks:
            arrow = '📈' if s['percent'] >= 0 else '📉'
            lines.append(f"  {arrow} {s['name']}({s['symbol']}): {s['price']} ({s['percent']:+.2f}%), 振幅{s.get('amplitude',0)}%")
        return '\n'.join(lines)
    except Exception:
        return ''

def _load_breaking_events_summary():
    """读取实时突发事件和热点事件，生成事件摘要供AI深度分析"""
    events = []
    try:
        if os.path.exists(REALTIME_BREAKING_CACHE):
            with open(REALTIME_BREAKING_CACHE, 'r', encoding='utf-8') as f:
                rt = json.load(f)
            for b in (rt.get('breaking') or []):
                events.append({
                    'title': b.get('title', ''),
                    'reason': b.get('reason', ''),
                    'category': b.get('category', ''),
                    'impact': b.get('impact', 0),
                    'source': b.get('source', ''),
                    'sectors_positive': b.get('sectors_positive', []),
                    'sectors_negative': b.get('sectors_negative', []),
                })
    except Exception:
        pass
    try:
        if os.path.exists(HOT_EVENTS_CACHE):
            with open(HOT_EVENTS_CACHE, 'r', encoding='utf-8') as f:
                he = json.load(f)
            for e in (he.get('events') or []):
                if not e.get('is_template'):
                    events.append({
                        'title': e.get('title', ''),
                        'reason': e.get('reason', ''),
                        'category': e.get('category', ''),
                        'impact': e.get('impact', 0),
                        'concepts': e.get('concepts', []),
                        'sectors_positive': e.get('sectors_positive', []),
                        'sectors_negative': e.get('sectors_negative', []),
                    })
    except Exception:
        pass
    if not events:
        return ''
    # 按影响力排序，最重要的事件排前面
    events.sort(key=lambda x: abs(x.get('impact', 0)), reverse=True)
    lines = ['[当前实时国际热点与市场事件（按影响力排序）]:']
    for i, e in enumerate(events):
        lines.append(f"  【{i+1}】[{e.get('category','')}] {e['title']} (影响:{e.get('impact',0):+d})")
        if e.get('reason'):
            lines.append(f"    详情: {e['reason']}")
        sp = e.get('sectors_positive', [])
        sn = e.get('sectors_negative', [])
        concepts = e.get('concepts', [])
        if sp:
            lines.append(f"    利好板块: {', '.join(sp)}")
        if sn:
            lines.append(f"    利空板块: {', '.join(sn)}")
        if concepts:
            lines.append(f"    涉及概念: {', '.join(concepts[:5])}")
    return '\n'.join(lines)


# ==================== 社媒数据筛选 ====================
# 热点关键词 — 用于从社媒数据中优先筛选与热点事件相关的内容
_HOT_KEYWORDS = [
    '原油', '石油', '油价', '能源', '伊朗', '中东', '战争', '制裁', '封锁',
    '化工', '化肥', '天然气', '黄金', '避险', '军工', '半导体', '芯片',
    '关税', '贸易战', '美联储', '降息', '加息', '通胀', 'AI', '人工智能',
    '稀土', '锂电', '新能源', '光伏', '特朗普', 'Trump', '俄罗斯', '乌克兰',
]


def _build_curated_social_data(items):
    """从社媒数据中筛选与热点事件最相关的内容，确保AI聚焦关键信息"""
    if not items:
        return '[]'

    scored = []
    for item in items:
        title = item.get('title', '') + ' ' + item.get('desc', '')
        score = sum(1 for kw in _HOT_KEYWORDS if kw in title)
        scored.append((score, item))

    # 按关键词匹配度排序，相关的排前面
    scored.sort(key=lambda x: x[0], reverse=True)

    # 取前40条，其中至少前15条是高相关的
    curated = [item for _, item in scored[:40]]

    return json.dumps(curated, ensure_ascii=False, indent=2)


def build_user_prompt(video_data_str):
    us_summary = _load_us_market_summary()
    us_block = f"""\n\n# 隔夜美股行情 (Overnight US Market)
以下是前一夜美股收盘行情，请将其纳入分析，特别关注半导体/科技股波动对A股相关板块（半导体、AI、科技ETF）的传导影响。
{us_summary}\n""" if us_summary else ''

    events_summary = _load_breaking_events_summary()
    events_block = f"""\n\n# 实时国际热点事件 (Breaking Events) — 这是你分析的核心输入
⚠️ 以下事件按影响力排序，前3个是最重要的事件，你的板块分析必须围绕这些事件展开。
请逐板块分析这些事件对原油/能源、化工/化肥、黄金/避险、军工等板块的产业链冲击。
{events_summary}\n""" if events_summary else ''

    return f"""# 输入数据格式 (Input Context)
以下是最新的市场舆情数据和国际事件摘要。
[社媒监控数据]:
{video_data_str}{us_block}{events_block}

# 分析逻辑 (Analytical Framework)
请在内心运行以下逻辑，无需在输出中展示推导过程：
1. **事件梳理**：找出当前最重大的 1-2 个核心事件（如战争/制裁/海峡封锁/能源危机等）
2. **板块拆解**：确定这些事件深度影响的 3-5 个行业板块，其中【原油/能源】板块必须分析
3. **逐板块深度分析**：对每个板块给出详尽的产业链冲击分析，包含具体数据、价格变动、成本传导
4. **中国映射**：明确事件对A股哪些板块/标的有利好或利空影响

# 输出格式要求 (Output Structure)
请严格按照以下 Markdown 格式输出。🔥部分是最核心的输出，每个板块都必须深入、详尽、有具体数据。

### 🚨 市场核心信号
[用一句话（30字以内）总结当前最核心的市场信号或风险点。]

### 🔥 板块深度影响分析
[这是你最重要的输出！根据当前核心事件，选择受影响最大的 3-5 个行业板块，逐一进行深度分析。
原油/能源板块必须包含在内。每个板块必须按以下格式输出完整、深入的分析。]

#### 🏭 [板块名称]：[一句话总结该板块受事件核心冲击]

**关键影响概览**
- [影响点1：具体数据+影响描述，必须有百分比或金额]
- [影响点2：同上]
- [影响点3：同上]
- [影响点4：如有]
- [中国影响：该板块对中国市场/A股的具体影响]

**产业链传导路径**
[用箭头链描述价格/成本传导，例如：原油价格↑ → 石脑油/燃料油价格↑ → 基础化学品(乙烯/丙烯)价格↑ → 塑料/纤维/涂料终端产品价格↑]

**量化影响预测**
| 指标 | 短期(1-3月) | 中期(3-12月) | 长期趋势 |
|------|-----------|------------|--------|
| [指标1] | [数据] | [数据] | [趋势] |
| [指标2] | [数据] | [数据] | [趋势] |
| [指标3] | [数据] | [数据] | [趋势] |

**投资策略**
[具体的投资建议：买入/卖出/持有什么标的或ETF，仓位建议，止盈止损位]

---

[重复以上格式，输出下一个板块分析]

### 💡 投资建议
#### 📌 各类持仓操作建议
[针对以下 9 类基金/板块类型，每类给出具体操作建议（加仓/减仓/持有/观望）和简明理由，要基于上面的板块分析]
- **🛢️ 原油/能源基金**：[建议和理由，必须基于上面的能源板块分析]
- **🧪 化工/化肥基金**：[建议和理由，必须基于上面的化工板块分析]
- **🥇 黄金类基金**：[建议和理由]
- **🥈 白银/贵金属**：[建议和理由]
- **📊 宽基指数（A500/中证500/沪深300）**：[建议和理由]
- **🤖 AI/科技/半导体**：[建议和理由]
- **💰 红利/价值**：[建议和理由]
- **⚔️ 军工/新能源/赛道股**：[建议和理由]
- **🍷 白酒/消费**：[建议和理由]

#### 🎯 综合建议
- **✅ 胜率较高的方向**：[2-3 个方向]
- **❌ 必须回避的绞肉机**：[高风险板块]
- **⏱️ 战术纪律**：[止损建议，至少 2 句话]

### 📊 情绪仪表盘参数 (System Data)
[必须在最末尾输出纯 JSON 代码块。参数值需为 0-100 的整数。]
```json
{{
  "hourly_dashboard": {{
    "market_temperature": <0-100>,
    "fomo_level": <0-100>,
    "panic_level": <0-100>,
    "divergence_index": <0-100>,
    "hot_assets": ["资产1", "资产2"],
    "action_signal": "<Aggressive Buy|Cautious Hold|Defensive|Strong Sell|Wait>"
  }}
}}
```"""


# ==================== AI 调用 ====================
def call_ai(items, provider_id=None, api_key=None, model=None, temperature=0.6):
    """调用 AI 大模型分析舆情数据"""
    provider_id = provider_id or DEFAULT_PROVIDER
    api_key = api_key or DEFAULT_API_KEY
    model = model or DEFAULT_MODEL

    provider = AI_PROVIDERS.get(provider_id)
    if not provider:
        raise ValueError(f'Unknown AI provider: {provider_id}')

    base_url = provider['base']
    # 优先选择与热点事件相关的社媒数据
    video_data_str = _build_curated_social_data(items)
    user_prompt = build_user_prompt(video_data_str)

    print(f'  🧠 调用 AI: {provider["name"]} / {model}')

    max_retries = 3
    for attempt in range(max_retries):
        resp = requests.post(
            base_url,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
            },
            json={
                'model': model,
                'messages': [
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': user_prompt},
                ],
                'temperature': temperature,
                'max_tokens': 16384,
            },
            timeout=120,
        )
        if resp.status_code == 429:
            wait = (attempt + 1) * 30
            print(f'  ⏳ 限频，等待 {wait}s 后重试 ({attempt+1}/{max_retries})...')
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break
    else:
        resp.raise_for_status()  # raise the last 429
    data = resp.json()
    content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
    if not content:
        raise ValueError('AI returned empty content')
    return content


# ==================== 解析 ====================
def extract_json(text):
    """从 AI 输出中提取 hourly_dashboard JSON"""
    # Try ```json block first
    m = re.search(r'```json\s*([\s\S]*?)\s*```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try loose match
    m = re.search(r'\{[\s\S]*"hourly_dashboard"[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Fallback
    return {
        'hourly_dashboard': {
            'market_temperature': 50,
            'fomo_level': 50,
            'panic_level': 50,
            'divergence_index': 50,
            'hot_assets': [],
            'action_signal': 'Wait'
        }
    }


def parse_radar_summary(text):
    """提取雷达摘要"""
    s = text.split('### 🚨')
    if len(s) > 1:
        part = s[1].split('###')[0]
        # Remove all header variants
        part = re.sub(r'(?:【当前小时】)?(?:情绪与预期差雷达|市场核心信号)', '', part).strip()
        return part
    return ''


def parse_kol_sections(text):
    """解析 KOL vs 散户博弈拆解 — 支持多行格式和内联格式"""
    sections = []
    # Split by either "#### 🎯" or "- **🎯" patterns
    parts = re.split(r'(?=(?:####?\s*)?(?:- \*\*)?\U0001F3AF\s*标的[/／]板块)', text)
    for sec in parts:
        if '\U0001F3AF' not in sec or '标的' not in sec:
            continue
        target = ''
        m = re.search(r'标的[/／]板块[*]*[：:]\s*(.*)', sec)
        if m: target = m.group(1).strip().rstrip('*').strip()
        # ---- KOL: ends at 🐑 散户 or ⚡ 预期差结论 ----
        kol = ''
        m = re.search(r'(?:聪明钱[/／])?KOL\s*观点\*\*[：:]\s*\n?\s*(.*?)(?=[\s\-]*\*\*\s*\U0001f411|[\s\-]*\*\*\s*\u26a1|\Z)', sec, re.DOTALL)
        if m:
            kol = re.sub(r'\s+', ' ', m.group(1)).strip()
        # ---- Retail: ends at ⚡ 预期差结论 ----
        retail = ''
        m = re.search(r'(?:羊群[/／])?散户\s*情绪\*\*[：:]\s*\n?\s*(.*?)(?=[\s\-]*\*\*\s*\u26a1|\Z)', sec, re.DOTALL)
        if m:
            retail = re.sub(r'\s+', ' ', m.group(1)).strip()
        # ---- Conclusion: ends at next 🎯 or ### ----
        conclusion = ''
        m = re.search(r'预期差结论\*\*[：:]\s*\n?\s*(.*?)(?=[\s\-]*\*\*\s*\U0001F3AF|\n###|\Z)', sec, re.DOTALL)
        if m:
            conclusion = re.sub(r'\s+', ' ', m.group(1)).strip()
        if target:
            sections.append({'target': target, 'kol': kol, 'retail': retail, 'conclusion': conclusion})
    return sections


def parse_actions(text):
    """解析操作指南"""
    parts = text.split('### 💡')
    s = parts[1] if len(parts) > 1 else ''

    # Parse per-holding-type recommendations
    holding_actions = []
    for m in re.finditer(r'- \*\*[\S]+\s+([^*]+)\*\*[：:]\s*(.*)', s):
        label = m.group(1).strip()
        advice = m.group(2).strip()
        if re.search(r'胜率|回避|绞肉|战术纪律', label):
            continue
        holding_actions.append({'label': label, 'advice': advice})

    bullish = ''
    m = re.search(r'胜率较高[^*]*\*\*[：:]\s*(.*)', s)
    if m: bullish = m.group(1).strip()
    bearish = ''
    m = re.search(r'(?:回避|绞肉)[^*]*\*\*[：:]\s*(.*)', s)
    if m: bearish = m.group(1).strip()
    tactical = ''
    m = re.search(r'战术纪律\*\*[：:]\s*(.*)', s)
    if m: tactical = m.group(1).strip()
    return {
        'holding_actions': holding_actions,
        'bullish': bullish,
        'bearish': bearish,
        'tactical': tactical,
    }


def parse_deep_analysis(text):
    """解析板块深度影响分析 — 按行业板块拆分，保留完整 markdown 内容"""
    analyses = []
    # 提取 ### 🔥 到下一个 ### 之间的内容
    m = re.search(r'###\s*🔥[^\n]*\n(.*?)(?=###\s*[⚖💡📊]|\Z)', text, re.DOTALL)
    if not m:
        return analyses
    deep_section = m.group(1)

    # 按 #### 🏭 板块标题 分割
    parts = re.split(r'####\s*🏭\s*', deep_section)
    for idx, part in enumerate(parts):
        if idx == 0:
            continue  # skip intro text before first sector
        part = part.strip()
        if not part:
            continue

        # 第一行是标题（板块名：冲击描述）
        lines = part.split('\n', 1)
        title = lines[0].strip().rstrip('*').strip()
        body = lines[1].strip() if len(lines) > 1 else ''

        # 提取投资策略（最后一个字段）
        strategy = ''
        m_strat = re.search(r'\*\*投资策略\*\*[：:]?\s*\n?(.*?)(?=\n---\s*$|\Z)', body, re.DOTALL)
        if m_strat:
            strategy = m_strat.group(1).strip()

        if title:
            analyses.append({
                'title': title,
                'content': body,  # 完整 markdown 内容
                'strategy': strategy,
            })
    return analyses


# ==================== 分析+缓存 ====================
def analyze_and_save(items, provider_id=None, api_key=None, model=None):
    """调用 AI 分析舆情数据，解析结果，保存缓存"""
    if not items:
        print('  ⚠️ 无数据，跳过 AI 分析')
        return None

    try:
        raw_text = call_ai(items, provider_id, api_key, model)
        dashboard = extract_json(raw_text)
        radar = parse_radar_summary(raw_text)
        actions = parse_actions(raw_text)
        deep_analysis = parse_deep_analysis(raw_text)

        result = {
            'raw_text': raw_text,
            'dashboard': dashboard,
            'radar_summary': radar,
            'kol_sections': [],
            'deep_analysis': deep_analysis,
            'actions': actions,
            'analysis_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'analysis_ts': int(time.time()),
            'model': model or DEFAULT_MODEL,
            'provider': provider_id or DEFAULT_PROVIDER,
            'data_count': len(items),
        }

        # 保存到缓存
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ANALYSIS_CACHE, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f'  🧠 AI 分析完成，已缓存: {ANALYSIS_CACHE}')
        return result

    except Exception as e:
        print(f'  ❌ AI 分析失败: {e}')
        return None


def load_analysis_cache():
    """读取 AI 分析缓存"""
    if not os.path.exists(ANALYSIS_CACHE):
        return None
    try:
        with open(ANALYSIS_CACHE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


# ==================== CLI ====================
if __name__ == '__main__':
    from collector import load_cache
    cache = load_cache()
    if cache and cache.get('items'):
        result = analyze_and_save(cache['items'])
        if result:
            d = result['dashboard'].get('hourly_dashboard', {})
            print(f'\n分析完成:')
            print(f'  温度: {d.get("market_temperature")}')
            print(f'  FOMO: {d.get("fomo_level")}')
            print(f'  恐慌: {d.get("panic_level")}')
            print(f'  分歧: {d.get("divergence_index")}')
            print(f'  信号: {d.get("action_signal")}')
    else:
        print('无缓存数据，请先运行 collector.py')
