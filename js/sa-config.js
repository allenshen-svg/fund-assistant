// =============================================
// KOL vs 散户 情绪博弈分析 - Config & State
// =============================================

const AI_PROVIDERS = [
  { id:'siliconflow', name:'硅基流动(免费)', free:true, base:'https://api.siliconflow.cn/v1/chat/completions',
    models: [{name:'DeepSeek-V3',model:'deepseek-ai/DeepSeek-V3'},{name:'Qwen2.5-72B',model:'Qwen/Qwen2.5-72B-Instruct'},{name:'DeepSeek-R1-32B',model:'deepseek-ai/DeepSeek-R1-Distill-Qwen-32B'}] },
  { id:'deepseek', name:'DeepSeek官方', free:false, base:'https://api.deepseek.com/chat/completions',
    models: [{name:'DeepSeek-V3',model:'deepseek-chat'},{name:'DeepSeek-R1',model:'deepseek-reasoner'}] },
  { id:'302ai', name:'302.AI', free:false, base:'https://api.302.ai/v1/chat/completions',
    models: [{name:'DeepSeek-R1',model:'deepseek-r1'},{name:'豆包',model:'doubao-1.5-pro-32k'},{name:'通义千问',model:'qwen3-235b-a22b'}] },
];

// ==================== STATE ====================
let _providerId = localStorage.getItem('sa_provider') || 'siliconflow';
let _provider = AI_PROVIDERS.find(p=>p.id===_providerId) || AI_PROVIDERS[0];
let _apiKey = localStorage.getItem('sa_apikey') || localStorage.getItem('fa_302ai_key') || 'sk-njqerftsrrnojbsdagigsrzbwwxgtuhrsyihphcxvsdpbaxl';
let _modelId = localStorage.getItem('sa_model') || _provider.models[0].model;
let _allVideoData = [];
let _analysisResult = null;
let _sentimentFactors = null;
let _isRunning = false;

// ==================== 财经关键词过滤 ====================
const FINANCE_KW = [
  'A股','股市','大盘','沪指','上证','深成','创业板','科创板','沪深300','恒生','港股','美股','纳斯达克',
  'AI','人工智能','算力','芯片','半导体','光模块','CPO','大模型','DeepSeek',
  '机器人','自动驾驶','新能源','光伏','锂电','碳酸锂','储能',
  '军工','国防','航天','白酒','消费','医药','创新药','CXO',
  '黄金','金价','原油','油价','有色金属','铜','铝','稀土',
  '红利','高股息','银行','保险','券商','地产',
  '央行','降息','降准','LPR','利率','通胀','CPI','GDP','PMI',
  '美联储','加息','国债','债券','汇率','人民币',
  '关税','贸易战','制裁','地缘','中东','俄乌',
  '基金','ETF','牛市','熊市','涨停','跌停','抄底','追高',
  '仓位','加仓','减仓','定投','主力','资金','北向',
  '茅台','比亚迪','宁德','英伟达','NVIDIA','特斯拉',
  'IPO','分红','回购','并购','重组','股','基','市场','经济','投资','收益','行情',
  '板块','指数','概念','题材','龙头','主线','赛道',
];

function isFinance(text) {
  if(!text) return false;
  const t = text.toLowerCase();
  return FINANCE_KW.some(kw => t.includes(kw.toLowerCase()));
}

// ==================== SETTINGS ====================
function initSettings() {
  document.getElementById('set-provider').value = _providerId;
  document.getElementById('set-apikey').value = _apiKey;
  populateModels();
}
function populateModels() {
  const sel = document.getElementById('set-model');
  sel.innerHTML = '';
  for(const m of _provider.models) {
    const o = document.createElement('option');
    o.value = m.model; o.textContent = m.name;
    if(m.model===_modelId) o.selected = true;
    sel.appendChild(o);
  }
}
function onProviderChange() {
  const id = document.getElementById('set-provider').value;
  _provider = AI_PROVIDERS.find(p=>p.id===id)||AI_PROVIDERS[0];
  _providerId = _provider.id;
  _modelId = _provider.models[0].model;
  populateModels();
}
function openSettings(){document.getElementById('settings-modal').classList.add('show')}
function closeSettings(){document.getElementById('settings-modal').classList.remove('show')}
function saveSettings() {
  _providerId = document.getElementById('set-provider').value;
  _provider = AI_PROVIDERS.find(p=>p.id===_providerId)||AI_PROVIDERS[0];
  _apiKey = document.getElementById('set-apikey').value.trim();
  _modelId = document.getElementById('set-model').value;
  localStorage.setItem('sa_provider',_providerId);
  localStorage.setItem('sa_apikey',_apiKey);
  localStorage.setItem('sa_model',_modelId);
  closeSettings();
}

// ==================== HELPERS ====================
function setProgress(pct,msg){document.getElementById('loading-fill').style.width=pct+'%';document.getElementById('loading-status').textContent=msg}
function sleep(ms){return new Promise(r=>setTimeout(r,ms))}
function toggleManual(){document.getElementById('manual-area').classList.toggle('show')}
function markSource(src, state) {
  const el = document.querySelector(`.loading-src[data-src="${src}"]`);
  if(!el) return;
  el.classList.remove('done','active');
  if(state) el.classList.add(state);
}
