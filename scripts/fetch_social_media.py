#!/usr/bin/env python3
"""
社交媒体舆情数据抓取管道
抓取抖音热榜 + 小红书热门笔记 → 过滤财经相关 → 输出 data/social_media_videos.json

数据流: 抖音热榜API + 小红书热点 → 财经关键词过滤 → 结构化输出 → 前端消费

可通过 GitHub Actions / cron 定时运行，也可手动执行
"""

import json, os, re, sys, ssl, time, hashlib
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.parse import quote, urlencode
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor, HTTPSHandler

# ==================== .env 自动加载 ====================
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
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'social_media_videos.json')

# 财经相关关键词 (用于过滤非财经内容)
FINANCE_KEYWORDS = [
    # 大盘/指数
    'A股', '股市', '大盘', '沪指', '上证', '深成', '创业板', '科创板', '北证',
    '沪深300', '中证500', '中证1000', '恒生', '港股', '美股', '纳斯达克', '道琼斯',
    # 行业/板块
    'AI', '人工智能', '算力', '芯片', '半导体', '光模块', 'CPO', '大模型', 'DeepSeek',
    '机器人', '自动驾驶', '新能源', '光伏', '锂电', '碳酸锂', '储能',
    '军工', '国防', '航天', '白酒', '消费', '医药', '创新药', 'CXO',
    '黄金', '金价', '原油', '油价', '有色金属', '铜', '铝', '稀土',
    '红利', '高股息', '银行', '保险', '券商', '地产', '房地产',
    # 宏观/政策
    '央行', '降息', '降准', 'LPR', '利率', '通胀', 'CPI', 'PPI', 'GDP', 'PMI',
    '美联储', 'Fed', '加息', '缩表', '国债', '债券', '汇率', '人民币', '美元',
    '关税', '贸易战', '制裁', '地缘', '中东', '俄乌', '美伊',
    # 地缘政治深度
    '伊朗', '叙利亚', '以色列', '九合', '台海', '南海', '北约', '战争', '冲突',
    '局势', '军事', '核武器', '导弹', '无人机', '英国脱欧',
    '中美关系', '中美博弈', '科技战', '芯片战',
    # 投资/理财
    '基金', 'ETF', '牛市', '熊市', '涨停', '跌停', '抄底', '追高', '割肉',
    '仓位', '加仓', '减仓', '清仓', '满仓', '空仓', '定投',
    '主力', '资金', '北向', '融资', '融券', '杠杆',
    '茅台', '比亚迪', '宁德', '英伟达', 'NVIDIA', '特斯拉',
    'IPO', '分红', '回购', '并购', '重组',
    # 经济/产业影响
    '经济影响', '未来经济', '产业链', '供应链', '双循环', '内循环',
    '科技革命', '数字经济', '碳中和', '绿色转型', '就业', '失业率',
    '房价', '楼市', '出口', '进口', '外贸', '通缩', '滞胀',
    # 石油/能源危机
    '石油', '天然气', 'OPEC', '能源危机', '能源安全', '电价', '气价',
    '白银', '银价', '贵金属', '铂金', '钯金',
]

# 需要过滤的营销/博眼球关键词
NOISE_KEYWORDS = [
    '震惊', '不转不是中国人', '速看', '必看', '删前快看',
    '最后一次机会', '全仓梭哈', '晚了就来不及', '赶紧',
]

# 娱乐/体育等无关内容排除关键词
NOISE_CATEGORY_KW = [
    '明星', '八卦', '绯闻', '综艺', '选秀', '偶像', '粉丝', '追星', '爱豆', '热播',
    '电视剧', '电影', '票房', '娱乐圈', '离婚', '恋情', '官宣', '红毯', '颁奖',
    '歌手', '演员', '导演', '变装', '综艺节目', '真人秀',
    '足球', '篮球', 'NBA', 'CBA', '西甲', '英超', '世界杯', '欧冠',
    '奥运', '亚运', '乒乓', '网球', '奖牌', '冠军赛',
    '宠物', '猫咪', '狗狗', '美食教程', '减肥', '化妆', '穿搭',
    '搞笑', '段子', '鬼畜', '整蛊',
]
_noise_cat_lower = [kw.lower() for kw in NOISE_CATEGORY_KW]

def is_noise_category(text):
    """检测是否属于娱乐/体育等无关类别"""
    if not text:
        return False
    t = text.lower()
    has_noise = any(kw in t for kw in _noise_cat_lower)
    if has_noise and not is_finance_related(text):
        return True
    return False

