#!/usr/bin/env python3
"""
基金助手 - 宏观事件自动追踪管道
GitHub Actions 每2小时运行: 抓取财经新闻 → LLM结构化提取 → 输出 data/hot_events.json

数据流: 新闻源 → AI事件提炼 → 概念标签 → 行业映射 → hot_events.json → 前端消费
"""

import json, os, re, sys, ssl, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor, HTTPSHandler

# ==================== .env 自动加载 ====================
def _load_dotenv():
    """从项目根目录 .env 文件加载环境变量（不覆盖已有变量）"""
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
API_BASE = os.environ.get('AI_API_BASE', 'https://api.siliconflow.cn/v1')
MODEL = os.environ.get('AI_MODEL', 'deepseek-ai/DeepSeek-V3')
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'hot_events.json')
SENTIMENT_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'sentiment_cache.json')
ANALYSIS_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'analysis_cache.json')
FIRST_SEEN_CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'event_first_seen.json')
XUEQIU_COOKIE = os.environ.get('XUEQIU_COOKIE', '').strip()

# 20个核心市场标签 (前端fund标签体系对齐)
MARKET_TAGS = [
    '人工智能', 'AI算力', '半导体', '机器人', '大模型',
    '新能源', '光伏', '锂电', '新能源车',
    '白酒', '消费', '医药', '港股科技',
    '黄金', '有色金属', '原油', '军工',
    '红利', '债券', '宽基',
]

# 标签 → 行业板块映射 (与前端 FUND_SECTOR_KEYWORD_MAP 对齐)
TAG_TO_SECTORS = {
    '人工智能': ['AI/科技', '科技', 'AIGC'],
    'AI算力': ['AI/科技', '半导体', '算力'],
    '半导体': ['半导体', '芯片', '科技'],
    '机器人': ['机器人', 'AI/科技', '科技'],
    '大模型': ['AI/科技', 'AIGC', '科技'],
    '新能源': ['新能源', '光伏', '锂电'],
    '光伏': ['光伏', '新能源'],
    '锂电': ['锂电', '新能源', '电池'],
    '新能源车': ['新能源车', '新能源'],
    '白酒': ['白酒', '消费', '食品饮料'],
    '消费': ['消费', '食品饮料', '内需'],
    '医药': ['医药', '创新药', '生物医药'],
    '港股科技': ['港股科技', '港股互联网', '恒生科技', 'QDII科技'],
    '黄金': ['黄金', '贵金属'],
    '有色金属': ['有色金属', '铜铝', '大宗商品'],
    '铁矿石': ['铁矿石', '钢铁', '有色金属', '大宗商品'],
    '煤炭': ['煤炭', '能源', '大宗商品'],
    '天然气': ['天然气', '能源', '油气'],
    '农产品': ['农产品', '大宗商品', '消费'],
    '原油': ['原油', '能源', '油气'],
    '军工': ['军工', '国防', '航天'],
    '红利': ['红利', '高股息', '低波动'],
    '债券': ['债券', '固收', '纯债'],
    '宽基': ['宽基', '沪深300', '中证500'],
}

CATEGORY_ICONS = {
    'technology': '🤖', 'geopolitics': '🌍', 'monetary': '🏦',
    'policy': '📜', 'commodity': '🛢️', 'market': '📊',
}

_ANALYST_HINT_WORDS = ['分析师', '首席', '基金经理', '策略', '研报', '观点', '解读', '看多', '看空', '建议']


def _safe_read_json(path):
    try:
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _clean_text(text, max_len=140):
    t = re.sub(r'\s+', ' ', str(text or '')).strip()
    return t[:max_len]


def _extract_analyst_views(max_items=16):
    """从 analysis_cache + sentiment_cache 抽取热门分析师观点"""
    views = []

    analysis_cache = _safe_read_json(ANALYSIS_CACHE_PATH) or {}
    for sec in (analysis_cache.get('kol_sections') or [])[:8]:
        target = _clean_text(sec.get('target', ''), 32)
        kol = _clean_text(sec.get('kol', ''), 180)
        if not (target and kol):
            continue
        views.append({
            'target': target,
            'text': f"{target}: {kol}",
            'likes': 100000,
            'source': 'KOL分析缓存',
        })

    sentiment_cache = _safe_read_json(SENTIMENT_CACHE_PATH) or {}
    for item in (sentiment_cache.get('items') or []):
        title = _clean_text(item.get('title', ''), 90)
        summary = _clean_text(item.get('summary', ''), 110)
        creator_type = _clean_text(item.get('creator_type', ''), 24)
        text_join = f"{title} {summary}"
        if not title:
            continue
        if creator_type not in ['财经频道', '财经资讯平台', '视频社区', '微博热搜', '社交热搜']:
            continue
        if not any(k in text_join for k in _ANALYST_HINT_WORDS):
            continue
        likes = int(item.get('likes') or 0)
        if likes < 1000:
            continue
        views.append({
            'target': '',
            'text': text_join,
            'likes': likes,
            'source': item.get('platform', '舆情源'),
        })

    # 去重 + 按热度排序
    dedup = {}
    for v in views:
        key = _clean_text(v.get('text', ''), 120)
        if not key:
            continue
        old = dedup.get(key)
        if not old or int(v.get('likes', 0)) > int(old.get('likes', 0)):
            dedup[key] = v
    out = sorted(dedup.values(), key=lambda x: int(x.get('likes', 0)), reverse=True)
    return out[:max_items]


def _analyst_snippet(views, keywords, limit=2):
    if not views:
        return ''
    matched = []
    for v in views:
        txt = (v.get('text') or '').lower()
        if any(k.lower() in txt for k in keywords):
            matched.append(v)
    if not matched:
        return ''
    top = matched[:limit]
    pieces = [f"{_clean_text(v.get('text', ''), 60)}（{v.get('source', '舆情源')}）" for v in top]
    return '；'.join(pieces)


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_http(url, timeout=15):
    """GET request with timeout and error handling"""
    req = Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) fund-assistant/2.0',
        'Accept': 'application/json, text/xml, */*',
    })
    try:
        with urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"  [WARN] fetch failed: {url[:80]}... - {e}", file=sys.stderr)
        return None


# ==================== 新闻源抓取 ====================

def fetch_sina_news():
    """新浪财经滚动快讯 (高信噪比, 中文)"""
    items = []
    raw = fetch_http('https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=40&page=1')
    if not raw:
        return items
    try:
        data = json.loads(raw)
        for item in (data.get('result', {}).get('data', []) or []):
            title = (item.get('title') or '').strip()
            if title and len(title) > 8:
                items.append({'title': title, 'source': '新浪财经', 'time': item.get('ctime', '')})
    except Exception as e:
        print(f"  [WARN] sina parse: {e}", file=sys.stderr)
    return items


def fetch_eastmoney_news():
    """东方财富7×24快讯 (高信噪比, 中文)"""
    items = []
    raw = fetch_http('https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?type=0&client=web&maxNewsId=0&pageSize=40&column=102')
    if not raw:
        return items
    try:
        data = json.loads(raw)
        for item in (data.get('data', {}).get('list', []) or []):
            title = (item.get('title') or '').strip()
            content = (item.get('content') or '').strip()
            text = title or content[:100]
            if text and len(text) > 6:
                items.append({'title': text, 'source': '东方财富', 'time': item.get('showTime', '')})
    except Exception as e:
        print(f"  [WARN] eastmoney parse: {e}", file=sys.stderr)
    return items


def fetch_cls_news():
    """财联社快讯 (电报, 机构级信噪比)"""
    items = []
    raw = fetch_http('https://www.cls.cn/nodeapi/updateTelegraphList?app=CailianpressWeb&os=web&sv=8.4.6&rn=30')
    if not raw:
        return items
    try:
        data = json.loads(raw)
        for item in (data.get('data', {}).get('roll_data', []) or []):
            title = (item.get('title') or item.get('content', '')[:100]).strip()
            title = re.sub(r'<[^>]+>', '', title)  # strip HTML
            if title and len(title) > 6:
                ctime = item.get('ctime', 0)
                time_str = datetime.fromtimestamp(ctime, tz=timezone(timedelta(hours=8))).isoformat() if ctime else ''
                items.append({'title': title, 'source': '财联社', 'time': time_str})
    except Exception as e:
        print(f"  [WARN] cls parse: {e}", file=sys.stderr)
    return items


