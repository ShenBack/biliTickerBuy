"""
task/buy.py — 抢票核心流程主引擎。

文件整体功能：
  定义 Buy 数据类与 buy_stream() 生成器函数，实现从"等待开售"到"抢票成功/失败"
  的完整业务流程。

  - Buy 类：对外封装配置解析、流式启动、后台线程工作者（BuyStreamWorker）创建、
            以及在新终端窗口中启动抢票子进程的能力。
  - buy_stream()：同步生成器，内部包含 emit()、handle_proxy_failure() 等闭包，
                   按顺序执行：等待开售 -> prepare（或本地token）-> 批量 create ->
                   成功提取支付链接 / 失败重试 / 触发终止规则。

所属模块：业务层 (task)
依赖文件：
  - app_cmd.config.BuyConfig        (抢票配置数据类)
  - interface.project               (fetch_project_payload，拉取项目详情)
  - util.request.BiliRequest        (HTTP 请求封装，含代理管理)
  - util.proxy.ProxyManager         (代理池管理)
  - util.proxy.ProxyBackoff         (代理退避策略)
  - util.proxy.ProxyApiProvider     (代理 API 自动补充)
  - util.notifer.Notifier           (多渠道通知)
  - task.buy_helpers                (支付结果构建、请求体构建、错误处理等辅助函数)
  - task.buy_types                  (BuyStreamState / BuyStreamEvent / BuyStreamUpdate 等)
  - cptoken                         (ctoken 与 ptoken 生成)

对外能力：
  - Buy(config).stream()            → 生成 BuyStreamEvent 事件流。
  - Buy(config).start_worker()      → 返回已启动的 BuyStreamWorker。
  - Buy(config).buy_new_terminal()  → 在新终端窗口中启动子进程抢票。
  - buy_stream(config)              → 低层生成器，可被直接消费或包装进 Worker。
  - buy_new_terminal(config, ...)   → 便捷函数，直接在新终端启动抢票。
"""

import json
import os
import random
import subprocess
import sys
import time
import uuid
import copy
import webbrowser
from collections.abc import Generator
from dataclasses import dataclass
from json import JSONDecodeError
import shutil
import qrcode
from loguru import logger

from requests import HTTPError, RequestException
from cptoken import (
    generate_browser_window_state,
    init_ctoken_state,
    PTokenGenerator,
)

from app_cmd.config.BuyConfig import BuyConfig
from interface.project import fetch_project_payload
from util.notifer.Notifier import NotifierManager
from util.proxy.ProxyBackoff import ProxyBackoff
from util.proxy.ProxyApiProvider import fetch_proxy_api
from util.proxy.ProxyManager import ProxyManager
from util.notifer.RandomMessages import get_random_fail_message
from util.TimeUtil import current_time_ms
from util.ErrorCodes import ErrorCodes
from task.buy_helpers import (
    BASE_URL as base_url,
    build_payment_result,
    build_token_payload as _build_token_payload,
    build_order_token as _build_order_token,
    create_order_terminal_rule as _create_order_terminal_rule,
    extract_order_id as _extract_order_id,
    format_retry_reason as _format_retry_reason,
    format_status_result as _format_status_result,
    get_order_detail_url,
    handle_proxy_failure as _handle_proxy_failure,
    is_create_success as _is_create_success,
    prepare_create_request as _prepare_create_request,
    summarize_non_json_response as _summarize_non_json_response,
    wait_until_start as _wait_until_start,
)
from task.buy_types import (
    BuyStreamEvent,
    BuyStreamState,
    BuyStreamUpdate,
    BuyStreamWorker,
    CreateOrderTerminalRule,
    RetryOutcome,
)
from util.request.BiliRequest import BiliRequest
from util.request.exceptions import BiliConnectionError, BiliRateLimitError


