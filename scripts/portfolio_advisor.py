#!/usr/bin/env python3
"""
实盘行动指南 — 服务器端14:50自动执行
1. 使用内置持仓列表（与小程序 FUND_DB 同步）
2. 从东方财富拉取每只基金的估值和近期净值
3. 拉取指数、商品、热点事件
4. 调用 DeepSeek AI 生成操作建议
5. 结果存入 data/portfolio_advice.json
"""

import os, sys, json, re, time, traceback
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import urllib.error
import urllib.parse

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
ADVICE_FILE = os.path.join(DATA_DIR, 'portfolio_advice.json')

# ==================== 内置持仓 (与小程序 FUND_DB 同步) ====================

FUND_DB = {
    '000216': {'name': '华安黄金ETF联接A', 'type': '黄金'},
    '022430': {'name': '华夏中证A500ETF联接A', 'type': '宽基'},
    '019868': {'name': '华夏云计算与大数据ETF联接A', 'type': 'AI/科技'},
    '004814': {'name': '中欧红利优享混合A', 'type': '红利'},
    '003017': {'name': '广发中证军工ETF联接A', 'type': '军工'},
    '003834': {'name': '华夏能源革新股票A', 'type': '新能源'},
    '161725': {'name': '招商中证白酒指数A', 'type': '白酒/消费'},
    '110011': {'name': '易方达优质精选混合', 'type': '蓝筹/QDII'},
    '005827': {'name': '易方达蓝筹精选混合', 'type': '蓝筹'},
    '008887': {'name': '华夏国证半导体芯片ETF联接A', 'type': '半导体'},
    '320007': {'name': '诺安成长混合A', 'type': '半导体/科技'},
    '160119': {'name': '南方中证500ETF联接A', 'type': '宽基'},
    '005693': {'name': '广发中证军工ETF联接C', 'type': '军工'},
}

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    'Referer': 'https://fund.eastmoney.com/',
}

def _get_json(url, timeout=10):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))

def _get_text(url, timeout=10):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8')

# ==================== 数据采集 ====================

def fetch_fund_estimate(code):
    """获取单只基金实时估值"""
    url = f'https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time()*1000)}'
    try:
        text = _get_text(url, timeout=5)
        m = re.search(r'\{.*\}', text)
        if m:
            obj = json.loads(m.group(0))
            return {
                'code': obj.get('fundcode', code),
                'name': obj.get('name', ''),
                'nav': float(obj.get('dwjz', 0)),
                'estimate': float(obj.get('gsz', 0)),
                'pct': float(obj.get('gszzl', 0)),
                'time': obj.get('gztime', ''),
            }
    except Exception as e:
        print(f'  [estimate] {code} 失败: {e}')
    return None


def fetch_fund_history(code, page_size=30):
    """获取基金历史净值（天天基金移动端API）"""
    url = (f'https://fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList'
           f'?pageIndex=1&pageSize={page_size}&plat=Android&appType=ttjj'
           f'&product=EFund&Version=1&deviceid=1&FCODE={code}')
    try:
        data = _get_json(url, timeout=10)
        datas = data.get('Datas', [])
        if datas:
            nav_list = [{'date': d['FSRQ'], 'nav': float(d['DWJZ'])}
                        for d in datas if d.get('DWJZ')]
            nav_list.reverse()  # 升序
            return nav_list
    except Exception as e:
        print(f'  [history] {code} 失败: {e}')
    return []


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
        return [{'name': d.get('f14', ''), 'price': d.get('f2'),
                 'pct': d.get('f3'), 'pctStr': f"{d.get('f3', 0):+.2f}%"} for d in diff]
    except Exception as e:
        print(f'  [indices] 失败: {e}')
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
            result.append({
                'name': c.get('name', d.get('f14', '')),
                'icon': c.get('icon', ''),
                'price': d.get('f2'),
                'pct': pct,
                'pctStr': f'{pct:+.2f}%' if pct is not None else '--',
            })
        return result
    except Exception as e:
        print(f'  [commodities] 失败: {e}')
        return []


