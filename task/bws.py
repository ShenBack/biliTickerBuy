import json
import time
from datetime import datetime
from time import sleep
from loguru import logger

BWS_RESERVE_SUCCESS = "成功"
BWS_RESERVE_UNKNOWN_STATUS = "异常"
BWS_RESERVE_FAIL = "失败"

BWS_RESERVE_CODE = {
    0: BWS_RESERVE_SUCCESS,
    75574: "当前场次已被抢空，请提前准备下场！",
    75637: "该场次还未开放预约",
    75638: "请先绑定门票",
    76645: "抱歉，邀请函用户不支持预约",
    76647: "您当前预约数已达上限！",
    76650: "当前预约人数较多，请稍后再试",
}

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
    """BWS 请求日志收集器"""

    def __init__(self):
        self._entries: list[str] = []

    def add(self, method: str, url: str, body: dict = None, resp_code: int = None, resp_data: dict = None, note: str = ""):
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
        return "\n\n".join(self._entries)

    def clear(self):
        self._entries.clear()


def _get_csrf(request) -> str:
    """从 cookies 中获取 bili_jct (CSRF token)"""
    for cookie in request.cookieManager.get_cookies(force=True) or []:
        name = cookie.get("name", "")
        value = cookie.get("value", "")
        if name == "bili_jct":
            return value
    return ""


def get_reserve_info(request, reserve_dates: str, log: BwsRequestLog = None) -> dict:
    """查询预约信息和门票绑定状态
    Args:
        reserve_dates: 逗号分隔的日期字符串，如 "20250711,20250712"
    Returns:
        {"success": bool, "code": int, "data": dict, "message": str}
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
    """绑定门票
    Args:
        bind_info: {ticket_no, card_type, card_no, name}
    Returns:
        {"success": bool, "code": int, "message": str}
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
    """执行预约
    Returns:
        (result_status, message)
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
    """BWS 预约主流程（供 UI 调用）
    Args:
        request: BiliRequest 实例
        reserve_dates: 逗号分隔日期，如 "20250711,20250712"
        target_reserve_id: 目标预约项 ID（0 表示需要查询后选择）
        target_ticket_no: 目标票号
        delay: 请求延迟（秒）
        progress_callback: 进度回调 fn(message: str)
        log: 请求日志收集器
    Returns:
        (success, message)
    """
    def _log(msg):
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