def fetch_rss_bbc():
    """BBC Business RSS (国际视角)"""
    import xml.etree.ElementTree as ET
    items = []
    raw = fetch_http('https://feeds.bbci.co.uk/news/business/rss.xml')
    if not raw:
        return items
    try:
        root = ET.fromstring(raw)
        for item in root.findall('.//item')[:15]:
            title = (item.findtext('title') or '').strip()
            if title:
                items.append({'title': title, 'source': 'BBC', 'time': item.findtext('pubDate', '')})
    except Exception as e:
        print(f"  [WARN] BBC RSS: {e}", file=sys.stderr)
    return items


def fetch_rss_reuters():
    """Reuters World/Business News via Google News RSS (地缘政治核心源)"""
    import xml.etree.ElementTree as ET
    items = []
    # Reuters自有RSS已关闭，改用Google News搜索Reuters来源
    rss_urls = [
        'https://news.google.com/rss/search?q=site:reuters.com+when:1d&hl=en&gl=US&ceid=US:en',
        'https://news.google.com/rss/search?q=geopolitics+OR+sanctions+OR+OPEC+OR+Iran+OR+tariff+when:1d&hl=en&gl=US&ceid=US:en',
    ]
    for url in rss_urls:
        raw = fetch_http(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
            for item in root.findall('.//item')[:15]:
                title = (item.findtext('title') or '').strip()
                if title:
                    items.append({'title': title, 'source': 'Reuters/Google', 'time': item.findtext('pubDate', '')})
        except Exception as e:
            print(f"  [WARN] Reuters/Google RSS: {e}", file=sys.stderr)
    # 去重
    seen = set()
    unique = []
    for it in items:
        if it['title'] not in seen:
            seen.add(it['title'])
            unique.append(it)
    return unique[:20]


def fetch_rss_aljazeera():
    """Al Jazeera RSS (中东/非洲/地缘视角)"""
    import xml.etree.ElementTree as ET
    items = []
    raw = fetch_http('https://www.aljazeera.com/xml/rss/all.xml')
    if not raw:
        return items
    try:
        root = ET.fromstring(raw)
        for item in root.findall('.//item')[:12]:
            title = (item.findtext('title') or '').strip()
            if title:
                items.append({'title': title, 'source': 'AlJazeera', 'time': item.findtext('pubDate', '')})
    except Exception as e:
        print(f"  [WARN] AlJazeera RSS: {e}", file=sys.stderr)
    return items


def fetch_rss_ft():
    """Financial Times RSS (国际财经+地缘)"""
    import xml.etree.ElementTree as ET
    items = []
    rss_urls = [
        'https://www.ft.com/rss/home',
        'https://www.ft.com/world?format=rss',
    ]
    for url in rss_urls:
        raw = fetch_http(url)
        if not raw:
            continue
        try:
            root = ET.fromstring(raw)
            for item in root.findall('.//item')[:10]:
                title = (item.findtext('title') or '').strip()
                if title:
                    items.append({'title': title, 'source': 'FT', 'time': item.findtext('pubDate', '')})
            if items:
                break
        except Exception as e:
            print(f"  [WARN] FT RSS: {e}", file=sys.stderr)
    return items


def fetch_guancha_news():
    """观察者网国际新闻 (中文地缘视角)"""
    items = []
    raw = fetch_http('https://www.guancha.cn/internationalNews')
    if not raw:
        return items
    try:
        # 简单提取标题 (HTML解析)
        titles = re.findall(r'<h4[^>]*>\s*<a[^>]*>([^<]+)</a>\s*</h4>', raw)
        for title in titles[:15]:
            title = title.strip()
            if title and len(title) > 6:
                items.append({'title': title, 'source': '观察者网', 'time': ''})
    except Exception as e:
        print(f"  [WARN] 观察者网: {e}", file=sys.stderr)
    return items


# ==================== 雪球实时热词抓取 ====================

# 股票名 → 板块映射 (用于把雪球热股映射到基金板块体系)
_XQ_STOCK_SECTOR_MAP = {
    # 关键词 → 板块列表 (按股票名/行业名模糊匹配)
    '宁德': ['锂电', '新能源'], '比亚迪': ['新能源车', '新能源'], '特斯拉': ['新能源车', '新能源'],
    '隆基': ['光伏', '新能源'], '通威': ['光伏', '新能源'], '阳光电源': ['光伏', '新能源'],
    '贵州茅台': ['白酒', '消费'], '五粮液': ['白酒', '消费'], '泸州老窖': ['白酒', '消费'], '茅台': ['白酒', '消费'],
    '中芯': ['半导体', 'AI'], '韦尔': ['半导体', 'AI'], '北方华创': ['半导体', 'AI'], '海光': ['半导体', 'AI'],
    '紫光': ['半导体', 'AI'], '中微': ['半导体', 'AI'], '寒武纪': ['AI', '半导体'],
    '腾讯': ['港股科技', 'AI'], '阿里': ['港股科技', 'AI'], '美团': ['港股科技', '消费'], '小米': ['港股科技', '消费'],
    '字节': ['AI', '港股科技'], '百度': ['AI', '港股科技'],
    '药明': ['医药', '创新药'], '恒瑞': ['医药', '创新药'], '迈瑞': ['医药', '医疗器械'],
    '中国中免': ['消费'], '海天': ['消费', '食品饮料'],
    '紫金矿业': ['有色金属', '黄金'], '山东黄金': ['黄金', '有色金属'], '中金黄金': ['黄金'],
    '洛阳钼业': ['有色金属'], '江西铜业': ['有色金属'],
    '中国石油': ['能源', '原油'], '中国石化': ['能源', '原油'], '中国海油': ['能源', '原油'],
    '中国神华': ['能源', '煤炭'],
    '中航': ['军工'], '航天': ['军工'], '北方导航': ['军工'],
    '招商银行': ['红利', '金融'], '工商银行': ['红利', '金融'], '建设银行': ['红利', '金融'],
    '长江电力': ['红利', '电力'], '中国移动': ['红利', '通信'],
    '科大讯飞': ['AI', '科技'], '浪潮': ['AI', '算力'], '中际旭创': ['AI', '算力'], '光模块': ['AI', '算力'],
    '机器人': ['AI', '机器人'], '汇川': ['机器人', '制造'], '绿的谐波': ['机器人'],
    '英伟达': ['AI', '半导体', '算力'], 'NVIDIA': ['AI', '半导体', '算力'],
    'DeepSeek': ['AI', '大模型'], 'GPT': ['AI', '大模型'], 'AI': ['AI', '科技'],
}

# 话题关键词 → 板块映射
_XQ_TOPIC_SECTOR_MAP = {
    '人工智能': ['AI', '科技'], '芯片': ['半导体', 'AI'], '半导体': ['半导体'], '算力': ['AI', '算力'],
    '大模型': ['AI', '大模型'], '机器人': ['AI', '机器人'], '智能驾驶': ['新能源车', 'AI'],
    '光伏': ['光伏', '新能源'], '新能源': ['新能源'], '锂电': ['锂电', '新能源'], '储能': ['新能源', '储能'],
    '白酒': ['白酒', '消费'], '消费': ['消费'], '食品': ['消费', '食品饮料'],
    '医药': ['医药'], '创新药': ['医药', '创新药'], '中药': ['医药'],
    '军工': ['军工'], '国防': ['军工'], '航天': ['军工'],
    '黄金': ['黄金'], '铜': ['有色金属'], '有色': ['有色金属'], '稀土': ['有色金属'],
    '原油': ['能源', '原油'], '石油': ['能源', '原油'], '煤炭': ['能源'],
    '港股': ['港股科技'], '恒生': ['港股科技'], '科技': ['科技', 'AI'],
    '银行': ['红利', '金融'], '红利': ['红利'], '高股息': ['红利'],
    '债券': ['债券'], '利率': ['债券', '金融'],
    '地产': ['地产'], '房地产': ['地产'], '基建': ['基建'],
    '光模块': ['AI', '算力'], '数据中心': ['AI', '算力'], '云计算': ['AI', '科技'],
    'ETF': ['宽基'], '沪深300': ['宽基'], '中证500': ['宽基'],
}


def _get_xueqiu_opener():
    """创建带cookie的请求器(雪球API需要先获取cookie)"""
    cj = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cj), HTTPSHandler(context=_ssl_ctx()))
    opener.addheaders = [
        ('User-Agent', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
        ('Accept', 'application/json, text/plain, */*'),
        ('Origin', 'https://xueqiu.com'),
        ('Referer', 'https://xueqiu.com/'),
    ]
    if XUEQIU_COOKIE:
        opener.addheaders.append(('Cookie', XUEQIU_COOKIE))
    # 先访问主页获取cookie
    try:
        resp = opener.open(Request('https://xueqiu.com/', headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        }), timeout=10)
        resp.read()
    except Exception as e:
        print(f"  [WARN] 雪球cookie获取失败: {e}", file=sys.stderr)
    return opener


