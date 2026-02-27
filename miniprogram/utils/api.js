const { fallbackHotEvents } = require('../data/fallback-hot-events');

function fetchHotEvents(settings) {
  if (!settings.useRemote) {
    return Promise.resolve({
      source: 'local',
      data: fallbackHotEvents
    });
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

module.exports = {
  fetchHotEvents
};
