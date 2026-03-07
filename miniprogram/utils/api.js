const { fallbackHotEvents } = require('../data/fallback-hot-events');

function normalizeEventText(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[\s\u3000]+/g, '')
    .replace(/[“”"'`·•:：，,。！？!？（）()【】\[\]、;；\-—]/g, '');
}

function dedupeHotEvents(events) {
  if (!Array.isArray(events) || events.length <= 1) return Array.isArray(events) ? events : [];

  const bestByKey = new Map();
  events.forEach((evt, idx) => {
    const titleKey = normalizeEventText(evt && evt.title);
    const reasonKey = normalizeEventText(evt && evt.reason).slice(0, 24);
    const categoryKey = String((evt && evt.category) || '');
    const conceptsKey = Array.isArray(evt && evt.concepts)
      ? evt.concepts.map(normalizeEventText).filter(Boolean).sort().join('|')
      : '';

    const key = titleKey
      ? `${titleKey}|${categoryKey}|${reasonKey || conceptsKey}`
      : `__idx_${idx}`;

    const current = bestByKey.get(key);
    if (!current) {
      bestByKey.set(key, evt);
      return;
    }

    const currentScore = Math.abs(Number(current.impact || 0)) * 100 + Number(current.confidence || 0);
    const nextScore = Math.abs(Number((evt && evt.impact) || 0)) * 100 + Number((evt && evt.confidence) || 0);
    if (nextScore > currentScore) {
      bestByKey.set(key, evt);
    }
  });

  return Array.from(bestByKey.values());
}

function normalizeHotEventsPayload(data) {
  const payload = data || {};
  return {
    ...payload,
    events: dedupeHotEvents(payload.events || []),
  };
}

/**
 * 获取热点事件数据
 */
function fetchHotEvents(settings) {
  if (!settings.useRemote) {
    return Promise.resolve({ source: 'local', data: normalizeHotEventsPayload(fallbackHotEvents) });
  }
  const base = String(settings.apiBase || '').replace(/\/$/, '');
  const url = `${base}/data/hot_events.json?_t=${Date.now()}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      method: 'GET',
      timeout: 8000,
      success(res) {
        const ok = res.statusCode >= 200 && res.statusCode < 300;
        const hasShape = res.data && Array.isArray(res.data.heatmap);
        if (ok && hasShape) {
          resolve({ source: 'remote', data: normalizeHotEventsPayload(res.data) });
          return;
        }
        resolve({ source: 'local', data: normalizeHotEventsPayload(fallbackHotEvents) });
      },
      fail() {
        resolve({ source: 'local', data: normalizeHotEventsPayload(fallbackHotEvents) });
      }
    });
  });
}

/**
 * 通过东方财富 JSONP 获取指数实时行情
 * 小程序不支持 JSONP，改用 wx.request 直取 JSON 接口
 */
function fetchIndices(codes) {
  const secids = codes.map(c => c.code).join(',');
  const url = `https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f12,f14&secids=${secids}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 6000,
      success(res) {
        if (res.data && res.data.data && res.data.data.diff) {
          const list = res.data.data.diff.map(d => ({
            code: d.f12,
            name: d.f14,
            price: d.f2,
            pct: d.f3,
            change: d.f4
          }));
          resolve(list);
        } else {
          resolve([]);
        }
      },
      fail() { resolve([]); }
    });
  });
}

/**
 * 获取基金实时估值 (天天基金)
 */
