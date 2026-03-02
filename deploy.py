import paramiko, time

host = '47.83.165.131'
user = 'root'
pwd = 'Allen1989716!'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(host, username=user, password=pwd, timeout=10)

def run_cmd(cmd, timeout=30):
    print(f'>>> {cmd}')
    chan = ssh.get_transport().open_session()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    out = b''
    try:
        while True:
            chunk = chan.recv(4096)
            if not chunk:
                break
            out += chunk
    except Exception:
        pass
    exit_code = chan.recv_exit_status()
    result = out.decode(errors='replace').strip()
    if result:
        print(result)
    print(f'[exit: {exit_code}]')
    return result

# Create test script on server
test_script = '''
import json
from urllib.request import Request, urlopen
import ssl
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
codes = '1.515070,1.562500,0.159857,0.159840,1.515030,1.513180,1.515080,1.511260'
url = f'https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&fields=f2,f3,f4,f12,f14&secids={codes}'
req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
resp = urlopen(req, timeout=10, context=ctx)
data = json.loads(resp.read().decode())
items = (data.get('data') or {}).get('diff') or []
for i in items:
    print(f"{i.get('f12','?')}: {i.get('f14','?')} pct={i.get('f3','N/A')}")
print(f'Total: {len(items)}/8')
'''
# Write and run on server
run_cmd(f"cat > /tmp/test_etf.py << 'PYEOF'\n{test_script}\nPYEOF", timeout=5)
# Force update server code
run_cmd('cd /opt/fund-assistant && git stash 2>/dev/null; git pull origin main 2>&1', timeout=30)
run_cmd('cd /opt/fund-assistant && git log --oneline -1', timeout=5)
run_cmd('kill -9 $(pgrep -f gunicorn) 2>/dev/null; sleep 2; systemctl start fund-assistant && echo started', timeout=15)
time.sleep(5)
run_cmd('systemctl is-active fund-assistant', timeout=5)

# Wait for the initial scheduler run
import time as _t
for i in range(90):
    result = run_cmd('curl -s http://localhost:8080/api/status 2>/dev/null', timeout=10)
    if '"collecting":false' in result:
        print('Initial run complete!')
        break
    if i > 0:
        print(f'Waiting... ({i+1}/90)')
    _t.sleep(5)

# Trigger fresh refresh
run_cmd('curl -s -X POST http://localhost:8080/api/refresh 2>&1', timeout=10)
_t.sleep(2)

# Wait for refresh
for i in range(90):
    result = run_cmd('curl -s http://localhost:8080/api/status 2>/dev/null', timeout=10)
    if '"collecting":false' in result:
        print('Refresh done!')
        break
    if i > 0:
        print(f'Refreshing... ({i+1}/90)')
    _t.sleep(5)

# Check heatmap - all 20 tags
run_cmd("curl -s http://localhost:8080/api/hot-events | python3 -c \"import sys,json; d=json.load(sys.stdin); hm=d.get('heatmap',[]); total=len(hm); has_pct=sum(1 for h in hm if h.get('real_pct') is not None); print(f'Total: {total} items, {has_pct}/{total} have real_pct'); [print(f\\\"{h['tag']:8s} trend={h['trend']:6s} pct={str(h.get('real_pct','N/A')):>7} temp={h['temperature']}\\\") for h in hm]\" 2>&1", timeout=15)
ssh.close()
print('Deploy done')
