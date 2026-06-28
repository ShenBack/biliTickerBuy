"""
task/buy_types.py — 抢票流程中的数据类型与后台工作者定义。

文件整体功能：
  定义了抢票事件流中使用的全部数据类，包括状态快照（BuyStreamState）、
  事件对象（BuyStreamEvent）、增量更新（BuyStreamUpdate）、重试结果（RetryOutcome）、
  订单终止规则（CreateOrderTerminalRule），以及基于线程的流式工作者 LatestValueWorker
  和其特化版本 BuyStreamWorker。

所属模块：业务层 (task)
依赖文件：
  无直接业务依赖，仅依赖 Python 标准库（copy, queue, threading, typing 等）。

对外能力：
  - BuyStreamState / BuyStreamUpdate：在 buy_stream() 与 TerminalRenderer 之间传递状态。
  - BuyStreamWorker：将同步生成器转换为线程安全的可迭代事件流。
  - RetryOutcome：记录一轮 create 请求的最终结果（成功/异常/错误码）。
  - CreateOrderTerminalRule：定义某些错误码应直接终止本轮抢票并暴露支付链接。
"""

from __future__ import annotations

import copy
import queue
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Callable, Generic, TypeVar


# ---------------------------------------------------------------------------
# BuyStreamState — 抢票状态快照
# ---------------------------------------------------------------------------

@dataclass
class BuyStreamState:
    """
    抢票过程的完整状态快照。

    该类设计作用：
      作为 buy_stream() 生成器与外部消费者（TerminalRenderer、日志系统）之间的
      状态载体。每次 emit() 时，当前 BuyStreamState 的深拷贝会被包装进 BuyStreamEvent。

    存储属性：
      stage             : str  — 当前阶段描述，如"初始化"、"订单准备"、"创建订单"、"抢票成功"。
      countdown         : str  — 倒计时格式化文本，如"2小时15分30秒"。
      countdown_seconds : int|None — 倒计时剩余秒数，用于终端渲染精确刷新。
      current_proxy     : str  — 当前正在使用的代理地址或"直连"。
      proxy_pool        : str  — 代理池状态摘要。
      cooldown_remaining: int|None — 代理冷却剩余秒数；None 表示不在冷却中。
      attempt_current   : int|None — 当前 create 请求是第几次尝试。
      attempt_total     : int|None — 本轮 create 的最大尝试次数。
      payment_qr_url    : str|None — 支付二维码 URL（抢票成功后填充）。
      order_id          : int|str|None — 订单 ID（抢票成功后填充）。
      order_detail_url  : str|None — 订单详情页 URL。
      payment_code_url  : str|None — 支付码 URL。
      status            : str  — 整体状态：running / succeeded / failed / cooldown / completed。
      last_message      : str  — 最后一条人类可读消息。
      account_name      : str  — 抢票账号名称（用于终端显示）。
      buyer_name        : str  — 购票人姓名（用于终端显示）。
      ticket_type       : str  — 票种信息（用于终端显示）。
      show_time         : str  — 开售时间（用于终端显示）。
      fixed_proxy       : str  — 本次终端固定使用的代理（启动时随机分配）。
    """

    stage: str = "初始化"
    countdown: str = "-"
    countdown_seconds: int | None = None
    current_proxy: str = "未初始化"
    proxy_pool: str = ""
    cooldown_remaining: int | None = None
    attempt_current: int | None = None
    attempt_total: int | None = None
    payment_qr_url: str | None = None
    order_id: int | str | None = None
    order_detail_url: str | None = None
    payment_code_url: str | None = None
    status: str = "running"
    last_message: str = ""
    account_name: str = ""
    buyer_name: str = ""
    ticket_type: str = ""
    show_time: str = ""
    fixed_proxy: str = ""


# ---------------------------------------------------------------------------
# BuyStreamEvent — 抢票事件对象
# ---------------------------------------------------------------------------

