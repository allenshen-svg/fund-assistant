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
run_cmd('cd /opt/fund-assistant && git stash && git pull origin main 2>&1', timeout=30)
run_cmd('cd /opt/fund-assistant && git log --oneline -3', timeout=5)
run_cmd("grep -c '515070' /opt/fund-assistant/scripts/fetch_events.py", timeout=5)
# Restart service
run_cmd('kill -9 $(pgrep -f gunicorn) 2>/dev/null; sleep 1; echo killed', timeout=10)
run_cmd('systemctl start fund-assistant && echo started', timeout=15)
time.sleep(4)
run_cmd('systemctl is-active fund-assistant', timeout=5)
ssh.close()
print('Deploy done')