def load_hot_events():
    """从 data/hot_events.json 读取热点事件 & 板块热力"""
    path = os.path.join(DATA_DIR, 'hot_events.json')
    if not os.path.exists(path):
        return [], []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('events', []), data.get('heatmap', [])
    except Exception:
        return [], []


# ==================== AI 调用 ====================

DAILY_ADVICE_SYSTEM_PROMPT = """你是一位专业的基金投资顾问。根据用户持仓基金过去一周（5-7个交易日）的净值走势，结合当前市场环境，给出今天的具体操作建议。

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
- 用中文回复"""


def _call_ai(system_prompt, user_prompt, temperature=0.7):
    """调用 DeepSeek AI API"""
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
        'max_tokens': 4096,
    }
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    encoded = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(api_base, data=encoded, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=300) as resp:
        result = json.loads(resp.read().decode('utf-8'))

    choices = result.get('choices', [])
    if not choices:
        raise ValueError('AI 返回空 choices')
    return choices[0].get('message', {}).get('content', '')


def _parse_ai_json(raw):
    """从 AI 返回文本中提取 JSON"""
    text = re.sub(r'<think>[\s\S]*?</think>', '', raw).strip()
    m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if m:
        text = m.group(1)
    else:
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            text = text[start:end+1]
    # 清理尾逗号
    text = re.sub(r',\s*([\]\}])', r'\1', text)
    return json.loads(text)


# ==================== 主流程 ====================

def build_prompt(holdings, estimates, history_map, indices, commodities, hot_events, heatmap):
    """构建 AI 输入 prompt（与小程序端 runDailyAdvice 逻辑一致）"""
    today = date.today().isoformat()
    ctx = f'## 日期：{today}\n\n'

    # 大盘指数
    if indices:
        ctx += '## 今日大盘指数\n'
        for idx in indices:
            ctx += f"- {idx['name']}: {idx.get('price', '--')} ({idx.get('pctStr', '--')})\n"
        ctx += '\n'

    # 大宗商品
    if commodities:
        ctx += '## 大宗商品行情\n'
        for c in commodities:
            anomaly = ' ⚠️异动' if abs(c.get('pct', 0) or 0) >= 2 else ''
            ctx += f"- {c.get('icon', '')} {c['name']}: {c.get('price', '--')} ({c.get('pctStr', '--')}){anomaly}\n"
        ctx += '\n'

    # 板块热力
    if heatmap:
        ctx += '## 板块热力图\n'
        for h in heatmap:
            ctx += f"- {h.get('tag', '')}: 温度{h.get('temperature', 0)}° 趋势{h.get('trend', '—')}\n"
        ctx += '\n'

    # 热点事件
    if hot_events:
        ctx += '## 近期热点事件\n'
        for ev in hot_events[:8]:
            impact = ev.get('impact', 0)
            ctx += f"- [影响{'+' if impact >= 0 else ''}{impact}] {ev.get('title', '')}"
            if ev.get('advice'):
                ctx += f" → {ev['advice']}"
            ctx += '\n'
        ctx += '\n'

    # 每只基金的一周净值数据
    ctx += f'## 持仓基金一周净值变化（共{len(holdings)}只）\n'
    for h in holdings:
        code = h['code']
        nav_list = history_map.get(code, [])
        est = estimates.get(code)

        ctx += f"\n### {h['name']}（{code}，{h['type']}）\n"

        if nav_list and len(nav_list) >= 2:
            recent = nav_list[-7:] if len(nav_list) >= 7 else nav_list
            week_start = recent[0]
            week_end = recent[-1]
            week_chg = (week_end['nav'] - week_start['nav']) / week_start['nav'] * 100
            ctx += f"- 一周净值: {week_start['date']}={week_start['nav']} → {week_end['date']}={week_end['nav']}, 周涨幅={week_chg:.2f}%\n"
            nav_str = ' → '.join(f"{r['date'][5:]}:{r['nav']}" for r in recent)
            ctx += f"- 逐日净值: {nav_str}\n"

        if est:
            pct_str = f"{est['pct']:+.2f}%" if est.get('pct') is not None else '--'
            ctx += f"- 今日估值: {est.get('estimate', '--')}, 估算涨幅: {pct_str}\n"

    names = '、'.join(f"{h['name']}({h['code']})" for h in holdings)
    ctx += f"""

⚠️ 用户共持有 {len(holdings)} 只基金：{names}。
funds 数组必须包含全部 {len(holdings)} 只基金。

请基于以上过去一周的净值走势和今日市场环境：
1. 分析每只基金过去一周的走势形态（连涨/连跌/震荡/突破等）
2. 结合当前市场局势，给出**今天**应该如何操作（买入/卖出/加仓/减仓/持有）
3. 每只基金的建议须引用具体的净值数据或技术指标作为依据
4. 给出操作时机建议（开盘/尾盘/等回调等）

用中文回复。"""

    return ctx