def fetch_xueqiu_hot_stocks(opener=None):
    """雪球热股榜 → [{name, code, percent, heat, current}]"""
    if not opener:
        opener = _get_xueqiu_opener()
    items = []
    # 热度排行 (关注度排行)
    urls = [
        'https://stock.xueqiu.com/v5/stock/hot_stock/list.json?size=30&_type=10&type=10',
        'https://stock.xueqiu.com/v5/stock/hot_stock/list.json?size=30&_type=12&type=12',
    ]
    for url in urls:
        try:
            resp = opener.open(Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Referer': 'https://xueqiu.com/',
            }), timeout=12)
            body = resp.read().decode('utf-8', errors='replace')
            data = json.loads(body)
            for stock in (data.get('data', {}).get('items', []) or []):
                name = stock.get('name', '')
                code = stock.get('code', stock.get('symbol', ''))
                percent = stock.get('percent', stock.get('current_year_percent', 0)) or 0
                value = stock.get('value', stock.get('current', 0)) or 0
                if name:
                    items.append({
                        'name': name,
                        'code': str(code),
                        'percent': round(float(percent), 2) if percent else 0,
                        'heat': int(value) if value else 0,
                    })
        except Exception as e:
            print(f"  [WARN] 雪球热股: {e}", file=sys.stderr)
    # 去重
    seen = set()
    unique = []
    for it in items:
        if it['name'] not in seen:
            seen.add(it['name'])
            unique.append(it)
    return unique[:30]


def fetch_xueqiu_hot_topics(opener=None):
    """雪球热帖/热议话题 → [{text, retweet_count, reply_count, like_count}]"""
    if not opener:
        opener = _get_xueqiu_opener()
    items = []
    urls = [
        'https://xueqiu.com/statuses/hot/listV2.json?since_id=-1&max_id=-1&size=20',
    ]
    for url in urls:
        try:
            resp = opener.open(Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Referer': 'https://xueqiu.com/',
            }), timeout=12)
            body = resp.read().decode('utf-8', errors='replace')
            data = json.loads(body)
            for item in (data.get('items', []) or []):
                og = item.get('original_status', item)
                text = (og.get('text') or og.get('title') or og.get('description') or '')
                text = re.sub(r'<[^>]+>', '', text).strip()[:200]
                if not text or len(text) < 6:
                    continue
                items.append({
                    'text': text,
                    'retweet_count': og.get('retweet_count', 0) or 0,
                    'reply_count': og.get('reply_count', 0) or 0,
                    'like_count': og.get('like_count', 0) or 0,
                })
        except Exception as e:
            print(f"  [WARN] 雪球热帖: {e}", file=sys.stderr)
    return items[:20]


def _map_stock_to_sectors(name):
    """将股票名映射到板块列表"""
    sectors = set()
    for kw, sector_list in _XQ_STOCK_SECTOR_MAP.items():
        if kw in name:
            sectors.update(sector_list)
    return list(sectors) if sectors else ['其他']


def _extract_topic_sectors(text):
    """从话题文本提取相关板块"""
    sectors = set()
    for kw, sector_list in _XQ_TOPIC_SECTOR_MAP.items():
        if kw in text:
            sectors.update(sector_list)
    # 也尝试用股票名映射
    for kw, sector_list in _XQ_STOCK_SECTOR_MAP.items():
        if kw in text:
            sectors.update(sector_list)
    return list(sectors)