@dataclass
class BuyStreamEvent:
    """
    单次抢票事件。

    该类设计作用：
      buy_stream() 生成器每次 yield 的对象，携带事件类型、消息、当前状态快照和增量数据。
      被 BuyStreamWorker 放入队列，供终端渲染器消费。

    存储属性：
      kind    : str  — 事件类型标识，如 "status" / "stage" / "attempt" / "success" /
                       "error" / "proxy" / "payment_qr" / "state"。
      message : str|None — 人类可读消息文本；None 表示纯状态更新事件。
      state   : BuyStreamState — 事件触发时的完整状态快照（深拷贝）。
      data    : dict — 本次事件附带的增量数据字典（通常来自 BuyStreamUpdate.to_dict()）。
    """

    kind: str
    message: str | None
    state: BuyStreamState
    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# BuyStreamUpdate — 状态增量更新
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BuyStreamUpdate:
    """
    状态增量更新对象。

    该类设计作用：
      buy_stream() 内部使用，每次状态变化时构造一个 BuyStreamUpdate，
      仅设置需要变更的字段（其余保持 None），然后调用 apply_to(state) 合并到状态中。
      相比每次都构造完整 BuyStreamState，这种方式更安全且避免遗漏字段。

    存储属性：
      与 BuyStreamState 同名字段，但类型均为 Optional（None 表示不更新该字段）。
      额外包含 fixed_proxy 字段，用于记录启动时分配的固定代理。
    """

    stage: str | None = None
    countdown: str | None = None
    countdown_seconds: int | None = None
    current_proxy: str | None = None
    proxy_pool: str | None = None
    cooldown_remaining: int | None = None
    attempt_current: int | None = None
    attempt_total: int | None = None
    payment_qr_url: str | None = None
    order_id: int | str | None = None
    order_detail_url: str | None = None
    payment_code_url: str | None = None
    status: str | None = None
    account_name: str | None = None
    buyer_name: str | None = None
    ticket_type: str | None = None
    show_time: str | None = None
    fixed_proxy: str | None = None

    def apply_to(self, state: BuyStreamState) -> None:
        """
        将本增量更新合并到目标状态对象。

        核心作用：
          遍历本对象的所有字段，若字段值不为 None，则覆盖 state 中的对应属性。
          这是 buy_stream() 中 emit() 的核心步骤。

        输入参数：
          state : BuyStreamState
            要被更新的目标状态对象（在 buy_stream() 中是外层闭包变量，会被原地修改）。

        返回值：无（原地修改 state）。

        调用场景：
          每次 emit() 时调用，将本次事件的增量更新应用到全局状态。
        """
        if self.stage is not None:
            state.stage = self.stage
        if self.countdown is not None:
            state.countdown = self.countdown
        if self.countdown_seconds is not None:
            state.countdown_seconds = self.countdown_seconds
        if self.current_proxy is not None:
            state.current_proxy = self.current_proxy
        if self.proxy_pool is not None:
            state.proxy_pool = self.proxy_pool
        if self.cooldown_remaining is not None:
            state.cooldown_remaining = self.cooldown_remaining
        if self.attempt_current is not None:
            state.attempt_current = self.attempt_current
        if self.attempt_total is not None:
            state.attempt_total = self.attempt_total
        if self.payment_qr_url is not None:
            state.payment_qr_url = self.payment_qr_url
        if self.order_id is not None:
            state.order_id = self.order_id
        if self.order_detail_url is not None:
            state.order_detail_url = self.order_detail_url
        if self.payment_code_url is not None:
            state.payment_code_url = self.payment_code_url
        if self.status is not None:
            state.status = self.status
        if self.account_name is not None:
            state.account_name = self.account_name
        if self.buyer_name is not None:
            state.buyer_name = self.buyer_name
        if self.ticket_type is not None:
            state.ticket_type = self.ticket_type
        if self.show_time is not None:
            state.show_time = self.show_time
        if self.fixed_proxy is not None:
            state.fixed_proxy = self.fixed_proxy

    def to_dict(self) -> dict:
        """
        将本增量更新转换为字典，用于 BuyStreamEvent.data。

        核心作用：
          仅导出非 None 的字段，减少事件体积。

        输入参数：无。

        返回值：
          dict
            键值对字典，仅包含值不为 None 的字段。

        调用场景：
          emit() 构造 BuyStreamEvent 时调用，将增量数据存入 event.data。
        """
        data: dict = {}
        if self.stage is not None:
            data["stage"] = self.stage
        if self.countdown is not None:
            data["countdown"] = self.countdown
        if self.countdown_seconds is not None:
            data["countdown_seconds"] = self.countdown_seconds
        if self.current_proxy is not None:
            data["current_proxy"] = self.current_proxy
        if self.proxy_pool is not None:
            data["proxy_pool"] = self.proxy_pool
        if self.cooldown_remaining is not None:
            data["cooldown_remaining"] = self.cooldown_remaining
        if self.attempt_current is not None:
            data["attempt_current"] = self.attempt_current
        if self.attempt_total is not None:
            data["attempt_total"] = self.attempt_total
        if self.payment_qr_url is not None:
            data["payment_qr_url"] = self.payment_qr_url
        if self.order_id is not None:
            data["order_id"] = self.order_id
        if self.order_detail_url is not None:
            data["order_detail_url"] = self.order_detail_url
        if self.payment_code_url is not None:
            data["payment_code_url"] = self.payment_code_url
        if self.status is not None:
            data["status"] = self.status
        if self.account_name is not None:
            data["account_name"] = self.account_name
        if self.buyer_name is not None:
            data["buyer_name"] = self.buyer_name
        if self.ticket_type is not None:
            data["ticket_type"] = self.ticket_type
        if self.show_time is not None:
            data["show_time"] = self.show_time
        if self.fixed_proxy is not None:
            data["fixed_proxy"] = self.fixed_proxy
        return data


