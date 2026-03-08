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
你是一位顶尖的国际宏观局势分析师、地缘政治与大宗商品产业链影响专家。
你最强的能力在于：
1. 对国际热点事件（战争/制裁/供应链中断/货币政策/能源危机等）进行深层产业链冲击推演
2. 给出量化数据、具体影响路径和可操作的投资建议
3. 将事件影响与中国A股市场/大宗商品走势挂钩

你必须严格按照指定格式输出。最后的 JSON 部分必须是合法的 JSON 代码块。
你的分析必须有干货、有数据、有逻辑链条，绝不能输出空洞占位符。
重点：🔥 国际热点深度影响分析 是你最核心的输出，必须详尽、有深度。"""

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
    lines = ['[当前实时国际热点与市场事件]:']
    for e in events:
        lines.append(f"  [{e.get('category','')}] {e['title']} (影响:{e.get('impact',0):+d})")
        if e.get('reason'):
            lines.append(f"    原因: {e['reason']}")
        sp = e.get('sectors_positive', [])
        sn = e.get('sectors_negative', [])
        if sp:
            lines.append(f"    利好板块: {', '.join(sp)}")
        if sn:
            lines.append(f"    利空板块: {', '.join(sn)}")
    return '\n'.join(lines)


def build_user_prompt(video_data_str):
    us_summary = _load_us_market_summary()
    us_block = f"""\n\n# 隔夜美股行情 (Overnight US Market)
以下是前一夜美股收盘行情，请将其纳入分析，特别关注半导体/科技股波动对A股相关板块（半导体、AI、科技ETF）的传导影响。
{us_summary}\n""" if us_summary else ''

    events_summary = _load_breaking_events_summary()
    events_block = f"""\n\n# 实时国际热点事件 (Breaking Events)
以下是最新的国际热点事件和市场异动。请对影响最大的 2-3 个事件进行深度产业链冲击分析。
{events_summary}\n""" if events_summary else ''

    return f"""# 输入数据格式 (Input Context)
以下是最新的市场舆情数据和国际事件摘要。
[社媒监控数据]:
{video_data_str}{us_block}{events_block}

# 分析逻辑 (Analytical Framework)
请在内心运行以下逻辑，无需在输出中展示推导过程：
1. **事件影响推演**：对重大国际事件，从供应链、产业链、贸易路线、能源价格等维度分析直接和间接影响。
2. **量化影响估算**：给出具体的价格变动幅度、成本变动百分比、受影响产业规模等数据。
3. **中国映射**：明确事件对A股哪些板块/标的有利好或利空影响。

# 输出格式要求 (Output Structure)
请严格按照以下 Markdown 格式输出。🔥部分是最核心的输出，必须充分展开、有深度。

### 🚨 市场核心信号
[用一句话（30字以内）总结当前最核心的市场信号或风险点。]

### 🔥 国际热点深度影响分析
[这是你最重要的输出！从当前国际热点事件中，挑选影响最大的 2-3 个事件进行深度产业链冲击分析。每个事件必须按照以下格式输出。要求：数据充分、逻辑清晰、有投资指导意义。如果没有重大国际事件，则分析当前最重要的市场/政策事件。]

#### 📌 事件：[事件标题，一句话概括]
- **冲击概述**：[用 2-3 句话概括该事件对全球市场的核心冲击，必须有具体数据或量化参考]
- **产业链传导**：
  - [传导路径1：原料→中间品→终端产品 的价格传导链，给出具体百分比或幅度]
  - [传导路径2：另一条影响链]
  - [传导路径3：如有]
- **受冲击产业**：
  - [产业1]：[影响描述，含价格/成本变动幅度]
  - [产业2]：[影响描述]
  - [产业3]：[影响描述]
- **中国市场影响**：[对A股和国内产业的具体影响，包括受益板块和受损板块，给出具体标的或ETF]
- **时间节奏**：[短期(1-3月)/中期(3-12月)/长期 的影响节奏预判]
- **投资者策略**：[具体的仓位建议和操作方向，不要空泛]

### 💡 投资建议
#### 📌 各类持仓操作建议
[针对以下 7 类基金类型，每类给出具体操作建议（加仓/减仓/持有/观望）和简明理由，要基于上面的事件分析]
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
    video_data_str = json.dumps(items[:60], ensure_ascii=False, indent=2)
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
                'max_tokens': 8192,
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
    """解析国际热点深度影响分析"""
    analyses = []
    # 提取 ### 🔥 到下一个 ### 之间的内容
    m = re.search(r'###\s*🔥[^\n]*\n(.*?)(?=###\s*[⚖💡📊]|\Z)', text, re.DOTALL)
    if not m:
        return analyses
    deep_section = m.group(1)

    # 按 #### 📌 事件 分割, 跳过第一个元素（事件前的描述文字）
    parts = re.split(r'####\s*📌\s*事件[：:]\s*', deep_section)
    for idx, part in enumerate(parts):
        if idx == 0:
            continue  # skip intro text before first event
        part = part.strip()

        lines = part.split('\n', 1)
        title = lines[0].strip().rstrip('*').strip()
        body = lines[1] if len(lines) > 1 else ''

        def _extract(field, txt=body):
            pat = rf'\*\*{field}\*\*[：:]\s*(.*?)(?=\n-\s*\*\*|\n####|\Z)'
            hit = re.search(pat, txt, re.DOTALL)
            return hit.group(1).strip() if hit else ''

        def _extract_list(field, txt=body):
            pat = rf'\*\*{field}\*\*[：:]\s*\n(.*?)(?=\n-\s*\*\*|\n####|\Z)'
            hit = re.search(pat, txt, re.DOTALL)
            if not hit:
                return []
            return [re.sub(r'^[\s\-]+', '', l).strip()
                    for l in hit.group(1).strip().split('\n')
                    if l.strip().startswith('-') or l.strip().startswith('  -')]

        if title:
            analyses.append({
                'title': title,
                'overview': _extract('冲击概述'),
                'chains': _extract_list('产业链传导'),
                'industries': _extract_list('受冲击产业'),
                'chinaImpact': _extract('中国市场影响'),
                'timeline': _extract('时间节奏'),
                'strategy': _extract('投资者策略'),
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