def process_xueqiu_to_hotwords(hot_stocks, hot_topics):
    """
    将雪球热股+热帖数据转换为标准热词格式:
    [{word, heat, trend, sources:['雪球'], relatedSectors:[...]}]
    """
    hotwords = []

    # 1. 热股 → 热词
    if hot_stocks:
        max_heat = max(s.get('heat', 1) for s in hot_stocks) or 1
        for s in hot_stocks:
            name = s['name']
            sectors = _map_stock_to_sectors(name)
            if '其他' in sectors and len(sectors) == 1:
                continue  # 跳过无法映射的
            raw_heat = s.get('heat', 0)
            normalized_heat = int(5000 + (raw_heat / max_heat) * 5000) if max_heat > 0 else 5000
            trend = 'up' if s.get('percent', 0) > 1 else ('down' if s.get('percent', 0) < -1 else 'stable')
            hotwords.append({
                'word': name,
                'heat': normalized_heat,
                'trend': trend,
                'sources': ['雪球'],
                'relatedSectors': sectors,
                'type': 'stock',
                'percent': s.get('percent', 0),
            })

    # 2. 热帖 → 话题热词 (提取关键词)
    topic_sector_count = {}  # 板块 → 出现次数 + 热度
    if hot_topics:
        for t in hot_topics:
            text = t.get('text', '')
            sectors = _extract_topic_sectors(text)
            engagement = (t.get('retweet_count', 0) + t.get('reply_count', 0) + t.get('like_count', 0))
            for s in sectors:
                if s not in topic_sector_count:
                    topic_sector_count[s] = {'count': 0, 'engagement': 0, 'texts': []}
                topic_sector_count[s]['count'] += 1
                topic_sector_count[s]['engagement'] += engagement
                if len(topic_sector_count[s]['texts']) < 2:
                    # 截取标题级摘要
                    short = text[:30].replace('\n', ' ')
                    topic_sector_count[s]['texts'].append(short)

    # 把话题聚合为板块热词
    for sector, info in topic_sector_count.items():
        if info['count'] < 1:
            continue
        heat = min(10000, 3000 + info['count'] * 1500 + info['engagement'] // 100)
        # 检查是否已有同板块的热股词
        exists = any(sector in hw['relatedSectors'] for hw in hotwords)
        if exists:
            # 追加到已有同板块热词的热度
            for hw in hotwords:
                if sector in hw['relatedSectors']:
                    hw['heat'] = min(10000, hw['heat'] + info['count'] * 300)
                    break
        else:
            hotwords.append({
                'word': f"{sector}(雪球热议)",
                'heat': heat,
                'trend': 'up' if info['count'] >= 3 else 'stable',
                'sources': ['雪球'],
                'relatedSectors': [sector],
                'type': 'topic',
            })

    # 排序 & 去重
    hotwords.sort(key=lambda x: x.get('heat', 0), reverse=True)
    return hotwords[:25]


def fetch_xueqiu_hotwords():
    """完整雪球热词抓取流程: cookie → 热股 + 热帖 → 标准热词格式"""
    print("  🔥 雪球热词抓取...")
    try:
        opener = _get_xueqiu_opener()
        stocks = fetch_xueqiu_hot_stocks(opener)
        print(f"    热股: {len(stocks)} 条")
        topics = fetch_xueqiu_hot_topics(opener)
        print(f"    热帖: {len(topics)} 条")
        hotwords = process_xueqiu_to_hotwords(stocks, topics)
        print(f"    热词: {len(hotwords)} 条")
        return {
            'hotwords': hotwords,
            'hot_stocks': stocks[:15],  # 保留原始数据供前端直接展示
            'hot_topics': [{'text': t['text'][:100], 'engagement': t.get('retweet_count',0)+t.get('reply_count',0)+t.get('like_count',0)} for t in topics[:10]],
            'fetched_at': datetime.now(timezone(timedelta(hours=8))).isoformat(),
        }
    except Exception as e:
        print(f"  ❌ 雪球热词抓取失败: {e}", file=sys.stderr)
        return None


def is_valid_xueqiu_data(data):
    """雪球数据是否有效（至少有热词或热股）"""
    if not isinstance(data, dict):
        return False
    hotwords = data.get('hotwords') or []
    hot_stocks = data.get('hot_stocks') or []
    return len(hotwords) > 0 or len(hot_stocks) > 0


# ==================== LLM 结构化提取 ====================

def call_llm(news_items):
    """调用大模型将新闻列表 → 结构化事件 + 热度标签"""
    if not API_KEY:
        print("[ERROR] AI_API_KEY not set, cannot call LLM", file=sys.stderr)
        return None

    # 构建新闻文本 (去重, 限制长度)
    seen = set()
    unique = []
    for n in news_items:
        key = re.sub(r'\W', '', n['title'])[:30]
        if key not in seen:
            seen.add(key)
            unique.append(n)
    news_text = '\n'.join([
        f"{i+1}. [{n['source']}] {n['title']}"
        for i, n in enumerate(unique[:50])
    ])

    tags_str = '、'.join(MARKET_TAGS)

    system_prompt = f"""你是一个顶级量化金融分析师，擅长从财经新闻中提取投资信号。

## 任务一：提取高影响力政经事件
从新闻中**仅提取政治、经济、金融政策、地缘政治、央行货币政策、财政政策、贸易政策、产业政策、大宗商品供需变化**等政经类事件。
严格过滤掉以下无关内容：娱乐八卦、体育赛事、社会新闻、天气、生活方式、明星、综艺等。
提取影响力≥3星(满5星)的重大政经事件，合并同类新闻。
每个事件必须标注影响的行业板块(正面/负面)。最多输出12条事件。

**重要：确保事件覆盖尽可能多的行业板块。** 除了AI/科技、债券等热门板块以外，必须特别关注以下板块的相关新闻并提取事件：
- **地缘政治(最高优先级!)**: 美伊关系、俄乌冲突、中美博弈、中东局势、红海航运、非洲资源国政策(锂矿/钴矿出口禁令)、OPEC减产、关税战、制裁措施等。地缘事件对油气、有色金属、黄金、军工板块影响极大，必须提取！
- **大宗商品**: 原油/油气价格变动、OPEC决策、铜铝等有色金属涨跌、黄金白银走势、锂矿/稀土供应链、铁矿石价格/政策变化、天然气/煤炭/焦煤价格波动
- **农产品**: 大豆/豆粕、玉米、小麦、棉花、棕榈油、橡胶、白糖、生猪等期货品种的价格异动和相关政策(进出口关税/配额/补贴/天气灾害)
- **能源与资源**: 能源政策、矿产资源供需、碳排放政策、非洲/南美/澳大利亚等资源国出口限制、铁矿石/煤炭/钢铁产业链
- **消费与内需**: 社零数据、消费政策、白酒/食品行业动态
- **医药健康**: 医药政策、集采、创新药审批
- **军工国防**: 军费预算、装备采购、地缘冲突驱动的军工需求
如果新闻中有涉及有色金属(铜、铝、锌、稀土、锂等)、铁矿石/钢铁、原油/油气、天然气/煤炭、黄金白银、农产品(大豆/玉米/小麦/棉花/生猪等)等大宗商品的内容，务必单独提取为事件。
如果新闻中有涉及澳大利亚/巴西等矿产出口国的铁矿石、煤炭、矿产资源政策的内容，务必单独提取为事件并标注影响板块(如钢铁、有色金属、大宗商品等)。
如果新闻中有涉及国际地缘冲突(美伊、俄乌、中美、红海等)的内容，务必单独提取为事件并标注影响的板块(如油气、军工、黄金等)。

**极其重要的板块影响判定规则（必须遵守！违反将产生严重错误！）：**
- 伊朗/以色列/中东战争/导弹/袭击 → sectors_positive 必须包含 ["原油", "黄金", "军工"]，绝对不能放在 sectors_negative
- 地缘冲突(战争/袭击/制裁/封锁) → 原油/油气/能源 应放入 sectors_positive（供应中断=价格上涨=利好能源板块）
- 地缘冲突 → 黄金/贵金属/军工 应放入 sectors_positive（避险需求+军费增加）
- 地缘冲突 → 消费/贸易 可放入 sectors_negative（经济不确定性）
- 战争/制裁导致石油供应紧张 = 油价上涨 = 能源公司受益 = sectors_positive: ["原油", "油气", "能源"]
- 切记：不要把"油价上涨导致的能源成本增加"理解为对能源板块不利。能源板块就是卖油的，油价涨=利好能源。
- ⚠️ 禁止将 "原油"/"油气"/"能源" 放入战争/地缘事件的 sectors_negative！

sectors_positive 和 sectors_negative 字段应使用以下标准板块名：
AI/科技、半导体、算力、AIGC、新能源、光伏、锂电、新能源车、消费、食品饮料、白酒、医药、创新药、
黄金、贵金属、有色金属、铜铝、铁矿石、钢铁、煤炭、天然气、农产品、大宗商品、能源、原油、油气、
军工、国防、红利、高股息、债券、固收、金融、银行、券商、
港股科技、港股互联网、恒生科技、地产、基建、宽基

fund_keywords 字段应包含能匹配到基金名称/类型的关键词，如: 人工智能、AI、算力、黄金、有色金属、油气、原油、天然气、煤炭、铁矿石、钢铁、农产品、大豆、新能源、半导体、军工、消费、医药、红利等。

## 任务二：生成市场标签热度
基于所有新闻的综合语义分析，为以下21个市场标签评估热度和情绪：
{tags_str}
- temperature: 0-100，反映当前市场关注度 (50=正常, 80+=高热, 20-=冰冷)
- sentiment: -1到+1，反映利好/利空方向

## 严格输出格式 (纯JSON，不要markdown/注释/多余文字)：
{{
  "events": [
    {{
      "title": "一句话事件摘要(15字内)",
      "category": "technology|geopolitics|monetary|policy|commodity|market",
      "concepts": ["标签1", "标签2"],
      "sentiment": 0.8,
      "impact": 4,
      "sectors_positive": ["AI/科技", "半导体"],
      "sectors_negative": [],
      "fund_keywords": ["人工智能", "AI", "算力"],
      "reason": "30字内简析",
      "advice": "15字内操作建议"
    }}
  ],
  "heatmap": [
    {{ "tag": "人工智能", "temperature": 85, "sentiment": 0.8 }}
  ],
  "outlook_summary": "50字内市场总览"
}}"""

    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请分析以下{len(unique)}条最新新闻，只提取政治经济金融相关事件，忽略娱乐体育社会等无关新闻：\n\n{news_text}"}
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
    })

    req = Request(
        f"{API_BASE}/chat/completions",
        data=payload.encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {API_KEY}',
        }
    )

    for attempt in range(2):
        try:
            with urlopen(req, timeout=180, context=_ssl_ctx()) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                content = result['choices'][0]['message']['content']
                # strip markdown code fences
                content = re.sub(r'```json\s*', '', content)
                content = re.sub(r'```\s*', '', content)
                return json.loads(content.strip())
        except Exception as e:
            print(f"[ERROR] LLM call failed (attempt {attempt+1}/2): {e}", file=sys.stderr)
            if attempt == 0:
                import time as _t; _t.sleep(5)
    return None


# ==================== 数据组装 ====================

