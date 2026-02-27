#!/usr/bin/env python3
"""
舆情数据采集器 — 后端采集所有数据源，缓存到 JSON 文件
支持: 抖音 / 微博 / 东方财富 / 财联社 / 知乎 / 百度 / B站
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
    '红利','高股息','银行','保险','券商','地产',
    '央行','降息','降准','LPR','利率','通胀','CPI','GDP','PMI',
    '美联储','加息','国债','债券','汇率','人民币',
    '关税','贸易战','制裁','地缘','中东','俄乌',
    '基金','ETF','牛市','熊市','涨停','跌停','抄底','追高',
    '仓位','加仓','减仓','定投','主力','资金','北向',
    '茅台','比亚迪','宁德','英伟达','NVIDIA','特斯拉',
    'IPO','分红','回购','并购','重组','股','基','市场','经济','投资','收益','行情',
    '板块','指数','概念','题材','龙头','主线','赛道',
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
    """抖音热搜"""
    items = []
    try:
        r = requests.get('https://aweme.snssdk.com/aweme/v1/hot/search/list/',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        word_list = (data.get('data') or {}).get('word_list') or data.get('word_list') or []
        for item in word_list:
            word = item.get('word') or item.get('content') or ''
            hot = safe_int(item.get('hot_value') or item.get('score'))
            if word and is_finance(word):
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
        print(f'[抖音] 采集失败: {e}')
    return items

def fetch_weibo():
    """微博热搜 — 尝试多种方式"""
    items = []
    # Approach 1: ajax API
    try:
        r = requests.get('https://weibo.com/ajax/side/hotSearch',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in (data.get('data') or {}).get('realtime') or []:
            word = item.get('word') or item.get('note') or ''
            hot = safe_int(item.get('raw_hot') or item.get('num'))
            if word and is_finance(word):
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

    # Approach 2: mobile API
    if not items:
        try:
            r = requests.get(
                'https://m.weibo.cn/api/container/getIndex?containerid=106003type%3D25%26t%3D3%26disable_hot%3D1%26filter_type%3Drealtimehot',
                headers=HEADERS, timeout=TIMEOUT)
            data = r.json()
            for card in (data.get('data') or {}).get('cards') or []:
                for g in card.get('card_group') or []:
                    word = g.get('desc') or ''
                    hot = safe_int(g.get('desc_extr'))
                    if word and is_finance(word):
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
            print(f'[微博-mobile] {e}')

    return items

def fetch_eastmoney():
    """东方财富 7x24 快讯"""
    items = []
    try:
        url = f'https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_724&column=350&pageSize=50&maxNewsId=0&type=0&req_trace=sa_{int(time.time())}'
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in (data.get('data') or {}).get('list') or []:
            title = (item.get('title') or '').strip()
            content = (item.get('content') or '').strip()
            text = title or content[:100]
            if text and len(text) >= 4:
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
        print(f'[东方财富] 采集失败: {e}')
    return items

def fetch_cailian():
    """财联社电报"""
    items = []
    try:
        r = requests.get(
            'https://www.cls.cn/nodeapi/updateTelegraphList?app=CailianpressWeb&os=web&sv=8.4.6&rn=30',
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
    """知乎热榜"""
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
    """百度热搜"""
    items = []
    try:
        r = requests.get('https://top.baidu.com/api/board?platform=wise&tab=realtime',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        flat_list = []
        for card in (data.get('data') or {}).get('cards') or []:
            for c in card.get('content') or []:
                if isinstance(c.get('content'), list):
                    flat_list.extend(c['content'])
                elif c.get('word'):
                    flat_list.append(c)
        for item in flat_list:
            word = item.get('word') or ''
            desc = item.get('desc') or ''
            hot = safe_int(item.get('hotScore'))
            if word and is_finance(word + ' ' + desc):
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
        print(f'[百度] 采集失败: {e}')
    return items

def fetch_bilibili():
    """B站热搜 + 排行"""
    items = []
    try:
        # 热搜词
        r = requests.get('https://s.search.bilibili.com/main/hotword',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in data.get('list') or []:
            kw = item.get('keyword') or ''
            if kw and is_finance(kw):
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

    try:
        # 排行榜
        r = requests.get('https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all',
                         headers=HEADERS, timeout=TIMEOUT)
        data = r.json()
        for item in (data.get('data') or {}).get('list') or []:
            title = item.get('title') or ''
            desc = item.get('desc') or ''
            views = (item.get('stat') or {}).get('view') or 0
            if title and is_finance(title + ' ' + desc):
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
    ('知乎', fetch_zhihu),
    ('百度', fetch_baidu),
    ('B站', fetch_bilibili),
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

def collect_and_save():
    """采集并保存 — 供 cron 或 server 调用"""
    data = collect_all()
    save_cache(data)
    return data

# ==================== CLI ====================
if __name__ == '__main__':
    data = collect_and_save()
    print(f'\n采集完成: {data["total"]} 条, 时间: {data["fetch_time"]}')
    for name, count in data['source_counts'].items():
        print(f'  {name}: {count}')
