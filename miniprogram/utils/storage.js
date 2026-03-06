function getAppConfig() {
  const app = getApp();
  return app.globalData;
}

function getSettings() {
  const cfg = getAppConfig();
  const raw = wx.getStorageSync(cfg.storageKeys.settings);
  if (!raw) return { ...cfg.defaultSettings };
  // 合并时，空字符串不覆盖默认值
  const merged = { ...cfg.defaultSettings };
  if (raw) {
    Object.keys(raw).forEach(k => {
      if (raw[k] !== '' && raw[k] !== null && raw[k] !== undefined) {
        merged[k] = raw[k];
      }
    });
  }
  return merged;
}

function setSettings(nextSettings) {
  const cfg = getAppConfig();
  const merged = { ...cfg.defaultSettings, ...nextSettings };
  wx.setStorageSync(cfg.storageKeys.settings, merged);
  return merged;
}

function getHoldings() {
  const cfg = getAppConfig();
  const raw = wx.getStorageSync(cfg.storageKeys.holdings);
  if (Array.isArray(raw) && raw.length > 0) return raw;
  // 如果没有持仓，使用 FUND_DB 作为默认持仓
  const db = cfg.FUND_DB;
  const defaults = Object.keys(db).map(code => ({
    code,
    name: db[code].name,
    type: db[code].type
  }));
  return defaults;
}

function setHoldings(list) {
  const cfg = getAppConfig();
  const normalized = (list || []).map((item) => ({
    code: String(item.code || '').trim(),
    name: String(item.name || '').trim(),
    type: String(item.type || '其他').trim()
  })).filter((item) => item.code && item.name);
  wx.setStorageSync(cfg.storageKeys.holdings, normalized);
  return normalized;
}

function getWatchlist() {
  const cfg = getAppConfig();
  const raw = wx.getStorageSync(cfg.storageKeys.watchlist);
  return Array.isArray(raw) ? raw : [];
}

function setWatchlist(list) {
  const cfg = getAppConfig();
  const normalized = (list || []).map((item) => ({
    code: String(item.code || '').trim(),
    name: String(item.name || '').trim(),
    type: String(item.type || '其他').trim()
  })).filter((item) => item.code && item.name);
  wx.setStorageSync(cfg.storageKeys.watchlist, normalized);
  return normalized;
}

/* ====== 模拟仓 ====== */
const SIM_KEY = 'fa_sim_portfolio_v1';
const SIM_LOG_KEY = 'fa_sim_trade_log_v1';
const SIM_REVIEW_KEY = 'fa_sim_weekly_review_v1';

/**
 * 获取模拟仓状态
 * @returns {{ cash: number, totalCash: number, positions: Array<{code,name,type,sector,shares,costPrice,costTotal,buyDate}>, createdAt: string }}
 */
function getSimPortfolio() {
  const raw = wx.getStorageSync(SIM_KEY);
  if (raw && typeof raw === 'object') return raw;
  return {
    cash: 100000,
    totalCash: 100000,
    positions: [],
    createdAt: new Date().toISOString(),
  };
}

function setSimPortfolio(portfolio) {
  wx.setStorageSync(SIM_KEY, portfolio);
  return portfolio;
}

/**
 * 获取交易记录
 * @returns {Array<{id,date,time,action,code,name,sector,amount,price,reason,aiSource}>}
 */
function getSimTradeLog() {
  const raw = wx.getStorageSync(SIM_LOG_KEY);
  return Array.isArray(raw) ? raw : [];
}

function addSimTradeLog(entry) {
  const log = getSimTradeLog();
  entry.id = Date.now();
  entry.time = new Date().toISOString();
  log.unshift(entry); // 最新的在前
  // 最多保留200条
  if (log.length > 200) log.length = 200;
  wx.setStorageSync(SIM_LOG_KEY, log);
  return log;
}

/**
 * 获取周复盘记录
 * @returns {Array<{id,weekStart,weekEnd,startValue,endValue,returnPct,details,aiReview,trades}>}
 */
function getSimWeeklyReviews() {
  const raw = wx.getStorageSync(SIM_REVIEW_KEY);
  return Array.isArray(raw) ? raw : [];
}

function addSimWeeklyReview(review) {
  const reviews = getSimWeeklyReviews();
  review.id = Date.now();
  reviews.unshift(review);
  if (reviews.length > 52) reviews.length = 52; // 保留一年
  wx.setStorageSync(SIM_REVIEW_KEY, reviews);
  return reviews;
}

module.exports = {
  getSettings,
  setSettings,
  getHoldings,
  setHoldings,
  getWatchlist,
  setWatchlist,
  getSimPortfolio,
  setSimPortfolio,
  getSimTradeLog,
  addSimTradeLog,
  getSimWeeklyReviews,
  addSimWeeklyReview,
};
