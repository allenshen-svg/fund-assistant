#!/usr/bin/env python3
"""
Fund-Assistant 健康探针 — 定期检查服务端各管线状态并告警

用法:
  # 单次检查
  python scripts/health_probe.py

  # 持续监控 (每 60 秒)
  python scripts/health_probe.py --loop 60

  # 指定服务器地址
  python scripts/health_probe.py --server http://your-server:8000

告警方式 (通过环境变量配置):
  HEALTH_WEBHOOK_URL  — 企业微信/钉钉/飞书 webhook，收到 JSON POST
  HEALTH_LOG_FILE     — 追加写入日志文件 (默认 stdout)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError

# ── 配置 ──────────────────────────────────────────
DEFAULT_SERVER = os.environ.get('HEALTH_SERVER', 'http://127.0.0.1:8000')
WEBHOOK_URL = os.environ.get('HEALTH_WEBHOOK_URL', '')
LOG_FILE = os.environ.get('HEALTH_LOG_FILE', '')


def fetch_status(server: str, timeout: int = 10) -> dict:
    url = f'{server.rstrip("/")}/api/status'
    req = Request(url, headers={'User-Agent': 'fund-health-probe/1.0'})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def check_health(status: dict) -> list[dict]:
    """返回所有异常项列表，每项 {level, pipeline, message}"""
    issues = []

    # 1. 管线过期检测
    pipelines = status.get('pipelines', {})
    for name, info in pipelines.items():
        if info.get('stale'):
            age = info.get('age_seconds')
            age_min = round(age / 60, 1) if age else '?'
            issues.append({
                'level': 'error' if (age and age > 3600) else 'warn',
                'pipeline': name,
                'message': f'{name} 数据过期 (已 {age_min} 分钟未更新)',
            })
        if info.get('last_updated') is None:
            issues.append({
                'level': 'error',
                'pipeline': name,
                'message': f'{name} 从未产出数据',
            })

    # 2. 线程存活检测
    threads = status.get('threads', {})
    for tname, alive in threads.items():
        if not alive:
            issues.append({
                'level': 'error',
                'pipeline': tname,
                'message': f'后台线程 {tname} 已停止',
            })

    # 3. 正在采集中超时 (连续 collecting 可能卡住)
    if status.get('collecting'):
        issues.append({
            'level': 'info',
            'pipeline': 'collector',
            'message': '正在采集中...',
        })

    return issues


def send_webhook(issues: list[dict], server: str):
    if not WEBHOOK_URL:
        return
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = [f'⏰ {now}  服务: {server}']
    for iss in issues:
        icon = '🔴' if iss['level'] == 'error' else '🟡' if iss['level'] == 'warn' else 'ℹ️'
        lines.append(f'  {icon} [{iss["pipeline"]}] {iss["message"]}')
    text = '\n'.join(lines)

    payload = json.dumps({
        'msgtype': 'text',
        'text': {'content': text},
    }).encode('utf-8')
    req = Request(
        WEBHOOK_URL,
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        print(f'[health_probe] webhook 发送失败: {e}', file=sys.stderr)


def log_output(msg: str):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] {msg}'
    print(line)
    if LOG_FILE:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


def run_once(server: str):
    try:
        status = fetch_status(server)
    except (URLError, OSError, ValueError) as e:
        log_output(f'🔴 无法连接 {server}: {e}')
        if WEBHOOK_URL:
            send_webhook([{
                'level': 'error',
                'pipeline': 'server',
                'message': f'服务不可达: {e}',
            }], server)
        return False

    issues = check_health(status)
    errors = [i for i in issues if i['level'] == 'error']
    warns = [i for i in issues if i['level'] == 'warn']

    if not errors and not warns:
        trading = '交易时段' if status.get('is_trading_hours') else '非交易时段'
        pipelines = status.get('pipelines', {})
        summary_parts = []
        for name, info in pipelines.items():
            age = info.get('age_seconds')
            age_str = f'{round(age/60, 1)}m' if age else 'N/A'
            summary_parts.append(f'{name}={age_str}')
        log_output(f'✅ 健康 [{trading}] {", ".join(summary_parts)}')
        return True

    for iss in issues:
        icon = '🔴' if iss['level'] == 'error' else '🟡' if iss['level'] == 'warn' else 'ℹ️'
        log_output(f'{icon} [{iss["pipeline"]}] {iss["message"]}')

    if errors or warns:
        send_webhook(errors + warns, server)

    return len(errors) == 0


def main():
    parser = argparse.ArgumentParser(description='Fund-Assistant 健康探针')
    parser.add_argument('--server', default=DEFAULT_SERVER, help='服务器地址')
    parser.add_argument('--loop', type=int, default=0, help='循环间隔秒数 (0=单次)')
    args = parser.parse_args()

    if args.loop > 0:
        log_output(f'🏥 健康探针启动, 目标={args.server}, 间隔={args.loop}s')
        while True:
            run_once(args.server)
            time.sleep(args.loop)
    else:
        ok = run_once(args.server)
        sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
