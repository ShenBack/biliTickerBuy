"""
文件说明：
- 文件整体功能：封装 Bilibili World / BWS 线上园区预约相关的 HTTP 请求与业务编排逻辑，
  包括查询预约信息、绑定门票、执行预约以及带重试与等待开票时间的主流程调度。
- 所属模块：task 业务任务层，供上层 UI/调度器调用以完成 BWS 票务预约。
- 依赖文件：依赖项目中的 HTTP 请求对象（request，需具备 cookieManager、get、post 能力），
  以及 loguru 日志库。
- 对外能力：提供 reserve_bws 主流程入口、get_reserve_info 查询接口、bind_ticket 绑定接口、
  do_reserve 单次预约接口，以及 BwsRequestLog 请求日志收集器。
"""

import json
import time
from datetime import datetime
from time import sleep
from loguru import logger

# 预约结果状态常量
BWS_RESERVE_SUCCESS = "成功"
BWS_RESERVE_UNKNOWN_STATUS = "异常"
BWS_RESERVE_FAIL = "失败"

# BWS 预约接口返回码与可读提示的映射表
BWS_RESERVE_CODE = {
    0: BWS_RESERVE_SUCCESS,
    75574: "当前场次已被抢空，请提前准备下场！",
    75637: "该场次还未开放预约",
    75638: "请先绑定门票",
    76645: "抱歉，邀请函用户不支持预约",
    76647: "您当前预约数已达上限！",
    76650: "当前预约人数较多，请稍后再试",
}

# 门票绑定接口返回码与可读提示的映射表
TICKET_BIND_CODE = {
    75635: "服务器异常，请稍后再试",
    75636: "身份校验不通过",
    75639: "证件已绑定其他账号",
    75640: "身份证件类型不支持",
    75641: "身份证件号码格式错误",
    75642: "当前账号已绑定门票",
    75643: "未查询到购票信息",
    75644: "请输入正确的票号",
}


class BwsRequestLog:
    """
    BWS 请求日志收集器。

    类设计作用：在预约流程中集中记录每一次 HTTP 请求的 method、url、请求体、响应码、
    响应数据与备注，便于流程结束后向用户展示完整请求链路，辅助问题排查。
    存储属性：
        _entries (list[str])：按时间顺序保存的日志条目字符串列表。
    整体承担业务：为 BWS 预约主流程提供请求-响应明细的收集、读取与清空能力。
    """

    def __init__(self):
        """
        初始化日志收集器。

        核心作用：创建空的日志条目列表。
        输入参数：无。
        返回值：无。
        内部关键执行逻辑：将 _entries 初始化为空列表。
        调用位置：由 reserve_bws 等需要记录请求日志的上层调用方实例化。
        """
        self._entries: list[str] = []

    def add(self, method: str, url: str, body: dict = None, resp_code: int = None, resp_data: dict = None, note: str = ""):
        """
        添加一条请求日志。

        核心作用：将一次 HTTP 请求的关键信息格式化为带时间戳的文本并保存。
        输入参数：
            method (str)：HTTP 方法，例如 "GET" 或 "POST"。
            url (str)：请求的完整 URL。
            body (dict, 可选)：请求体字典，仅 POST 等请求有值，会截断超过 200 字符的内容。
            resp_code (int, 可选)：响应码，例如 B站接口的 code 字段。
            resp_data (dict, 可选)：响应数据字典，会截断超过 300 字符的内容。
            note (str, 可选)：额外备注信息。
        返回值：无。
        内部关键执行逻辑：
            1. 生成当前时间戳；
            2. 拼接 method、url；
            3. 按存在性依次追加请求体、响应码、响应数据、备注；
            4. 对过长的请求体与响应数据进行截断；
            5. 将完整日志行追加到 _entries。
        调用位置：由 get_reserve_info、bind_ticket、do_reserve 等网络请求函数调用。
        """
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {method} {url}"
        if body:
            body_str = json.dumps(body, ensure_ascii=False)
            if len(body_str) > 200:
                body_str = body_str[:200] + "..."
            line += f"\n  请求: {body_str}"
        if resp_code is not None:
            line += f"\n  响应: code={resp_code}"
        if resp_data:
            data_str = json.dumps(resp_data, ensure_ascii=False)
            if len(data_str) > 300:
                data_str = data_str[:300] + "..."
            line += f"\n  数据: {data_str}"
        if note:
            line += f"\n  备注: {note}"
        self._entries.append(line)

    def get_text(self) -> str:
        """
        获取所有日志条目的合并文本。

        核心作用：将收集到的所有日志条目以双换行连接，便于展示。
        输入参数：无。
        返回值 (str)：合并后的日志文本，条目之间以两个换行分隔。
        内部关键执行逻辑：使用 "\n\n".join 连接 _entries 中的日志行。
        调用位置：由上层 UI 或调试代码在预约流程结束后调用以展示日志。
        """
        return "\n\n".join(self._entries)

    def clear(self):
        """
        清空已收集的日志条目。

        核心作用：重置日志收集器，便于复用实例开始新一轮记录。
        输入参数：无。
        返回值：无。
        内部关键执行逻辑：调用 _entries.clear() 清空列表。
        调用位置：可由上层在多次预约流程之间调用，避免日志混淆。
        """
        self._entries.clear()