function fetchFundEstimate(fundCode) {
  const url = `https://fundgz.1234567.com.cn/js/${fundCode}.js?rt=${Date.now()}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 5000,
      header: { 'Referer': 'https://fund.eastmoney.com/' },
      success(res) {
        try {
          // 返回格式: jsonpgz({...});
          const text = typeof res.data === 'string' ? res.data : '';
          const match = text.match(/\{.*\}/);
          if (match) {
            const obj = JSON.parse(match[0]);
            resolve({
              code: obj.fundcode,
              name: obj.name,
              nav: parseFloat(obj.dwjz),
              estimate: parseFloat(obj.gsz),
              pct: parseFloat(obj.gszzl),
              time: obj.gztime
            });
            return;
          }
        } catch (e) {}
        resolve(null);
      },
      fail() { resolve(null); }
    });
  });
}

/**
 * 批量获取多只基金估值
 */
async function fetchMultiFundEstimates(codes) {
  const results = {};
  const tasks = codes.map(code =>
    fetchFundEstimate(code).then(r => { if (r) results[code] = r; })
  );
  await Promise.allSettled(tasks);
  return results;
}

/**
 * 获取板块资金流向
 */
function fetchSectorFlows() {
  const url = 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&po=1&np=1&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87';
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 6000,
      success(res) {
        if (res.data && res.data.data && res.data.data.diff) {
          const list = res.data.data.diff.map(d => ({
            code: d.f12,
            name: d.f14,
            pct: d.f3,
            mainNet: d.f62,       // 主力净流入
            mainPct: d.f184,      // 主力净占比
          }));
          resolve(list);
        } else {
          resolve([]);
        }
      },
      fail() { resolve([]); }
    });
  });
}

/**
 * 获取基金历史净值 (东方财富)
 * 返回 [{date, nav}] 按时间升序，最多取250条
 *
 * 策略：
 *   1. 优先使用天天基金移动端 API（支持所有场外基金，纯 JSON，无需 Referer）
 *   2. 备选使用 push2his kline 接口（场内/LOF 基金可用）
 */
function getSecidForFund(code) {
  const first = code.charAt(0);
  if (first === '5' || first === '6') return `1.${code}`;
  return `0.${code}`;
}

function fetchFundHistory(fundCode, pageSize) {
  pageSize = pageSize || 250;

  // —— 方式1: 天天基金移动端 API（全场外基金通用，纯 JSON） ——
  function tryMobileApi() {
    const url = `https://fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList?pageIndex=1&pageSize=${pageSize}&plat=Android&appType=ttjj&product=EFund&Version=1&deviceid=1&FCODE=${fundCode}`;
    return new Promise((resolve) => {
      wx.request({
        url,
        timeout: 10000,
        success(res) {
          try {
            const datas = res.data && res.data.Datas;
            if (datas && datas.length > 0) {
              const navList = datas
                .filter(d => d.DWJZ && !isNaN(parseFloat(d.DWJZ)))
                .map(d => ({ date: d.FSRQ, nav: parseFloat(d.DWJZ) }))
                .reverse();
              if (navList.length > 0) { resolve(navList); return; }
            }
            resolve(null);
          } catch (e) { resolve(null); }
        },
        fail() { resolve(null); }
      });
    });
  }

  // —— 方式2: push2his kline 接口（场内基金/LOF，无需 Referer） ——
  function tryKline() {
    const secid = getSecidForFund(fundCode);
    const url = `https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=${secid}&fields1=f1,f2,f3&fields2=f51,f52,f53&klt=101&fqt=1&beg=0&end=20500101&lmt=${pageSize}`;
    return new Promise((resolve) => {
      wx.request({
        url,
        timeout: 8000,
        success(res) {
          try {
            const klines = res.data && res.data.data && res.data.data.klines;
            if (klines && klines.length > 0) {
              const navList = klines.map(k => {
                const parts = k.split(',');
                return { date: parts[0], nav: parseFloat(parts[1]) };
              }).filter(d => !isNaN(d.nav));
              if (navList.length > 0) { resolve(navList); return; }
            }
            resolve(null);
          } catch (e) { resolve(null); }
        },
        fail() { resolve(null); }
      });
    });
  }

  return tryMobileApi().then(result => {
    if (result && result.length > 0) return result;
    return tryKline();
  }).then(result => result || []);
}

/**
 * 批量获取多只基金历史净值
 */
async function fetchMultiFundHistory(codes) {
  const results = {};
  const tasks = codes.map(code =>
    fetchFundHistory(code, 120).then(r => { if (r && r.length > 0) results[code] = r; })
  );
  await Promise.allSettled(tasks);
  return results;
}

/**
 * 获取大宗商品期货实时行情 (东方财富期货推送接口)
 */
