// =============================================
// KOL vs 散户 情绪博弈 - Pipeline & Init (Clean)
// =============================================

async function runFullPipeline() {
  if (_isRunning) return;
  _isRunning = true;

  var btn = document.getElementById('btn-refresh');
  var progress = document.getElementById('progress-text');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-sm"></span> 分析中...';

  var overlay = document.getElementById('loading-overlay');
  overlay.classList.remove('hide');
  setProgress(5, '启动数据管道...');

  try {
    // Phase 1: fetch sentiment data from backend
    setProgress(10, '📡 读取后端舆情数据...');
    markSource('douyin', 'active');
    markSource('weibo', 'active');
    markSource('eastmoney', 'active');
    markSource('tophub', 'active');

    var apiData = await fetchSentimentData();

    if (!apiData.items || apiData.items.length === 0 || apiData.stale) {
      setProgress(15, '📡 触发后端采集...');
      await triggerRefresh();
      apiData = await waitForRefresh(function(elapsed, max, status) {
        var pct = 15 + Math.round((elapsed / max) * 35);
        setProgress(pct, '⏳ 采集中... (' + elapsed + 's/' + max + 's)');
      });
    }

    setProgress(50, '📊 汇总数据...');

    var allItems = apiData.items || [];
    var srcCounts = apiData.source_counts || {};

    // update source pill counts
    var srcMap = {
      'src-douyin': srcCounts['抖音'] || 0,
      'src-weibo': srcCounts['微博'] || 0,
      'src-em': srcCounts['东方财富'] || 0,
      'src-cls': srcCounts['财联社'] || 0,
      'src-sina': srcCounts['新浪财经'] || 0,
      'src-zhihu': srcCounts['知乎'] || 0,
      'src-baidu': srcCounts['百度'] || 0,
      'src-bili': srcCounts['B站'] || 0,
      'src-xhs': srcCounts['小红书'] || 0
    };
    for (var id in srcMap) {
      var el = document.getElementById(id);
      if (el) {
        var b = el.querySelector('b');
        if (b) b.textContent = srcMap[id];
      }
    }
    document.getElementById('total-badge').textContent = '共 ' + (apiData.total || allItems.length) + ' 条';

    // mark source status
    markSource('douyin', (srcCounts['抖音'] || 0) > 0 ? 'done' : '');
    markSource('weibo', (srcCounts['微博'] || 0) > 0 ? 'done' : '');
    markSource('eastmoney', (srcCounts['东方财富'] || 0) > 0 ? 'done' : '');
    markSource('tophub',
      ((srcCounts['知乎'] || 0) + (srcCounts['百度'] || 0) + (srcCounts['B站'] || 0) + (srcCounts['财联社'] || 0) + (srcCounts['新浪财经'] || 0)) > 0
      ? 'done' : '');
    markSource('xhs', (srcCounts['小红书'] || 0) > 0 ? 'done' : '');

    // merge manual input
    var manualInput = document.getElementById('manual-input').value.trim();
    if (manualInput) {
      try {
        var manualData = JSON.parse(manualInput);
        if (Array.isArray(manualData)) allItems = allItems.concat(manualData);
      } catch(e) { console.warn('Manual data parse error:', e); }
    }

    // dedup + sort
    allItems = dedup(allItems);
    allItems.sort(function(a, b) { return (b.likes || 0) - (a.likes || 0); });
    allItems = allItems.slice(0, 100);
    _allVideoData = allItems;

    if (apiData.fetch_time) {
      progress.textContent = '数据来自后端: ' + apiData.fetch_time;
    }

    renderVideoTable(allItems);
    renderHeatbar(allItems);

    setProgress(55, '✅ 获取到 ' + allItems.length + ' 条数据');

    if (allItems.length === 0) {
      setProgress(100, '⚠️ 没有数据，请检查网络');
      await sleep(800);
      overlay.classList.add('hide');
      progress.textContent = '⚠️ 没有数据，请稍后重试';
      document.getElementById('live-dot').className = 'ph-dot off';
      document.getElementById('header-status').textContent = '抓取失败';
      _isRunning = false;
      btn.disabled = false;
      btn.innerHTML = '🔄 重新分析';
      return;
    }

    // Phase 2: AI analysis
    markSource('ai', 'active');
    setProgress(60, '🧠 读取 AI 分析...');
    progress.textContent = '获取 AI 分析结果...';

    var analysisData = await fetchAnalysisData();

    if (!analysisData || analysisData.status === 'no_data' || analysisData.stale) {
      setProgress(65, '🧠 等待 AI 分析...');
      progress.textContent = 'AI 分析中，约 15-30 秒...';
      for (var i = 0; i < 20; i++) {
        await sleep(3000);
        analysisData = await fetchAnalysisData();
        setProgress(65 + i, '🧠 等待 AI... (' + ((i + 1) * 3) + 's)');
        if (analysisData && analysisData.raw_text) break;
      }
    }

    if (analysisData && analysisData.raw_text) {
      var result = analysisData.raw_text;
      _analysisResult = result;
      _sentimentFactors = analysisData.dashboard || extractJSON(result);
      markSource('ai', 'done');

      setProgress(92, '渲染结果...');
      renderDashboard(allItems, result, _sentimentFactors);

      setProgress(100, '✅ 分析完成');
      await sleep(400);
      overlay.classList.add('hide');

      document.getElementById('live-dot').className = 'ph-dot live';
      document.getElementById('header-status').textContent = '已分析 ' + allItems.length + ' 条';
      document.getElementById('header-time').textContent = new Date().toLocaleTimeString('zh-CN');
      progress.textContent = '✅ 完成 · ' + new Date().toLocaleTimeString('zh-CN')
        + (analysisData.analysis_time ? ' (分析于 ' + analysisData.analysis_time + ')' : '');
    } else {
      // fallback: frontend AI
      markSource('ai', 'active');
      setProgress(65, '🧠 使用前端 AI...');
      progress.textContent = 'AI 分析中，约 15-30 秒...';

      var prompts = buildAnalysisPrompt(JSON.stringify(allItems, null, 2));
      var result = await callAI(_modelId, prompts.systemPrompt, prompts.userPrompt, 0.6);
      _analysisResult = result;
      markSource('ai', 'done');

      setProgress(85, '解析因子...');
      _sentimentFactors = extractJSON(result);

      setProgress(92, '渲染结果...');
      renderDashboard(allItems, result, _sentimentFactors);

      setProgress(100, '✅ 分析完成');
      await sleep(400);
      overlay.classList.add('hide');

      document.getElementById('live-dot').className = 'ph-dot live';
      document.getElementById('header-status').textContent = '已分析 ' + allItems.length + ' 条';
      document.getElementById('header-time').textContent = new Date().toLocaleTimeString('zh-CN');
      progress.textContent = '✅ 完成 · ' + new Date().toLocaleTimeString('zh-CN');
    }

    // scroll to KOL section
    document.getElementById('sec-hotspot').scrollIntoView({ behavior: 'smooth', block: 'start' });

  } catch(e) {
    console.error('Pipeline failed:', e);
    overlay.classList.add('hide');
    progress.textContent = '❌ 失败: ' + e.message;
    document.getElementById('live-dot').className = 'ph-dot off';
    document.getElementById('header-status').textContent = '分析失败';
  } finally {
    _isRunning = false;
    btn.disabled = false;
    btn.innerHTML = '🔄 重新分析';
  }
}

// ==================== INIT ====================
document.getElementById('header-time').textContent = new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
initSettings();

window.addEventListener('DOMContentLoaded', function() {
  setTimeout(function() { runFullPipeline(); }, 300);
});
