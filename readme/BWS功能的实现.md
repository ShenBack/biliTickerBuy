## BWS 活动预约功能总结
rush_bws 方法（api.py:773-1035）实现了 B 站 BWS 线下活动的预约抢票，完整流程如下：

### 阶段一：实名校验（only_lysk 模式）
当 only_lysk=True 时，先调用 GET /x/member/realname/apply/status 校验：

- 实名认证状态（status=1 通过）
- 证件类型（card_type=0 身份证）
- 身份证第 17 位判断性别（奇数=男通过，偶数=女拒绝）
### 阶段二：门票绑定检查
调用 GET /x/activity/bws/online/park/reserve/info?csrf=xxx&reserve_date=20250711,20250712,20250713&reserve_type=-1

若返回 code=75638 （未绑定门票），进入绑定流程：

1. 选择证件类型（身份证/护照/港澳/台湾）
2. 输入姓名、证件号码、票号后四位
3. 调用 POST /x/activity/bws/online/park/ticket/bind 提交绑定
4. 绑定成功后 递归调用 rush_bws() 继续预约
绑定错误码处理：75635（服务器异常）、75636（身份校验不通过）、75639（证件已绑定其他账号）、75642（账号已绑定）、75643（未查询到购票信息）、76645（邀请函用户不支持）

### 阶段三：选择预约项目
从 reserve/info 返回的 reserve_list[date] 中：

1. 用户选择预约日期
2. 展示票号、票种信息
3. 按 reserve_begin_time 升序排列，state 3/4/5（已结束/已预约/售完）放最后
4. 若 only_lysk=True ，过滤只保留"恋与深空"相关
5. 用户选择目标预约项
6. 若有 next_reserve （下一场次），询问是否使用
### 阶段四：定时抢预约
1. 用户输入请求延迟（默认 900ms）
2. 构造请求体： {csrf, inter_reserve_id, ticket_no}
3. 等待循环 ：
   - 距开票 > 5s 时，每 2s 打印剩余时间
   - 距开票 ≤ 5s 时，HEAD 请求 show.bilibili.com 预热连接
   - busy-wait 直到 reserve_begin_time + delay 到达
4. 调用 POST /x/activity/bws/online/park/reserve/do 提交预约
5. 成功（code=0）返回 True
6. 失败处理：
   - 412（风控）、429（限流）、-702（速率过高）、75637（未开放）、76650（操作频繁）→ sleep(delay) 后重试
   - 75574（场次抢空）、76647（预约数达上限）→ 直接返回 False
### 涉及的 B 站 API（共 4 个）
API 方法 用途 /x/member/realname/apply/status GET 实名认证状态 /x/activity/bws/online/park/reserve/info GET 查询预约信息和门票绑定状态 /x/activity/bws/online/park/ticket/bind POST 绑定门票 /x/activity/bws/online/park/reserve/do POST 执行预约

### 当前实现特点
- 命令行交互式（questionary），无 GUI
- 日期硬编码 20250711,20250712,20250713
- bid 硬编码 202501
- 有 only_lysk 模式过滤恋与深空
- 无代理支持、无多账号、无并发
以上是完整总结，确认后我开始移植。