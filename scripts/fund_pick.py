#!/usr/bin/env python3
"""
定时选基金/股票 — 每日 14:50 自动执行
1. 从东方财富拉取板块资金流向、指数行情、大宗商品、板块TOP基金/个股
2. 读取 hot_events.json 中的热点 & 板块热力
3. 构建 prompt，调用 AI（智谱/DeepSeek）
4. 结果存入 data/fund_pick.json
"""

import os, sys, json, re, time, traceback
from datetime import datetime, date
import urllib.request
import urllib.error
import urllib.parse

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
PICK_FILE = os.path.join(DATA_DIR, 'fund_pick.json')

# ==================== 东方财富 API ====================

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    'Referer': 'https://quote.eastmoney.com/',
}

def _get_json(url, timeout=10):
    """GET JSON from URL"""
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def fetch_indices():
    """获取主要指数行情"""
    codes = [
        {'code': '1.000001', 'name': '上证指数'},
        {'code': '0.399001', 'name': '深证成指'},
        {'code': '0.399006', 'name': '创业板指'},
        {'code': '1.000300', 'name': '沪深300'},
    ]
    secids = ','.join(c['code'] for c in codes)
    url = f'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f12,f14&secids={secids}'
    try:
        data = _get_json(url)
        diff = data.get('data', {}).get('diff', [])
        return [{'code': d.get('f12', ''), 'name': d.get('f14', ''), 'price': d.get('f2'),
                 'pct': d.get('f3'), 'change': d.get('f4')} for d in diff]
    except Exception as e:
        print(f'[fund_pick] 获取指数失败: {e}')
        return []


def fetch_commodities():
    """获取大宗商品行情"""
    codes = [
        {'code': '113.aum', 'name': '沪金主连', 'icon': '🥇'},
        {'code': '113.agm', 'name': '沪银主连', 'icon': '🥈'},
        {'code': '113.cum', 'name': '沪铜主连', 'icon': '🔩'},
        {'code': '113.fum', 'name': '燃油主连', 'icon': '🛢️'},
        {'code': '113.rbm', 'name': '螺纹钢主连', 'icon': '🏗️'},
        {'code': '113.im',  'name': '铁矿石主连', 'icon': '⛏️'},
    ]
    secids = ','.join(c['code'] for c in codes)
    url = f'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f12,f14&secids={secids}'
    try:
        data = _get_json(url)
        diff = data.get('data', {}).get('diff', [])
        result = []
        for i, d in enumerate(diff):
            c = codes[i] if i < len(codes) else {}
            pct = d.get('f3')
            pct_str = f'{pct:+.2f}%' if pct is not None else '--'
            result.append({
                'name': c.get('name', d.get('f14', '')),
                'icon': c.get('icon', ''),
                'price': d.get('f2'),
                'pct': pct,
                'pctStr': pct_str,
            })
        return result
    except Exception as e:
        print(f'[fund_pick] 获取商品失败: {e}')
        return []


def fetch_sector_flows():
    """获取板块资金流向"""
    url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&po=1&np=1'
           '&fltt=2&invt=2&fid=f62&fs=m:90+t:2'
           '&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87')
    try:
        data = _get_json(url)
        diff = data.get('data', {}).get('diff', [])
        return [{'code': d.get('f12', ''), 'name': d.get('f14', ''),
                 'pct': d.get('f3'), 'mainNet': d.get('f62'), 'mainPct': d.get('f184')}
                for d in diff]
    except Exception as e:
        print(f'[fund_pick] 获取板块资金流向失败: {e}')
        return []


def fetch_sector_top_funds(sector_name, top_n=4):
    """获取板块内TOP基金"""
    kw_map = {
        '半导体': '半导体', '电子': '半导体', 'AI算力': '人工智能',
        '人工智能': '人工智能', '计算机': '科技', '机器人': '机器人',
        '通信': '通信', '军工': '军工', '国防军工': '军工',
        '医药生物': '医药', '医药': '医药', '创新药': '医药',
        '食品饮料': '消费', '消费': '消费', '白酒': '白酒',
        '新能源': '新能源', '光伏': '光伏', '锂电': '新能源',
        '新能源车': '新能源车', '电力设备': '电力',
        '有色金属': '有色', '贵金属': '黄金', '黄金': '黄金',
        '原油': '原油', '石油石化': '原油', '能源': '能源',
        '银行': '银行', '非银金融': '证券', '证券': '证券',
        '煤炭': '煤炭', '钢铁': '钢铁', '基建': '基建',
        '房地产': '地产', '交通运输': '交通',
        '公用事业': '公用事业', '农林牧渔': '农业',
        '传媒': '传媒', '纺织服饰': '消费',
    }
    kw = kw_map.get(sector_name, sector_name)
    url = (f'https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchPageByField.ashx'
           f'?key={urllib.parse.quote(kw)}&pageindex=1&pagesize={top_n}'
           f'&Sort=SYL_3Y&SortType=Desc&_={int(time.time()*1000)}')
    try:
        data = _get_json(url)
        datas = data.get('Datas', [])
        return [{'code': d.get('CODE', ''), 'name': d.get('NAME', '')} for d in datas[:top_n]]
    except Exception as e:
        print(f'[fund_pick] 获取 {sector_name} TOP基金失败: {e}')
        return []


