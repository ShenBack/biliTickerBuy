"""
task/buy_helpers.py — 抢票核心流程的辅助函数集合。

文件整体功能：
  提供 buy_stream() 中各阶段所需的纯工具函数，包括：
  - 支付二维码/订单链接构建
  - 倒计时格式化与等待逻辑
  - prepare / create 请求的 payload 构建
  - 错误码判定、订单终止规则、代理失败处理
  - 通知发送、状态结果格式化等

  本文件不依赖 Gradio 或 UI，所有函数均为无状态工具函数，
  可被 buy_stream()、interface/execution.py、测试代码等多处复用。

所属模块：业务层 (task)
依赖文件：
  - util.Constant              (BASE_URL, BEIJING_TZ, WARMUP_AT_SECONDS)
  - util.TimeUtil              (current_time_ms)
  - util.ErrorCodes            (错误码映射与消息格式化)
  - util.request.BiliRequest   (HTTP 请求封装，用于获取支付二维码)
  - util.request.TokenUtil     (generate_token，本地 token 生成)
  - util.proxy.ProxyBackoff    (代理池耗尽后的退避策略)
  - util.notifer.Notifier      (多渠道通知管理)
  - app_cmd.config.NotifierConfig (通知配置)
  - task.buy_types             (CreateOrderTerminalRule, RetryOutcome)
  - cptoken                    (CTokenRuntimeState, sim_ctoken_state, PTokenGenerator)

对外能力：
  - build_payment_result()     : 抢票成功后构造支付结果字典。
  - wait_until_start()         : 生成器，阻塞等待直到开售时间，期间 yield 倒计时状态。
  - build_token_payload()      : 构造 prepare 接口的请求体。
  - prepare_create_request()   : 构造 createV2 接口的 URL 和请求体（含 ctoken/ptoken）。
  - handle_proxy_failure()     : 统一代理失败处理（切换、冷却、退避、API 补充、通知）。
  - format_status_result()     : 格式化 API 响应为可读状态消息。
  - is_create_success()        : 判定 create 响应是否表示成功下单。
  - create_order_terminal_rule() : 判定错误码是否应终止本轮抢票。
"""

from __future__ import annotations

import datetime
import math
import time
from collections.abc import Callable
from typing import Any

from cptoken import CTokenRuntimeState, sim_ctoken_state, PTokenGenerator

from util import time_service
from app_cmd.config.NotifierConfig import NotifierConfig
from util.Constant import (
    BASE_URL,
    BEIJING_TZ,
    WARMUP_AT_SECONDS,
)
from util.notifer.Notifier import NotifierManager
from util.proxy.ProxyBackoff import ProxyBackoff
from util.TimeUtil import current_time_ms
from util.request.BiliRequest import BiliRequest
from util.request.TokenUtil import generate_token
from util.ErrorCodes import ErrorCodes

from .buy_types import CreateOrderTerminalRule, RetryOutcome


# ---------------------------------------------------------------------------
# 支付结果相关
# ---------------------------------------------------------------------------

def get_qrcode_url(_request, order_id) -> str:
    """
    通过 B站 API 获取订单的支付二维码 URL。

    核心作用：
      调用 /api/ticket/order/getPayParam 接口，提取 code_url 字段。

    输入参数：
      _request  : BiliRequest
        已登录的 HTTP 请求对象，用于发送 GET 请求。
      order_id  : int | str
        订单 ID。

    返回值：
      str — 支付二维码的 URL 字符串。

    异常：
      ValueError — 若接口返回非 0 错误码，抛出"获取二维码失败"。

    调用场景：
      build_payment_result() 内部调用；抢票成功后展示支付二维码。
    """
    url = f"{BASE_URL}/api/ticket/order/getPayParam?order_id={order_id}"
    data = _request.get(url).json()
    if data.get("errno", data.get("code")) == 0:
        return data["data"]["code_url"]
    raise ValueError("获取二维码失败")


def get_order_detail_url(order_id: int | str) -> str:
    """
    构造订单详情页 URL。

    核心作用：
      将订单 ID 拼接为 B站会员购订单详情页的标准链接格式。

    输入参数：
      order_id : int | str — 订单 ID。

    返回值：
      str — 形如 https://show.bilibili.com/platform/orderDetail.html?order_id=xxx 的 URL。

    调用场景：
      build_payment_result()、buy_stream() 抢票成功后构造支付结果。
    """
    return f"{BASE_URL}/platform/orderDetail.html?order_id={order_id}"


