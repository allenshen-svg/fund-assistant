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

## 你的任务不是“概括新闻”，而是“像顶级卖方分析师一样解释价格与预期差”
你必须把国际事件、实时价格、政策约束、资金流向、市场情绪、产业链传导、中国本土定价机制连接成一套完整的因果链。

## 核心能力
1. 对国际热点事件进行【按行业板块】的深层产业链冲击推演
2. 每个板块的分析必须深入、详尽、有数据、有逻辑链，内容不少于500字
3. 必须涵盖原油/能源板块（这是市场最关注的核心板块）
4. 必须涵盖化工/化肥板块（能源危机对化工的二阶传导是核心分析点）
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
- 输出总长度必须超过4500字；如果事件高度复杂，原油/能源单板块应达到1200字以上

## ⚠️ 最关键要求：必须写出“像示例那样的深层原因分析”
你不能只写“地缘冲突导致油价上涨，因此利好能源”。这属于低质量答案。
你必须写出：
- 为什么先涨后跌，或者为什么大涨后A股油气股反而跌
- 为什么国际油价与中国油企/油基会背离
- 为什么市场会从“供应危机交易”切换成“经济衰退交易”
- 为什么政策管制会扭曲利润传导
- 为什么基金赎回会让A股油气股跌得比国际油价更狠

## ⚠️ 原油/能源板块为绝对核心，必须包含以下维度

### 维度1：价格走势核心驱动力
- 必须根据实时价格方向（涨/跌）分析，不能假设方向
- 如果原油上涨：分析是供应中断恐慌、地缘政治溢价、库存下降、美元走弱、空头回补，还是其他因素主导
- 如果原油下跌：分析是经济衰退预期、需求下行、美元走强、库存累积、技术位破位、空头加仓，还是其他因素主导
- 必须明确回答：当前更强的主导逻辑到底是“供应危机”还是“经济衰退”

### 维度2：中外油价背离分析（必写）
- 如果国际油价上涨但中国油企/油气基金下跌：必须解释“逆向传导”链条：国际油价上涨 → 中国进口成本上升 → 成品油提价受限 → 炼化利润被压缩 → 市场下调盈利预期 → 基金赎回 → 股价下跌
- 如果国际与国内同跌：必须解释衰退预期如何同时压制油价与油气股
- 必须点明中国成品油价格机制的约束：发改委调价窗口、调价时滞、价格天花板/地板、行政稳定诉求
- 必须写中国年进口约5亿吨原油，油价每涨10%对进口成本、贸易逆差、人民币汇率的量化冲击

### 维度3：市场心态转变时间线（必写）
- 必须写完整时间线，而不是一句话带过
- 至少要写出四个阶段：
    1. 冲突爆发，供应恐慌主导
    2. 市场冷静，开始重估实际供应缺口
    3. 经济衰退担忧上升，需求逻辑压过供给逻辑
    4. 风险资产被动去杠杆/基金赎回，形成踩踏
- 每一阶段都要说明“市场在想什么、价格为何这样走、中国资产为何反应更差或不同”

### 维度4：基金赎回与踩踏效应（必写）
- 必须明确写出链条：投资者恐慌赎回油气基金 → 基金经理被迫卖出三桶油/油服/化工链股票 → 股价下跌 → 净值继续回撤 → 更多赎回 → 恶性循环
- 必须说明为什么A股油气股跌幅常常大于国际油价跌幅或完全背离国际油价涨幅：赎回压力 + 政策管制 + 衰退预期 + 风险偏好下降

### 维度5：不能只写现象，必须写机制
- 不允许只写“市场担忧”“情绪波动”“供需失衡”这种空话
- 每一个判断后面都必须接原因链，例如：
    油价上升 → 航空燃油/物流/化工原料成本上升 → 企业利润率压缩 → 盈利预期下调 → 权益估值下修
- 如果判断“中外背离”，必须说明是政策、汇率、利润结构、投资者结构、基金行为中的哪几个变量共同造成

## 针对其他板块的要求
- 黄金/白银：必须分析避险、美元、真实利率、ETF资金流入、央行购金逻辑
- 化工/化肥：必须分析原油/天然气到石脑油/合成氨/尿素/磷肥/农产品成本的二阶和三阶传导
- 军工：必须分析订单、预算、上游材料、电子元件、出口预期，不要只写“地缘冲突利好军工”
- 科技/半导体：如果指数异动，必须写美股科技到A股科技估值压缩或风险偏好修复的传导