function fetchCommodities(codes) {
  const secids = codes.map(c => c.code).join(',');
  const url = `https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f12,f14&secids=${secids}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 6000,
      success(res) {
        if (res.data && res.data.data && res.data.data.diff) {
          const list = res.data.data.diff.map((d, i) => ({
            code: codes[i] ? codes[i].code : d.f12,
            name: codes[i] ? codes[i].name : d.f14,
            short: codes[i] ? codes[i].short : '',
            icon: codes[i] ? codes[i].icon : '',
            group: codes[i] ? codes[i].group : '',
            price: d.f2,
            pct: d.f3,
            change: d.f4
          }));
          resolve(list);
        } else {
          resolve([]);
        }
      },
      fail() { resolve([]); }
    });
  });
}

/**
 * 获取后端 API 基址（Flask 服务器）
 * 优先用 settings.serverUrl（云服务器 / 本地开发地址），
 * 如果未配置则返回空串（此时手动刷新不可用）
 */
function _getServerBase(settings) {
  if (settings && settings.serverUrl) return settings.serverUrl.replace(/\/$/, '');
  // apiBase 指向 GitHub Pages 的不作为服务器地址
  const base = String((settings && settings.apiBase) || '').replace(/\/$/, '');
  if (!base || /github\.io/i.test(base)) return '';
  return base;
}

/**
 * 触发后端重新采集舆情数据 (POST /api/refresh)
 */
function triggerRefresh(settings) {
  const base = _getServerBase(settings);
  const url = `${base}/api/refresh`;
  return new Promise((resolve) => {
    wx.request({
      url,
      method: 'POST',
      timeout: 10000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data) {
          resolve(res.data);
        } else {
          resolve({ status: 'error', message: '请求失败' });
        }
      },
      fail() { resolve({ status: 'error', message: '网络错误' }); }
    });
  });
}

/**
 * 触发后端重新 AI 分析 (POST /api/reanalyze)
 */
function triggerReanalyze(settings) {
  const base = _getServerBase(settings);
  const url = `${base}/api/reanalyze`;
  return new Promise((resolve) => {
    wx.request({
      url,
      method: 'POST',
      timeout: 10000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data) {
          resolve(res.data);
        } else {
          resolve({ status: 'error', message: '请求失败' });
        }
      },
      fail() { resolve({ status: 'error', message: '网络错误' }); }
    });
  });
}

/**
 * 按基金代码查找基金信息
 * 先查本地 FUND_DB，再查天天基金实时接口
 * 返回 { code, name, type, nav, estimate, pct, source } 或 null
 */
function searchFundByCode(fundCode) {
  if (!fundCode || !/^\d{6}$/.test(fundCode)) {
    return Promise.resolve(null);
  }
  // 1. 先查内置数据库
  const app = getApp();
  const db = (app && app.globalData && app.globalData.FUND_DB) || {};
  const local = db[fundCode];
  if (local) {
    return Promise.resolve({
      code: fundCode,
      name: local.name,
      type: local.type || _guessType(local.name),
      nav: null,
      estimate: null,
      pct: null,
      source: 'local',
    });
  }
  // 2. 调天天基金实时估值接口
  return fetchFundEstimate(fundCode).then(r => {
    if (r && r.name) {
      return {
        code: r.code || fundCode,
        name: r.name,
        type: _guessType(r.name),
        nav: r.nav,
        estimate: r.estimate,
        pct: r.pct,
        source: 'remote',
      };
    }
    return null;
  });
}

/**
 * 根据基金名称猜测基金类型
 */
function _guessType(name) {
  if (!name) return '其他';
  const rules = [
    [/黄金/,         '黄金'],
    [/白银/,         '有色金属'],
    [/有色|铜|铝|稀土/, '有色金属'],
    [/原油|石油|油气|石化|能源化工/, '原油'],
    [/半导体|芯片|集成电路/, '半导体'],
    [/军工|国防|航天|航空/, '军工'],
    [/白酒|食品|饮料/,  '白酒/消费'],
    [/医药|医疗|生物|创新药/, '医药'],
    [/新能源|光伏|锂电|碳中和|储能|风电/, '新能源'],
    [/科技|人工智能|AI|云计算|大数据|信息|互联网|计算机|软件|机器人|通信|5G/, 'AI/科技'],
    [/红利|股息|高息/,  '红利'],
    [/港股科技|港股互联网|恒生科技/,  '港股科技'],
    [/港股|恒生|H股/,  'QDII'],
    [/债|纯债|信用|利率/, '债券'],
    [/沪深300|中证500|中证1000|A500|上证50|创业板|科创|中证100|万得全A|MSCI/, '宽基'],
    [/蓝筹|价值|龙头/,  '蓝筹'],
    [/QDII|纳斯达克|标普|美股|中概/, '蓝筹/QDII'],
  ];
  for (const [re, type] of rules) {
    if (re.test(name)) return type;
  }
  return '其他';
}

/**
 * 获取板块内 TOP 基金排行（按近3月收益排序）
 * 使用东方财富基金排行接口
 * sectorName: 板块名称用于关键词过滤
 * topN: 返回前N只
 */
function fetchSectorTopFunds(sectorName, topN) {
  topN = topN || 5;
  // 板块名 → 天天基金分类搜索关键词映射
  const SECTOR_FUND_KW = {
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
  };
  const kw = SECTOR_FUND_KW[sectorName] || sectorName;

  // 东方财富基金搜索接口 - 按近3月收益降序
  const url = `https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchPageByField.ashx?key=${encodeURIComponent(kw)}&pageindex=1&pagesize=${topN}&Sort=SYL_3Y&SortType=Desc&_=${Date.now()}`;

  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 8000,
      success(res) {
        try {
          const datas = res.data && res.data.Datas;
          if (datas && datas.length > 0) {
            const list = datas.slice(0, topN).map(d => ({
              code: d.CODE || '',
              name: d.NAME || '',
              type: _guessType(d.NAME || ''),
            }));
            resolve(list);
            return;
          }
        } catch (e) {}
        resolve([]);
      },
      fail() { resolve([]); }
    });
  });
}

/**
 * 获取板块内领涨个股（东方财富板块成分股接口）
 * sectorCode: 板块代码 (如 BK0477)
 * topN: 返回前N只
 */
function fetchSectorTopStocks(sectorCode, topN) {
  topN = topN || 5;
  const url = `https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=${topN}&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:${sectorCode}&fields=f12,f14,f2,f3,f4,f15,f16,f17,f20,f115`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 8000,
      success(res) {
        try {
          const diff = res.data && res.data.data && res.data.data.diff;
          if (diff && diff.length > 0) {
            const list = diff.slice(0, topN).map(d => ({
              code: d.f12,
              name: d.f14,
              price: d.f2,
              pct: d.f3,
              high: d.f15,
              low: d.f16,
              open: d.f17,
              marketCap: d.f20, // 总市值
              pe: d.f115,       // 市盈率
            }));
            resolve(list);
            return;
          }
        } catch (e) {}
        resolve([]);
      },
      fail() { resolve([]); }
    });
  });
}

/**
 * 从服务器获取 AI 选基金/股票结果（每日 14:50 自动生成）
 * GET /api/fund-pick
 */
function fetchServerFundPick(settings) {
  const base = _getServerBase(settings);
  if (!base) return Promise.resolve(null);
  const url = `${base}/api/fund-pick?_t=${Date.now()}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 8000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data && res.data.result) {
          resolve(res.data);
        } else {
          resolve(null);
        }
      },
      fail() { resolve(null); }
    });
  });
}