def load_previous():
    """加载上次数据, 用于计算趋势"""
    try:
        with open(OUTPUT_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return None


def compute_trend(current_temp, prev_data, tag):
    """对比上次温度, 判断趋势方向"""
    if not prev_data or 'heatmap' not in prev_data:
        return 'stable'
    prev = next((h for h in prev_data['heatmap'] if h['tag'] == tag), None)
    if not prev:
        return 'new'
    diff = current_temp - prev.get('temperature', 50)
    if diff > 5:
        return 'up'
    if diff < -5:
        return 'down'
    return 'stable'


def _fetch_realtime_market_pct():
    """获取关键 ETF/期货 实时涨跌幅，用于修正热力图趋势方向
    返回 {tag: pct} 映射，tag 与 MARKET_TAGS 对齐"""
    # ETF/指数代码 → 板块tag映射
    etf_map = {
        '1.512400': '有色金属',   # 有色金属ETF
        '1.518880': '黄金',       # 黄金ETF
        '0.161226': '白银',       # 白银LOF (归入贵金属)
        '1.516380': '半导体',     # 半导体ETF
        '0.159819': '人工智能',   # 人工智能ETF
        '0.159792': '军工',       # 军工ETF
        '1.512690': '白酒',       # 白酒LOF
        '1.510300': '宽基',       # 沪深300ETF
        '0.159697': '原油',       # 油气ETF (原油期货收盘后可用ETF)
        '0.159930': '新能源',     # 能源ETF
        '1.515880': '消费',       # 消费ETF
        '0.159828': '医药',       # 医药ETF
        '1.515070': 'AI算力',     # AI算力ETF
        '1.562500': '机器人',     # 机器人ETF
        '0.159857': '光伏',       # 光伏ETF
        '0.159840': '锂电',       # 锂电池ETF
        '1.515030': '新能源车',   # 新能源车ETF
        '1.513180': '港股科技',   # 恒生科技ETF
        '1.515080': '红利',       # 红利ETF
        '1.511260': '债券',       # 十年国债ETF
    }
    # 期货主力 → tag映射
    futures_map = {
        '113.aum': '黄金',
        '113.agm': '白银',
        '113.fum': '能源',
        '113.cum': '有色金属',
        '113.im':  '钢铁',
        '114.jmm': '煤炭',
        '114.mm':  '农产品',
    }
    all_codes = list(etf_map.keys()) + list(futures_map.keys())
    secids = ','.join(all_codes)
    url = f'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f12,f14&secids={secids}'
    result = {}
    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=8, context=_ssl_ctx()) as resp:
            data = json.loads(resp.read().decode())
        items = (data.get('data') or {}).get('diff') or []
        # 按原始secid顺序匹配
        for item in items:
            code_num = str(item.get('f12', ''))
            pct = item.get('f3')
            if pct is None:
                continue
            # 查找 code_num 对应的 secid (可能是 1.xxx 或 0.xxx 或 113.xxx)
            for secid, tag in {**etf_map, **futures_map}.items():
                if secid.split('.')[-1] == code_num or secid.endswith('.' + code_num):
                    # ETF 优先于期货
                    if tag not in result or secid in etf_map:
                        result[tag] = float(pct)
                    break
    except Exception as e:
        print(f'  [WARN] 实时行情获取失败: {e}')
    return result


def enrich_event(evt, idx, now):
    """补全事件字段, 计算impact/confidence, 生成id"""
    # 从concepts推导 sectors_positive/negative (如果LLM没返回)
    if not evt.get('sectors_positive'):
        sectors = []
        for concept in evt.get('concepts', []):
            sectors.extend(TAG_TO_SECTORS.get(concept, []))
        evt['sectors_positive'] = list(dict.fromkeys(sectors))[:5]  # dedup

    if 'sectors_negative' not in evt:
        evt['sectors_negative'] = []

    # ============ 板块归属强制修正 ============
    # 1) 地缘冲突事件: 能源/原油/油气 必须在 sectors_positive（LLM常犯错）
    _GEO_KEYWORDS = ['伊朗', '以色列', '中东', '战争', '袭击', '导弹',
                      '军事', '冲突', '制裁', '封锁', '红海', '胡塞',
                      '俄乌', '乌克兰', '轰炸', '战机', '伊以',
                      '霍尔木兹', '美军', '空袭', '入侵']
    _ENERGY_SECTORS = {'能源', '原油', '油气', '石油'}
    _GEO_POSITIVE_SECTORS = _ENERGY_SECTORS | {'黄金', '贵金属', '军工', '国防'}
    title = evt.get('title', '')
    is_geo = any(kw in title for kw in _GEO_KEYWORDS) or evt.get('category') == 'geopolitics'
    if is_geo:
        neg = evt.get('sectors_negative', [])
        pos = evt.get('sectors_positive', [])
        # 把被误放到negative的能源/黄金/军工移到positive
        moved = [s for s in neg if s in _GEO_POSITIVE_SECTORS]
        if moved:
            evt['sectors_negative'] = [s for s in neg if s not in _GEO_POSITIVE_SECTORS]
            evt['sectors_positive'] = list(dict.fromkeys(pos + moved))
        # 确保能源相关sector一定出现在positive
        if not any(s in _ENERGY_SECTORS for s in evt['sectors_positive']):
            evt['sectors_positive'].append('原油')

    # 2) 板块名规范化 → 统一为 MARKET_TAGS 中的标准名
    _SECTOR_NORMALIZE = {
        '能源': '原油', '油气': '原油', '石油': '原油',
        '贵金属': '黄金', '铜铝': '有色金属',
        '国防': '军工', '大宗商品': '有色金属',
        'AI/科技': '人工智能', '科技': '人工智能',
        '算力': 'AI算力', 'AIGC': '大模型',
        '芯片': '半导体', '食品饮料': '消费',
        '创新药': '医药', '高股息': '红利',
        '固收': '债券', '金融': '宽基', '银行': '宽基',
        '港股互联网': '港股科技', '恒生科技': '港股科技',
    }
    for field in ('sectors_positive', 'sectors_negative'):
        raw = evt.get(field, [])
        normalized = []
        for s in raw:
            ns = _SECTOR_NORMALIZE.get(s, s)
            if ns not in normalized:
                normalized.append(ns)
        evt[field] = normalized

    # 3) 同一板块不能同时出现在positive和negative，positive优先
    pos_set = set(evt.get('sectors_positive', []))
    evt['sectors_negative'] = [s for s in evt.get('sectors_negative', []) if s not in pos_set]

    # 计算 confidence (sentiment强度 + impact级别)
    sentiment = evt.get('sentiment', 0)
    impact_level = evt.get('impact', 3)
    confidence = min(1.0, max(0.3, abs(sentiment) * 0.5 + impact_level * 0.12))

    # 将 impact_level (1-5) 映射到实际 impact 值 (-20 to +20)
    impact_value = int(sentiment * impact_level * 4)
    impact_value = max(-20, min(20, impact_value))

    return {
        "id": f"evt_{now.strftime('%Y%m%d')}_{idx+1:03d}",
        "title": evt.get('title', ''),
        "category": evt.get('category', 'market'),
        "concepts": evt.get('concepts', []),
        "sentiment": round(sentiment, 2),
        "impact": impact_value,
        "confidence": round(confidence, 2),
        "sectors_positive": evt.get('sectors_positive', []),
        "sectors_negative": evt.get('sectors_negative', []),
        "fund_keywords": evt.get('fund_keywords', []),
        "reason": evt.get('reason', ''),
        "advice": evt.get('advice', ''),
        "source": "AI综合分析",
        "time": now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
    }


# 大宗商品/资源常驻事件模板 (LLM未覆盖时自动注入)
_COMMODITY_FALLBACKS = [
    {
        "key_sectors": {'黄金', '贵金属'},
        "template": {
            "title": "避险资产受青睐",
            "category": "commodity",
            "concepts": ["黄金"],
            "sentiment": 0.3,
            "impact": 3,
            "sectors_positive": ["黄金", "贵金属", "有色金属"],
            "sectors_negative": [],
            "fund_keywords": ["黄金", "贵金属", "避险", "白银", "有色金属"],
            "reason": "地缘不确定性+央行购金，黄金作为避险资产维持关注",
            "advice": "黄金ETF作底仓配置",
        },
    },
    {
        "key_sectors": {'有色金属', '铜铝', '大宗商品'},
        "template": {
            "title": "全球有色金属需求旺盛",
            "category": "commodity",
            "concepts": ["有色金属"],
            "sentiment": 0.3,
            "impact": 3,
            "sectors_positive": ["有色金属", "铜铝", "大宗商品", "资源"],
            "sectors_negative": [],
            "fund_keywords": ["有色金属", "铜", "铝", "资源", "矿业"],
            "reason": "新基建+新能源车带动铜铝需求，有色金属维持结构性行情",
            "advice": "有色金属ETF波段操作",
        },
    },
    {
        "key_sectors": {'能源', '原油', '油气'},
        "template": {
            "title": "国际油价波动加剧",
            "category": "commodity",
            "concepts": ["原油"],
            "sentiment": 0.1,
            "impact": 2,
            "sectors_positive": ["能源", "原油", "油气", "大宗商品"],
            "sectors_negative": [],
            "fund_keywords": ["原油", "油气", "石油", "天然气", "能源"],
            "reason": "OPEC减产预期+地缘冲突，油气价格波动信号",
            "advice": "油气ETF关注供给端变化",
        },
    },
]


