/**
 * 市场状态工具函数
 */

function todayStr() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function isTradingDay(dateStr) {
  const app = getApp();
  if (!dateStr) dateStr = todayStr();
  if (app.globalData.CN_HOLIDAYS.has(dateStr)) return false;
  if (app.globalData.CN_WORKDAYS.has(dateStr)) return true;
  const d = new Date(dateStr);
  const day = d.getDay();
  return day !== 0 && day !== 6;
}

function getMarketStatus() {
  const app = getApp();
  const now = new Date();
  const day = now.getDay();
  const h = now.getHours(), m = now.getMinutes();
  const t = h * 60 + m;
  const today = todayStr();

  if (app.globalData.CN_HOLIDAYS.has(today)) {
    return { status: 'closed', text: '节假日休市', isHoliday: true };
  }
  if ((day === 0 || day === 6) && !app.globalData.CN_WORKDAYS.has(today)) {
    return { status: 'closed', text: '周末休市' };
  }
  if (t < 570) return { status: 'pre', text: '未开盘' };
  if (t >= 570 && t < 690) return { status: 'open', text: '交易中·上午' };
  if (t >= 690 && t < 780) return { status: 'break', text: '午间休市' };
  if (t >= 780 && t < 900) return { status: 'open', text: '交易中·下午' };
  return { status: 'closed', text: '已收盘' };
}

function isMarketOpen() {
  const ms = getMarketStatus();
  return ms.status === 'open' || ms.status === 'break';
}

function formatPct(val) {
  if (val === null || val === undefined || isNaN(val)) return '--';
  const num = parseFloat(val);
  const sign = num > 0 ? '+' : '';
  return sign + num.toFixed(2) + '%';
}

function pctClass(val) {
  if (val === null || val === undefined || isNaN(val)) return 'flat';
  const num = parseFloat(val);
  if (num > 0.01) return 'up';
  if (num < -0.01) return 'down';
  return 'flat';
}

function formatMoney(val) {
  if (!val && val !== 0) return '--';
  const num = parseFloat(val);
  if (Math.abs(num) >= 1e8) return (num / 1e8).toFixed(2) + '亿';
  if (Math.abs(num) >= 1e4) return (num / 1e4).toFixed(1) + '万';
  return num.toFixed(2);
}

function formatTime(date) {
  if (!date) date = new Date();
  const h = String(date.getHours()).padStart(2, '0');
  const m = String(date.getMinutes()).padStart(2, '0');
  const s = String(date.getSeconds()).padStart(2, '0');
  return `${h}:${m}:${s}`;
}

/**
 * 获取前一个交易日
 */
function getPrevTradingDay(dateStr) {
  const d = new Date(dateStr);
  for (let i = 0; i < 10; i++) {
    d.setDate(d.getDate() - 1);
    const s = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    if (isTradingDay(s)) return s;
  }
  return null;
}

/**
 * 获取下一个交易日
 */
function getNextTradingDay(dateStr) {
  const d = new Date(dateStr);
  for (let i = 0; i < 10; i++) {
    d.setDate(d.getDate() + 1);
    const s = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    if (isTradingDay(s)) return s;
  }
  return null;
}

module.exports = {
  todayStr,
  isTradingDay,
  getMarketStatus,
  isMarketOpen,
  formatPct,
  pctClass,
  formatMoney,
  formatTime,
  getPrevTradingDay,
  getNextTradingDay,
};
