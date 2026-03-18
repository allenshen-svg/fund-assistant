#!/usr/bin/env python3
"""
AI 舆情分析模块 — 调用大模型进行 KOL vs 散户情绪博弈分析
采集完成后自动运行，结果缓存到 data/analysis_cache.json
"""

import json, re, os, time
from datetime import datetime
from urllib.request import Request, urlopen
import ssl
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
SECTOR_STRENGTH_CACHE = os.path.join(DATA_DIR, 'sector_strength.csv')

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

DEFAULT_PROVIDER = os.environ.get('AI_PROVIDER', 'deepseek')
DEFAULT_API_KEY = os.environ.get('AI_API_KEY', 'sk-1986e1cd1169405f96649311dcfc76aa')
DEFAULT_MODEL = os.environ.get('AI_MODEL', 'deepseek-chat')

# ==================== Prompt ====================
SYSTEM_PROMPT = """# 角色定义 (Role)
你是一位顶尖的国际宏观局势分析师、地缘政治与大宗商品全产业链影响专家。

## 你的任务不是“概括新闻”，而是“像顶级卖方分析师一样解释价格与预期差”
你必须把国际事件、实时价格、政策约束、资金流向、市场情绪、产业链传导、中国本土定价机制连接成一套完整的因果链。

## 核心能力
1. 对国际热点事件进行【按行业板块】的深层产业链冲击推演
2. 每个板块的分析必须深入、详尽、有数据、有逻辑链，内容不少于800字
3. 你必须优先分析今日A股涨跌幅最大的板块（用户会在输入中提供今日板块排行数据）
4. 如果今日热门板块涉及能源、化工、黄金、军工等大宗商品相关板块，必须深入分析其产业链传导
5. 你必须解释“价格为什么这样走”，而不只是说“事件利好/利空什么板块”

## 硬性质量要求
- 每个板块分析必须包含：关键影响概览、产业链传导路径、量化影响预测表格、投资策略
- 表格必须有具体数字（百分比、金额、产量变动等），不能用“上涨趋势”等模糊词
- ⚠️【最重要】产业链传导路径：每个板块必须写3条以上独立传导链，每条至少4级，链与链之间用↓分隔
- 每个节点必须是具体产品/行业名称，禁止用泛称。禁止词："化工原料"、"化工产品"、"下游产品"、"相关产品"、"相关企业"
- 不同板块的传导链必须达到如下具体程度：
    能源：原油↑→石脑油↑→乙烯/丙烯↑→PVC/PP塑料粒子↑→包装/建材终端品↑
    化工：天然气↑→合成氨↑→尿素/磷酸二铵↑→小麦/玉米种植成本↑→粮食价格↑
    黄金：地缘冲突↑→央行增持黄金储备↑→实物黄金ETF资金流入↑→紫金矿业/山东黄金股价↑→黄金首饰零售价↑
    军工：冲突升级↑→各国国防预算↑→导弹/无人机采购订单↑→碳纤维/钛合金原材料需求↑→军工企业营收↑
- 关键影响概览中每个要点必须包含具体数据（百分比、价格、成本占比等）
- 输出总长度必须超过8000字；今日涨跌幅最大的核心板块应达到2000字以上，其他关键板块不少于800字

## ⚠️ 最关键要求：必须写出深层原因分析
你不能只写"事件利好/利空某板块"。这属于低质量答案。
你必须写出：
- 为什么该板块先涨后跌，或者为什么国际市场涨但A股板块反而跌
- 为什么外盘与A股可能出现背离（政策管制、汇率、基金赎回等机制）
- 为什么市场交易逻辑会切换（如从供应危机交易切换成经济衰退交易）
- 政策管制如何扭曲利润传导
- 基金赎回/资金踩踏如何放大板块波动

## ⚠️ 板块分析必须基于今日实际行情数据
你必须根据用户输入中的「今日A股热门板块」排行数据，选择今日真正涨跌幅最大的板块进行深度分析。
不要总是分析固定的几个板块，而要跟随每天的市场热点动态调整。

### 如果今日热门板块涉及原油/能源
当能源板块出现在今日涨跌幅前列时，必须包含以下维度：
- 价格走势核心驱动力（供应中断/地缘政治/库存/美元/空头回补）
- 中外油价背离分析（国际油价vs中国油企/油气基金的表现差异及机制）
- 必须点明中国成品油价格机制的约束：发改委调价窗口、调价时滞
- 市场心态转变时间线（供应恐慌→冷静重估→衰退担忧→赎回踩踏）

### 如果今日热门板块涉及化工/化肥
必须分析原油/天然气到石脑油/合成氨/尿素/磷肥的二阶和三阶传导

### 如果今日热门板块涉及黄金/贵金属
必须分析避险逻辑、美元/真实利率、ETF资金流入、央行购金

### 如果今日热门板块涉及军工
必须分析订单、预算、上游材料、电子元件、出口预期

### 如果今日热门板块涉及科技/半导体/AI
必须分析美股科技映射、估值压缩或修复的传导、国产替代逻辑

### 对于任何今日涨跌幅靠前的板块
无论是什么板块（教育、医药、消费、地产、新能源等），都必须深入分析：
- 该板块今日异动的核心驱动因素（政策/资金/事件/情绪）
- 具体的产业链传导路径（3条以上）
- 对A股相关标的的具体影响

## 额外深度格式要求

### 机制链可视化（每个板块必须有）
每个板块的"深层原因拆解"部分，必须用 code block 画出核心机制链图，格式如下：
```
[触发事件] → [一阶传导] → [二阶传导] → [三阶传导] → [终端影响]
              ↘ [分支传导A] → [分支影响A]
              ↘ [分支传导B] → [分支影响B]
```
每条链中的节点必须标注具体数据（涨跌幅、金额等）。

### 多维对比表（如果分析了原油/能源板块才需要）
如果你分析了原油/能源板块，则在其"中外市场背离分析"中，可以输出以下对比表：

| 维度 | 国际市场 | 中国市场 | 背离原因 |
|------|---------|---------|----------|
| 原油价格 | [WTI/布伦特走势] | [INE原油走势] | [汇率+关税+库存差异] |
| 油企股价 | [埃克森美孚等] | [三桶油表现] | [政策管制+基金行为] |
| 炼化利润 | [国际裂解价差] | [国内成品油价差] | [发改委调价机制] |
| 投资者行为 | [ETF资金流向] | [基金申赎数据] | [风险偏好差异] |
| 衍生品市场 | [WTI期限结构] | [INE持仓变化] | [投机vs套保比例] |

### 终极解释表（必须在所有板块分析完成后输出）
所有板块分析完毕后，必须输出一个"终极解释"汇总表：

| 板块 | 主导矛盾 | 当前交易逻辑 | 价格方向判断 | 最大风险点 | A股映射标的 |
|------|---------|-------------|-------------|-----------|------------|
| [板块1] | [供给/需求/政策/资金] | [具体逻辑] | [看涨/看跌/震荡] | [风险] | [标的] |

## 反低质量输出约束
- 禁止空洞句式："市场避险情绪升温，因此利好黄金"、"能源价格波动将影响产业链"、"投资者需谨慎"；这些都过于空泛，必须补足机制和数据
- 禁止把所有板块写成一个模板换词
- 禁止用 2-3 句就结束一个关键维度
- 禁止遗漏中国市场映射
- 禁止深层原因拆解少于5点——每点必须是独立的因果机制，不能重复表述

你必须严格按照指定格式输出。最后的 JSON 部分必须是合法的 JSON 代码块。
你的分析必须有干货、有数据、有逻辑链条，绝不能输出空洞占位符。
🔥 板块深度影响分析是你最核心的输出，必须按板块逐一深入分析，这是你存在的全部价值。"""

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