def _ensure_commodity_events(events, now):
    """确保大宗商品核心板块始终有事件覆盖 (LLM可能遗漏)"""
    all_sectors = set()
    for e in events:
        all_sectors.update(e.get('sectors_positive', []))
        all_sectors.update(e.get('sectors_negative', []))

    added = 0
    for fb in _COMMODITY_FALLBACKS:
        if not fb['key_sectors'] & all_sectors:
            # 该板块未被任何动态事件覆盖 → 注入常驻事件
            idx = len(events)
            evt = dict(fb['template'])
            evt['id'] = f"evt_{now.strftime('%Y%m%d')}_base_{idx+1:03d}"
            evt['confidence'] = 0.6
            evt['source'] = "常驻基础事件"
            evt['is_template'] = True
            evt['impact'] = 1
            evt['time'] = now.strftime('%Y-%m-%dT%H:%M:%S+08:00')
            events.append(evt)
            added += 1
            print(f"  📌 补充常驻事件: {evt['title']} (动态事件未覆盖 {fb['key_sectors']})")

    if added:
        print(f"  共补充 {added} 个大宗商品常驻事件")
    return events


# 地缘政治常驻事件模板 (持续性地缘风险，即使LLM未单独提取也应追踪)
_GEOPOLITICAL_FALLBACKS = [
    {
        "key_keywords": ['伊朗', 'iran', '中东', '霍尔木兹', '红海', '胡塞', 'houthi'],
        "key_sectors": {'能源', '原油', '油气'},
        "template": {
            "title": "中东地缘局势紧张",
            "category": "geopolitics",
            "concepts": ["原油", "黄金"],
            "sentiment": -0.3,
            "impact": 3,
            "sectors_positive": ["原油", "能源", "油气", "大宗商品", "黄金", "贵金属"],
            "sectors_negative": ["航空", "消费"],
            "fund_keywords": ["原油", "油气", "石油", "天然气", "能源", "黄金", "避险"],
            "reason": "美伊关系紧张+红海航运受阻，推升油价和避险资产",
            "advice": "油气+黄金对冲配置",
        },
    },
    {
        'key_keywords': ['美俄', '乌克兰', '俄乌', 'russia', 'ukraine', 'nato', 'NATO'],
        "key_sectors": {'军工', '国防'},
        "template": {
            "title": "俄乌冲突与制裁影响延续",
            "category": "geopolitics",
            "concepts": ["军工", "原油"],
            "sentiment": -0.2,
            "impact": 3,
            "sectors_positive": ["军工", "国防", "能源", "黄金", "贵金属"],
            "sectors_negative": ["消费", "航空"],
            "fund_keywords": ["军工", "国防", "航天", "黄金", "原油", "能源"],
            "reason": "俄乌冲突持续，推升军工+能源需求，避险情绪受益黄金",
            "advice": "军工ETF+黄金底仓",
        },
    },
    {
        "key_keywords": ['美中', '中美', '制裁', '关税', 'tariff', 'sanction', '芯片禁令', '科技战'],
        "key_sectors": {'半导体', 'AI/科技'},
        "template": {
            "title": "中美科技博弈延续",
            "category": "geopolitics",
            "concepts": ["半导体", "AI算力"],
            "sentiment": -0.3,
            "impact": 3,
            "sectors_positive": ["半导体", "军工", "AI/科技"],
            "sectors_negative": ["消费", "贸易相关"],
            "fund_keywords": ["半导体", "芯片", "科技", "AI", "军工"],
            "reason": "中美科技脱钩加速，半导体国产替代+军工自主攻关受益",
            "advice": "半导体+军工国产替代主线",
        },
    },
    {
        'key_keywords': ['锂矿', '稀土', '出口禁', '矿产', '非洲', '智利', '刚果', 'lithium', 'rare earth', 'cobalt'],
        "key_sectors": {'有色金属', '新能源'},
        "template": {
            "title": "全球关键矿产供应链紧张",
            "category": "geopolitics",
            "concepts": ["有色金属", "锂电"],
            "sentiment": 0.3,
            "impact": 3,
            "sectors_positive": ["有色金属", "铜铝", "大宗商品", "锂电", "新能源"],
            "sectors_negative": [],
            "fund_keywords": ["有色金属", "铜", "铝", "锂", "稀土", "资源", "矿业", "新能源"],
            "reason": "非洲国家锂矿出口限制+全球稀土供应紧张，推升有色金属价格",
            "advice": "有色金属+锂电ETF关注供给端",
        },
    },
    {
        "key_keywords": ['OPEC', 'opec', '减产', '油价', '英伦特', '布伦特原油', 'oil price', 'crude'],
        "key_sectors": {'原油', '油气', '能源'},
        "template": {
            "title": "OPEC+产量政策影响油价",
            "category": "commodity",
            "concepts": ["原油"],
            "sentiment": 0.2,
            "impact": 3,
            "sectors_positive": ["原油", "能源", "油气", "大宗商品"],
            "sectors_negative": ["航空", "交通"],
            "fund_keywords": ["原油", "油气", "石油", "能源"],
            "reason": "OPEC+减产政策延续，油价中枢上移，能源股受益",
            "advice": "油气基金关注供给端变化",
        },
    },
]


def _ensure_geopolitical_events(events, all_news, now):
    """确保重大地缘事件始终被追踪(即使LLM未单独提取)"""
    # 汇总所有新闻标题文本用于关键词检测
    all_text = ' '.join(n.get('title', '') for n in all_news).lower()
    
    # 汇总已有事件覆盖的板块
    all_sectors = set()
    for e in events:
        all_sectors.update(e.get('sectors_positive', []))
        all_sectors.update(e.get('sectors_negative', []))
    
    added = 0
    for fb in _GEOPOLITICAL_FALLBACKS:
        # 检查该地缘主题是否已被动态事件完全覆盖(所有关键板块都有对应事件才跳过)
        uncovered = fb['key_sectors'] - all_sectors
        if not uncovered:
            continue
        
        # 检查新闻中是否有相关关键词(即使LLM没提取，新闻中有提及就补充)
        kw_found = any(kw.lower() in all_text for kw in fb['key_keywords'])
        
        if kw_found:
            idx = len(events)
            evt = dict(fb['template'])
            evt['id'] = f"evt_{now.strftime('%Y%m%d')}_geo_{idx+1:03d}"
            evt['confidence'] = 0.65
            evt['source'] = "地缘事件追踪"
            evt['is_template'] = True
            evt['impact'] = 1
            evt['time'] = now.strftime('%Y-%m-%dT%H:%M:%S+08:00')
            events.append(evt)
            added += 1
            print(f"  🌍 补充地缘事件: {evt['title']} (新闻中检测到关键词)")
    
    if added:
        print(f"  共补充 {added} 个地缘政治事件")
    return events


_KEY_EVENT_TEMPLATES = [
    {
        'name': '中东冲突',
        'keywords': ['中东', '伊朗', '以色列', '伊以', '霍尔木兹', '红海', 'houthi', 'iran', 'israel'],
        'title': '中东局势升级扰动市场',
        'category': 'geopolitics',
        'concepts': ['原油', '黄金', '军工'],
        'sectors_positive': ['原油', '油气', '能源', '黄金', '贵金属', '军工', '国防'],
        'sectors_negative': ['消费', '航空'],
        'fund_keywords': ['原油', '油气', '能源', '黄金', '军工'],
        'sentiment': -0.35,
        'impact': 4,
        'reason': '地缘冲突抬升避险与通胀预期，油气与黄金波动放大',
        'advice': '油气+黄金防御配置，避免追涨杀跌',
    },
    {
        'name': '伊朗高层突发',
        'keywords': ['伊朗领导人', '伊朗总统', '伊朗高层', '伊朗 领导人', 'tehran', 'assassinated', '死亡', '遇袭', '坠机'],
        'title': '伊朗高层突发事件引发避险交易',
        'category': 'geopolitics',
        'concepts': ['黄金', '原油'],
        'sectors_positive': ['黄金', '贵金属', '原油', '油气', '军工'],
        'sectors_negative': ['消费', '航空'],
        'fund_keywords': ['黄金', '原油', '油气', '军工', '避险'],
        'sentiment': -0.45,
        'impact': 4,
        'reason': '中东政治不确定性上升，风险资产风险偏好下降',
        'advice': '提高防御仓位，重点观察油价与金价共振',
    },
]


