#!/usr/bin/env python3
"""
fund-assistant 后端服务器
- 提供 /api/sentiment   → 返回缓存的舆情数据
- 提供 /api/refresh     → 触发立即采集
- 提供静态文件服务       → HTML/CSS/JS
- 后台每小时自动采集一次
"""

import os, sys, json, time, threading
from datetime import datetime

# 将项目根目录加入 path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

from flask import Flask, jsonify, send_from_directory, request
from scripts.collector import collect_and_save, load_cache, CACHE_FILE
from scripts.analyzer import load_analysis_cache, analyze_and_save, ANALYSIS_CACHE

app = Flask(__name__, static_folder=None)

# ==================== 配置 ====================
PORT = int(os.environ.get('PORT', 8000))
COLLECT_INTERVAL = int(os.environ.get('COLLECT_INTERVAL', 3600))  # 默认1小时
_collecting_lock = threading.Lock()
_collecting = False

# ==================== API ====================

@app.route('/api/sentiment')
def api_sentiment():
    """返回缓存的舆情数据"""
    cache = load_cache()
    if cache is None:
        return jsonify({'items': [], 'total': 0, 'source_counts': {},
                        'fetch_time': None, 'message': '暂无数据，请等待首次采集完成'}), 200

    # 检查缓存是否过期 (超过2小时视为过期)
    age = int(time.time()) - cache.get('fetch_ts', 0)
    cache['cache_age_seconds'] = age
    cache['stale'] = age > COLLECT_INTERVAL * 2
    return jsonify(cache)


@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    """手动触发数据采集"""
    global _collecting
    if _collecting:
        return jsonify({'status': 'busy', 'message': '采集正在进行中，请稍候'}), 429

    def do_collect():
        global _collecting
        with _collecting_lock:
            _collecting = True
            try:
                collect_and_save()
            finally:
                _collecting = False

    t = threading.Thread(target=do_collect, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'message': '采集已启动'})


@app.route('/api/status')
def api_status():
    """服务状态"""
    cache = load_cache()
    analysis = load_analysis_cache()
    return jsonify({
        'server': 'running',
        'collecting': _collecting,
        'cache_exists': cache is not None,
        'analysis_exists': analysis is not None,
        'last_fetch': cache.get('fetch_time') if cache else None,
        'last_analysis': analysis.get('analysis_time') if analysis else None,
        'total_items': cache.get('total', 0) if cache else 0,
        'interval_sec': COLLECT_INTERVAL,
    })


@app.route('/api/analysis')
def api_analysis():
    """返回 AI 分析结果（缓存）"""
    analysis = load_analysis_cache()
    if analysis is None:
        return jsonify({'status': 'no_data', 'message': '暂无分析结果，请等待采集+分析完成'}), 200
    age = int(time.time()) - analysis.get('analysis_ts', 0)
    analysis['analysis_age_seconds'] = age
    analysis['stale'] = age > COLLECT_INTERVAL * 2
    return jsonify(analysis)


@app.route('/api/reanalyze', methods=['POST'])
def api_reanalyze():
    """手动触发重新分析（使用已缓存的采集数据）"""
    cache = load_cache()
    if not cache or not cache.get('items'):
        return jsonify({'status': 'error', 'message': '无采集数据，请先刷新采集'}), 400

    def do_analyze():
        analyze_and_save(cache['items'])

    t = threading.Thread(target=do_analyze, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'message': 'AI 分析已启动'})

# ==================== 静态文件 ====================

@app.route('/')
def index():
    return send_from_directory(ROOT_DIR, 'index.html')

@app.route('/<path:path>')
def static_files(path):
    """服务所有静态文件 (HTML/CSS/JS/JSON/...)"""
    full = os.path.join(ROOT_DIR, path)
    if os.path.isfile(full):
        directory = os.path.dirname(full)
        filename = os.path.basename(full)
        return send_from_directory(directory, filename)
    return 'Not Found', 404

# ==================== 后台定时采集 ====================

def scheduler_loop():
    """每 COLLECT_INTERVAL 秒执行一次采集"""
    global _collecting
    # 启动后先等5秒再首次采集
    time.sleep(5)
    while True:
        if not _collecting:
            with _collecting_lock:
                _collecting = True
                try:
                    print(f'\n[定时任务] {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} 开始自动采集...')
                    collect_and_save()
                    print(f'[定时任务] 采集完成，下次: {COLLECT_INTERVAL}秒后\n')
                except Exception as e:
                    print(f'[定时任务] 采集异常: {e}')
                finally:
                    _collecting = False
        time.sleep(COLLECT_INTERVAL)


# ==================== 启动 ====================
if __name__ == '__main__':
    print(f'''
╔══════════════════════════════════════════════════╗
║  📊 Fund-Assistant 舆情分析后端                  ║
║  端口: {PORT:<6}                                  ║
║  采集间隔: {COLLECT_INTERVAL}秒 ({COLLECT_INTERVAL//60}分钟)                       ║
║  API:                                            ║
║    GET  /api/sentiment  → 舆情数据               ║
║    GET  /api/analysis   → AI分析结果             ║
║    POST /api/refresh    → 手动刷新               ║
║    POST /api/reanalyze  → 重新AI分析             ║
║    GET  /api/status     → 服务状态               ║
╚══════════════════════════════════════════════════╝
''')

    # 启动后台定时采集线程
    scheduler = threading.Thread(target=scheduler_loop, daemon=True)
    scheduler.start()

    app.run(host='0.0.0.0', port=PORT, debug=False)
