#!/usr/bin/env python3
"""服务端自动模拟仓执行器。"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from scripts.fetch_events import main as fetch_hot_events
from scripts.fund_pick import load_fund_pick_cache
from scripts.infra import env
from scripts.portfolio_advisor import (
    fetch_commodities,
    fetch_fund_estimate,
    fetch_indices,
    load_portfolio_advice_cache,
)
from scripts.stock_screener import load_stock_screen_cache

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
PORTFOLIO_FILE = os.path.join(DATA_DIR, 'sim_auto_portfolio.json')
TRADE_FILE = os.path.join(DATA_DIR, 'sim_auto_trade_log.json')
SETTLE_FILE = os.path.join(DATA_DIR, 'sim_auto_settle_log.json')
REVIEW_FILE = os.path.join(DATA_DIR, 'sim_auto_weekly_reviews.json')
CONFIG_FILE = os.path.join(DATA_DIR, 'sim_auto_config.json')
INITIAL_CAPITAL = float(os.environ.get('SIM_AUTO_INITIAL_CAPITAL', 10000) or 10000)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    'Referer': 'https://quote.eastmoney.com/',
}

SELL_SYSTEM_PROMPT = """你是一位严格的量化风控交易员。

你的任务：根据持仓、市场环境、热点、博弈面资金流向与已有行动指南，为模拟仓输出今天是否要减仓。

只输出合法 JSON：
{
  "overview": "一句总结",
  "sellAdvice": [
    {
      "code": "代码",
      "action": "hold|sell_part|sell_all",
      "pct": 0,
      "reason": "原因",
      "confidence": 0
    }
  ],
  "riskAlert": "一句风险提示"
}

规则：
- 只对用户当前持仓给建议
- `sell_part` 的 pct 只能在 10-60 之间
- 无需减仓时返回空数组
- 不要输出 markdown，不要解释

博弈面规则（重要）：
- 若某持仓主力资金连续3日净流出且累计超5000万，即使基本面正常也建议 sell_part 20%
- 若主力大幅流入但股价横盘，可能是吸筹阶段，建议 hold 不要过早止损
- 股价创新高但主力净流出（量价背离），是出货信号，建议 sell_part 25-40%
- 博弈面与技术面/基本面冲突时，优先参考博弈面（资金流向是领先指标）
- 北向资金方向与内资背离时，以北向资金方向为准"""

REVIEW_SYSTEM_PROMPT = """你是一位资深投资复盘分析师。

请对模拟仓过去一周的自动交易做复盘，只输出合法 JSON：
{
  "summary": "本周总结",
  "performance": {
    "totalReturn": "如 +2.3%",
    "bestTrade": "最佳交易",
    "worstTrade": "最差交易"
  },
  "analysis": "盈亏原因分析",
  "lessons": ["教训1", "教训2", "教训3"],
  "nextWeekStrategy": "下周策略",
  "optimizationHints": "后续算法改进建议"
}