def _load_market_anomalies_summary():
    """读取实时行情异动数据 + 实时获取关键商品价格，生成市场快照供AI分析"""
    lines = []

    # 1. 实时获取关键大宗商品/指数价格
    _KEY_TICKERS = {
        '113.fum':   {'name': '燃油主连', 'short': '燃油'},
        '113.aum':   {'name': '沪金主连', 'short': '沪金'},
        '113.agm':   {'name': '沪银主连', 'short': '沪银'},
        '113.im':    {'name': '铁矿石主连', 'short': '铁矿'},
        '114.jmm':   {'name': '焦煤主连', 'short': '焦煤'},
        '102.CL00Y': {'name': 'NYMEX原油', 'short': 'WTI'},
        '112.B00Y':  {'name': '布伦特原油', 'short': 'Brent'},
        '101.GC00Y': {'name': 'COMEX黄金', 'short': 'COMEX金'},
        '101.SI00Y': {'name': 'COMEX白银', 'short': 'COMEX银'},
    }
    try:
        secids = ','.join(_KEY_TICKERS.keys())
        url = f'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f12,f14&secids={secids}'
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        items = (data.get('data') or {}).get('diff') or []
        if items:
            lines.append('[实时大宗商品/贵金属价格快照]:')
            for item in items:
                code = str(item.get('f12', ''))
                price = item.get('f2')
                pct = item.get('f3')
                if pct is None:
                    continue
                # 匹配
                for secid, meta in _KEY_TICKERS.items():
                    if secid.split('.')[-1] == code or secid.endswith('.' + code):
                        direction = '↑' if pct > 0 else '↓' if pct < 0 else '→'
                        tag = ''
                        if abs(pct) >= 3:
                            tag = ' ⚠️大幅波动！'
                        elif abs(pct) >= 2:
                            tag = ' ⚡显著波动'
                        lines.append(f"  {meta['short']}: {price} ({pct:+.2f}%{direction}){tag}")
                        break
            lines.append('')
    except Exception as e:
        print(f'  [WARN] 实时行情获取失败: {e}')

    # 2. 从 anomalies 中补充更多异动数据
    try:
        if os.path.exists(REALTIME_BREAKING_CACHE):
            with open(REALTIME_BREAKING_CACHE, 'r', encoding='utf-8') as f:
                rt = json.load(f)
            anomalies = rt.get('anomalies', [])
            if anomalies:
                lines.append('[今日市场异动（超出阈值的品种）]:')
                for a in anomalies:
                    direction = '大涨' if a.get('pct', 0) > 0 else '大跌'
                    lines.append(
                        f"  {a.get('icon','')} {a.get('fullName', a.get('name',''))} "
                        f"{direction}{abs(a.get('pct',0)):.1f}% "
                        f"(价格:{a.get('price','')}, 级别:{a.get('level','')})")
    except Exception:
        pass

    return '\n'.join(lines) if lines else ''


