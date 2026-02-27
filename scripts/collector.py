#!/usr/bin/env python3
"""
舆情数据采集器 — 后端采集所有数据源，缓存到 JSON 文件
支持: 抖音/头条 / 微博 / 东方财富 / 财联社 / 新浪财经 / 知乎 / 百度 / B站
"""

import json, re, os, time, hashlib, traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# ==================== 常量 ====================
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
CACHE_FILE = os.path.join(DATA_DIR, 'sentiment_cache.json')
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
HEADERS = {'User-Agent': UA}
TIMEOUT = 15

# ==================== 财经关键词 ====================
FINANCE_KW = [
    'A股','股市','大盘','沪指','上证','深成','创业板','科创板','沪深300','恒生','港股','美股','纳斯达克',
    'AI','人工智能','算力','芯片','半导体','光模块','CPO','大模型','DeepSeek',
    '机器人','自动驾驶','新能源','光伏','锂电','碳酸锂','储能',
    '军工','国防','航天','白酒','消费','医药','创新药','CXO',
    '黄金','金价','原油','油价','有色金属','铜','铝','稀土',
    '红利','高股息','银行','保险','券商','地产','房价','楼市','房地产',
    '央行','降息','降准','LPR','利率','通胀','CPI','GDP','PMI',
    '美联储','加息','国债','债券','汇率','人民币',
    '关税','贸易战','制裁',
    '基金','ETF','牛市','熊市','涨停','跌停','抄底','追高',
    '仓位','加仓','减仓','定投','主力','资金流','北向资金',
    '茅台','比亚迪','宁德','英伟达','NVIDIA','特斯拉','格力','万达',
    'IPO','分红','回购','并购','重组','减持','增持',
    '板块','指数','概念股','题材','龙头股','主线','赛道',
    '净利润','营收','业绩','净值','估值','市盈率','市值',
    '电力','农业','春耕',
    '私募','公募','期货','期权','基民','股民','散户',
    '目标价','评级','买入','卖出','持有','看多','看空',
    '投资者','融资','融券','杠杆','做空','做多','止损',
    '证券','上市','港交所','外汇','利润','亏损','盈利','资产','负债',
    '经济','金融',
]
_kw_lower = [kw.lower() for kw in FINANCE_KW]

def is_finance(text):
    if not text:
        return False
    t = text.lower()
    return any(kw in t for kw in _kw_lower)

def estimate_sentiment(text):
    if not text:
        return '中性'
    if re.search(r'暴涨|疯涨|大涨|飙升|涨停|全仓|梭哈|起飞|爆发|牛市|创新高|狂热', text):
        return '极度看多'
    if re.search(r'上涨|走高|反弹|利好|加仓|机会|突破|看好|推荐|配置|走强', text):
        return '偏多'
    if re.search(r'暴跌|崩盘|大跌|跳水|清仓|割肉|熊市|腰斩', text):
        return '极度悲观'
    if re.search(r'下跌|走低|利空|减仓|风险|警惕|谨慎|回调|承压|重挫', text):
        return '偏空'
    if re.search(r'震荡|分歧|观望|持平|稳定|盘整', text):
        return '中性'
    return '中性偏多'

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def safe_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default

# ==================== 各数据源采集 ====================

