// =============================================
// KOL vs 散户 情绪博弈分析 - Data Fetchers
// 前端仅负责从后端 API 获取数据，不直接爬取外部源
// =============================================

async function fetchSentimentData() {
  try {
    const resp = await fetch('/api/sentiment?t=' + Date.now());
    if (!resp.ok) throw new Error('API error: ' + resp.status);
    return await resp.json();
  } catch (e) {
    console.error('Fetch sentiment failed:', e);
    return { items: [], source_counts: {}, total: 0, fetch_time: null, error: e.message };
  }
}

async function fetchAnalysisData() {
  try {
    const resp = await fetch('/api/analysis?t=' + Date.now());
    if (!resp.ok) throw new Error('API error: ' + resp.status);
    return await resp.json();
  } catch (e) {
    console.error('Fetch analysis failed:', e);
    return null;
  }
}

async function triggerRefresh() {
  try {
    const resp = await fetch('/api/refresh', { method: 'POST' });
    return await resp.json();
  } catch (e) {
    console.error('Trigger refresh failed:', e);
    return { status: 'error', message: e.message };
  }
}

async function triggerReanalyze() {
  try {
    const resp = await fetch('/api/reanalyze', { method: 'POST' });
    return await resp.json();
  } catch (e) {
    console.error('Trigger reanalyze failed:', e);
    return { status: 'error', message: e.message };
  }
}

async function fetchServerStatus() {
  try {
    const resp = await fetch('/api/status');
    return await resp.json();
  } catch (e) {
    return { server: 'offline' };
  }
}

async function fetchUSMarketData() {
  try {
    const resp = await fetch('/api/us_market?t=' + Date.now());
    if (!resp.ok) throw new Error('API error: ' + resp.status);
    return await resp.json();
  } catch (e) {
    console.error('Fetch US market failed:', e);
    return null;
  }
}

async function waitForRefresh(onProgress) {
  const maxWait = 120, interval = 3;
  for (let elapsed = 0; elapsed < maxWait; elapsed += interval) {
    await new Promise(r => setTimeout(r, interval * 1000));
    const status = await fetchServerStatus();
    if (onProgress) onProgress(elapsed, maxWait, status);
    if (!status.collecting) return await fetchSentimentData();
  }
  return await fetchSentimentData();
}

function estimateSentiment(text) {
  if (!text) return '中性';
  if (/暴涨|疯涨|大涨|飙升|涨停|全仓|梭哈|起飞|爆发|牛市|创新高|狂热/.test(text)) return '极度看多';
  if (/上涨|走高|反弹|利好|加仓|机会|突破|看好|推荐|配置|走强/.test(text)) return '偏多';
  if (/暴跌|崩盘|大跌|跳水|清仓|割肉|熊市|腰斩/.test(text)) return '极度悲观';
  if (/下跌|走低|利空|减仓|风险|警惕|谨慎|回调|承压|重挫/.test(text)) return '偏空';
  if (/震荡|分歧|观望|持平|稳定|盘整/.test(text)) return '中性';
  return '中性偏多';
}

function dedup(items) {
  const seen = new Set();
  return items.filter(item => {
    const key = (item.title || '').replace(/[\W\s]/g, '').slice(0, 20);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
