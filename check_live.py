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

# Check current heatmap data with all fields
run_cmd('''curl -s http://localhost:8080/api/hot-events | python3 -c "
import sys,json
d=json.load(sys.stdin)
hm=d.get('heatmap',[])
ua = d.get('updated_at','?')
print('Updated:', ua)
print('Total:', len(hm), 'items')
for h in hm:
    tag = h['tag']
    trend = h['trend']
    pct = h.get('real_pct', 'N/A')
    temp = h['temperature']
    print(tag.ljust(8), 'trend='+trend.ljust(6), 'pct='+str(pct).rjust(7), 'temp='+str(temp))

events = d.get('events', [])
print()
print('=== Events with energy/oil sectors ===')
for e in events:
    sp = e.get('sectors_positive', [])
    sn = e.get('sectors_negative', [])
    concepts = e.get('concepts', [])
    all_text = str(sp+sn+concepts)
    for k in ['能源','油','原油','石油']:
        if k in all_text:
            print('Title:', e.get('title','?'))
            print('  positive:', sp)
            print('  negative:', sn)
            print('  concepts:', concepts)
            print('  impact:', e.get('impact',0), 'sent:', e.get('sentiment',0))
            print()
            break
" 2>&1'''
, timeout=15)

ssh.close()
print('Done')
