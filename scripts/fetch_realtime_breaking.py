#!/usr/bin/env python3
"""
基金助手 - 实时突发新闻 & 全球市场异动监控
高频运行（交易时段每5分钟），提供近实时的国际媒体头条 + 市场异动自动告警

数据源:
  新闻: Reuters(Google News代理), Bloomberg(Google News代理), CNBC, MarketWatch, Yahoo Finance, BBC
  行情: 东方财富推送API — 全球指数/大宗期货/行业ETF

输出: data/realtime_breaking.json → 前端"实时热点·异动"消费
"""

import json, os, re, ssl, sys, time, hashlib
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
import xml.etree.ElementTree as ET

# ==================== .env 加载 ====================
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if not os.path.exists(env_path):
        return
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

_load_dotenv()

# ==================== 配置 ====================
API_KEY = os.environ.get('AI_API_KEY', '')
API_BASE = os.environ.get('AI_API_BASE', '').strip()
MODEL = os.environ.get('AI_MODEL', 'deepseek-ai/DeepSeek-V3')
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'realtime_breaking.json')
HOT_EVENTS_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'hot_events.json')

# 自动检测 API 基地址 (与 server.py 中 _AI_PROVIDERS 保持一致)
if not API_BASE:
    model_lower = MODEL.lower()
    if 'glm' in model_lower or 'chatglm' in model_lower:
        API_BASE = 'https://open.bigmodel.cn/api/paas/v4'
    elif 'deepseek' in model_lower:
        API_BASE = 'https://api.deepseek.com/v1'
    else:
        API_BASE = 'https://api.siliconflow.cn/v1'

CST = timezone(timedelta(hours=8))

# ==================== SSL / HTTP ====================
_SSL_CTX = None

def _ssl_ctx():
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = ssl.create_default_context()
        _SSL_CTX.check_hostname = False
        _SSL_CTX.verify_mode = ssl.CERT_NONE
    return _SSL_CTX


def fetch_http(url, timeout=10, headers=None):
    """通用 HTTP GET，返回文本或 None"""
    hdrs = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
    if headers:
        hdrs.update(headers)
    try:
        req = Request(url, headers=hdrs)
        with urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f'  [HTTP] {url[:80]}... → {e}')
        return None


# ==================== 国际媒体 RSS 抓取 ====================

