const { fallbackHotEvents } = require('../data/fallback-hot-events');

/**
 * 获取热点事件数据
 */
function fetchHotEvents(settings) {
  if (!settings.useRemote) {
    return Promise.resolve({ source: 'local', data: fallbackHotEvents });
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
          resolve({ source: 'remote', data: res.data });
          return;
        }
        resolve({ source: 'local', data: fallbackHotEvents });
      },
      fail() {
        resolve({ source: 'local', data: fallbackHotEvents });
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
  const url = 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=20&po=1&np=1&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87';
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
  triggerRefresh,
  triggerReanalyze,
  getServerBase: _getServerBase,
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