# ---------------------------------------------------------------------------
# RetryOutcome — 一轮 create 请求的结果记录
# ---------------------------------------------------------------------------

@dataclass
class RetryOutcome:
    """
    记录一轮 create 订单请求的最终结果。

    该类设计作用：
      在一轮 create 请求结束后（无论成功、失败还是异常），通过本对象汇总
      最后一次响应的错误码、响应体或异常信息，用于生成失败原因描述。

    存储属性：
      err : int|None — 最后一次响应的错误码（errno / code）。
      ret : dict|None — 最后一次响应的完整 JSON 字典。
      exc : Exception|None — 若因异常结束，记录该异常对象。
    """

    err: int | None = None
    ret: dict | None = None
    exc: Exception | None = None

    def set_response(self, err: int, ret: dict) -> None:
        """
        记录一次正常响应结果。

        核心作用：将错误码和响应体保存到本对象，同时清除之前的异常记录。

        输入参数：
          err : int  — B站 API 返回的错误码。
          ret : dict — B站 API 返回的完整响应字典。

        返回值：无。

        调用场景：
          buy_stream() 中每次 create 请求返回 JSON 后调用。
        """
        self.err = err
        self.ret = ret
        self.exc = None

    def set_exception(self, exc: Exception) -> None:
        """
        记录一次异常结果。

        核心作用：将异常对象保存到本对象，表示本轮因异常而非错误码结束。

        输入参数：
          exc : Exception — 捕获到的异常对象。

        返回值：无。

        调用场景：
          buy_stream() 中当 create 请求抛出 JSONDecodeError、RequestException 等异常时调用。
        """
        self.exc = exc


# ---------------------------------------------------------------------------
# CreateOrderTerminalRule — 订单终止规则
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CreateOrderTerminalRule:
    """
    创建订单的终止规则。

    该类设计作用：
      某些错误码（如 100003 限购、100048 已有未完成订单）表示继续重试无意义，
      应直接终止本轮抢票。本类定义了对应错误码的终止行为，包括是否暴露支付链接。

    存储属性：
      status          : str  — 终止后的状态标识，如 "completed"。
      message         : str  — 终止原因的人类可读描述。
      expose_payment_url : bool — 若为 True，尝试提取并展示已有订单的支付链接。
    """

    status: str
    message: str
    expose_payment_url: bool = False


# ---------------------------------------------------------------------------
# LatestValueWorker — 线程安全的最新值工作者
# ---------------------------------------------------------------------------

T = TypeVar("T")