# 用户代理
UA_MOBILE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
UA_DESKTOP = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'

NOW = datetime.now(timezone(timedelta(hours=8)))


def _ssl_ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_url(url, headers=None, timeout=15):
    """通用 HTTP GET"""
    h = {
        'User-Agent': UA_DESKTOP,
        'Accept': 'application/json, text/html, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    try:
        with urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"  [WARN] fetch failed: {url[:80]}... - {e}", file=sys.stderr)
        return None


def is_finance_related(text):
    """判断文本是否与财经相关"""
    if not text:
        return False
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in FINANCE_KEYWORDS)


def noise_score(text):
    """计算噪音分 (越高越可能是营销/博眼球)"""
    if not text:
        return 0
    score = 0
    for kw in NOISE_KEYWORDS:
        if kw in text:
            score += 1
    # 感叹号数量
    score += text.count('！') * 0.3
    score += text.count('!') * 0.3
    # 全大写标识
    if text == text.upper() and len(text) > 5:
        score += 1
    return score


def gen_id(text):
    """生成确定性唯一 ID"""
    return 'sm_' + hashlib.md5(text.encode()).hexdigest()[:12]


# ==================== 抖音热榜抓取 ====================

def fetch_douyin_hot():
    """
    抖音热榜 (公开 Web API)
    返回热搜词条列表 → 过滤财经相关
    """
    print("  📱 抖音热榜抓取...")
    items = []

    # 方式1: 抖音热点榜 API
    hot_urls = [
        'https://www.douyin.com/aweme/v1/web/hot/search/list/',
        'https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/',
    ]

    for url in hot_urls:
        raw = fetch_url(url, headers={
            'User-Agent': UA_DESKTOP,
            'Referer': 'https://www.douyin.com/',
        })
        if not raw:
            continue
        try:
            data = json.loads(raw)
            word_list = (data.get('data', {}).get('word_list', []) or
                        data.get('word_list', []) or [])
            for item in word_list:
                word = item.get('word', '') or item.get('query', '') or ''
                hot_value = item.get('hot_value', 0) or item.get('search_count', 0) or 0
                event_time = item.get('event_time', '') or ''
                if word and is_finance_related(word):
                    items.append({
                        'title': word,
                        'summary': item.get('word_sub_board', '') or item.get('label', '') or '',
                        'likes': int(hot_value) if hot_value else 0,
                        'platform': '抖音',
                        'source_type': '热榜',
                    })
            if items:
                break
        except Exception as e:
            print(f"    [WARN] 抖音热榜解析失败: {e}", file=sys.stderr)

    # 方式2: 第三方聚合 (tophub)
    if not items:
        raw = fetch_url('https://api.vvhan.com/api/hotlist/douyinHot', headers={
            'User-Agent': UA_DESKTOP,
        })
        if raw:
            try:
                data = json.loads(raw)
                for item in (data.get('data', []) or []):
                    title = item.get('title', '') or item.get('name', '') or ''
                    hot = item.get('hot', 0) or item.get('hotValue', 0) or 0
                    if title and is_finance_related(title):
                        items.append({
                            'title': title,
                            'summary': item.get('desc', '') or '',
                            'likes': int(hot) if hot else 0,
                            'platform': '抖音',
                            'source_type': '热搜',
                        })
            except Exception as e:
                print(f"    [WARN] 抖音备用源失败: {e}", file=sys.stderr)

    # 方式3: 韩小韩热榜API
    if not items:
        raw = fetch_url('https://api.vvhan.com/api/hotlist?type=douyinHot')
        if raw:
            try:
                data = json.loads(raw)
                for item in (data.get('data', []) or []):
                    title = item.get('title', '') or ''
                    hot = item.get('hot', 0) or 0
                    if title and is_finance_related(title):
                        items.append({
                            'title': title,
                            'summary': item.get('desc', '') or '',
                            'likes': int(hot) if hot else 0,
                            'platform': '抖音',
                            'source_type': '热搜',
                        })
            except Exception as e:
                print(f"    [WARN] 抖音韩小韩API失败: {e}", file=sys.stderr)

    print(f"    ✅ 抖音财经相关: {len(items)} 条")
    return items