def fetch_sector_top_stocks(sector_code, top_n=4):
    """获取板块内领涨个股"""
    url = (f'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz={top_n}&po=1&np=1'
           f'&fltt=2&invt=2&fid=f3&fs=b:{sector_code}'
           f'&fields=f12,f14,f2,f3,f4,f15,f16,f17,f20,f115')
    try:
        data = _get_json(url)
        diff = data.get('data', {}).get('diff', [])
        return [{'code': d.get('f12', ''), 'name': d.get('f14', ''),
                 'price': d.get('f2'), 'pct': d.get('f3'),
                 'marketCap': d.get('f20'), 'pe': d.get('f115')} for d in diff[:top_n]]
    except Exception as e:
        print(f'[fund_pick] 获取 {sector_code} 个股失败: {e}')
        return []


def load_hot_events():
    """从 data/hot_events.json 读取热点事件 & 板块热力"""
    path = os.path.join(DATA_DIR, 'hot_events.json')
    if not os.path.exists(path):
        return [], []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        events = data.get('events', [])
        heatmap = data.get('heatmap', [])
        return events, heatmap
    except Exception:
        return [], []


# ==================== AI 调用 ====================

FUND_PICK_SYSTEM_PROMPT = """你是一位管理百亿规模基金的资深基金经理，擅长从板块轮动和资金流向中发现投资机会。

## 你的任务
根据当前增长良好的板块数据（涨幅、资金净流入）、板块内TOP基金和领涨个股，站在专业基金经理角度，选出最值得投资的基金和股票。

## 分析方法论
1. 先判断板块景气度：资金面（主力净流入持续性）+ 基本面（行业增长逻辑）+ 催化剂（政策/事件驱动）
2. 再从板块中精选标的：基金看3个月业绩支撑+规模适中+跟踪误差小；股票看业绩增速+估值合理+资金关注度
3. 用因果链分析WHY：为什么这个板块现在值得配置，是短期情绪还是中期趋势

## 输出要求（JSON格式）：
{
  "sectorOverview": "当前市场板块轮动总览（3-5句话，哪些板块在领涨，资金在向哪里流动，背后的宏观逻辑是什么）",
  "fundPicks": [
    {
      "code": "基金代码（6位数字）",
      "name": "基金真实名称",
      "sector": "所属板块",
      "sectorPct": "板块今日涨幅如+2.79%",
      "rating": "strong_buy|buy|accumulate",
      "confidence": 60-95,
      "whyThisSector": "为什么看好这个板块（2-3句话，用因果链A→B→C格式）",
      "whyThisPick": "为什么选这只基金（2-3句话，看业绩/规模/跟踪精度）",
      "supportAnalysis": "过去3个月的支撑面分析（3-5句话）",
      "riskPoints": "主要风险点（1-2句话）",
      "strategy": "建议买入策略",
      "targetReturn": "预期收益区间"
    }
  ],
  "stockPicks": [
    {
      "code": "股票代码",
      "name": "股票真实名称",
      "sector": "所属板块",
      "sectorPct": "板块今日涨幅如+2.79%",
      "rating": "strong_buy|buy|accumulate",
      "confidence": 60-95,
      "whyThisSector": "为什么看好这个板块",
      "whyThisPick": "为什么选这只股票",
      "supportAnalysis": "过去3个月的支撑面分析",
      "riskPoints": "主要风险点",
      "strategy": "建议买入策略",
      "targetReturn": "预期收益区间"
    }
  ],
  "marketRisk": "当前市场整体风险提示（2-3句话）",
  "allocationAdvice": "资金配置建议（2-3句话）"
}

## 注意：
- 只输出JSON，不要其他文字
- fundPicks 必须恰好 5 只基金（如ETF、指数基金、主动管理型基金等）
- stockPicks 必须恰好 5 只股票（A股龙头股）
- 基金和股票要覆盖不同板块，避免过度集中
- confidence 范围 60-95 的整数"""


