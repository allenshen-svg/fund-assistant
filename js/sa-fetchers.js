// =============================================
// KOL vs 散户 情绪博弈分析 - Data Fetchers
// 2026-02 verified working APIs
// =============================================

const CORS_PROXIES = [
  url => url,  // direct first
  url => 'https://api.allorigins.win/raw?url=' + encodeURIComponent(url),
  url => 'https://corsproxy.io/?' + encodeURIComponent(url),
];

async function fetchJSON(url, opts={}) {
  // Try direct first, then CORS proxies
  for(const proxy of CORS_PROXIES) {
    try {
      const pUrl = proxy(url);
      const resp = await fetch(pUrl, {
        signal: AbortSignal.timeout(12000),
        mode: pUrl === url ? 'cors' : undefined,
        ...opts,
      });
      if(!resp.ok) continue;
      const data = await resp.json();
      if(data) return data;
    } catch(e) {
      // try next proxy
    }
  }
  console.warn('All fetch attempts failed:', url);
  return null;
}

// ==================== 抖音热搜 ====================
// Verified: aweme.snssdk.com/aweme/v1/hot/search/list/
async function fetchDouyin() {
  markSource('douyin', 'active');
  let items = [];
  try {
    const data = await fetchJSON('https://aweme.snssdk.com/aweme/v1/hot/search/list/');
    if(data) {
      const list = data?.data?.word_list || data?.word_list || [];
      for(const item of list) {
        const word = item.word || item.content || '';
        const hot = parseInt(item.hot_value || item.score || 0) || 0;
        if(word && isFinance(word)) {
          items.push({
            title: word.slice(0,80),
            summary: (item.word_cover?.url ? '' : '') + word,
            likes: hot,
            platform: '抖音',
            source_type: '热搜',
            sentiment: estimateSentiment(word),
            creator_type: '社交热搜',
            publish_time: new Date().toISOString(),
          });
        }
      }
    }
  } catch(e) { console.warn('Douyin fetch error:', e); }
  markSource('douyin', items.length > 0 ? 'done' : '');
  return items;
}

// ==================== 微博热搜 ====================
// Weibo frontend API requires cookie, so try multiple approaches
async function fetchWeibo() {
  markSource('weibo', 'active');
  let items = [];

  // Approach 1: weibo.com/ajax/side/hotSearch (may need cookie)
  try {
    const data = await fetchJSON('https://weibo.com/ajax/side/hotSearch');
    if(data?.data?.realtime) {
      for(const item of data.data.realtime) {
        const word = item.word || item.note || '';
        const hot = parseInt(item.raw_hot || item.num || 0) || 0;
        if(word && isFinance(word)) {
          items.push({
            title: word.slice(0,80),
            summary: word,
            likes: hot,
            platform: '微博',
            source_type: '热搜',
            sentiment: estimateSentiment(word),
            creator_type: '微博热搜',
            publish_time: new Date().toISOString(),
          });
        }
      }
    }
  } catch(e) { console.warn('Weibo ajax error:', e); }

  // Approach 2: weibo mobile API
  if(items.length === 0) {
    try {
      const data = await fetchJSON('https://m.weibo.cn/api/container/getIndex?containerid=106003type%3D25%26t%3D3%26disable_hot%3D1%26filter_type%3Drealtimehot');
      if(data?.data?.cards) {
        for(const card of data.data.cards) {
          const groups = card?.card_group || [];
          for(const g of groups) {
            const word = g?.desc || '';
            const hot = parseInt(g?.desc_extr || 0) || 0;
            if(word && isFinance(word)) {
              items.push({
                title: word.slice(0,80),
                summary: word,
                likes: hot,
                platform: '微博',
                source_type: '热搜',
                sentiment: estimateSentiment(word),
                creator_type: '微博热搜',
                publish_time: new Date().toISOString(),
              });
            }
          }
        }
      }
    } catch(e) { console.warn('Weibo mobile error:', e); }
  }

  markSource('weibo', items.length > 0 ? 'done' : '');
  return items;
}