def fetch_douyin_finance_videos():
    """
    抖音财经类视频热门内容 (搜索API / 推荐流)
    搜索预设财经关键词获取热门视频
    """
    print("  🎬 抖音财经视频搜索...")
    items = []
    search_keywords = ['AI算力', '股市', '基金', '黄金投资', '半导体', '新能源', '军工',
                         '港股', '石油经济', '伊朗制裁', '中东局势', 'AI未来经济']

    for kw in search_keywords:
        # 60 second API
        url = f'https://www.douyin.com/aweme/v1/web/general/search/single/?keyword={quote(kw)}&search_channel=aweme_general&sort_type=2&publish_time=1&count=5'
        raw = fetch_url(url, headers={
            'User-Agent': UA_DESKTOP,
            'Referer': f'https://www.douyin.com/search/{quote(kw)}',
            'Cookie': 'ttwid=placeholder',
        })
        if not raw:
            continue
        try:
            data = json.loads(raw)
            aweme_list = data.get('data', []) or data.get('aweme_list', []) or []
            for aweme in aweme_list[:3]:
                desc = aweme.get('aweme_info', aweme).get('desc', '') or ''
                stats = aweme.get('aweme_info', aweme).get('statistics', {})
                if desc and is_finance_related(desc):
                    items.append({
                        'title': desc[:80],
                        'summary': desc,
                        'likes': stats.get('digg_count', 0) or 0,
                        'shares': stats.get('share_count', 0) or 0,
                        'comments_count': stats.get('comment_count', 0) or 0,
                        'platform': '抖音',
                        'source_type': '视频搜索',
                    })
        except Exception as e:
            print(f"    [WARN] 抖音搜索 '{kw}' 失败: {e}", file=sys.stderr)
        time.sleep(0.5)  # 避免频率限制

    print(f"    ✅ 抖音视频搜索: {len(items)} 条")
    return items


# ==================== 小红书热点抓取 ====================

def fetch_xiaohongshu_hot():
    """
    小红书热门话题/笔记 (公开接口)
    """
    print("  📕 小红书热点抓取...")
    items = []

    # 第三方热榜聚合
    hot_urls = [
        'https://api.vvhan.com/api/hotlist/xhsHot',
        'https://api.vvhan.com/api/hotlist?type=xiaohongshuHot',
    ]

    for url in hot_urls:
        raw = fetch_url(url, headers={'User-Agent': UA_DESKTOP})
        if not raw:
            continue
        try:
            data = json.loads(raw)
            data_list = data.get('data', []) or []
            for item in data_list:
                title = item.get('title', '') or item.get('name', '') or ''
                hot = item.get('hot', 0) or item.get('hotValue', 0) or 0
                desc = item.get('desc', '') or item.get('description', '') or ''
                if title and is_finance_related(title + desc):
                    items.append({
                        'title': title,
                        'summary': desc,
                        'likes': int(hot) if hot else 0,
                        'platform': '小红书',
                        'source_type': '热搜',
                    })
            if items:
                break
        except Exception as e:
            print(f"    [WARN] 小红书热榜解析失败: {e}", file=sys.stderr)

    print(f"    ✅ 小红书财经相关: {len(items)} 条")
    return items


def fetch_xiaohongshu_finance_notes():
    """
    小红书财经笔记搜索 (公开页面提取)
    """
    print("  📝 小红书财经笔记搜索...")
    items = []
    search_keywords = ['基金推荐', 'AI算力投资', '黄金还能买吗', '新能源基金', '消费基金', '港股ETF',
                        '石油投资', '伊朗局势经济', '中东战争影响', 'AI对经济影响', '科技革命']

    for kw in search_keywords:
        url = f'https://www.xiaohongshu.com/search_result?keyword={quote(kw)}&type=51'
        raw = fetch_url(url, headers={
            'User-Agent': UA_DESKTOP,
            'Referer': 'https://www.xiaohongshu.com/',
        })
        if not raw:
            continue
        try:
            # 尝试从HTML中提取初始化数据
            json_match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>', raw, re.DOTALL)
            if json_match:
                # 小红书使用 undefined 替代, 需要替换
                raw_json = json_match.group(1).replace('undefined', 'null')
                data = json.loads(raw_json)
                notes = data.get('search', {}).get('notes', {}).get('items', []) or []
                for note in notes[:3]:
                    note_data = note.get('noteCard', note.get('note', {}))
                    title = note_data.get('title', '') or note_data.get('displayTitle', '') or ''
                    desc = note_data.get('desc', '') or ''
                    liked = note_data.get('interactInfo', {}).get('likedCount', 0) or 0
                    if title:
                        items.append({
                            'title': title[:80],
                            'summary': desc[:200] if desc else title,
                            'likes': int(liked) if liked else 0,
                            'platform': '小红书',
                            'source_type': '笔记搜索',
                        })
        except Exception as e:
            print(f"    [WARN] 小红书搜索 '{kw}' 失败: {e}", file=sys.stderr)
        time.sleep(0.5)

    print(f"    ✅ 小红书笔记搜索: {len(items)} 条")
    return items