# ---------------------------------------------------------------------------
# Buy — 抢票业务封装类
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class Buy:
    """
    抢票业务封装类。

    该类设计作用：
      将 BuyConfig 配置转化为可执行的抢票流，对外提供统一接口：
      - stream()      : 获取同步生成器。
      - start_worker(): 获取后台线程包装的事件流工作者。
      - run()         : 阻塞运行并消费消息（默认通过 logger.info 输出）。
      - buy()         : run() 的便捷调用。
      - buy_new_terminal() / start_new_terminal() : 在新终端子进程中启动抢票。

    存储属性：
      config : BuyConfig — 原始抢票配置（可能包含 config_file 路径或内嵌 JSON）。
    """

    config: BuyConfig

    def _resolved_tickets_info(self) -> str:
        """
        解析最终的 tickets_info JSON 字符串。

        核心作用：
          若 config 中指定了 config_file 路径，则从文件读取；否则直接使用配置中的字符串。

        返回值：
          str — 完整的 tickets_info JSON 字符串。

        调用场景：
          resolved_config() 内部调用。
        """
        if self.config.config_file:
            config_path = os.path.expanduser(self.config.config_file)
            with open(config_path, "r", encoding="utf-8") as config_file:
                return config_file.read()
        return self.config.tickets_info

    def resolved_config(self) -> BuyConfig:
        """
        返回已解析 tickets_info 后的新 BuyConfig。

        核心作用：
          用 _resolved_tickets_info() 的结果覆盖原配置，确保 buy_stream() 拿到的是
          最终 JSON 字符串而非文件路径。

        返回值：
          BuyConfig — 新的配置对象（原对象不变）。

        调用场景：
          stream() 和 to_cli_args() 中使用。
        """
        return self.config.with_overrides(
            tickets_info=self._resolved_tickets_info(),
        )

    def stream(self):
        """
        获取抢票事件流生成器。

        核心作用：
          将 resolved_config() 传入 buy_stream()，并委托 yield from 返回事件。

        返回值：
          Generator[BuyStreamEvent, None, None] — 可迭代的抢票事件流。

        调用场景：
          start_worker()、run() 内部调用。
        """
        yield from buy_stream(self.resolved_config())

    def start_worker(self) -> BuyStreamWorker:
        """
        启动后台抢票工作者。

        核心作用：
          将 self.stream() 包装进 BuyStreamWorker 并在后台线程中运行，
          使主线程可通过 iter_events() 非阻塞消费事件。

        返回值：
          BuyStreamWorker — 已启动的后台工作者。

        调用场景：
          run() 和外部 UI 代码调用。
        """
        return BuyStreamWorker.start_buy_stream_worker(self.stream)

    def to_cli_args(self) -> list[str]:
        """
        将当前 Buy 实例转换为命令行参数列表。

        核心作用：
          用于 start_new_terminal() 中构造子进程的命令行参数，
          支持 --config-file 或 --tickets-info 两种模式。

        返回值：
          list[str] — 命令行参数列表，形如 ["buy", "--tickets-info", "{...}", ...]。

        调用场景：
          start_new_terminal() 内部调用。
        """
        if self.config.config_file:
            return [
                "buy",
                "--config-file",
                self.config.config_file,
                *self.config.to_cli_args(),
            ]
        return [
            "buy",
            "--tickets-info",
            self.config.tickets_info,
            *self.config.to_cli_args(),
        ]

    def run(self, on_message=None) -> None:
        """
        阻塞运行抢票流程并消费消息。

        核心作用：
          启动工作者后遍历事件流；若事件包含 message 且提供了 on_message 回调，
          则将消息传递给回调函数。

        输入参数：
          on_message : Callable[[str], None] | None
            消息回调，如 logger.info 或 print。

        返回值：无。

        调用场景：
          buy() 内部调用；命令行模式下直接运行。
        """
        worker = self.start_worker()
        for event in worker.iter_events():
            if event.message is not None and on_message is not None:
                on_message(event.message)

    def buy(self) -> None:
        """
        启动抢票并默认通过 logger.info 输出消息。

        核心作用：run(logger.info) 的便捷封装。

        调用场景：命令行入口直接调用。
        """
        self.run(logger.info)

    def start_new_terminal(
        self,
        *,
        log_file_path: str | None = None,
        log_level: str | None = None,
        log_retention_days: int | None = None,
    ) -> subprocess.Popen:
        """
        在新终端窗口中启动抢票子进程。

        核心作用：
          1. 根据当前运行环境（PyInstaller 冻结态或源码态）构造命令行。
          2. 设置环境变量（BTB_PARENT_PID、BTB_LOG_LEVEL、BTB_LOG_RETENTION_DAYS 等）。
          3. Windows 下使用 CREATE_NEW_CONSOLE 打开新终端窗口；
             Linux/macOS 下使用 start_new_session 脱离终端。

        输入参数：
          log_file_path      : str | None — 日志文件路径。
          log_level          : str | None — 日志级别（simple / debug / 其他）。
          log_retention_days : int | None — 日志保留天数。

        返回值：
          subprocess.Popen — 新终端子进程对象。

        调用场景：
          Gradio UI 中点击"在新终端运行"时调用。
        """
        command = None

        if getattr(sys, "frozen", False):
            command = [sys.executable]
        else:
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            main_py = os.path.join(script_dir, "main.py")

            if os.path.exists(main_py):
                command = [sys.executable, main_py]
            else:
                btb_path = shutil.which("btb")
                if not btb_path:
                    raise RuntimeError("Cannot find main.py or btb command")
                command = [btb_path]
        command.extend(self.to_cli_args())
        env = os.environ.copy()
        env["BTB_PARENT_PID"] = str(os.getpid())
        effective_log_level = log_level or self.config.log_level
        if effective_log_level:
            normalized_log_level = str(effective_log_level).lower()
            if normalized_log_level == "simple":
                env["BTB_LOG_LEVEL"] = "INFO"
                env["BTB_CONSOLE_LOG_LEVEL"] = "INFO"
            elif normalized_log_level == "debug":
                env["BTB_LOG_LEVEL"] = "DEBUG"
                env["BTB_CONSOLE_LOG_LEVEL"] = "DEBUG"
            else:
                env["BTB_LOG_LEVEL"] = "DEBUG"
                env["BTB_CONSOLE_LOG_LEVEL"] = "INFO"
        env["BTB_LOG_RETENTION_DAYS"] = str(
            log_retention_days
            if log_retention_days is not None
            else self.config.log_retention_days
        )
        if log_file_path:
            env["BTB_APP_LOG_NAME"] = os.path.basename(log_file_path)
        else:
            env.setdefault("BTB_APP_LOG_NAME", f"{uuid.uuid4()}.log")
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE
            )
            env["BTB_HOLD_TERMINAL"] = "1"
        else:
            env["BTB_CHILD_PROCESS"] = "1"
            kwargs["start_new_session"] = True

        if os.name == "nt":
            return subprocess.Popen(command, env=env, **kwargs)

        with open(os.devnull, "r") as devnull_in, open(os.devnull, "a") as devnull_out:
            return subprocess.Popen(
                command,
                env=env,
                stdin=devnull_in,
                stdout=devnull_out,
                stderr=devnull_out,
                **kwargs,
            )

    def buy_new_terminal(
        self,
        *,
        log_file_path: str | None = None,
        log_level: str | None = None,
        log_retention_days: int | None = None,
    ) -> subprocess.Popen:
        """
        start_new_terminal() 的别名封装，保持向后兼容。

        调用场景：
          Gradio UI 和命令行代码中统一调用此方法启动新终端。
        """
        return self.start_new_terminal(
            log_file_path=log_file_path,
            log_level=log_level,
            log_retention_days=log_retention_days,
        )


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------

