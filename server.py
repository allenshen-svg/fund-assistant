#!/usr/bin/env python3
"""
fund-assistant 后端服务器
- 提供 /api/sentiment   → 返回缓存的舆情数据
- 提供 /api/refresh     → 触发立即采集
- 提供静态文件服务       → HTML/CSS/JS
- 后台每小时自动采集一次
"""

import os, sys, json, time, threading
from datetime import datetime, date

# 将项目根目录加入 path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

# 自动加载 .env 环境变量 & infra.json 配置
from scripts.infra import infra, env  # noqa: F401

from flask import Flask, jsonify, send_from_directory, request
from scripts.collector import collect_and_save, load_cache, load_us_market_cache, fetch_us_market, CACHE_FILE
from scripts.analyzer import load_analysis_cache, analyze_and_save, ANALYSIS_CACHE

app = Flask(__name__, static_folder=None)

# ==================== CORS（跨域支持）====================
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    if request.method == 'OPTIONS':
        response.status_code = 204
    return response

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


@app.route('/api/us_market')
def api_us_market():
    """返回隔夜美股行情数据"""
    cache = load_us_market_cache()
    if cache is None:
        # 尝试即时采集
        cache = fetch_us_market()
    if cache is None:
        return jsonify({'stocks': [], 'message': '美股行情暂无数据'}), 200
    age = int(time.time()) - cache.get('fetch_ts', 0)
    cache['cache_age_seconds'] = age
    return jsonify(cache)


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

# ==================== 交易日判定 ====================

def is_trading_day(d=None):
    """判断是否为 A 股交易日（周一~周五且非法定节假日）
    简易版：只判断工作日，不含节假日日历（如需精确可接入第三方日历）"""
    if d is None:
        d = date.today()
    return d.weekday() < 5  # 0=周一 ... 4=周五

def is_trading_hours():
    """判断当前是否处于交易时段 (09:15~15:30)"""
    now = datetime.now()
    h, m = now.hour, now.minute
    return is_trading_day() and ((h == 9 and m >= 15) or (10 <= h <= 14) or (h == 15 and m <= 30))

def get_collect_interval():
    """根据交易日/非交易日动态调整采集间隔
    - 交易时段：COLLECT_INTERVAL（默认1小时）
    - 非交易日/非交易时段：7200秒（2小时）"""
    if is_trading_hours():
        return COLLECT_INTERVAL
    return max(COLLECT_INTERVAL, 7200)  # 非交易时段至少2小时

# ==================== 后台定时采集 ====================

def scheduler_loop():
    """动态间隔采集：交易时段按 COLLECT_INTERVAL，非交易日/时段每2小时"""
    global _collecting
    # 启动后先等5秒再首次采集
    time.sleep(5)
    while True:
        if not _collecting:
            interval = get_collect_interval()
            trading_label = '交易时段' if is_trading_hours() else '非交易时段'
            with _collecting_lock:
                _collecting = True
                try:
                    print(f'\n[定时任务] {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} [{trading_label}] 开始自动采集...')
                    collect_and_save()
                    print(f'[定时任务] 采集完成，下次: {interval}秒({interval//60}分钟)后\n')
                except Exception as e:
                    print(f'[定时任务] 采集异常: {e}')
                finally:
                    _collecting = False
            time.sleep(interval)
        else:
            time.sleep(10)


# ==================== 启动后台采集线程 ====================
_scheduler_started = False
def _ensure_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    print('[scheduler] 定时采集线程已启动')

# gunicorn 兼容：通过 before_first_request 在第一次请求时启动采集线程
# 避免多 worker fork 时重复启动
@app.before_request
def _lazy_start_scheduler():
    _ensure_scheduler()
    # 只需执行一次，之后移除此 hook
    app.before_request_funcs[None].remove(_lazy_start_scheduler)

# ==================== 入口 ======================================
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
    app.run(host='0.0.0.0', port=PORT, debug=False)