# ==================== 微博财经热搜 (补充源) ====================

def fetch_weibo_finance_hot():
    """微博财经热搜 (纯公开API, 补充社交媒体维度)"""
    print("  🐦 微博财经热搜...")
    items = []

    # 微博热搜API
    urls = [
        'https://weibo.com/ajax/side/hotSearch',
        'https://api.vvhan.com/api/hotlist/wbHot',
    ]

    for url in urls:
        raw = fetch_url(url, headers={
            'User-Agent': UA_DESKTOP,
            'Referer': 'https://weibo.com/',
        })
        if not raw:
            continue
        try:
            data = json.loads(raw)
            # 微博官方API格式
            realtime = data.get('data', {}).get('realtime', []) or []
            if realtime:
                for item in realtime:
                    word = item.get('word', '') or item.get('note', '') or ''
                    num = item.get('num', 0) or item.get('raw_hot', 0) or 0
                    label_name = item.get('label_name', '') or ''
                    if word and is_finance_related(word):
                        items.append({
                            'title': word,
                            'summary': label_name,
                            'likes': int(num) if num else 0,
                            'platform': '微博',
                            'source_type': '热搜',
                        })
                if items:
                    break

            # 第三方API格式
            data_list = data.get('data', []) if isinstance(data.get('data'), list) else []
            for item in data_list:
                title = item.get('title', '') or item.get('name', '') or ''
                hot = item.get('hot', 0) or item.get('hotValue', 0) or 0
                if title and is_finance_related(title):
                    items.append({
                        'title': title,
                        'summary': item.get('desc', '') or '',
                        'likes': int(hot) if hot else 0,
                        'platform': '微博',
                        'source_type': '热搜',
                    })
            if items:
                break
        except Exception as e:
            print(f"    [WARN] 微博热搜解析失败: {e}", file=sys.stderr)

    print(f"    ✅ 微博财经相关: {len(items)} 条")
    return items


# ==================== 东方财富/同花顺社区舆情 ====================

def fetch_eastmoney_community():
    """东方财富股吧热门话题 (纯财经社区)"""
    print("  💬 东方财富股吧热帖...")
    items = []

    # 股吧热帖
    urls = [
        'https://guba.eastmoney.com/interface/GetData.aspx?path=newtopic/api/Topic/HomePageListRead&param=ps%3D30%26p%3D1',
        'https://gbapi.eastmoney.com/senti/api/Topic/GetHotTopicList?ps=30&p=1',
    ]

    # 东方财富7x24人气榜
    raw = fetch_url('https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?type=0&client=web&maxNewsId=0&pageSize=30&column=102')
    if raw:
        try:
            data = json.loads(raw)
            data_obj = data.get('data') or {}
            if isinstance(data_obj, dict):
                news_list = data_obj.get('list', []) or []
            else:
                news_list = []
            for item in news_list:
                title = (item.get('title') or '').strip()
                content = (item.get('content') or '').strip()
                text = title or content[:100]
                if text and len(text) >= 6 and is_finance_related(text):
                    items.append({
                        'title': text[:80],
                        'summary': content[:200] if content else text,
                        'likes': 0,
                        'platform': '东方财富',
                        'source_type': '快讯',
                    })
        except Exception as e:
            print(f"    [WARN] 东方财富解析失败: {e}", file=sys.stderr)

    print(f"    ✅ 东方财富: {len(items)} 条")
    return items


# ==================== 综合财经社交热点 (兜底) ====================

