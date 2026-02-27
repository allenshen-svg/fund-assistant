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
    // Phase 1: 并行抓取所有数据源
    setProgress(10, '📡 并行抓取多个社交/财经数据源...');

    const results = await Promise.allSettled([
      fetchDouyin(),
      fetchWeibo(),
      fetchEastmoney(),
      fetchCailian(),
      fetchAggHotlists(),
      fetchPrebuiltData(),
    ]);

    setProgress(50, '📊 汇总数据...');

    let allItems = [];
    const srcCounts = { '抖音':0, '微博':0, '东方财富':0, '财联社':0, '聚合':0 };

    results.forEach((r, i) => {
      if(r.status === 'fulfilled' && r.value.length > 0) {
        allItems = allItems.concat(r.value);
      }
    });

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

    // 统计各来源
    for(const item of allItems) {
      const p = item.platform || '';
      if(p === '抖音') srcCounts['抖音']++;
      else if(p === '微博') srcCounts['微博']++;
      else if(p === '东方财富') srcCounts['东方财富']++;
      else if(p === '财联社') srcCounts['财联社']++;
      else srcCounts['聚合']++;
    }

    // 更新来源统计UI
    document.getElementById('src-douyin').textContent = srcCounts['抖音'] || '0';
    document.getElementById('src-weibo').textContent = srcCounts['微博'] || '0';
    document.getElementById('src-em').textContent = srcCounts['东方财富'] || '0';
    document.getElementById('src-cls').textContent = srcCounts['财联社'] || '0';
    document.getElementById('src-agg').textContent = srcCounts['聚合'] || '0';
    document.getElementById('total-badge').textContent = `共 ${allItems.length} 条`;

    // 渲染视频表格
    renderVideoTable(allItems);

    // 渲染热度条
    renderHeatbar(allItems);

    setProgress(55, `✅ 抓取到 ${allItems.length} 条财经舆情数据`);

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