def _get_csrf(request) -> str:
    """
    从 request 的 cookies 中获取 bili_jct（CSRF token）。

    核心作用：为 B站接口请求提取必要的 csrf 参数，用于接口鉴权与表单提交。
    输入参数：
        request：具备 cookieManager.get_cookies(force=True) 返回 cookie 列表的对象，
                每个 cookie 为包含 name、value 字段的字典。
    返回值 (str)：名为 bili_jct 的 cookie 值；未找到时返回空字符串。
    内部关键执行逻辑：
        1. 强制获取当前所有 cookie；
        2. 遍历 cookie 列表，匹配 name 为 "bili_jct" 的项；
        3. 命中则返回对应 value，否则返回空字符串。
    调用位置：由 get_reserve_info、bind_ticket、do_reserve、reserve_bws 等需要 csrf 的函数调用。
    """
    for cookie in request.cookieManager.get_cookies(force=True) or []:
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if name == "bili_jct":
            return value
    return ""


def get_reserve_info(request, reserve_dates: str, log: BwsRequestLog = None) -> dict:
    """
    查询 BWS 预约信息和门票绑定状态。

    核心作用：调用 B站 BWS 线上园区预约信息接口，获取指定日期范围内各场次的预约状态。
    输入参数：
        request：具备 get(url) 方法并返回 json 响应的请求对象。
        reserve_dates (str)：逗号分隔的日期字符串，例如 "20250711,20250712"。
        log (BwsRequestLog, 可选)：请求日志收集器，用于记录本次请求。
    返回值 (dict)：统一结构 {"success": bool, "code": int, "data": dict, "message": str}。
    内部关键执行逻辑：
        1. 从 cookie 获取 csrf；
        2. 拼接请求 URL，包含 csrf、reserve_date、reserve_type 参数；
        3. 发起 GET 请求并解析 JSON；
        4. 若提供日志收集器则记录请求；
        5. 返回包含 success、code、data、message 的字典；异常时返回失败结构。
    调用位置：由 reserve_bws 主流程在正式预约前调用，用于确认可预约场次。
    """
    csrf = _get_csrf(request)
    url = f"https://api.bilibili.com/x/activity/bws/online/park/reserve/info?csrf={csrf}&reserve_date={reserve_dates}&reserve_type=-1"
    try:
        resp = request.get(url)
        res_json = resp.json()
        if log:
            log.add("GET", url, resp_code=res_json.get("code"), resp_data=res_json.get("data"))
        return {
            "success": res_json.get("code") == 0,
            "code": res_json.get("code"),
            "data": res_json.get("data", {}),
            "message": res_json.get("message", ""),
        }
    except Exception as e:
        return {"success": False, "code": -1, "data": {}, "message": str(e)}


def bind_ticket(request, bind_info: dict, log: BwsRequestLog = None) -> dict:
    """
    绑定 BWS 门票。

    核心作用：调用 B站 BWS 线上园区门票绑定接口，将实体票号与观演人证件信息绑定到当前账号。
    输入参数：
        request：具备 post(url, data=body) 方法并返回 json 响应的请求对象。
        bind_info (dict)：绑定所需信息，必须包含以下键：
            ticket_no (str)：票号；
            card_type (int/str)：证件类型；
            card_no (str)：证件号码；
            name (str)：持票人姓名。
        log (BwsRequestLog, 可选)：请求日志收集器。
    返回值 (dict)：统一结构 {"success": bool, "code": int, "message": str}。
    内部关键执行逻辑：
        1. 从 cookie 获取 csrf；
        2. 构造 POST 请求体，包含票号、证件类型、证件号、姓名、csrf；
        3. 发起 POST 请求并解析 JSON；
        4. 使用 TICKET_BIND_CODE 映射可读错误提示；
        5. 若提供日志收集器则记录请求；
        6. 异常时返回失败结构。
    调用位置：目前主要在独立绑定场景中由上层 UI 调用，reserve_bws 主流程主要依赖已绑定门票。
    """
    csrf = _get_csrf(request)
    url = f"https://api.bilibili.com/x/activity/bws/online/park/ticket/bind?csrf={csrf}"
    body = {
        "ticket_no": bind_info["ticket_no"],
        "card_type": str(bind_info["card_type"]),
        "card_no": bind_info["card_no"],
        "name": bind_info["name"],
        "csrf": csrf,
    }
    try:
        resp = request.post(url, data=body)
        res_json = resp.json()
        code = res_json.get("code")
        if log:
            log.add("POST", url, body=body, resp_code=code, resp_data=res_json.get("data"))
        return {
            "success": code == 0,
            "code": code,
            "message": TICKET_BIND_CODE.get(code, res_json.get("message", "未知错误")),
        }
    except Exception as e:
        return {"success": False, "code": -1, "message": str(e)}