def fetch_tophub_finance():
    """
    今日热榜聚合 - 财经相关平台
    覆盖: 36氪、虎嗅、财联社等
    """
    print("  🔥 聚合财经热榜...")
    items = []

    tophub_sources = [
        ('https://api.vvhan.com/api/hotlist/36Ke', '36氪'),
        ('https://api.vvhan.com/api/hotlist/huXiu', '虎嗅'),
        ('https://api.vvhan.com/api/hotlist/zhihuHot', '知乎'),
        ('https://api.vvhan.com/api/hotlist/baiduRD', '百度'),
        ('https://api.vvhan.com/api/hotlist/bili', 'B站'),
    ]

    for url, platform in tophub_sources:
        raw = fetch_url(url, headers={'User-Agent': UA_DESKTOP})
        if not raw:
            continue
        try:
            data = json.loads(raw)
            data_list = data.get('data', []) or []
            for item in data_list:
                title = item.get('title', '') or item.get('name', '') or ''
                hot = item.get('hot', 0) or item.get('hotValue', 0) or 0
                desc = item.get('desc', '') or ''
                if title and is_finance_related(title + desc):
                    items.append({
                        'title': title[:80],
                        'summary': desc[:200] if desc else '',
                        'likes': int(hot) if hot else 0,
                        'platform': platform,
                        'source_type': '热榜',
                    })
        except Exception as e:
            print(f"    [WARN] {platform} 解析失败: {e}", file=sys.stderr)
        time.sleep(0.3)

    print(f"    ✅ 聚合热榜财经相关: {len(items)} 条")
    return items


# ==================== 数据处理 ====================

def deduplicate(items):
    """去重 (基于标题相似度)"""
    seen = set()
    unique = []
    for item in items:
        # 简单去重: 取标题前20字
        key = re.sub(r'\W', '', item['title'])[:20]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def estimate_sentiment(item):
    """基于标题关键词估算情绪标签"""
    title = item.get('title', '') + ' ' + item.get('summary', '')

    # 极度看多信号
    if re.search(r'暴涨|疯涨|大涨|飙升|涨停|全仓|梭哈|起飞|爆发|牛市|创新高', title):
        return '极度看多'
    # 看多
    if re.search(r'上涨|走高|反弹|利好|加仓|机会|突破|看好|推荐|配置', title):
        return '偏多'
    # 极度看空
    if re.search(r'暴跌|崩盘|大跌|跳水|清仓|割肉|熊市|腰斩|崩', title):
        return '极度悲观'
    # 看空
    if re.search(r'下跌|走低|利空|减仓|风险|警惕|谨慎|回调|承压', title):
        return '偏空'
    # 中性
    if re.search(r'震荡|分歧|观望|持平|稳定|盘整', title):
        return '中性'

    return '中性偏多'


def estimate_creator_type(item):
    """估算内容来源类型"""
    platform = item.get('platform', '')
    source_type = item.get('source_type', '')

    if platform in ['东方财富', '财联社']:
        return '财经资讯平台'
    if source_type == '热搜':
        return '社交媒体热搜'
    if source_type == '视频搜索':
        return '短视频创作者'
    if source_type == '笔记搜索':
        return '理财博主'
    if platform == '36氪':
        return '科技财经媒体'
    if platform == '虎嗅':
        return '商业分析师'
    if platform == '知乎':
        return '知识社区用户'
    if platform == 'B站':
        return 'UP主/视频创作者'
    return '财经博主'


def process_items(all_items):
    """处理所有抓取结果, 生成最终输出"""
    # 去重
    unique = deduplicate(all_items)

    # 过滤娱乐/体育无关内容
    before_filter = len(unique)
    unique = [item for item in unique if not is_noise_category(item.get('title', '') + ' ' + item.get('summary', ''))]
    noise_filtered = before_filter - len(unique)
    if noise_filtered > 0:
        print(f"    🗑️ 过滤娱乐/体育噪音: {noise_filtered} 条")

    # 按热度排序
    unique.sort(key=lambda x: x.get('likes', 0), reverse=True)

    # 过滤噪音
    filtered = []
    for item in unique:
        ns = noise_score(item.get('title', ''))
        if ns >= 2:
            item['noise_flag'] = True
        filtered.append(item)

    # 添加估算字段
    result = []
    for i, item in enumerate(filtered[:50]):  # 最多50条
        entry = {
            'id': gen_id(item['title'] + str(i)),
            'platform': item.get('platform', '未知'),
            'title': item.get('title', ''),
            'summary': item.get('summary', '') or item.get('title', ''),
            'likes': item.get('likes', 0),
            'shares': item.get('shares', 0),
            'comments_count': item.get('comments_count', 0),
            'sentiment': estimate_sentiment(item),
            'main_opinion': item.get('summary', '')[:50] if item.get('summary') else '',
            'creator_type': estimate_creator_type(item),
            'source_type': item.get('source_type', ''),
            'publish_time': NOW.isoformat(),
            'noise_flag': item.get('noise_flag', False),
        }
        result.append(entry)

    return result