def _extract_prepare_token(result: dict | None) -> str | None:
    """
    从 prepare 接口响应中提取订单 token。

    核心作用：
      防御性地逐层检查 result -> data -> token，去除空白字符后返回；
      任何环节异常都返回 None。

    输入参数：
      result : dict | None — prepare 接口的 JSON 响应字典。

    返回值：
      str | None — 提取到的 token；无效时返回 None。

    调用场景：
      buy_stream() 的 prepare 阶段。
    """
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    token = data.get("token")
    if token is None:
        return None
    token = str(token).strip()
    return token or None


def _format_reprepare_reason(reason: str) -> str:
    """
    格式化"重新准备订单"的人类可读原因。

    输入参数：
      reason : str — 简短原因描述。

    返回值：
      str — 格式化后的完整原因文本。

    调用场景：
      buy_stream() 中 token 失效或其他原因需要重新 prepare 时调用。
    """
    return f"重新准备订单，原因：{reason}"


# ---------------------------------------------------------------------------
# buy_stream — 核心抢票生成器
# ---------------------------------------------------------------------------

def buy_stream(config: BuyConfig):
    """
    核心抢票流程生成器。

    该函数设计作用：
      作为同步生成器，按时间线顺序执行抢票的全部阶段，并通过 yield BuyStreamEvent
      将状态变化实时暴露给外部消费者（TerminalRenderer、Gradio UI 等）。

    执行阶段概览：
      1. 初始化状态、解析 tickets_info、设置代理池、提取终端显示信息。
      2. 等待开售（wait_until_start），期间定期 yield 倒计时事件。
      3. 循环执行：
         a. prepare 阶段（或本地 token 模式跳过）-> 获取 order_token。
         b. create 阶段 -> 批量发送 createV2 请求，处理成功/重试/终止规则。
         c. 成功后 -> 获取支付二维码、发送通知、打开浏览器（可选）。
         d. 失败/终止 -> 根据错误码决定重试、终止或展示已有订单链接。
      4. 抢票结束 -> 注销代理使用。

    输入参数：
      config : BuyConfig — 完整的抢票配置对象。

    返回值：
      Generator[BuyStreamEvent, None, None] — 实时事件流。

    内部闭包函数：
      - emit()                  : 构造 BuyStreamEvent，将增量更新应用到状态。
      - emit_payment_details()  : 抢票成功后 yield 支付相关标记事件。
      - _extract_terminal_state_info() : 提取账号名、购票人、票种、开售时间等。
      - handle_proxy_failure()  : 统一代理失败处理（切换/冷却/API补充）。
      - handle_non_json_response() : 处理非 JSON 响应（如 412 风控）。
      - emit_reprepare()        : 发送"重新准备订单"状态事件。
      - refresh_hot_and_warm()  : 拉取项目详情并预热 HTTP/2 连接。
      - _reset_refresh_counter() / _on_100001() : 循环内项目详情复检逻辑。
    """
    state = BuyStreamState()

    def emit(
        kind: str,
        message: str | None,
        update: BuyStreamUpdate | None = None,
    ):
        """
        构造并返回 BuyStreamEvent，同时将增量更新应用到当前状态。

        核心作用：
          buy_stream() 内所有 yield 的统一出口。先 apply_to(state) 更新状态，
          再对 state 做深拷贝，避免外部消费者与内部状态互相污染。

        输入参数：
          kind    : str — 事件类型标识。
          message : str | None — 人类可读消息；None 表示纯状态更新。
          update  : BuyStreamUpdate | None — 增量更新对象。

        返回值：
          BuyStreamEvent — 包装了当前状态快照的事件对象。

        调用场景：
          buy_stream() 内几乎所有 yield 点。
        """
        if update is not None:
            update.apply_to(state)
        if message is not None:
            state.last_message = message

        return BuyStreamEvent(
            kind=kind,
            message=message,
            state=copy.deepcopy(state),
            data=update.to_dict() if update is not None else {},
        )

    def emit_payment_details(
        payment_result: dict,
        *,
        status: str,
    ):
        """
        抢票成功后，逐条 yield 支付相关标记事件。

        核心作用：
          将 order_id、order_detail_url、payment_code_url、payment_qr_url
          分别包装为 "payment_qr" 类型事件并 yield，便于终端渲染器提取展示。

        输入参数：
          payment_result : dict — build_payment_result() 返回的结果字典。
          status         : str  — 当前状态标识（如 "succeeded" / "completed"）。

        返回值：
          Generator[BuyStreamEvent, None, None] — 支付标记事件流。

        调用场景：
          buy_stream() 中抢票成功或终止规则要求暴露支付链接时调用。
        """
        update = BuyStreamUpdate(
            order_id=payment_result.get("order_id"),
            order_detail_url=payment_result.get("order_detail_url"),
            payment_code_url=payment_result.get("payment_code_url"),
            payment_qr_url=payment_result.get("payment_qr_url"),
            status=status,
        )

        markers = [
            ("ORDER_ID", payment_result.get("order_id")),
            ("ORDER_DETAIL_URL", payment_result.get("order_detail_url")),
            ("PAYMENT_CODE_URL", payment_result.get("payment_code_url")),
            ("PAYMENT_QR_URL", payment_result.get("payment_qr_url")),
        ]
        for marker, value in markers:
            if value in (None, ""):
                continue
            yield emit("payment_qr", f"{marker}={value}", update)

    def _extract_terminal_state_info(tickets_info: dict) -> dict[str, str]:
        """
        从 tickets_info 提取终端显示所需信息。

        核心作用：
          提取 account_name（用户名）、buyer_name（购票人姓名拼接）、
          ticket_type（票种）、show_time（开售时间），用于 Gradio 终端卡片展示。

        关键逻辑：
          - buyer_info 优先使用 _prepare_buyer_info（原始列表），避免 json.dumps 后的字符串。
          - ticket_type 通过 detail 字段解析，去掉 "用户名-" 前缀和 "-购票人名" 后缀。

        输入参数：
          tickets_info : dict — 解析后的抢票配置字典。

        返回值：
          dict[str, str] — {"account_name": ..., "buyer_name": ..., "ticket_type": ..., "show_time": ...}。

        调用场景：
          buy_stream() 初始化阶段，emit "state" 事件前调用。
        """
        account_name = str(tickets_info.get("username") or "")

        buyer_info = tickets_info.get("_prepare_buyer_info") or tickets_info.get("buyer_info") or []
        if isinstance(buyer_info, str):
            try:
                buyer_info = json.loads(buyer_info)
            except Exception:
                buyer_info = []
        buyer_names = [
            str(p.get("name") or "")
            for p in buyer_info
            if p and p.get("name")
        ]
        buyer_name = "、".join(buyer_names) if buyer_names else ""
        show_time = str(tickets_info.get("sale_start") or "")

        detail = str(tickets_info.get("detail") or "")
        ticket_type = ""
        if account_name and detail.startswith(account_name + "-"):
            rest = detail[len(account_name) + 1 :]
            for name in reversed(buyer_names):
                suffix = "-" + name
                if rest.endswith(suffix):
                    rest = rest[: -len(suffix)]
            if "-" in rest:
                ticket_type = rest.split("-", 1)[1]
            else:
                ticket_type = rest
        else:
            ticket_type = detail

        return {
            "account_name": account_name,
            "buyer_name": buyer_name,
            "ticket_type": ticket_type,
            "show_time": show_time,
        }

    def handle_proxy_failure(
        reason: str,
        *,
        attempt: int | None = None,
    ):
        """
        闭包：统一处理代理失败，包括切换代理、冷却等待、API 自动补充。

        核心作用：
          1. 若配置了 proxy_api_url，尝试从代理 API 自动拉取新代理补充池子。
          2. 调用 _handle_proxy_failure() 执行立即切换/冷却/退避逻辑。
          3. 在冷却期间每秒 yield 一次 countdown 状态事件。
          4. 冷却结束后若成功切换到可用代理，yield 恢复 running 状态。

        输入参数：
          reason  : str — 代理失败原因描述。
          attempt : int | None — 当前 create 尝试次数，用于状态展示。

        返回值：
          Generator[BuyStreamEvent, None, None] — 代理切换与冷却过程事件。

        调用场景：
          buy_stream() 中请求异常、412 风控或代理连续失败时调用。
        """
        def replenish_proxy_pool():
            """
            尝试从配置的代理 API 自动获取新代理。

            返回值：
              tuple[bool, str|None] — (是否成功, 成功/失败消息)。
            """
            if not str(config.proxy_api_url or "").strip():
                return False, None
            try:
                request_count = int(config.proxy_api_request_count or 0)
            except (TypeError, ValueError):
                request_count = 0
            if request_count <= 0:
                request_count = max(
                    1,
                    len(
                        [
                            proxy
                            for proxy in _request.proxy_manager.proxy_list
                            if proxy.lower() != "none"
                        ]
                    ),
                )
            try:
                result = fetch_proxy_api(
                    config.proxy_api_url,
                    count=request_count,
                    protocol=config.proxy_api_protocol,
                )
                _request.replace_proxy_pool(",".join(result.proxies))
                return (
                    True,
                    f"已从代理 API 自动获取 {len(result.proxies)} 个新代理",
                )
            except Exception as exc:
                logger.warning(f"代理 API 自动获取失败: {exc}")
                return False, f"代理 API 自动获取失败: {exc}"

        immediate_message, delay_seconds = _handle_proxy_failure(
            _request,
            reason,
            proxy_backoff,
            config.notifier_config,
            replenish_proxy_pool=replenish_proxy_pool,
        )
        attempt_total = (
            effective_retry_limit if attempt is not None else state.attempt_total
        )
        if immediate_message:
            for message in immediate_message.splitlines():
                yield emit(
                    "proxy",
                    message,
                    BuyStreamUpdate(
                        current_proxy=_request.current_proxy_status(),
                        proxy_pool=_request.proxy_pool_status(),
                        cooldown_remaining=None,
                        status="running",
                        attempt_current=attempt,
                        attempt_total=attempt_total,
                    ),
                )
        if delay_seconds is None:
            return
        for remaining in range(delay_seconds, 0, -1):
            yield emit(
                "state",
                None,
                BuyStreamUpdate(
                    current_proxy=_request.current_proxy_status(),
                    proxy_pool=_request.proxy_pool_status(),
                    cooldown_remaining=remaining,
                    status="cooldown",
                    attempt_current=attempt,
                    attempt_total=attempt_total,
                ),
            )
            time.sleep(1)
        if _request.ensure_active_proxy():
            proxy_backoff.reset()
            yield emit(
                "state",
                None,
                BuyStreamUpdate(
                    current_proxy=_request.current_proxy_status(),
                    proxy_pool=_request.proxy_pool_status(),
                    cooldown_remaining=None,
                    status="running",
                    attempt_current=attempt,
                    attempt_total=attempt_total,
                ),
            )

    def handle_non_json_response(
        prefix: str,
        response,
        *,
        attempt: int | None = None,
    ) -> Generator[object, None, bool]:
        """
        闭包：处理非 JSON 响应（如 HTML 风控页、412 拦截）。

        核心作用：
          1. 诊断响应内容，生成摘要。
          2. 若检测到 "412 风控"，则转入 handle_proxy_failure() 切换代理或冷却。
          3. 否则 yield 普通错误事件。

        输入参数：
          prefix  : str — 接口名称前缀，用于日志和消息。
          response: requests.Response | 任意 — 原始响应对象。
          attempt : int | None — 当前尝试次数。

        返回值：
          bool — True 表示已按 412 风控处理；False 表示普通错误。

        调用场景：
          buy_stream() 中 JSONDecodeError 时调用。
        """
        diagnostic = _request.describe_non_json_response(response)
        summary = _summarize_non_json_response(prefix, diagnostic)
        # 出现 412 风控时，走代理失败处理，切换代理或进入冷却等待。
        if "412 风控" in summary:
            yield emit(
                "proxy",
                f"{prefix}触发 412 风控",
                BuyStreamUpdate(
                    current_proxy=_request.current_proxy_status(),
                    proxy_pool=_request.proxy_pool_status(),
                    attempt_current=attempt,
                    attempt_total=(
                        effective_retry_limit
                        if attempt is not None
                        else state.attempt_total
                    ),
                ),
            )
            yield from handle_proxy_failure(f"{prefix} 412 风控", attempt=attempt)
            return True
        yield emit(
            "attempt" if attempt is not None else "error",
            summary,
            BuyStreamUpdate(
                current_proxy=_request.current_proxy_status(),
                proxy_pool=_request.proxy_pool_status(),
                attempt_current=attempt,
                attempt_total=(
                    effective_retry_limit
                    if attempt is not None
                    else state.attempt_total
                ),
            ),
        )
        return False

    # ========================================================================
    # buy_stream 主流程开始
    # ========================================================================

    isRunning = True
    tickets_info = json.loads(config.tickets_info)
    detail = tickets_info["detail"]
    cookies = tickets_info["cookies"]
    tickets_info.pop("cookies", None)
    tickets_info["_prepare_buyer_info"] = copy.deepcopy(tickets_info["buyer_info"])
    tickets_info["buyer_info"] = json.dumps(tickets_info["buyer_info"])
    tickets_info["deliver_info"] = json.dumps(tickets_info["deliver_info"])
    masked_proxies = ProxyManager.mask_proxy_string(config.https_proxys)
    logger.info(f"目前已配置代理：{masked_proxies or '直连'}")
    _request = BiliRequest(
        cookies=cookies,
        proxy=config.https_proxys,
        proxy_failure_threshold=config.proxy_max_consecutive_failures,
        proxy_cooldown_seconds=config.proxy_cooldown_seconds,
    )
    # 启动时随机分配一个固定代理
    if _request.proxy_manager.proxy_list and len(_request.proxy_manager.proxy_list) > 1:
        from random import randint
        random_idx = randint(0, len(_request.proxy_manager.proxy_list) - 1)
        _request.proxy_manager.now_proxy_idx = random_idx
        _request.proxy_manager.apply_to_session(_request.session)
        logger.info(f"[代理分配] 本次终端固定使用代理: {_request.proxy_manager.current_proxy_display}")

    terminal_info = _extract_terminal_state_info(tickets_info)
    yield emit(
        "state",
        None,
        BuyStreamUpdate(
            fixed_proxy=_request.current_proxy_status(),
            **terminal_info,
        ),
    )

    # 注册代理使用
    from util import GlobalStatusInstance
    task_name = detail[:30] if len(detail) > 30 else detail
    GlobalStatusInstance.register_proxy_usage(_request.proxy_manager.current_proxy, task_name)

    proxy_backoff = ProxyBackoff(max_seconds=config.proxy_backoff_max_seconds)
    is_hot_project = bool(tickets_info.get("is_hot_project", False))
    use_local_token = bool(config.use_local_token)
    local_ptoken_gen = PTokenGenerator(start_seq=0) if use_local_token else None
    browser_window_state = generate_browser_window_state()
    token_payload = _build_token_payload(tickets_info)
    request_interval = max(1, int(config.interval or 1000))
    effective_retry_limit = max(1, int(config.create_retry_limit))
    effective_batch_size = max(1, int(config.create_request_batch_size))
    rate_limit_delay_ms = max(0, int(config.rate_limit_delay_ms))

    def emit_reprepare(reason: str):
        """
        闭包：发送"重新准备订单"状态事件并记录日志。

        输入参数：
          reason : str — 重新 prepare 的原因。

        返回值：
          BuyStreamEvent — "status" 类型事件。

        调用场景：
          token 失效、prepare 未返回有效 token 等需要重新 prepare 的情况。
        """
        message = _format_reprepare_reason(reason)
        logger.info(message)
        return emit("status", message)

    def refresh_hot_and_warm():
        """
        闭包：拉取项目详情并预热 HTTP/2 连接。

        核心作用：
          1. 调用 fetch_project_payload() 获取项目详情，检测 hotProject 标记。
          2. 若检测到 hotProject=True 且此前未标记，则升级为 hot 抢票策略。
          3. 调用 prewarm_h2_connection() 预热到 B站服务器的连接，减少后续请求延迟。

        返回值：无。

        调用场景：
          初始化阶段、warmup 回调、100001 错误处理、循环内主动复检时调用。
        """
        nonlocal is_hot_project
        logger.info("预热/复检：开始拉取项目详情并预热连接")
        payload = fetch_project_payload(
            request=_request, project_id=int(tickets_info["project_id"])
        )
        if bool(payload["hotProject"]) and not is_hot_project:
            is_hot_project = True
            tickets_info["is_hot_project"] = True
            logger.info("预热/复检：检测到 hotProject=True，已升级为 hot 抢票策略")
        else:
            logger.info("预热/复检完成。")
        _request.prewarm_h2_connection(f"{base_url}/")

    # 循环内主动复检项目详情：按随机 create 次数触发纯拉取，与 100001 路径共享计数。
    # fetch 落在两次 create 的 sleep 窗口，不与 create 并发。
    refresh_min_count = max(0, int(config.refresh_interval_min_count))
    refresh_max_count = max(0, int(config.refresh_interval_max_count))
    refresh_count_enabled = (
        refresh_max_count > 0 and refresh_min_count <= refresh_max_count
    )
    refresh_counter = 0
    refresh_target = (
        random.randint(refresh_min_count, refresh_max_count)
        if refresh_count_enabled
        else None
    )

    def _reset_refresh_counter():
        """
        重置计数器并重抽下一次目标次数。定时与 100001 两路径共用。
        """
        nonlocal refresh_counter, refresh_target
        refresh_counter = 0
        if refresh_count_enabled:
            refresh_target = random.randint(refresh_min_count, refresh_max_count)

    def _on_100001():
        """
        100001 错误码处理器：刷新项目详情并重置复检计数器。

        调用场景：
          _request.handle_100001() 内部回调。
        """
        refresh_hot_and_warm()
        _reset_refresh_counter()

    _request.set_100001_handler(_on_100001)

    refresh_hot_and_warm()

    yield emit(
        "proxy",
        f"当前代理: {_request.current_proxy_status()}",
        BuyStreamUpdate(
            current_proxy=_request.current_proxy_status(),
            proxy_pool=_request.proxy_pool_status(),
        ),
    )

    for wait_state in _wait_until_start(
        config.time_start,
        warmup=refresh_hot_and_warm,
    ):
        wait_message = wait_state.get("message")
        countdown_value = wait_state.get("countdown")
        countdown_seconds = wait_state.get("countdown_seconds")
        stage_value = None
        if isinstance(wait_message, str) and wait_message.startswith("0)"):
            stage_value = "等待开票"
        yield emit(
            "status",
            wait_message,
            BuyStreamUpdate(
                stage=stage_value or state.stage,
                countdown=countdown_value or state.countdown,
                countdown_seconds=(
                    countdown_seconds
                    if countdown_seconds is not None
                    else state.countdown_seconds
                ),
            ),
        )
    while isRunning:
        try:
            request_result: dict | None = None
            ticket_collection_t = current_time_ms()
            ticket_state = init_ctoken_state(
                browser_window_state=browser_window_state,
                href_length=len(
                    f"https://mall.bilibili.com/neul-next/ticket-renovation/detail.html?id={tickets_info['project_id']}"
                ),
                user_agent_length=len(_request.get_user_agent()),
                ticket_collection_t=ticket_collection_t,
            )
            # if is_hot_project:
            # hot
            if use_local_token:
                order_token = _build_order_token(tickets_info)
                yield emit(
                    "status",
                    "已启用本地 token 模式，跳过 prepare",
                    BuyStreamUpdate(stage="订单准备"),
                )
            else:
                yield emit("stage", "开始准备订单", BuyStreamUpdate(stage="订单准备"))
                prepare_ctoken_state = ticket_state.snapshot(now_ms=ticket_collection_t)
                token_payload["token"] = prepare_ctoken_state.generate_prepare_ctoken()
                request_result_normal = _request.post(
                    url=f"{base_url}/api/ticket/order/prepare?project_id={tickets_info['project_id']}",
                    data=token_payload,
                    isJson=True,
                )
    
                request_result = request_result_normal.json()
                logger.info(f"[prepare] 请求: project_id={tickets_info['project_id']}, ctoken={token_payload.get('token', '')[:50]}")
                logger.info(f"[prepare] 响应: {request_result}")
                proxy_backoff.reset()
                yield emit(
                    "status",
                    _format_status_result(
                        "订单准备结果",
                        request_result,  # type: ignore
                    ),
                )
                order_token = _extract_prepare_token(request_result)
                if not order_token:
                    yield emit_reprepare("订单准备未返回有效 token")
                    continue
            # else:
            #     # normal
            #     yield emit("status", None, BuyStreamUpdate(stage="订单准备"))
            #     if use_local_token:
            #         order_token = _build_order_token(tickets_info)
            #         yield emit(
            #             "status",
            #             "已启用本地 token 模式，跳过 prepare",
            #         )
            #     else:
            #         request_result_normal = _request.post(
            #             url=f"{base_url}/api/ticket/order/prepare?project_id={tickets_info['project_id']}",
            #             data=token_payload,
            #             isJson=True,
            #         )
            #         request_result = request_result_normal.json()
            #         proxy_backoff.reset()
            #         yield emit(
            #             "status",
            #             _format_status_result("订单准备结果", request_result),
            #         )
            #         order_token = _extract_prepare_token(request_result)
            #         if not order_token:
            #             yield emit_reprepare("订单准备未返回有效 token")
            #             time.sleep(request_interval / 1000)
            #             continue

            yield emit(
                "stage",
                "开始创建订单",
                BuyStreamUpdate(
                    stage="创建订单",
                    attempt_current=None,
                    attempt_total=effective_retry_limit,
                ),
            )
            result = None
            retry_outcome = RetryOutcome()
            token_expired = False
            terminal_result: tuple[int, dict, CreateOrderTerminalRule] | None = None
            attempt = 1
            while attempt <= effective_retry_limit:
                batch_end = min(
                    attempt + effective_batch_size - 1,
                    effective_retry_limit,
                )
                url, payload = _prepare_create_request(
                    tickets_info,
                    order_token,
                    is_hot_project=is_hot_project,
                    request_result=request_result,
                    ticket_state=ticket_state,
                    local_ptoken=local_ptoken_gen.generate(
                        ticket_state.snapshot(
                            now_ms=ticket_collection_t
                        ).generate_prepare_ctoken(),
                    ) if local_ptoken_gen else None,
                )
                while attempt <= batch_end:
                    if not isRunning:
                        yield emit("status", "抢票结束")
                        break
                    should_sleep_before_next_attempt = False
                    try:
                        logger.info(f"[create] 请求: url={url}")
                        logger.info(f"[create] ctoken={payload.get('ctoken', '')}")
                        logger.info(f"[create] ptoken={payload.get('ptoken', '')}")
                        logger.info(f"[create] body={payload}")
                        create_response = _request.post(
                            url=url,
                            data=payload,
                            isJson=True,
                        )
                        ret = create_response.json()
                        logger.info(f"[create] 响应: {ret}")
                        proxy_backoff.reset()
                        err = int(ret.get("errno", ret.get("code")))
                        retry_outcome.set_response(err, ret)
                        _request.handle_100001(err)
                        if _is_create_success(ret, err):
                            yield emit(
                                "success",
                                "创建订单成功",
                                BuyStreamUpdate(
                                    attempt_current=attempt,
                                    attempt_total=effective_retry_limit,
                                ),
                            )
                            result = (ret, err)
                            break
                        yield emit(
                            "attempt",
                            ErrorCodes.format_attempt_result(err, ret),
                            BuyStreamUpdate(
                                attempt_current=attempt,
                                attempt_total=effective_retry_limit,
                            ),
                        )
                        terminal_rule = _create_order_terminal_rule(err)
                        if terminal_rule is not None:
                            terminal_result = (err, ret, terminal_rule)
                            yield emit(
                                "status",
                                ErrorCodes.append_response_message(
                                    err,
                                    terminal_rule.message,
                                    ret,
                                ),
                                BuyStreamUpdate(
                                    attempt_current=attempt,
                                    attempt_total=effective_retry_limit,
                                    status=terminal_rule.status,
                                ),
                            )
                            break
                        if err == 100051:
                            yield emit_reprepare("token过期")
                            token_expired = True
                            break
                        if err == 100034:
                            yield emit(
                                "status",
                                f"更新票价为：{ret['data']['pay_money'] / 100}",
                                BuyStreamUpdate(
                                    attempt_current=attempt,
                                    attempt_total=effective_retry_limit,
                                ),
                            )
                            tickets_info["pay_money"] = ret["data"]["pay_money"]
                        should_sleep_before_next_attempt = True
                    except JSONDecodeError as exc:
                        handled_412 = yield from handle_non_json_response(
                            "创建订单接口",
                            create_response,
                            attempt=attempt,
                        )
                        if not handled_412:
                            retry_outcome.set_exception(exc)
                    except BiliRateLimitError as e:
                        retry_outcome.set_exception(e)
                        yield emit(
                            "attempt",
                            (
                                f"{e}，延迟 {rate_limit_delay_ms}ms 后继续"
                                if rate_limit_delay_ms > 0
                                else str(e)
                            ),
                            BuyStreamUpdate(
                                attempt_current=attempt,
                                attempt_total=effective_retry_limit,
                            ),
                        )
                        if rate_limit_delay_ms > 0:
                            time.sleep(rate_limit_delay_ms / 1000)
                        continue  # 不需要sleep
                    except RequestException as e:
                        retry_outcome.set_exception(e)
                        for message in handle_proxy_failure(
                            f"创建订单请求异常({e.__class__.__name__})",
                            attempt=attempt,
                        ):
                            yield message
                        yield emit(
                            "attempt",
                            str(e),
                            BuyStreamUpdate(
                                attempt_current=attempt,
                                attempt_total=effective_retry_limit,
                            ),
                        )
                    except Exception as e:
                        logger.exception(e)
                        retry_outcome.set_exception(e)
                        yield emit(
                            "attempt",
                            str(e),
                            BuyStreamUpdate(
                                attempt_current=attempt,
                                attempt_total=effective_retry_limit,
                            ),
                        )
                    finally:
                        attempt += 1

                    if (
                        result is not None
                        or token_expired
                        or terminal_result is not None
                    ):
                        break
                    # 按随机 create 次数主动复检项目详情（纯拉取，落在 sleep 窗口，不与 create 并发）
                    if refresh_count_enabled and refresh_target is not None:
                        refresh_counter += 1
                        if refresh_counter >= refresh_target:
                            try:
                                refresh_hot_and_warm()
                            except Exception as exc:
                                logger.warning(f"循环内项目详情复检失败（忽略）：{exc}")
                            _reset_refresh_counter()
                    if should_sleep_before_next_attempt:
                        time.sleep(request_interval / 1000)

                if (
                    result is not None
                    or token_expired
                    or terminal_result is not None
                    or not isRunning
                ):
                    break

            else:
                if config.show_random_message:
                    yield emit("status", f"群友说👴： {get_random_fail_message()}")
                yield emit(
                    "status",
                    None,
                    BuyStreamUpdate(
                        attempt_total=effective_retry_limit,
                    ),
                )
                continue
            if result is None:
                if terminal_result is not None:
                    errno, terminal_ret, terminal_rule = terminal_result
                    order_id = _extract_order_id(terminal_ret)
                    if terminal_rule.expose_payment_url and order_id is not None:
                        payment_result = {
                            "order_id": order_id,
                            "order_detail_url": get_order_detail_url(order_id),
                            "payment_code_url": None,
                            "payment_qr_url": get_order_detail_url(order_id),
                        }
                        try:
                            payment_result = build_payment_result(_request, order_id)
                        except Exception as exc:
                            yield emit(
                                "status",
                                f"获取支付二维码链接失败，将继续返回订单详情页: {exc}",
                                BuyStreamUpdate(
                                    order_id=payment_result["order_id"],
                                    order_detail_url=payment_result["order_detail_url"],
                                    payment_qr_url=payment_result["payment_qr_url"],
                                    status=terminal_rule.status,
                                ),
                            )
                        for payment_event in emit_payment_details(
                            payment_result,
                            status=terminal_rule.status,
                        ):
                            yield payment_event
                        if config.auto_open_payment_url:
                            try:
                                webbrowser.open(payment_result["order_detail_url"])
                                yield emit(
                                    "status",
                                    "已自动打开现有订单链接",
                                    BuyStreamUpdate(
                                        order_id=payment_result.get("order_id"),
                                        order_detail_url=payment_result.get(
                                            "order_detail_url"
                                        ),
                                        payment_code_url=payment_result.get(
                                            "payment_code_url"
                                        ),
                                        payment_qr_url=payment_result.get(
                                            "payment_qr_url"
                                        ),
                                        status=terminal_rule.status,
                                    ),
                                )
                            except Exception as exc:
                                yield emit("status", f"自动打开订单链接失败: {exc}")
                    break
                reason = _format_retry_reason(retry_outcome)
                yield emit(
                    "status",
                    f"本轮创建订单未成功，{reason}",
                )
                yield emit_reprepare(reason)
                continue
            # win了
            request_result, errno = result
            if errno == 0:
                notifierManager = NotifierManager.create_from_config(
                    config=config.notifier_config,
                    title="抢票成功",
                    content=f"bilibili会员购，请尽快前往订单中心付款: {detail}",
                )

                notifierManager.start_all()

                yield emit(
                    "stage",
                    "抢票成功，弹出付款二维码",
                    BuyStreamUpdate(
                        stage="抢票成功",
                        status="succeeded",
                    ),
                )
                order_id = request_result["data"]["orderId"]  # type: ignore
                payment_result = build_payment_result(_request, order_id)
                for payment_event in emit_payment_details(
                    payment_result,
                    status="succeeded",
                ):
                    yield payment_event
                if config.auto_open_payment_url:
                    try:
                        webbrowser.open(payment_result["order_detail_url"])
                        yield emit(
                            "status",
                            "已自动打开支付链接",
                            BuyStreamUpdate(
                                order_id=payment_result.get("order_id"),
                                order_detail_url=payment_result.get("order_detail_url"),
                                payment_code_url=payment_result.get("payment_code_url"),
                                payment_qr_url=payment_result.get("payment_qr_url"),
                                status="succeeded",
                            ),
                        )
                    except Exception as exc:
                        yield emit("status", f"自动打开支付链接失败: {exc}")
                if config.show_qrcode and payment_result.get("payment_code_url"):
                    qr_gen = qrcode.QRCode()
                    qr_gen.add_data(payment_result["payment_code_url"])
                    qr_gen.make(fit=True)
                    qr_gen_image = qr_gen.make_image()
                    qr_gen_image.show()  # type: ignore
                # 让 Server酱/Bark/PushPlus 等渠道的 HTTP 请求有时间发完，否则会被掐断。
                notifierManager.join_all(timeout=15)
                break
        except (HTTPError, RequestException) as e:
            logger.exception(e)
            yield emit("error", f"请求错误: {e}")
            for message in handle_proxy_failure(
                f"订单准备请求异常({e.__class__.__name__})"
            ):
                yield message
        except JSONDecodeError:
            yield from handle_non_json_response(
                "准备订单接口",
                request_result_normal,
                attempt=0,
            )
        except BiliRateLimitError as e:
            logger.warning(str(e))
            yield emit(
                "error",
                (
                    f"{e}，延迟 {rate_limit_delay_ms}ms 后重试准备订单"
                    if rate_limit_delay_ms > 0
                    else str(e)
                ),
            )
            if rate_limit_delay_ms > 0:
                time.sleep(rate_limit_delay_ms / 1000)
            yield emit_reprepare("订单准备阶段触发 HTTP 429")
        except BiliConnectionError as e:
            logger.warning(str(e))
            yield emit(
                "error",
                str(e),
            )
        except Exception as e:
            logger.exception(e)
            yield emit(
                "error",
                f"程序异常: {repr(e)}",
                BuyStreamUpdate(status="failed"),
            )

    # 注销代理使用
    GlobalStatusInstance.unregister_proxy_usage(_request.proxy_manager.current_proxy, task_name)


# ---------------------------------------------------------------------------
# buy_new_terminal — 便捷函数
# ---------------------------------------------------------------------------

def buy_new_terminal(
    config: BuyConfig,
    log_file_path: str | None = None,
    log_level: str | None = None,
    log_retention_days: int | None = None,
) -> subprocess.Popen:
    """
    便捷函数：直接在新终端窗口中启动抢票。

    核心作用：
      Buy(config).buy_new_terminal(...) 的顶层封装，供外部模块直接调用。

    输入参数：
      config             : BuyConfig — 抢票配置。
      log_file_path      : str | None — 日志文件路径。
      log_level          : str | None — 日志级别。
      log_retention_days : int | None — 日志保留天数。

    返回值：
      subprocess.Popen — 新终端子进程对象。

    调用场景：
      interface/execution.py 等外部模块调用。
    """
    return Buy(config=config).buy_new_terminal(
        log_file_path=log_file_path,
        log_level=log_level,
        log_retention_days=log_retention_days,
    )
