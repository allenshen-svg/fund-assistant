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
from urllib.parse import urlencode

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
from scripts.portfolio_advisor import run_portfolio_advice, load_portfolio_advice_cache
from scripts.sim_auto_trader import (
    get_auto_trade_config,
    get_status_payload as get_sim_auto_status_payload,
    run_auto_trade as run_sim_auto_trade,
    run_weekly_review as run_sim_auto_weekly_review,
    update_auto_trade_config,
)
from scripts.stock_screener import run_stock_screen, load_stock_screen_cache

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
        # 标记缓存年龄 & 过期
        updated = data.get('updated_at', '')
        if updated:
            try:
                from datetime import timezone as _tz
                ut = datetime.fromisoformat(updated)
                age = (datetime.now(ut.tzinfo or _tz.utc) - ut).total_seconds()
                data['cache_age_seconds'] = int(age)
                stale_thr = COLLECT_INTERVAL * 2.5 if is_trading_hours() else 7200
                data['stale'] = age > stale_thr
            except Exception:
                pass
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
        # 标记缓存年龄 & 过期
        updated = data.get('updated_at', '')
        if updated:
            try:
                from datetime import timezone as _tz
                ut = datetime.fromisoformat(updated)
                age = (datetime.now(ut.tzinfo or _tz.utc) - ut).total_seconds()
                data['cache_age_seconds'] = int(age)
                rt_stale_thr = (REALTIME_INTERVAL_TRADING * 3) if is_trading_hours() else (REALTIME_INTERVAL_OFF * 3)
                data['stale'] = age > rt_stale_thr
            except Exception:
                pass
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/status')
def api_status():
    """服务状态（含各管线健康度）"""
    cache = load_cache()
    analysis = load_analysis_cache()
    now_ts = time.time()

    # ---- hot_events ----
    hot_path = os.path.join(ROOT_DIR, 'data', 'hot_events.json')
    hot_updated = None
    hot_count = 0
    if os.path.exists(hot_path):
        try:
            with open(hot_path, 'r', encoding='utf-8') as f:
                hd = json.load(f)
            hot_updated = hd.get('updated_at')
            hot_count = len(hd.get('events', []))
        except Exception:
            pass

    # ---- realtime_breaking ----
    rt_path = os.path.join(ROOT_DIR, 'data', 'realtime_breaking.json')
    rt_updated = None
    rt_count = 0
    rt_sources_ok = []
    if os.path.exists(rt_path):
        try:
            with open(rt_path, 'r', encoding='utf-8') as f:
                rd = json.load(f)
            rt_updated = rd.get('updated_at')
            rt_count = len(rd.get('breaking', []))
            rt_sources_ok = rd.get('meta', {}).get('sources_ok', [])
        except Exception:
            pass

    # ---- staleness detection (seconds) ----
    def _age(iso_str):
        if not iso_str:
            return None
        try:
            from datetime import timezone as _tz
            dt = datetime.fromisoformat(str(iso_str))
            return (datetime.now(dt.tzinfo or _tz.utc) - dt).total_seconds()
        except Exception:
            return None

    hot_age = _age(hot_updated)
    rt_age = _age(rt_updated)
    sentiment_age = None
    if cache and cache.get('fetch_time'):
        sentiment_age = _age(cache['fetch_time'])

    # ---- analysis age ----
    analysis_age = None
    analysis_time_str = analysis.get('analysis_time') if analysis else None
    if analysis_time_str:
        try:
            from datetime import timezone as _tz2
            adt = datetime.strptime(analysis_time_str, '%Y-%m-%d %H:%M:%S')
            analysis_age = (datetime.now() - adt).total_seconds()
        except Exception:
            pass

    # 阈值: 交易时段更严格
    trading = is_trading_hours()
    hot_stale_thr = COLLECT_INTERVAL * 2.5 if trading else 7200
    rt_stale_thr = (REALTIME_INTERVAL_TRADING * 3) if trading else (REALTIME_INTERVAL_OFF * 3)
    sentiment_stale_thr = COLLECT_INTERVAL * 2.5 if trading else 7200
    analysis_stale_thr = 18000  # 5小时: 11:30→14:50 间隔3h20m, 留余量

    # ---- thread health ----
    threads_alive = {name: t.is_alive() for name, t in _bg_threads.items()}

    return jsonify({
        'server': 'running',
        'collecting': _collecting,
        'cache_exists': cache is not None,
        'analysis_exists': analysis is not None,
        'last_fetch': cache.get('fetch_time') if cache else None,
        'last_analysis': analysis.get('analysis_time') if analysis else None,
        'last_hot_events': hot_updated,
        'last_realtime_breaking': rt_updated,
        'total_items': cache.get('total', 0) if cache else 0,
        'interval_sec': COLLECT_INTERVAL,
        # per-pipeline health
        'pipelines': {
            'hot_events': {
                'last_updated': hot_updated,
                'age_seconds': int(hot_age) if hot_age is not None else None,
                'stale': hot_age is not None and hot_age > hot_stale_thr,
                'item_count': hot_count,
            },
            'realtime_breaking': {
                'last_updated': rt_updated,
                'age_seconds': int(rt_age) if rt_age is not None else None,
                'stale': rt_age is not None and rt_age > rt_stale_thr,
                'item_count': rt_count,
                'sources_ok': rt_sources_ok,
            },
            'sentiment': {
                'last_updated': cache.get('fetch_time') if cache else None,
                'age_seconds': int(sentiment_age) if sentiment_age is not None else None,
                'stale': sentiment_age is not None and sentiment_age > sentiment_stale_thr,
                'item_count': cache.get('total', 0) if cache else 0,
            },
            'analysis': {
                'last_updated': analysis_time_str,
                'age_seconds': int(analysis_age) if analysis_age is not None else None,
                'stale': analysis_age is not None and analysis_age > analysis_stale_thr,
                'model': analysis.get('model') if analysis else None,
                'provider': analysis.get('provider') if analysis else None,
            },
        },
        'threads': threads_alive,
        'is_trading_hours': trading,
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

# ==================== 实盘行动指南 ====================

_portfolio_lock = threading.Lock()
_portfolio_running = False

_stock_screen_lock = threading.Lock()
_stock_screen_running = False
_sim_auto_lock = threading.Lock()
_sim_auto_running = False
_sim_review_lock = threading.Lock()
_sim_review_running = False
_sim_auto_ops_lock = threading.Lock()
_stock_screen_notify_lock = threading.Lock()
_wechat_token_cache = {'token': '', 'expires_at': 0}
_stock_screen_subscribers_file = os.path.join(ROOT_DIR, 'data', 'stock_screen_subscribers.json')


def _get_stock_screen_notify_config():
    appid = str(env('WECHAT_APPID', infra.appid or '') or '').strip()
    secret = str(env('WECHAT_APP_SECRET', '') or '').strip()
    template_id = str(env('WECHAT_SUBSCRIBE_TEMPLATE_ID', '') or '').strip()
    title_field = str(env('WECHAT_SUBSCRIBE_FIELD_TITLE', 'thing1') or 'thing1').strip()
    time_field = str(env('WECHAT_SUBSCRIBE_FIELD_TIME', 'time2') or 'time2').strip()
    remark_field = str(env('WECHAT_SUBSCRIBE_FIELD_REMARK', 'thing3') or 'thing3').strip()
    page = str(env('WECHAT_SUBSCRIBE_PAGE', 'pages/stock-screen/index') or 'pages/stock-screen/index').strip()

    available = bool(appid and secret and template_id)
    reason = '' if available else '服务器暂未配置订阅消息所需的 AppSecret 或模板 ID'
    return {
        'available': available,
        'reason': reason,
        'appid': appid,
        'secret': secret,
        'templateId': template_id,
        'page': page,
        'titleField': title_field,
        'timeField': time_field,
        'remarkField': remark_field,
    }


def _get_public_stock_screen_notify_meta():
    config = _get_stock_screen_notify_config()
    return {
        'available': config['available'],
        'reason': config['reason'],
        'templateId': config['templateId'] if config['available'] else '',
        'page': config['page'],
    }


def _load_stock_screen_subscribers():
    if not os.path.exists(_stock_screen_subscribers_file):
        return {'subscribers': []}
    try:
        with open(_stock_screen_subscribers_file, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        if isinstance(data, dict) and isinstance(data.get('subscribers'), list):
            return data
    except Exception as exc:
        print(f'[stock_screen] 读取订阅列表失败: {exc}')
    return {'subscribers': []}


def _save_stock_screen_subscribers(data):
    os.makedirs(os.path.dirname(_stock_screen_subscribers_file), exist_ok=True)
    with open(_stock_screen_subscribers_file, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def _truncate_text(value, max_len):
    text = str(value or '').replace('\n', ' ').strip()
    if len(text) <= max_len:
        return text
    return text[: max(0, max_len - 1)] + '…'


def _format_subscribe_time(timestamp):
    try:
        dt = datetime.fromisoformat(str(timestamp))
        return dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d %H:%M')


def _upsert_stock_screen_subscriber(openid, template_id, page):
    now_text = datetime.now().isoformat()
    with _stock_screen_notify_lock:
        data = _load_stock_screen_subscribers()
        subscribers = data.setdefault('subscribers', [])
        matched = None
        for item in subscribers:
            if item.get('openid') == openid:
                matched = item
                break

        if matched is None:
            matched = {
                'openid': openid,
                'template_id': template_id,
                'page': page,
                'status': 'pending',
                'created_at': now_text,
                'updated_at': now_text,
                'last_error': '',
                'fail_count': 0,
            }
            subscribers.append(matched)
        else:
            matched['template_id'] = template_id
            matched['page'] = page or matched.get('page') or 'pages/stock-screen/index'
            matched['status'] = 'pending'
            matched['updated_at'] = now_text
            matched['last_error'] = ''

        _save_stock_screen_subscribers(data)

    return {
        'status': matched.get('status'),
        'updatedAt': matched.get('updated_at'),
    }


def _fetch_json_via_urllib(url, payload=None):
    data = None
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = _urllib_req.Request(url, data=data, headers=headers)
    with _urllib_req.urlopen(req, timeout=12) as response:
        body = response.read().decode('utf-8')
        return json.loads(body)


def _get_wechat_access_token(config):
    now_ts = time.time()
    if _wechat_token_cache['token'] and _wechat_token_cache['expires_at'] > now_ts + 60:
        return _wechat_token_cache['token']

    query = urlencode({
        'grant_type': 'client_credential',
        'appid': config['appid'],
        'secret': config['secret'],
    })
    token_data = _fetch_json_via_urllib(f'https://api.weixin.qq.com/cgi-bin/token?{query}')
    token = token_data.get('access_token', '')
    expires_in = int(token_data.get('expires_in', 0) or 0)
    if not token:
        raise RuntimeError(token_data.get('errmsg') or '获取微信 access_token 失败')
    _wechat_token_cache['token'] = token
    _wechat_token_cache['expires_at'] = now_ts + max(0, expires_in - 120)
    return token


def _exchange_wechat_login_code(code, config):
    query = urlencode({
        'appid': config['appid'],
        'secret': config['secret'],
        'js_code': code,
        'grant_type': 'authorization_code',
    })
    data = _fetch_json_via_urllib(f'https://api.weixin.qq.com/sns/jscode2session?{query}')
    openid = data.get('openid', '')
    if not openid:
        raise RuntimeError(data.get('errmsg') or '微信登录态换取 openid 失败')
    return openid


def _build_stock_screen_subscribe_message(payload, config):
    result = (payload or {}).get('result') or {}
    picks = result.get('picks') or []
    top_pick = picks[0] if picks else {}
    qualified_count = int(result.get('qualifiedCount', 0) or 0)

    title = f'自动选股已完成，命中{qualified_count}只'
    if top_pick.get('name'):
        title = f'自动选股完成，首选{top_pick.get("name")}'

    if picks:
        remark = f'共命中{qualified_count}只，可回小程序查看完整结果'
    else:
        remark = '本轮暂无达标标的，可回小程序查看市场总结'

    return {
        config['titleField']: {'value': _truncate_text(title, 20)},
        config['timeField']: {'value': _format_subscribe_time(payload.get('timestamp'))},
        config['remarkField']: {'value': _truncate_text(remark, 20)},
    }


def _send_stock_screen_notifications(payload):
    config = _get_stock_screen_notify_config()
    if not config['available']:
        return {'sent': 0, 'failed': 0, 'pending': 0, 'reason': config['reason']}

    with _stock_screen_notify_lock:
        data = _load_stock_screen_subscribers()
        subscribers = data.setdefault('subscribers', [])
        pending = [item for item in subscribers if item.get('status') == 'pending']

    if not pending:
        return {'sent': 0, 'failed': 0, 'pending': 0, 'reason': 'no_pending_subscribers'}

    try:
        access_token = _get_wechat_access_token(config)
    except Exception as exc:
        print(f'[stock_screen] 获取微信 access_token 失败: {exc}')
        return {'sent': 0, 'failed': len(pending), 'pending': len(pending), 'reason': str(exc)}

    sent_count = 0
    failed_count = 0
    now_text = datetime.now().isoformat()
    message_data = _build_stock_screen_subscribe_message(payload, config)

    with _stock_screen_notify_lock:
        data = _load_stock_screen_subscribers()
        subscribers = data.setdefault('subscribers', [])

        for item in subscribers:
            if item.get('status') != 'pending':
                continue

            template_id = item.get('template_id') or config['templateId']
            body = {
                'touser': item.get('openid', ''),
                'template_id': template_id,
                'page': item.get('page') or config['page'],
                'data': message_data,
            }

            try:
                response = _fetch_json_via_urllib(
                    f'https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token={access_token}',
                    payload=body,
                )
                if int(response.get('errcode', -1) or -1) == 0:
                    item['status'] = 'sent'
                    item['sent_at'] = now_text
                    item['updated_at'] = now_text
                    item['last_error'] = ''
                    sent_count += 1
                else:
                    item['status'] = 'failed'
                    item['updated_at'] = now_text
                    item['last_error'] = response.get('errmsg', '发送失败')
                    item['fail_count'] = int(item.get('fail_count', 0) or 0) + 1
                    failed_count += 1
            except Exception as exc:
                item['status'] = 'failed'
                item['updated_at'] = now_text
                item['last_error'] = str(exc)
                item['fail_count'] = int(item.get('fail_count', 0) or 0) + 1
                failed_count += 1

        _save_stock_screen_subscribers(data)

    return {
        'sent': sent_count,
        'failed': failed_count,
        'pending': max(0, len(pending) - sent_count - failed_count),
        'reason': '',
    }


def _run_stock_screen_with_notify(trigger_time):
    payload = run_stock_screen()
    if isinstance(payload, dict):
        payload['triggerTime'] = trigger_time
        try:
            with open(os.path.join(ROOT_DIR, 'data', 'stock_screen.json'), 'w', encoding='utf-8') as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f'[stock_screen] 更新缓存触发来源失败: {exc}')

    notify_summary = _send_stock_screen_notifications(payload or {})
    print(f'[stock_screen] 通知结果: sent={notify_summary.get("sent", 0)} failed={notify_summary.get("failed", 0)} reason={notify_summary.get("reason", "")}')
    return payload

@app.route('/api/portfolio-advice')
def api_portfolio_advice():
    """返回最新的实盘行动指南（由每日 14:50 自动生成）"""
    data = load_portfolio_advice_cache()
    if data is None:
        return jsonify({'status': 'no_data', 'message': '暂无行动指南，请等待每日 14:50 自动生成'}), 200
    return jsonify(data)

@app.route('/api/portfolio-advice/trigger', methods=['POST'])
def api_portfolio_advice_trigger():
    """手动触发实盘行动指南"""
    global _portfolio_running
    if _portfolio_running:
        return jsonify({'status': 'busy', 'message': '行动指南正在生成中，请稍候'}), 429

    def do_advice():
        global _portfolio_running
        with _portfolio_lock:
            _portfolio_running = True
            try:
                run_portfolio_advice()
            except Exception as e:
                print(f'[portfolio] 手动触发失败: {e}')
            finally:
                _portfolio_running = False

    t = threading.Thread(target=do_advice, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'message': '实盘行动指南已启动'})


# ==================== 每日形态选股 ====================

@app.route('/api/stock-screen')
def api_stock_screen():
    """返回最新的A股形态选股结果（每日自动生成）"""
    data = load_stock_screen_cache()
    payload = {
        'status': 'ok' if data else 'no_data',
        'running': _stock_screen_running,
        'notify': _get_public_stock_screen_notify_meta(),
        'message': '暂无形态选股结果，请等待每日自动生成' if data is None else '',
    }
    if data:
        payload.update(data)
    return jsonify(payload)


@app.route('/api/stock-screen/subscribe', methods=['POST'])
def api_stock_screen_subscribe():
    """登记本次选股结果订阅消息"""
    config = _get_stock_screen_notify_config()
    if not config['available']:
        return jsonify({
            'status': 'unavailable',
            'message': config['reason'],
            'notify': _get_public_stock_screen_notify_meta(),
        }), 503

    payload = request.get_json(silent=True) or {}
    code = str(payload.get('code', '') or '').strip()
    template_id = str(payload.get('templateId', '') or config['templateId']).strip()
    page = str(payload.get('page', '') or config['page']).strip()
    if not code:
        return jsonify({'status': 'error', 'message': '缺少微信登录 code'}), 400

    try:
        openid = _exchange_wechat_login_code(code, config)
        subscription = _upsert_stock_screen_subscriber(openid, template_id, page)
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 400

    return jsonify({
        'status': 'subscribed',
        'message': '已登记结果通知，任务完成后会尝试发送微信订阅消息',
        'subscription': subscription,
        'notify': _get_public_stock_screen_notify_meta(),
    })


@app.route('/api/stock-screen/trigger', methods=['POST'])
def api_stock_screen_trigger():
    """手动触发A股形态选股"""
    global _stock_screen_running
    if _stock_screen_running:
        return jsonify({'status': 'busy', 'message': '形态选股正在进行中，请稍候'}), 429

    def do_screen():
        global _stock_screen_running
        with _stock_screen_lock:
            _stock_screen_running = True
            try:
                _run_stock_screen_with_notify('manual')
            except Exception as e:
                print(f'[stock_screen] 手动触发失败: {e}')
            finally:
                _stock_screen_running = False

    t = threading.Thread(target=do_screen, daemon=True)
    t.start()
    return jsonify({'status': 'started', 'message': 'A股形态选股已启动'})


# ==================== 自动模拟仓 ====================

@app.route('/api/sim-auto')
def api_sim_auto_status():
    """返回服务端自动模拟仓状态。"""
    payload = get_sim_auto_status_payload()
    payload['running'] = _sim_auto_running
    payload['reviewRunning'] = _sim_review_running
    return jsonify(payload)


@app.route('/api/sim-auto/trigger', methods=['POST'])
def api_sim_auto_trigger():
    """自动模拟仓仅允许定时执行，不再开放手动触发。"""
    return jsonify({
        'status': 'disabled',
        'message': '自动模拟仓仅支持系统在交易日 14:55 定时执行，不再提供手动触发入口',
    }), 403


@app.route('/api/sim-auto/review/trigger', methods=['POST'])
def api_sim_auto_review_trigger():
    """自动模拟仓周复盘仅允许定时执行，不再开放手动触发。"""
    return jsonify({
        'status': 'disabled',
        'message': '自动模拟仓周复盘仅支持系统在每周五 15:10 定时执行，不再提供手动触发入口',
    }), 403


@app.route('/api/sim-auto/reset', methods=['POST'])
def api_sim_auto_reset():
    """自动模拟仓已改为只读展示，不再开放手动重置。"""
    return jsonify({
        'status': 'disabled',
        'message': '自动模拟仓当前仅保留定时执行与结果展示，不再提供手动重置入口',
    }), 403


@app.route('/api/sim-auto/config', methods=['GET', 'POST'])
def api_sim_auto_config():
    """读取或更新自动模拟仓参数。"""
    if request.method == 'GET':
        return jsonify({'status': 'ok', 'config': get_auto_trade_config()})

    payload = request.get_json(silent=True) or {}
    config = update_auto_trade_config(payload)
    return jsonify({'status': 'ok', 'config': config, 'message': '自动模拟仓参数已更新'})

# ==================== AI 代理 ====================

import urllib.request as _urllib_req
import urllib.error as _urllib_err

# AI 提供商配置（与小程序端保持一致）
_AI_PROVIDERS = {
    'zhipu': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
    'siliconflow': 'https://api.siliconflow.cn/v1/chat/completions',
    'deepseek': 'https://api.deepseek.com/v1/chat/completions',
    '302ai': 'https://api.302.ai/v1/chat/completions',
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

    # 兼容前端 callAI() 直接传 messages/model 在顶层的格式
    if not req_body and data.get('messages'):
        req_body = {
            'model': data.get('model', 'GLM-4-Flash'),
            'messages': data.get('messages', []),
            'temperature': data.get('temperature', 0.7),
            'max_tokens': min(data.get('max_tokens', 4096), 16384),
        }

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
        elif provider == 'siliconflow':
            api_key = os.environ.get('SILICONFLOW_API_KEY', 'sk-njqerftsrrnojbsdagigsrzbwwxgtuhrsyihphcxvsdpbaxl')

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
    """动态间隔采集：交易时段按 COLLECT_INTERVAL，非交易日/时段每2小时
    板块深度分析改为每日 2 次定时（11:30 和 14:50），不再跟随每次采集"""
    global _collecting
    _last_analysis_slot = None  # 记录已执行的分析时段，避免重复
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
                    # 2. 采集舆情数据（不自动触发 AI 分析）
                    collect_and_save(run_analysis=False)
                    print(f'[定时任务] 采集完成，下次: {interval}秒({interval//60}分钟)后')

                    # 3. 板块深度分析：每日 11:30 和 14:50 各执行一次
                    now = datetime.now()
                    today = now.strftime('%Y-%m-%d')
                    slot = None
                    if now.hour == 11 and now.minute >= 30 or (now.hour == 12 and now.minute < 30):
                        slot = f'{today}-1130'
                    elif now.hour == 14 and now.minute >= 50 or (now.hour == 15 and now.minute < 20):
                        slot = f'{today}-1450'
                    if slot and slot != _last_analysis_slot:
                        print(f'[定时任务] 🧠 触发板块深度分析 ({slot})...')
                        try:
                            cache = load_cache()
                            if cache and cache.get('items'):
                                analyze_and_save(cache['items'])
                                print(f'[定时任务] ✅ 板块深度分析完成')
                            else:
                                print(f'[定时任务] ⚠️ 无舆情数据，跳过板块深度分析')
                        except Exception as e:
                            print(f'[定时任务] ❌ 板块深度分析失败: {e}')
                        _last_analysis_slot = slot

                    print()
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

REALTIME_INTERVAL_TRADING = int(os.environ.get('REALTIME_INTERVAL_TRADING', 180))    # 交易时段3分钟(v2并行抓取更快)
REALTIME_INTERVAL_OFF = int(os.environ.get('REALTIME_INTERVAL_OFF', 300))              # 非交易时段5分钟

def realtime_breaking_loop():
    """全天候高频实时突发新闻采集线程（v2算法, 24/7不间断）"""
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
    """每日定时任务调度

    工作逻辑：
    - 每分钟检查一次时间
    - 交易日 14:00：自动触发A股形态选股（每日仅一次）
    - 交易日 14:50：自动执行选基推荐 + 行动指南（每日仅一次）
    - 交易日 14:55：自动执行模拟仓调仓（每日仅一次）
    - 交易日 周五 15:10+：自动执行模拟仓周复盘（每周仅一次）
    - 非交易日（周末/节假日）不执行任何任务
    """
    global _fund_pick_running, _portfolio_running, _stock_screen_running
    global _sim_auto_running, _sim_review_running
    _last_pick_date = None
    _last_screen_date = None
    _last_sim_trade_date = None
    _last_sim_review_week = None
    time.sleep(15)  # 启动后等待15秒
    print('[fund_pick_scheduler] 定时任务已启动 (选股 14:00 / 推荐 14:50 / 调仓 14:55)')

    while True:
        try:
            now = datetime.now()
            today = now.strftime('%Y-%m-%d')

            # ---- 交易日 14:00 触发A股形态选股（每日仅一次）----
            if (is_trading_day() and
                now.hour == 14 and now.minute >= 0 and
                _last_screen_date != today and
                not _stock_screen_running):

                print(f'\n[fund_pick_scheduler] ⏰ {now.strftime("%H:%M")} 触发A股形态选股...')
                with _stock_screen_lock:
                    _stock_screen_running = True
                    try:
                        _run_stock_screen_with_notify('daily')
                    except Exception as e:
                        print(f'[fund_pick_scheduler] ❌ 形态选股失败: {e}')
                        import traceback
                        traceback.print_exc()
                    finally:
                        _stock_screen_running = False
                _last_screen_date = today

            # ---- 交易日 14:50 触发选基推荐 + 行动指南（每日仅一次）----
            if (is_trading_day() and
                now.hour == 14 and now.minute >= 50 and
                _last_pick_date != today and
                not _fund_pick_running):

                print(f'\n[fund_pick_scheduler] ⏰ {now.strftime("%H:%M")} 触发每日选基...')
                with _fund_pick_lock:
                    _fund_pick_running = True
                    try:
                        run_fund_pick()
                    except Exception as e:
                        print(f'[fund_pick_scheduler] ❌ 选基失败: {e}')
                        import traceback
                        traceback.print_exc()
                    finally:
                        _fund_pick_running = False

                print(f'[fund_pick_scheduler] ⏰ {now.strftime("%H:%M")} 触发实盘行动指南...')
                with _portfolio_lock:
                    _portfolio_running = True
                    try:
                        run_portfolio_advice()
                    except Exception as e:
                        print(f'[fund_pick_scheduler] ❌ 行动指南失败: {e}')
                        import traceback
                        traceback.print_exc()
                    finally:
                        _portfolio_running = False

                _last_pick_date = today

            if (is_trading_day() and
                now.hour == 14 and now.minute >= 55 and
                _last_sim_trade_date != today and
                not _sim_auto_running):

                print(f'[fund_pick_scheduler] ⏰ {now.strftime("%H:%M")} 触发自动模拟仓调仓...')
                with _sim_auto_ops_lock:
                    with _sim_auto_lock:
                        _sim_auto_running = True
                        try:
                            result = run_sim_auto_trade(now=now)
                            print(f'[fund_pick_scheduler] ✅ 自动模拟仓状态: {result.get("status")}')
                        except Exception as e:
                            print(f'[fund_pick_scheduler] ❌ 自动模拟仓失败: {e}')
                            import traceback
                            traceback.print_exc()
                        finally:
                            _sim_auto_running = False
                _last_sim_trade_date = today

            week_key = f'{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}'
            if (is_trading_day() and now.weekday() == 4 and
                (now.hour > 15 or (now.hour == 15 and now.minute >= 10)) and
                _last_sim_review_week != week_key and
                not _sim_review_running):

                print(f'\n[fund_pick_scheduler] ⏰ {now.strftime("%H:%M")} 触发自动模拟仓周复盘...')
                with _sim_auto_ops_lock:
                    with _sim_review_lock:
                        _sim_review_running = True
                        try:
                            result = run_sim_auto_weekly_review(now=now)
                            print(f'[fund_pick_scheduler] ✅ 自动模拟仓周复盘状态: {result.get("status")}')
                        except Exception as e:
                            print(f'[fund_pick_scheduler] ❌ 自动模拟仓周复盘失败: {e}')
                            import traceback
                            traceback.print_exc()
                        finally:
                            _sim_review_running = False
                _last_sim_review_week = week_key
        except Exception as e:
            print(f'[fund_pick_scheduler] 异常: {e}')

        time.sleep(30)  # 每30秒检查一次


# ==================== 启动后台采集线程 ====================
_scheduler_started = False
_scheduler_lock = threading.Lock()
_scheduler_flock = None   # file lock to prevent duplicate threads across workers
_bg_threads = {}  # name → Thread, for health monitoring

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
        t = threading.Thread(target=scheduler_loop, daemon=True, name='scheduler')
        t.start()
        _bg_threads['scheduler'] = t
        print('[scheduler] 定时采集线程已启动')
        t2 = threading.Thread(target=fund_pick_scheduler_loop, daemon=True, name='fund_pick')
        t2.start()
        _bg_threads['fund_pick'] = t2
        print('[scheduler] 推荐/模拟仓定时线程已启动 (推荐14:50 / 调仓14:55)')
        t3 = threading.Thread(target=realtime_breaking_loop, daemon=True, name='realtime_breaking')
        t3.start()
        _bg_threads['realtime_breaking'] = t3
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
║  形态选股: 交易日 14:00 自动执行                 ║
║  推荐生成: 交易日 14:50 自动执行                 ║
║  模拟仓调仓: 交易日 14:55 自动执行               ║
║  API:                                            ║
║    GET  /api/sentiment        → 舆情数据         ║
║    GET  /api/analysis         → AI分析结果       ║
║    GET  /api/fund-pick        → AI选基结果       ║
║    GET  /api/stock-screen     → 形态选股结果     ║
║    GET  /api/realtime-breaking→ 实时突发+异动    ║
║    POST /api/refresh          → 手动刷新         ║
║    POST /api/reanalyze        → 重新AI分析       ║
║    POST /api/fund-pick/trigger→ 手动触发选基     ║
║    POST /api/stock-screen/trigger → 手动触发选股 ║
║    GET  /api/status           → 服务状态         ║
╚══════════════════════════════════════════════════╝
''')
    app.run(host='0.0.0.0', port=PORT, debug=False)