# ==================== 趋势主题提取 ====================

SOCIAL_TREND_THEMES = [
    {'id': 'ai_tech',      'name': 'AI/科技革命',     'icon': '🤖', 'keywords': ['AI', '人工智能', '算力', '芯片', '半导体', '大模型', 'DeepSeek', '光模块', 'CPO', '机器人', '自动驾驶', '英伟达', 'NVIDIA', 'AMD', '科技']},
    {'id': 'gold_metal',   'name': '黄金/贵金属',     'icon': '🥇', 'keywords': ['黄金', '金价', '白银', '银价', '贵金属', '铂金', '钯金']},
    {'id': 'oil_energy',   'name': '石油/能源',       'icon': '🛢️', 'keywords': ['原油', '油价', '石油', '天然气', 'OPEC', '能源', '电价', '气价']},
    {'id': 'geopolitics',  'name': '地缘政治',       'icon': '🌍', 'keywords': ['伊朗', '中东', '俄乌', '制裁', '战争', '冲突', '地缘', '关税', '贸易战', '军事', '以色列', '叙利亚', '台海', '南海', '中美关系']},
    {'id': 'macro_policy', 'name': '宏观政策',       'icon': '🏛️', 'keywords': ['央行', '降息', '降准', 'LPR', '美联储', '加息', '通胀', 'CPI', 'GDP', 'PMI', '汇率', '人民币', '美元', '利率']},
    {'id': 'new_energy',   'name': '新能源/碳中和',   'icon': '☀️', 'keywords': ['新能源', '光伏', '锂电', '碳酸锂', '储能', '风电', '氢能', '充电桩', '比亚迪', '特斯拉']},
    {'id': 'military',     'name': '军工/国防',       'icon': '🚀', 'keywords': ['军工', '国防', '航天', '导弹', '卫星', '雷达', '舰船', '无人机']},
    {'id': 'consumption',  'name': '消费/医药',       'icon': '🏥', 'keywords': ['消费', '白酒', '医药', '创新药', 'CXO', '茅台', '食品饮料', '餐饮']},
    {'id': 'realestate',   'name': '房地产/基建',     'icon': '🏗️', 'keywords': ['地产', '房价', '楼市', '房地产', '基建', '建材', '水泥']},
    {'id': 'hk_us_stock',  'name': '港股/美股',       'icon': '🌐', 'keywords': ['港股', '恒生', '美股', '纳斯达克', '道琼斯', '标普']},
    {'id': 'a_share',      'name': 'A股/大盘',       'icon': '📈', 'keywords': ['A股', '股市', '大盘', '沪指', '上证', '深成', '创业板', '科创板']},
    {'id': 'fund_etf',     'name': '基金/ETF',       'icon': '💰', 'keywords': ['基金', 'ETF', '定投', '净值', '基民', '公募', '私募']},
    {'id': 'economy',      'name': '经济影响/趋势',   'icon': '📊', 'keywords': ['经济影响', '未来经济', '产业链', '供应链', '双循环', '科技革命', '数字经济', '碳中和', '就业', '失业率', '出口', '进口', '外贸', '通缩', '滞胀']},
]