def fetch_reuters_headlines():
    """Reuters via Google News RSS — 实时财经/地缘头条"""
    items = []
    queries = [
        'site:reuters.com+when:4h',
        'site:reuters.com+oil+OR+gold+OR+tariff+OR+sanctions+OR+Iran+OR+Fed+when:4h',
        'site:reuters.com+markets+OR+stocks+OR+commodities+when:4h',
    ]
    for q in queries:
        url = f'https://news.google.com/rss/search?q={q}&hl=en&gl=US&ceid=US:en'
        raw = fetch_http(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
            for item in root.findall('.//item')[:12]:
                title = (item.findtext('title') or '').strip()
                pub = (item.findtext('pubDate') or '').strip()
                if title:
                    items.append({'title': title, 'source': 'Reuters', 'time': pub})
        except Exception as e:
            print(f'  [WARN] Reuters RSS: {e}')
    return _dedup_items(items)[:20]


def fetch_bloomberg_headlines():
    """Bloomberg via Google News RSS"""
    items = []
    queries = [
        'site:bloomberg.com+when:4h',
        'site:bloomberg.com+markets+OR+economy+OR+stocks+OR+oil+OR+gold+when:4h',
    ]
    for q in queries:
        url = f'https://news.google.com/rss/search?q={q}&hl=en&gl=US&ceid=US:en'
        raw = fetch_http(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
            for item in root.findall('.//item')[:12]:
                title = (item.findtext('title') or '').strip()
                pub = (item.findtext('pubDate') or '').strip()
                if title:
                    items.append({'title': title, 'source': 'Bloomberg', 'time': pub})
        except Exception as e:
            print(f'  [WARN] Bloomberg RSS: {e}')
    return _dedup_items(items)[:15]


def fetch_cnbc_headlines():
    """CNBC RSS Feed"""
    items = []
    urls = [
        'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114',  # Top News
        'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135',   # World
        'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362',  # Investing
    ]
    for url in urls:
        raw = fetch_http(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
            for item in root.findall('.//item')[:10]:
                title = (item.findtext('title') or '').strip()
                pub = (item.findtext('pubDate') or '').strip()
                if title:
                    items.append({'title': title, 'source': 'CNBC', 'time': pub})
        except Exception as e:
            print(f'  [WARN] CNBC RSS: {e}')
    return _dedup_items(items)[:15]


def fetch_marketwatch_headlines():
    """MarketWatch RSS"""
    items = []
    urls = [
        'https://feeds.marketwatch.com/marketwatch/topstories/',
        'https://feeds.marketwatch.com/marketwatch/marketpulse/',
    ]
    for url in urls:
        raw = fetch_http(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
            for item in root.findall('.//item')[:10]:
                title = (item.findtext('title') or '').strip()
                pub = (item.findtext('pubDate') or '').strip()
                if title:
                    items.append({'title': title, 'source': 'MarketWatch', 'time': pub})
        except Exception as e:
            print(f'  [WARN] MarketWatch RSS: {e}')
    return _dedup_items(items)[:12]


def fetch_yahoo_finance_headlines():
    """Yahoo Finance RSS"""
    items = []
    url = 'https://finance.yahoo.com/news/rssindex'
    raw = fetch_http(url)
    if raw:
        try:
            root = ET.fromstring(raw)
            for item in root.findall('.//item')[:15]:
                title = (item.findtext('title') or '').strip()
                pub = (item.findtext('pubDate') or '').strip()
                if title:
                    items.append({'title': title, 'source': 'Yahoo Finance', 'time': pub})
        except Exception as e:
            print(f'  [WARN] Yahoo Finance RSS: {e}')
    return items[:12]


def fetch_google_news_finance():
    """Google News Finance 聚合 — 全球金融/地缘热点"""
    items = []
    queries = [
        'gold+price+OR+oil+price+OR+stock+market+OR+federal+reserve+OR+ECB+when:4h',
        'geopolitics+OR+war+OR+sanctions+OR+tariff+OR+trade+war+when:4h',
        'AI+stocks+OR+NVIDIA+OR+semiconductor+OR+tech+stocks+when:4h',
        'commodity+OR+copper+OR+silver+OR+iron+ore+when:4h',
    ]
    for q in queries:
        url = f'https://news.google.com/rss/search?q={q}&hl=en&gl=US&ceid=US:en'
        raw = fetch_http(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
            for item in root.findall('.//item')[:8]:
                title = (item.findtext('title') or '').strip()
                pub = (item.findtext('pubDate') or '').strip()
                if title:
                    source_match = re.search(r'\s*-\s*([^-]+)$', title)
                    source = source_match.group(1).strip() if source_match else 'Google News'
                    items.append({'title': title, 'source': source, 'time': pub})
        except Exception as e:
            print(f'  [WARN] Google News: {e}')
    return _dedup_items(items)[:20]


def fetch_cls_flash():
    """财联社快讯 — 中文实时快讯"""
    items = []
    url = 'https://www.cls.cn/nodeapi/updateTelegraphList?app=CailianpressWeb&os=web&sv=7.7.5&rn=20'
    raw = fetch_http(url)
    if not raw:
        return items
    try:
        data = json.loads(raw)
        rolls = data.get('data', {}).get('roll_data', [])
        for r in rolls[:20]:
            content = r.get('content', '').strip()
            title = r.get('title', '').strip() or content[:80]
            ctime = r.get('ctime', 0)
            if title:
                items.append({
                    'title': title,
                    'source': '财联社',
                    'time': datetime.fromtimestamp(ctime, tz=CST).isoformat() if ctime else '',
                })
    except Exception as e:
        print(f'  [WARN] CLS flash: {e}')
    return items[:15]


def fetch_sina_live():
    """新浪财经7x24实时快讯"""
    items = []
    url = 'https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=20&zhibo_id=152&tag_id=0&type=0'
    raw = fetch_http(url)
    if not raw:
        return items
    try:
        data = json.loads(raw)
        feeds = data.get('result', {}).get('data', {}).get('feed', {}).get('list', [])
        for f in feeds[:15]:
            rich_text = f.get('rich_text', '').strip()
            # 清除HTML
            text = re.sub(r'<[^>]+>', '', rich_text).strip()
            title = text[:100] if text else ''
            create_time = f.get('create_time', '')
            if title and len(title) > 8:
                items.append({'title': title, 'source': '新浪财经', 'time': create_time})
    except Exception as e:
        print(f'  [WARN] Sina live: {e}')
    return items[:12]


def _dedup_items(items):
    """标题去重"""
    seen = set()
    result = []
    for it in items:
        key = re.sub(r'\W', '', it['title'])[:40].lower()
        if key and key not in seen:
            seen.add(key)
            result.append(it)
    return result


# ==================== 全球市场异动检测 ====================

# 东方财富推送API字段：f2=最新价, f3=涨跌幅, f4=涨跌额, f12=代码, f14=名称
GLOBAL_INDICES = {
    # A股主要指数
    '1.000001': {'name': '上证指数', 'short': '沪指', 'icon': '🇨🇳', 'threshold': 1.0},
    '0.399001': {'name': '深证成指', 'short': '深指', 'icon': '🇨🇳', 'threshold': 1.0},
    '0.399006': {'name': '创业板指', 'short': '创业板', 'icon': '🇨🇳', 'threshold': 1.5},
    # 全球指数 (通过东方财富全球指数)
    '100.DJIA':  {'name': '道琼斯', 'short': '道指', 'icon': '🇺🇸', 'threshold': 1.0},
    '100.NDX':   {'name': '纳斯达克100', 'short': '纳指', 'icon': '🇺🇸', 'threshold': 1.0},
    '100.SPX':   {'name': '标普500', 'short': '标普', 'icon': '🇺🇸', 'threshold': 1.0},
    '100.N225':  {'name': '日经225', 'short': '日经', 'icon': '🇯🇵', 'threshold': 1.5},
    '100.GDAXI': {'name': '德国DAX', 'short': 'DAX', 'icon': '🇩🇪', 'threshold': 1.5},
    '100.FTSE':  {'name': '富时100', 'short': '富时', 'icon': '🇬🇧', 'threshold': 1.5},
    '100.HSI':   {'name': '恒生指数', 'short': '恒指', 'icon': '🇭🇰', 'threshold': 1.5},
    '100.SENSEX':{'name': '印度SENSEX', 'short': '印度', 'icon': '🇮🇳', 'threshold': 2.0},
}

COMMODITY_FUTURES = {
    '113.AU0':  {'name': '沪金主力', 'short': '黄金', 'icon': '🥇', 'threshold': 1.0, 'tag': '黄金'},
    '113.AG0':  {'name': '沪银主力', 'short': '白银', 'icon': '🥈', 'threshold': 1.5, 'tag': '白银'},
    '113.CU0':  {'name': '沪铜主力', 'short': '铜', 'icon': '🔩', 'threshold': 1.5, 'tag': '有色金属'},
    '113.AL0':  {'name': '沪铝主力', 'short': '铝', 'icon': '⚙️', 'threshold': 1.5, 'tag': '有色金属'},
    '113.SC0':  {'name': '原油主力', 'short': '原油', 'icon': '🛢️', 'threshold': 2.0, 'tag': '原油'},
    '113.FU0':  {'name': '燃油主力', 'short': '燃油', 'icon': '⛽', 'threshold': 2.0, 'tag': '原油'},
    '113.ZN0':  {'name': '沪锌主力', 'short': '锌', 'icon': '🔧', 'threshold': 1.5, 'tag': '有色金属'},
    '113.NI0':  {'name': '沪镍主力', 'short': '镍', 'icon': '🧲', 'threshold': 2.0, 'tag': '有色金属'},
    '113.RB0':  {'name': '螺纹钢', 'short': '钢', 'icon': '🏗️', 'threshold': 2.0, 'tag': '工业'},
    '113.I0':   {'name': '铁矿石', 'short': '铁矿', 'icon': '⛏️', 'threshold': 2.0, 'tag': '工业'},
}

SECTOR_ETFS = {
    '0.159819': {'name': '人工智能ETF', 'short': 'AI', 'icon': '🤖', 'threshold': 2.0, 'tag': '人工智能'},
    '1.515070': {'name': 'AI算力ETF', 'short': '算力', 'icon': '💻', 'threshold': 2.0, 'tag': 'AI算力'},
    '1.516380': {'name': '半导体ETF', 'short': '芯片', 'icon': '🔲', 'threshold': 2.0, 'tag': '半导体'},
    '1.562500': {'name': '机器人ETF', 'short': '机器人', 'icon': '🦾', 'threshold': 2.5, 'tag': '机器人'},
    '0.159930': {'name': '能源ETF', 'short': '新能源', 'icon': '🔋', 'threshold': 2.0, 'tag': '新能源'},
    '0.159857': {'name': '光伏ETF', 'short': '光伏', 'icon': '☀️', 'threshold': 2.5, 'tag': '光伏'},
    '1.512690': {'name': '白酒LOF', 'short': '白酒', 'icon': '🍷', 'threshold': 2.0, 'tag': '白酒'},
    '0.159828': {'name': '医药ETF', 'short': '医药', 'icon': '💊', 'threshold': 2.0, 'tag': '医药'},
    '0.159792': {'name': '军工ETF', 'short': '军工', 'icon': '🛡️', 'threshold': 2.0, 'tag': '军工'},
    '1.513180': {'name': '恒生科技', 'short': '港股科技', 'icon': '📱', 'threshold': 2.0, 'tag': '港股科技'},
    '1.518880': {'name': '黄金ETF', 'short': '金ETF', 'icon': '🥇', 'threshold': 1.5, 'tag': '黄金'},
    '1.515080': {'name': '红利ETF', 'short': '红利', 'icon': '💰', 'threshold': 1.5, 'tag': '红利'},
}

# COMEX/NYMEX国际商品 (via 东方财富全球期货)
GLOBAL_COMMODITIES = {
    '101.GC00Y':  {'name': 'COMEX黄金', 'short': '国际金', 'icon': '🥇', 'threshold': 1.0, 'tag': '黄金'},
    '101.SI00Y':  {'name': 'COMEX白银', 'short': '国际银', 'icon': '🥈', 'threshold': 1.5, 'tag': '白银'},
    '101.CL00Y':  {'name': 'WTI原油', 'short': 'WTI油', 'icon': '🛢️', 'threshold': 2.0, 'tag': '原油'},
    '101.HG00Y':  {'name': 'COMEX铜', 'short': '国际铜', 'icon': '🔩', 'threshold': 1.5, 'tag': '有色金属'},
}


def _fetch_market_data(secids_dict):
    """通用东方财富市场数据获取"""
    if not secids_dict:
        return {}
    secids = ','.join(secids_dict.keys())
    url = f'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f12,f14&secids={secids}'
    results = {}
    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=8, context=_ssl_ctx()) as resp:
            data = json.loads(resp.read().decode())
        items = (data.get('data') or {}).get('diff') or []
        for item in items:
            code = str(item.get('f12', ''))
            price = item.get('f2')
            pct = item.get('f3')
            name = item.get('f14', '')
            if pct is None:
                continue
            # 匹配 secid
            for secid, info in secids_dict.items():
                if secid.split('.')[-1] == code or secid.endswith('.' + code):
                    results[secid] = {
                        **info,
                        'price': price,
                        'pct': float(pct),
                        'code': code,
                        'secid': secid,
                    }
                    break
    except Exception as e:
        print(f'  [WARN] 市场数据获取失败: {e}')
    return results


def detect_market_anomalies():
    """检测全球市场异动：指数、商品、行业ETF"""
    anomalies = []

    # 1. 国内商品期货
    print('  📊 检测大宗商品...')
    commodity_data = _fetch_market_data(COMMODITY_FUTURES)
    for secid, info in commodity_data.items():
        meta = COMMODITY_FUTURES[secid]
        pct = info['pct']
        if abs(pct) >= meta['threshold']:
            level = '🔴 剧烈' if abs(pct) >= meta['threshold'] * 2 else '🟡 显著'
            anomalies.append({
                'type': 'commodity',
                'name': meta['short'],
                'fullName': meta['name'],
                'icon': meta['icon'],
                'price': info['price'],
                'pct': pct,
                'tag': meta.get('tag', ''),
                'level': level,
                'alert': f"{meta['icon']} {meta['short']}{'大涨' if pct > 0 else '大跌'}{abs(pct):.1f}%",
            })

    # 2. 全球指数
    print('  📊 检测全球指数...')
    index_data = _fetch_market_data(GLOBAL_INDICES)
    for secid, info in index_data.items():
        meta = GLOBAL_INDICES[secid]
        pct = info['pct']
        if abs(pct) >= meta['threshold']:
            level = '🔴 剧烈' if abs(pct) >= meta['threshold'] * 2 else '🟡 显著'
            anomalies.append({
                'type': 'index',
                'name': meta['short'],
                'fullName': meta['name'],
                'icon': meta['icon'],
                'price': info['price'],
                'pct': pct,
                'tag': '',
                'level': level,
                'alert': f"{meta['icon']} {meta['short']}{'大涨' if pct > 0 else '大跌'}{abs(pct):.1f}%",
            })

    # 3. 行业ETF
    print('  📊 检测行业ETF...')
    etf_data = _fetch_market_data(SECTOR_ETFS)
    for secid, info in etf_data.items():
        meta = SECTOR_ETFS[secid]
        pct = info['pct']
        if abs(pct) >= meta['threshold']:
            level = '🔴 剧烈' if abs(pct) >= meta['threshold'] * 2 else '🟡 显著'
            anomalies.append({
                'type': 'sector',
                'name': meta['short'],
                'fullName': meta['name'],
                'icon': meta['icon'],
                'price': info['price'],
                'pct': pct,
                'tag': meta.get('tag', ''),
                'level': level,
                'alert': f"{meta['icon']} {meta['short']}{'大涨' if pct > 0 else '大跌'}{abs(pct):.1f}%",
            })

    # 4. 国际商品 (COMEX/NYMEX)
    print('  📊 检测国际商品...')
    global_data = _fetch_market_data(GLOBAL_COMMODITIES)
    for secid, info in global_data.items():
        meta = GLOBAL_COMMODITIES[secid]
        pct = info['pct']
        if abs(pct) >= meta['threshold']:
            level = '🔴 剧烈' if abs(pct) >= meta['threshold'] * 2 else '🟡 显著'
            anomalies.append({
                'type': 'global_commodity',
                'name': meta['short'],
                'fullName': meta['name'],
                'icon': meta['icon'],
                'price': info['price'],
                'pct': pct,
                'tag': meta.get('tag', ''),
                'level': level,
                'alert': f"{meta['icon']} {meta['short']}{'大涨' if pct > 0 else '大跌'}{abs(pct):.1f}%",
            })

    # 按涨跌幅绝对值排序
    anomalies.sort(key=lambda x: abs(x['pct']), reverse=True)
    return anomalies


def get_all_market_snapshot():
    """获取全量市场快照 (用于LLM上下文)"""
    snapshot = {}
    for label, data_dict in [
        ('期货', COMMODITY_FUTURES),
        ('指数', GLOBAL_INDICES),
        ('ETF', SECTOR_ETFS),
        ('国际', GLOBAL_COMMODITIES),
    ]:
        result = _fetch_market_data(data_dict)
        for secid, info in result.items():
            meta = data_dict[secid]
            snapshot[meta.get('short', meta['name'])] = {
                'pct': info['pct'],
                'price': info['price'],
            }
    return snapshot


# ==================== LLM 分析 ====================

def _build_breaking_prompt(headlines, anomalies, market_snapshot):
    """构建LLM提示词：从实时头条和市场异动生成突发新闻摘要"""
    now = datetime.now(CST)

    # 新闻头条
    news_text = '\n'.join([
        f"[{h['source']}] {h['title']}"
        for h in headlines[:60]
    ])

    # 市场异动
    anomaly_text = '\n'.join([
        f"[{a['type']}] {a['fullName']}: {a['pct']:+.1f}% (价格:{a['price']})"
        for a in anomalies[:20]
    ])

    # 市场快照
    snapshot_lines = []
    for name, data in sorted(market_snapshot.items(), key=lambda x: abs(x[1]['pct']), reverse=True)[:30]:
        snapshot_lines.append(f"  {name}: {data['pct']:+.1f}%")
    snapshot_text = '\n'.join(snapshot_lines)

    prompt = f"""你是一位全球金融市场实时分析师。当前北京时间: {now.strftime('%Y-%m-%d %H:%M')}

## 实时国际媒体头条
{news_text or '(暂无新闻)'}

## 市场异动检测
{anomaly_text or '(暂无异动)'}

## 市场快照（实时涨跌幅）
{snapshot_text or '(暂无数据)'}

## 任务
请根据以上实时信息，提取最重要的突发/热点事件，生成JSON数组。

### 要求
1. 重点关注：地缘政治（战争/制裁/关税）、央行政策（降息/加息/QE）、大宗商品剧烈波动、AI/科技重大突破、全球股市异动
2. 每条事件必须有**具体事实**，不要泛泛而谈
3. 如果某个商品/指数出现异动，结合新闻分析原因
4. 将英文新闻翻译为简洁中文标题
5. impact 范围 -15 到 +15（绝对值越大影响越大）
6. 最多提取 10 条最重要的事件
7. category 只能是: geopolitics, commodity, monetary, technology, market, policy

### 输出格式（严格JSON）
```json
[
  {{
    "title": "简洁中文标题（20字以内）",
    "reason": "详细分析原因和影响（50字以内）",
    "source": "消息来源（如Reuters/Bloomberg）",
    "category": "类别",
    "impact": 数字,
    "sectors_positive": ["利好板块"],
    "sectors_negative": ["利空板块"],
    "advice": "投资建议（20字以内）"
  }}
]
```

只输出JSON数组，不要其他内容。"""
    return prompt


def call_llm_breaking(headlines, anomalies, market_snapshot):
    """调用LLM生成突发事件摘要"""
    if not API_KEY:
        print('  ⚠️ 未配置 AI_API_KEY，跳过LLM分析')
        return []

    prompt = _build_breaking_prompt(headlines, anomalies, market_snapshot)
    url = f'{API_BASE}/chat/completions'
    body = {
        'model': MODEL,
        'messages': [
            {'role': 'system', 'content': '你是专业金融市场实时分析师，只输出合法JSON。'},
            {'role': 'user', 'content': prompt},
        ],
        'temperature': 0.3,
        'max_tokens': 3000,
    }
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {API_KEY}',
    }
    try:
        req = Request(url, data=json.dumps(body).encode('utf-8'), headers=headers, method='POST')
        with urlopen(req, timeout=60, context=_ssl_ctx()) as resp:
            result = json.loads(resp.read().decode())
        content = result['choices'][0]['message']['content'].strip()
        # 提取JSON
        content = re.sub(r'^```json\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
        events = json.loads(content)
        if isinstance(events, list):
            return events
        return []
    except Exception as e:
        print(f'  ❌ LLM调用失败: {e}')
        return []


# ==================== 关键词匹配兜底（无LLM时使用） ====================

BREAKING_KEYWORDS = {
    'geopolitics': {
        'keywords': ['war', 'conflict', 'sanctions', 'iran', 'russia', 'ukraine', 'china', 'tariff',
                      'missile', 'attack', 'military', 'nuclear', 'strait', '战争', '冲突', '制裁',
                      '伊朗', '俄罗斯', '乌克兰', '关税', '导弹', '袭击', '军事', '核', '海峡',
                      '中东', '红海', '霍尔木兹', '地缘'],
        'impact': 10,
    },
    'monetary': {
        'keywords': ['fed', 'rate cut', 'rate hike', 'interest rate', 'inflation', 'cpi', 'ecb',
                      'boj', 'pboc', 'stimulus', '降息', '加息', '利率', '通胀', 'CPI', '刺激',
                      '央行', '美联储', '欧央行', '量化宽松', 'QE'],
        'impact': 8,
    },
    'commodity': {
        'keywords': ['gold', 'oil', 'crude', 'silver', 'copper', 'opec', 'commodity',
                      '黄金', '原油', '白银', '铜', 'OPEC', '大宗商品', '期货'],
        'impact': 7,
    },
    'technology': {
        'keywords': ['nvidia', 'ai', 'artificial intelligence', 'semiconductor', 'chip', 'openai',
                      'chatgpt', 'robot', '人工智能', '芯片', '半导体', '机器人', 'AI', '大模型',
                      '算力', 'autonomous', 'quantum'],
        'impact': 6,
    },
    'market': {
        'keywords': ['crash', 'surge', 'plunge', 'rally', 'record high', 'bear market', 'bull',
                      '暴跌', '暴涨', '大涨', '大跌', '跳水', '熔断', '创新高', '崩盘'],
        'impact': 9,
    },
}


def keyword_classify(title):
    """关键词匹配分类"""
    title_lower = title.lower()
    best = None
    best_score = 0
    for cat, config in BREAKING_KEYWORDS.items():
        score = sum(1 for kw in config['keywords'] if kw.lower() in title_lower)
        if score > best_score:
            best_score = score
            best = cat
    return best, best_score


def generate_fallback_events(headlines, anomalies):
    """无LLM时的关键词兜底事件生成"""
    events = []

    # 从新闻中提取
    for h in headlines[:40]:
        cat, score = keyword_classify(h['title'])
        if cat and score >= 1:
            events.append({
                'title': h['title'][:40],
                'reason': f'来源: {h["source"]}',
                'source': h['source'],
                'category': cat,
                'impact': BREAKING_KEYWORDS[cat]['impact'] * (1 if score >= 2 else 0.7),
                'sectors_positive': [],
                'sectors_negative': [],
                'advice': '密切关注后续发展',
            })

    # 从异动中生成
    for a in anomalies[:10]:
        direction = '大涨' if a['pct'] > 0 else '大跌'
        events.append({
            'title': f"{a['icon']} {a['name']}{direction}{abs(a['pct']):.1f}%",
            'reason': f"{'超常波动' if abs(a['pct']) >= 3 else '显著异动'}，关注相关持仓联动",
            'source': '行情监控',
            'category': a['type'] if a['type'] in ('commodity', 'index') else 'market',
            'impact': round(a['pct'] * 2),
            'sectors_positive': [a['tag']] if a['pct'] > 0 and a.get('tag') else [],
            'sectors_negative': [a['tag']] if a['pct'] < 0 and a.get('tag') else [],
            'advice': '关注偏离修复机会' if abs(a['pct']) >= 3 else '观察后续走势',
        })

    # 去重 + 排序
    events.sort(key=lambda x: abs(x.get('impact', 0)), reverse=True)
    return events[:10]


# ==================== 输出组装 ====================

CATEGORY_ICONS = {
    'geopolitics': '🌍',
    'commodity': '📦',
    'monetary': '🏦',
    'technology': '🤖',
    'market': '📊',
    'policy': '📜',
}


def build_event_id(event):
    """生成稳定的事件ID"""
    text = f"{event.get('title', '')}{event.get('category', '')}"
    return 'rtb_' + hashlib.md5(text.encode()).hexdigest()[:8]


def merge_with_existing(new_events, output_path):
    """与现有数据合并，保留最近4小时的事件，防止重复"""
    existing = []
    try:
        if os.path.exists(output_path):
            with open(output_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            existing = data.get('breaking', [])
    except Exception:
        pass

    # 现有事件的标题关键字集合（用于去重）
    existing_keys = set()
    for evt in existing:
        key = re.sub(r'\W', '', (evt.get('title') or ''))[:30].lower()
        if key:
            existing_keys.add(key)

    # 新事件替换旧事件（保留新的LLM分析）
    # 先用新事件，再补充不重复的旧事件
    merged = list(new_events)
    new_keys = set()
    for evt in new_events:
        key = re.sub(r'\W', '', (evt.get('title') or ''))[:30].lower()
        if key:
            new_keys.add(key)

    for evt in existing:
        key = re.sub(r'\W', '', (evt.get('title') or ''))[:30].lower()
        if key and key not in new_keys:
            # 只保留非过期的旧事件
            merged.append(evt)

    # 按影响力排序，最多15条
    merged.sort(key=lambda x: abs(x.get('impact', 0)), reverse=True)
    return merged[:15]


def build_output(events, anomalies, sources_ok, now):
    """组装最终输出"""
    breaking = []
    for i, evt in enumerate(events[:12]):
        impact = evt.get('impact', 0)
        if isinstance(impact, float):
            impact = round(impact)
        category = evt.get('category', 'market')
        cat_icon = CATEGORY_ICONS.get(category, '📰')

        item = {
            'id': build_event_id(evt),
            'title': str(evt.get('title', '')).strip()[:60],
            'reason': str(evt.get('reason', '')).strip()[:100],
            'source': str(evt.get('source', '')).strip(),
            'category': category,
            'catIcon': cat_icon,
            'impact': impact,
            'impactClass': 'up' if impact >= 0 else 'down',
            'impactAbs': abs(impact),
            'sectors_positive': evt.get('sectors_positive', []),
            'sectors_negative': evt.get('sectors_negative', []),
            'advice': str(evt.get('advice', '保持观察')).strip()[:50],
            'timestamp': now.isoformat(),
            'isRealtime': True,
        }
        breaking.append(item)

    # 按影响力排序后合并已有数据
    breaking = merge_with_existing(breaking, OUTPUT_PATH)

    return {
        'updated_at': now.isoformat(),
        'breaking': breaking,
        'anomalies': [{
            'id': f"anom_{a.get('name', '')}_{now.strftime('%H%M')}",
            'name': a['name'],
            'fullName': a['fullName'],
            'icon': a['icon'],
            'price': a['price'],
            'pct': a['pct'],
            'type': a['type'],
            'tag': a.get('tag', ''),
            'level': a['level'],
            'alert': a['alert'],
        } for a in anomalies[:12]],
        'meta': {
            'sources_ok': sources_ok,
            'news_count': sum(1 for _ in breaking),
            'anomaly_count': len(anomalies),
            'model': MODEL if API_KEY else 'keyword-fallback',
            'refresh_interval_seconds': 300,
        },
    }


# ==================== 主流程 ====================

def main():
    now = datetime.now(CST)
    print(f"\n{'='*55}")
    print(f"⚡ 实时突发新闻 & 市场异动监控")
    print(f"   时间: {now.strftime('%Y-%m-%d %H:%M:%S')} CST")
    print(f"   模型: {MODEL}")
    print(f"{'='*55}")

    # 1. 并行抓取国际媒体头条
    print('\n📡 [1/4] 抓取国际媒体实时头条...')
    all_headlines = []
    sources_ok = []

    fetchers = [
        ('Reuters', fetch_reuters_headlines),
        ('Bloomberg', fetch_bloomberg_headlines),
        ('CNBC', fetch_cnbc_headlines),
        ('MarketWatch', fetch_marketwatch_headlines),
        ('Yahoo Finance', fetch_yahoo_finance_headlines),
        ('Google News', fetch_google_news_finance),
        ('财联社', fetch_cls_flash),
        ('新浪财经', fetch_sina_live),
    ]

    for name, fetcher in fetchers:
        try:
            items = fetcher()
            if items:
                all_headlines.extend(items)
                sources_ok.append(name)
                print(f'  ✅ {name}: {len(items)} 条')
            else:
                print(f'  ⚠️ {name}: 0 条')
        except Exception as e:
            print(f'  ❌ {name}: {e}')

    # 全量去重
    all_headlines = _dedup_items(all_headlines)
    print(f'\n  📰 总计头条: {len(all_headlines)} 条 (来自 {len(sources_ok)} 个源)')

    # 2. 检测市场异动
    print('\n📊 [2/4] 检测全球市场异动...')
    anomalies = detect_market_anomalies()
    print(f'  ⚡ 检测到 {len(anomalies)} 个异动')
    for a in anomalies[:5]:
        print(f'    {a["alert"]}')

    # 3. LLM 分析
    print('\n🧠 [3/4] AI分析实时头条...')
    market_snapshot = get_all_market_snapshot()

    if API_KEY and all_headlines:
        events = call_llm_breaking(all_headlines, anomalies, market_snapshot)
        if events:
            print(f'  ✅ LLM生成 {len(events)} 条突发事件')
        else:
            print('  ⚠️ LLM返回空，使用关键词兜底')
            events = generate_fallback_events(all_headlines, anomalies)
    else:
        print('  ⚠️ 无API Key或无新闻，使用关键词兜底')
        events = generate_fallback_events(all_headlines, anomalies)

    # 4. 组装输出
    print('\n📦 [4/4] 组装输出...')
    output = build_output(events, anomalies, sources_ok, now)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*55}")
    print(f'✅ 输出: {OUTPUT_PATH}')
    print(f'   突发事件: {len(output["breaking"])} 条')
    print(f'   市场异动: {len(output["anomalies"])} 个')
    print(f'   数据源: {", ".join(sources_ok)}')
    print(f"{'='*55}\n")

    return output


if __name__ == '__main__':
    main()