class LatestValueWorker(Generic[T]):
    """
    在后台线程中运行生成器，并通过线程安全队列提供最新值的通用工作者。

    该类设计作用：
      将同步生成器（如 buy_stream()）放入独立线程执行，主线程通过 iter_events()
      消费事件，避免阻塞主线程或 Gradio 请求处理。
      使用 maxsize=1 的队列实现"最新值"语义：若消费速度慢于生产速度，
      旧值会被丢弃，消费者总是拿到最新状态。

    存储属性：
      _producer       : Callable — 生成器工厂函数（如 buy_stream）。
      _args / _kwargs : 传给生成器工厂的位置/关键字参数。
      _queue          : queue.Queue(maxsize=1) — 单槽队列，用于跨线程传递值。
      _done           : threading.Event — 标记生成器是否已结束。
      _lock           : threading.Lock — 保护 _latest_value 的读写。
      _latest_value   : T|None — 最新值的深拷贝副本。
      _error          : BaseException|None — 若生成器抛出异常，记录在此。
      _thread         : threading.Thread — 后台工作线程（daemon=True）。
    """

    def __init__(self, producer: Callable[..., Iterable[T]], *args, **kwargs):
        """
        构造工作者，但尚未启动线程。

        核心作用：保存生成器工厂和参数，初始化队列、锁、事件等线程同步原语。

        输入参数：
          producer : Callable[..., Iterable[T]]
            生成器工厂函数；在线程中会以 producer(*args, **kwargs) 调用。
          *args, **kwargs :
            传给 producer 的位置和关键字参数。

        返回值：无。

        调用场景：
          由子类 BuyStreamWorker 或外部代码直接实例化。
        """
        self._producer = producer
        self._args = args
        self._kwargs = kwargs
        self._queue: queue.Queue[T] = queue.Queue(maxsize=1)
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._latest_value: T | None = None
        self._error: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="latest-value-worker",
            daemon=True,
        )

    def start(self) -> "LatestValueWorker[T]":
        """
        启动后台工作线程。

        核心作用：启动 _thread，使其开始执行 _run() 方法中的生成器循环。

        输入参数：无。

        返回值：
          LatestValueWorker[T]
            返回 self，支持链式调用（如 LatestValueWorker(...).start()）。

        调用场景：
          外部代码在构造后立即调用；BuyStreamWorker.start_buy_stream_worker() 也调用此方法。
        """
        self._thread.start()
        return self

    def _publish(self, value: T) -> None:
        """
        将生成器产出的值发布到队列和最新值缓存。

        核心作用：
          1. 在锁保护下更新 _latest_value（深拷贝，避免跨线程数据竞争）。
          2. 将值放入 _queue；若队列已满（maxsize=1），先丢弃旧值再入队，
             确保消费者总是获取最新状态。

        输入参数：
          value : T
            生成器产出的值（如 BuyStreamEvent）。

        返回值：无。

        调用场景：
          仅在 _run() 的生成器循环中被调用。
        """
        with self._lock:
            self._latest_value = copy.deepcopy(value)
        while True:
            try:
                self._queue.put_nowait(value)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    return

    def _run(self) -> None:
        """
        后台线程的主循环。

        核心作用：
          调用 self._producer(*self._args, **self._kwargs) 获取生成器，
          逐个迭代产出值，通过 _publish() 发送到队列。
          若生成器抛出异常，记录到 _error 并设置 _done 事件。

        输入参数：无（使用 self._producer 和 self._args / self._kwargs）。

        返回值：无。

        调用场景：
          仅由后台线程在 start() 后自动执行，外部不应直接调用。
        """
        try:
            for value in self._producer(*self._args, **self._kwargs):
                self._publish(value)
        except BaseException as exc:
            self._error = exc
        finally:
            self._done.set()

    def is_alive(self) -> bool:
        """
        检查后台线程是否仍在运行。

        返回值：bool，线程存活状态。

        调用场景：
          外部监控线程健康状态；TerminalRenderer 的刷新循环中可能使用。
        """
        return self._thread.is_alive()

    def done(self) -> bool:
        """
        检查生成器是否已结束（正常结束或异常结束）。

        返回值：bool，_done 事件是否被设置。

        调用场景：
          iter_events() 的循环条件判断；外部轮询任务是否完成。
        """
        return self._done.is_set()

    def latest_value(self) -> T | None:
        """
        获取最新值的深拷贝副本（非阻塞）。

        核心作用：
          不等待队列，直接读取 _latest_value 的深拷贝。
          适合 UI 或渲染器的"拉取最新状态"场景。

        返回值：T | None，最新值或 None（尚无产出）。

        调用场景：
          TerminalRenderer 的实时刷新、UI 面板的状态更新。
        """
        with self._lock:
            return copy.deepcopy(self._latest_value)

    def get_value(self, timeout: float | None = None) -> T | None:
        """
        从队列中阻塞获取一个值。

        核心作用：
          若消费者希望按顺序消费每个事件（而非只取最新），可使用此方法。
          超时返回 None。

        输入参数：
          timeout : float | None
            阻塞等待的最大秒数；None 表示无限等待。

        返回值：
          T | None — 队列中的值；超时返回 None。

        调用场景：
          iter_events() 的内部实现；消费者按顺序读取事件流。
        """
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def raise_if_failed(self) -> None:
        """
        若后台线程因异常结束，抛出该异常。

        核心作用：
          将后台线程的异常传播到主线程，避免静默失败。

        返回值：无；若 _error 不为 None 则抛出异常。

        调用场景：
          iter_events() 在消费完所有事件后调用；外部在 join() 后检查。
        """
        if self._error is not None:
            raise self._error

    def join(self, timeout: float | None = None) -> None:
        """
        等待后台线程结束。

        输入参数：
          timeout : float | None — 最大等待秒数；None 表示无限等待。

        返回值：无。

        调用场景：
          外部需要等待任务完成后再继续执行。
        """
        self._thread.join(timeout=timeout)