/**
 * 获取实时突发新闻 + 全球市场异动
 * 返回 { breaking[], anomalies[], updated_at, meta }
 */
function fetchRealtimeBreaking(settings) {
  const base = String((settings && settings.apiBase) || '').replace(/\/$/, '');
  const url = `${base}/data/realtime_breaking.json?_t=${Date.now()}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 8000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data && Array.isArray(res.data.breaking)) {
          resolve(res.data);
        } else {
          // 兜底: 尝试 API 路由
          const apiUrl = `${base}/api/realtime-breaking?_t=${Date.now()}`;
          wx.request({
            url: apiUrl,
            timeout: 8000,
            success(apiRes) {
              if (apiRes.statusCode >= 200 && apiRes.statusCode < 300 && apiRes.data && Array.isArray(apiRes.data.breaking)) {
                resolve(apiRes.data);
              } else {
                resolve(null);
              }
            },
            fail() { resolve(null); }
          });
        }
      },
      fail() { resolve(null); }
    });
  });
}

module.exports = {
  fetchHotEvents,
  fetchIndices,
  fetchFundEstimate,
  fetchMultiFundEstimates,
  fetchSectorFlows,
  fetchFundHistory,
  fetchMultiFundHistory,
  fetchCommodities,
  fetchSentimentData,
  fetchAnalysisData,
  fetchUSMarketData,
  fetchSocialTrends,
  fetchRealtimeBreaking,
  triggerRefresh,
  triggerReanalyze,
  getServerBase: _getServerBase,
  searchFundByCode,
  fetchSectorTopFunds,
  fetchSectorTopStocks,
  fetchServerFundPick,
};

/**
 * 获取舆情采集数据 (sentiment_cache.json)
 * 返回 { items[], source_counts{}, total, fetch_time }
 */
function fetchSentimentData(settings) {
  const base = String((settings && settings.apiBase) || '').replace(/\/$/, '');
  const url = `${base}/data/sentiment_cache.json?_t=${Date.now()}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 12000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data && Array.isArray(res.data.items)) {
          resolve(res.data);
        } else {
          resolve(null);
        }
      },
      fail() { resolve(null); }
    });
  });
}