def _call_ai(system_prompt, user_prompt, temperature=0.7):
    """直接调用 AI API（DeepSeek）"""
    api_key = os.environ.get('DEEPSEEK_API_KEY',
                             os.environ.get('AI_API_KEY', 'sk-1986e1cd1169405f96649311dcfc76aa'))
    api_base = os.environ.get('AI_API_BASE', 'https://api.deepseek.com/v1')
    model = os.environ.get('AI_MODEL', 'deepseek-chat')

    # 确保 URL 以 /chat/completions 结尾
    if not api_base.endswith('/chat/completions'):
        api_base = api_base.rstrip('/') + '/chat/completions'

    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': temperature,
    }
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    encoded = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(api_base, data=encoded, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=300) as resp:
        result = json.loads(resp.read().decode('utf-8'))

    # 提取 content
    choices = result.get('choices', [])
    if not choices:
        raise ValueError('AI 返回空 choices')
    content = choices[0].get('message', {}).get('content', '')
    return content


def _parse_ai_json(raw):
    """从 AI 返回文本中提取 JSON"""
    # 去掉 <think>...</think> 标签
    text = re.sub(r'<think>[\s\S]*?</think>', '', raw).strip()
    # 尝试找 ```json ... ``` 块
    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if m:
        text = m.group(1)
    else:
        # 找第一个 { 到最后一个 }
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            text = text[start:end+1]
    return json.loads(text)


# ==================== 主流程 ====================

def build_prompt(indices, commodities, hot_events, heatmap, sector_flows,
                 top_sector_funds, top_sector_stocks):
    """构建 AI 输入 prompt"""
    today = date.today().isoformat()
    ctx = f'## 日期：{today}\n\n'

    # 大盘指数
    if indices:
        ctx += '## 今日大盘指数\n'
        for idx in indices:
            pct = idx.get('pct')
            pct_s = f'{pct:+.2f}%' if pct is not None else '--'
            ctx += f"- {idx['name']}: {idx.get('price', '--')} ({pct_s})\n"
        ctx += '\n'

    # 大宗商品
    if commodities:
        ctx += '## 大宗商品\n'
        for c in commodities:
            ctx += f"- {c.get('icon','')} {c['name']}: {c.get('price','--')} ({c.get('pctStr','--')})\n"
        ctx += '\n'

    # 热点事件
    if hot_events:
        ctx += '## 近期热点事件\n'
        for ev in hot_events[:6]:
            impact = ev.get('impact', 0)
            ctx += f"- [影响{'+' if impact >= 0 else ''}{impact}] {ev.get('title', '')}"
            if ev.get('reason'):
                ctx += f" | {ev['reason']}"
            ctx += '\n'
        ctx += '\n'

    # 板块热力
    if heatmap:
        ctx += '## 板块热力图\n'
        for h in heatmap:
            ctx += f"- {h.get('tag','')}: 温度{h.get('temperature',0)}° 趋势{h.get('trend','—')}"
            rp = h.get('realPct')
            if rp is not None:
                ctx += f" 实际涨幅{'+' if rp >= 0 else ''}{rp}%"
            ctx += '\n'
        ctx += '\n'

    # 板块资金流向
    if sector_flows:
        ctx += '## 板块资金流向（按主力净流入排序）\n'
        top_pct = sorted(sector_flows, key=lambda s: s.get('pct', 0) or 0, reverse=True)[:15]
        ctx += '### 涨幅领先板块\n'
        for f in top_pct:
            net_b = (f.get('mainNet', 0) or 0) / 1e8
            ctx += f"- {f['name']}: 涨幅{'+' if (f.get('pct',0) or 0) >= 0 else ''}{f.get('pct',0)}%, 主力净流入{net_b:.2f}亿 (占比{f.get('mainPct',0)}%)\n"
        top_flow = sorted(sector_flows, key=lambda s: s.get('mainNet', 0) or 0, reverse=True)[:10]
        ctx += '### 资金净流入领先板块\n'
        for f in top_flow:
            net_b = (f.get('mainNet', 0) or 0) / 1e8
            ctx += f"- {f['name']}: 主力净流入{net_b:.2f}亿, 涨幅{'+' if (f.get('pct',0) or 0) >= 0 else ''}{f.get('pct',0)}%\n"
        ctx += '\n'

    # 板块TOP基金
    if top_sector_funds:
        ctx += '## 增长板块内的TOP基金（按近3月业绩排序）\n'
        for sf in top_sector_funds:
            ctx += f"### {sf['sector']}板块\n"
            if sf.get('funds'):
                for fund in sf['funds']:
                    ctx += f"- {fund['name']}（{fund['code']}）\n"
            else:
                ctx += '- 暂无匹配基金\n'
        ctx += '\n'

    # 板块领涨个股
    if top_sector_stocks:
        ctx += '## 增长板块内的领涨个股\n'
        for ss in top_sector_stocks:
            ctx += f"### {ss['sector']}板块\n"
            if ss.get('stocks'):
                for s in ss['stocks']:
                    cap_str = f"{s.get('marketCap',0)/1e8:.0f}亿" if s.get('marketCap') else '--'
                    pct = s.get('pct', 0) or 0
                    ctx += f"- {s['name']}（{s['code']}）价格:{s.get('price','--')} 涨幅:{'+' if pct >= 0 else ''}{pct}% 市值:{cap_str} PE:{s.get('pe','--')}\n"
            else:
                ctx += '- 暂无数据\n'
        ctx += '\n'

    ctx += """
请站在百亿基金经理的角度，从以上增长良好的板块中，精选最值得投资的基金和股票。
要求：
1. 必须推荐恰好5只基金（fundPicks）+ 5只股票（stockPicks），共10只标的
2. 基金和股票要覆盖不同的增长板块，不要过度集中在同一板块
3. 每只标的的 sectorPct 字段要标注所属板块的今日涨幅
4. 每只标的都要有详细的过去3个月支撑面分析（supportAnalysis）
5. 用因果链分析为什么看好这个板块
6. 给出具体的买入策略和预期收益
用中文回复，只输出JSON。"""

    return ctx


