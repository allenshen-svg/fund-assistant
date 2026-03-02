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

# Update code
run_cmd('cd /opt/fund-assistant && git stash 2>/dev/null; git pull origin main 2>&1', timeout=30)
run_cmd('cd /opt/fund-assistant && git log --oneline -1', timeout=5)

# Restart
run_cmd('kill -9 $(pgrep -f gunicorn) 2>/dev/null; sleep 2; systemctl start fund-assistant && echo started', timeout=15)
time.sleep(5)
run_cmd('systemctl is-active fund-assistant', timeout=5)

# Wait for initial run
for i in range(90):
    result = run_cmd('curl -s http://localhost:8080/api/status 2>/dev/null', timeout=10)
    if '"collecting":false' in result:
        print('Ready!')
        break
    if i > 0:
        print(f'Waiting... ({i+1}/90)')
    time.sleep(5)

# Trigger refresh
run_cmd('curl -s -X POST http://localhost:8080/api/refresh 2>&1', timeout=10)
time.sleep(2)

# Wait for refresh
for i in range(90):
    result = run_cmd('curl -s http://localhost:8080/api/status 2>/dev/null', timeout=10)
    if '"collecting":false' in result:
        print('Refresh done!')
        break
    if i > 0:
        print(f'Refreshing... ({i+1}/90)')
    time.sleep(5)

# Check events - energy sector assignments
run_cmd('''curl -s http://localhost:8080/api/hot-events | python3 -c "
import sys,json
d=json.load(sys.stdin)
events = d.get('events', [])
print('=== Energy-related events ===')
for e in events:
    sp = e.get('sectors_positive', [])
    sn = e.get('sectors_negative', [])
    all_text = str(sp+sn+e.get('concepts',[]))
    for k in ['能源','油','原油','石油','军工','黄金']:
        if k in all_text:
            t = e.get('title','?')
            print(t)
            print('  +:', sp)
            print('  -:', sn)
            print()
            break
print('=== Heatmap top 10 ===')
hm = d.get('heatmap', [])
for h in hm[:10]:
    print(h['tag'].ljust(8), 'trend='+h['trend'].ljust(6), 'pct='+str(h.get('real_pct','N/A')).rjust(7))
" 2>&1''', timeout=15)

ssh.close()
print('Deploy done')
