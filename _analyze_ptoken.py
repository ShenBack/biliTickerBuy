import re, base64

log_path = r'D:\bhyg-my\biliTickerBuy2.15.4\biliTickerBuy-main\btb_logs\这厢无理-上海_BilibiliWorld_2026-2026-07-11_周六_-_游园票_-__128_-_售罄_-__起售时间_2026-06-20_180000_-吴天羽-袁瑾仪-徐秋枫_387a96c1.log'
with open(log_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

states = []
for line in lines:
    if 'base_timer' in line and 'INFO' in line and 'm1' in line:
        fields = {}
        for key in ['base_timer', 'm1', 'touchend', 'm2', 'visibilitychange', 'm3', 'm4', 'openWindow', 'm5', 'timer', 'm6', 'm7', 'm8', 'm9', 'beforeunload']:
            m = re.search(rf"'{key}':\s*(\d+)", line)
            if m:
                fields[key] = int(m.group(1))
        td = re.search(r"'timediff':\s*([\d.]+)", line)
        if td:
            fields['timediff'] = float(td.group(1))
        tct = re.search(r"'ticket_collection_t':\s*(\d+)", line)
        if tct:
            fields['ticket_collection_t'] = int(tct.group(1))
        if fields:
            states.append(fields)
    if '[prepare] 请求' in line:
        m = re.search(r'ctoken=([A-Za-z0-9+/=]+)', line)
        if m and states:
            states[-1]['ctoken'] = m.group(1)
    if '[prepare] 响应' in line and 'ptoken' in line:
        m = re.search(r"'ptoken':\s*'([A-Za-z0-9+/=]+)'", line)
        if m and states:
            states[-1]['ptoken'] = m.group(1)

print(f'Found {len(states)} prepare pairs\n')

# Collect unique tail patterns
tails_seen = set()
for i, s in enumerate(states):
    pt = s.get('ptoken')
    ct = s.get('ctoken')
    if not pt or not ct:
        continue
    pt_raw = list(base64.b64decode(pt))
    ct_raw = list(base64.b64decode(pt))
    tail = tuple(pt_raw[24:28])
    sid = pt_raw[29]
    tail_key = (tail, sid)
    if tail_key not in tails_seen:
        tails_seen.add(tail_key)
        print(f'=== New tail pattern: {tail}, session_id={sid} ===')
        print(f'  ctoken m1={s.get("m1")} m2={s.get("m2")} m3={s.get("m3")} m4={s.get("m4")}')
        print(f'  ctoken m5={s.get("m5")} m6={s.get("m6")} m7={s.get("m7")} m8={s.get("m8")} m9={s.get("m9")}')
        print(f'  ctoken openWindow={s.get("openWindow")} beforeunload={s.get("beforeunload")}')
        print(f'  base_timer={s.get("base_timer")} timer={s.get("timer")}')
        print(f'  ticket_collection_t={s.get("ticket_collection_t")}')
        ct_decoded = list(base64.b64decode(s['ctoken']))
        print(f'  ctoken raw: {ct_decoded}')
        print()

# Also dump first 5 pairs in detail
print('\n=== First 5 pairs ===')
for i, s in enumerate(states[:5]):
    pt = s.get('ptoken')
    ct = s.get('ctoken')
    if not pt or not ct:
        continue
    pt_raw = list(base64.b64decode(pt))
    ct_raw = list(base64.b64decode(ct))
    print(f'Pair {i}:')
    print(f'  ctoken: {ct_raw}')
    print(f'  ptoken: {pt_raw}')
    print(f'  p[11]={pt_raw[11]} p[13]={pt_raw[13]} p[19]={pt_raw[19]} p[24:28]={pt_raw[24:28]} p[29]={pt_raw[29]} p[31]={pt_raw[31]}')
    print()