def extract_social_trends(processed_items):
    """从处理后的社媒数据提取跨平台趋势主题"""
    import math
    theme_data = {}
    for th in SOCIAL_TREND_THEMES:
        theme_data[th['id']] = {
            'id': th['id'],
            'name': th['name'],
            'icon': th['icon'],
            'mention_count': 0,
            'total_engagement': 0,
            'platforms': set(),
            'sentiments': {'bullish': 0, 'bearish': 0, 'neutral': 0},
            'sample_titles': [],
            'keywords_hit': set(),
        }

    for item in processed_items:
        text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
        sentiment = item.get('sentiment', '中性')
        platform = item.get('platform', '')
        likes = item.get('likes', 0) or 0

        for th in SOCIAL_TREND_THEMES:
            matched = False
            for kw in th['keywords']:
                if kw.lower() in text:
                    matched = True
                    theme_data[th['id']]['keywords_hit'].add(kw)
            if matched:
                td = theme_data[th['id']]
                td['mention_count'] += 1
                td['total_engagement'] += likes
                td['platforms'].add(platform)
                if len(td['sample_titles']) < 5:
                    title = item.get('title', '')
                    if title and title not in td['sample_titles']:
                        td['sample_titles'].append(title)
                if '看多' in sentiment or '偏多' in sentiment or '乐观' in sentiment:
                    td['sentiments']['bullish'] += 1
                elif '看空' in sentiment or '偏空' in sentiment or '悲观' in sentiment:
                    td['sentiments']['bearish'] += 1
                else:
                    td['sentiments']['neutral'] += 1

    results = []
    for td in theme_data.values():
        if td['mention_count'] == 0:
            continue
        heat = td['mention_count'] * 10 + (math.log10(td['total_engagement'] + 1) * 5)
        s = td['sentiments']
        total_s = s['bullish'] + s['bearish'] + s['neutral']
        if total_s > 0:
            if s['bullish'] / total_s > 0.5:
                dom_sentiment = '偏多'
            elif s['bearish'] / total_s > 0.5:
                dom_sentiment = '偏空'
            else:
                dom_sentiment = '中性'
        else:
            dom_sentiment = '中性'
        results.append({
            'id': td['id'],
            'name': td['name'],
            'icon': td['icon'],
            'mention_count': td['mention_count'],
            'heat_score': round(heat, 1),
            'platforms': sorted(td['platforms']),
            'sentiment': dom_sentiment,
            'sentiment_detail': td['sentiments'],
            'sample_titles': td['sample_titles'][:3],
            'keywords_hit': sorted(td['keywords_hit'])[:8],
        })

    results.sort(key=lambda x: x['heat_score'], reverse=True)
    return results[:15]


# ==================== 主流程 ====================

def main():
    print(f"\n{'='*60}")
    print(f"📡 社交媒体舆情抓取 - {NOW.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    all_items = []

    # 1. 抖音热榜
    try:
        all_items.extend(fetch_douyin_hot())
    except Exception as e:
        print(f"  ❌ 抖音热榜异常: {e}", file=sys.stderr)

    # 2. 抖音财经视频搜索
    try:
        all_items.extend(fetch_douyin_finance_videos())
    except Exception as e:
        print(f"  ❌ 抖音视频搜索异常: {e}", file=sys.stderr)

    # 3. 小红书热点
    try:
        all_items.extend(fetch_xiaohongshu_hot())
    except Exception as e:
        print(f"  ❌ 小红书热点异常: {e}", file=sys.stderr)

    # 4. 小红书财经笔记
    try:
        all_items.extend(fetch_xiaohongshu_finance_notes())
    except Exception as e:
        print(f"  ❌ 小红书笔记异常: {e}", file=sys.stderr)

    # 5. 微博财经热搜
    try:
        all_items.extend(fetch_weibo_finance_hot())
    except Exception as e:
        print(f"  ❌ 微博热搜异常: {e}", file=sys.stderr)

    # 6. 东方财富社区
    try:
        all_items.extend(fetch_eastmoney_community())
    except Exception as e:
        print(f"  ❌ 东方财富异常: {e}", file=sys.stderr)

    # 7. 聚合财经热榜 (36氪/虎嗅/知乎/百度/B站)
    try:
        all_items.extend(fetch_tophub_finance())
    except Exception as e:
        print(f"  ❌ 聚合热榜异常: {e}", file=sys.stderr)

    print(f"\n  📊 总抓取: {len(all_items)} 条")

    # 处理
    processed = process_items(all_items)

    print(f"  📋 去重+过滤后: {len(processed)} 条")

    # 提取趋势主题
    trends = extract_social_trends(processed)
    print(f"  🔥 识别社媒趋势: {len(trends)} 个")
    for t in trends[:5]:
        print(f"      {t['icon']} {t['name']}: {t['mention_count']}条, 热度{t['heat_score']}")

    # 输出
    output = {
        'updated_at': NOW.isoformat(),
        'total_fetched': len(all_items),
        'total_processed': len(processed),
        'sources': list(set(item.get('platform', '') for item in processed)),
        'trends': trends,
        'videos': processed,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ 输出至: {OUTPUT_PATH}")
    print(f"  📱 平台覆盖: {', '.join(output['sources'])}")
    print(f"{'='*60}\n")

    return output


if __name__ == '__main__':
    main()