def build_payment_result(
    _request: BiliRequest,
    order_id: int | str,
) -> dict[str, Any]:
    """
    构造抢票成功后的支付结果字典。

    核心作用：
      整合订单详情页 URL 和支付二维码 URL，供终端渲染器和通知系统使用。

    输入参数：
      _request  : BiliRequest — 已登录的请求对象。
      order_id  : int | str   — 成功创建的订单 ID。

    返回值：
      dict[str, Any]
        {
          "order_id": order_id,
          "order_detail_url": "...",   # 订单详情页
          "payment_code_url": "...",   # 支付二维码（可能失败为 None）
          "payment_qr_url": "...",     # 与 order_detail_url 相同（兼容字段）
        }

    调用场景：
      buy_stream() 中抢票成功后，用于 emit_payment_details() 和通知消息。
    """
    order_detail_url = get_order_detail_url(order_id)
    payment_code_url = get_qrcode_url(_request, order_id)
    return {
        "order_id": order_id,
        "order_detail_url": order_detail_url,
        "payment_code_url": payment_code_url,
        "payment_qr_url": order_detail_url,
    }


# ---------------------------------------------------------------------------
# 倒计时与等待逻辑
# ---------------------------------------------------------------------------

def format_countdown(seconds: float) -> str:
    """
    将秒数格式化为中文倒计时文本。

    核心作用：
      把浮点秒数转为 "X天X小时X分X秒" 或 "X小时X分X秒" 的友好格式。

    输入参数：
      seconds : float — 剩余秒数（可为负数，内部会被 max(0, ...) 截断）。

    返回值：
      str — 格式化后的倒计时文本。

    调用场景：
      wait_until_start() 的生成器循环中，yield 倒计时状态给终端渲染。
    """
    total_seconds = max(0, int(seconds))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days > 0:
        return f"{days}天{hours}小时{minutes}分{secs}秒"
    return f"{hours}小时{minutes}分{secs}秒"