def _inject_key_events_with_analyst_views(events, all_news, analyst_views, now):
    """注入重点事件，并融合热门分析师实时观点"""
    all_text = ' '.join(n.get('title', '') for n in (all_news or [])).lower()
    existing_titles = {e.get('title', '') for e in events}
    added = 0

    for tpl in _KEY_EVENT_TEMPLATES:
        if not any(kw.lower() in all_text for kw in tpl['keywords']):
            continue

        # 已有相同主题则仅增强观点字段
        existing = next((e for e in events if tpl['title'] in (e.get('title') or '') or any(c in (e.get('concepts') or []) for c in tpl['concepts'])), None)
        analyst_note = _analyst_snippet(analyst_views, tpl['keywords'], limit=2)

        if existing:
            if analyst_note and '分析师观点' not in (existing.get('reason') or ''):
                existing['reason'] = f"{existing.get('reason', '')}；分析师观点：{analyst_note}".strip('；')
            if analyst_note and not existing.get('analyst_view'):
                existing['analyst_view'] = analyst_note
            continue

        idx = len(events)
        evt = {
            'id': f"evt_{now.strftime('%Y%m%d')}_key_{idx+1:03d}",
            'title': tpl['title'],
            'category': tpl['category'],
            'concepts': tpl['concepts'],
            'sentiment': round(tpl['sentiment'], 2),
            'impact': int(tpl['sentiment'] * tpl['impact'] * 4),
            'confidence': 0.78,
            'sectors_positive': tpl['sectors_positive'],
            'sectors_negative': tpl['sectors_negative'],
            'fund_keywords': tpl['fund_keywords'],
            'reason': tpl['reason'],
            'advice': tpl['advice'],
            'source': '重点事件追踪',
            'time': now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        }
        if analyst_note:
            evt['reason'] = f"{evt['reason']}；分析师观点：{analyst_note}"
            evt['advice'] = f"{evt['advice']}（参考热门分析师实时观点）"
            evt['analyst_view'] = analyst_note

        evt['is_template'] = True
        if evt['title'] not in existing_titles:
            events.append(evt)
            existing_titles.add(evt['title'])
            added += 1

    if added:
        print(f"  🧩 注入重点事件: {added} 条（含分析师观点融合）")
    return events


def _attach_analyst_views_to_events(events, analyst_views):
    """为事件补充分析师观点（即使不是重点事件）"""
    if not events or not analyst_views:
        return events

    for evt in events:
        if evt.get('analyst_view'):
            continue
        keywords = []
        title = (evt.get('title') or '').strip()
        if title:
            keywords.append(title)
        for c in (evt.get('concepts') or []):
            if c:
                keywords.append(str(c))
        for k in (evt.get('fund_keywords') or []):
            if k:
                keywords.append(str(k))

        # 关键词过少时，用类别兜底
        if not keywords and evt.get('category'):
            keywords.append(str(evt.get('category')))

        note = _analyst_snippet(analyst_views, keywords, limit=1)
        if note:
            evt['analyst_view'] = note
            if '分析师观点' not in (evt.get('reason') or ''):
                evt['reason'] = f"{evt.get('reason', '')}；分析师观点：{note}".strip('；')

    return events


def _normalize_event_text(text):
    return re.sub(r'\W+', '', str(text or '').lower())


