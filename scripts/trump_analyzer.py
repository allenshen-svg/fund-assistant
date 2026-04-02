#!/usr/bin/env python3
"""
特朗普言论金融市场预警系统 (Trump Statement Alert System)
- 实时抓取特朗普相关新闻/言论
- NLP情绪分析 (通过已有LLM接口)
- 资产反应矩阵 + 概率预测
- 输出: data/trump_alert_cache.json
"""

import json, os, re, time, math, hashlib
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

# ==================== 路径 ====================
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
CACHE_PATH = os.path.join(DATA_DIR, 'trump_alert_cache.json')
HISTORY_PATH = os.path.join(DATA_DIR, 'trump_alert_history.json')

# ==================== .env ====================
def _load_dotenv():
    env_path = os.path.join(ROOT_DIR, '.env')
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

CST = timezone(timedelta(hours=8))

# ==================== AI 配置 ====================
API_KEY = os.environ.get('AI_API_KEY', '')
API_BASE = os.environ.get('AI_API_BASE', '').strip()
MODEL = os.environ.get('TRUMP_AI_MODEL', 'glm-4-flash')

if not API_BASE:
    model_lower = MODEL.lower()
    if 'glm' in model_lower:
        API_BASE = 'https://open.bigmodel.cn/api/paas/v4'
    elif 'deepseek' in model_lower:
        API_BASE = 'https://api.deepseek.com/v1'
    else:
        API_BASE = 'https://api.siliconflow.cn/v1'

