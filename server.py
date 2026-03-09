#!/usr/bin/env python3
"""
fund-assistant 后端服务器
- 提供 /api/sentiment   → 返回缓存的舆情数据
- 提供 /api/refresh     → 触发立即采集
- 提供静态文件服务       → HTML/CSS/JS
- 后台每30分钟自动采集一次
"""

import os, sys, json, time, threading, fcntl
from datetime import datetime, date

# 将项目根目录加入 path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

# 自动加载 .env 环境变量 & infra.json 配置
from scripts.infra import infra, env  # noqa: F401

from flask import Flask, jsonify, send_from_directory, request
from scripts.collector import collect_and_save, load_cache, load_us_market_cache, fetch_us_market, CACHE_FILE
from scripts.analyzer import load_analysis_cache, analyze_and_save, ANALYSIS_CACHE
from scripts.fetch_events import main as fetch_hot_events
from scripts.fetch_realtime_breaking import main as fetch_realtime_breaking
from scripts.fund_pick import run_fund_pick, load_fund_pick_cache

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
COLLECT_INTERVAL = int(os.environ.get('COLLECT_INTERVAL', 1800))  # 默认30分钟
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

    # 检查缓存是否过期 (超过2倍采集间隔视为过期)
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
                try:
                    fetch_hot_events()
                except SystemExit:
                    print('[手动刷新] ⚠️ 热点事件脚本调用了 sys.exit，已拦截')
                except Exception as e:
                    print(f'[手动刷新] 热点事件采集失败: {e}')
                collect_and_save()
            finally:
                _collecting = False

    t = threading.Thread(target=do_collect, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'message': '采集已启动'})


@app.route('/api/social-trends')
def api_social_trends():
    """返回社交媒体趋势热点（从 sentiment_cache 提取的 trends 字段）"""
    cache = load_cache()
    if cache and cache.get('trends'):
        return jsonify({
            'trends': cache['trends'],
            'fetch_time': cache.get('fetch_time'),
            'total_items': cache.get('total', 0),
        })
    # 兜底: 尝试从 social_media_videos.json 读取
    sm_path = os.path.join(ROOT_DIR, 'data', 'social_media_videos.json')
    if os.path.exists(sm_path):
        try:
            with open(sm_path, 'r', encoding='utf-8') as f:
                sm_data = json.load(f)
            return jsonify({
                'trends': sm_data.get('trends', []),
                'fetch_time': sm_data.get('updated_at'),
                'total_items': sm_data.get('total_processed', 0),
            })
        except Exception:
            pass
    return jsonify({'trends': [], 'message': '暂无趋势数据'}), 200