/**
 * 获取 AI 分析结果 (analysis_cache.json)
 * 返回 { raw_text, dashboard, radar_summary, kol_sections[], actions, analysis_time }
 */
function fetchAnalysisData(settings) {
  const base = String((settings && settings.apiBase) || '').replace(/\/$/, '');
  const url = `${base}/data/analysis_cache.json?_t=${Date.now()}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 12000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data && res.data.raw_text) {
          resolve(res.data);
        } else {
          resolve(null);
        }
      },
      fail() { resolve(null); }
    });
  });
}

/**
 * 获取隔夜美股行情 (us_market_cache.json)
 * 返回 { stocks[], fetch_time }
 */
function fetchUSMarketData(settings) {
  const base = String((settings && settings.apiBase) || '').replace(/\/$/, '');
  const url = `${base}/data/us_market_cache.json?_t=${Date.now()}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 10000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data && Array.isArray(res.data.stocks)) {
          resolve(res.data);
        } else {
          resolve(null);
        }
      },
      fail() { resolve(null); }
    });
  });
}

/**
 * 获取社交媒体趋势热点
 * 优先从 sentiment_cache.json 的 trends 字段读取
 * 兑底从 /api/social-trends 读取
 * 返回 { trends[], fetch_time }
 */
function fetchSocialTrends(settings) {
  // 尝试从 sentiment_cache.json 获取 (内含 trends)
  const base = String((settings && settings.apiBase) || '').replace(/\/$/, '');
  const url = `${base}/data/sentiment_cache.json?_t=${Date.now()}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 12000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data && Array.isArray(res.data.trends) && res.data.trends.length > 0) {
          resolve({ trends: res.data.trends, fetch_time: res.data.fetch_time });
        } else {
          // 兑底: 尝试 social_media_videos.json
          const smUrl = `${base}/data/social_media_videos.json?_t=${Date.now()}`;
          wx.request({
            url: smUrl,
            timeout: 10000,
            success(smRes) {
              if (smRes.statusCode >= 200 && smRes.statusCode < 300 && smRes.data && Array.isArray(smRes.data.trends)) {
                resolve({ trends: smRes.data.trends, fetch_time: smRes.data.updated_at });
              } else {
                resolve(null);
              }
            },
            fail() { resolve(null); }
          });
        }
      },
      fail() { resolve(null); }
    });
  });
}