def run_portfolio_advice():
    """执行完整的实盘行动指南分析"""
    print(f'[portfolio] {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} 开始实盘行动指南...')

    holdings = [{'code': code, 'name': info['name'], 'type': info['type']}
                for code, info in FUND_DB.items()]
    codes = [h['code'] for h in holdings]

    # 并行获取数据
    print(f'[portfolio] 📡 获取 {len(codes)} 只基金数据...')
    estimates = {}
    history_map = {}

    with ThreadPoolExecutor(max_workers=6) as executor:
        # 估值
        est_futures = {executor.submit(fetch_fund_estimate, c): c for c in codes}
        # 历史净值
        hist_futures = {executor.submit(fetch_fund_history, c, 30): c for c in codes}

        for f in as_completed(est_futures):
            code = est_futures[f]
            try:
                r = f.result()
                if r:
                    estimates[code] = r
            except Exception:
                pass

        for f in as_completed(hist_futures):
            code = hist_futures[f]
            try:
                r = f.result()
                if r:
                    history_map[code] = r
            except Exception:
                pass

    print(f'[portfolio]   估值: {len(estimates)}/{len(codes)}, 历史: {len(history_map)}/{len(codes)}')

    print('[portfolio] 📡 获取行情和热点...')
    indices = fetch_indices()
    commodities = fetch_commodities()
    hot_events, heatmap = load_hot_events()

    # 构建 prompt 并调用 AI
    print('[portfolio] 🤖 调用 AI 分析...')
    user_prompt = build_prompt(holdings, estimates, history_map, indices, commodities, hot_events, heatmap)
    raw = _call_ai(DAILY_ADVICE_SYSTEM_PROMPT, user_prompt)
    result = _parse_ai_json(raw)

    # 确保 funds 数组覆盖全部持仓
    if result.get('funds'):
        covered = {f['code'] for f in result['funds']}
        for h in holdings:
            if h['code'] not in covered:
                nav_list = history_map.get(h['code'], [])
                recent = nav_list[-7:] if nav_list else []
                week_chg = '--'
                if len(recent) >= 2:
                    week_chg = f"{(recent[-1]['nav'] - recent[0]['nav']) / recent[0]['nav'] * 100:.2f}%"
                result['funds'].append({
                    'code': h['code'],
                    'name': h['name'],
                    'weekChange': week_chg,
                    'weekTrend': '数据不足',
                    'action': 'hold',
                    'reason': 'AI未覆盖此基金，建议持有观望',
                    'timing': '--',
                    'confidence': 40,
                })

    # 保存结果
    now = datetime.now()
    advice_data = {
        'date': now.strftime('%Y-%m-%d'),
        'timestamp': now.isoformat(),
        'triggerTime': '14:50',
        'result': result,
        'holdingsCount': len(holdings),
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ADVICE_FILE, 'w', encoding='utf-8') as f:
        json.dump(advice_data, f, ensure_ascii=False, indent=2)

    fund_count = len(result.get('funds', []))
    print(f'[portfolio] ✅ 实盘行动指南完成: {fund_count} 只基金建议')
    print(f'[portfolio] 💾 结果已保存: {ADVICE_FILE}')
    return advice_data


def load_portfolio_advice_cache():
    """读取缓存的实盘建议"""
    if not os.path.exists(ADVICE_FILE):
        return None
    try:
        with open(ADVICE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


if __name__ == '__main__':
    run_portfolio_advice()
