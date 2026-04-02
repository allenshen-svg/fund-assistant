const { getSettings } = require('../../utils/storage');
const { getServerBase } = require('../../utils/api');

const ASSET_NAMES = {
  crude_oil: '原油', gold: '黄金', tech_nasdaq: 'NASDAQ', sp500: 'S&P500',
  usd_index: '美元指数', china_a50: 'A股', defense: '军工', crypto_btc: '比特币',
  natural_gas: '天然气', copper: '铜', treasury_bond: '国债', eu_stocks: '欧股',
  ai_tech: 'AI/算力', robotics: '机器人', new_energy: '新能源/光伏',
  hk_tech: '港股科技', nonferrous_metals: '有色金属', semiconductor: '半导体',
  lithium_battery: '锂电', nev: '新能源车'
};

const DISPLAY_ORDER = [
  'gold', 'crude_oil', 'defense', 'ai_tech', 'semiconductor', 'robotics',
  'new_energy', 'hk_tech', 'nev', 'lithium_battery', 'nonferrous_metals',
  'tech_nasdaq', 'sp500', 'china_a50', 'natural_gas', 'usd_index',
  'crypto_btc', 'copper', 'treasury_bond', 'eu_stocks'
];

function _fmtTime(iso) {
  if (!iso) return '';
  try {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    var m = d.getMonth() + 1;
    var day = d.getDate();
    var h = d.getHours();
    var min = d.getMinutes();
    return m + '/' + day + ' ' + (h < 10 ? '0' : '') + h + ':' + (min < 10 ? '0' : '') + min;
  } catch (e) { return iso; }
}

Page({
  data: {
    loading: true,
    triggering: false,
    alerts: [],
    probList: [],
    statements: [],
    statementCount: 0,
    alertCount: 0,
    updatedAt: '--'
  },

  _settings: null,
  _base: '',

  onLoad: function () {
    this._settings = getSettings();
    this._base = getServerBase(this._settings);
    this.loadData();
  },

  onPullDownRefresh: function () {
    this.loadData(function () {
      wx.stopPullDownRefresh();
    });
  },

  loadData: function (cb) {
    var that = this;
    if (!this._base) {
      that.setData({ loading: false });
      wx.showToast({ title: '未配置服务器地址', icon: 'none' });
      if (cb) cb();
      return;
    }
    that.setData({ loading: true });
    wx.request({
      url: that._base + '/api/trump-alert',
      method: 'GET',
      timeout: 15000,
      success: function (res) {
        if (res.statusCode === 200 && res.data) {
          that._processData(res.data);
        } else {
          wx.showToast({ title: '加载失败', icon: 'none' });
        }
      },
      fail: function () {
        wx.showToast({ title: '网络错误', icon: 'none' });
      },
      complete: function () {
        that.setData({ loading: false });
        if (cb) cb();
      }
    });
  },

  _processData: function (raw) {
    var probs = raw.predictions || {};
    var stmts = raw.statements || [];
    var calibration = raw.calibration || {};

    // Build prob list
    var probList = [];
    var alerts = [];
    for (var i = 0; i < DISPLAY_ORDER.length; i++) {
      var id = DISPLAY_ORDER[i];
      var p = probs[id];
      if (!p) continue;
      var prob = p.probability || 0.5;
      var pct = Math.round(prob * 100);
      var signal = p.signal || 'neutral';
      var sigClass = 'neutral';
      var signal_zh = '中性';
      var barClass = 'neutral-bar';
      if (signal === 'bullish' || signal === '看涨') {
        sigClass = 'bull'; signal_zh = '看涨'; barClass = 'bull-bar';
      } else if (signal === 'bearish' || signal === '看跌') {
        sigClass = 'bear'; signal_zh = '看跌'; barClass = 'bear-bar';
      }
      var name = ASSET_NAMES[id] || id;
      var logic = p.logic || '';
      var hit_rate = (calibration[id] && calibration[id].hit_rate != null) ? calibration[id].hit_rate : null;
      var hitStr = hit_rate != null ? (Math.round(hit_rate * 100) + '%') : '';

      var item = {
        id: id, name: name, pct: pct, signal: signal,
        sigClass: sigClass, signal_zh: signal_zh, barClass: barClass,
        logic: logic, hit_rate: hit_rate, hitStr: hitStr
      };
      probList.push(item);

      if (prob >= 0.7 || prob <= 0.3) {
        alerts.push({
          asset: name,
          signal: signal_zh,
          probability: prob,
          pctStr: pct + '%'
        });
      }
    }

    // Build statements
    var stmtList = [];
    for (var j = 0; j < stmts.length; j++) {
      var s = stmts[j];
      var sentiment = (s.sentiment || 'neutral').toLowerCase();
      var sentimentLabel = sentiment === 'hawkish' ? '鹰派' : sentiment === 'dovish' ? '鸽派' : '中性';
      var score = s.score || 0;
      var scoreStr = score > 0 ? ('+' + score.toFixed(1)) : score.toFixed(1);
      stmtList.push({
        title: s.title || '',
        source: s.source || '',
        timeStr: _fmtTime(s.pub_time || s.published),
        sentiment: sentiment,
        sentimentLabel: sentimentLabel,
        scoreStr: scoreStr,
        is_policy: s.is_policy || false,
        summary_zh: s.summary_zh || ''
      });
    }

    var updated = raw.updated_at || '';
    this.setData({
      probList: probList,
      alerts: alerts,
      statements: stmtList,
      statementCount: stmtList.length,
      alertCount: alerts.length,
      updatedAt: _fmtTime(updated) || '--'
    });
  },

  onTrigger: function () {
    var that = this;
    if (that.data.triggering) return;
    if (!that._base) {
      wx.showToast({ title: '未配置服务器地址', icon: 'none' });
      return;
    }
    that.setData({ triggering: true });
    wx.showLoading({ title: '正在分析...' });
    wx.request({
      url: that._base + '/api/trump-alert/trigger',
      method: 'POST',
      timeout: 120000,
      success: function (res) {
        if (res.statusCode === 200) {
          wx.showToast({ title: '分析完成', icon: 'success' });
          that.loadData();
        } else {
          wx.showToast({ title: '触发失败', icon: 'none' });
        }
      },
      fail: function () {
        wx.showToast({ title: '请求失败', icon: 'none' });
      },
      complete: function () {
        wx.hideLoading();
        that.setData({ triggering: false });
      }
    });
  }
});