# ---------------------------------------------------------------------------
# BuyStreamWorker — 特化为 BuyStreamEvent 的 LatestValueWorker
# ---------------------------------------------------------------------------

class BuyStreamWorker(LatestValueWorker[BuyStreamEvent]):
    """
    抢票事件流的特化工作者。

    该类设计作用：
      继承 LatestValueWorker[BuyStreamEvent]，提供类型更明确的方法名
      （latest_event / get_event / iter_events），并封装 start_buy_stream_worker()
      工厂方法，使 buy_stream() 的启动更简洁。

    存储属性：同 LatestValueWorker，泛型参数 T 被绑定为 BuyStreamEvent。
    """

    def __init__(
        self, producer: Callable[..., Iterable[BuyStreamEvent]], *args, **kwargs
    ):
        """
        构造抢票事件工作者。

        输入参数：
          producer : Callable[..., Iterable[BuyStreamEvent]]
            生成 BuyStreamEvent 的生成器工厂（如 buy_stream 的绑定结果）。
          *args, **kwargs : 传给 producer 的参数。

        返回值：无。

        调用场景：
          通常不直接调用，而是通过 start_buy_stream_worker() 静态工厂方法创建。
        """
        super().__init__(producer, *args, **kwargs)

    def latest_event(self) -> BuyStreamEvent | None:
        """
        获取最新事件的深拷贝副本。

        返回值：BuyStreamEvent | None。

        调用场景：
          TerminalRenderer 拉取最新状态用于刷新显示。
        """
        return self.latest_value()

    def get_event(self, timeout: float | None = None) -> BuyStreamEvent | None:
        """
        阻塞获取下一个事件。

        输入参数：
          timeout : float | None — 等待超时秒数。

        返回值：BuyStreamEvent | None；超时返回 None。

        调用场景：
          iter_events() 的内部循环；消费者按顺序消费事件。
        """
        return self.get_value(timeout=timeout)

    def iter_events(self, *, timeout: float = 0.1):
        """
        迭代消费事件流，直到生成器结束。

        核心作用：
          在生成器未结束时，每次阻塞 timeout 秒等待新事件；
          生成器结束后，立即消费队列中剩余的所有事件；
          最后若后台线程抛出了异常，将其传播到主线程。

        输入参数：
          timeout : float — 每次等待新事件的超时秒数，默认 0.1 秒。

        返回值：
          Generator[BuyStreamEvent, None, None]
            可迭代的事件流。

        内部逻辑：
          1. while not self.done(): 循环调用 get_event(timeout) 产出事件。
          2. while True: 在 done 后迅速清空队列中剩余事件。
          3. 调用 raise_if_failed() 传播异常。

        调用场景：
          app_cmd/buy.py 的 run_with_terminal_renderer()、
          interface/execution.py 的 _run_buy_task() 等。
        """
        while not self.done():
            event = self.get_event(timeout=timeout)
            if event is not None:
                yield event

        while True:
            event = self.get_event(timeout=0)
            if event is None:
                break
            yield event

        self.raise_if_failed()

    @staticmethod
    def start_buy_stream_worker(
        producer: Callable[..., Iterable[BuyStreamEvent]], *args, **kwargs
    ) -> "BuyStreamWorker":
        """
        静态工厂方法：创建并启动 BuyStreamWorker。

        核心作用：
          将构造和启动合并为一步，减少外部代码的样板。

        输入参数：
          producer : Callable[..., Iterable[BuyStreamEvent]]
            事件生成器工厂。
          *args, **kwargs : 传给 producer 的参数。

        返回值：
          BuyStreamWorker
            已启动的工作者实例。

        调用场景：
          Buy.start_worker()、app_cmd/buy.py、interface/execution.py 等处统一使用此方法启动抢票。
        """
        return BuyStreamWorker(producer, *args, **kwargs).start()