def fetch_douyin():
    """抖音热搜 + 头条热搜 + 头条财经频道（同属字节跳动）— 目标 30+"""
    items = []
    seen = set()

    # Source 1: 抖音热搜 API (finance filtered)
    try:
        r = requests.get('https://aweme.snssdk.com/aweme/v1/hot/search/list/',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        word_list = (data.get('data') or {}).get('word_list') or data.get('word_list') or []
        for item in word_list:
            word = item.get('word') or item.get('content') or ''
            hot = safe_int(item.get('hot_value') or item.get('score'))
            if word and is_finance(word) and word not in seen:
                seen.add(word)
                items.append({
                    'title': word[:80],
                    'summary': word,
                    'likes': hot,
                    'platform': '抖音',
                    'source_type': '热搜',
                    'sentiment': estimate_sentiment(word),
                    'creator_type': '社交热搜',
                    'publish_time': now_iso(),
                })
    except Exception as e:
        print(f'[抖音] {e}')

    # Source 2: 头条热搜 API（字节跳动旗下）(finance filtered)
    try:
        r = requests.get('https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in data.get('data') or []:
            title = item.get('Title') or ''
            hot = safe_int(item.get('HotValue'))
            if title and is_finance(title) and title not in seen:
                seen.add(title)
                items.append({
                    'title': title[:80],
                    'summary': title,
                    'likes': hot,
                    'platform': '抖音',
                    'source_type': '头条热搜',
                    'sentiment': estimate_sentiment(title),
                    'creator_type': '社交热搜',
                    'publish_time': now_iso(),
                })
    except Exception as e:
        print(f'[头条] {e}')

    # Source 3: 头条财经频道信息流（分页获取，每页10条，取3页=30条）
    max_behot = 0
    for page_idx in range(3):
        try:
            r = requests.get(
                f'https://www.toutiao.com/api/pc/feed/?category=news_finance&max_behot_time={max_behot}&widen=1&tadrequire=true',
                headers=HEADERS, timeout=TIMEOUT)
            data = r.json()
            feed_items = data.get('data') or []
            for item in feed_items:
                if not isinstance(item, dict):
                    continue
                title = (item.get('title') or '').strip()
                abstract = (item.get('abstract') or '').strip()
                if not title or title in seen:
                    continue
                seen.add(title)
                items.append({
                    'title': title[:80],
                    'summary': (abstract[:200] or title),
                    'likes': safe_int(item.get('hot', 0)),
                    'platform': '抖音',
                    'source_type': '头条财经',
                    'sentiment': estimate_sentiment(title + ' ' + abstract),
                    'creator_type': '财经资讯平台',
                    'publish_time': now_iso(),
                })
                bt = item.get('behot_time', 0)
                if bt:
                    max_behot = bt
        except Exception as e:
            print(f'[头条财经-page{page_idx+1}] {e}')
            break

    return items

def fetch_weibo():
    """微博热搜 — 通过 Tophub 聚合 API + 官方 API（财经过滤）— 目标 30+"""
    items = []
    seen = set()

    # Approach 1: Tophub 聚合 API（稳定可用，全量采集 ~51条）
    try:
        r = requests.get('https://api.codelife.cc/api/top/list?lang=cn&id=KqndgxeLl9',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in data.get('data') or []:
            word = (item.get('title') or '').strip()
            hot_str = item.get('hotValue') or ''
            # 解析 "108万" → 1080000
            hot = 0
            m = re.match(r'([\d.]+)\s*万', hot_str)
            if m:
                hot = int(float(m.group(1)) * 10000)
            else:
                hot = safe_int(re.sub(r'[^\d]', '', hot_str))
            if word and is_finance(word) and word not in seen:
                seen.add(word)
                items.append({
                    'title': word[:80],
                    'summary': word,
                    'likes': hot,
                    'platform': '微博',
                    'source_type': '热搜',
                    'sentiment': estimate_sentiment(word),
                    'creator_type': '微博热搜',
                    'publish_time': now_iso(),
                })
    except Exception as e:
        print(f'[微博-tophub] {e}')

    # Approach 2: 官方 ajax API 兜底
    if len(items) < 30:
        try:
            r = requests.get('https://weibo.com/ajax/side/hotSearch',
                             headers=HEADERS, timeout=TIMEOUT)
            data = r.json()
            for item in (data.get('data') or {}).get('realtime') or []:
                word = item.get('word') or item.get('note') or ''
                hot = safe_int(item.get('raw_hot') or item.get('num'))
                if word and is_finance(word) and word not in seen:
                    seen.add(word)
                    items.append({
                        'title': word[:80],
                        'summary': word,
                        'likes': hot,
                        'platform': '微博',
                        'source_type': '热搜',
                        'sentiment': estimate_sentiment(word),
                        'creator_type': '微博热搜',
                        'publish_time': now_iso(),
                    })
        except Exception as e:
            print(f'[微博-ajax] {e}')

    return items

def fetch_eastmoney():
    """东方财富 7x24 快讯 + Tophub 东方财富热榜 — 目标 30+"""
    items = []
    seen = set()

    # Source 1: 7x24 快讯 API (finance focused, ~10 items)
    try:
        url = f'https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_724&column=350&pageSize=50&maxNewsId=0&type=0&req_trace=sa_{int(time.time())}'
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in (data.get('data') or {}).get('list') or []:
            title = (item.get('title') or '').strip()
            content = (item.get('content') or '').strip()
            text = title or content[:100]
            if text and len(text) >= 4 and text not in seen:
                seen.add(text)
                items.append({
                    'title': text[:80],
                    'summary': (content[:200] or text),
                    'likes': 0,
                    'platform': '东方财富',
                    'source_type': '快讯',
                    'sentiment': estimate_sentiment(text + ' ' + content),
                    'creator_type': '财经资讯平台',
                    'publish_time': item.get('showTime') or now_iso(),
                })
    except Exception as e:
        print(f'[东方财富-7x24] {e}')

    # Source 2: Tophub 东方财富热榜 (~20 items)
    try:
        r = requests.get('https://api.codelife.cc/api/top/list?lang=cn&id=Y2KeDGQdNP',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in data.get('data') or []:
            title = (item.get('title') or '').strip()
            if title and title not in seen:
                seen.add(title)
                items.append({
                    'title': title[:80],
                    'summary': title,
                    'likes': 0,
                    'platform': '东方财富',
                    'source_type': '热榜',
                    'sentiment': estimate_sentiment(title),
                    'creator_type': '财经资讯平台',
                    'publish_time': now_iso(),
                })
    except Exception as e:
        print(f'[东方财富-tophub] {e}')

    return items

def fetch_cailian():
    """财联社电报 — 目标 50 条"""
    items = []
    try:
        r = requests.get(
            'https://www.cls.cn/nodeapi/updateTelegraphList?app=CailianpressWeb&os=web&sv=8.4.6&rn=50',
            headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in (data.get('data') or {}).get('roll_data') or []:
            title = (item.get('title') or '').strip()
            content = re.sub(r'<[^>]+>', '', item.get('content') or '').strip()
            text = title or content[:100]
            if text and len(text) >= 4:
                pub_time = now_iso()
                if item.get('ctime'):
                    pub_time = datetime.fromtimestamp(item['ctime'], tz=timezone.utc).isoformat()
                items.append({
                    'title': text[:80],
                    'summary': (content[:200] or text),
                    'likes': 0,
                    'platform': '财联社',
                    'source_type': '电报',
                    'sentiment': estimate_sentiment(text + ' ' + content),
                    'creator_type': '财经资讯平台',
                    'publish_time': pub_time,
                })
    except Exception as e:
        print(f'[财联社] 采集失败: {e}')
    return items

def fetch_zhihu():
    """知乎热榜 — 财经过滤，目标 50 条"""
    items = []
    try:
        r = requests.get('https://api.zhihu.com/topstory/hot-lists/total?limit=50',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in data.get('data') or []:
            target = item.get('target') or {}
            title = target.get('title') or ''
            excerpt = target.get('excerpt') or ''
            detail = item.get('detail_text') or ''
            hot = safe_int(re.sub(r'[^\d]', '', detail))
            if title and is_finance(title + ' ' + excerpt):
                items.append({
                    'title': title[:80],
                    'summary': (excerpt[:200] or title),
                    'likes': hot,
                    'platform': '知乎',
                    'source_type': '热榜',
                    'sentiment': estimate_sentiment(title + ' ' + excerpt),
                    'creator_type': '聚合热榜',
                    'publish_time': now_iso(),
                })
    except Exception as e:
        print(f'[知乎] 采集失败: {e}')
    return items

def fetch_baidu():
    """百度热搜 (realtime 财经过滤 + 财经频道) — 目标 30+"""
    items = []
    seen = set()

    def _parse_baidu(data):
        flat = []
        for card in (data.get('data') or {}).get('cards') or []:
            for c in card.get('content') or []:
                if isinstance(c.get('content'), list):
                    flat.extend(c['content'])
                elif c.get('word'):
                    flat.append(c)
        return flat

    # Source 1: 实时热搜（财经过滤）
    try:
        r = requests.get('https://top.baidu.com/api/board?platform=wise&tab=realtime',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in _parse_baidu(data):
            word = item.get('word') or ''
            desc = item.get('desc') or ''
            hot = safe_int(item.get('hotScore'))
            if word and is_finance(word + ' ' + desc) and word not in seen:
                seen.add(word)
                items.append({
                    'title': word[:80],
                    'summary': (desc[:200] or word),
                    'likes': hot,
                    'platform': '百度',
                    'source_type': '热搜',
                    'sentiment': estimate_sentiment(word + ' ' + desc),
                    'creator_type': '聚合热榜',
                    'publish_time': now_iso(),
                })
    except Exception as e:
        print(f'[百度-realtime] {e}')

    # Source 2: 财经热搜（补充财经专题）
    if len(items) < 30:
        try:
            r = requests.get('https://top.baidu.com/api/board?platform=wise&tab=finance',
                             headers=HEADERS, timeout=TIMEOUT)
            data = r.json()
            for item in _parse_baidu(data):
                word = item.get('word') or ''
                desc = item.get('desc') or ''
                hot = safe_int(item.get('hotScore'))
                if word and word not in seen:
                    seen.add(word)
                    items.append({
                        'title': word[:80],
                        'summary': (desc[:200] or word),
                        'likes': hot,
                        'platform': '百度',
                        'source_type': '财经热搜',
                        'sentiment': estimate_sentiment(word + ' ' + desc),
                        'creator_type': '聚合热榜',
                        'publish_time': now_iso(),
                    })
        except Exception as e:
            print(f'[百度-finance] {e}')

    return items

def fetch_bilibili():
    """B站财经频道 + 热搜 + 排行 — 目标 30+"""
    items = []
    seen = set()

    # Source 1: 财经频道动态（rid=207, 二次过滤确保财经相关）~50条
    try:
        r = requests.get('https://api.bilibili.com/x/web-interface/dynamic/region?rid=207&ps=50',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in (data.get('data') or {}).get('archives') or []:
            title = (item.get('title') or '').strip()
            desc = (item.get('desc') or '').strip()
            views = (item.get('stat') or {}).get('view') or 0
            if title and is_finance(title + ' ' + desc) and title not in seen:
                seen.add(title)
                items.append({
                    'title': title[:80],
                    'summary': (desc[:200] or title),
                    'likes': safe_int(views),
                    'platform': 'B站',
                    'source_type': '财经频道',
                    'sentiment': estimate_sentiment(title + ' ' + desc),
                    'creator_type': '视频社区',
                    'publish_time': now_iso(),
                })
    except Exception as e:
        print(f'[B站-财经频道] {e}')

    # Source 2: 热搜词（财经过滤）
    try:
        r = requests.get('https://s.search.bilibili.com/main/hotword',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in data.get('list') or []:
            kw = item.get('keyword') or ''
            if kw and is_finance(kw) and kw not in seen:
                seen.add(kw)
                items.append({
                    'title': kw[:80],
                    'summary': kw,
                    'likes': safe_int(item.get('heat_score')),
                    'platform': 'B站',
                    'source_type': '热搜',
                    'sentiment': estimate_sentiment(kw),
                    'creator_type': '聚合热榜',
                    'publish_time': now_iso(),
                })
    except Exception as e:
        print(f'[B站-热搜] {e}')

    # Source 3: 全站排行榜（财经过滤）
    try:
        r = requests.get('https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in (data.get('data') or {}).get('list') or []:
            title = item.get('title') or ''
            desc = item.get('desc') or ''
            views = (item.get('stat') or {}).get('view') or 0
            if title and is_finance(title + ' ' + desc) and title not in seen:
                seen.add(title)
                items.append({
                    'title': title[:80],
                    'summary': (desc[:200] or title),
                    'likes': safe_int(views),
                    'platform': 'B站',
                    'source_type': '排行',
                    'sentiment': estimate_sentiment(title + ' ' + desc),
                    'creator_type': '聚合热榜',
                    'publish_time': now_iso(),
                })
    except Exception as e:
        print(f'[B站-排行] {e}')

    return items

def fetch_sina_finance():
    """新浪财经热点新闻 — 目标 50 条"""
    items = []
    try:
        r = requests.get(
            'https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=50&page=1',
            headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in (data.get('result') or {}).get('data') or []:
            title = (item.get('title') or '').strip()
            summary = (item.get('intro') or item.get('summary') or '').strip()
            pub_time = now_iso()
            if item.get('ctime'):
                try:
                    pub_time = datetime.fromtimestamp(int(item['ctime']), tz=timezone.utc).isoformat()
                except: pass
            if title and len(title) >= 4:
                items.append({
                    'title': title[:80],
                    'summary': (summary[:200] or title),
                    'likes': 0,
                    'platform': '新浪财经',
                    'source_type': '财经新闻',
                    'sentiment': estimate_sentiment(title + ' ' + summary),
                    'creator_type': '财经资讯平台',
                    'publish_time': pub_time,
                })
    except Exception as e:
        print(f'[新浪财经] 采集失败: {e}')
    return items

def _parse_xhs_ssr(html):
    """从小红书 HTML 中提取 __INITIAL_STATE__ SSR 数据."""
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(.+?)</script>', html, re.DOTALL)
    if not m:
        return []
    raw = m.group(1).strip().rstrip(';').replace('undefined', 'null')
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    feeds = data.get('feed', {}).get('feeds', [])
    if not feeds:
        feeds = data.get('homeFeed', {}).get('feeds', [])
    results = []
    for f in feeds:
        card = f.get('noteCard') or {}
        title = (card.get('displayTitle') or '').strip()
        if not title:
            continue
        interact = card.get('interactInfo', {})
        liked_str = interact.get('likedCount', '0')
        if isinstance(liked_str, str) and '万' in liked_str:
            liked = int(float(liked_str.replace('万', '')) * 10000)
        else:
            liked = safe_int(liked_str)
        results.append({'title': title, 'likes': liked})
    return results


def fetch_xiaohongshu():
    """小红书热门 — explore SSR + 频道页，间歇性可用 — 目标 30+"""
    items = []
    seen = set()

    def _add(notes, source_type):
        for n in notes:
            t = n['title']
            if t in seen:
                continue
            seen.add(t)
            items.append({
                'title': t[:80],
                'summary': t,
                'likes': n['likes'],
                'platform': '小红书',
                'source_type': source_type,
                'sentiment': estimate_sentiment(t),
                'creator_type': '小红书博主',
                'publish_time': now_iso(),
            })

    # Source 1: explore 首页 SSR
    try:
        r = requests.get('https://www.xiaohongshu.com/explore', headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200 and len(r.text) > 50000:
            notes = _parse_xhs_ssr(r.text)
            _add(notes, '热门笔记')
        else:
            print(f'[小红书] explore 返回 {len(r.text)} 字节 (无 SSR)')
    except Exception as e:
        print(f'[小红书-explore] {e}')

    # Source 2: 频道页（推荐/美食/旅行等，各频道内容不同）
    if len(items) < 30:
        channels = ['homefeed_recommend', 'homefeed.food_v3', 'homefeed.travel_v3']
        for cid in channels:
            if len(items) >= 40:
                break
            try:
                r = requests.get(f'https://www.xiaohongshu.com/explore?channel_id={cid}',
                                 headers=HEADERS, timeout=TIMEOUT)
                if r.status_code == 200 and len(r.text) > 50000:
                    notes = _parse_xhs_ssr(r.text)
                    _add(notes, '频道热门')
            except Exception as e:
                print(f'[小红书-{cid}] {e}')

    if not items:
        print('[小红书] SSR 不可用（可能被限流），本次返回 0 条')

    return items


# ==================== 隔夜美股行情 ====================
US_MARKET_CACHE = os.path.join(DATA_DIR, 'us_market_cache.json')

# 追踪的美股标的: (symbol, 中文名, 类型)
US_SYMBOLS = [
    ('NVDA',  '英伟达',    '半导体'),
    ('AMD',   'AMD',      '半导体'),
    ('AVGO',  '博通',      '半导体'),
    ('TSLA',  '特斯拉',    '新能源车'),
    ('AAPL',  '苹果',      '科技'),
    ('SOXX',  '半导体ETF', '半导体指数'),
    ('.IXIC', '纳斯达克',  '美股指数'),
    ('.INX',  '标普500',   '美股指数'),
    ('.DJI',  '道琼斯',    '美股指数'),
]

def fetch_us_market():
    """从雪球获取隔夜美股行情 — 半导体 + 科技 + 三大指数"""
    try:
        s = requests.Session()
        s.headers.update({'User-Agent': UA})
        s.get('https://xueqiu.com/', timeout=5)  # 获取 cookie

        symbols = ','.join(sym for sym, _, _ in US_SYMBOLS)
        r = s.get(
            f'https://stock.xueqiu.com/v5/stock/realtime/quotec.json?symbol={symbols}',
            timeout=TIMEOUT
        )
        data = r.json()
        sym_map = {sym: (cn, cat) for sym, cn, cat in US_SYMBOLS}

        results = []
        for item in data.get('data', []):
            sym = item.get('symbol', '')
            cn, cat = sym_map.get(sym, (sym, '其他'))
            results.append({
                'symbol': sym,
                'name': cn,
                'category': cat,
                'price': item.get('current'),
                'change': round(item.get('chg', 0), 2),
                'percent': round(item.get('percent', 0), 2),
                'amplitude': item.get('amplitude'),
                'high': item.get('high'),
                'low': item.get('low'),
                'volume': item.get('volume'),
                'market_cap': item.get('market_capital'),
                'timestamp': item.get('timestamp'),
            })

        # 保存缓存
        cache = {
            'stocks': results,
            'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'fetch_ts': int(time.time()),
        }
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(US_MARKET_CACHE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f'  🇺🇸 美股行情: {len(results)} 个标的已缓存')
        return cache
    except Exception as e:
        print(f'  ⚠️ 美股行情采集失败: {e}')
        return None

def load_us_market_cache():
    """读取美股行情缓存"""
    if not os.path.exists(US_MARKET_CACHE):
        return None
    try:
        with open(US_MARKET_CACHE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


# ==================== 去重 ====================
def dedup(items):
    seen = set()
    result = []
    for item in items:
        key = re.sub(r'[\W\s]', '', (item.get('title') or ''))[:20]
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result

# ==================== 主采集流程 ====================
ALL_FETCHERS = [
    ('抖音', fetch_douyin),
    ('微博', fetch_weibo),
    ('东方财富', fetch_eastmoney),
    ('财联社', fetch_cailian),
    ('新浪财经', fetch_sina_finance),
    ('知乎', fetch_zhihu),
    ('百度', fetch_baidu),
    ('B站', fetch_bilibili),
    ('小红书', fetch_xiaohongshu),
]

def collect_all():
    """并行采集所有数据源，返回 { items, source_counts, fetch_time, ... }"""
    all_items = []
    source_counts = {}
    errors = []

    print(f'[{datetime.now().strftime("%H:%M:%S")}] 开始采集 {len(ALL_FETCHERS)} 个数据源...')

    with ThreadPoolExecutor(max_workers=len(ALL_FETCHERS)) as executor:
        futures = {executor.submit(fn): name for name, fn in ALL_FETCHERS}
        for future in as_completed(futures):
            name = futures[future]
            try:
                items = future.result(timeout=20)
                source_counts[name] = len(items)
                all_items.extend(items)
                print(f'  ✅ {name}: {len(items)} 条')
            except Exception as e:
                source_counts[name] = 0
                errors.append(f'{name}: {str(e)}')
                print(f'  ❌ {name}: {e}')

    # 去重 + 排序
    all_items = dedup(all_items)
    all_items.sort(key=lambda x: x.get('likes', 0), reverse=True)

    result = {
        'items': all_items,
        'source_counts': source_counts,
        'total': len(all_items),
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'fetch_ts': int(time.time()),
        'errors': errors,
    }

    print(f'  📊 共计 {len(all_items)} 条 (去重后)')
    return result

def save_cache(data):
    """将采集结果保存到 JSON 缓存文件"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'  💾 缓存已保存: {CACHE_FILE}')

def load_cache():
    """读取缓存文件"""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

def collect_and_save(run_analysis=True):
    """采集并保存，可选自动运行 AI 分析 — 供 cron 或 server 调用"""
    # 先采集美股行情（不影响主流程）
    us_data = fetch_us_market()
    data = collect_all()
    save_cache(data)
    if run_analysis and data.get('items'):
        try:
            import importlib, sys
            # Ensure scripts dir is on path for sibling import
            scripts_dir = os.path.dirname(os.path.abspath(__file__))
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from analyzer import analyze_and_save
            analyze_and_save(data['items'])
        except Exception as e:
            print(f'  ⚠️ AI 分析阶段出错: {e}')
    return data

# ==================== CLI ====================
if __name__ == '__main__':
    data = collect_and_save()
    print(f'\n采集完成: {data["total"]} 条, 时间: {data["fetch_time"]}')
    for name, count in data['source_counts'].items():
        print(f'  {name}: {count}')