@app.route('/api/hot-events')
def api_hot_events():
    """返回最新热点事件数据（与 /data/hot_events.json 相同内容，但走 API 路由）"""
    hot_path = os.path.join(ROOT_DIR, 'data', 'hot_events.json')
    if not os.path.exists(hot_path):
        return jsonify({'error': '暂无热点数据'}), 404
    try:
        with open(hot_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/realtime-breaking')
def api_realtime_breaking():
    """返回实时突发新闻 + 全球市场异动数据"""
    rt_path = os.path.join(ROOT_DIR, 'data', 'realtime_breaking.json')
    if not os.path.exists(rt_path):
        return jsonify({'breaking': [], 'anomalies': [], 'message': '暂无实时数据'}), 200
    try:
        with open(rt_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 标记缓存年龄
        updated = data.get('updated_at', '')
        if updated:
            try:
                from datetime import timezone as _tz
                ut = datetime.fromisoformat(updated)
                age = (datetime.now(ut.tzinfo or _tz.utc) - ut).total_seconds()
                data['cache_age_seconds'] = int(age)
            except Exception:
                pass
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/status')
def api_status():
    """服务状态"""
    cache = load_cache()
    analysis = load_analysis_cache()
    hot_path = os.path.join(ROOT_DIR, 'data', 'hot_events.json')
    hot_updated = None
    if os.path.exists(hot_path):
        try:
            with open(hot_path, 'r', encoding='utf-8') as f:
                hd = json.load(f)
            hot_updated = hd.get('updated_at')
        except Exception:
            pass
    return jsonify({
        'server': 'running',
        'collecting': _collecting,
        'cache_exists': cache is not None,
        'analysis_exists': analysis is not None,
        'last_fetch': cache.get('fetch_time') if cache else None,
        'last_analysis': analysis.get('analysis_time') if analysis else None,
        'last_hot_events': hot_updated,
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

# ==================== 选基金/股票 ====================

_fund_pick_lock = threading.Lock()
_fund_pick_running = False

@app.route('/api/fund-pick')
def api_fund_pick():
    """返回最新的 AI 选基金/股票结果（由每日 14:50 自动生成）"""
    data = load_fund_pick_cache()
    if data is None:
        return jsonify({'status': 'no_data', 'message': '暂无推荐，请等待每日 14:50 自动生成'}), 200
    return jsonify(data)

@app.route('/api/fund-pick/trigger', methods=['POST'])
def api_fund_pick_trigger():
    """手动触发选基金/股票（管理员用，正常由定时任务触发）"""
    global _fund_pick_running
    if _fund_pick_running:
        return jsonify({'status': 'busy', 'message': '选基正在进行中，请稍候'}), 429

    def do_pick():
        global _fund_pick_running
        with _fund_pick_lock:
            _fund_pick_running = True
            try:
                run_fund_pick()
            except Exception as e:
                print(f'[fund_pick] 手动触发失败: {e}')
            finally:
                _fund_pick_running = False

    t = threading.Thread(target=do_pick, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'message': '选基金/股票已启动'})

# ==================== AI 代理 ====================

import urllib.request as _urllib_req
import urllib.error as _urllib_err

# AI 提供商配置（与小程序端保持一致）
_AI_PROVIDERS = {
    'zhipu': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
    'siliconflow': 'https://api.siliconflow.cn/v1/chat/completions',
    'deepseek': 'https://api.deepseek.com/v1/chat/completions',
}

@app.route('/api/ai-proxy', methods=['POST', 'OPTIONS'])
def api_ai_proxy():
    """AI 请求代理：小程序发请求到此端点，服务器转发到外部 AI API。
    解决微信真机域名白名单限制。"""
    if request.method == 'OPTIONS':
        return '', 204

    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({'error': '请求体为空'}), 400

    provider = data.get('provider', 'zhipu')
    api_key = data.get('api_key', '')
    req_body = data.get('body', {})

    # 确定目标 API URL
    api_url = _AI_PROVIDERS.get(provider)
    if not api_url:
        api_url = data.get('api_base', '')
    if not api_url:
        return jsonify({'error': f'未知 AI 提供商: {provider}'}), 400

    # 如未提供 key，使用服务器默认 key
    if not api_key:
        if provider == 'deepseek':
            api_key = os.environ.get('DEEPSEEK_API_KEY', 'sk-1986e1cd1169405f96649311dcfc76aa')
        elif provider == 'zhipu':
            api_key = os.environ.get('AI_API_KEY', '4511f9dee1e64b7da49a539ddef85dfd.Z6HgN8s8cDhL2LeQ')

    if not api_key:
        return jsonify({'error': '缺少 API Key'}), 400

    # 转发请求
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    encoded = json.dumps(req_body, ensure_ascii=False).encode('utf-8')
    req_obj = _urllib_req.Request(api_url, data=encoded, headers=headers, method='POST')

    try:
        with _urllib_req.urlopen(req_obj, timeout=300) as resp:
            body = resp.read().decode('utf-8')
            return app.response_class(body, status=200, mimetype='application/json')
    except _urllib_err.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace') if e.fp else ''
        print(f'[AI proxy] HTTP {e.code}: {err_body[:500]}')
        return jsonify({'error': f'AI API 返回 {e.code}', 'detail': err_body[:500]}), e.code
    except _urllib_err.URLError as e:
        print(f'[AI proxy] URLError: {e.reason}')
        return jsonify({'error': f'无法连接 AI 服务: {e.reason}'}), 502
    except Exception as e:
        print(f'[AI proxy] 异常: {e}')
        return jsonify({'error': str(e)}), 500


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
    - 交易时段：COLLECT_INTERVAL（默认30分钟）
    - 非交易日/非交易时段：3600秒（1小时）"""
    if is_trading_hours():
        return COLLECT_INTERVAL
    return max(COLLECT_INTERVAL, 3600)  # 非交易时段至少1小时

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
                    # 1. 采集热点事件（宏观新闻 + AI结构化提取）
                    try:
                        print('[定时任务] 📡 采集热点事件...')
                        fetch_hot_events()
                        print('[定时任务] ✅ 热点事件采集完成')
                    except SystemExit:
                        print('[定时任务] ⚠️ 热点事件脚本调用了 sys.exit，已拦截')
                    except Exception as e:
                        print(f'[定时任务] ⚠️ 热点事件采集失败: {e}')
                    # 2. 采集舆情数据 + AI 分析
                    collect_and_save()
                    print(f'[定时任务] 采集完成，下次: {interval}秒({interval//60}分钟)后\n')
                except SystemExit:
                    print(f'[定时任务] ⚠️ 采集过程调用了 sys.exit，已拦截')
                except Exception as e:
                    print(f'[定时任务] 采集异常: {e}')
                finally:
                    _collecting = False
            time.sleep(interval)
        else:
            time.sleep(10)


# ==================== 实时突发新闻高频采集线程 ====================
_rt_breaking_lock = threading.Lock()
_rt_breaking_running = False

REALTIME_INTERVAL_TRADING = int(os.environ.get('REALTIME_INTERVAL_TRADING', 300))    # 交易时段5分钟
REALTIME_INTERVAL_OFF = int(os.environ.get('REALTIME_INTERVAL_OFF', 300))              # 非交易时段也5分钟(全天候高频)

def realtime_breaking_loop():
    """全天候高频实时突发新闻采集线程（每5分钟，24/7不间断）"""
    global _rt_breaking_running
    time.sleep(8)  # 启动后等8秒
    print('[realtime_breaking] ⚡ 实时突发新闻监控线程已启动 (全天候24/7)')

    while True:
        if _rt_breaking_running:
            time.sleep(5)
            continue

        interval = REALTIME_INTERVAL_TRADING if is_trading_hours() else REALTIME_INTERVAL_OFF
        trading_label = '交易时段' if is_trading_hours() else '非交易时段'

        with _rt_breaking_lock:
            _rt_breaking_running = True
            try:
                print(f'\n[realtime_breaking] {datetime.now().strftime("%H:%M:%S")} [{trading_label}] 采集实时突发...')
                fetch_realtime_breaking()
                print(f'[realtime_breaking] ✅ 完成，下次: {interval}秒后')
            except SystemExit:
                print('[realtime_breaking] ⚠️ 脚本调用了 sys.exit，已拦截')
            except Exception as e:
                print(f'[realtime_breaking] ❌ 异常: {e}')
                import traceback
                traceback.print_exc()
            finally:
                _rt_breaking_running = False

        time.sleep(interval)


def fund_pick_scheduler_loop():
    """每日 14:50 自动执行选基金/股票

    工作逻辑：
    - 每分钟检查一次时间
    - 如果是交易日 14:50 且今天还没有生成过推荐，自动执行
    - 结果缓存在 data/fund_pick.json
    """
    global _fund_pick_running
    _last_pick_date = None
    time.sleep(15)  # 启动后等待15秒
    print('[fund_pick_scheduler] 选基金定时任务已启动 (每日 14:50)')

    while True:
        try:
            now = datetime.now()
            today = now.strftime('%Y-%m-%d')

            # 检查条件：交易日 + 14:50 + 今日未执行
            if (is_trading_day() and
                now.hour == 14 and now.minute >= 50 and
                _last_pick_date != today and
                not _fund_pick_running):

                print(f'\n[fund_pick_scheduler] ⏰ {now.strftime("%H:%M")} 触发每日选基...')
                with _fund_pick_lock:
                    _fund_pick_running = True
                    try:
                        run_fund_pick()
                        _last_pick_date = today
                    except Exception as e:
                        print(f'[fund_pick_scheduler] ❌ 选基失败: {e}')
                        import traceback
                        traceback.print_exc()
                    finally:
                        _fund_pick_running = False
        except Exception as e:
            print(f'[fund_pick_scheduler] 异常: {e}')

        time.sleep(30)  # 每30秒检查一次


# ==================== 启动后台采集线程 ====================
_scheduler_started = False
_scheduler_lock = threading.Lock()
_scheduler_flock = None   # file lock to prevent duplicate threads across workers

def _ensure_scheduler():
    global _scheduler_started, _scheduler_flock
    if _scheduler_started:
        return
    with _scheduler_lock:
        if _scheduler_started:          # double-check
            return
        # Inter-process file lock: only ONE gunicorn worker runs background threads
        lock_path = os.path.join(ROOT_DIR, '.scheduler.lock')
        try:
            _scheduler_flock = open(lock_path, 'w')
            fcntl.flock(_scheduler_flock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            # Another worker already holds the lock — skip background threads
            print('[scheduler] 另一个worker已持有调度锁，跳过后台线程')
            _scheduler_started = True
            return
        _scheduler_started = True
        t = threading.Thread(target=scheduler_loop, daemon=True)
        t.start()
        print('[scheduler] 定时采集线程已启动')
        t2 = threading.Thread(target=fund_pick_scheduler_loop, daemon=True)
        t2.start()
        print('[scheduler] 选基金定时线程已启动 (每日14:50)')
        t3 = threading.Thread(target=realtime_breaking_loop, daemon=True)
        t3.start()
        print('[scheduler] ⚡ 实时突发新闻线程已启动 (交易时段每5分钟)')

# gunicorn 兼容：通过 before_request 在第一次请求时启动采集线程
@app.before_request
def _lazy_start_scheduler():
    _ensure_scheduler()
    # 安全移除：避免并发 remove 导致 ValueError
    try:
        app.before_request_funcs[None].remove(_lazy_start_scheduler)
    except (ValueError, KeyError):
        pass

# ==================== 入口 ======================================
if __name__ == '__main__':
    print(f'''
╔══════════════════════════════════════════════════╗
║  📊 Fund-Assistant 舆情分析后端                  ║
║  端口: {PORT:<6}                                  ║
║  采集间隔: {COLLECT_INTERVAL}秒 ({COLLECT_INTERVAL//60}分钟)                       ║
║  实时突发: 交易{REALTIME_INTERVAL_TRADING}秒/非交易{REALTIME_INTERVAL_OFF}秒       ║
║  选基金: 每日 14:50 自动执行                     ║
║  API:                                            ║
║    GET  /api/sentiment        → 舆情数据         ║
║    GET  /api/analysis         → AI分析结果       ║
║    GET  /api/fund-pick        → AI选基结果       ║
║    GET  /api/realtime-breaking→ 实时突发+异动    ║
║    POST /api/refresh          → 手动刷新         ║
║    POST /api/reanalyze        → 重新AI分析       ║
║    POST /api/fund-pick/trigger→ 手动触发选基     ║
║    GET  /api/status           → 服务状态         ║
╚══════════════════════════════════════════════════╝
''')
    app.run(host='0.0.0.0', port=PORT, debug=False)
