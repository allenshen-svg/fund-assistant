function getAppConfig() {
  const app = getApp();
  return app.globalData;
}

function getSettings() {
  const cfg = getAppConfig();
  const raw = wx.getStorageSync(cfg.storageKeys.settings);
  if (!raw) return { ...cfg.defaultSettings };
  return { ...cfg.defaultSettings, ...raw };
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
  return Array.isArray(raw) ? raw : [];
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

module.exports = {
  getSettings,
  setSettings,
  getHoldings,
  setHoldings
};
