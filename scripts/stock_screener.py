#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股全市场短线强势股扫描器 V3

服务端集成版本：
1. 遍历当天A股全部股票日K线
2. 技术面打分：趋势、形态、量能、RSI、MACD
3. 行业板块强度过滤
4. 输出前5只候选股
5. 输出人话建议
6. 自动给出关注买点 / 止损位 / 第一目标位 / 盈亏比
7. 将结果保存为 data/stock_screen.json，供小程序模拟仓展示
"""

import json
import os
import time
import urllib.parse
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta

import akshare as ak
import numpy as np
import pandas as pd
import requests as _requests

warnings.filterwarnings("ignore")

# Patch requests to enforce a default timeout and bypass system proxy for
# financial data APIs (eastmoney, sina, 10jqka) so akshare calls don't hang
# or fail when a local proxy (e.g. Privoxy) is configured.
_orig_adapter_send = _requests.adapters.HTTPAdapter.send
_DIRECT_CONNECT_HOSTS = ('eastmoney.com', 'sina.com.cn', '10jqka.com.cn', 'akshare')
def _timeout_adapter_send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
    if timeout is None:
        timeout = 15
    if proxies and hasattr(request, 'url') and request.url:
        for _host in _DIRECT_CONNECT_HOSTS:
            if _host in request.url:
                proxies = {}
                break
    return _orig_adapter_send(self, request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)
_requests.adapters.HTTPAdapter.send = _timeout_adapter_send

try:
    from tqdm import tqdm
except Exception:
    def tqdm(iterable, **kwargs):
        return iterable


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
SCREEN_FILE = os.path.join(DATA_DIR, 'stock_screen.json')

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    'Referer': 'https://quote.eastmoney.com/',
}

_INDUSTRY_FALLBACK_CACHE = {}


CONFIG = {
    'lookback_days': 100,
    'min_history_days': 35,
    'ma_short': 5,
    'ma_long': 20,
    'rsi_period': 14,
    'rsi_oversold': 35,
    'rsi_overbought': 70,
    'atr_period': 14,
    'volume_multiplier': 1.35,
    'hammer_shadow_ratio': 2.0,
    'double_bottom_tol': 0.02,
    'score_threshold': 64,
    'top_n': 5,
    'sleep_seconds': 0.05,
    'test_mode': False,
    'test_stock_limit': 300,
    'enable_sector_filter': True,
    'sector_hist_days': 20,
    'sector_score_min': 58,
    'sector_top_n': 30,
    'sector_sleep_seconds': 0.03,
    'output_file': os.path.join(DATA_DIR, 'top5_candidates_v3.csv'),
    'sector_cache_file': os.path.join(DATA_DIR, 'sector_strength.csv'),
    'sector_map_cache_file': os.path.join(DATA_DIR, 'stock_sector_map.csv'),
    'save_top_charts': False,
    'chart_dir': os.path.join(DATA_DIR, 'top5_charts_v3'),
    'breakout_buy_buffer': 0.003,
    'buy_chase_limit': 0.03,
    'support_buffer': 0.01,
    'stop_loss_buffer': 0.03,
    'default_rr': 2.0,
    'min_rr_show': 1.2,
}


def safe_float(value, default=np.nan):
    try:
        return float(value)
    except Exception:
        return default


def safe_round(value, digits=2, default=np.nan):
    try:
        if pd.isna(value):
            return default
        return round(float(value), digits)
    except Exception:
        return default


def pct(a, b):
    if b == 0 or pd.isna(a) or pd.isna(b):
        return 0
    return (a / b - 1) * 100


def clamp(value, low, high):
    return max(low, min(value, high))


def num_or_none(value, digits=2):
    if pd.isna(value):
        return None
    return safe_round(value, digits)


def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def normalize_sector_text(text):
    value = str(text or '').strip().lower()
    for token in ('行业', '概念', '板块', 'ⅰ', 'ⅱ', 'ⅲ', '（', '）', '(', ')', ' '):
        value = value.replace(token, '')
    return value


def match_sector_name(industry_name, sector_names):
    target = normalize_sector_text(industry_name)
    if not target:
        return ''

    for sector_name in sector_names:
        norm = normalize_sector_text(sector_name)
        if norm == target:
            return sector_name

    for sector_name in sector_names:
        norm = normalize_sector_text(sector_name)
        if norm and (norm in target or target in norm):
            return sector_name

    return ''


def fetch_stock_industry_fallback(code, sector_names):
    cache_key = str(code).zfill(6)
    if cache_key in _INDUSTRY_FALLBACK_CACHE:
        return _INDUSTRY_FALLBACK_CACHE[cache_key]

    try:
        df = ak.stock_industry_change_cninfo(
            symbol=cache_key,
            start_date='20200101',
            end_date=datetime.now().strftime('%Y%m%d'),
        )
        if df is None or df.empty:
            _INDUSTRY_FALLBACK_CACHE[cache_key] = ''
            return ''

        df = df.copy()
        if '变更日期' in df.columns:
            df['变更日期'] = pd.to_datetime(df['变更日期'], errors='coerce')
            df = df.sort_values('变更日期', ascending=False)

        preferred = df[df['分类标准'].astype(str).str.contains('上市公司协会', na=False)].copy()
        candidates = [preferred, df]
        industry_columns = ['行业大类', '行业次类', '行业中类']

        for candidate_df in candidates:
            if candidate_df.empty:
                continue
            latest_row = candidate_df.iloc[0]
            for column in industry_columns:
                industry_name = str(latest_row.get(column, '') or '').strip()
                if not industry_name or industry_name.lower() == 'nan':
                    continue
                matched = match_sector_name(industry_name, sector_names)
                if matched:
                    _INDUSTRY_FALLBACK_CACHE[cache_key] = matched
                    return matched
                _INDUSTRY_FALLBACK_CACHE[cache_key] = industry_name
                return industry_name
    except Exception:
        pass

    _INDUSTRY_FALLBACK_CACHE[cache_key] = ''
    return ''


def ema_series(series, span):
    return series.ewm(span=span, adjust=False).mean()


def rsi_series(series, period):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.bfill()


def atr_series(high, low, close, period):
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def macd_frame(series):
    ema12 = ema_series(series, 12)
    ema26 = ema_series(series, 26)
    macd_line = ema12 - ema26
    macd_signal = ema_series(macd_line, 9)
    macd_hist = macd_line - macd_signal
    return pd.DataFrame({
        'macd_line': macd_line,
        'macd_hist': macd_hist,
        'macd_signal': macd_signal,
    })


def build_reason(row):
    reasons = []

    if row.get('sector_strong', False):
        reasons.append('所属行业板块较强')
    if row.get('breakout', False):
        reasons.append('突破关键阻力位')
    if row.get('engulfing', False):
        reasons.append('出现看涨吞没')
    if row.get('hammer', False):
        reasons.append('出现锤头止跌信号')
    if row.get('double_bottom', False):
        reasons.append('形成双底结构')
    if row.get('support_stable', False):
        reasons.append('支撑位附近企稳')
    if row.get('hh_hl', False):
        reasons.append('高低点持续抬高')
    if row.get('ma5_gt_ma20', False):
        reasons.append('5日均线强于20日均线')
    if row.get('ma5_up', False):
        reasons.append('5日均线向上')
    if row.get('ma20_up', False):
        reasons.append('20日均线向上')
    if row.get('ma_multi_head', False):
        reasons.append('均线多头排列')
    if row.get('vol_surge', False):
        reasons.append('成交量明显放大')
    if row.get('pv_sync', False):
        reasons.append('量价配合良好')
    if row.get('rsi_rebound', False):
        reasons.append('RSI低位回升')
    if row.get('macd_cross', False):
        reasons.append('MACD金叉')

    return '、'.join(reasons) if reasons else '满足基础强势条件'


def build_risk_note(row):
    risks = []
    if row.get('pv_diverge', False):
        risks.append('上涨缩量')
    if row.get('rsi_extreme', False):
        risks.append('RSI严重过热')
    elif row.get('rsi_hot', False):
        risks.append('RSI偏热')
    if row.get('upper_shadow_risk', False):
        risks.append('长上影风险')
    if row.get('too_far_from_breakout', False):
        risks.append('距离突破观察位偏远')
    return '、'.join(risks) if risks else '无明显风险'


def build_advice(row):
    score = row.get('score', 0)
    breakout = row.get('breakout', False)
    vol_surge = row.get('vol_surge', False)
    macd_cross = row.get('macd_cross', False)
    support_stable = row.get('support_stable', False)
    double_bottom = row.get('double_bottom', False)
    rsi_rebound = row.get('rsi_rebound', False)
    ma5_gt_ma20 = row.get('ma5_gt_ma20', False)
    price_above_ma20 = row.get('price_above_ma20', False)
    sector_strong = row.get('sector_strong', False)
    rsi = safe_float(row.get('rsi', np.nan), np.nan)

    if breakout and vol_surge and macd_cross and sector_strong and score >= 88:
        return '适合突破跟随'
    if support_stable and ma5_gt_ma20 and price_above_ma20 and sector_strong and score >= 80:
        return '适合回踩低吸观察'
    if double_bottom and rsi_rebound and macd_cross and sector_strong and score >= 78:
        return '适合反转观察'
    if not pd.isna(rsi) and rsi >= 65:
        return '趋势较强，但不建议追高'
    if sector_strong and score >= 75:
        return '适合重点关注'
    return '仅列入观察，不建议急追'


def get_stock_list():
    df = ak.stock_info_a_code_name()
    df.columns = ['code', 'name']
    df = df[df['code'].astype(str).str.startswith(('00', '30', '60', '68'))].copy()
    df['code'] = df['code'].astype(str).str.zfill(6)
    df['name'] = df['name'].astype(str).str.strip()
    df = df[~df['name'].str.upper().str.contains('ST', na=False)]
    df = df[~df['name'].str.contains('退', na=False)]
    return df.reset_index(drop=True)


_KLINE_EXECUTOR = ThreadPoolExecutor(max_workers=1)

# Track consecutive eastmoney failures to fast-switch to sina
_eastmoney_fail_count = 0
_USE_SINA_ONLY = False


def _code_to_sina_symbol(code):
    """Convert 6-digit code to sina-style symbol like sh600519, sz000001"""
    code = str(code).zfill(6)
    if code.startswith(('6', '9')):
        return f'sh{code}'
    return f'sz{code}'


def _fetch_kline_sina(code, days, start_date, end_date):
    """Fallback: use stock_zh_a_daily (sina source)"""
    symbol = _code_to_sina_symbol(code)
    df = ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=end_date, adjust='qfq')
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.reset_index()
    col_map = {}
    for col in df.columns:
        lower = str(col).lower()
        if lower == 'date':
            col_map[col] = 'date'
        elif lower == 'open':
            col_map[col] = 'open'
        elif lower == 'high':
            col_map[col] = 'high'
        elif lower == 'low':
            col_map[col] = 'low'
        elif lower == 'close':
            col_map[col] = 'close'
        elif lower == 'volume':
            col_map[col] = 'volume'
    df = df.rename(columns=col_map)
    needed = ['date', 'open', 'high', 'low', 'close', 'volume']
    if not all(c in df.columns for c in needed):
        return pd.DataFrame()
    df = df[needed].copy()
    df['date'] = pd.to_datetime(df['date'])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.dropna().sort_values('date').reset_index(drop=True).tail(days).copy()


def _fetch_kline_inner(code, days, start_date, end_date):
    global _eastmoney_fail_count, _USE_SINA_ONLY

    # If eastmoney is down, go straight to sina
    if _USE_SINA_ONLY:
        return _fetch_kline_sina(code, days, start_date, end_date)

    try:
        df = ak.stock_zh_a_hist(
            symbol=code,
            period='daily',
            start_date=start_date,
            end_date=end_date,
            adjust='qfq',
        )
        if df is not None and not df.empty:
            _eastmoney_fail_count = 0
            df = df[['日期', '开盘', '最高', '最低', '收盘', '成交量']].copy()
            df.columns = ['date', 'open', 'high', 'low', 'close', 'volume']
            df['date'] = pd.to_datetime(df['date'])
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            return df.dropna().sort_values('date').reset_index(drop=True).tail(days).copy()
    except Exception:
        _eastmoney_fail_count += 1
        if _eastmoney_fail_count >= 5 and not _USE_SINA_ONLY:
            _USE_SINA_ONLY = True
            print(f'[stock_screen] ⚠️ 东方财富API连续{_eastmoney_fail_count}次失败，切换至新浪数据源')

    # Fallback to sina
    try:
        return _fetch_kline_sina(code, days, start_date, end_date)
    except Exception:
        return pd.DataFrame()


def fetch_kline(code, days=100, timeout=20):
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days + 50)).strftime('%Y%m%d')

    try:
        future = _KLINE_EXECUTOR.submit(_fetch_kline_inner, code, days, start_date, end_date)
        return future.result(timeout=timeout)
    except (FuturesTimeoutError, Exception):
        return pd.DataFrame()


def get_sector_list():
    try:
        df = ak.stock_sector_spot(indicator='行业')
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={'板块': 'sector_name', 'label': 'sector_label', '涨跌幅': 'spot_pct'})
        return df[['sector_name', 'sector_label', 'spot_pct']].dropna(subset=['sector_name']).copy()
    except Exception:
        return pd.DataFrame()


def fetch_sector_hist(sector_name, days=20):
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days + 20)).strftime('%Y%m%d')

    try:
        df = ak.stock_board_industry_index_ths(
            symbol=sector_name,
            start_date=start_date,
            end_date=end_date,
        )
        if df is None or df.empty:
            return pd.DataFrame()

        cols_map = {}
        for col in df.columns:
            if col == '日期':
                cols_map[col] = 'date'
            elif col in ('开盘', '开盘价'):
                cols_map[col] = 'open'
            elif col in ('最高', '最高价'):
                cols_map[col] = 'high'
            elif col in ('最低', '最低价'):
                cols_map[col] = 'low'
            elif col in ('收盘', '收盘价'):
                cols_map[col] = 'close'
            elif col == '成交量':
                cols_map[col] = 'volume'
            elif col == '成交额':
                cols_map[col] = 'amount'

        df = df.rename(columns=cols_map)
        if 'date' not in df.columns or 'close' not in df.columns:
            return pd.DataFrame()

        use_cols = [col for col in ['date', 'open', 'high', 'low', 'close', 'volume', 'amount'] if col in df.columns]
        df = df[use_cols].copy()
        df['date'] = pd.to_datetime(df['date'])

        for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        return df.dropna(subset=['close']).sort_values('date').reset_index(drop=True).tail(days).copy()
    except Exception:
        return pd.DataFrame()


def score_sector_hist(df):
    if df.empty or len(df) < 10:
        return {
            'sector_score': 0,
            'ret_3d': 0,
            'ret_5d': 0,
            'ret_10d': 0,
            'close_above_ma5': False,
            'ma5_above_ma10': False,
            'ma5_up': False,
        }

    df = df.copy()
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()

    close_now = df['close'].iloc[-1]
    ret_3d = pct(close_now, df['close'].iloc[-4]) if len(df) >= 4 else 0
    ret_5d = pct(close_now, df['close'].iloc[-6]) if len(df) >= 6 else 0
    ret_10d = pct(close_now, df['close'].iloc[-11]) if len(df) >= 11 else 0

    close_above_ma5 = bool(close_now > df['ma5'].iloc[-1]) if not pd.isna(df['ma5'].iloc[-1]) else False
    ma5_above_ma10 = bool(df['ma5'].iloc[-1] > df['ma10'].iloc[-1]) if not pd.isna(df['ma10'].iloc[-1]) else False
    ma5_up = bool(df['ma5'].iloc[-1] > df['ma5'].iloc[-2]) if len(df) >= 6 and not pd.isna(df['ma5'].iloc[-2]) else False

    score = 50
    score += clamp(ret_3d * 2, -12, 12)
    score += clamp(ret_5d * 2, -16, 16)
    score += clamp(ret_10d * 1.5, -18, 18)

    if close_above_ma5:
        score += 6
    if ma5_above_ma10:
        score += 6
    if ma5_up:
        score += 4

    return {
        'sector_score': clamp(round(score, 2), 0, 100),
        'ret_3d': round(ret_3d, 2),
        'ret_5d': round(ret_5d, 2),
        'ret_10d': round(ret_10d, 2),
        'close_above_ma5': close_above_ma5,
        'ma5_above_ma10': ma5_above_ma10,
        'ma5_up': ma5_up,
    }


def build_sector_strength_table():
    print('[stock_screen] 开始计算行业板块强度...')
    sector_df = get_sector_list()
    if sector_df.empty:
        print('[stock_screen] 行业列表获取失败，将跳过板块过滤。')
        return pd.DataFrame()

    results = []
    for _, row in tqdm(sector_df.iterrows(), total=len(sector_df), desc='计算行业强度'):
        sector_name = str(row['sector_name']).strip()
        hist = fetch_sector_hist(sector_name, CONFIG['sector_hist_days'])
        if hist.empty:
            spot_pct = safe_float(row.get('spot_pct', 0), 0)
            scored = {
                'sector_score': clamp(50 + spot_pct * 6, 0, 100),
                'ret_3d': 0,
                'ret_5d': safe_round(spot_pct),
                'ret_10d': 0,
                'close_above_ma5': spot_pct > 0,
                'ma5_above_ma10': spot_pct > 0.5,
                'ma5_up': spot_pct > 0,
            }
        else:
            scored = score_sector_hist(hist)
        scored['sector_name'] = sector_name
        scored['sector_label'] = row.get('sector_label', '')
        results.append(scored)
        time.sleep(CONFIG['sector_sleep_seconds'])

    if not results:
        return pd.DataFrame()

    out = pd.DataFrame(results)
    out = out.sort_values(by=['sector_score', 'ret_5d', 'ret_3d'], ascending=False).reset_index(drop=True)
    out['sector_rank'] = np.arange(1, len(out) + 1)
    out['sector_strong'] = (
        (out['sector_score'] >= CONFIG['sector_score_min']) |
        (out['sector_rank'] <= CONFIG['sector_top_n'])
    )
    out.to_csv(CONFIG['sector_cache_file'], index=False, encoding='utf-8-sig')
    return out


def build_stock_sector_map(sector_strength_df):
    print('[stock_screen] 开始建立股票与行业映射...')
    if sector_strength_df.empty:
        return pd.DataFrame(columns=['code', 'sector_name'])

    mapping_rows = []
    seen = set()
    sector_rows = sector_strength_df[['sector_name', 'sector_label']].dropna(subset=['sector_label']).drop_duplicates()

    for _, row in tqdm(sector_rows.iterrows(), total=len(sector_rows), desc='建立行业映射'):
        sector_name = str(row['sector_name']).strip()
        sector_label = str(row['sector_label']).strip()
        if not sector_label or sector_label in seen:
            continue
        seen.add(sector_label)
        try:
            cons = ak.stock_sector_detail(sector=sector_label)
            if cons is None or cons.empty:
                continue
            temp = cons[['code']].copy()
            temp['code'] = temp['code'].astype(str).str.zfill(6)
            temp['sector_name'] = sector_name
            mapping_rows.append(temp)
            time.sleep(CONFIG['sector_sleep_seconds'])
        except Exception:
            continue

    if not mapping_rows:
        return pd.DataFrame(columns=['code', 'sector_name'])

    mapping_df = pd.concat(mapping_rows, ignore_index=True).drop_duplicates(subset=['code'], keep='first')
    mapping_df.to_csv(CONFIG['sector_map_cache_file'], index=False, encoding='utf-8-sig')
    return mapping_df


def compute_indicators(df):
    if df.empty or len(df) < CONFIG['min_history_days']:
        return pd.DataFrame()

    df = df.copy()
    closes = df['close']
    opens = df['open']
    highs = df['high']
    lows = df['low']
    volume = df['volume']

    df['ma5'] = closes.rolling(CONFIG['ma_short']).mean()
    df['ma20'] = closes.rolling(CONFIG['ma_long']).mean()
    df['vol_ma5'] = volume.rolling(5).mean()
    df['rsi'] = rsi_series(closes, CONFIG['rsi_period'])
    df['atr'] = atr_series(highs, lows, closes, CONFIG['atr_period'])

    macd_df = macd_frame(closes)
    df['macd_line'] = macd_df['macd_line']
    df['macd_hist'] = macd_df['macd_hist']
    df['macd_signal'] = macd_df['macd_signal']

    df['body'] = (closes - opens).abs()
    df['upper_shadow'] = highs - df[['open', 'close']].max(axis=1)
    df['lower_shadow'] = df[['open', 'close']].min(axis=1) - lows
    return df


def detect_bullish_engulfing(df):
    if len(df) < 2:
        return False
    prev = df.iloc[-2]
    curr = df.iloc[-1]
    prev_bearish = prev['close'] < prev['open']
    curr_bullish = curr['close'] > curr['open']
    body_engulf = curr['close'] >= prev['open'] and curr['open'] <= prev['close']
    return bool(prev_bearish and curr_bullish and body_engulf)


def detect_hammer(df):
    if len(df) < 5:
        return False

    row = df.iloc[-1]
    body = safe_float(row['body'], 0)
    lower_shadow = safe_float(row['lower_shadow'], 0)
    upper_shadow = safe_float(row['upper_shadow'], 0)
    if body <= 0:
        return False

    prior_closes = df['close'].iloc[-4:-1]
    prior_decline = prior_closes.is_monotonic_decreasing
    has_long_lower = lower_shadow >= CONFIG['hammer_shadow_ratio'] * body
    has_short_upper = upper_shadow <= 0.5 * body
    return bool(prior_decline and has_long_lower and has_short_upper)


def detect_double_bottom(df):
    if len(df) < 30:
        return False

    lows = df['low'].values
    highs = df['high'].values
    tol = CONFIG['double_bottom_tol']
    half = len(lows) // 2
    b1 = np.min(lows[:half])
    b1_idx = np.argmin(lows[:half])
    b2 = np.min(lows[half:])
    b2_idx = half + np.argmin(lows[half:])

    if b1 <= 0 or b2_idx <= b1_idx:
        return False

    similar = abs(b1 - b2) / b1 <= tol
    middle_high = np.max(highs[b1_idx:b2_idx + 1])
    rebound_ok = middle_high > max(b1, b2) * 1.03
    return bool(similar and rebound_ok)


def detect_support_stabilization(df):
    if len(df) < 20:
        return False

    recent_low = df['low'].iloc[-10:].min()
    recent_high = df['high'].iloc[-20:].max()
    last_close = df['close'].iloc[-1]
    if recent_high <= recent_low:
        return False

    in_support_zone = (last_close - recent_low) / (recent_high - recent_low) < 0.35
    lows_last3 = df['low'].iloc[-3:].values
    no_new_low = lows_last3[-1] >= lows_last3[-2] and lows_last3[-2] >= lows_last3[-3]
    return bool(in_support_zone and no_new_low)


def detect_breakout(df):
    if len(df) < 21:
        return False
    prev_high_20 = df['high'].iloc[-21:-1].max()
    last_close = df['close'].iloc[-1]
    return bool(last_close > prev_high_20)


def detect_higher_highs_lows(df):
    if len(df) < 30:
        return False

    highs = df['high'].values
    lows = df['low'].values
    n = len(df)
    width = n // 3
    h1 = np.max(highs[:width])
    h2 = np.max(highs[width:2 * width])
    h3 = np.max(highs[2 * width:])
    l1 = np.min(lows[:width])
    l2 = np.min(lows[width:2 * width])
    l3 = np.min(lows[2 * width:])
    return bool(h2 > h1 and h3 > h2 and l2 > l1 and l3 > l2)


def detect_ma_trend(df):
    if len(df) < 25:
        return {
            'ma5_gt_ma20': False,
            'ma5_up': False,
            'ma20_up': False,
            'price_above_ma5': False,
            'price_above_ma20': False,
        }

    ma5 = df['ma5'].iloc[-1]
    ma20 = df['ma20'].iloc[-1]
    ma5_prev = df['ma5'].iloc[-2]
    ma20_prev = df['ma20'].iloc[-2]
    close_now = df['close'].iloc[-1]

    return {
        'ma5_gt_ma20': bool(ma5 > ma20),
        'ma5_up': bool(ma5 > ma5_prev),
        'ma20_up': bool(ma20 > ma20_prev),
        'price_above_ma5': bool(close_now > ma5),
        'price_above_ma20': bool(close_now > ma20),
    }


def detect_volume_surge(df):
    if len(df) < 6:
        return False
    today_vol = df['volume'].iloc[-1]
    vol_ma5 = df['vol_ma5'].iloc[-1]
    if pd.isna(vol_ma5) or vol_ma5 <= 0:
        return False
    return bool(today_vol >= CONFIG['volume_multiplier'] * vol_ma5)


def detect_price_volume_sync(df):
    if len(df) < 6:
        return {'pv_sync': False, 'pv_diverge': False}

    close_now = df['close'].iloc[-1]
    close_prev = df['close'].iloc[-2]
    vol_now = df['volume'].iloc[-1]
    vol_ma5 = df['vol_ma5'].iloc[-1]
    if pd.isna(vol_ma5) or vol_ma5 <= 0:
        return {'pv_sync': False, 'pv_diverge': False}

    price_up = close_now > close_prev
    pv_sync = price_up and vol_now > vol_ma5
    pv_diverge = price_up and vol_now < vol_ma5 * 0.8
    return {'pv_sync': bool(pv_sync), 'pv_diverge': bool(pv_diverge)}


def detect_rsi_signal(df):
    if len(df) < 3 or df['rsi'].iloc[-2:].isna().any():
        return {'rsi_rebound': False, 'rsi_hot': False}

    rsi_prev = df['rsi'].iloc[-2]
    rsi_now = df['rsi'].iloc[-1]
    return {
        'rsi_rebound': bool(rsi_prev < CONFIG['rsi_oversold'] and rsi_now > rsi_prev),
        'rsi_hot': bool(rsi_now > CONFIG['rsi_overbought']),
        'rsi_extreme': bool(rsi_now > 80),
    }


def detect_macd_cross(df):
    if len(df) < 3:
        return False
    if df[['macd_line', 'macd_signal']].iloc[-2:].isna().any().any():
        return False

    prev_line = df['macd_line'].iloc[-2]
    prev_signal = df['macd_signal'].iloc[-2]
    now_line = df['macd_line'].iloc[-1]
    now_signal = df['macd_signal'].iloc[-1]
    return bool(prev_line <= prev_signal and now_line > now_signal)


def detect_long_upper_shadow(df):
    if len(df) < 2:
        return False

    row = df.iloc[-1]
    body = safe_float(row['body'], 0)
    upper_shadow = safe_float(row['upper_shadow'], 0)
    vol_now = safe_float(row['volume'], 0)
    vol_ma5 = safe_float(row['vol_ma5'], 0)
    if body <= 0 or vol_ma5 <= 0:
        return False
    return bool(upper_shadow >= 2 * body and vol_now > 1.2 * vol_ma5)


def extract_trade_levels(df):
    last_close = safe_float(df['close'].iloc[-1], np.nan)
    ma5 = safe_float(df['ma5'].iloc[-1], np.nan) if 'ma5' in df.columns else np.nan
    ma20 = safe_float(df['ma20'].iloc[-1], np.nan) if 'ma20' in df.columns else np.nan
    atr = safe_float(df['atr'].iloc[-1], np.nan) if 'atr' in df.columns else np.nan
    prev_high_20 = safe_float(df['high'].iloc[-21:-1].max(), np.nan) if len(df) >= 21 else np.nan
    recent_low_10 = safe_float(df['low'].iloc[-10:].min(), np.nan) if len(df) >= 10 else np.nan
    recent_low_20 = safe_float(df['low'].iloc[-20:].min(), np.nan) if len(df) >= 20 else np.nan
    recent_high_20 = safe_float(df['high'].iloc[-20:].max(), np.nan) if len(df) >= 20 else np.nan
    return {
        'last_close': last_close,
        'ma5': ma5,
        'ma20': ma20,
        'atr': atr,
        'prev_high_20': prev_high_20,
        'recent_low_10': recent_low_10,
        'recent_low_20': recent_low_20,
        'recent_high_20': recent_high_20,
    }


def compute_rr(entry, stop, target):
    try:
        risk = entry - stop
        reward = target - entry
        if risk <= 0:
            return np.nan
        return safe_round(reward / risk, 2)
    except Exception:
        return np.nan


def build_trade_plan(row, levels):
    last_close = safe_float(levels.get('last_close'), np.nan)
    ma5 = safe_float(levels.get('ma5'), np.nan)
    ma20 = safe_float(levels.get('ma20'), np.nan)
    atr = safe_float(levels.get('atr'), np.nan)
    prev_high_20 = safe_float(levels.get('prev_high_20'), np.nan)
    recent_low_10 = safe_float(levels.get('recent_low_10'), np.nan)
    recent_low_20 = safe_float(levels.get('recent_low_20'), np.nan)
    recent_high_20 = safe_float(levels.get('recent_high_20'), np.nan)

    if pd.isna(atr) or atr <= 0:
        atr = max(last_close * 0.02, 0.01)

    plan_type = '观察'
    buy_low = np.nan
    buy_high = np.nan
    stop_loss = np.nan
    target_1 = np.nan
    note = ''

    if row.get('breakout', False):
        plan_type = '突破跟随'
        ref_entry = prev_high_20 * (1 + CONFIG['breakout_buy_buffer']) if not pd.isna(prev_high_20) else last_close
        buy_low = ref_entry
        buy_high = ref_entry * 1.01
        raw_stop = max(
            ma20 if not pd.isna(ma20) else -np.inf,
            recent_low_10 if not pd.isna(recent_low_10) else -np.inf,
            ref_entry - 1.5 * atr,
        )
        if raw_stop >= buy_low:
            raw_stop = buy_low * (1 - CONFIG['stop_loss_buffer'])
        stop_loss = raw_stop
        target_1 = buy_low + CONFIG['default_rr'] * (buy_low - stop_loss)
        if not pd.isna(recent_high_20):
            target_1 = max(target_1, recent_high_20 * 1.03)
        if last_close > buy_low * (1 + CONFIG['buy_chase_limit']):
            note = '当前价格距离突破观察位偏远，不建议追高'
        else:
            note = '放量突破后更适合跟随，若冲高无量需谨慎'
    elif row.get('support_stable', False):
        plan_type = '回踩低吸'
        zone_low = max(
            recent_low_10 * (1 + CONFIG['support_buffer']) if not pd.isna(recent_low_10) else -np.inf,
            ma20 if not pd.isna(ma20) else -np.inf,
        )
        zone_high = min(
            ma5 * 1.01 if not pd.isna(ma5) else np.inf,
            last_close * 1.01 if not pd.isna(last_close) else np.inf,
        )
        if zone_low <= 0 or zone_low >= zone_high:
            zone_low = min(last_close, ma20) if not pd.isna(ma20) else last_close * 0.99
            zone_high = max(last_close, ma5) if not pd.isna(ma5) else last_close * 1.01
        buy_low = zone_low
        buy_high = zone_high
        stop_loss = min(
            recent_low_10 * (1 - CONFIG['stop_loss_buffer']) if not pd.isna(recent_low_10) else buy_low * 0.97,
            buy_low - 1.2 * atr,
        )
        target_1 = max(recent_high_20, buy_high + CONFIG['default_rr'] * (buy_high - stop_loss))
        note = '更适合等待回踩确认，不宜离支撑位过远再追'
    elif row.get('double_bottom', False):
        plan_type = '反转观察'
        buy_low = max(ma5 if not pd.isna(ma5) else last_close * 0.99, last_close * 0.995)
        buy_high = buy_low * 1.01
        base_low = recent_low_20 if not pd.isna(recent_low_20) else recent_low_10
        stop_loss = min(
            base_low * (1 - 0.02) if not pd.isna(base_low) else buy_low * 0.96,
            buy_low - 1.5 * atr,
        )
        target_1 = max(recent_high_20, buy_low + CONFIG['default_rr'] * (buy_low - stop_loss))
        note = '更适合等反转结构继续确认，若重新跌破前低需严格止损'
    else:
        plan_type = '趋势关注'
        buy_low = max(ma5 if not pd.isna(ma5) else last_close * 0.99, last_close * 0.995)
        buy_high = buy_low * 1.01
        stop_loss = min(ma20 if not pd.isna(ma20) else buy_low * 0.97, buy_low - 1.2 * atr)
        if stop_loss >= buy_low:
            stop_loss = buy_low * (1 - CONFIG['stop_loss_buffer'])
        target_1 = max(recent_high_20, buy_low + CONFIG['default_rr'] * (buy_low - stop_loss))
        note = '趋势尚可，优先等回踩或小幅整理后再观察'

    rr_ref = compute_rr((buy_low + buy_high) / 2, stop_loss, target_1)
    too_far = False
    if not pd.isna(last_close) and not pd.isna(buy_high):
        too_far = last_close > buy_high * 1.01

    return {
        'plan_type': plan_type,
        'buy_low': safe_round(buy_low),
        'buy_high': safe_round(buy_high),
        'stop_loss': safe_round(stop_loss),
        'target_1': safe_round(target_1),
        'rr_ratio': rr_ref,
        'plan_note': note,
        'too_far_from_breakout': too_far,
    }


def format_buy_zone(row):
    low = row.get('buy_low', np.nan)
    high = row.get('buy_high', np.nan)
    if pd.isna(low) and pd.isna(high):
        return ''
    if pd.isna(high) or abs(low - high) < 0.01:
        return f'{safe_round(low)}'
    return f'{safe_round(low)} - {safe_round(high)}'


def score_stock(df, market_bonus=0, sector_bonus=0):
    score = 0
    hh_hl = detect_higher_highs_lows(df)
    ma_info = detect_ma_trend(df)

    if hh_hl:
        score += 10
    if ma_info['ma5_gt_ma20']:
        score += 5
    if ma_info['ma5_up']:
        score += 5
    if ma_info['ma20_up']:
        score += 5
    if ma_info['price_above_ma5']:
        score += 5
    if ma_info['price_above_ma20']:
        score += 5

    engulfing = detect_bullish_engulfing(df)
    hammer = detect_hammer(df)
    double_bottom = detect_double_bottom(df)
    support_stable = detect_support_stabilization(df)
    breakout = detect_breakout(df)

    if engulfing:
        score += 8
    if hammer:
        score += 8
    if double_bottom:
        score += 8
    if support_stable:
        score += 8
    if breakout:
        score += 10

    vol_surge = detect_volume_surge(df)
    pv = detect_price_volume_sync(df)
    if vol_surge:
        score += 8
    if pv['pv_sync']:
        score += 5
    if pv['pv_diverge']:
        score -= 8

    rsi_info = detect_rsi_signal(df)
    macd_cross = detect_macd_cross(df)
    if rsi_info['rsi_rebound']:
        score += 5
    if macd_cross:
        score += 5
    if rsi_info['rsi_hot']:
        score -= 3          # 70-80: 轻微扣分而非重罚
    if rsi_info.get('rsi_extreme', False):
        score -= 8          # RSI>80: 严重过热

    upper_shadow_risk = detect_long_upper_shadow(df)
    if upper_shadow_risk:
        score -= 8

    # 均线多头排列识别
    trend_score = sum([
        ma_info['ma5_gt_ma20'],
        ma_info['ma5_up'],
        ma_info['ma20_up'],
        ma_info['price_above_ma5'],
        ma_info['price_above_ma20'],
    ])
    ma_multi_head = bool(trend_score >= 4 and hh_hl)

    total_score = max(score + market_bonus + sector_bonus, 0)
    return {
        'score': total_score,
        'pattern_score': max(score, 0),
        'market_bonus': market_bonus,
        'sector_bonus': sector_bonus,
        'hh_hl': hh_hl,
        'ma5_gt_ma20': ma_info['ma5_gt_ma20'],
        'ma5_up': ma_info['ma5_up'],
        'ma20_up': ma_info['ma20_up'],
        'price_above_ma5': ma_info['price_above_ma5'],
        'price_above_ma20': ma_info['price_above_ma20'],
        'ma_multi_head': ma_multi_head,
        'engulfing': engulfing,
        'hammer': hammer,
        'double_bottom': double_bottom,
        'support_stable': support_stable,
        'breakout': breakout,
        'vol_surge': vol_surge,
        'pv_sync': pv['pv_sync'],
        'pv_diverge': pv['pv_diverge'],
        'rsi_rebound': rsi_info['rsi_rebound'],
        'rsi_hot': rsi_info['rsi_hot'],
        'rsi_extreme': rsi_info.get('rsi_extreme', False),
        'macd_cross': macd_cross,
        'upper_shadow_risk': upper_shadow_risk,
    }


def get_market_bonus():
    try:
        df = ak.stock_zh_index_daily(symbol='sh000001')
        df = df.tail(6).copy()
        if df.empty or len(df) < 6:
            return 0

        closes = pd.to_numeric(df['close'], errors='coerce').dropna().values
        if len(closes) < 6:
            return 0
        if closes[-1] > closes[-2] > closes[-3]:
            return 10
        if closes[-1] > closes[0]:
            return 5
        return 0
    except Exception:
        return 0


def get_sector_bonus(sector_score, sector_strong):
    if pd.isna(sector_score):
        return 0
    if sector_strong and sector_score >= 75:
        return 8
    if sector_strong and sector_score >= 65:
        return 5
    if sector_score >= 60:
        return 2
    return 0


def has_strong_trigger(row):
    return bool(
        row['engulfing'] or
        row['hammer'] or
        row['double_bottom'] or
        row['breakout'] or
        row['support_stable'] or
        row.get('ma_multi_head', False)
    )


def has_volume_confirmation(row):
    # 支撑企稳和双底不强制要求放量（缩量整理是正常形态）
    if row.get('support_stable') or row.get('double_bottom'):
        return True
    return bool(row['vol_surge'] or row['pv_sync'])


def is_low_risk_candidate(row):
    if row['pv_diverge']:
        return False
    if row.get('rsi_extreme', False):
        return False
    if row['upper_shadow_risk']:
        return False
    return True


def print_results(df):
    print('\n' + '=' * 100)
    print('今日前5只强势候选股（含板块过滤 + 交易计划）')
    print('=' * 100)

    if df.empty:
        print('今天没有筛出符合条件的股票。')
        return

    for idx, row in df.iterrows():
        print(f"{idx + 1}. {row['code']} {row['name']}")
        print(f"   所属行业: {row.get('sector_name', '')}")
        print(f"   行业分数: {safe_round(row.get('sector_score', np.nan))}   行业排名: {row.get('sector_rank', '')}")
        print(f"   最新价: {row['last_close']}")
        print(f"   综合分: {row['score']}   RSI: {row['rsi']}")
        print(f"   入选理由: {row['reason']}")
        print(f"   建议动作: {row['advice']}")
        print(f"   计划类型: {row.get('plan_type', '')}")
        print(f"   关注买点/区间: {row.get('buy_zone', '')}")
        print(f"   止损位: {row.get('stop_loss', '')}")
        print(f"   第一目标位: {row.get('target_1', '')}")
        print(f"   盈亏比参考: {row.get('rr_ratio', '')}")
        print(f"   计划说明: {row.get('plan_note', '')}")
        print(f"   风险提示: {row['risk_note']}")
        print('-' * 100)


def save_results(df):
    if df.empty:
        print('[stock_screen] 没有结果可保存。')
        return

    export_cols = [
        'code', 'name', 'sector_name', 'sector_score', 'sector_rank', 'sector_strong',
        'last_close', 'score', 'rsi', 'reason', 'advice', 'plan_type', 'buy_zone', 'buy_low', 'buy_high',
        'stop_loss', 'target_1', 'rr_ratio', 'plan_note', 'risk_note',
    ]
    keep_cols = [col for col in export_cols if col in df.columns]
    df[keep_cols].to_csv(CONFIG['output_file'], index=False, encoding='utf-8-sig')


def save_single_chart(code, name, df, out_dir):
    if df.empty:
        return

    try:
        import importlib
        mpf = importlib.import_module('mplfinance')
    except Exception:
        return

    os.makedirs(out_dir, exist_ok=True)

    chart_df = df.copy().tail(60)
    chart_df = chart_df[['date', 'open', 'high', 'low', 'close', 'volume']]
    chart_df.columns = ['Date', 'Open', 'High', 'Low', 'Close', 'Volume']
    chart_df = chart_df.set_index('Date')
    path = os.path.join(out_dir, f'{code}_{name}.png')

    try:
        mpf.plot(
            chart_df,
            type='candle',
            mav=(5, 20),
            volume=True,
            style='yahoo',
            title=f'{code} {name}',
            savefig={'fname': path, 'dpi': 120, 'bbox_inches': 'tight'},
        )
    except Exception:
        pass


def save_top_charts(result_df):
    if result_df.empty or not CONFIG['save_top_charts']:
        return

    print(f"[stock_screen] 开始保存前{len(result_df)}名K线图...")
    for _, row in result_df.iterrows():
        df = fetch_kline(row['code'], CONFIG['lookback_days'])
        if df.empty:
            continue
        save_single_chart(row['code'], row['name'], df, CONFIG['chart_dir'])


def dataframe_to_payload(df, market_bonus, scanned_count):
    if df.empty:
        market_state = '偏强' if market_bonus >= 10 else '震荡偏强' if market_bonus >= 5 else '震荡'
        return {
            'marketSummary': f'当前A股市场{market_state}，但严格条件下暂无达标候选股。',
            'strategyNote': '优先等待趋势、量能和板块共振同时出现，再考虑观察。',
            'picks': [],
            'qualifiedCount': 0,
            'scannedCount': scanned_count,
        }

    picks = []
    for _, row in df.iterrows():
        signal_list = []
        if row.get('sector_strong'):
            signal_list.append('强板块')
        if row.get('breakout'):
            signal_list.append('突破')
        if row.get('engulfing'):
            signal_list.append('吞没')
        if row.get('hammer'):
            signal_list.append('锤头')
        if row.get('double_bottom'):
            signal_list.append('双底')
        if row.get('support_stable'):
            signal_list.append('支撑企稳')
        if row.get('ma_multi_head'):
            signal_list.append('多头排列')
        if row.get('vol_surge'):
            signal_list.append('放量')
        if row.get('macd_cross'):
            signal_list.append('MACD金叉')
        if row.get('rsi_rebound'):
            signal_list.append('RSI回升')

        risk_level = '高' if row.get('rr_ratio', 0) < 1.2 else '中' if row.get('rr_ratio', 0) < 1.8 else '低'
        latest_pct = 0
        if not pd.isna(row.get('last_close')) and not pd.isna(row.get('buy_low')) and row.get('buy_low'):
            latest_pct = pct(row.get('last_close'), row.get('buy_low'))

        picks.append({
            'code': row['code'],
            'name': row['name'],
            'sector': row.get('sector_name', '') or '未知板块',
            'sectorScore': num_or_none(row.get('sector_score')),
            'sectorRank': None if pd.isna(row.get('sector_rank')) else int(row.get('sector_rank')),
            'score': num_or_none(row.get('score')),
            'confidence': int(clamp(row.get('score', 0), 0, 100)),
            'latestPrice': num_or_none(row.get('last_close')),
            'latestPct': num_or_none(latest_pct),
            'support': num_or_none(row.get('buy_low')),
            'resistance': num_or_none(row.get('target_1')),
            'rsi': num_or_none(row.get('rsi'), 1),
            'signals': signal_list,
            'signalText': ' / '.join(signal_list[:5]),
            'reason': row.get('reason', ''),
            'advice': row.get('advice', ''),
            'entryHint': row.get('buy_zone', ''),
            'planType': row.get('plan_type', ''),
            'buyZone': row.get('buy_zone', ''),
            'stopLoss': num_or_none(row.get('stop_loss')),
            'targetPrice': num_or_none(row.get('target_1')),
            'rrRatio': num_or_none(row.get('rr_ratio')),
            'planNote': row.get('plan_note', ''),
            'riskLevel': risk_level,
            'riskFlags': [row.get('risk_note', '')] if row.get('risk_note') else [],
            'riskText': row.get('risk_note', ''),
        })

    strong_count = int(df['sector_strong'].fillna(False).sum()) if 'sector_strong' in df.columns else 0
    breakout_count = int(df['breakout'].fillna(False).sum()) if 'breakout' in df.columns else 0
    market_state = '偏强' if market_bonus >= 10 else '震荡偏强' if market_bonus >= 5 else '震荡'
    summary = f'当前A股市场{market_state}，共筛选{scanned_count}只股票，{len(df)}只通过严格条件。'
    strategy = f'板块强势命中{strong_count}只，突破形态命中{breakout_count}只，优先关注放量突破与回踩企稳。'

    return {
        'marketSummary': summary,
        'strategyNote': strategy,
        'picks': picks,
        'qualifiedCount': len(df),
        'scannedCount': scanned_count,
    }


def run_screener():
    print('[stock_screen] 开始获取A股股票列表...')
    stock_list = get_stock_list()
    if CONFIG['test_mode']:
        stock_list = stock_list.head(CONFIG['test_stock_limit']).copy()
        print(f"[stock_screen] 测试模式，扫描 {len(stock_list)} 只股票")
    else:
        print(f"[stock_screen] 全市场扫描，共 {len(stock_list)} 只A股股票")

    # ---- 预检: 用 3 只蓝筹股探测数据源可达性 ----
    _preflight_codes = ['600519', '000001', '601318']
    _preflight_ok = 0
    for _pc in _preflight_codes:
        _pf = fetch_kline(_pc, 20)
        if not _pf.empty and len(_pf) >= 10:
            _preflight_ok += 1
    if _preflight_ok == 0:
        print('[stock_screen] ❌ 预检失败: 3只蓝筹股K线全部获取失败，数据源不可达，本次选股中止')
        print('[stock_screen] ❌ 请检查网络/代理设置或稍后重试')
        return pd.DataFrame(), 0, len(stock_list)
    elif _preflight_ok < len(_preflight_codes):
        print(f'[stock_screen] ⚠️ 预检部分通过 ({_preflight_ok}/{len(_preflight_codes)})，继续执行但数据源可能不稳定')
    else:
        print(f'[stock_screen] ✅ 预检通过 ({_preflight_ok}/{len(_preflight_codes)})，数据源可达')

    market_bonus = get_market_bonus()
    sector_strength_df = pd.DataFrame()
    stock_sector_map_df = pd.DataFrame()

    if CONFIG['enable_sector_filter']:
        sector_strength_df = build_sector_strength_table()
        if not sector_strength_df.empty:
            stock_sector_map_df = build_stock_sector_map(sector_strength_df)
            if not stock_sector_map_df.empty:
                stock_list = stock_list.merge(stock_sector_map_df, on='code', how='left')

    sector_score_dict = {}
    sector_rank_dict = {}
    sector_strong_dict = {}

    if not sector_strength_df.empty:
        sector_score_dict = dict(zip(sector_strength_df['sector_name'], sector_strength_df['sector_score']))
        sector_rank_dict = dict(zip(sector_strength_df['sector_name'], sector_strength_df['sector_rank']))
        sector_strong_dict = dict(zip(sector_strength_df['sector_name'], sector_strength_df['sector_strong']))
    sector_names = list(sector_score_dict.keys())

    results = []
    _consecutive_timeouts = 0
    _fetch_ok = 0     # K线获取成功计数
    _fetch_fail = 0   # K线获取失败计数
    _scan_total = len(stock_list)
    for _idx, row in tqdm(stock_list.iterrows(), total=_scan_total, desc='扫描个股K线'):
        code = str(row['code']).zfill(6)
        name = str(row['name'])
        t0 = time.time()
        df = fetch_kline(code, CONFIG['lookback_days'])
        elapsed = time.time() - t0

        # Rate-limit detection: if fetch took >18s, it likely timed out
        if df.empty and elapsed > 18:
            _consecutive_timeouts += 1
            _fetch_fail += 1
            if _consecutive_timeouts >= 5:
                print(f'[stock_screen] 连续{_consecutive_timeouts}次超时，暂停60秒后继续...')
                time.sleep(60)
                _consecutive_timeouts = 0
            continue
        else:
            _consecutive_timeouts = 0

        if df.empty or len(df) < CONFIG['min_history_days']:
            _fetch_fail += 1
            continue

        _fetch_ok += 1

        # ---- 早停: 前 100 只失败率 > 90% 则中止 ----
        _scanned_so_far = _fetch_ok + _fetch_fail
        if _scanned_so_far == 100 and _fetch_ok < 10:
            print(f'[stock_screen] ❌ 早停: 前100只中仅{_fetch_ok}只获取成功(失败率{_fetch_fail}%)，数据源严重异常，中止扫描')
            break

        df = compute_indicators(df)
        if df.empty:
            continue

        sector_name = str(row.get('sector_name', '') or '').strip()
        sector_score = sector_score_dict.get(sector_name, np.nan)
        sector_rank = sector_rank_dict.get(sector_name, np.nan)
        sector_strong = sector_strong_dict.get(sector_name, False)
        sector_bonus = get_sector_bonus(sector_score, sector_strong)
        scored = score_stock(df, market_bonus=market_bonus, sector_bonus=sector_bonus)

        if not sector_name and sector_names and scored['score'] >= max(CONFIG['score_threshold'] - 8, 58):
            fallback_sector_name = fetch_stock_industry_fallback(code, sector_names)
            if fallback_sector_name:
                sector_name = fallback_sector_name
                sector_score = sector_score_dict.get(sector_name, np.nan)
                sector_rank = sector_rank_dict.get(sector_name, np.nan)
                sector_strong = sector_strong_dict.get(sector_name, False)
                sector_bonus = get_sector_bonus(sector_score, sector_strong)
                scored = score_stock(df, market_bonus=market_bonus, sector_bonus=sector_bonus)

        levels = extract_trade_levels(df)
        trade_plan = build_trade_plan(scored, levels)
        item = {
            'code': code,
            'name': name,
            'sector_name': sector_name,
            'sector_score': sector_score,
            'sector_rank': sector_rank,
            'sector_strong': sector_strong,
            'last_close': safe_round(df['close'].iloc[-1]),
            'rsi': safe_round(df['rsi'].iloc[-1]) if 'rsi' in df.columns else np.nan,
            **scored,
            **trade_plan,
        }
        results.append(item)
        time.sleep(CONFIG['sleep_seconds'])

    if not results:
        _success_rate = round(_fetch_ok / max(_fetch_ok + _fetch_fail, 1) * 100, 1)
        print(f'[stock_screen] 📊 诊断: 无任何打分结果（K线成功{_fetch_ok}只/失败{_fetch_fail}只，成功率{_success_rate}%）')
        if _success_rate < 50:
            print(f'[stock_screen] ❌ 数据源健康异常！成功率仅{_success_rate}%，请检查网络/代理/API限流')
        return pd.DataFrame(), market_bonus, len(stock_list)

    _success_rate = round(_fetch_ok / max(_fetch_ok + _fetch_fail, 1) * 100, 1)
    result_df = pd.DataFrame(results)
    print(f'[stock_screen] 📊 诊断: 打分完成 {len(result_df)} 只, market_bonus={market_bonus} (K线成功{_fetch_ok}/失败{_fetch_fail}, 成功率{_success_rate}%)')
    if _success_rate < 80:
        print(f'[stock_screen] ⚠️ 数据源成功率偏低({_success_rate}%)，选股结果可能不完整')
    if not result_df.empty:
        top_scores = result_df.nlargest(10, 'score')[['code', 'name', 'score', 'sector_name', 'sector_strong']].to_string(index=False)
        print(f'[stock_screen] 📊 得分 TOP10:\n{top_scores}')

    effective_threshold = CONFIG['score_threshold'] if market_bonus > 0 else max(CONFIG['score_threshold'] - 6, 58)
    print(f'[stock_screen] 📊 分数门槛: {effective_threshold} (score_threshold={CONFIG["score_threshold"]}, market_bonus={market_bonus})')
    result_df = result_df[result_df['score'] >= effective_threshold].copy()
    print(f'[stock_screen] 📊 ① 分数过滤后: {len(result_df)} 只')
    if result_df.empty:
        return result_df, market_bonus, len(stock_list)

    trend_votes = (
        result_df['hh_hl'].astype(int)
        + result_df['ma5_gt_ma20'].astype(int)
        + result_df['ma5_up'].astype(int)
    )
    result_df = result_df[
        (trend_votes >= 2) &
        ((result_df['price_above_ma20'] == True) | (result_df['price_above_ma5'] == True))
    ].copy()
    print(f'[stock_screen] 📊 ② 趋势过滤后: {len(result_df)} 只')
    if result_df.empty:
        return result_df, market_bonus, len(stock_list)

    result_df = result_df[result_df.apply(has_strong_trigger, axis=1)].copy()
    print(f'[stock_screen] 📊 ③ 形态触发过滤后: {len(result_df)} 只')
    if result_df.empty:
        return result_df, market_bonus, len(stock_list)

    result_df = result_df[result_df.apply(has_volume_confirmation, axis=1)].copy()
    print(f'[stock_screen] 📊 ④ 量能确认过滤后: {len(result_df)} 只')
    if result_df.empty:
        return result_df, market_bonus, len(stock_list)

    result_df = result_df[result_df.apply(is_low_risk_candidate, axis=1)].copy()
    print(f'[stock_screen] 📊 ⑤ 低风险过滤后: {len(result_df)} 只')
    if result_df.empty:
        return result_df, market_bonus, len(stock_list)

    if CONFIG['enable_sector_filter']:
        sector_known = result_df['sector_name'].fillna('').astype(str).str.strip() != ''
        result_df = result_df[
            (~sector_known) |
            (result_df['sector_strong'] == True) |
            (result_df['sector_score'] >= CONFIG['sector_score_min']) |
            (result_df['sector_rank'] <= CONFIG['sector_top_n'])
        ].copy()
        print(f'[stock_screen] 📊 ⑥ 板块过滤后: {len(result_df)} 只')
        if result_df.empty:
            return result_df, market_bonus, len(stock_list)

    result_df['reason'] = result_df.apply(build_reason, axis=1)
    result_df['risk_note'] = result_df.apply(build_risk_note, axis=1)
    result_df['advice'] = result_df.apply(build_advice, axis=1)
    result_df['buy_zone'] = result_df.apply(format_buy_zone, axis=1)
    result_df['priority'] = (
        result_df['score'] * 10
        + result_df['sector_strong'].astype(int) * 10
        + result_df['breakout'].astype(int) * 8
        + result_df['vol_surge'].astype(int) * 5
        + result_df['macd_cross'].astype(int) * 3
        + result_df['rsi_rebound'].astype(int) * 2
        + result_df['support_stable'].astype(int) * 2
    )
    result_df = result_df.sort_values(
        by=['priority', 'sector_score', 'score', 'pattern_score'],
        ascending=False,
    ).reset_index(drop=True)
    result_df = result_df.head(CONFIG['top_n']).copy()
    return result_df, market_bonus, len(stock_list)


def run_stock_screen():
    print(f"[stock_screen] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 开始执行A股形态筛选...")
    result_df, market_bonus, scanned_count = run_screener()
    print_results(result_df)
    save_results(result_df)
    save_top_charts(result_df)

    result = dataframe_to_payload(result_df, market_bonus, scanned_count)
    payload = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'timestamp': datetime.now().isoformat(),
        'triggerTime': 'daily',
        'result': result,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCREEN_FILE, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(f"[stock_screen] ✅ 完成，命中 {result.get('qualifiedCount', 0)} 只，已输出前 {len(result.get('picks', []))} 只")
    return payload


def load_stock_screen_cache():
    if not os.path.exists(SCREEN_FILE):
        return None
    try:
        with open(SCREEN_FILE, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except Exception:
        return None


if __name__ == '__main__':
    start = time.time()
    run_stock_screen()
    elapsed = round(time.time() - start, 2)
    print(f'\n运行完成，总耗时: {elapsed} 秒')