// ==================== 东方财富 7x24 快讯 ====================
// Verified: column=350 with biz & req_trace params
async function fetchEastmoney() {
  markSource('eastmoney', 'active');
  let items = [];
  try {
    const data = await fetchJSON(
      'https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_724&column=350&pageSize=50&maxNewsId=0&type=0&req_trace=sa_' + Date.now()
    );
    if(data?.data?.list) {
      for(const item of data.data.list) {
        const title = (item.title || '').trim();
        const content = (item.content || '').trim();
        const text = title || content.slice(0,100);
        if(text && text.length >= 4) {
          items.push({
            title: text.slice(0,80),
            summary: content.slice(0,200) || text,
            likes: 0,
            platform: '东方财富',
            source_type: '快讯',
            sentiment: estimateSentiment(text + ' ' + content),
            creator_type: '财经资讯平台',
            publish_time: item.showTime || new Date().toISOString(),
          });
        }
      }
    }
  } catch(e) { console.warn('Eastmoney fetch failed:', e); }
  markSource('eastmoney', items.length > 0 ? 'done' : '');
  return items;
}

// ==================== 财联社电报 ====================
// Verified: cls.cn/nodeapi/updateTelegraphList
async function fetchCailian() {
  let items = [];
  try {
    const data = await fetchJSON(
      'https://www.cls.cn/nodeapi/updateTelegraphList?app=CailianpressWeb&os=web&sv=8.4.6&rn=30'
    );
    if(data?.data?.roll_data) {
      for(const item of data.data.roll_data) {
        const title = (item.title || '').trim();
        const content = (item.content || '').replace(/<[^>]+>/g,'').trim();
        const text = title || content.slice(0,100);
        if(text && text.length >= 4) {
          items.push({
            title: text.slice(0,80),
            summary: content.slice(0,200) || text,
            likes: 0,
            platform: '财联社',
            source_type: '电报',
            sentiment: estimateSentiment(text + ' ' + content),
            creator_type: '财经资讯平台',
            publish_time: item.ctime ? new Date(item.ctime * 1000).toISOString() : new Date().toISOString(),
          });
        }
      }
    }
  } catch(e) { console.warn('Cailian fetch failed:', e); }
  return items;
}

// ==================== 知乎热榜 ====================
// Verified: api.zhihu.com/topstory/hot-lists/total
async function fetchZhihu() {
  let items = [];
  try {
    const data = await fetchJSON('https://api.zhihu.com/topstory/hot-lists/total?limit=50');
    if(data?.data) {
      for(const item of data.data) {
        const title = item?.target?.title || '';
        const excerpt = item?.target?.excerpt || '';
        const hot = parseInt(item?.detail_text?.replace(/[^\d]/g,'') || 0) || 0;
        if(title && isFinance(title + ' ' + excerpt)) {
          items.push({
            title: title.slice(0,80),
            summary: excerpt.slice(0,200) || title,
            likes: hot,
            platform: '知乎',
            source_type: '热榜',
            sentiment: estimateSentiment(title + ' ' + excerpt),
            creator_type: '聚合热榜',
            publish_time: new Date().toISOString(),
          });
        }
      }
    }
  } catch(e) { console.warn('Zhihu fetch failed:', e); }
  return items;
}

// ==================== 百度热搜 ====================
// Verified: top.baidu.com/api/board
async function fetchBaidu() {
  let items = [];
  try {
    const data = await fetchJSON('https://top.baidu.com/api/board?platform=wise&tab=realtime');
    if(data?.data?.cards) {
      // Baidu has nested structure: cards[0].content[0].content[]
      let list = [];
      for(const card of data.data.cards) {
        const content = card?.content;
        if(!Array.isArray(content)) continue;
        for(const c of content) {
          if(Array.isArray(c?.content)) {
            list = list.concat(c.content);
          } else if(c?.word) {
            list.push(c);
          }
        }
      }
      for(const item of list) {
        const word = item.word || '';
        const desc = item.desc || '';
        const hot = parseInt(item.hotScore || item.rawUrl?.match(/hot=(\d+)/)?.[1] || 0) || 0;
        if(word && isFinance(word + ' ' + desc)) {
          items.push({
            title: word.slice(0,80),
            summary: desc.slice(0,200) || word,
            likes: hot,
            platform: '百度',
            source_type: '热搜',
            sentiment: estimateSentiment(word + ' ' + desc),
            creator_type: '聚合热榜',
            publish_time: new Date().toISOString(),
          });
        }
      }
    }
  } catch(e) { console.warn('Baidu fetch failed:', e); }
  return items;
}

