// =============================================
// KOL vs 散户 情绪博弈分析 - Pipeline & Init
// =============================================

async function runFullPipeline() {
  if(_isRunning) return;
  _isRunning = true;

  const btn = document.getElementById('btn-refresh');
  const progress = document.getElementById('progress-text');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-sm"></span> 抓取分析中...';

  const overlay = document.getElementById('loading-overlay');
  overlay.classList.remove('hide');
  setProgress(5, '启动数据抓取管道...');

  try {
    // Phase 1: 从后端 API 获取舆情数据
    setProgress(10, '📡 读取后端舆情数据...');
    markSource('douyin', 'active');
    markSource('weibo', 'active');
    markSource('eastmoney', 'active');
    markSource('tophub', 'active');

    let apiData = await fetchSentimentData();

    // 如果缓存为空或过期，触发后端采集并等待
    if (!apiData.items || apiData.items.length === 0 || apiData.stale) {
      setProgress(15, '📡 触发后端数据采集...');
      await triggerRefresh();
      apiData = await waitForRefresh((elapsed, max, status) => {
        const pct = 15 + Math.round((elapsed / max) * 35);
        setProgress(pct, `⏳ 后端采集中... (${elapsed}s/${max}s)`);
      });
    }

    setProgress(50, '📊 汇总数据...');

    let allItems = apiData.items || [];
    const srcCounts = apiData.source_counts || {};

    // 标记数据源状态
    markSource('douyin', (srcCounts['抖音'] || 0) > 0 ? 'done' : '');
    markSource('weibo', (srcCounts['微博'] || 0) > 0 ? 'done' : '');
    markSource('eastmoney', (srcCounts['东方财富'] || 0) > 0 ? 'done' : '');
    markSource('tophub',
      ((srcCounts['知乎']||0) + (srcCounts['百度']||0) + (srcCounts['B站']||0) + (srcCounts['财联社']||0)) > 0
      ? 'done' : '');

    // 合并手动输入数据
    const manualInput = document.getElementById('manual-input').value.trim();
    if(manualInput) {
      try {
        const manualData = JSON.parse(manualInput);
        if(Array.isArray(manualData)) allItems = allItems.concat(manualData);
      } catch(e) { console.warn('Manual data parse error:', e); }
    }

    // 去重 + 按热度排序
    allItems = dedup(allItems);
    allItems.sort((a,b) => (b.likes||0) - (a.likes||0));
    allItems = allItems.slice(0, 50);

    _allVideoData = allItems;

    // 统计各来源 (从后端数据的 platform 字段)
    const uiCounts = { '抖音':0, '微博':0, '东方财富':0, '财联社':0, '聚合':0 };
    for(const item of allItems) {
      const p = item.platform || '';
      if(p === '抖音') uiCounts['抖音']++;
      else if(p === '微博') uiCounts['微博']++;
      else if(p === '东方财富') uiCounts['东方财富']++;
      else if(p === '财联社') uiCounts['财联社']++;
      else uiCounts['聚合']++;
    }

    // 更新来源统计UI
    document.getElementById('src-douyin').textContent = uiCounts['抖音'] || '0';
    document.getElementById('src-weibo').textContent = uiCounts['微博'] || '0';
    document.getElementById('src-em').textContent = uiCounts['东方财富'] || '0';
    document.getElementById('src-cls').textContent = uiCounts['财联社'] || '0';
    document.getElementById('src-agg').textContent = uiCounts['聚合'] || '0';
    document.getElementById('total-badge').textContent = `共 ${allItems.length} 条`;

    // 显示数据采集时间
    if(apiData.fetch_time) {
      progress.textContent = `数据来自后端: ${apiData.fetch_time}`;
    }

    // 渲染视频表格
    renderVideoTable(allItems);

    // 渲染热度条
    renderHeatbar(allItems);

    setProgress(55, `✅ 获取到 ${allItems.length} 条财经舆情数据`);

    if(allItems.length === 0) {
      setProgress(100, '⚠️ 没有抓取到数据，请检查网络或稍后重试');
      await sleep(800);
      overlay.classList.add('hide');
      progress.textContent = '⚠️ 没有抓取到有效数据，请检查网络后点击"重新抓取分析"';
      document.getElementById('live-dot').className = 'ph-dot off';
      document.getElementById('header-status').textContent = '抓取失败';
      _isRunning = false;
      btn.disabled = false;
      btn.innerHTML = '🔄 重新抓取分析';
      return;
    }

    // Phase 2: AI 分析
    markSource('ai', 'active');
    setProgress(60, '🧠 调用 AI 引擎进行逆向分析...');
    progress.textContent = '正在与 AI 对话，预计需要 15-30 秒...';

    const prompts = buildAnalysisPrompt(JSON.stringify(allItems, null, 2));
    const result = await callAI(_modelId, prompts.systemPrompt, prompts.userPrompt, 0.6);
    _analysisResult = result;
    markSource('ai', 'done');

    setProgress(85, '解析情绪因子...');
    _sentimentFactors = extractJSON(result);

    setProgress(92, '渲染仪表盘...');
    renderDashboard(allItems, result, _sentimentFactors);

    setProgress(100, '✅ 分析完成！');
    await sleep(400);
    overlay.classList.add('hide');

    // 更新 header
    document.getElementById('live-dot').className = 'ph-dot live';
    document.getElementById('header-status').textContent = `已分析 ${allItems.length} 条数据`;
    document.getElementById('header-time').textContent = new Date().toLocaleTimeString('zh-CN');
    progress.textContent = '✅ 分析完成 · ' + new Date().toLocaleTimeString('zh-CN');

    document.getElementById('sec-gauge').scrollIntoView({behavior:'smooth', block:'start'});

  } catch(e) {
    console.error('Pipeline failed:', e);
    overlay.classList.add('hide');
    progress.textContent = '❌ 分析失败: ' + e.message;
    document.getElementById('live-dot').className = 'ph-dot off';
    document.getElementById('header-status').textContent = '分析失败';
  } finally {
    _isRunning = false;
    btn.disabled = false;
    btn.innerHTML = '🔄 重新抓取分析';
  }
}

// ==================== NAV SCROLL ====================
function setupNav() {
  const sections = ['sec-sources','sec-gauge','sec-hotspot','sec-radar','sec-action','sec-videos','sec-raw','sec-json'];
  const navItems = document.querySelectorAll('.nav-item');
  window.addEventListener('scroll',()=>{
    let current = sections[0];
    sections.forEach(id=>{const el=document.getElementById(id);if(el&&el.getBoundingClientRect().top<=120)current=id});
    navItems.forEach(item=>{item.classList.toggle('active',item.getAttribute('href')==='#'+current)});
  });
}

// ==================== INIT ====================
document.getElementById('header-time').textContent = new Date().toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
initSettings();
setupNav();

// 页面加载后自动启动全流程
window.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => runFullPipeline(), 300);
});