def next_countdown_report_at(countdown_seconds: int) -> int:
    """
    计算下一次需要报告倒计时的秒数阈值。

    核心作用：
      避免每秒都输出日志，只在有意义的节点（每 10 秒、每分、每小时、每天）报告。
      例如：剩余 125 秒时，返回 120，表示到 120 秒时再报告。

    输入参数：
      countdown_seconds : int — 当前倒计时剩余秒数。

    返回值：
      int — 下一个报告节点的剩余秒数；-1 表示不再按节点报告（进入最后 10 秒逐秒刷新）。

    调用场景：
      wait_until_start() 的循环中，控制日志报告频率。
    """
    if countdown_seconds > 86400:
        return ((countdown_seconds - 1) // 86400) * 86400
    if countdown_seconds > 3600:
        return ((countdown_seconds - 1) // 3600) * 3600
    if countdown_seconds > 60:
        return ((countdown_seconds - 1) // 60) * 60
    if countdown_seconds > 10:
        return ((countdown_seconds - 1) // 10) * 10
    return -1


def wait_until_start(time_start: str, warmup=None):
    """
    生成器：阻塞等待直到开售时间，期间定期 yield 倒计时状态。

    核心作用：
      解析用户设置的开售时间字符串（支持多种格式），计算与当前系统时间的偏差，
      然后在循环中 sleep 并 yield 倒计时消息。接近开售时（WARMUP_AT_SECONDS 内）
      自动调用 warmup 回调预热连接。

    输入参数：
      time_start : str
        开售时间字符串，支持格式：
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"。
      warmup     : Callable[[], Iterable[str]] | None
        预热回调函数，在距离开售前 WARMUP_AT_SECONDS 秒时调用；
        其返回值（字符串可迭代对象）会被逐个 yield 出去。

    返回值：
      Generator[dict, None, None]
        每次 yield 的字典可能包含：
        - "message": str — 人类可读消息（如"距离开始抢票还有: 2小时15分"）。
        - "countdown": str — 格式化倒计时文本。
        - "countdown_seconds": int — 倒计时剩余秒数。

    内部关键逻辑：
      1. 从 time_service 获取时间偏差（timeoffset），用于补偿本地时间与服务器时间的差异。
      2. 使用 time.perf_counter() 做高精度计时，避免 time.sleep 累积误差。
      3. 每秒检查一次剩余时间；当 remaining <= 0 时隐式返回，结束生成器。
      4. 在倒计时 >10 秒时按节点报告（每 10 秒/每分/每小时/每天），
         <=10 秒时逐秒刷新但不重复 yield（通过 last_countdown_seconds 去重）。

    调用场景：
      buy_stream() 的主循环开头，在正式抢票前等待开售时间。
    """
    if not time_start:
        return

    timeoffset = time_service.get_timeoffset()
    yield {"message": "0) 等待开始时间"}
    yield {"message": f"时间偏差已被设置为: {timeoffset}秒"}

    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
    ):
        try:
            target_time = datetime.datetime.strptime(time_start.strip(), fmt).replace(
                tzinfo=BEIJING_TZ
            )
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"无法解析抢票时间: {time_start!r}")

    yield {"message": f"计划抢票开始时间: {target_time.strftime('%Y-%m-%d %H:%M:%S')}"}

    time_difference = target_time.timestamp() - time.time() + timeoffset
    end_time = time.perf_counter() + time_difference
    next_report_at = float("inf")
    warmed = False
    last_countdown_seconds: int | None = None
    while True:
        remaining = end_time - time.perf_counter()
        if remaining <= 0:
            return
        countdown_seconds = max(0, math.ceil(remaining))
        countdown_text = format_countdown(remaining)
        if countdown_seconds != last_countdown_seconds:
            last_countdown_seconds = countdown_seconds
            yield {
                "message": None,
                "countdown": countdown_text,
                "countdown_seconds": countdown_seconds,
            }
        if not warmed and warmup is not None and remaining <= WARMUP_AT_SECONDS:
            warmed = True
            for warm_message in warmup() or []:
                yield {
                    "message": warm_message,
                    "countdown": countdown_text,
                    "countdown_seconds": countdown_seconds,
                }
            continue
        if countdown_seconds <= next_report_at:
            if countdown_seconds > 10:
                yield {
                    "message": f"距离开始抢票还有: {countdown_text}",
                    "countdown": countdown_text,
                    "countdown_seconds": countdown_seconds,
                }
            next_report_at = next_countdown_report_at(countdown_seconds)
        time.sleep(min(0.5, remaining))


# ---------------------------------------------------------------------------
# prepare / create 请求构建
# ---------------------------------------------------------------------------

def build_token_payload(tickets_info: dict) -> dict:
    """
    构造 prepare 接口的请求体（token payload）。

    核心作用：
      从 tickets_info 提取 count、screen_id、project_id、sku_id 等核心字段，
      构造 B站 /api/ticket/order/prepare 接口所需的标准请求体。

    输入参数：
      tickets_info : dict
        抢票配置字典，必须包含 count、screen_id、project_id、sku_id。

    返回值：
      dict
        包含 count、screen_id、order_type、project_id、sku_id、
        buyer_info、ignoreRequestLimit、ticket_agent、token、newRisk、requestSource 等字段。

    调用场景：
      buy_stream() 的 prepare 阶段，每次重新准备订单前调用。
    """
    count = int(tickets_info["count"])
    screen_id = int(tickets_info["screen_id"])
    order_type = int(tickets_info.get("order_type", 1))
    project_id = int(tickets_info["project_id"])
    sku_id = int(tickets_info["sku_id"])
    return {
        "count": count,
        "screen_id": screen_id,
        "order_type": order_type,
        "project_id": project_id,
        "sku_id": sku_id,
        "buyer_info": tickets_info.get(
            "_prepare_buyer_info",
            tickets_info.get("buyer_info", []),
        ),
        "ignoreRequestLimit": True,
        "ticket_agent": "",
        "token": "",
        "newRisk": True,
        "requestSource": "neul-next",
    }


def build_order_token(tickets_info: dict) -> str:
    """
    使用本地算法生成订单 token（本地 token 模式）。

    核心作用：
      在不调用 B站 prepare 接口的情况下，直接生成本地可用的 order token，
      用于某些允许本地 token 的抢票场景（use_local_token=True）。

    输入参数：
      tickets_info : dict — 抢票配置字典。

    返回值：
      str — 本地生成的 token 字符串。

    调用场景：
      buy_stream() 中当 config.use_local_token=True 时，跳过 prepare 直接调用此函数。
    """
    return generate_token(
        project_id=int(tickets_info["project_id"]),
        screen_id=int(tickets_info["screen_id"]),
        order_type=int(tickets_info.get("order_type", 1)),
        count=int(tickets_info["count"]),
        sku_id=int(tickets_info["sku_id"]),
    )


def normalize_prepare_ptoken(value: str | None) -> str:
    """
    规范化 prepare 接口返回的 ptoken。

    核心作用：
      B站 prepare 接口有时会返回包含等号的 ptoken，需要去除等号以兼容 create 接口。

    输入参数：
      value : str | None — prepare 响应中的 ptoken 原始值。

    返回值：
      str — 去除等号后的 ptoken；None 时返回空字符串。

    调用场景：
      prepare_create_request() 中构造 create 请求体时调用。
    """
    if value is None:
        return ""
    return str(value).replace("=", "")


# ---------------------------------------------------------------------------
# 订单终止规则与成功判定
# ---------------------------------------------------------------------------

# 错误码 -> 终止规则的映射表。
# 这些错误码表示继续重试无意义，应直接终止本轮抢票。
CREATE_ORDER_TERMINAL_RULES: dict[int, CreateOrderTerminalRule] = {
    100003: CreateOrderTerminalRule(
        status="completed",
        message="该项目每人限购1张，已存在购买订单，停止重试",
    ),
    100048: CreateOrderTerminalRule(
        status="completed",
        message="有尚未完成订单，停止重试",
        expose_payment_url=True,
    ),
    100079: CreateOrderTerminalRule(
        status="completed",
        message="有重复订单，停止重试",
    ),
}


def create_order_terminal_rule(err: int) -> CreateOrderTerminalRule | None:
    """
    查询错误码对应的终止规则。

    核心作用：
      若错误码在 CREATE_ORDER_TERMINAL_RULES 中，返回对应的终止规则；
      否则返回 None，表示应继续重试。

    输入参数：
      err : int — B站 API 返回的错误码。

    返回值：
      CreateOrderTerminalRule | None — 终止规则或 None。

    调用场景：
      buy_stream() 的 create 阶段，每次收到响应后调用。
    """
    return CREATE_ORDER_TERMINAL_RULES.get(err)


def is_create_success(ret: dict, err: int) -> bool:
    """
    判定 create 接口响应是否表示成功下单。

    核心作用：
      成功的条件是：错误码为 0，且响应消息中不包含 "defaultBBR"（B站的一种降级标记）。

    输入参数：
      ret : dict — create 接口的完整响应字典。
      err : int  — 解析后的错误码。

    返回值：
      bool — True 表示成功创建订单，False 表示失败或需要重试。

    调用场景：
      buy_stream() 的 create 阶段，每次收到响应后立即调用。
    """
    resp_message = str(ret.get("msg", ret.get("message", "")) or "")
    return err == 0 and "defaultBBR" not in resp_message


def extract_order_id(ret: dict | None) -> int | str | None:
    """
    从 create 接口响应中提取订单 ID。

    核心作用：
      安全地导航 response.data.orderId 路径，处理各种空值情况。

    输入参数：
      ret : dict | None — create 接口的响应字典。

    返回值：
      int | str | None — 订单 ID；若路径缺失或值为空/0，返回 None。

    调用场景：
      buy_stream() 中当收到终止错误码（如 100048）且 expose_payment_url=True 时调用，
      用于提取已有订单的 ID 以展示支付链接。
    """
    if not isinstance(ret, dict):
        return None
    data = ret.get("data")
    if not isinstance(data, dict):
        return None
    order_id = data.get("orderId")
    return order_id if order_id not in (None, "", 0) else None


def extract_response_message(ret: dict) -> str:
    """
    从 API 响应中提取消息文本。

    核心作用：
      优先取 msg 字段，其次取 message 字段，处理空值并 strip。

    输入参数：
      ret : dict — API 响应字典。

    返回值：
      str — 消息文本（可能为空字符串）。

    调用场景：
      format_status_result()、ErrorCodes.append_response_message() 等格式化函数中调用。
    """
    return str(ret.get("msg", ret.get("message", "")) or "").strip()


def append_response_message(err: int, base: str, ret: dict | None) -> str:
    """
    将错误码、基础消息和响应消息拼接为完整状态描述。

    核心作用：
      委托给 ErrorCodes.append_response_message() 实现统一格式化。

    输入参数：
      err  : int      — 错误码。
      base : str      — 基础描述文本。
      ret  : dict|None — 原始响应字典。

    返回值：
      str — 拼接后的完整消息。

    调用场景：
      format_status_result()、format_retry_reason() 中调用。
    """
    return ErrorCodes.append_response_message(err, base, ret)


def format_retry_reason(outcome: RetryOutcome) -> str:
    """
    格式化一轮 create 失败后的重试原因描述。

    核心作用：
      根据 RetryOutcome 中记录的最后状态（异常、错误码+响应、未知），
      生成人类可读的失败原因摘要，用于日志和终端显示。

    输入参数：
      outcome : RetryOutcome
        一轮 create 请求的结果记录对象。

    返回值：
      str — 失败原因描述，如"最后一次异常: ConnectionTimeout"
            或"最后一次返回: [100001](token过期) | {...}"。

    调用场景：
      buy_stream() 中一轮 create 全部尝试失败后，决定是否重新 prepare 时调用。
    """
    if outcome.exc is not None:
        return f"最后一次异常: {outcome.exc}"
    if outcome.err is None:
        return "最后一次失败原因未知"
    reason = ErrorCodes.get_message_or_unknown(outcome.err)
    detail = outcome.ret if outcome.ret is not None else {}
    base = f"最后一次返回: [{outcome.err}]({reason}) | {detail}"
    return append_response_message(outcome.err, base, outcome.ret)


def summarize_non_json_response(prefix: str, diagnostic: str) -> str:
    """
    总结非 JSON 响应的诊断信息。

    核心作用：
      B站有时返回 HTML（如 412 风控页）而非 JSON，本函数从诊断字符串中
      提取关键信息（状态码、内容类型），生成简洁的人类可读消息。

    输入参数：
      prefix     : str — 前缀文本，如"创建订单接口"。
      diagnostic : str — BiliRequest.describe_non_json_response() 返回的诊断字符串。

    返回值：
      str — 简洁的总结消息，如"创建订单接口触发 412 风控"。

    调用场景：
      buy_stream() 中当 json() 解析失败时调用。
    """
    if "status=412" in diagnostic:
        return f"{prefix}触发 412 风控"

    content_type = "未知"
    for part in diagnostic.split(", "):
        if part.startswith("content_type="):
            content_type = part.split("=", 1)[1]
            break
    return f"{prefix}返回了非 JSON 响应（{content_type}）"


# ---------------------------------------------------------------------------
# 代理失败处理
# ---------------------------------------------------------------------------

def build_proxy_exhausted_message(_request: BiliRequest, delay_seconds: int) -> str:
    """
    构造代理池耗尽时的通知消息。

    核心作用：
      生成包含当前代理池状态的告警文本，用于多渠道通知（Server酱/Bark等）。

    输入参数：
      _request      : BiliRequest — 当前请求对象。
      delay_seconds : int — 预计等待的秒数。

    返回值：
      str — 通知消息文本。

    调用场景：
      notify_proxy_exhausted() 内部调用。
    """
    return (
        "当前所有代理暂时不可用，请尽快补充或更换代理。"
        f"程序将休息 {delay_seconds} 秒后继续尝试。"
        f" 代理池状态：{_request.proxy_pool_status()}"
    )


def notify_proxy_exhausted(
    notifier_config: NotifierConfig,
    _request: BiliRequest,
    delay_seconds: int,
) -> None:
    """
    当代理池全部失效时发送通知。

    核心作用：
      若用户在高级设置中开启了"代理耗尽通知"，则通过 NotifierManager
      并行发送所有已配置渠道（Bark、Server酱、PushPlus 等）的通知。

    输入参数：
      notifier_config : NotifierConfig — 通知配置（含各渠道开关）。
      _request        : BiliRequest     — 当前请求对象。
      delay_seconds   : int             — 预计等待秒数。

    返回值：无。

    调用场景：
      handle_proxy_failure() 中当所有代理均不可用且退避策略触发通知时调用。
    """
    if not notifier_config.notify_proxy_exhausted:
        return

    manager = NotifierManager.create_from_config(
        config=notifier_config,
        title="代理已全部失效",
        content=build_proxy_exhausted_message(_request, delay_seconds),
        include_audio=False,
    )
    manager.start_all()


def handle_proxy_failure(
    _request: BiliRequest,
    reason: str,
    proxy_backoff: ProxyBackoff,
    notifier_config: NotifierConfig,
    replenish_proxy_pool: Callable[[], tuple[bool, str | None]] | None = None,
) -> tuple[str | None, int | None]:
    """
    统一处理代理请求失败后的逻辑。

    核心作用：
      按优先级执行以下步骤：
      1. 标记当前代理失败（若连续失败达到阈值则冷却）。
      2. 尝试切换到下一个可用代理。
      3. 若切换失败但代理池仍有可用代理，返回 immediate_message。
      4. 若配置了 replenish_proxy_pool 回调（如代理 API），尝试补充代理。
      5. 若所有代理均不可用，进入退避等待（ProxyBackoff），并可能发送通知。

    输入参数：
      _request            : BiliRequest
        当前请求对象，用于代理状态查询和切换。
      reason              : str
        失败原因描述，用于日志和冷却记录。
      proxy_backoff       : ProxyBackoff
        代理池耗尽后的退避策略对象，管理等待时间和通知频率。
      notifier_config     : NotifierConfig
        通知配置，用于代理耗尽时发送告警。
      replenish_proxy_pool: Callable[[], tuple[bool, str|None]] | None
        可选的代理补充回调；返回 (是否成功补充, 消息文本)。
        在 buy_stream() 中，此回调会从代理 API 拉取新代理。

    返回值：
      tuple[str | None, int | None]
        - immediate_message : str|None — 需要立即显示的消息（如切换代理、冷却通知）。
        - delay_seconds     : int|None — 需要等待的秒数；None 表示无需等待，可继续请求。

    内部关键逻辑：
      - 切换代理成功时，proxy_backoff.reset() 重置退避计时器。
      - 补充代理成功时，同样重置退避计时器。
      - 退避等待时间按指数增长（由 ProxyBackoff 内部实现），最大不超过配置上限。

    调用场景：
      buy_stream() 中每次请求异常（HTTPError、RequestException、BiliConnectionError）
      或收到 412 风控响应时调用。
    """
    previous_proxy = _request.current_proxy_display()
    cooled = _request.mark_current_proxy_failure(reason)
    if cooled:
        immediate_message = f"代理冷却: {previous_proxy} 短时间内连续失败，已暂时停用"
    else:
        immediate_message = None

    if _request.switch_proxy():
        proxy_backoff.reset()
        switched_message = f"切换代理到 {_request.current_proxy_display()}"
        if immediate_message:
            return f"{immediate_message}\n{switched_message}", None
        return switched_message, None

    if _request.has_available_proxy():
        return immediate_message, None

    if replenish_proxy_pool is not None:
        replenished, replenish_message = replenish_proxy_pool()
        if replenished:
            proxy_backoff.reset()
            if immediate_message and replenish_message:
                return f"{immediate_message}\n{replenish_message}", None
            return replenish_message or immediate_message, None
        if replenish_message:
            immediate_message = (
                f"{immediate_message}\n{replenish_message}"
                if immediate_message
                else replenish_message
            )

    delay_seconds = proxy_backoff.next_delay_seconds()
    if proxy_backoff.should_notify():
        notify_proxy_exhausted(notifier_config, _request, delay_seconds)
    exhausted_message = f"所有代理当前不可用，休息 {delay_seconds} 秒后再试"
    if immediate_message:
        return f"{immediate_message}\n{exhausted_message}", delay_seconds
    return exhausted_message, delay_seconds


# ---------------------------------------------------------------------------
# 状态格式化
# ---------------------------------------------------------------------------

def format_status_result(prefix: str, ret: dict) -> str:
    """
    格式化 API 响应为可读的状态结果消息。

    核心作用：
      从响应中提取 errno/code，查询对应的错误码含义，
      拼接成 "前缀: [错误码] 错误含义 | 响应消息" 的格式。

    输入参数：
      prefix : str — 前缀文本，如"订单准备结果"。
      ret    : dict — API 响应字典。

    返回值：
      str — 格式化后的状态消息。

    调用场景：
      buy_stream() 中每次 prepare 或 create 收到响应后，emit 到终端显示。
    """
    err = int(ret.get("errno", ret.get("code", -1)))
    reason = ErrorCodes.get_message(err)
    if reason:
        return append_response_message(err, f"{prefix}: [{err}] {reason}", ret)
    message = extract_response_message(ret)
    if message:
        return f"{prefix}: [{err}] {message}"
    return f"{prefix}: [{err}] {ret}"


# ---------------------------------------------------------------------------
# create 请求构建
# ---------------------------------------------------------------------------

def prepare_create_request(
    tickets_info: dict,
    order_token: str,
    is_hot_project: bool,
    request_result: dict | None,
    ticket_state: CTokenRuntimeState,
    local_ptoken: str | None = None,
) -> tuple[str, dict]:
    """
    构造 createV2 接口的 URL 和请求体。

    核心作用：
      1. 复制 tickets_info 作为请求体基础，清理无用字段（detail、sale_start、username、_prepare_buyer_info）。
      2. 设置 token、timestamp、ctoken、ptoken 等关键参数。
      3. 使用 cptoken 库生成 create 阶段的 ctoken。
      4. 若启用了本地 token 模式，使用本地生成的 ptoken；否则从 prepare 响应中提取 ptoken。

    输入参数：
      tickets_info   : dict
        抢票配置字典，包含 project_id、count、screen_id、sku_id、buyer_info 等。
      order_token    : str
        prepare 阶段获取的订单 token（或本地生成的 token）。
      is_hot_project : bool
        是否为热门项目（影响 URL 参数，当前版本保留参数但逻辑已简化）。
      request_result : dict | None
        prepare 接口的原始响应，用于提取 ptoken；本地 token 模式下可为 None。
      ticket_state   : CTokenRuntimeState
        ctoken 运行时状态对象，用于生成 create 阶段的 ctoken。
      local_ptoken   : str | None
        本地生成的 ptoken；不为 None 时优先使用，跳过从 prepare 响应提取。

    返回值：
      tuple[str, dict]
        - url     : str  — 完整的 createV2 请求 URL（含 project_id 和 ptoken 查询参数）。
        - payload : dict — POST 请求体字典（会被 BiliRequest 序列化为 form-data 或 JSON）。

    内部关键逻辑：
      - ctoken 通过 sim_ctoken_state() 基于 ticket_state 生成，模拟浏览器环境。
      - ptoken 来源：local_ptoken > prepare_response.data.ptoken > ""。
      - URL 格式：{BASE_URL}/api/ticket/order/createV2?project_id={pid}&ptoken={ptoken}

    调用场景：
      buy_stream() 的 create 阶段，每次批量请求前调用。
    """
    payload = dict(tickets_info)
    payload["again"] = 1
    payload["token"] = order_token
    now_ms = current_time_ms()
    payload["timestamp"] = now_ms
    payload["newRisk"] = True
    payload["requestSource"] = "neul-next"
    payload.pop("detail", None)
    payload.pop("sale_start", None)
    payload.pop("username", None)
    payload.pop("_prepare_buyer_info", None)
    url = (
        f"{BASE_URL}/api/ticket/order/createV2?project_id={tickets_info['project_id']}"
    )

    create_state = sim_ctoken_state(
        before_state=ticket_state,
        now_ms=now_ms,
    )
    ctoken = create_state.generate_create_ctoken()
    payload["ctoken"] = ctoken

    if local_ptoken is not None:
        ptoken = local_ptoken
    else:
        prepare_data = request_result.get("data", {}) if request_result else {}
        ptoken = normalize_prepare_ptoken(prepare_data.get("ptoken"))

    payload["ptoken"] = ptoken
    payload["orderCreateUrl"] = "https://show.bilibili.com/api/ticket/order/createV2"
    url += "&ptoken=" + ptoken
    return url, payload
