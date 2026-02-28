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
 */
function fetchFundHistory(fundCode, pageSize) {
  pageSize = pageSize || 250;
  const url = `https://api.fund.eastmoney.com/f10/lsjz?fundCode=${fundCode}&pageIndex=1&pageSize=${pageSize}`;
  return new Promise((resolve) => {
    wx.request({
      url,
      timeout: 10000,
      header: { 'Referer': 'https://fundf10.eastmoney.com/' },
      success(res) {
        try {
          const data = res.data;
          const lsjz = (data && data.Data && data.Data.LSJZList) || [];
          const navList = lsjz
            .filter(item => item.DWJZ && !isNaN(parseFloat(item.DWJZ)))
            .map(item => ({
              date: item.FSRQ,
              nav: parseFloat(item.DWJZ),
            }))
            .reverse(); // 按时间升序
          resolve(navList);
        } catch (e) {
          resolve([]);
        }
      },
      fail() { resolve([]); }
    });
  });
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

module.exports = {
  fetchHotEvents,
  fetchIndices,
  fetchFundEstimate,
  fetchMultiFundEstimates,
  fetchSectorFlows,
  fetchFundHistory,
  fetchMultiFundHistory,
};
