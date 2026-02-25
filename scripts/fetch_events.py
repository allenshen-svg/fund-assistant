#!/usr/bin/env python3
"""
基金助手 - 宏观事件自动追踪管道
GitHub Actions 每2小时运行: 抓取财经新闻 → LLM结构化提取 → 输出 data/hot_events.json

数据流: 新闻源 → AI事件提炼 → 概念标签 → 行业映射 → hot_events.json → 前端消费
"""

import json, os, re, sys, ssl, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request

# ==================== 配置 ====================
API_KEY = os.environ.get('AI_API_KEY', '')
API_BASE = os.environ.get('AI_API_BASE', 'https://api.302.ai/v1')
MODEL = os.environ.get('AI_MODEL', 'deepseek-ai/DeepSeek-V3')
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'hot_events.json')

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
- **大宗商品**: 原油/油气价格变动、OPEC决策、铜铝等有色金属涨跌、黄金白银走势
- **能源与资源**: 能源政策、矿产资源供需、碳排放政策
- **消费与内需**: 社零数据、消费政策、白酒/食品行业动态
- **医药健康**: 医药政策、集采、创新药审批
- **军工国防**: 军费预算、装备采购、地缘冲突
如果新闻中有涉及有色金属(铜、铝、锌、稀土等)、原油/油气、黄金白银等大宗商品的内容，务必单独提取为事件。

sectors_positive 和 sectors_negative 字段应使用以下标准板块名：
AI/科技、半导体、算力、AIGC、新能源、光伏、锂电、新能源车、消费、食品饮料、白酒、医药、创新药、
黄金、贵金属、有色金属、铜铝、大宗商品、能源、原油、油气、
军工、国防、红利、高股息、债券、固收、金融、银行、券商、
港股科技、港股互联网、恒生科技、地产、基建、宽基

fund_keywords 字段应包含能匹配到基金名称/类型的关键词，如: 人工智能、AI、算力、黄金、有色金属、油气、原油、新能源、半导体、军工、消费、医药、红利等。

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

    try:
        with urlopen(req, timeout=90, context=_ssl_ctx()) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            content = result['choices'][0]['message']['content']
            # strip markdown code fences
            content = re.sub(r'```json\s*', '', content)
            content = re.sub(r'```\s*', '', content)
            return json.loads(content.strip())
    except Exception as e:
        print(f"[ERROR] LLM call failed: {e}", file=sys.stderr)
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
        "time": now.isoformat(),
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
            evt['time'] = now.isoformat()
            events.append(evt)
            added += 1
            print(f"  📌 补充常驻事件: {evt['title']} (动态事件未覆盖 {fb['key_sectors']})")

    if added:
        print(f"  共补充 {added} 个大宗商品常驻事件")
    return events


def build_output(llm_result, prev_data, now):
    """组装最终JSON输出"""
    events = []
    for i, e in enumerate(llm_result.get('events', [])[:12]):
        events.append(enrich_event(e, i, now))

    # === 补充大宗商品常驻事件 (确保油气/有色/黄金持仓始终可被归因) ===
    events = _ensure_commodity_events(events, now)

    # 热度图: 补充趋势
    heatmap = []
    for h in llm_result.get('heatmap', []):
        tag = h.get('tag', '')
        if tag not in MARKET_TAGS:
            continue
        temp = max(0, min(100, h.get('temperature', 50)))
        heatmap.append({
            "tag": tag,
            "temperature": temp,
            "sentiment": round(h.get('sentiment', 0), 2),
            "trend": compute_trend(temp, prev_data, tag),
        })
    # 确保所有标签都有热度数据
    existing_tags = {h['tag'] for h in heatmap}
    for tag in MARKET_TAGS:
        if tag not in existing_tags:
            heatmap.append({"tag": tag, "temperature": 50, "sentiment": 0, "trend": "stable"})
    heatmap.sort(key=lambda x: x['temperature'], reverse=True)

    # 市场总览分数
    if events:
        avg_sentiment = sum(e['sentiment'] * abs(e['impact']) for e in events) / max(sum(abs(e['impact']) for e in events), 1)
        outlook_score = int(50 + avg_sentiment * 25)
    else:
        outlook_score = 50
    outlook_score = max(10, min(90, outlook_score))

    return {
        "updated_at": now.isoformat(),
        "heatmap": heatmap,
        "events": events,
        "outlook": {
            "period": f"{now.year}年{now.month}月",
            "summary": llm_result.get('outlook_summary', '市场结构性行情延续'),
            "score": outlook_score,
        },
        "meta": {
            "news_count": 0,  # filled by main
            "sources": [],
            "model": MODEL,
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

    # 1. 多源抓取新闻
    print("\n📡 [1/3] 抓取财经新闻...")
    all_news = []
    sources_ok = []

    for name, fetcher in [
        ('新浪财经', fetch_sina_news),
        ('东方财富', fetch_eastmoney_news),
        ('财联社', fetch_cls_news),
        ('BBC', fetch_rss_bbc),
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
        sys.exit(0)

    # 去重
    seen = set()
    deduped = []
    for n in all_news:
        key = re.sub(r'\W', '', n['title'])[:30]
        if key not in seen:
            seen.add(key)
            deduped.append(n)
    print(f"\n  总计: {len(all_news)} 条, 去重后: {len(deduped)} 条")

    # 2. LLM 结构化
    print("\n🧠 [2/3] AI结构化提取...")
    llm_result = call_llm(deduped)

    if not llm_result:
        print("\n❌ LLM分析失败, 保留上次数据")
        sys.exit(0)

    print(f"  提取事件: {len(llm_result.get('events', []))} 条")
    print(f"  热度标签: {len(llm_result.get('heatmap', []))} 个")

    # 3. 组装输出
    print("\n📦 [3/3] 组装输出...")
    prev_data = load_previous()
    output = build_output(llm_result, prev_data, now)
    output['meta']['news_count'] = len(deduped)
    output['meta']['sources'] = sources_ok

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
    print(f"{'='*50}")


if __name__ == '__main__':
    main()