def do_reserve(request, inter_reserve_id: int, ticket_no: str, csrf: str = "", log: BwsRequestLog = None) -> tuple[str, str]:
    """
    执行一次 BWS 预约请求。

    核心作用：调用 B站 BWS 线上园区预约提交接口，尝试为指定场次和票号完成预约。
    输入参数：
        request：具备 post(url, data=body) 方法并返回 json 响应的请求对象。
        inter_reserve_id (int)：目标预约项 ID。
        ticket_no (str)：目标票号。
        csrf (str, 可选)：CSRF token，为空时自动从 cookie 获取。
        log (BwsRequestLog, 可选)：请求日志收集器。
    返回值 (tuple[str, str])：
        第一个元素为结果状态，可能取值：
            - "成功"：预约成功；
            - "失败"：明确失败（如已抢空、已达上限）；
            - "retry"：需要重试（如限流、未开放、网络异常）；
            - "异常"：其他未知状态。
        第二个元素为对应提示信息。
    内部关键执行逻辑：
        1. 若 csrf 为空则从 cookie 获取；
        2. 构造 POST 请求体，包含 csrf、inter_reserve_id、ticket_no；
        3. 发起请求并解析响应码；
        4. 根据 BWS_RESERVE_CODE 和特定重试/失败码分类返回结果；
        5. 若提供日志收集器则记录请求；
        6. 异常时返回 "retry" 与异常信息。
    调用位置：由 reserve_bws 主流程在循环预约中反复调用。
    """
    if not csrf:
        csrf = _get_csrf(request)
    url = f"https://api.bilibili.com/x/activity/bws/online/park/reserve/do?csrf={csrf}"
    body = {
        "csrf": csrf,
        "inter_reserve_id": inter_reserve_id,
        "ticket_no": ticket_no,
    }
    try:
        resp = request.post(url, data=body)
        res_json = resp.json()
        code = res_json.get("code")
        if log:
            log.add("POST", url, body=body, resp_code=code, resp_data=res_json.get("data"))
        msg = BWS_RESERVE_CODE.get(code, f"{BWS_RESERVE_UNKNOWN_STATUS} {res_json}")
        if code == 0:
            return BWS_RESERVE_SUCCESS, msg
        elif code in (412, 429, -702, 75637, 76650):
            return "retry", msg
        elif code in (75574, 76647):
            return BWS_RESERVE_FAIL, msg
        else:
            return BWS_RESERVE_UNKNOWN_STATUS, msg
    except Exception as e:
        return "retry", f"请求异常: {e}"