// ==================== B站热搜 + 排行 ====================
// Verified: s.search.bilibili.com/main/hotword + ranking API
async function fetchBilibili() {
  let items = [];
  try {
    // Hot search words
    const hw = await fetchJSON('https://s.search.bilibili.com/main/hotword');
    if(hw?.list) {
      for(const item of hw.list) {
        const kw = item.keyword || '';
        if(kw && isFinance(kw)) {
          items.push({
            title: kw.slice(0,80),
            summary: kw,
            likes: parseInt(item.heat_score || 0) || 0,
            platform: 'B站',
            source_type: '热搜',
            sentiment: estimateSentiment(kw),
            creator_type: '聚合热榜',
            publish_time: new Date().toISOString(),
          });
        }
      }
    }
    // Ranking videos
    const rank = await fetchJSON('https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all');
    if(rank?.data?.list) {
      for(const item of rank.data.list) {
        const title = item.title || '';
        const desc = item.desc || '';
        const views = item?.stat?.view || 0;
        if(title && isFinance(title + ' ' + desc)) {
          items.push({
            title: title.slice(0,80),
            summary: desc.slice(0,200) || title,
            likes: views,
            platform: 'B站',
            source_type: '排行',
            sentiment: estimateSentiment(title + ' ' + desc),
            creator_type: '聚合热榜',
            publish_time: new Date().toISOString(),
          });
        }
      }
    }
  } catch(e) { console.warn('Bilibili fetch failed:', e); }
  return items;
}

// ==================== 聚合热榜 (知乎+百度+B站+财联社) ====================
async function fetchAggHotlists() {
  markSource('tophub', 'active');
  let all = [];
  const results = await Promise.allSettled([
    fetchZhihu(),
    fetchBaidu(),
    fetchBilibili(),
  ]);
  for(const r of results) {
    if(r.status === 'fulfilled' && r.value.length > 0) {
      all = all.concat(r.value);
    }
  }
  markSource('tophub', all.length > 0 ? 'done' : '');
  return all;
}

// ==================== 小红书热搜 ====================
// XHS API requires signatures; try public endpoint, fallback gracefully
async function fetchXiaohongshu() {
  markSource('xiaohongshu', 'active');
  let items = [];
  try {
    const data = await fetchJSON('https://edith.xiaohongshu.com/api/sns/v1/search/hot_list');
    if(data?.data?.items || data?.data?.list) {
      const list = data.data.items || data.data.list || [];
      for(const item of list) {
        const word = item.word || item.title || item.name || '';
        const hot = parseInt(item.score || item.hot || 0) || 0;
        if(word && isFinance(word)) {
          items.push({
            title: word.slice(0,80),
            summary: word,
            likes: hot,
            platform: '小红书',
            source_type: '热搜',
            sentiment: estimateSentiment(word),
            creator_type: '社交热搜',
            publish_time: new Date().toISOString(),
          });
        }
      }
    }
  } catch(e) { console.warn('XHS fetch failed:', e); }
  markSource('xiaohongshu', items.length > 0 ? 'done' : '');
  return items;
}

// ==================== 预存数据加载 ====================
async function fetchPrebuiltData() {
  try {
    const resp = await fetch('data/social_media_videos.json?t=' + Date.now());
    if(!resp.ok) return [];
    const data = await resp.json();
    return data.videos || [];
  } catch(e) { return []; }
}

// ==================== 情绪估算 ====================
function estimateSentiment(text) {
  if(!text) return '中性';
  if(/暴涨|疯涨|大涨|飙升|涨停|全仓|梭哈|起飞|爆发|牛市|创新高|狂热/.test(text)) return '极度看多';
  if(/上涨|走高|反弹|利好|加仓|机会|突破|看好|推荐|配置|走强/.test(text)) return '偏多';
  if(/暴跌|崩盘|大跌|跳水|清仓|割肉|熊市|腰斩/.test(text)) return '极度悲观';
  if(/下跌|走低|利空|减仓|风险|警惕|谨慎|回调|承压|重挫/.test(text)) return '偏空';
  if(/震荡|分歧|观望|持平|稳定|盘整/.test(text)) return '中性';
  return '中性偏多';
}

// ==================== 去重 ====================
function dedup(items) {
  const seen = new Set();
  return items.filter(item => {
    const key = (item.title||'').replace(/[\W\s]/g,'').slice(0,20);
    if(!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