## 反低质量输出约束
- 禁止空洞句式："市场避险情绪升温，因此利好黄金"、"能源价格波动将影响产业链"、"投资者需谨慎"；这些都过于空泛，必须补足机制和数据
- 禁止把所有板块写成一个模板换词
- 禁止用 2-3 句就结束一个关键维度
- 禁止遗漏中国市场映射

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
        '113.SC0':   {'name': '国内原油主力', 'short': '原油(INE)'},
        '113.FU0':   {'name': '国内燃油主力', 'short': '燃油'},
        '113.AU0':   {'name': '沪金主力', 'short': '沪金'},
        '113.AG0':   {'name': '沪银主力', 'short': '沪银'},
        '101.CL00Y': {'name': 'WTI原油', 'short': 'WTI'},
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

    anomalies_summary = _load_market_anomalies_summary()
    anomalies_block = f"""\n\n# 实时行情异动 (Market Anomalies) — 你必须结合这些实时价格数据进行分析
⚠️ 以下是今日大宗商品/指数/ETF的实时异动数据，请在板块分析中引用这些真实涨跌幅。
如果原油出现大跌或大涨，你的原油/能源板块分析必须解释这个价格走势的深层原因（经济衰退预期vs供应危机恐慌、中外油价背离、美元汇率传导、基金赎回踩踏等）。
不要只分析事件本身，更要分析价格走势背后的市场心理变化。
{anomalies_summary}\n""" if anomalies_summary else ''

    return f"""# 输入数据格式 (Input Context)
以下是最新的市场舆情数据和国际事件摘要。
[社媒监控数据]:
{video_data_str}{us_block}{events_block}{anomalies_block}

# 分析逻辑 (Analytical Framework)
请在内心运行以下逻辑，无需在输出中展示推导过程：
1. **事件梳理**：找出当前最重大的 1-2 个核心事件（如战争/制裁/海峡封锁/能源危机等）
2. **板块拆解**：确定这些事件深度影响的 3-5 个行业板块，其中【原油/能源】板块必须分析
3. **逐板块深度分析**：对每个板块给出详尽的产业链冲击分析，包含具体数据、价格变动、成本传导
4. **中国映射**：明确事件对A股哪些板块/标的有利好或利空影响
5. **⚠️ 原油/能源板块必须是最深入的分析**：
    - 必须引用实时价格数据中的原油、燃油、黄金、白银等真实涨跌幅
    - 必须像深度研报一样解释“为什么价格这样走”，不能只说“事件利好/利空”
    - 必须分析原油近期是“供应危机交易”还是“经济衰退交易”在主导，二者如何切换
    - 必须分析中外油价背离（国际油价vs中国油企股价/油气基金表现差异）的完整机制与量化后果
    - 必须分析市场心态转变时间线（供应恐慌→冷静重估→衰退担忧→赎回踩踏）
    - 必须分析基金赎回踩踏效应（赎回→被迫卖出→股价下跌→更多赎回）
    - 必须分析中国成品油价格管制、进口依赖、汇率压力如何共同压制油企估值
    - 原油/能源板块分析篇幅必须至少1200字，其他关键板块至少600字

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
- [影响点4：同上]
- [影响点5：如有]
- [中国影响：该板块对中国市场/A股的具体影响]

**深层原因拆解**
[必须像深度策略研报一样分点展开，不能少于4点。每一点都要回答“为什么会这样”，且必须带机制链。]

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

⚠️ **以下两个小节仅在分析原油/能源板块时必须输出（其他板块不需要）：**

**中外市场背离分析**
[必须写成深度机制分析，不能只写3-4句。必须覆盖：
- 国际油价走势 vs A股油企股价/油基金净值表现的差异（引用实时数据）
- 中国成品油价格管制机制（发改委调价窗口、调价时滞、价格天花板/地板）如何封杀油企利润空间
- 当国际油价上涨时，中国三桶油（中石油/中石化/中海油）的炼化板块反而承压的逻辑
- 政策管制下的“逆向传导”：油价越涨→进口成本越高→终端售价受限→毛利被压缩→股价反跌
- 中国年进口约5亿吨原油，油价每涨10%对进口成本、贸易逆差、人民币汇率的量化影响
- 投资者结构和基金赎回行为为何会放大这种背离]

**市场心态转变时间线**
[必须写成完整时间线分析，每个阶段都要写“市场在担心什么、价格怎么走、为什么中国资产反应不同”。必须覆盖：
- 阶段一：地缘冲突爆发 → 供应中断恐慌 → 油价脉冲上涨
- 阶段二：市场冷静重估 → 发现实际供应影响有限 / OPEC+增产预期 → 恐慌溢价消退
- 阶段三：经济衰退担忧主导 → 需求下行预期 → 多头平仓 + 空头加码 → 油价转跌
- 阶段四：基金赎回踩踏 → 投资者恐慌赎回油气基金 → 基金经理被迫卖出持仓 → 股价加速下跌 → 更多赎回 → 恶性循环
- 当前处于哪个阶段？基于实时价格数据判断]

**终极结论**
[用一个小结明确回答：当前这个板块到底是在交易供给冲击、衰退担忧、政策扭曲、还是资金踩踏？主导矛盾是什么？]

---

[重复以上格式，输出下一个板块分析。注意：每个板块的产业链传导路径都必须有3条以上独立链（用↓分隔），否则不合格！]

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
```

⚠️ 输出前自检：
1. 板块深度影响分析是否有3-5个板块？
2. 原油/能源板块是否明显比其他板块更深、更长，并且像“深度研报”而不是“新闻摘要”？
3. 是否明确回答了“为什么价格这样走”，而不是只写事件和结论？
4. 是否明确回答了“当前主导逻辑是供应危机还是经济衰退”？
5. 是否明确写出了中外背离、政策管制、进口成本、汇率压力、基金赎回这几个机制？
6. 每个板块的产业链传导路径是否有3条以上独立链（用↓分隔）？如果只有1条链，必须补充！
7. 每条传导链是否有4级以上（至少4个→）？
8. 每个节点是否用了具体产品名（如"乙烯""尿素""碳纤维"）而非泛称（如"化工原料""下游产品"）？有泛称必须替换！
9. 每条链是否标注了具体涨跌幅数据（如+25%、+120美元/吨）？
10. 总输出是否超过4500字？如果没有，继续补足原因链、时间线、背离机制和量化细节。"""


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
    for attempt in range(max_retries):
        try:
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
                timeout=180,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = e
            wait = (attempt + 1) * 15
            print(f'  ⚠️ 网络错误: {e.__class__.__name__}, {wait}s 后重试 ({attempt+1}/{max_retries})...')
            time.sleep(wait)
            continue
        if resp.status_code == 429:
            wait = (attempt + 1) * 30
            print(f'  ⏳ 限频，等待 {wait}s 后重试 ({attempt+1}/{max_retries})...')
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