def reserve_bws(request, reserve_dates: str, target_reserve_id: int = 0,
                target_ticket_no: str = "", delay: float = 0.9,
                progress_callback=None, log: BwsRequestLog = None) -> tuple[bool, str]:
    """
    BWS 预约主流程（供 UI 调用）。

    核心作用：编排完整的 BWS 预约流程，包括查询可预约场次、选择目标场次、等待开票时间、
    循环提交预约直至成功或达到最大重试次数。
    输入参数：
        request：BiliRequest 请求对象，需具备 cookieManager、get、post 能力。
        reserve_dates (str)：逗号分隔日期，例如 "20250711,20250712"。
        target_reserve_id (int, 可选)：目标预约项 ID，为 0 时自动选择可预约场次。
        target_ticket_no (str, 可选)：目标票号，为空时使用目标场次自带票号。
        delay (float, 可选)：请求间隔与等待偏移（秒），默认 0.9。
        progress_callback (callable, 可选)：进度回调函数，接收一个 str 参数用于展示进度信息。
        log (BwsRequestLog, 可选)：请求日志收集器。
    返回值 (tuple[bool, str])：
        第一个元素表示是否预约成功；第二个元素为最终结果提示信息。
    内部关键执行逻辑：
        1. 定义内部 _log 函数，统一输出到 logger 与 progress_callback；
        2. 调用 get_reserve_info 查询预约信息，处理未绑定门票等错误；
        3. 从响应中按日期扁平化收集所有预约项；
        4. 若指定 target_reserve_id 则匹配对应项；否则选择 state==1 的可预约项，
           无则选择未结束/未预约/未售完的项；
        5. 校验场次状态（结束、已预约、售完直接返回）；
        6. 若存在 reserve_begin_time 且未到达，则进入等待循环，临近开票时高频轮询；
        7. 开票时间到达后循环调用 do_reserve，根据返回状态决定成功、失败或重试，
           重试最多 max_fail 次，每次间隔 delay 秒。
    调用位置：由上层 UI 或任务调度器在用户触发 BWS 预约时调用。
    """
    def _log(msg):
        """
        输出并回调进度信息。

        核心作用：将预约流程中的进度信息同时写入 loguru 日志和上游回调函数。
        输入参数：
            msg (str)：要输出的进度信息。
        返回值：无。
        内部关键执行逻辑：调用 logger.info 输出带 "[BWS预约]" 前缀的日志，并在 progress_callback 存在时调用它。
        调用位置：由 reserve_bws 内部各阶段代码调用。
        """
        logger.info(f"[BWS预约] {msg}")
        if progress_callback:
            progress_callback(msg)

    # 查询预约信息
    info = get_reserve_info(request, reserve_dates, log=log)
    if info["code"] == 75638:
        return False, "门票未绑定，请先在 B 站 App 中绑定门票"
    if not info["success"]:
        return False, f"查询预约信息失败: {info['message']}"

    reserve_list = info["data"].get("reserve_list", {})
    if not reserve_list:
        return False, "未查询到可预约的场次"

    # 收集所有预约项
    all_reserves = []
    for date_key, items in reserve_list.items():
        if isinstance(items, list):
            for item in items:
                all_reserves.append(item)

    if not all_reserves:
        return False, "无可预约项"

    # 找到目标预约项
    target = None
    if target_reserve_id:
        for item in all_reserves:
            rid = item.get("reserve_id") or item.get("inter_reserve_id")
            if rid == target_reserve_id:
                target = item
                break

    if not target:
        available = [r for r in all_reserves if r.get("state") == 1]
        if not available:
            all_reserves.sort(key=lambda x: x.get("reserve_begin_time", 0))
            available = [r for r in all_reserves if r.get("state") not in (3, 4, 5)]
        if not available:
            return False, "所有场次已结束或已预约"
        target = available[0]

    ticket_no = target_ticket_no or target.get("ticket_no", "")
    inter_reserve_id = target.get("reserve_id") or target.get("inter_reserve_id")
    reserve_name = target.get("act_title") or target.get("reserve_name", "未知")
    begin_time = target.get("reserve_begin_time", 0)
    state = target.get("state")

    _log(f"目标预约: {reserve_name} (ID: {inter_reserve_id})")
    _log(f"票号: {ticket_no}")

    if state == 3:
        return False, "该场次已结束"
    if state == 4:
        return False, "该场次已预约"
    if state == 5:
        return False, "该场次已售完"

    # 等待开票时间
    if begin_time:
        now = time.time()
        begin_dt = datetime.fromtimestamp(begin_time)
        if now < begin_time:
            _log(f"开票时间: {begin_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            _log(f"等待开票...")
            while True:
                now = time.time()
                remaining = begin_time - now
                if remaining <= 0:
                    break
                if remaining > 5:
                    time.sleep(min(2, remaining - 5))
                    logger.info(f"距离开票: {int(remaining)}s")
                else:
                    time.sleep(0.05)
            time.sleep(delay)
            _log("开票时间到，开始预约！")

    # 循环预约
    csrf = _get_csrf(request)
    fail_count = 0
    max_fail = 20

    while fail_count < max_fail:
        result, msg = do_reserve(request, inter_reserve_id, ticket_no, csrf, log=log)
        _log(f"预约结果: {msg}")

        if result == BWS_RESERVE_SUCCESS:
            return True, msg
        elif result == BWS_RESERVE_FAIL:
            return False, msg
        elif result == "retry":
            fail_count += 1
            if fail_count >= max_fail:
                return False, f"连续失败 {max_fail} 次，放弃: {msg}"
            time.sleep(delay)
        else:
            fail_count += 1
            time.sleep(delay)

    return False, "预约失败，超过最大重试次数"