def _carry_forward_event_times(events, prev_data, all_news):
    """使用持久化 first_seen 缓存跟踪事件首次出现时间。
    同一主题事件即使 LLM 生成不同标题，也能通过模糊匹配命中缓存。
    超过 90 分钟的非模板事件直接从输出中移除（后端侧过滤）。
    """
    if not events:
        return events

    now_dt = datetime.now(timezone(timedelta(hours=8)))
    now_iso = now_dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')
    ninety_min_ago = (now_dt - timedelta(minutes=90)).strftime('%Y-%m-%dT%H:%M:%S')

    # ---------- 加载持久化 first_seen 缓存 ----------
    first_seen = {}
    try:
        if os.path.exists(FIRST_SEEN_CACHE_PATH):
            with open(FIRST_SEEN_CACHE_PATH, 'r', encoding='utf-8') as f:
                first_seen = json.load(f)
    except Exception:
        first_seen = {}

    # 清理超过 48 小时的老条目
    cutoff_48h = (now_dt - timedelta(hours=48)).strftime('%Y-%m-%dT%H:%M:%S')
    first_seen = {k: v for k, v in first_seen.items() if v[:19] > cutoff_48h}

    # ---------- overlap coefficient (比 Dice 宽容长度差异) ----------
    def _bigram_sim(a, b):
        if len(a) < 2 or len(b) < 2:
            return 0.0
        ga = {a[i:i+2] for i in range(len(a)-1)}
        gb = {b[i:i+2] for i in range(len(b)-1)}
        inter = len(ga & gb)
        return inter / max(1, min(len(ga), len(gb)))

    def _strip_microseconds(t):
        """去掉微秒（防止前端 Date.parse 解析失败）"""
        if '.' in t:
            base, rest = t.split('.', 1)
            tz_part = ''
            for sep in ['+', '-', 'Z']:
                if sep in rest:
                    tz_part = sep + rest.split(sep, 1)[1]
                    break
            return base + tz_part
        return t

    # ---------- 为每个事件查找/写入 first_seen ----------
    result = []
    for evt in events:
        title_key = _normalize_event_text(evt.get('title'))
        if not title_key or len(title_key) < 3:
            result.append(evt)
            continue

        is_template = evt.get('is_template', False)

        # 1) 精确匹配缓存
        matched_time = first_seen.get(title_key)

        # 2) 模糊匹配缓存 (overlap >= 0.4 或 子串包含)
        if not matched_time:
            for ck, ct in first_seen.items():
                if len(ck) < 4:
                    continue
                if ck in title_key or title_key in ck:
                    matched_time = ct
                    break
                if _bigram_sim(title_key, ck) >= 0.4:
                    matched_time = ct
                    break

        if matched_time:
            matched_time = _strip_microseconds(matched_time)
            evt['time'] = matched_time
            first_seen[title_key] = matched_time
            # 超过 90 分钟的事件（含模板） → 不输出
            if matched_time[:19] < ninety_min_ago:
                continue
            result.append(evt)
        else:
            # 3) 全新事件：尝试用原始新闻发布时间
            best_news_time = None
            evt_title = evt.get('title', '')
            if evt_title and all_news:
                best_overlap = 0
                evt_chars = set(evt_title)
                for n in all_news:
                    nt = n.get('title', '')
                    ntime = n.get('time', '')
                    if not nt or not ntime:
                        continue
                    overlap = len(evt_chars & set(nt))
                    if overlap > best_overlap and overlap >= 3:
                        best_overlap = overlap
                        best_news_time = ntime

            final_time = _strip_microseconds(best_news_time or evt.get('time', now_iso))
            evt['time'] = final_time
            first_seen[title_key] = final_time
            result.append(evt)

    # ---------- 持久化缓存 ----------
    try:
        with open(FIRST_SEEN_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(first_seen, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  [WARN] 保存 first_seen 缓存失败: {e}", file=sys.stderr)

    removed = len(events) - len(result)
    if removed:
        print(f"  🕐 过滤掉 {removed} 个超过90分钟的老事件")

    return result


def _dedupe_events(events):
    """事件去重：按标题+类别+简化原因合并，保留影响/置信度更高的一条"""
    if not events:
        return []

    best = {}
    ordered_keys = []

    for i, evt in enumerate(events):
        title_key = _normalize_event_text(evt.get('title'))
        category_key = str(evt.get('category') or '')
        reason_key = _normalize_event_text(evt.get('reason'))[:24]
        concepts_key = '|'.join(sorted(_normalize_event_text(c) for c in (evt.get('concepts') or []) if c))

        if title_key:
            dedupe_key = f"{title_key}|{category_key}|{reason_key or concepts_key}"
        else:
            dedupe_key = f"__idx_{i}"

        current = best.get(dedupe_key)
        if current is None:
            best[dedupe_key] = evt
            ordered_keys.append(dedupe_key)
            continue

        cur_score = abs(float(current.get('impact', 0) or 0)) * 100 + float(current.get('confidence', 0) or 0)
        nxt_score = abs(float(evt.get('impact', 0) or 0)) * 100 + float(evt.get('confidence', 0) or 0)
        if nxt_score > cur_score:
            best[dedupe_key] = evt

    return [best[k] for k in ordered_keys]


def build_output(llm_result, prev_data, now, all_news=None, xueqiu_data=None, analyst_views=None):
    """组装最终JSON输出"""
    events = []
    for i, e in enumerate(llm_result.get('events', [])[:12]):
        events.append(enrich_event(e, i, now))

    # === 补充大宗商品常驻事件 ===
    events = _ensure_commodity_events(events, now)

    # === 补充地缘政治事件(新闻中有关键词but LLM未提取的) ===
    if all_news:
        events = _ensure_geopolitical_events(events, all_news, now)

    # === 注入重点事件 + 热门分析师实时观点 ===
    if all_news:
        events = _inject_key_events_with_analyst_views(events, all_news, analyst_views or [], now)

    # === 全量事件补充分析师观点 ===
    events = _attach_analyst_views_to_events(events, analyst_views or [])

    # === 事件去重（防止LLM/模板注入后出现同题材重复卡片） ===
    events = _dedupe_events(events)

    # === 保留已有事件的首次出现时间（防止同一事件每次刷新都变成"刚刚"） ===
    events = _carry_forward_event_times(events, prev_data, all_news)

    # 热度图: 补充趋势（去重：同tag取最高温度）
    heatmap_dict = {}
    for h in llm_result.get('heatmap', []):
        tag = h.get('tag', '')
        if tag not in MARKET_TAGS:
            continue
        temp = max(0, min(100, h.get('temperature', 50)))
        if tag not in heatmap_dict or temp > heatmap_dict[tag]['temperature']:
            heatmap_dict[tag] = {
                "tag": tag,
                "temperature": temp,
                "sentiment": round(h.get('sentiment', 0), 2),
                "trend": compute_trend(temp, prev_data, tag),
            }
    heatmap = list(heatmap_dict.values())
    # 确保所有标签都有热度数据
    existing_tags = {h['tag'] for h in heatmap}
    for tag in MARKET_TAGS:
        if tag not in existing_tags:
            heatmap.append({"tag": tag, "temperature": 50, "sentiment": 0, "trend": "stable"})

    # 用真实行情数据修正热力图趋势方向（LLM 温度对比不可靠）
    real_pct = _fetch_realtime_market_pct()
    if real_pct:
        # 大模型与人工智能共享行情
        if '人工智能' in real_pct and '大模型' not in real_pct:
            real_pct['大模型'] = real_pct['人工智能']
        for h in heatmap:
            pct = real_pct.get(h['tag'])
            if pct is not None:
                h['trend'] = 'up' if pct > 0.5 else ('down' if pct < -0.5 else 'stable')
                h['real_pct'] = round(pct, 2)
                # 涨跌明显时提高温度
                if abs(pct) >= 2:
                    h['temperature'] = max(h['temperature'], 75)
                elif abs(pct) >= 1:
                    h['temperature'] = max(h['temperature'], 60)

    heatmap.sort(key=lambda x: x['temperature'], reverse=True)

    # 市场总览分数
    if events:
        avg_sentiment = sum(e['sentiment'] * abs(e['impact']) for e in events) / max(sum(abs(e['impact']) for e in events), 1)
        outlook_score = int(50 + avg_sentiment * 25)
    else:
        outlook_score = 50
    outlook_score = max(10, min(90, outlook_score))

    return {
        "updated_at": now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        "heatmap": heatmap,
        "events": events,
        "outlook": {
            "period": f"{now.year}年{now.month}月",
            "summary": llm_result.get('outlook_summary', '市场结构性行情延续'),
            "score": outlook_score,
        },
        "xueqiu_hotwords": xueqiu_data if xueqiu_data else None,
        "meta": {
            "news_count": 0,  # filled by main
            "sources": [],
            "model": MODEL,
            "refresh_interval_minutes": 30,
            "analyst_views_count": len(analyst_views or []),
        }
    }


# ==================== 主流程 ====================

def main():
    now = datetime.now(timezone(timedelta(hours=8)))
    print(f"{'='*50}")
    print(f"基金助手 - 宏观事件追踪管道")
    print(f"时间: {now.strftime('%Y-%m-%d %H:%M:%S')} CST")
    print(f"模型: {MODEL}")
    print(f"{'='*50}")

    # 0. 雪球实时热词 (独立于LLM流程, 先行抓取)
    print("\n❄️ [0/3] 雪球实时热词...")
    xueqiu_data = fetch_xueqiu_hotwords()
    if is_valid_xueqiu_data(xueqiu_data):
        xq_count = len(xueqiu_data.get('hotwords', []))
        print(f"  ✅ 雪球热词: {xq_count} 条")
    else:
        print("  ⚠️ 雪球热词: 抓取失败或为空, 将在输出阶段尝试回退缓存")

    # 1. 多源抓取新闻
    print("\n📡 [1/3] 抓取财经新闻...")
    all_news = []
    sources_ok = []

    for name, fetcher in [
        ('新浪财经', fetch_sina_news),
        ('东方财富', fetch_eastmoney_news),
        ('财联社', fetch_cls_news),
        ('BBC', fetch_rss_bbc),
        ('Reuters', fetch_rss_reuters),
        ('AlJazeera', fetch_rss_aljazeera),
        ('FT', fetch_rss_ft),
        ('观察者网', fetch_guancha_news),
    ]:
        try:
            items = fetcher()
            if items:
                all_news.extend(items)
                sources_ok.append(name)
                print(f"  ✅ {name}: {len(items)} 条")
            else:
                print(f"  ⚠️ {name}: 0 条")
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    if not all_news:
        print("\n❌ 没有获取到任何新闻, 保留上次数据")
        return

    # 去重
    seen = set()
    deduped = []
    for n in all_news:
        key = re.sub(r'\W', '', n['title'])[:30]
        if key not in seen:
            seen.add(key)
            deduped.append(n)
    print(f"\n  总计: {len(all_news)} 条, 去重后: {len(deduped)} 条")

    # 1.5 读取热门分析师实时观点（用于重点事件增强）
    analyst_views = _extract_analyst_views(max_items=16)
    if analyst_views:
        print(f"  🧠 热门分析师观点: {len(analyst_views)} 条")
    else:
        print("  ⚠️ 热门分析师观点: 0 条（将仅使用新闻语义）")

    # 2. LLM 结构化
    print("\n🧠 [2/3] AI结构化提取...")
    llm_result = call_llm(deduped)

    if not llm_result:
        print("\n❌ LLM分析失败, 保留上次数据")
        return

    print(f"  提取事件: {len(llm_result.get('events', []))} 条")
    print(f"  热度标签: {len(llm_result.get('heatmap', []))} 个")

    # 3. 组装输出
    print("\n📦 [3/3] 组装输出...")
    prev_data = load_previous()
    if not is_valid_xueqiu_data(xueqiu_data):
        prev_xq = prev_data.get('xueqiu_hotwords') if isinstance(prev_data, dict) else None
        if is_valid_xueqiu_data(prev_xq):
            xueqiu_data = prev_xq
            print(f"  ♻️ 使用上次雪球缓存: {len(xueqiu_data.get('hotwords', []))} 热词")

    output = build_output(
        llm_result,
        prev_data,
        now,
        all_news=deduped,
        xueqiu_data=xueqiu_data,
        analyst_views=analyst_views,
    )
    output['meta']['news_count'] = len(deduped)
    output['meta']['sources'] = list(sources_ok)

    # 保持来源标记与雪球数据一致：仅在有有效雪球热词/热股时保留“雪球”
    has_valid_xq = is_valid_xueqiu_data(output.get('xueqiu_hotwords'))
    if has_valid_xq:
        if '雪球' not in output['meta']['sources']:
            output['meta']['sources'].append('雪球')
    else:
        output['meta']['sources'] = [s for s in output['meta']['sources'] if s != '雪球']

    # 写入文件
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"✅ 输出: {OUTPUT_PATH}")
    print(f"   事件: {len(output['events'])} 条")
    print(f"   热度: {len(output['heatmap'])} 标签")
    print(f"   总览: {output['outlook']['summary']}")
    print(f"   分数: {output['outlook']['score']}")
    if is_valid_xueqiu_data(xueqiu_data):
        print(f"   雪球: {len(xueqiu_data.get('hotwords', []))} 热词 / {len(xueqiu_data.get('hot_stocks', []))} 热股")
    print(f"{'='*50}")


if __name__ == '__main__':
    main()