def run_fund_pick():
    """执行完整的选基金/股票流程"""
    print(f'[fund_pick] {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} 开始选基金/股票...')

    # 1. 获取行情数据
    print('[fund_pick] 📡 获取指数行情...')
    indices = fetch_indices()

    print('[fund_pick] 📡 获取大宗商品...')
    commodities = fetch_commodities()

    print('[fund_pick] 📡 获取板块资金流向...')
    sector_flows = fetch_sector_flows()

    print('[fund_pick] 📡 读取热点事件...')
    hot_events, heatmap = load_hot_events()

    # 2. 选取涨幅+资金流入最强的 5 个板块
    import math
    scored = []
    for s in sector_flows:
        pct = s.get('pct', 0) or 0
        net = s.get('mainNet', 0) or 0
        score = pct * 2 + (math.log10(max(net, 1)) if net > 0 else -1)
        scored.append({**s, 'score': score})
    scored.sort(key=lambda x: x['score'], reverse=True)
    top_sectors = scored[:5]

    # 3. 获取各板块TOP基金/个股
    print(f'[fund_pick] 📡 获取TOP5板块基金和个股: {[s["name"] for s in top_sectors]}')
    top_sector_funds = []
    top_sector_stocks = []
    for s in top_sectors:
        funds = fetch_sector_top_funds(s['name'], 4)
        top_sector_funds.append({'sector': s['name'], 'funds': funds})
        stocks = fetch_sector_top_stocks(s['code'], 4)
        top_sector_stocks.append({'sector': s['name'], 'stocks': stocks})
        time.sleep(0.3)  # 避免请求过快

    # 4. 构建 prompt 并调用 AI
    print('[fund_pick] 🤖 调用 AI 分析...')
    user_prompt = build_prompt(indices, commodities, hot_events, heatmap,
                               sector_flows, top_sector_funds, top_sector_stocks)
    raw = _call_ai(FUND_PICK_SYSTEM_PROMPT, user_prompt)
    result = _parse_ai_json(raw)

    # 兼容旧格式
    if result.get('picks') and not result.get('fundPicks'):
        result['fundPicks'] = [p for p in result['picks'] if p.get('type') == 'fund']
        result['stockPicks'] = [p for p in result['picks'] if p.get('type') != 'fund']
        del result['picks']

    # 5. 保存结果
    now = datetime.now()
    pick_data = {
        'date': now.strftime('%Y-%m-%d'),
        'timestamp': now.isoformat(),
        'triggerTime': '14:50',
        'result': result,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PICK_FILE, 'w', encoding='utf-8') as f:
        json.dump(pick_data, f, ensure_ascii=False, indent=2)

    fund_count = len(result.get('fundPicks', []))
    stock_count = len(result.get('stockPicks', []))
    print(f'[fund_pick] ✅ 选基金/股票完成: {fund_count}基金 + {stock_count}股票')
    print(f'[fund_pick] 💾 结果已保存: {PICK_FILE}')
    return pick_data


def load_fund_pick_cache():
    """读取缓存的选基结果"""
    if not os.path.exists(PICK_FILE):
        return None
    try:
        with open(PICK_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except Exception:
        return None


if __name__ == '__main__':
    run_fund_pick()