要求：
- 客观分析
- 重点看择时、仓位、止盈止损
- 不要输出 markdown 或额外说明"""

DEFAULT_AUTO_TRADE_CONFIG = {
    'minBuyAmount': float(os.environ.get('SIM_AUTO_MIN_BUY_AMOUNT', 600) or 600),
    'targetBuyPct': float(os.environ.get('SIM_AUTO_TARGET_BUY_PCT', 0.10) or 0.10),
    'maxSingleBuyPct': float(os.environ.get('SIM_AUTO_MAX_SINGLE_BUY_PCT', 0.18) or 0.18),
    'maxPositionWeight': float(os.environ.get('SIM_AUTO_MAX_POSITION_WEIGHT', 0.25) or 0.25),
    'maxBuyCandidates': int(os.environ.get('SIM_AUTO_MAX_BUY_CANDIDATES', 2) or 2),
    'fundMinScore': float(os.environ.get('SIM_AUTO_FUND_MIN_SCORE', 68) or 68),
    'stockMinScore': float(os.environ.get('SIM_AUTO_STOCK_MIN_SCORE', 72) or 72),
    'fundScoreBonus': float(os.environ.get('SIM_AUTO_FUND_SCORE_BONUS', 3) or 3),
    'stockRrWeight': float(os.environ.get('SIM_AUTO_STOCK_RR_WEIGHT', 8) or 8),
    'reduceFundPct': float(os.environ.get('SIM_AUTO_REDUCE_FUND_PCT', 25) or 25),
    'stopLossReducePct': float(os.environ.get('SIM_AUTO_STOP_LOSS_REDUCE_PCT', 50) or 50),
    'drawdownTriggerPct': float(os.environ.get('SIM_AUTO_DRAWDOWN_TRIGGER_PCT', -6) or -6),
    'drawdownReducePct': float(os.environ.get('SIM_AUTO_DRAWDOWN_REDUCE_PCT', 35) or 35),
    'takeProfitTriggerPct': float(os.environ.get('SIM_AUTO_TAKE_PROFIT_TRIGGER_PCT', 12) or 12),
    'takeProfitReducePct': float(os.environ.get('SIM_AUTO_TAKE_PROFIT_REDUCE_PCT', 20) or 20),
}


def _coerce_config_value(key: str, value: Any) -> Any:
    template = DEFAULT_AUTO_TRADE_CONFIG.get(key)
    if template is None:
        return value
    if isinstance(template, int):
        return int(float(value))
    return float(value)


def _load_auto_config() -> Dict[str, Any]:
    data = _read_json(CONFIG_FILE, {})
    merged = dict(DEFAULT_AUTO_TRADE_CONFIG)
    if isinstance(data, dict):
        for key, value in data.items():
            if key in DEFAULT_AUTO_TRADE_CONFIG:
                try:
                    merged[key] = _coerce_config_value(key, value)
                except Exception:
                    pass
    return merged

def get_auto_trade_config() -> Dict[str, Any]:
    return dict(AUTO_TRADE_CONFIG)


def update_auto_trade_config(overrides: Dict[str, Any]) -> Dict[str, Any]:
    next_config = dict(AUTO_TRADE_CONFIG)
    for key, value in (overrides or {}).items():
        if key not in DEFAULT_AUTO_TRADE_CONFIG or value in (None, ''):
            continue
        next_config[key] = _coerce_config_value(key, value)

    next_config['minBuyAmount'] = max(100.0, float(next_config['minBuyAmount']))
    next_config['targetBuyPct'] = min(max(float(next_config['targetBuyPct']), 0.01), 0.5)
    next_config['maxSingleBuyPct'] = min(max(float(next_config['maxSingleBuyPct']), next_config['targetBuyPct']), 0.8)
    next_config['maxPositionWeight'] = min(max(float(next_config['maxPositionWeight']), 0.05), 0.8)
    next_config['maxBuyCandidates'] = min(max(int(next_config['maxBuyCandidates']), 1), 5)
    next_config['fundMinScore'] = min(max(float(next_config['fundMinScore']), 0), 100)
    next_config['stockMinScore'] = min(max(float(next_config['stockMinScore']), 0), 100)
    next_config['drawdownTriggerPct'] = min(float(next_config['drawdownTriggerPct']), -0.5)
    next_config['takeProfitTriggerPct'] = max(float(next_config['takeProfitTriggerPct']), 1.0)
    next_config['reduceFundPct'] = min(max(float(next_config['reduceFundPct']), 1.0), 100.0)
    next_config['stopLossReducePct'] = min(max(float(next_config['stopLossReducePct']), 1.0), 100.0)
    next_config['drawdownReducePct'] = min(max(float(next_config['drawdownReducePct']), 1.0), 100.0)
    next_config['takeProfitReducePct'] = min(max(float(next_config['takeProfitReducePct']), 1.0), 100.0)

    AUTO_TRADE_CONFIG.clear()
    AUTO_TRADE_CONFIG.update(next_config)
    _write_json(CONFIG_FILE, AUTO_TRADE_CONFIG)
    return get_auto_trade_config()


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except Exception:
        return default


def _write_json(path: str, payload: Any) -> None:
    _ensure_data_dir()
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


AUTO_TRADE_CONFIG = _load_auto_config()


def today_str(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime('%Y-%m-%d')


def _iso_week_key(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now()
    iso = dt.isocalendar()
    return '%04d-W%02d' % (iso[0], iso[1])


# 中国股市法定节假日（手动维护，每年底更新下一年）
_CN_MARKET_HOLIDAYS: set = {
    # 2026 元旦
    date(2026, 1, 1), date(2026, 1, 2),
    # 2026 春节
    date(2026, 1, 26), date(2026, 1, 27), date(2026, 1, 28),
    date(2026, 1, 29), date(2026, 1, 30),
    date(2026, 2, 2), date(2026, 2, 3),
    # 2026 清明
    date(2026, 4, 6),
    # 2026 劳动节
    date(2026, 5, 1), date(2026, 5, 4), date(2026, 5, 5),
    # 2026 端午
    date(2026, 5, 31), date(2026, 6, 1),
    # 2026 中秋+国庆
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5),
    date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8),
}


def is_trading_day(day: Optional[date] = None) -> bool:
    target = day or date.today()
    if target.weekday() >= 5:
        return False
    return target not in _CN_MARKET_HOLIDAYS


def _secid_for_stock(code: str) -> str:
    code = str(code or '').strip()
    if not code:
        return ''
    if code.startswith(('5', '6', '9')):
        return '1.' + code
    return '0.' + code


def _fetch_json(url: str, timeout: int = 10) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def fetch_capital_flow(code: str) -> Optional[Dict[str, Any]]:
    """获取个股主力资金流向（东方财富 fflow/kline API）。
    返回最近 3 日主力/散户净流入和净流入方向。
    """
    secid = _secid_for_stock(code)
    if not secid:
        return None
    url = (
        'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
        '?secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56'
        '&lmt=5&klt=101'
    ).format(secid=urllib.parse.quote(secid))
    try:
        payload = _fetch_json(url, timeout=8)
        klines = (payload.get('data') or {}).get('klines') or []
        if not klines:
            return None
        # kline format: "date,主力净流入,小单净流入,中单净流入,大单净流入,超大单净流入"
        main_net_3d = 0.0
        retail_net_3d = 0.0
        days_parsed = 0
        for line in klines[-3:]:
            parts = line.split(',')
            if len(parts) < 6:
                continue
            main_net_3d += float(parts[1] or 0)   # 主力净流入
            retail_net_3d += float(parts[2] or 0)  # 小单净流入
            days_parsed += 1
        if days_parsed == 0:
            return None
        # 最新一天
        latest = klines[-1].split(',')
        main_today = float(latest[1] or 0) if len(latest) >= 2 else 0.0
        return {
            'main_net_3d': round(main_net_3d / 1e8, 2),        # 元 → 亿
            'retail_net_3d': round(retail_net_3d / 1e8, 2),
            'main_today': round(main_today / 1e8, 2),
            'main_direction': 'inflow' if main_net_3d > 0 else 'outflow',
            'days': days_parsed,
        }
    except Exception as exc:
        print('[sim_auto] 获取 %s 资金流失败: %s' % (code, exc))
        return None


def fetch_stock_quote(code: str) -> Optional[Dict[str, Any]]:
    secid = _secid_for_stock(code)
    if not secid:
        return None
    fields = 'f57,f58,f43,f170,f169,f46,f60,f44,f45'
    url = 'https://push2.eastmoney.com/api/qt/stock/get?invt=2&fltt=2&fields={fields}&secid={secid}'.format(
        fields=fields,
        secid=urllib.parse.quote(secid),
    )
    try:
        payload = _fetch_json(url, timeout=6)
        data = payload.get('data') or {}
        if not data:
            return None
        price = float(data.get('f43', 0) or 0) / 100.0
        prev_close = float(data.get('f60', 0) or 0) / 100.0
        return {
            'code': str(data.get('f57', code) or code),
            'name': str(data.get('f58', '') or ''),
            'nav': price,
            'estimate': price,
            'pct': float(data.get('f170', 0) or 0) / 100.0,
            'time': datetime.now().isoformat(),
            'prev_close': prev_close,
        }
    except Exception as exc:
        print('[sim_auto] 获取股票 %s 行情失败: %s' % (code, exc))
        return None


def _default_portfolio() -> Dict[str, Any]:
    return {
        'mode': 'server_auto',
        'cash': round(INITIAL_CAPITAL, 2),
        'totalCash': round(INITIAL_CAPITAL, 2),
        'positions': [],
        'createdAt': datetime.now().isoformat(),
        'updatedAt': datetime.now().isoformat(),
        'lastTradeDate': '',
        'lastTradeSummary': {},
        'lastReviewWeek': '',
    }


def load_portfolio() -> Dict[str, Any]:
    data = _read_json(PORTFOLIO_FILE, None)
    if isinstance(data, dict):
        base = _default_portfolio()
        base.update(data)
        if not isinstance(base.get('positions'), list):
            base['positions'] = []
        if not base.get('totalCash'):
            base['totalCash'] = round(INITIAL_CAPITAL, 2)
        return base
    return _default_portfolio()


def save_portfolio(portfolio: Dict[str, Any]) -> Dict[str, Any]:
    portfolio['updatedAt'] = datetime.now().isoformat()
    _write_json(PORTFOLIO_FILE, portfolio)
    return portfolio


def load_trade_log() -> List[Dict[str, Any]]:
    data = _read_json(TRADE_FILE, [])
    return data if isinstance(data, list) else []


def save_trade_log(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    _write_json(TRADE_FILE, items[:400])
    return items


def append_trade(entry: Dict[str, Any]) -> Dict[str, Any]:
    log = load_trade_log()
    payload = dict(entry)
    payload['id'] = int(time.time() * 1000)
    payload['time'] = datetime.now().isoformat()
    log.insert(0, payload)
    save_trade_log(log)
    return payload


def _entry_sort_key(entry: Dict[str, Any]) -> Tuple[str, int]:
    timestamp = str(
        entry.get('createdAt')
        or entry.get('time')
        or entry.get('updatedAt')
        or entry.get('date')
        or ''
    )
    try:
        entry_id = int(entry.get('id', 0) or 0)
    except Exception:
        entry_id = 0
    return (timestamp, entry_id)


def _normalize_settle_log(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []

    sorted_items = sorted(
        [dict(item) for item in items if isinstance(item, dict)],
        key=_entry_sort_key,
        reverse=True,
    )
    unique: List[Dict[str, Any]] = []
    seen_keys = set()
    for item in sorted_items:
        kind = str(item.get('kind') or 'daily')
        if kind == 'weekly':
            dedupe_key = f'weekly:{item.get("week") or item.get("date") or ""}'
        else:
            dedupe_key = f'{kind}:{item.get("date") or ""}'
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        unique.append(item)
    return unique[:104]


def _normalize_weekly_reviews(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        return []

    sorted_items = sorted(
        [dict(item) for item in items if isinstance(item, dict)],
        key=_entry_sort_key,
        reverse=True,
    )
    unique: List[Dict[str, Any]] = []
    seen_weeks = set()
    for item in sorted_items:
        week_key = str(item.get('week') or '')
        if not week_key:
            week_start = str(item.get('weekStart') or '')
            week_end = str(item.get('weekEnd') or '')
            week_key = f'{week_start}:{week_end}'
        if week_key in seen_weeks:
            continue
        seen_weeks.add(week_key)
        unique.append(item)
    return unique[:52]


def load_settle_log() -> List[Dict[str, Any]]:
    data = _read_json(SETTLE_FILE, [])
    return _normalize_settle_log(data)


def save_settle_log(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = _normalize_settle_log(items)
    _write_json(SETTLE_FILE, normalized)
    return normalized


def append_settlement(entry: Dict[str, Any]) -> Dict[str, Any]:
    log = load_settle_log()
    payload = dict(entry)
    payload['id'] = int(time.time() * 1000)
    log.insert(0, payload)
    save_settle_log(log)
    return payload


def load_weekly_reviews() -> List[Dict[str, Any]]:
    data = _read_json(REVIEW_FILE, [])
    return _normalize_weekly_reviews(data)


def save_weekly_reviews(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = _normalize_weekly_reviews(items)
    _write_json(REVIEW_FILE, normalized)
    return normalized


def append_weekly_review(entry: Dict[str, Any]) -> Dict[str, Any]:
    reviews = load_weekly_reviews()
    payload = dict(entry)
    payload['id'] = int(time.time() * 1000)
    reviews.insert(0, payload)
    save_weekly_reviews(reviews)
    return payload


def reset_sim_auto_portfolio() -> Dict[str, Any]:
    portfolio = _default_portfolio()
    save_portfolio(portfolio)
    save_trade_log([])
    save_settle_log([])
    save_weekly_reviews([])
    return portfolio


def _extract_hot_events() -> List[Dict[str, Any]]:
    hot_path = os.path.join(DATA_DIR, 'hot_events.json')
    data = _read_json(hot_path, {})
    events = data.get('events', []) if isinstance(data, dict) else []
    return events if isinstance(events, list) else []


def _extract_portfolio_fund_map() -> Dict[str, Dict[str, Any]]:
    cache = load_portfolio_advice_cache() or {}
    result = cache.get('result', {}) if isinstance(cache, dict) else {}
    funds = result.get('funds', []) if isinstance(result, dict) else []
    return {str(item.get('code')): item for item in funds if item.get('code')}


def _extract_stock_screen_map() -> Dict[str, Dict[str, Any]]:
    cache = load_stock_screen_cache() or {}
    result = cache.get('result', {}) if isinstance(cache, dict) else {}
    picks = result.get('picks', []) if isinstance(result, dict) else []
    return {str(item.get('code')): item for item in picks if item.get('code')}


def _extract_fund_pick_candidates() -> List[Dict[str, Any]]:
    cache = load_fund_pick_cache() or {}
    result = cache.get('result', {}) if isinstance(cache, dict) else {}
    fund_picks = result.get('fundPicks', []) if isinstance(result, dict) else []
    stock_picks = result.get('stockPicks', []) if isinstance(result, dict) else []
    items: List[Dict[str, Any]] = []
    for item in fund_picks:
        payload = dict(item)
        payload['type'] = 'fund'
        payload['source'] = 'fund_pick'
        items.append(payload)
    for item in stock_picks:
        payload = dict(item)
        payload['type'] = 'stock'
        payload['source'] = 'fund_pick'
        items.append(payload)
    screen_map = _extract_stock_screen_map()
    for item in screen_map.values():
        payload = dict(item)
        payload['type'] = 'stock'
        payload['source'] = 'stock_screen'
        items.append(payload)
    return items


def _candidate_score(item: Dict[str, Any]) -> float:
    confidence = float(item.get('confidence', 0) or 0)
    score = float(item.get('score', 0) or 0)
    rr_ratio = float(item.get('rrRatio', 0) or 0)
    base = confidence if confidence > 0 else score
    if item.get('source') == 'stock_screen':
        base += rr_ratio * AUTO_TRADE_CONFIG['stockRrWeight']
    if item.get('type') == 'fund':
        base += AUTO_TRADE_CONFIG['fundScoreBonus']
    return base


def _pick_trade_price(position_type: str, code: str) -> Optional[Dict[str, Any]]:
    if position_type == 'stock':
        return fetch_stock_quote(code)
    return fetch_fund_estimate(code)


def _position_value(position: Dict[str, Any], price: float) -> float:
    return float(position.get('shares', 0) or 0) * price


def _price_or_default(quote: Optional[Dict[str, Any]], position: Dict[str, Any]) -> float:
    if quote:
        nav = quote.get('estimate') or quote.get('nav') or 0
        if nav:
            return float(nav)
    return float(position.get('lastNav') or position.get('costPrice') or 0)


def _build_live_positions(portfolio: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], float]:
    positions = portfolio.get('positions', []) or []
    if not positions:
        return [], round(float(portfolio.get('cash', 0) or 0), 2)

    # 并发拉取行情
    quotes: Dict[int, Optional[Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(positions))) as pool:
        futures = {
            pool.submit(_pick_trade_price, pos.get('type', 'fund'), pos.get('code', '')): i
            for i, pos in enumerate(positions)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                quotes[idx] = future.result()
            except Exception:
                quotes[idx] = None

    live_positions: List[Dict[str, Any]] = []
    total = float(portfolio.get('cash', 0) or 0)
    for i, position in enumerate(positions):
        quote = quotes.get(i)
        price = _price_or_default(quote, position)
        value = _position_value(position, price)
        profit = value - float(position.get('costTotal', 0) or 0)
        cost_total = float(position.get('costTotal', 0) or 0)
        profit_pct = (profit / cost_total * 100) if cost_total > 0 else 0
        payload = dict(position)
        payload['currentPrice'] = round(price, 4)
        payload['confirmedNav'] = round(float((quote or {}).get('nav') or price), 4)
        payload['currentValue'] = round(value, 2)
        payload['profit'] = round(profit, 2)
        payload['profitPct'] = round(profit_pct, 2)
        payload['pct'] = float((quote or {}).get('pct', 0) or 0)
        total += value
        live_positions.append(payload)
    return live_positions, round(total, 2)


def _ensure_source_caches() -> None:
    # 自动模拟仓优先使用已有缓存，避免手动触发时阻塞在大任务上。
    # 正常 14:50 调度会先生成 fund_pick / portfolio_advice / stock_screen，
    # 14:55 再执行自动模拟仓调仓，
    # 此处只在缺少热点事件文件时补一次基础缓存。
    load_fund_pick_cache()
    load_stock_screen_cache()
    load_portfolio_advice_cache()
    hot_path = os.path.join(DATA_DIR, 'hot_events.json')
    if not os.path.exists(hot_path):
        fetch_hot_events()


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _contains_any(text: str, keywords: List[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _position_value_map(live_positions: List[Dict[str, Any]]) -> Dict[str, float]:
    return {
        str(item.get('code', '')): float(item.get('currentValue', 0) or 0)
        for item in live_positions
        if item.get('code')
    }


def _risk_bias_from_sell_result(sell_result: Dict[str, Any]) -> float:
    text = ' '.join([
        str(sell_result.get('overview', '') or ''),
        str(sell_result.get('riskAlert', '') or ''),
    ])
    advice = sell_result.get('sellAdvice', []) or []
    sell_count = sum(1 for item in advice if item.get('action') in ('sell_part', 'sell_all'))
    bias = 1.0 - min(0.24, sell_count * 0.08)
    if _contains_any(text, ['高风险', '波动较大', '谨慎', '承压', '不确定', '风险提示']):
        bias -= 0.10
    if _contains_any(text, ['机会', '修复', '改善', '企稳', '回暖']):
        bias += 0.05
    return _clamp(bias, 0.55, 1.08)


def _candidate_buy_multiplier(candidate: Dict[str, Any], sell_result: Dict[str, Any]) -> Tuple[float, List[str]]:
    threshold = AUTO_TRADE_CONFIG['stockMinScore'] if candidate.get('type') == 'stock' else AUTO_TRADE_CONFIG['fundMinScore']
    score = float(candidate.get('_score', 0) or _candidate_score(candidate))
    confidence = float(candidate.get('confidence', 0) or 0)
    rr_ratio = float(candidate.get('rrRatio', 0) or 0)
    notes: List[str] = []

    edge_ratio = _clamp((score - threshold) / 18.0, 0.0, 1.0)
    multiplier = 0.72 + edge_ratio * 0.40
    if edge_ratio >= 0.65:
        notes.append('候选评分明显高于门槛')
    elif edge_ratio > 0:
        notes.append('候选评分略高于门槛')

    if confidence >= 82:
        multiplier += 0.15
        notes.append('AI 置信度较高')
    elif confidence >= 75:
        multiplier += 0.08
        notes.append('AI 置信度偏高')
    elif confidence and confidence < 68:
        multiplier -= 0.08
        notes.append('AI 置信度一般')

    if candidate.get('type') == 'stock':
        if rr_ratio >= 1.75:
            multiplier += 0.12
            notes.append('盈亏比良好')
        elif rr_ratio >= 1.50:
            multiplier += 0.06
        elif rr_ratio and rr_ratio < 1.20:
            multiplier -= 0.08
            notes.append('盈亏比偏弱')

    action_text = ' '.join([
        str(candidate.get('advice', '') or ''),
        str(candidate.get('planNote', '') or ''),
        str(candidate.get('riskText', '') or ''),
        str(candidate.get('reason', '') or ''),
    ])
    if _contains_any(action_text, ['建议加仓', '开盘即可', '量价配合良好', '无明显风险']):
        multiplier += 0.08
        notes.append('执行条件较顺畅')
    if _contains_any(action_text, ['不建议追高', '偏远', '谨慎', '风险', '冲高无量']):
        multiplier -= 0.16
        notes.append('追高或风险提示，降档执行')

    # ---- 博弈面信号：买入候选的主力资金流向 ----
    if candidate.get('type') == 'stock':
        flow = fetch_capital_flow(str(candidate.get('code', '')))
        if flow:
            main_3d = flow.get('main_net_3d', 0)
            if main_3d > 0.5:  # 主力3日累计净流入 > 5000万
                multiplier += 0.15
                notes.append('博弈面: 主力3日净流入%.2f亿，加仓信号' % main_3d)
            elif main_3d > 0.1:  # 小幅流入
                multiplier += 0.06
                notes.append('博弈面: 主力小幅流入')
            elif main_3d < -0.5:  # 主力3日累计净流出 > 5000万
                multiplier -= 0.20
                notes.append('博弈面: 主力3日净流出%.2f亿，降档' % abs(main_3d))
            elif main_3d < -0.1:
                multiplier -= 0.10
                notes.append('博弈面: 主力小幅流出，谨慎')

    market_bias = _risk_bias_from_sell_result(sell_result)
    if market_bias < 0.8:
        notes.append('市场风险提示偏强，整体降仓位')
    multiplier *= market_bias
    return _clamp(multiplier, 0.45, 1.35), notes


def _suggest_buy_amount(candidate: Dict[str, Any], portfolio: Dict[str, Any], live_positions: List[Dict[str, Any]], total_value: float, sell_result: Dict[str, Any]) -> Tuple[float, float, List[str]]:
    cash = float(portfolio.get('cash', 0) or 0)
    if cash < 100:
        return 0.0, 0.0, []

    multiplier, notes = _candidate_buy_multiplier(candidate, sell_result)
    base_target = max(
        AUTO_TRADE_CONFIG['minBuyAmount'],
        total_value * AUTO_TRADE_CONFIG['targetBuyPct'] * multiplier,
    )
    single_cap = total_value * AUTO_TRADE_CONFIG['maxSingleBuyPct'] * max(0.75, multiplier)
    current_value = _position_value_map(live_positions).get(str(candidate.get('code', '')), 0.0)
    position_room = max(0.0, total_value * AUTO_TRADE_CONFIG['maxPositionWeight'] - current_value)
    cash_reserve = max(0.0, cash - max(200.0, total_value * 0.03))
    amount = min(base_target, single_cap, position_room or single_cap, cash_reserve or cash)

    minimum_trade = min(AUTO_TRADE_CONFIG['minBuyAmount'], cash, position_room or cash)
    if amount < minimum_trade and multiplier >= 1.08:
        amount = min(minimum_trade, single_cap, position_room or minimum_trade, cash_reserve or cash)

    amount = math.floor(max(0.0, amount) / 100.0) * 100.0
    if amount <= 0 and cash >= 100 and position_room >= 100:
        amount = 100.0

    if position_room and amount >= position_room:
        notes.append('接近单标的仓位上限')
    notes.append('动态仓位系数 %.2f' % multiplier)
    return round(min(amount, cash, position_room or cash), 2), round(multiplier, 2), notes


def _parse_sizing_notes(reason: str) -> List[str]:
    text = str(reason or '')
    if '仓位依据:' not in text:
        return []
    note_text = text.split('仓位依据:', 1)[1].strip()
    return [item.strip() for item in note_text.split('，') if item.strip()]


def _infer_sizing_multiplier_from_amount(amount: float, reference_total: float) -> float:
    base_amount = max(
        float(AUTO_TRADE_CONFIG.get('minBuyAmount', 0) or 0),
        float(reference_total or INITIAL_CAPITAL) * float(AUTO_TRADE_CONFIG.get('targetBuyPct', 0) or 0),
    )
    if base_amount <= 0:
        return 1.0
    return round(_clamp(float(amount or 0) / base_amount, 0.1, 3.0), 2)


def _normalize_trade_entry(entry: Dict[str, Any], reference_total: Optional[float] = None) -> Dict[str, Any]:
    payload = dict(entry or {})
    if payload.get('action') == 'buy':
        multiplier = payload.get('sizingMultiplier')
        if multiplier in (None, ''):
            multiplier = _infer_sizing_multiplier_from_amount(float(payload.get('amount', 0) or 0), float(reference_total or INITIAL_CAPITAL))
        payload['sizingMultiplier'] = round(float(multiplier or 0), 2)
        notes = payload.get('sizingNotes')
        if not isinstance(notes, list) or not notes:
            payload['sizingNotes'] = _parse_sizing_notes(str(payload.get('reason', '') or ''))
    return payload


def _trade_group_key(entry: Dict[str, Any]) -> Tuple[str, str, str, str, str]:
    return (
        str(entry.get('date') or ''),
        str(entry.get('action') or ''),
        str(entry.get('code') or ''),
        str(entry.get('type') or ''),
        str(entry.get('aiSource') or ''),
    )


def _merge_trade_note_lists(first: Any, second: Any) -> List[str]:
    merged: List[str] = []
    for value in list(first or []) + list(second or []):
        text = str(value or '').strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def _compact_trade_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compacted: List[Dict[str, Any]] = []
    for item in entries:
        payload = dict(item or {})
        payload['mergedCount'] = int(payload.get('mergedCount', 1) or 1)
        if not compacted or _trade_group_key(compacted[-1]) != _trade_group_key(payload):
            compacted.append(payload)
            continue

        current = compacted[-1]
        current['amount'] = round(float(current.get('amount', 0) or 0) + float(payload.get('amount', 0) or 0), 2)
        current['shares'] = round(float(current.get('shares', 0) or 0) + float(payload.get('shares', 0) or 0), 4)
        current['mergedCount'] = int(current.get('mergedCount', 1) or 1) + int(payload.get('mergedCount', 1) or 1)
        current['id'] = max(int(current.get('id', 0) or 0), int(payload.get('id', 0) or 0))
        current['time'] = max(str(current.get('time', '') or ''), str(payload.get('time', '') or ''))
        current['sizingNotes'] = _merge_trade_note_lists(current.get('sizingNotes'), payload.get('sizingNotes'))

        if current.get('action') == 'buy':
            current['sizingMultiplier'] = round(max(float(current.get('sizingMultiplier', 0) or 0), float(payload.get('sizingMultiplier', 0) or 0)), 2)

    for item in compacted:
        merged_count = int(item.get('mergedCount', 1) or 1)
        if merged_count <= 1:
            continue
        action_label = '自动买入' if item.get('action') == 'buy' else '自动减仓'
        base_reason = str(item.get('reason', '') or '').strip()
        item['reason'] = '同日合并%d笔%s。%s' % (merged_count, action_label, base_reason)

    return compacted


def _suggest_sell_pct(position: Dict[str, Any], suggestion: Dict[str, Any]) -> float:
    action = suggestion.get('action', 'hold')
    if action == 'sell_all':
        return 100.0
    raw_pct = float(suggestion.get('pct', 0) or 0)
    if raw_pct > 0:
        return round(_clamp(raw_pct, 5.0, 100.0), 2)

    confidence = float(suggestion.get('confidence', 60) or 60)
    profit_pct = float(position.get('profitPct', 0) or 0)
    reason = str(suggestion.get('reason', '') or '')
    base_pct = 18.0 + max(0.0, confidence - 60.0) * 0.55
    if profit_pct <= AUTO_TRADE_CONFIG['drawdownTriggerPct']:
        base_pct += min(18.0, abs(profit_pct - AUTO_TRADE_CONFIG['drawdownTriggerPct']) * 2.0)
    if profit_pct >= AUTO_TRADE_CONFIG['takeProfitTriggerPct']:
        base_pct += min(12.0, (profit_pct - AUTO_TRADE_CONFIG['takeProfitTriggerPct']) * 0.8)
    if _contains_any(reason, ['止损', '跌破', '风险', '回撤']):
        base_pct += 8.0
    return round(_clamp(base_pct, 10.0, 60.0), 2)


def _apply_buy(portfolio: Dict[str, Any], candidate: Dict[str, Any], amount: float, reason: str, sizing_multiplier: Optional[float] = None, sizing_notes: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    if amount < 100:
        return None
    quote = _pick_trade_price(candidate.get('type', 'fund'), candidate.get('code', ''))
    if not quote:
        return None
    price = float(quote.get('estimate') or quote.get('nav') or 0)
    if price <= 0:
        return None
    shares = amount / price
    positions = portfolio.setdefault('positions', [])
    idx = next((i for i, item in enumerate(positions) if item.get('code') == candidate.get('code')), -1)
    if idx >= 0:
        old = positions[idx]
        new_cost_total = float(old.get('costTotal', 0) or 0) + amount
        new_shares = float(old.get('shares', 0) or 0) + shares
        positions[idx] = {
            **old,
            'shares': new_shares,
            'costTotal': new_cost_total,
            'costPrice': new_cost_total / new_shares,
            'lastNav': price,
            'lastNavDate': today_str(),
        }
    else:
        positions.append({
            'code': str(candidate.get('code', '')).strip(),
            'name': str(candidate.get('name', '')).strip(),
            'type': candidate.get('type', 'fund'),
            'sector': candidate.get('sector', '') or candidate.get('type', ''),
            'shares': shares,
            'costPrice': price,
            'costTotal': amount,
            'buyDate': today_str(),
            'lastNav': price,
            'lastNavDate': today_str(),
        })
    portfolio['cash'] = round(float(portfolio.get('cash', 0) or 0) - amount, 2)
    return append_trade({
        'date': today_str(),
        'action': 'buy',
        'code': candidate.get('code', ''),
        'name': candidate.get('name', ''),
        'type': candidate.get('type', 'fund'),
        'sector': candidate.get('sector', '') or candidate.get('type', ''),
        'amount': round(amount, 2),
        'price': round(price, 4),
        'shares': round(shares, 4),
        'reason': reason,
        'sizingMultiplier': round(float(sizing_multiplier or 0), 2) if sizing_multiplier else None,
        'sizingNotes': list(sizing_notes or []),
        'aiSource': candidate.get('source', 'auto_trade'),
    })


def _apply_sell(portfolio: Dict[str, Any], position: Dict[str, Any], pct: float, reason: str, ai_source: str) -> Optional[Dict[str, Any]]:
    pct = max(1.0, min(100.0, float(pct or 0)))
    quote = _pick_trade_price(position.get('type', 'fund'), position.get('code', ''))
    price = _price_or_default(quote, position)
    if price <= 0:
        return None
    positions = portfolio.setdefault('positions', [])
    idx = next((i for i, item in enumerate(positions) if item.get('code') == position.get('code')), -1)
    if idx < 0:
        return None
    current = positions[idx]
    sell_shares = float(current.get('shares', 0) or 0) * (pct / 100.0)
    sell_amount = sell_shares * price
    remain_pct = max(0.0, 1.0 - pct / 100.0)
    if pct >= 99.9 or remain_pct <= 0.0001:
        positions.pop(idx)
    else:
        positions[idx] = {
            **current,
            'shares': float(current.get('shares', 0) or 0) - sell_shares,
            'costTotal': float(current.get('costTotal', 0) or 0) * remain_pct,
            'lastNav': price,
            'lastNavDate': today_str(),
        }
    portfolio['cash'] = round(float(portfolio.get('cash', 0) or 0) + sell_amount, 2)
    return append_trade({
        'date': today_str(),
        'action': 'sell',
        'code': position.get('code', ''),
        'name': position.get('name', ''),
        'type': position.get('type', 'fund'),
        'sector': position.get('sector', '') or position.get('type', ''),
        'amount': round(sell_amount, 2),
        'price': round(price, 4),
        'shares': round(sell_shares, 4),
        'reason': reason,
        'aiSource': ai_source,
    })


# AI 调用状态追踪
_ai_call_status: Dict[str, Any] = {'last': 'unknown', 'okAt': '', 'failAt': '', 'error': ''}


def _detect_ai_base(model: str) -> str:
    explicit = str(env('AI_API_BASE', '') or os.environ.get('AI_API_BASE', '') or '').strip()
    if explicit:
        return explicit.rstrip('/')
    lower = model.lower()
    if 'glm' in lower or 'chatglm' in lower:
        return 'https://open.bigmodel.cn/api/paas/v4'
    if 'deepseek' in lower:
        return 'https://api.deepseek.com/v1'
    return 'https://api.siliconflow.cn/v1'


def _call_ai_json(system_prompt: str, user_prompt: str, temperature: float = 0.4) -> Optional[Dict[str, Any]]:
    api_key = str(env('AI_API_KEY', '') or os.environ.get('AI_API_KEY', '') or '').strip()
    if not api_key:
        return None
    model = str(env('SIM_AUTO_AI_MODEL', '') or os.environ.get('SIM_AUTO_AI_MODEL', '') or env('BREAKING_AI_MODEL', '') or 'glm-4-flash').strip()
    api_base = _detect_ai_base(model)
    body = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
        'temperature': temperature,
        'max_tokens': 2200,
    }
    req = urllib.request.Request(
        api_base.rstrip('/') + '/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + api_key,
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode('utf-8'))
        content = ((result.get('choices') or [{}])[0].get('message') or {}).get('content', '').strip()
        content = re.sub(r'^```json\s*', '', content)
        content = re.sub(r'^```\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            _ai_call_status['last'] = 'ok'
            _ai_call_status['okAt'] = datetime.now().isoformat()
            return parsed
        _ai_call_status['last'] = 'invalid_response'
        _ai_call_status['failAt'] = datetime.now().isoformat()
        return None
    except Exception as exc:
        print('[sim_auto] AI 调用失败: %s' % exc)
        _ai_call_status['last'] = 'error'
        _ai_call_status['failAt'] = datetime.now().isoformat()
        _ai_call_status['error'] = str(exc)[:200]
        return None


def _build_sell_prompt(positions: List[Dict[str, Any]], fund_map: Dict[str, Dict[str, Any]], stock_map: Dict[str, Dict[str, Any]], hot_events: List[Dict[str, Any]]) -> str:
    lines = ['## 当前持仓']
    for item in positions:
        code = str(item.get('code', ''))
        fund_advice = fund_map.get(code, {})
        stock_advice = stock_map.get(code, {})
        lines.append(
            '- {name}({code}) 类型:{type} 板块:{sector} 成本:{cost:.4f} 现价:{price:.4f} 盈亏:{pct:+.2f}%'.format(
                name=item.get('name', ''),
                code=code,
                type=item.get('type', ''),
                sector=item.get('sector', ''),
                cost=float(item.get('costPrice', 0) or 0),
                price=float(item.get('currentPrice', 0) or 0),
                pct=float(item.get('profitPct', 0) or 0),
            )
        )
        if fund_advice:
            lines.append('  实盘行动指南: action={action}, confidence={confidence}, reason={reason}'.format(
                action=fund_advice.get('action', ''),
                confidence=fund_advice.get('confidence', ''),
                reason=fund_advice.get('reason', ''),
            ))
        if stock_advice:
            lines.append('  形态选股参考: score={score}, risk={risk}, stopLoss={stop_loss}, reason={reason}'.format(
                score=stock_advice.get('score', ''),
                risk=stock_advice.get('riskLevel', ''),
                stop_loss=stock_advice.get('stopLoss', ''),
                reason=stock_advice.get('reason', ''),
            ))
    lines.append('\n## 热点事件')
    for event in hot_events[:6]:
        lines.append('- [%s] %s' % (event.get('category', ''), event.get('title', '')))

    # ---- 博弈面数据：主力资金流向 ----
    flow_lines = []
    stock_positions = [p for p in positions if p.get('type') == 'stock']
    if stock_positions:
        flow_map: Dict[str, Optional[Dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=min(4, len(stock_positions))) as pool:
            futures = {
                pool.submit(fetch_capital_flow, str(p.get('code', ''))): str(p.get('code', ''))
                for p in stock_positions
            }
            for future in as_completed(futures):
                code = futures[future]
                try:
                    flow_map[code] = future.result()
                except Exception:
                    flow_map[code] = None
        for pos in stock_positions:
            code = str(pos.get('code', ''))
            flow = flow_map.get(code)
            if flow:
                flow_lines.append(
                    '- {name}({code}): 主力3日净流入{main_3d:+.2f}亿, '
                    '今日主力{main_today:+.2f}亿, '
                    '散户3日{retail_3d:+.2f}亿, '
                    '方向:{direction}'.format(
                        name=pos.get('name', ''),
                        code=code,
                        main_3d=flow['main_net_3d'],
                        main_today=flow['main_today'],
                        retail_3d=flow['retail_net_3d'],
                        direction='主力流入' if flow['main_direction'] == 'inflow' else '主力流出',
                    )
                )
    if flow_lines:
        lines.append('\n## 博弈面·主力资金流向')
        lines.extend(flow_lines)
    return '\n'.join(lines)


def _fallback_sell_advice(positions: List[Dict[str, Any]], fund_map: Dict[str, Dict[str, Any]], stock_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    advice = []
    for item in positions:
        code = str(item.get('code', ''))
        profit_pct = float(item.get('profitPct', 0) or 0)
        fund_item = fund_map.get(code, {})
        stock_item = stock_map.get(code, {})
        if fund_item.get('action') == 'reduce':
            confidence = int(fund_item.get('confidence', 60) or 60)
            advice.append({
                'code': code,
                'action': 'sell_part',
                'pct': round(_clamp(AUTO_TRADE_CONFIG['reduceFundPct'] * (0.8 + max(0.0, confidence - 60.0) / 100.0), 10.0, 55.0), 2),
                'reason': fund_item.get('reason', '实盘行动指南建议减仓'),
                'confidence': confidence,
            })
            continue
        stop_loss = stock_item.get('stopLoss')
        current_price = float(item.get('currentPrice', 0) or 0)
        try:
            if stop_loss and current_price and current_price < float(stop_loss):
                advice.append({
                    'code': code,
                    'action': 'sell_part',
                    'pct': round(_clamp(AUTO_TRADE_CONFIG['stopLossReducePct'] + max(0.0, (float(stop_loss) - current_price) * 10.0), 20.0, 75.0), 2),
                    'reason': '跌破形态止损位，执行风控减仓',
                    'confidence': 72,
                })
                continue
        except Exception:
            pass
        if profit_pct <= AUTO_TRADE_CONFIG['drawdownTriggerPct']:
            advice.append({
                'code': code,
                'action': 'sell_part',
                'pct': round(_clamp(AUTO_TRADE_CONFIG['drawdownReducePct'] + max(0.0, abs(profit_pct - AUTO_TRADE_CONFIG['drawdownTriggerPct']) * 2.0), 15.0, 70.0), 2),
                'reason': '回撤超过 %.1f%%，先降低仓位' % abs(AUTO_TRADE_CONFIG['drawdownTriggerPct']),
                'confidence': 65,
            })
        elif profit_pct >= AUTO_TRADE_CONFIG['takeProfitTriggerPct']:
            advice.append({
                'code': code,
                'action': 'sell_part',
                'pct': round(_clamp(AUTO_TRADE_CONFIG['takeProfitReducePct'] + max(0.0, (profit_pct - AUTO_TRADE_CONFIG['takeProfitTriggerPct']) * 0.8), 10.0, 45.0), 2),
                'reason': '已有较大浮盈，部分止盈锁定收益',
                'confidence': 60,
            })
            continue
        # ---- 博弈面兜底：主力资金持续外逃 ----
        if item.get('type') == 'stock':
            flow = fetch_capital_flow(code)
            if flow and flow.get('main_net_3d', 0) < -0.5:
                # 主力3日累计净流出 > 5000万，主动减仓
                pct = round(_clamp(20 + abs(flow['main_net_3d']) * 5, 15.0, 40.0), 2)
                advice.append({
                    'code': code,
                    'action': 'sell_part',
                    'pct': pct,
                    'reason': '博弈面预警: 主力3日净流出%.2f亿，主动减仓' % abs(flow['main_net_3d']),
                    'confidence': 68,
                })
    return {
        'overview': '使用规则引擎完成减仓判断',
        'sellAdvice': advice,
        'riskAlert': 'AI 不可用时使用规则兜底',
    }


def _get_sell_decisions(portfolio: Dict[str, Any], live_positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    fund_map = _extract_portfolio_fund_map()
    stock_map = _extract_stock_screen_map()
    hot_events = _extract_hot_events()
    prompt = _build_sell_prompt(live_positions, fund_map, stock_map, hot_events)
    ai_result = _call_ai_json(SELL_SYSTEM_PROMPT, prompt, temperature=0.35)
    if ai_result and isinstance(ai_result.get('sellAdvice'), list):
        return ai_result
    return _fallback_sell_advice(live_positions, fund_map, stock_map)


def _select_buy_candidates(portfolio: Dict[str, Any], live_positions: List[Dict[str, Any]], total_value: float) -> List[Dict[str, Any]]:
    candidates = _extract_fund_pick_candidates()
    if not candidates:
        return []
    current_weights = {}
    for item in live_positions:
        current_weights[str(item.get('code'))] = float(item.get('currentValue', 0) or 0) / max(total_value, 1)
    unique: Dict[str, Dict[str, Any]] = {}
    for item in candidates:
        code = str(item.get('code', '')).strip()
        if not code:
            continue
        score = _candidate_score(item)
        threshold = AUTO_TRADE_CONFIG['stockMinScore'] if item.get('type') == 'stock' else AUTO_TRADE_CONFIG['fundMinScore']
        if score < threshold:
            continue
        if current_weights.get(code, 0) >= AUTO_TRADE_CONFIG['maxPositionWeight']:
            continue
        payload = dict(item)
        payload['_score'] = score
        previous = unique.get(code)
        if previous is None or score > float(previous.get('_score', 0)):
            unique[code] = payload
    ordered = sorted(unique.values(), key=lambda item: float(item.get('_score', 0)), reverse=True)
    selected: List[Dict[str, Any]] = []
    has_fund = False
    has_stock = False
    for item in ordered:
        if len(selected) >= AUTO_TRADE_CONFIG['maxBuyCandidates']:
            break
        if item.get('type') == 'fund' and has_fund:
            continue
        if item.get('type') == 'stock' and has_stock:
            continue
        # 写入评分明细到 reason，方便事后复盘
        score = float(item.get('_score', 0))
        conf = float(item.get('confidence', 0) or 0)
        rr = float(item.get('rrRatio', 0) or 0)
        parts = ['综合%.1f' % score]
        if conf > 0:
            parts.append('信心%.0f' % conf)
        if item.get('source') == 'stock_screen' and rr > 0:
            parts.append('RR%.1f×%.0f' % (rr, AUTO_TRADE_CONFIG['stockRrWeight']))
        if item.get('type') == 'fund':
            parts.append('基金+%.0f' % AUTO_TRADE_CONFIG['fundScoreBonus'])
        item['scoreDetail'] = '（评分: %s）' % '，'.join(parts)
        selected.append(item)
        has_fund = has_fund or item.get('type') == 'fund'
        has_stock = has_stock or item.get('type') == 'stock'
    return selected


def run_auto_trade(now: Optional[datetime] = None, force: bool = False) -> Dict[str, Any]:
    current = now or datetime.now()
    if not force:
        if not is_trading_day(current.date()):
            return {'status': 'skipped', 'reason': 'non_trading_day'}
        if current.hour < 14 or (current.hour == 14 and current.minute < 55) or current.hour >= 16:
            return {'status': 'skipped', 'reason': 'outside_trade_window'}
    _ensure_source_caches()
    portfolio = load_portfolio()
    trade_day = today_str(current)
    if not force and portfolio.get('lastTradeDate') == trade_day:
        return {'status': 'skipped', 'reason': 'already_traded_today'}

    live_positions, total_value = _build_live_positions(portfolio)
    sell_result = _get_sell_decisions(portfolio, live_positions)
    executed_sells = []
    for item in sell_result.get('sellAdvice', []) or []:
        code = str(item.get('code', ''))
        action = item.get('action', 'hold')
        if action == 'hold':
            continue
        position = next((pos for pos in portfolio.get('positions', []) if pos.get('code') == code), None)
        if not position:
            continue
        pct = _suggest_sell_pct(position, item)
        if pct <= 0:
            continue
        trade = _apply_sell(portfolio, position, pct, item.get('reason', '自动减仓'), 'auto_sell_ai')
        if trade:
            executed_sells.append(trade)
            save_portfolio(portfolio)

    live_positions, total_value = _build_live_positions(portfolio)
    buy_candidates = _select_buy_candidates(portfolio, live_positions, total_value)
    executed_buys = []
    for item in buy_candidates:
        live_positions, total_value = _build_live_positions(portfolio)
        amount, sizing_multiplier, sizing_notes = _suggest_buy_amount(item, portfolio, live_positions, total_value, sell_result)
        if amount < 100:
            continue
        reason_core = item.get('reason') or item.get('advice') or item.get('planNote') or '高分候选标的'
        score_detail = item.get('scoreDetail', '')
        reason = '自动加仓: %s%s；仓位依据: %s' % (reason_core, score_detail, '，'.join(sizing_notes[:4]))
        trade = _apply_buy(portfolio, item, amount, reason, sizing_multiplier=sizing_multiplier, sizing_notes=sizing_notes)
        if trade:
            executed_buys.append(trade)
            save_portfolio(portfolio)
            live_positions, total_value = _build_live_positions(portfolio)

    portfolio['lastTradeDate'] = trade_day
    portfolio['lastTradeSummary'] = {
        'date': trade_day,
        'buyCount': len(executed_buys),
        'sellCount': len(executed_sells),
        'overview': sell_result.get('overview', ''),
        'riskAlert': sell_result.get('riskAlert', ''),
    }
    save_portfolio(portfolio)

    # ---- 每日结算快照（供收益日历展示） ----
    live_positions, total_value = _build_live_positions(portfolio)
    position_value = round(total_value - float(portfolio.get('cash', 0) or 0), 2)
    settle_log = load_settle_log()
    prev_settle = next((s for s in settle_log if str(s.get('date', '')) < trade_day), None)
    prev_value = float(prev_settle.get('totalValue', portfolio.get('totalCash', INITIAL_CAPITAL)) if prev_settle else portfolio.get('totalCash', INITIAL_CAPITAL))
    daily_pnl = total_value - prev_value
    daily_pct = (daily_pnl / prev_value * 100) if prev_value else 0
    append_settlement({
        'date': trade_day,
        'kind': 'daily',
        'cash': round(float(portfolio.get('cash', 0) or 0), 2),
        'positionValue': position_value,
        'totalValue': round(total_value, 2),
        'dailyPnl': round(daily_pnl, 2),
        'dailyPct': round(daily_pct, 2),
        'positions': [{
            'code': p.get('code', ''),
            'name': p.get('name', ''),
            'type': p.get('type', ''),
            'shares': round(float(p.get('shares', 0) or 0), 4),
            'value': round(float(p.get('currentValue', 0) or 0), 2),
            'price': round(float(p.get('currentPrice', 0) or 0), 4),
        } for p in live_positions],
    })

    status = 'executed' if (executed_buys or executed_sells) else 'no_action'
    return {
        'status': status,
        'date': trade_day,
        'buys': executed_buys,
        'sells': executed_sells,
        'sellAnalysis': sell_result,
        'summary': portfolio.get('lastTradeSummary', {}),
    }


def _build_review_fallback(trades: List[Dict[str, Any]], start_value: float, end_value: float, week_start: str, week_end: str) -> Dict[str, Any]:
    profit = end_value - start_value
    pct = (profit / start_value * 100) if start_value else 0
    best = next((item for item in trades if item.get('action') == 'sell'), None)
    worst = next((item for item in reversed(trades) if item.get('action') == 'buy'), None)
    return {
        'summary': '本周自动交易共执行 %d 笔，期末资产 %.2f 元，收益 %.2f%%。' % (len(trades), end_value, pct),
        'performance': {
            'totalReturn': '%+.2f%%' % pct,
            'bestTrade': ('%s %s' % (best.get('name', ''), best.get('reason', ''))) if best else '暂无',
            'worstTrade': ('%s %s' % (worst.get('name', ''), worst.get('reason', ''))) if worst else '暂无',
        },
        'analysis': '本周复盘使用规则模板生成。若盈利，主因通常是顺势加仓与及时减仓；若亏损，主因通常是入场过早或热点持续性不足。',
        'lessons': [
            '控制单标的仓位，避免热点过度集中。',
            '减仓建议要优先执行，防止盈利回吐。',
            '买入前结合板块强度与风险收益比。',
        ],
        'nextWeekStrategy': '继续只做高置信度标的，按建议强弱动态控制单次加减仓，对弱势持仓优先降仓。',
        'optimizationHints': '后续可把止盈止损、持仓周期和行业轮动信号纳入评分。',
    }


def _build_review_prompt(trades: List[Dict[str, Any]], week_start: str, week_end: str, start_value: float, end_value: float) -> str:
    lines = [
        '## 周期: %s ~ %s' % (week_start, week_end),
        '## 期初资产: %.2f' % start_value,
        '## 期末资产: %.2f' % end_value,
        '## 本周交易',
    ]
    for item in trades[:30]:
        lines.append('- {date} {action} {name}({code}) 金额:{amount:.2f} 原因:{reason}'.format(
            date=item.get('date', ''),
            action='买入' if item.get('action') == 'buy' else '卖出',
            name=item.get('name', ''),
            code=item.get('code', ''),
            amount=float(item.get('amount', 0) or 0),
            reason=item.get('reason', ''),
        ))
    return '\n'.join(lines)


def _build_weekly_attribution(latest_review: Optional[Dict[str, Any]], latest_settle: Optional[Dict[str, Any]], latest_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    review = latest_review or {}
    ai_review = review.get('aiReview', {}) if isinstance(review, dict) else {}
    settlement = latest_settle or review.get('settlement', {}) if isinstance(review, dict) else {}
    trades = list(latest_trades or [])
    buy_count = sum(1 for item in trades if item.get('action') == 'buy')
    sell_count = sum(1 for item in trades if item.get('action') == 'sell')
    buy_amount = round(sum(float(item.get('amount', 0) or 0) for item in trades if item.get('action') == 'buy'), 2)
    sell_amount = round(sum(float(item.get('amount', 0) or 0) for item in trades if item.get('action') == 'sell'), 2)
    summary = ai_review.get('summary') or '暂无周度归因总结'
    analysis = ai_review.get('analysis') or ''
    lessons = ai_review.get('lessons') or []
    next_strategy = ai_review.get('nextWeekStrategy') or ''
    best_trade = ((ai_review.get('performance') or {}).get('bestTrade', ''))
    worst_trade = ((ai_review.get('performance') or {}).get('worstTrade', ''))
    return_pct_text = str(review.get('returnPct', '0%') or '0%').replace('%', '').replace('+', '')
    try:
        return_pct_num = float(return_pct_text)
    except Exception:
        return_pct_num = 0.0
    timing_score = max(35, min(92, 55 + int(return_pct_num * 8) + sell_count * 4))
    position_score = max(30, min(90, 70 - max(0, buy_count - 2) * 8 + int((sell_amount - buy_amount) / 5000)))
    risk_score = max(28, min(95, 62 + sell_count * 6 + (8 if '风险' in analysis or '风控' in analysis else 0)))
    selection_score = max(30, min(94, 58 + buy_count * 5 + (10 if best_trade else 0)))
    factor_breakdown = [
        {
            'key': 'timing',
            'label': '择时',
            'score': timing_score,
            'summary': '卖出执行与调仓时点%s' % ('较优' if timing_score >= 65 else '一般'),
        },
        {
            'key': 'position',
            'label': '仓位控制',
            'score': position_score,
            'summary': '本周仓位节奏%s' % ('较稳健' if position_score >= 65 else '偏激进'),
        },
        {
            'key': 'risk',
            'label': '风控执行',
            'score': risk_score,
            'summary': '止盈止损与减仓执行%s' % ('到位' if risk_score >= 65 else '偏弱'),
        },
        {
            'key': 'selection',
            'label': '标的选择',
            'score': selection_score,
            'summary': '本周候选标的质量%s' % ('较高' if selection_score >= 65 else '一般'),
        },
    ]
    return {
        'week': review.get('week', ''),
        'weekStart': review.get('weekStart', ''),
        'weekEnd': review.get('weekEnd', ''),
        'returnPct': review.get('returnPct', ''),
        'weekPnl': settlement.get('weekPnl', settlement.get('dailyPnl', 0)),
        'buyCount': buy_count,
        'sellCount': sell_count,
        'buyAmount': buy_amount,
        'sellAmount': sell_amount,
        'summary': summary,
        'analysis': analysis,
        'bestTrade': best_trade,
        'worstTrade': worst_trade,
        'lessons': lessons[:3] if isinstance(lessons, list) else [],
        'nextWeekStrategy': next_strategy,
        'factorBreakdown': factor_breakdown,
    }


def run_weekly_review(now: Optional[datetime] = None, force: bool = False) -> Dict[str, Any]:
    current = now or datetime.now()
    if not force:
        if current.weekday() != 4:
            return {'status': 'skipped', 'reason': 'not_friday'}
        if current.hour < 15 or (current.hour == 15 and current.minute < 10):
            return {'status': 'skipped', 'reason': 'before_1510'}
    portfolio = load_portfolio()
    week_key = _iso_week_key(current)
    if not force and portfolio.get('lastReviewWeek') == week_key:
        return {'status': 'skipped', 'reason': 'already_reviewed_this_week'}

    live_positions, total_value = _build_live_positions(portfolio)
    position_value = round(total_value - float(portfolio.get('cash', 0) or 0), 2)
    settle_log = load_settle_log()
    prev = settle_log[0] if settle_log else None
    start_value = float(prev.get('totalValue', portfolio.get('totalCash', INITIAL_CAPITAL)) if prev else portfolio.get('totalCash', INITIAL_CAPITAL))
    week_pnl = total_value - start_value
    week_pct = (week_pnl / start_value * 100) if start_value else 0
    settlement = append_settlement({
        'date': today_str(current),
        'week': week_key,
        'kind': 'weekly',
        'cash': round(float(portfolio.get('cash', 0) or 0), 2),
        'positionValue': position_value,
        'totalValue': round(total_value, 2),
        'weekPnl': round(week_pnl, 2),
        'weekPct': round(week_pct, 2),
        'positions': [{
            'code': item.get('code', ''),
            'name': item.get('name', ''),
            'type': item.get('type', ''),
            'shares': round(float(item.get('shares', 0) or 0), 4),
            'value': round(float(item.get('currentValue', 0) or 0), 2),
            'price': round(float(item.get('currentPrice', 0) or 0), 4),
        } for item in live_positions],
    })

    week_end = today_str(current)
    week_start = (current - timedelta(days=4)).strftime('%Y-%m-%d')
    trades = [_normalize_trade_entry(item, portfolio.get('totalCash', INITIAL_CAPITAL)) for item in load_trade_log() if week_start <= str(item.get('date', '')) <= week_end]
    review = _call_ai_json(REVIEW_SYSTEM_PROMPT, _build_review_prompt(trades, week_start, week_end, start_value, total_value), temperature=0.45)
    if not review:
        review = _build_review_fallback(trades, start_value, total_value, week_start, week_end)

    payload = append_weekly_review({
        'weekStart': week_start,
        'weekEnd': week_end,
        'week': week_key,
        'startValue': round(start_value, 2),
        'endValue': round(total_value, 2),
        'returnPct': '%+.2f%%' % week_pct,
        'aiReview': review,
        'trades': trades,
        'settlement': settlement,
        'createdAt': datetime.now().isoformat(),
    })
    portfolio['lastReviewWeek'] = week_key
    save_portfolio(portfolio)
    return {
        'status': 'executed',
        'review': payload,
        'settlement': settlement,
    }


def get_status_payload() -> Dict[str, Any]:
    portfolio = load_portfolio()
    live_positions, total_value = _build_live_positions(portfolio)
    total_cash = float(portfolio.get('totalCash', INITIAL_CAPITAL) or INITIAL_CAPITAL)
    total_return = ((total_value - total_cash) / total_cash * 100) if total_cash else 0
    raw_settle = load_settle_log()[:20]
    # 统一 settleLog 输出字段名：pnl / pct
    latest_settle = []
    for _se in raw_settle:
        se = dict(_se)
        se['pnl'] = se.get('dailyPnl') if se.get('dailyPnl') is not None else se.get('weekPnl', 0)
        se['pct'] = se.get('dailyPct') if se.get('dailyPct') is not None else se.get('weekPct', 0)
        latest_settle.append(se)
    raw_latest_reviews = load_weekly_reviews()[:12]
    week_key_now = _iso_week_key()
    all_trades_raw = [_normalize_trade_entry(item, total_cash) for item in load_trade_log()[:80]]
    latest_trades = _compact_trade_entries(all_trades_raw)

    latest_reviews = []
    for item in raw_latest_reviews:
        review_payload = dict(item)
        raw_review_trades = [_normalize_trade_entry(trade, total_cash) for trade in list((item or {}).get('trades', []) or [])]
        review_payload['trades'] = _compact_trade_entries(raw_review_trades)[:20]
        latest_reviews.append(review_payload)

    latest_review_raw = raw_latest_reviews[0] if raw_latest_reviews else None
    latest_review_week = str((latest_review_raw or {}).get('week', '') if isinstance(latest_review_raw, dict) else '')
    if latest_review_week == week_key_now:
        # 本周已有周复盘，使用复盘中的交易记录
        raw_weekly_trades = [_normalize_trade_entry(item, total_cash) for item in list((latest_review_raw or {}).get('trades', []) if isinstance(latest_review_raw, dict) else [])[:20]]
    else:
        # 本周尚无周复盘，从 trade log 中筛选本周所有交易
        now = datetime.now()
        week_monday = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
        raw_weekly_trades = [_normalize_trade_entry(item, total_cash) for item in load_trade_log()[:200] if str(item.get('date', '') or '') >= week_monday]
    weekly_trades = _compact_trade_entries(raw_weekly_trades)
    weekly_attribution = _build_weekly_attribution(latest_review_raw if latest_review_week == week_key_now else None, latest_settle[0] if latest_settle else None, raw_weekly_trades)
    return {
        'status': 'ok',
        'mode': 'server_auto',
        'portfolio': {
            **portfolio,
            'positions': live_positions,
            'totalValue': round(total_value, 2),
            'totalReturnPct': round(total_return, 2),
        },
        'tradeLog': latest_trades,
        'settleLog': latest_settle,
        'weeklyReviews': latest_reviews,
        'weeklyTradeDetails': weekly_trades,
        'weeklyAttribution': weekly_attribution,
        'meta': {
            'tradeSchedule': '每个交易日 14:55 自动加减仓',
            'reviewSchedule': '每周五 15:10 自动结算并复盘',
            'initialCapital': round(INITIAL_CAPITAL, 2),
            'autoConfig': AUTO_TRADE_CONFIG,
            'aiStatus': dict(_ai_call_status),
        },
    }


if __name__ == '__main__':
    try:
        print(json.dumps(run_auto_trade(force=True), ensure_ascii=False, indent=2))
    except Exception:
        traceback.print_exc()
