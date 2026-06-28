"""
文件说明：
- 文件整体功能：读取指定日志文件，从中提取 prepare 阶段的浏览器状态字段、ctoken、ptoken，
  分析不同 ptoken 尾字节模式与对应状态之间的关系，并打印前 5 组 ctoken/ptoken 的原始字节明细。
- 所属模块：项目根目录下的分析脚本，用于逆向分析 ptoken 生成规律，不直接参与业务运行。
- 依赖文件：依赖本地日志文件 btb_logs/...log；依赖 Python 标准库 re、base64。
- 对外能力：作为一次性分析脚本运行，输出尾字节模式统计和详细字节对比到控制台。
"""

import re, base64

# 指定要分析的日志文件路径，文件名为一次具体的购票准备日志
log_path = r'D:\bhyg-my\biliTickerBuy2.15.4\biliTickerBuy-main\btb_logs\这厢无理-上海_BilibiliWorld_2026-2026-07-11_周六_-_游园票_-__128_-_售罄_-__起售时间_2026-06-20_180000_-吴天羽-袁瑾仪-徐秋枫_387a96c1.log'

# 打开日志文件并按行读取
with open(log_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# states 列表用于存储每一次 prepare 请求前后解析出的状态字段、ctoken、ptoken
states = []

# 逐行解析日志，收集状态字段、ctoken 和 ptoken
for line in lines:
    # 匹配包含 base_timer、INFO 和 m1 的行，这些行记录了完整的浏览器状态
    if 'base_timer' in line and 'INFO' in line and 'm1' in line:
        fields = {}
        # 依次提取已知的浏览器状态整数字段
        for key in ['base_timer', 'm1', 'touchend', 'm2', 'visibilitychange', 'm3', 'm4', 'openWindow', 'm5', 'timer', 'm6', 'm7', 'm8', 'm9', 'beforeunload']:
            m = re.search(rf"'{key}':\s*(\d+)", line)
            if m:
                fields[key] = int(m.group(1))
        # 提取 timediff 浮点字段
        td = re.search(r"'timediff':\s*([\d.]+)", line)
        if td:
            fields['timediff'] = float(td.group(1))
        # 提取 ticket_collection_t 整数字段
        tct = re.search(r"'ticket_collection_t':\s*(\d+)", line)
        if tct:
            fields['ticket_collection_t'] = int(tct.group(1))
        # 只要提取到任意字段，就认为是一次有效状态记录
        if fields:
            states.append(fields)
    # 匹配 prepare 请求行，提取 URL 中的 ctoken 参数，关联到最近一次状态记录
    if '[prepare] 请求' in line:
        m = re.search(r'ctoken=([A-Za-z0-9+/=]+)', line)
        if m and states:
            states[-1]['ctoken'] = m.group(1)
    # 匹配 prepare 响应行，提取响应中的 ptoken，关联到最近一次状态记录
    if '[prepare] 响应' in line and 'ptoken' in line:
        m = re.search(r"'ptoken':\s*'([A-Za-z0-9+/=]+)'", line)
        if m and states:
            states[-1]['ptoken'] = m.group(1)

# 输出成功解析的 prepare 状态对数量
print(f'Found {len(states)} prepare pairs\n')

# 收集并打印不同的 ptoken 尾字节模式，以及对应的状态字段
# 这里将 ptoken 的第 24-27 字节作为 tail，第 29 字节作为 session_id
# 注意：代码中 ct_raw 被计算但未使用，实际使用的是 pt_raw
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
    # 只打印首次出现的新模式
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

# 额外输出前 5 组 ctoken/ptoken 的完整字节明细，用于逐字节对比
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