# ==================== 资产反应矩阵 ====================
# 每个资产: entity关键词 → 鹰派(hawkish)影响方向 / 鸽派(dovish)影响方向
# direction: +1 = 看涨, -1 = 看跌
ASSET_MATRIX = {
    'crude_oil': {
        'name': '原油 (Crude Oil)', 'name_en': 'Crude Oil',
        'keywords': ['iran', 'oil', 'opec', 'drill', 'sanctions', 'middle east',
                     'war', 'military', 'embargo', 'petroleum', 'energy',
                     '伊朗', '原油', '制裁', '战争', '中东', '石油', '钻井', '禁运'],
        'hawkish_dir': +1,
        'dovish_dir': -1,
        'base_weight': 0.35,
        'logic': '地缘政治冲突直接增加原油风险溢价；中东局势升温→供应中断预期→油价上行',
    },
    'gold': {
        'name': '黄金 (Gold)', 'name_en': 'Gold',
        'keywords': ['tariff', 'war', 'retaliation', 'uncertainty', 'fed',
                     'dollar', 'inflation', 'sanctions', 'nuclear', 'crisis',
                     '关税', '战争', '报复', '不确定', '美联储', '通胀', '危机', '核'],
        'hawkish_dir': +1,
        'dovish_dir': -1,
        'base_weight': 0.30,
        'logic': '终极避险资产，地缘/贸易动荡→避险需求→金价走强',
    },
    'tech_nasdaq': {
        'name': '科技股 (NASDAQ)', 'name_en': 'Tech/NASDAQ',
        'keywords': ['tariff', 'china', 'chip', 'semiconductor', 'antitrust',
                     'tech', 'ban', 'restrict', 'huawei', 'tiktok', 'apple',
                     '关税', '中国', '芯片', '半导体', '制裁', '科技', '禁令'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.40,
        'logic': '高度依赖全球供应链，关税/禁令→成本飙升→估值承压',
    },
    'sp500': {
        'name': '标普500 (S&P 500)', 'name_en': 'S&P 500',
        'keywords': ['economy', 'tax', 'stimulus', 'rate', 'great', 'deal',
                     'regulation', 'jobs', 'gdp', 'recession', 'market',
                     '经济', '减税', '刺激', '利率', '就业', '衰退', '市场'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.25,
        'logic': '大盘偏好确定性；减税/放松监管→利好，贸易战/冲突→利空',
    },
    'usd_index': {
        'name': '美元指数 (DXY)', 'name_en': 'US Dollar',
        'keywords': ['dollar', 'fed', 'rate', 'interest', 'currency', 'forex',
                     'strong dollar', 'weak dollar', 'treasury', 'debt',
                     '美元', '利率', '汇率', '外汇', '国债', '美联储'],
        'hawkish_dir': +1,
        'dovish_dir': -1,
        'base_weight': 0.25,
        'logic': '鹰派立场→避险资金回流美元→DXY走强；鸽派→风险偏好回升→美元走弱',
    },
    'china_a50': {
        'name': 'A股/中概股', 'name_en': 'China A-Shares',
        'keywords': ['china', 'tariff', 'trade war', 'beijing', 'ban', 'delist',
                     'restrict', 'decouple', 'huawei', 'tiktok', 'taiwan',
                     '中国', '关税', '贸易战', '脱钩', '制裁', '台湾', '禁令'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.40,
        'logic': '对华鹰派→中概承压/A股外资流出；贸易和解→估值修复反弹',
    },
    'defense': {
        'name': '军工板块 (Defense)', 'name_en': 'Defense',
        'keywords': ['war', 'military', 'nato', 'defense', 'weapon', 'missile',
                     'army', 'navy', 'strike', 'bomb', 'troops', 'pentagon',
                     '战争', '军事', '国防', '导弹', '军队', '打击', 'NATO'],
        'hawkish_dir': +1,
        'dovish_dir': -1,
        'base_weight': 0.35,
        'logic': '军事冲突升级→国防预算增加→军工订单确定性提升',
    },
    'crypto_btc': {
        'name': '比特币 (BTC)', 'name_en': 'Bitcoin',
        'keywords': ['crypto', 'bitcoin', 'digital', 'currency', 'regulation',
                     'ban crypto', 'cbdc', 'stablecoin', 'dollar', 'inflation',
                     '加密', '比特币', '数字货币', '通胀', '监管'],
        'hawkish_dir': +1,
        'dovish_dir': -1,
        'base_weight': 0.20,
        'logic': '地缘动荡+通胀担忧→去中心化避险需求↑；美元走弱→BTC受益',
    },
    'natural_gas': {
        'name': '天然气 (Nat Gas)', 'name_en': 'Natural Gas',
        'keywords': ['gas', 'lng', 'pipeline', 'russia', 'europe', 'energy',
                     'sanction', 'nord stream', 'export', 'drill',
                     '天然气', 'LNG', '管道', '俄罗斯', '能源', '出口'],
        'hawkish_dir': +1,
        'dovish_dir': -1,
        'base_weight': 0.25,
        'logic': '对俄/中东制裁→LNG供应紧张→气价走高；能源政策放松→增产→气价回落',
    },
    'copper': {
        'name': '铜 (Copper)', 'name_en': 'Copper',
        'keywords': ['tariff', 'infrastructure', 'china', 'trade', 'manufacturing',
                     'stimulus', 'construction', 'ev', 'green', 'industrial',
                     '关税', '基建', '制造', '铜', '新能源', '工业'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.20,
        'logic': '贸易战→全球制造业放缓→铜需求下降；基建刺激/和解→铜价反弹',
    },
    'treasury_bond': {
        'name': '美债 (US Treasury)', 'name_en': 'US Treasury',
        'keywords': ['fed', 'rate', 'interest', 'bond', 'treasury', 'debt',
                     'deficit', 'spending', 'inflation', 'yield',
                     '美联储', '利率', '国债', '赤字', '通胀', '收益率'],
        'hawkish_dir': +1,
        'dovish_dir': -1,
        'base_weight': 0.25,
        'logic': '风险事件→资金涌入国债避险→债价涨(收益率跌)；经济乐观→债价跌',
    },
    'eu_stocks': {
        'name': '欧洲股市 (STOXX)', 'name_en': 'EU Stocks',
        'keywords': ['europe', 'eu', 'nato', 'tariff', 'germany', 'france',
                     'trade', 'ally', 'alliance', 'sanction',
                     '欧洲', '欧盟', '关税', '德国', '法国', '盟友'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.20,
        'logic': '对欧关税/退出NATO→欧洲经济承压→欧股下行；修复关系→利好',
    },
    'ai_tech': {
        'name': '人工智能/AI算力', 'name_en': 'AI & Computing',
        'keywords': ['ai', 'artificial intelligence', 'chip', 'nvidia', 'gpu',
                     'semiconductor', 'compute', 'data center', 'export control',
                     'china', 'restrict', 'ban', 'technology',
                     '人工智能', 'AI', '算力', '芯片', '大模型', '英伟达', '数据中心'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.40,
        'logic': '芯片出口管制→AI算力受限→板块承压；放松管制/对华缓和→估值修复',
    },
    'robotics': {
        'name': '机器人', 'name_en': 'Robotics',
        'keywords': ['robot', 'automation', 'ai', 'manufacturing', 'tariff',
                     'china', 'technology', 'industry', 'labor',
                     '机器人', '自动化', '制造', '智能制造', '工业'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.25,
        'logic': '关税→制造业成本↑→机器人需求分化；国产替代加速→长期利好',
    },
    'new_energy': {
        'name': '新能源/光伏', 'name_en': 'New Energy/Solar',
        'keywords': ['solar', 'clean energy', 'renewable', 'green', 'climate',
                     'tariff', 'subsidy', 'ira', 'china', 'panel',
                     '新能源', '光伏', '清洁能源', '补贴', '碳中和', '风电'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.30,
        'logic': '对华光伏关税→组件成本↑→装机放缓；取消IRA补贴→行业利空',
    },
    'hk_tech': {
        'name': '港股科技', 'name_en': 'HK Tech',
        'keywords': ['china', 'hong kong', 'tariff', 'delist', 'ban', 'restrict',
                     'tencent', 'alibaba', 'tech', 'sanction', 'trade',
                     '港股', '中概股', '科技', '腾讯', '阿里', '制裁', '关税'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.35,
        'logic': '对华制裁/脱钩→港股科技外资流出→估值压缩；缓和→资金回流',
    },
    'nonferrous_metals': {
        'name': '有色金属', 'name_en': 'Non-ferrous Metals',
        'keywords': ['metal', 'aluminum', 'copper', 'zinc', 'tariff', 'china',
                     'sanction', 'mining', 'rare earth', 'industrial',
                     '有色金属', '铝', '铜', '锌', '稀土', '矿业', '关税'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.25,
        'logic': '贸易战→工业需求萎缩→金属价格承压；但制裁→供应中断→局部品种涨',
    },
    'semiconductor': {
        'name': '半导体', 'name_en': 'Semiconductor',
        'keywords': ['chip', 'semiconductor', 'tsmc', 'asml', 'restrict',
                     'export control', 'china', 'fab', 'wafer', 'foundry',
                     '半导体', '芯片', '晶圆', '光刻', '制裁', '出口管制'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.45,
        'logic': '芯片禁令升级→国产替代加速但短期承压；出口放松→全球半导体回暖',
    },
    'lithium_battery': {
        'name': '锂电', 'name_en': 'Lithium Battery',
        'keywords': ['lithium', 'battery', 'ev', 'tariff', 'china', 'catl',
                     'energy storage', 'electric', 'mineral', 'cobalt',
                     '锂电', '电池', '储能', '新能源车', '锂矿', '钴'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.25,
        'logic': '对华EV/电池关税→出口受阻→锂电产能过剩加剧；和解→需求修复',
    },
    'nev': {
        'name': '新能源车', 'name_en': 'NEV / EV',
        'keywords': ['ev', 'electric vehicle', 'tesla', 'auto', 'tariff',
                     'china', 'subsidy', 'battery', 'byd', 'car',
                     '新能源车', '电动车', '特斯拉', '比亚迪', '汽车', '补贴'],
        'hawkish_dir': -1,
        'dovish_dir': +1,
        'base_weight': 0.30,
        'logic': 'EV关税→中国车企出海受阻→估值承压；补贴延续/关税豁免→反弹',
    },
}

# ==================== 置信度阈值 ====================
ALERT_THRESHOLD = 0.70   # P >= 0.70 → 强烈看涨
BEARISH_THRESHOLD = 0.30 # P <= 0.30 → 强烈看跌

# ==================== 时间衰减参数 ====================
DECAY_HALF_LIFE_HOURS = 12  # 半衰期12小时
FATIGUE_WINDOW_HOURS = 72   # 疲劳检测窗口72小时
FATIGUE_DECAY = 0.6         # 相似话题重复时衰减系数

# ==================== SSL ====================
import ssl
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
}

# ==================== RSS 数据源 ====================
TRUMP_RSS_FEEDS = [
    # Google News 搜索 Trump
    ('Google News',
     'https://news.google.com/rss/search?q=Trump+tariff+OR+trade+OR+sanctions+OR+war&hl=en-US&gl=US&ceid=US:en'),
    # Reuters 政治
    ('Reuters', 'https://www.rss-bridge.org/bridge01/?action=display&bridge=FilterBridge&url='
     'https%3A%2F%2Ffeeds.reuters.com%2Freuters%2FtopNews&filter=Trump&format=Mrss'),
    # CNBC 政治
    ('CNBC', 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664'),
]

# ==================== 抓取函数 ====================

def _fetch_rss(source_name, url, timeout=15):
    """抓取单个 RSS 源，返回 [{title, summary, source, time, url}]"""
    items = []
    try:
        req = Request(url, headers=_HEADERS)
        with urlopen(req, context=_CTX, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
        root = ET.fromstring(raw)
        # RSS 2.0 格式
        for item in root.iter('item'):
            title = (item.findtext('title') or '').strip()
            desc = (item.findtext('description') or '').strip()
            link = (item.findtext('link') or '').strip()
            pub = (item.findtext('pubDate') or '').strip()
            # 只保留含 Trump 关键词的条目
            combined = f'{title} {desc}'.lower()
            if 'trump' not in combined:
                continue
            items.append({
                'title': title,
                'summary': re.sub(r'<[^>]+>', '', desc)[:500],
                'source': source_name,
                'time': pub,
                'url': link,
            })
    except Exception as e:
        print(f'  [trump] ⚠️ RSS抓取失败 {source_name}: {e}')
    return items


def _fetch_google_news_api(timeout=15):
    """通过 Google News RSS 搜索 Trump 最新言论"""
    url = ('https://news.google.com/rss/search?q=Trump+statement+OR+speech+'
           'OR+tariff+OR+sanctions+OR+executive+order&hl=en-US&gl=US&ceid=US:en')
    return _fetch_rss('Google News', url, timeout)


def _fetch_truthsocial_bridge(timeout=15):
    """尝试通过 RSS Bridge 抓取 Truth Social (可能不可用，做兜底)"""
    # Truth Social 无官方 RSS，用新闻聚合兜底
    url = ('https://news.google.com/rss/search?q=Trump+%22Truth+Social%22+'
           'OR+%22Trump+said%22+OR+%22Trump+posted%22&hl=en-US&gl=US&ceid=US:en')
    return _fetch_rss('Truth Social (via News)', url, timeout)


# 川普直接相关性判定: 标题必须包含这些模式之一才算是川普本人的言论/政策
_TRUMP_DIRECT_PATTERNS = re.compile(
    r'trump\s+(said|says|sign|order|threat|warn|claim|declar|announc|post|'
    r'vow|demand|call|push|propose|plan|consider|suggest|signal|seek|'
    r'impose|slap|hit|raise|expand|tariff|sanction|executive|speech|'
    r'interview|tweet|truth\s*social|makes?\s+his|lash)'
    r'|trump\'s\s+(speech|statement|order|tariff|policy|plan|threat|war|'
    r'address|remark|comment|post|move|decision|action|proposal|demand|claim)'
    r'|president\s+trump\s+(sign|order|announc|declar|issue|direct)'
    r'|trump\s+(to|will|would|could|may)\s+'
    r'|trump\s+on\s+'
    r'|trump\s+interview'
    r'|trump\s+primetime'
    r'|trump\s+press\s+conference',
    re.IGNORECASE
)

# 排除: 仅评论/分析文章, 非川普本人言行
_TRUMP_EXCLUDE_PATTERNS = re.compile(
    r'^(opinion|analysis|editorial|column|review|poll|survey|how\s+american|'
    r'what\s+.*\s+think|unbothered|here\'s\s+what|live\s+update|key\s+takeaway|'
    r'fact\s+check|timeline|explainer)',
    re.IGNORECASE
)


def _is_trump_direct(title):
    """判断标题是否直接关于川普本人的言论/政策/行动"""
    title_clean = title.split(' - ')[0].strip()  # 去掉来源后缀
    if _TRUMP_EXCLUDE_PATTERNS.search(title_clean):
        return False
    return bool(_TRUMP_DIRECT_PATTERNS.search(title_clean))


def _parse_pub_time(time_str):
    """解析发布时间为 datetime，用于排序"""
    if not time_str:
        return datetime.min.replace(tzinfo=CST)
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(time_str)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(time_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return dt
    except Exception:
        return datetime.min.replace(tzinfo=CST)


def fetch_trump_statements():
    """并行抓取所有数据源的特朗普言论，过滤+去重+按时间排序"""
    all_items = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {}
        # RSS 源
        for name, url in TRUMP_RSS_FEEDS:
            futures[pool.submit(_fetch_rss, name, url)] = name
        # 额外源
        futures[pool.submit(_fetch_google_news_api)] = 'Google News API'
        futures[pool.submit(_fetch_truthsocial_bridge)] = 'Truth Social'

        for fut in as_completed(futures, timeout=30):
            try:
                items = fut.result()
                all_items.extend(items)
            except Exception as e:
                print(f'  [trump] ⚠️ {futures[fut]} 异常: {e}')

    # 去重: 基于标题 MD5
    seen = set()
    unique = []
    for item in all_items:
        h = hashlib.md5(item['title'].encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            unique.append(item)

    # 过滤: 只保留川普本人直接相关的言论/政策/行动
    before_filter = len(unique)
    direct = [item for item in unique if _is_trump_direct(item['title'])]
    print(f'  [trump] 📡 抓取 {before_filter} 条 → 过滤后 {len(direct)} 条川普直接言论')

    # 按时间降序排序 (最新在前)
    direct.sort(key=lambda x: _parse_pub_time(x.get('time', '')), reverse=True)

    return direct


# ==================== LLM 情绪分析 ====================

SENTIMENT_PROMPT = """你是一位专业的金融市场情绪分析师。请分析以下特朗普相关新闻/言论，返回JSON。

要求:
1. sentiment: "hawkish"(鹰派/激进/威胁/军事行动/贸易战升级) 或 "dovish"(鸽派/缓和/谈判/和解) 或 "neutral"
2. score: 情绪强度 0.0~1.0，务必根据实际影响程度打分，不要保守:
   - 0.1~0.2: 轻微/例行表态 (如: "我们会持续关注")
   - 0.3~0.4: 中等 (如: 暗示可能加关税、口头警告)
   - 0.5~0.6: 较强 (如: 宣布具体关税/制裁措施、威胁军事行动)
   - 0.7~0.8: 强烈 (如: 实际发动军事打击、全面贸易战、战争言论)
   - 0.9~1.0: 极端 (如: 核威胁、全面战争、史无前例的制裁)
   当前伊朗战争、油价飙涨、军事打击等新闻应该在0.6~0.8范围
3. entities: 涉及的关键实体列表 (如 ["iran","oil","military","nasdaq"])
4. summary_zh: 一句话中文摘要 (≤50字)
5. category: "trade_war"|"geopolitics"|"monetary"|"domestic"|"diplomatic"|"other"
6. is_policy: true(实际政策/行政令/军事行动) 或 false(竞选口号/个人攻击/媒体评论)

仅返回JSON，不要其他文字:
{"sentiment":"...","score":0.0,"entities":[],"summary_zh":"...","category":"...","is_policy":false}"""


def _call_llm(text, timeout=30):
    """调用LLM分析单条言论情绪，返回解析后的dict"""
    url = f'{API_BASE}/chat/completions'
    payload = json.dumps({
        'model': MODEL,
        'messages': [
            {'role': 'system', 'content': SENTIMENT_PROMPT},
            {'role': 'user', 'content': text[:2000]},
        ],
        'temperature': 0.1,
        'max_tokens': 300,
    }).encode()

    req = Request(url, data=payload, headers={
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {API_KEY}',
        **_HEADERS,
    })
    try:
        with urlopen(req, context=_CTX, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        content = data['choices'][0]['message']['content']
        # 提取 JSON
        m = re.search(r'\{.*\}', content, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f'  [trump] ⚠️ LLM分析失败: {e}')
    return None


def _keyword_fallback(text):
    """关键词兜底情绪分析 (LLM不可用时)"""
    text_lower = text.lower()
    hawk_words = ['tariff', 'sanction', 'war', 'ban', 'punish', 'threat',
                  'retaliat', 'attack', 'destroy', 'crush', 'enemy',
                  '关税', '制裁', '战争', '禁令', '威胁', '报复', '打击']
    dove_words = ['deal', 'negotiat', 'peace', 'agree', 'cooperat', 'lift',
                  'remove', 'friend', 'great', 'beautiful', 'together',
                  '协议', '谈判', '和平', '合作', '取消', '解除', '朋友']

    hawk_score = sum(1 for w in hawk_words if w in text_lower)
    dove_score = sum(1 for w in dove_words if w in text_lower)

    if hawk_score > dove_score:
        sentiment = 'hawkish'
        score = min(1.0, hawk_score * 0.2)
    elif dove_score > hawk_score:
        sentiment = 'dovish'
        score = min(1.0, dove_score * 0.2)
    else:
        sentiment = 'neutral'
        score = 0.3

    # 提取实体
    entities = []
    for asset_id, asset in ASSET_MATRIX.items():
        for kw in asset['keywords']:
            if kw in text_lower:
                entities.append(kw)
                break

    return {
        'sentiment': sentiment,
        'score': score,
        'entities': entities,
        'summary_zh': text[:50],
        'category': 'other',
        'is_policy': False,
    }


def analyze_sentiment(items):
    """对抓取到的言论列表进行情绪分析，返回带分析结果的列表"""
    results = []
    for item in items[:20]:  # 限制最多分析20条
        text = f"{item['title']}. {item.get('summary', '')}"
        # 优先 LLM，失败则用关键词兜底
        analysis = None
        if API_KEY:
            analysis = _call_llm(text)
            time.sleep(0.3)  # 避免限频
        if not analysis:
            analysis = _keyword_fallback(text)

        item['analysis'] = analysis
        results.append(item)
        label = analysis.get('sentiment', '?')
        score = analysis.get('score', 0)
        print(f'  [trump] 📊 {label}({score:.1f}) | {item["title"][:60]}')

    return results


# ==================== 概率预测引擎 ====================

def _time_decay(pub_time_str):
    """时间衰减: 越新影响越大, DECAY_HALF_LIFE_HOURS小时半衰期"""
    if not pub_time_str:
        return 0.5
    now = datetime.now(CST)
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub_time_str)
    except Exception:
        try:
            dt = datetime.fromisoformat(pub_time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CST)
        except Exception:
            return 0.5
    age_h = max(0, (now - dt).total_seconds() / 3600)
    return 2.0 ** (-age_h / DECAY_HALF_LIFE_HOURS)


def _fatigue_factor(item, history):
    """疲劳效应: 如果近期有相似话题则衰减"""
    if not history:
        return 1.0
    entities = set(item.get('analysis', {}).get('entities', []))
    if not entities:
        return 1.0
    now = datetime.now(CST)
    overlap_count = 0
    for hist in history:
        # 检查时间窗口
        ht = hist.get('time', '')
        try:
            from email.utils import parsedate_to_datetime
            hdt = parsedate_to_datetime(ht)
        except Exception:
            continue
        age_h = (now - hdt).total_seconds() / 3600
        if age_h > FATIGUE_WINDOW_HOURS:
            continue
        h_entities = set(hist.get('analysis', {}).get('entities', []))
        if entities & h_entities:
            overlap_count += 1
    if overlap_count == 0:
        return 1.0
    return max(0.2, FATIGUE_DECAY ** overlap_count)


def _entity_relevance(item, asset_id):
    """计算言论与特定资产的实体相关度 W_entity (0~1)"""
    asset = ASSET_MATRIX[asset_id]
    text = f"{item.get('title','')} {item.get('summary','')}".lower()
    entities = item.get('analysis', {}).get('entities', [])
    all_text = f"{text} {' '.join(entities)}"

    hit = sum(1 for kw in asset['keywords'] if kw in all_text)
    return min(1.0, hit / max(1, len(asset['keywords']) * 0.3))


def compute_probabilities(analyzed_items, history=None):
    """
    核心概率公式 (Logistic):
    P(R_i > 0 | S_t) = 1 / (1 + e^-(α + β1·E_score + β2·W_entity + γ·V_t))

    综合多条言论时取加权平均 (按时间衰减+疲劳加权)
    融入历史校准: alpha_adj修正偏差, confidence_adj调整信号强度
    """
    # 加载校准数据 (从历史预测vs实际对比中学习)
    calibration = load_calibration()

    asset_probs = {}

    for asset_id, asset in ASSET_MATRIX.items():
        # 基准参数 + 校准修正
        cal = calibration.get(asset_id, {})
        alpha = 0.0 + cal.get('alpha_adj', 0.0)  # 基准偏移 + 校准修正
        beta1 = 2.5       # 情绪系数
        beta2 = 1.8       # 实体相关度系数
        gamma = 0.3       # 波动率系数
        conf_adj = cal.get('confidence_adj', 1.0)  # 置信度调整

        weighted_sum = 0.0
        total_weight = 0.0

        for item in analyzed_items:
            analysis = item.get('analysis', {})
            sentiment = analysis.get('sentiment', 'neutral')
            score = analysis.get('score', 0.3)
            is_policy = analysis.get('is_policy', False)

            # E_score: -1~+1 (按资产的反应方向映射)
            if sentiment == 'hawkish':
                e_score = score * asset['hawkish_dir']
            elif sentiment == 'dovish':
                e_score = score * asset['dovish_dir']
            else:
                e_score = 0.0

            # W_entity: 0~1
            w_entity = _entity_relevance(item, asset_id)

            # 权重: 时间衰减 × 疲劳衰减 × 政策加成
            decay = _time_decay(item.get('time', ''))
            fatigue = _fatigue_factor(item, history)
            policy_boost = 1.5 if is_policy else 1.0
            weight = decay * fatigue * policy_boost * asset['base_weight']

            # Logistic 概率
            z = alpha + beta1 * e_score + beta2 * w_entity * abs(e_score) + gamma
            prob = 1.0 / (1.0 + math.exp(-z))

            weighted_sum += prob * weight
            total_weight += weight

        # 加权平均概率
        if total_weight > 0:
            raw_prob = weighted_sum / total_weight
        else:
            raw_prob = 0.5

        # 应用校准: confidence_adj 调整偏离中性的幅度
        deviation = (raw_prob - 0.5) * conf_adj
        final_prob = max(0.01, min(0.99, 0.5 + deviation))

        # 生成信号
        if final_prob >= ALERT_THRESHOLD:
            signal = 'strong_bullish'
            signal_zh = '强烈看涨 📈'
        elif final_prob >= 0.6:
            signal = 'bullish'
            signal_zh = '偏多 📈'
        elif final_prob <= BEARISH_THRESHOLD:
            signal = 'strong_bearish'
            signal_zh = '强烈看跌 📉'
        elif final_prob <= 0.4:
            signal = 'bearish'
            signal_zh = '偏空 📉'
        else:
            signal = 'neutral'
            signal_zh = '中性 ➡️'

        asset_probs[asset_id] = {
            'name': asset['name'],
            'name_en': asset['name_en'],
            'probability': round(final_prob, 4),
            'signal': signal,
            'signal_zh': signal_zh,
            'logic': asset['logic'],
            'hit_rate': cal.get('hit_rate'),
            'calibrated': bool(cal),
        }

    return asset_probs


# ==================== 缓存读写 ====================

def load_cache():
    """读取缓存"""
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _load_history():
    """加载历史记录 (用于疲劳检测)"""
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(items, max_items=200):
    """追加到历史记录，保留最近 max_items 条"""
    history = _load_history()
    history.extend(items)
    history = history[-max_items:]
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HISTORY_PATH, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ==================== 主函数 ====================

def main():
    """完整流水线: 抓取 → 分析 → 预测 → 保存"""
    print(f'\n[trump] ===== 特朗普言论预警分析 {datetime.now(CST).strftime("%Y-%m-%d %H:%M")} =====')

    # 1. 抓取
    raw_items = fetch_trump_statements()
    if not raw_items:
        print('[trump] ⚠️ 未抓取到任何言论，跳过')
        return

    # 2. 情绪分析
    analyzed = analyze_sentiment(raw_items)

    # 3. 加载历史 (疲劳检测)
    history = _load_history()

    # 4. 概率预测
    predictions = compute_probabilities(analyzed, history)

    # 5. 生成警报
    alerts = []
    for asset_id, pred in predictions.items():
        p = pred['probability']
        if p >= ALERT_THRESHOLD or p <= BEARISH_THRESHOLD:
            alerts.append({
                'asset': pred['name'],
                'probability': p,
                'signal': pred['signal_zh'],
                'logic': pred['logic'],
            })

    # 6. 保存缓存 (按时间降序)
    sorted_analyzed = sorted(analyzed,
        key=lambda x: _parse_pub_time(x.get('time', '')), reverse=True)
    result = {
        'updated_at': datetime.now(CST).isoformat(),
        'statement_count': len(sorted_analyzed),
        'statements': [
            {
                'title': item['title'],
                'source': item['source'],
                'time': item.get('time', ''),
                'url': item.get('url', ''),
                'sentiment': item['analysis']['sentiment'],
                'score': item['analysis']['score'],
                'summary_zh': item['analysis'].get('summary_zh', ''),
                'category': item['analysis'].get('category', ''),
                'is_policy': item['analysis'].get('is_policy', False),
            }
            for item in sorted_analyzed
        ],
        'predictions': predictions,
        'alerts': alerts,
        'alert_count': len(alerts),
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 7. 追加历史
    _save_history(analyzed)

    # 8. 输出摘要
    print(f'[trump] ✅ 分析完成: {len(analyzed)}条言论, {len(alerts)}条警报')
    for a in alerts:
        print(f'  🚨 {a["asset"]}: {a["signal"]} (P={a["probability"]:.1%})')

    # 9. 记录预测日志 (用于后续复盘校准)
    log_prediction(predictions)

    return result


# ==================== 预测日志 & 反馈校准系统 ====================

PREDICTION_LOG_PATH = os.path.join(DATA_DIR, 'trump_prediction_log.json')
CALIBRATION_PATH = os.path.join(DATA_DIR, 'trump_calibration.json')

# 每个 asset_id → 东方财富 secid (用于获取实际涨跌)
ASSET_SECIDS = {
    'crude_oil':        '102.CL00Y',   # WTI原油
    'gold':             '101.GC00Y',   # COMEX黄金
    'tech_nasdaq':      '100.NDX',     # 纳斯达克100
    'sp500':            '100.SPX',     # 标普500
    'usd_index':        '100.UDI',     # 美元指数
    'china_a50':        '1.000001',    # 上证指数
    'defense':          '0.159792',    # 军工ETF
    'crypto_btc':       None,          # 暂无东方财富source
    'natural_gas':      '102.NG00Y',   # NYMEX天然气
    'copper':           '101.HG00Y',   # COMEX铜
    'treasury_bond':    None,          # 暂无直接source
    'eu_stocks':        '100.GDAXI',   # 德国DAX
    'ai_tech':          '0.159819',    # 人工智能ETF
    'robotics':         '1.562500',    # 机器人ETF
    'new_energy':       '0.159930',    # 新能源ETF
    'hk_tech':          '1.513180',    # 恒生科技ETF
    'nonferrous_metals':'113.cum',     # 沪铜(代表有色)
    'semiconductor':    '1.516380',    # 半导体ETF
    'lithium_battery':  None,          # 暂无单独ETF
    'nev':              None,          # 暂无直接ETF
}


def _fetch_actual_market():
    """获取所有可追踪资产的实际涨跌幅"""
    secids_dict = {}
    for asset_id, secid in ASSET_SECIDS.items():
        if secid:
            secids_dict[secid] = {'asset_id': asset_id}
    if not secids_dict:
        return {}
    secids_str = ','.join(secids_dict.keys())
    url = f'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f12,f14&secids={secids_str}'
    results = {}
    try:
        req = Request(url, headers=_HEADERS)
        with urlopen(req, context=_CTX, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        items_list = (data.get('data') or {}).get('diff') or []
        for it in items_list:
            code = str(it.get('f12', ''))
            pct = it.get('f3')
            price = it.get('f2')
            name = it.get('f14', '')
            if pct is None:
                continue
            for secid, info in secids_dict.items():
                if secid.split('.')[-1] == code or secid.endswith('.' + code):
                    results[info['asset_id']] = {
                        'pct': float(pct), 'price': price, 'name': name,
                    }
                    break
    except Exception as e:
        print(f'  [trump] ⚠️ 实际行情获取失败: {e}')
    return results


def _load_prediction_log():
    if not os.path.exists(PREDICTION_LOG_PATH):
        return []
    try:
        with open(PREDICTION_LOG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save_prediction_log(log_data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PREDICTION_LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, ensure_ascii=False, indent=2)


def log_prediction(predictions):
    """记录当前预测到日志 (每小时最多一条)"""
    now = datetime.now(CST)
    log_data = _load_prediction_log()
    hour_key = now.strftime('%Y-%m-%d %H')
    if log_data and log_data[-1].get('hour_key') == hour_key:
        log_data[-1]['predictions'] = predictions
    else:
        log_data.append({
            'hour_key': hour_key,
            'timestamp': now.isoformat(),
            'predictions': predictions,
            'actual': None,
        })
    log_data = log_data[-720:]  # 保留30天
    _save_prediction_log(log_data)


def backfill_actual():
    """回填历史预测的实际行情结果"""
    log_data = _load_prediction_log()
    if not log_data:
        return
    actual = _fetch_actual_market()
    if not actual:
        print('  [trump] ⚠️ 无法获取实际行情，跳过回填')
        return
    now = datetime.now(CST)
    changed = False
    for entry in log_data:
        if entry.get('actual'):
            continue
        ts = entry.get('timestamp', '')
        try:
            entry_time = datetime.fromisoformat(ts)
        except Exception:
            continue
        age_h = (now - entry_time).total_seconds() / 3600
        if age_h < 6 or age_h > 48:
            continue
        entry['actual'] = {}
        for asset_id, mkt in actual.items():
            entry['actual'][asset_id] = {'pct': mkt['pct'], 'price': mkt.get('price')}
        entry['backfill_at'] = now.isoformat()
        changed = True
    if changed:
        _save_prediction_log(log_data)
        filled = sum(1 for e in log_data if e.get('backfill_at'))
        print(f'  [trump] 📊 已回填 {filled} 条历史预测的实际结果')


def compute_calibration():
    """对比预测vs实际，计算校准因子: hit_rate, bias, confidence_adj, alpha_adj"""
    log_data = _load_prediction_log()
    records = [e for e in log_data if e.get('actual') and e.get('predictions')]
    if len(records) < 3:
        return {}
    calibration = {}
    for asset_id in ASSET_MATRIX:
        hits = 0
        total = 0
        bias_sum = 0.0
        for entry in records[-50:]:
            pred = entry['predictions'].get(asset_id, {})
            act = entry['actual'].get(asset_id, {})
            if not pred or not act:
                continue
            prob = pred.get('probability', 0.5)
            actual_pct = act.get('pct', 0)
            total += 1
            pred_up = prob > 0.5
            actual_up = actual_pct > 0
            if pred_up == actual_up:
                hits += 1
            bias_sum += (prob - 0.5) - (0.01 * actual_pct)
        if total < 3:
            continue
        hit_rate = hits / total
        avg_bias = bias_sum / total
        # 校准因子
        if hit_rate >= 0.65:
            confidence_adj = 1.0 + (hit_rate - 0.5) * 0.5
        elif hit_rate <= 0.35:
            confidence_adj = 0.6
        else:
            confidence_adj = 0.8 + hit_rate * 0.4
        alpha_adj = max(-0.5, min(0.5, -avg_bias * 2.0))
        calibration[asset_id] = {
            'hit_rate': round(hit_rate, 3),
            'total_samples': total,
            'avg_bias': round(avg_bias, 4),
            'confidence_adj': round(confidence_adj, 3),
            'alpha_adj': round(alpha_adj, 4),
            'updated_at': datetime.now(CST).isoformat(),
        }
    if calibration:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CALIBRATION_PATH, 'w', encoding='utf-8') as f:
            json.dump(calibration, f, ensure_ascii=False, indent=2)
        avg_hit = sum(c['hit_rate'] for c in calibration.values()) / len(calibration)
        print(f'  [trump] 🎯 校准完成: {len(calibration)}个资产, 平均命中率 {avg_hit:.1%}')
    return calibration


def load_calibration():
    if not os.path.exists(CALIBRATION_PATH):
        return {}
    try:
        with open(CALIBRATION_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def daily_review():
    """每日复盘: 回填实际行情 → 计算校准 → 输出报告"""
    print(f'\n[trump] 📋 ===== 每日预测复盘 {datetime.now(CST).strftime("%Y-%m-%d %H:%M")} =====')
    backfill_actual()
    calibration = compute_calibration()
    if calibration:
        print('  [trump] 📊 板块命中率:')
        for aid, cal in sorted(calibration.items(), key=lambda x: x[1]['hit_rate'], reverse=True):
            name = ASSET_MATRIX.get(aid, {}).get('name', aid)
            print(f'    {name:20s} 命中={cal["hit_rate"]:.0%} ({cal["total_samples"]}次) 偏差={cal["avg_bias"]:+.3f} 调整={cal["confidence_adj"]:.2f}')
    else:
        print('  [trump] ⚠️ 样本不足，暂无法校准 (需至少3条已回填的历史记录)')
    return calibration


if __name__ == '__main__':
    main()