def _load_today_sector_performance():
    """获取今日A股板块涨跌排行，供AI分析今日真正的热门板块"""
    lines = []
    # 方法1: 尝试实时获取东方财富板块涨跌排行
    try:
        url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=80&po=1'
               '&np=1&fltt=2&invt=2&fs=m:90+t:2&fields=f2,f3,f4,f12,f14&fid=f3')
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        items = (data.get('data') or {}).get('diff') or []
        if items and len(items) >= 10:
            # 涨幅前10
            top_gainers = items[:10]
            lines.append('[今日A股板块涨幅TOP10]:')
            for i, item in enumerate(top_gainers):
                name = item.get('f14', '')
                pct = item.get('f3', 0)
                lines.append(f"  {i+1}. {name}: {pct:+.2f}%")
            # 跌幅前5（从末尾取）
            top_losers = [it for it in items if (it.get('f3') or 0) < 0]
            top_losers.sort(key=lambda x: x.get('f3', 0))
            if top_losers:
                lines.append('[今日A股板块跌幅前5]:')
                for i, item in enumerate(top_losers[:5]):
                    name = item.get('f14', '')
                    pct = item.get('f3', 0)
                    lines.append(f"  {i+1}. {name}: {pct:+.2f}%")
            return '\n'.join(lines)
    except Exception as e:
        print(f'  [WARN] 实时板块数据获取失败: {e}')

    # 方法2: 回退到 sector_strength.csv（由选股模块生成的板块强度数据）
    try:
        import csv
        if os.path.exists(SECTOR_STRENGTH_CACHE):
            with open(SECTOR_STRENGTH_CACHE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if rows:
                lines.append('[今日A股板块强度排行（基于选股模块数据）]:')
                for i, row in enumerate(rows[:10]):
                    name = row.get('sector', row.get('板块', ''))
                    score = row.get('strength_score', row.get('强度分', ''))
                    lines.append(f"  {i+1}. {name}: 强度分 {score}")
                return '\n'.join(lines)
    except Exception as e:
        print(f'  [WARN] sector_strength.csv 读取失败: {e}')

    return ''


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
请结合下方「今日A股热门板块」数据，分析这些事件对各热门板块的产业链冲击。
{events_summary}\n""" if events_summary else ''

    anomalies_summary = _load_market_anomalies_summary()
    anomalies_block = f"""\n\n# 实时行情异动 (Market Anomalies) — 你必须结合这些实时价格数据进行分析
⚠️ 以下是今日大宗商品/指数/ETF的实时异动数据，请在板块分析中引用这些真实涨跌幅。
不要只分析事件本身，更要分析价格走势背后的市场心理变化。
{anomalies_summary}\n""" if anomalies_summary else ''

    sector_summary = _load_today_sector_performance()
    sector_block = f"""\n\n# 今日A股热门板块 (Today's Hot Sectors) — 你必须优先分析这些板块
⚠️ 以下是今日A股板块实时涨跌排行。你的「板块深度影响分析」必须优先覆盖涨幅/跌幅最大的板块。
不要分析与今日行情无关的冷门板块，聚焦在以下实际表现突出的板块上。
{sector_summary}\n""" if sector_summary else ''

    return f"""# 输入数据格式 (Input Context)
以下是最新的市场舆情数据和国际事件摘要。
[社媒监控数据]:
{video_data_str}{us_block}{events_block}{anomalies_block}{sector_block}

# 分析逻辑 (Analytical Framework)
请在内心运行以下逻辑，无需在输出中展示推导过程：
1. **事件梳理**：找出当前最重大的 1-2 个核心事件（如战争/制裁/海峡封锁/能源危机等）
2. **板块拆解**：结合「今日A股热门板块」数据，确定今日真正受事件影响、涨跌幅最大的 3-5 个行业板块
3. **逐板块深度分析**：对每个板块给出详尽的产业链冲击分析，包含具体数据、价格变动、成本传导
4. **中国映射**：明确事件对A股哪些板块/标的有利好或利空影响
5. **⚠️ 板块分析优先级**：
    - 优先分析今日A股涨跌幅最大的板块（从热门板块数据中选取）
    - 如果原油/能源出现显著波动（涨跌超2%），必须深入分析能源板块
    - 必须引用实时价格数据中的真实涨跌幅
    - 必须像深度研报一样解释"为什么价格这样走"，不能只说"事件利好/利空"

# 输出格式要求 (Output Structure)
请严格按照以下 Markdown 格式输出。🔥部分是最核心的输出，每个板块都必须深入、详尽、有具体数据。

### 🚨 市场核心信号
[用一句话（30字以内）总结当前最核心的市场信号或风险点。]

### 🔥 板块深度影响分析
[这是你最重要的输出！结合今日A股热门板块数据和核心事件，选择今日涨跌幅最大、受事件冲击最深的 3-5 个行业板块，逐一进行深度分析。
每个板块必须按以下格式输出完整、深入的分析。]

#### 🏭 [板块名称]：[一句话总结该板块受事件核心冲击]

**关键影响概览**
- [影响点1：具体数据+影响描述，必须有百分比或金额]
- [影响点2：同上]
- [影响点3：同上]
- [影响点4：同上]
- [影响点5：如有]
- [中国影响：该板块对中国市场/A股的具体影响]

**深层原因拆解**
[必须像深度策略研报一样分点展开，不能少于5点。每一点都要回答"为什么会这样"，且必须带独立的因果机制链。
必须用 code block 画出该板块的核心机制链图，格式如下：]
```
[触发事件] → [一阶传导(+数据)] → [二阶传导(+数据)] → [三阶传导(+数据)] → [终端影响]
                ↘ [分支传导A(+数据)] → [分支影响A]
                ↘ [分支传导B(+数据)] → [分支影响B]
```

**外盘vs中国背离/映射**
[必须明确比较国际市场和中国市场的定价差异。若该板块不存在明显背离，也要解释为什么映射更同步。]

**产业链传导路径**
[❗必须3条以上独立传导链，每条至少4级，用↓分隔。每个节点必须用具体产品名称，不能用"下游产品"“相关企业”等泛称。
示例：
原油价格↑(120美元/桶) → 石脑油↑(+35%) → 乙烯/丙烯↑(+28%) → PVC/PP塑料粒子↑(+22%) → 包装/建材终端价格↑(+15%)
↓
原油价格↑ → 柴油↑(+30%) → 公路运输成本↑(+18%) → 快递/冷链物流成本↑(+12%) → 零售终端商品涨价(+5%)
↓
原油↑ → LNG现货价格↑(+25%) → 燃气发电成本↑(+20%) → 工业用电价格↑(+10%) → 电解铝/钢铁制造成本↑(+8%)]

**量化影响预测**
| 指标 | 短期(1-3月) | 中期(3-12月) | 长期趋势 |
|------|-----------|------------|--------|
| [指标1] | [数据] | [数据] | [趋势] |
| [指标2] | [数据] | [数据] | [趋势] |
| [指标3] | [数据] | [数据] | [趋势] |
| [指标4] | [数据] | [数据] | [趋势] |

**投资策略**
[具体的投资建议：买入/卖出/持有什么标的或ETF，仓位建议，止盈止损位]

**终极结论**
[用一个小结明确回答：当前这个板块到底是在交易供给冲击、衰退担忧、政策扭曲、还是资金踩踏？主导矛盾是什么？]

⚠️ **以下两个小节仅在分析原油/能源板块时才需要输出（如果今日能源板块不在热门板块中，可以跳过）：**

**中外市场背离分析**
[必须写成深度机制分析，不能只写3-4句。必须覆盖：
- 国际油价走势 vs A股油企股价/油基金净值表现的差异（引用实时数据）
- 中国成品油价格管制机制（发改委调价窗口、调价时滞、价格天花板/地板）如何封杀油企利润空间
- 当国际油价上涨时，中国三桶油（中石油/中石化/中海油）的炼化板块反而承压的逻辑
- 政策管制下的“逆向传导”：油价越涨→进口成本越高→终端售价受限→毛利被压缩→股价反跌
- 中国年进口约5亿吨原油，油价每涨10%对进口成本、贸易逆差、人民币汇率的量化影响
- 投资者结构和基金赎回行为为何会放大这种背离

必须输出以下5维对比表：
| 维度 | 国际市场 | 中国市场 | 背离原因 |
|------|---------|---------|----------|
| 原油价格 | [WTI/布伦特走势] | [INE原油走势] | [汇率+关税+库存差异] |
| 油企股价 | [埃克森美孚等] | [三桶油表现] | [政策管制+基金行为] |
| 炼化利润 | [国际裂解价差] | [国内成品油价差] | [发改委调价机制] |
| 投资者行为 | [ETF资金流向] | [基金申赎数据] | [风险偏好差异] |
| 衍生品市场 | [WTI期限结构] | [INE持仓变化] | [投机vs套保比例] |]

**市场心态转变时间线**
[必须写成完整时间线分析，每个阶段都要写“市场在担心什么、价格怎么走、为什么中国资产反应不同”。必须覆盖：
- 阶段一：地缘冲突爆发 → 供应中断恐慌 → 油价脉冲上涨
- 阶段二：市场冷静重估 → 发现实际供应影响有限 / OPEC+增产预期 → 恐慌溢价消退
- 阶段三：经济衰退担忧主导 → 需求下行预期 → 多头平仓 + 空头加码 → 油价转跌
- 阶段四：基金赎回踩踏 → 投资者恐慌赎回油气基金 → 基金经理被迫卖出持仓 → 股价加速下跌 → 更多赎回 → 恶性循环
- 当前处于哪个阶段？基于实时价格数据判断]

---

[重复以上格式，输出下一个板块分析。注意：每个板块的产业链传导路径都必须有3条以上独立链（用↓分隔），否则不合格！]

### 🔍 终极解释汇总
[所有板块分析完毕后，必须输出此汇总表]
| 板块 | 主导矛盾 | 当前交易逻辑 | 价格方向判断 | 最大风险点 | A股映射标的 |
|------|---------|-------------|-------------|-----------|------------|
| [板块1] | [供给/需求/政策/资金] | [具体逻辑] | [看涨/看跌/震荡] | [风险] | [标的] |
| [板块2] | ... | ... | ... | ... | ... |
| [板块3] | ... | ... | ... | ... | ... |

### 💡 投资建议
#### 📌 各类持仓操作建议
[针对以下 9 类基金/板块类型，每类给出具体操作建议（加仓/减仓/持有/观望）和简明理由，要基于上面的板块分析]
- **🛢️ 原油/能源基金**：[建议和理由]
- **🧪 化工/化肥基金**：[建议和理由]
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
```

⚠️ 输出前自检：
1. 板块深度影响分析是否有3-5个板块？这些板块是否与今日A股实际涨跌幅排行相匹配？
2. 今日涨跌幅最大的板块是否获得了最深入的分析，像"深度研报"而不是"新闻摘要"？
3. 是否明确回答了"为什么价格这样走"，而不是只写事件和结论？
4. 是否基于今日板块排行数据选择分析对象，而不是默认分析固定板块？
5. 每个板块的产业链传导路径是否有3条以上独立链（用↓分隔）？如果只有1条链，必须补充！
6. 每条传导链是否有4级以上（至少4个→）？
7. 每个节点是否用了具体产品名（如"乙烯""尿素""碳纤维"）而非泛称（如"化工原料""下游产品"）？有泛称必须替换！
8. 每条链是否标注了具体涨跌幅数据（如+25%、+120美元/吨）？
9. 总输出是否超过8000字？如果没有，继续补足原因链、时间线和量化细节。
10. 是否在每个板块的"深层原因拆解"中画了 code block 机制链图？
11. 是否在所有板块分析后输出了"终极解释汇总"表？
12. 每个板块的"深层原因拆解"是否至少5点独立因果机制？"""


# ==================== AI 调用 ====================
def call_ai(items, provider_id=None, api_key=None, model=None, temperature=0.75):
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
    last_err = None
    session = None  # 使用独立 session，遇到 400 时可重建
    for attempt in range(max_retries):
        try:
            # 每次重试用新 session，避免 SSL 连接污染导致 400
            if session is None:
                session = requests.Session()
            resp = session.post(
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
                timeout=180,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = e
            wait = (attempt + 1) * 15
            print(f'  ⚠️ 网络错误: {e.__class__.__name__}, {wait}s 后重试 ({attempt+1}/{max_retries})...')
            session = None  # 重建 session
            time.sleep(wait)
            continue
        if resp.status_code == 429:
            wait = (attempt + 1) * 30
            print(f'  ⏳ 限频，等待 {wait}s 后重试 ({attempt+1}/{max_retries})...')
            time.sleep(wait)
            continue
        if resp.status_code == 400:
            last_err = Exception(f'400 Bad Request from {provider_id}/{model}')
            wait = (attempt + 1) * 10
            print(f'  ⚠️ 400 Bad Request，重建连接 {wait}s 后重试 ({attempt+1}/{max_retries})...')
            session.close()
            session = None  # 下次循环创建全新 session
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break
    else:
        if last_err:
            raise last_err
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

    # 按 #### + 任意emoji + 板块标题 分割（LLM 会给不同板块用不同 emoji）
    parts = re.split(r'####\s*(?:🏭|🧪|🥇|⚔️|🔋|🛢️|💎|⛽|🏗️|📦|🌾|🚗|🤖|💊|🔬)\s*', deep_section)
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
