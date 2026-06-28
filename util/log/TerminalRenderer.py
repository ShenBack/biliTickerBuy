"""
文件整体功能：为抢票任务提供终端界面渲染能力，支持纯文本回退与 Textual/Rich 富文本终端。
所属模块：util.log
依赖文件：无外部业务依赖，按需导入 rich、textual 等第三方库。
对外能力：
    1. 提供 TerminalRenderContext、TerminalViewState、LogItem 等数据结构；
    2. 提供 BaseTerminalRenderer、PlainTerminalRenderer、TextualTerminalRenderer 渲染器；
    3. 提供 create_terminal_renderer 工厂函数与 render_message_stream 入口函数。
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Iterable


@dataclass(frozen=True)
class TerminalRenderContext:
    """
    终端渲染上下文。

    类设计作用：为终端渲染器传递配置名称、日志文件、平台名称等静态信息。
    存储属性：
        config_name (str)：抢票配置名称。
        log_file (str)：当前任务日志文件路径。
        platform_name (str)：运行平台标识，如 "nt" 表示 Windows。
    承担业务：在创建终端渲染器时一次性传入，供 header 和状态展示使用。
    """

    config_name: str
    log_file: str
    platform_name: str


@dataclass
class TerminalViewState:
    """
    终端状态视图数据。

    类设计作用：保存抢票任务的实时状态字段，供终端界面渲染。
    存储属性：
        stage (str)：当前阶段。
        countdown (str)：倒计时显示文本。
        current_proxy (str)：当前代理显示文本。
        cooldown (str)：冷却时间显示文本。
        buyer_name (str)：购票人姓名。
        ticket_type (str)：票种信息。
        show_time (str)：开售时间。
        account_name (str)：账号名称。
        fixed_proxy (str)：固定代理信息。
    承担业务：被渲染器读取并在终端顶部或状态区展示。
    """

    stage: str = "初始化"
    countdown: str = "-"
    current_proxy: str = "未初始化"
    cooldown: str = "-"
    buyer_name: str = ""
    ticket_type: str = ""
    show_time: str = ""
    account_name: str = ""
    fixed_proxy: str = ""


@dataclass
class LogItem:
    """
    单条日志项。

    类设计作用：封装日志消息及其合并状态，用于 attempt 次数等重复日志的折叠展示。
    存储属性：
        raw_message (str)：原始消息文本。
        display_message (str)：展示用消息文本。
        count (int)：相同消息出现的次数，默认 1。
        kind (str)：消息类型，如 "normal" 或 "attempt"。
        attempt_start (int | None)：attempt 起始序号。
        attempt_end (int | None)：attempt 结束序号。
        attempt_total (int | None)：attempt 总数。
        attempt_body (str | None)：attempt 消息体。
    承担业务：在 TextualTerminalRenderer 中对连续 attempt 消息进行合并展示。
    """

    raw_message: str
    display_message: str
    count: int = 1
    kind: str = "normal"
    attempt_start: int | None = None
    attempt_end: int | None = None
    attempt_total: int | None = None
    attempt_body: str | None = None


def _extract_message_meta(item) -> tuple[str, str, int | None, int | None]:
    """
    从任意日志对象中提取消息元数据。

    参数：
        item：日志对象或字符串，可能包含 message、kind、state 等属性。
    返回值：tuple[str, str, int | None, int | None]，分别为消息文本、类型、当前 attempt 序号、总数。
    内部逻辑：通过 getattr 安全读取属性，兼容原始字符串输入。
    调用位置：_make_log_item、_can_merge_log_item、_merge_log_item 中调用。
    """
    message = getattr(item, "message", item)
    kind = getattr(item, "kind", "normal")
    state = getattr(item, "state", None)
    attempt_current = getattr(state, "attempt_current", None)
    attempt_total = getattr(state, "attempt_total", None)
    return str(message), str(kind), attempt_current, attempt_total


class BaseTerminalRenderer:
    """
    终端渲染器抽象基类。

    类设计作用：定义终端渲染器的统一接口，包括 header、消息、状态渲染与关闭。
    存储属性：
        context (TerminalRenderContext)：渲染上下文。
    承担业务：作为 PlainTerminalRenderer 与 TextualTerminalRenderer 的公共父类，
              使调用方可以统一使用不同渲染后端。
    """

    def __init__(self, context: TerminalRenderContext):
        """
        初始化基类渲染器。

        参数：
            context (TerminalRenderContext)：渲染上下文。
        返回值：无。
        内部逻辑：保存 context 到实例属性。
        调用位置：子类 __init__ 中通过 super().__init__ 调用。
        """
        self.context = context

    def render_header(self) -> None:
        """
        渲染终端头部信息。

        参数：无。
        返回值：无。
        内部逻辑：子类应实现此方法，基类默认抛出 NotImplementedError。
        调用位置：render_message_stream 开始时调用。
        """
        raise NotImplementedError

    def render_message(self, message: str) -> None:
        """
        渲染单条日志消息。

        参数：
            message (str)：要渲染的日志消息。
        返回值：无。
        内部逻辑：子类应实现此方法，基类默认抛出 NotImplementedError。
        调用位置：render_message_stream 遍历消息时调用。
        """
        raise NotImplementedError

    def render_state(self, state) -> None:
        """
        渲染任务状态。

        参数：
            state：包含任务状态字段的对象。
        返回值：None。
        内部逻辑：基类默认返回 None，子类可重写以更新状态展示。
        调用位置：render_message_stream 在消息附带状态时调用。
        """
        return None

    def close(self) -> None:
        """
        关闭渲染器并清理资源。

        参数：无。
        返回值：None。
        内部逻辑：基类默认返回 None，子类可重写以关闭线程或界面。
        调用位置：render_message_stream 结束时调用。
        """
        return None


class PlainTerminalRenderer(BaseTerminalRenderer):
    """
    纯文本终端渲染器。

    类设计作用：在不支持或不需要 Textual/Rich 的环境中，使用 print 输出抢票状态与日志。
    存储属性：
        context (TerminalRenderContext)：继承自基类的渲染上下文。
        state (TerminalViewState)：当前任务状态视图。
        _last_snapshot (tuple | None)：上一次状态快照，用于减少重复打印。
    承担业务：以纯文本形式打印配置信息、状态行和日志消息，作为稳定的 fallback 方案。
    """

    def __init__(self, context: TerminalRenderContext):
        """
        初始化纯文本渲染器。

        参数：
            context (TerminalRenderContext)：渲染上下文。
        返回值：无。
        内部逻辑：调用父类初始化，创建 TerminalViewState 实例并初始化 _last_snapshot。
        调用位置：create_terminal_renderer 在 Textual 不可用时调用。
        """
        super().__init__(context)
        self.state = TerminalViewState()
        self._last_snapshot: tuple[str, str, str, str] | None = None

    def render_header(self) -> None:
        """
        打印终端头部信息。

        参数：无。
        返回值：无。
        内部逻辑：输出配置名称与日志文件路径，并强制打印一次状态快照。
        调用位置：render_message_stream 开始时调用。
        """
        print(
            f"[抢票终端] 配置: {self.context.config_name} | 日志: {self.context.log_file}",
            flush=True,
        )
        self._print_snapshot(force=True)

    def render_message(self, item) -> None:
        """
        打印单条日志消息。

        参数：
            item：日志对象或字符串。
        返回值：无。
        内部逻辑：先打印当前状态快照，再输出消息文本。
        调用位置：render_message_stream 遍历消息时调用。
        """
        message = getattr(item, "message", item)
        self._print_snapshot()
        print(message, flush=True)

    def render_state(self, state) -> None:
        """
        更新并打印任务状态。

        参数：
            state：包含任务状态字段的对象。
        返回值：无。
        内部逻辑：使用 getattr 从 state 中读取各字段更新 self.state，然后打印快照。
        调用位置：render_message_stream 在消息附带状态时调用。
        """
        self.state.stage = getattr(state, "stage", self.state.stage)
        self.state.countdown = getattr(state, "countdown", self.state.countdown)
        self.state.current_proxy = getattr(
            state, "current_proxy", self.state.current_proxy
        )
        cooldown_remaining = getattr(state, "cooldown_remaining", None)
        self.state.cooldown = (
            f"{cooldown_remaining} 秒"
            if isinstance(cooldown_remaining, int) and cooldown_remaining > 0
            else "-"
        )
        self.state.buyer_name = getattr(state, "buyer_name", self.state.buyer_name)
        self.state.ticket_type = getattr(state, "ticket_type", self.state.ticket_type)
        self.state.show_time = getattr(state, "show_time", self.state.show_time)
        self.state.account_name = getattr(state, "account_name", self.state.account_name)
        self.state.fixed_proxy = getattr(state, "fixed_proxy", self.state.fixed_proxy)
        self._print_snapshot()

    def _print_snapshot(self, *, force: bool = False) -> None:
        """
        打印当前状态快照，若状态未变化则跳过。

        参数：
            force (bool)：是否强制打印，默认 False。
        返回值：无。
        内部逻辑：构建包含阶段、倒计时、代理、冷却及附加信息的输出行；
                  非强制模式下与 _last_snapshot 比较，无变化则直接返回。
        调用位置：render_header、render_message、render_state 内部调用。
        """
        snapshot = (
            self.state.stage,
            self.state.countdown,
            self.state.current_proxy,
            self.state.cooldown,
            self.state.buyer_name,
            self.state.ticket_type,
            self.state.show_time,
            self.state.account_name,
            self.state.fixed_proxy,
        )
        if not force and snapshot == self._last_snapshot:
            return

        # 构建关键信息行
        info_parts = []
        if self.state.account_name:
            info_parts.append(f"账号: {self.state.account_name}")
        if self.state.buyer_name:
            info_parts.append(f"购票人: {self.state.buyer_name}")
        if self.state.ticket_type:
            info_parts.append(f"票种: {self.state.ticket_type}")
        if self.state.show_time:
            info_parts.append(f"开售时间: {self.state.show_time}")
        if self.state.fixed_proxy:
            info_parts.append(f"固定IP: {self.state.fixed_proxy}")

        print(
            (
                "[状态] "
                f"阶段: {self.state.stage} | "
                f"倒计时: {self.state.countdown} | "
                f"代理: {self.state.current_proxy} | "
                f"冷却: {self.state.cooldown}"
            ),
            flush=True,
        )
        if info_parts:
            print(
                f"[信息] {' | '.join(info_parts)}",
                flush=True,
            )
        self._last_snapshot = snapshot


def _make_log_item(item) -> LogItem:
    """
    将日志对象转换为 LogItem。

    参数：
        item：日志对象或字符串。
    返回值：LogItem，封装后的日志项。
    内部逻辑：提取消息元数据；非 attempt 类型或缺少 attempt 序号时返回普通 LogItem，
              否则返回 attempt 类型的 LogItem。
    调用位置：TextualTerminalRenderer.add_message 中添加新日志项时调用。
    """
    message, kind, attempt_current, attempt_total = _extract_message_meta(item)

    if kind != "attempt" or attempt_current is None or attempt_total is None:
        return LogItem(
            raw_message=message,
            display_message=message,
            count=1,
            kind="normal",
        )

    return LogItem(
        raw_message=message,
        display_message=message,
        count=1,
        kind="attempt",
        attempt_start=attempt_current,
        attempt_end=attempt_current,
        attempt_total=attempt_total,
        attempt_body=message,
    )


def _can_merge_log_item(item: LogItem, next_item) -> bool:
    """
    判断两条日志项是否可以合并展示。

    参数：
        item (LogItem)：当前日志项。
        next_item：下一条日志对象或字符串。
    返回值：bool，可合并返回 True，否则返回 False。
    内部逻辑：
        - 普通消息要求 raw_message 相同；
        - attempt 消息要求类型一致、总数相同、消息体相同且 next attempt 序号连续。
    调用位置：TextualTerminalRenderer.add_message 中调用。
    """
    message, kind, attempt_current, attempt_total = _extract_message_meta(next_item)
    if item.kind == "normal":
        return item.raw_message == message

    if kind != "attempt" or attempt_current is None or attempt_total is None:
        return False

    if item.attempt_end is None:
        return False

    return (
        item.kind == "attempt"
        and item.attempt_total == attempt_total
        and item.attempt_body == message
        and attempt_current == item.attempt_end + 1
    )


def _merge_log_item(item: LogItem, next_item) -> None:
    """
    将下一条日志项合并到当前日志项中。

    参数：
        item (LogItem)：当前日志项，合并结果会修改此对象。
        next_item：下一条日志对象或字符串。
    返回值：无。
    内部逻辑：更新 count、attempt_end、attempt_total，并根据 attempt 范围更新 display_message。
    调用位置：TextualTerminalRenderer.add_message 中在 _can_merge_log_item 返回 True 后调用。
    """
    message, kind, attempt_current, attempt_total = _extract_message_meta(next_item)
    if item.kind == "normal":
        item.count += 1
        return

    if kind != "attempt" or attempt_current is None or attempt_total is None:
        item.count += 1
        return

    item.raw_message = message
    item.count += 1
    item.attempt_end = attempt_current
    item.attempt_total = attempt_total
    item.attempt_body = message

    if item.attempt_start == item.attempt_end:
        item.display_message = (
            f"[{item.attempt_start}/{attempt_total}] {message}".rstrip()
        )
    else:
        item.display_message = f"[{item.attempt_start}-{item.attempt_end}/{attempt_total}] {message}".rstrip()


class TextualTerminalRenderer(BaseTerminalRenderer):
    """
    基于 Textual + Rich 的富文本终端渲染器。

    类设计作用：在支持的终端中提供带颜色、面板、状态表格和日志滚动区域的沉浸式界面。
    存储属性：
        context (TerminalRenderContext)：继承自基类的渲染上下文。
        app (TicketTerminalApp)：Textual 应用实例。
        thread (Thread | None)：运行 Textual 应用的守护线程。
        threading (module)：threading 模块引用，避免在类顶层导入。
        ready (Event)：用于等待 Textual 应用启动完成的事件对象。
    承担业务：启动全屏/内联终端界面，实时展示抢票状态与彩色日志，并在退出时打印最终快照。
    """

    def __init__(self, context: TerminalRenderContext):
        """
        初始化富文本终端渲染器。

        参数：
            context (TerminalRenderContext)：渲染上下文。
        返回值：无。
        内部逻辑：
            1. 调用父类初始化；
            2. 延迟导入 threading、rich、textual 等库；
            3. 创建 TicketTerminalApp 实例与启动事件。
        调用位置：create_terminal_renderer 在平台支持且 prefer_rich 为 True 时调用。
        """
        super().__init__(context)

        import threading

        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from textual.app import App, ComposeResult
        from textual.containers import Vertical, VerticalScroll
        from textual.widgets import Static

        self.threading = threading
        self.ready = threading.Event()

        ready = self.ready

        class TicketTerminalApp(App):
            """
            Textual 抢票终端应用。

            类设计作用：定义终端界面的布局、样式、快捷键及状态/日志更新逻辑。
            存储属性：
                state (TerminalViewState)：当前状态视图。
                status_widget (Static | None)：顶部状态面板控件。
                log_container (VerticalScroll | None)：日志滚动容器。
                log_widget (Static | None)：日志内容控件。
                message_count (int)：累计消息数。
                log_items (list[LogItem])：已渲染的日志项列表。
            承担业务：在终端中渲染状态面板与可滚动日志区，并处理退出快捷键。
            """

            CSS = """
            Screen {
                background: #0f1117;
            }

            #root {
                height: 100%;
                padding: 1 2;
            }

            #status {
                height: auto;
                max-height: 20;
                margin-bottom: 1;
            }

            #log_container {
                height: 1fr;
                border: round #3b4252;
                background: #111827;
            }

            #log {
                height: auto;
                min-height: 100%;
                padding: 0 1;
            }
            """

            BINDINGS = [
                ("q", "quit", "退出"),
                ("ctrl+c", "quit", "退出"),
            ]

            def __init__(self):
                """
                初始化 Textual 应用。

                参数：无。
                返回值：无。
                内部逻辑：调用父类初始化，创建状态实例与控件引用占位符。
                调用位置：TextualTerminalRenderer.__init__ 中定义并实例化。
                """
                super().__init__()

                self.state = TerminalViewState()
                self.status_widget: Static | None = None
                self.log_container: VerticalScroll | None = None
                self.log_widget: Static | None = None

                self.message_count = 0
                self.log_items: list[LogItem] = []

            def compose(self) -> ComposeResult:
                """
                定义应用界面布局。

                参数：无。
                返回值：ComposeResult，Textual 界面组成结果。
                内部逻辑：创建 Vertical 根容器，内部包含状态 Static 和日志 VerticalScroll。
                调用位置：Textual 应用启动时由框架调用。
                """
                # 不显示 Header / Footer，所以不会出现标题栏和底部快捷键栏。
                # 退出键位放在顶部状态区里显示。
                with Vertical(id="root"):
                    self.status_widget = Static(id="status")
                    yield self.status_widget

                    with VerticalScroll(id="log_container") as log_container:
                        self.log_container = log_container
                        self.log_widget = Static(id="log")
                        yield self.log_widget

            def on_mount(self) -> None:
                """
                应用挂载后的初始化。

                参数：无。
                返回值：无。
                内部逻辑：清空标题/副标题，初始化状态与日志显示，并设置 ready 事件通知渲染器启动完成。
                调用位置：Textual 应用挂载后由框架调用。
                """
                self.title = ""
                self.sub_title = ""

                self.update_status()
                self.update_log()

                ready.set()

            def update_status(self) -> None:
                """
                更新顶部状态面板。

                参数：无。
                返回值：无。
                内部逻辑：使用 Rich Table 组织关键信息，再用 Panel 包裹后更新 status_widget。
                调用位置：on_mount、sync_state 中调用。
                """
                table = Table.grid(expand=True)
                table.add_column(style="dim", ratio=1)
                table.add_column(style="bold white", ratio=3)

                # 关键信息
                if self.state.account_name:
                    table.add_row("账号", self.state.account_name)
                if self.state.buyer_name:
                    table.add_row("购票人", self.state.buyer_name)
                if self.state.ticket_type:
                    table.add_row("票种", self.state.ticket_type)
                if self.state.show_time:
                    table.add_row("开售时间", self.state.show_time)
                if self.state.fixed_proxy:
                    table.add_row("固定IP", self.state.fixed_proxy)

                table.add_row("", "")  # 分隔行
                table.add_row(
                    "倒计时",
                    self.state.countdown,
                )
                table.add_row(
                    "代理状态",
                    self._shorten(self.state.current_proxy, 96),
                )
                table.add_row(
                    "冷却",
                    self.state.cooldown,
                )

                panel = Panel(
                    table,
                    border_style="cyan",
                    padding=(0, 1),
                    expand=True,
                )

                if self.status_widget is not None:
                    self.status_widget.update(panel)

            def update_log(self) -> None:
                """
                更新日志显示区域。

                参数：无。
                返回值：无。
                内部逻辑：将 log_items 渲染为 Rich Text 列表，用 Group 组合后更新 log_widget，
                          并自动滚动到底部。
                调用位置：on_mount、add_message 中调用。
                """
                if self.log_widget is None:
                    return

                if not self.log_items:
                    self.log_widget.update(Text("等待日志输出...", style="dim"))
                    return

                rendered = [self.render_log_item(item) for item in self.log_items]
                self.log_widget.update(Group(*rendered))
                if self.log_container is not None:
                    self.log_container.scroll_end(animate=False)

            @staticmethod
            def _shorten(text: str, width: int = 60) -> str:
                """
                截断过长文本并添加省略号。

                参数：
                    text (str)：原始文本。
                    width (int)：最大显示宽度，默认 60。
                返回值：str，截断后的文本。
                内部逻辑：空文本或 "-" 直接返回 "-"，否则按 width 截断并加 "…"。
                调用位置：update_status 等展示代理状态等长文本时调用。
                """
                if not text or text == "-":
                    return "-"
                return text if len(text) <= width else text[: width - 1] + "…"

            def sync_state(self, state) -> None:
                """
                从外部状态对象同步到应用状态。

                参数：
                    state：包含任务状态字段的对象。
                返回值：无。
                内部逻辑：使用 getattr 读取 state 字段更新 self.state，然后调用 update_status。
                调用位置：TextualTerminalRenderer.render_state 中通过 call_from_thread 调用。
                """
                self.state.stage = getattr(state, "stage", self.state.stage)
                self.state.countdown = getattr(state, "countdown", self.state.countdown)
                self.state.current_proxy = getattr(
                    state, "current_proxy", self.state.current_proxy
                )
                cooldown_remaining = getattr(state, "cooldown_remaining", None)
                self.state.cooldown = (
                    f"{cooldown_remaining} 秒"
                    if isinstance(cooldown_remaining, int) and cooldown_remaining > 0
                    else "-"
                )
                self.state.buyer_name = getattr(state, "buyer_name", self.state.buyer_name)
                self.state.ticket_type = getattr(state, "ticket_type", self.state.ticket_type)
                self.state.show_time = getattr(state, "show_time", self.state.show_time)
                self.state.account_name = getattr(state, "account_name", self.state.account_name)
                self.state.fixed_proxy = getattr(state, "fixed_proxy", self.state.fixed_proxy)
                self.update_status()

            def render_log_message(self, message: str, item: LogItem) -> Text:
                """
                根据消息内容渲染带样式的 Rich Text。

                参数：
                    message (str)：消息文本。
                    item (LogItem)：日志项对象。
                返回值：Text，带颜色/样式前缀的 Rich Text 对象。
                内部逻辑：按消息前缀或关键字匹配不同样式规则，如成功绿、警告黄、错误红、attempt 灰等。
                调用位置：render_log_item 中调用。
                """
                text = Text()

                if message.startswith(("0)", "1）", "2）", "3）")):
                    text.append("● ", style="bold cyan")
                    text.append(message, style="bold white")
                    return text

                if message.startswith("距离开始抢票还有"):
                    text.append("⏱ ", style="cyan")
                    text.append(message, style="cyan")
                    return text

                if "412风控" in message:
                    text.append("⚠ ", style="bold yellow")
                    text.append(message, style="bold yellow")
                    return text

                if (
                    message.startswith("当前代理:")
                    or message.startswith("目前已配置代理")
                    or message.startswith("切换代理到 ")
                    or message.startswith("代理冷却:")
                    or message.startswith("代理池状态:")
                    or message.startswith("所有代理当前不可用")
                ):
                    text.append("⇄ ", style="yellow")
                    text.append(message, style="yellow")
                    return text

                if "抢票成功" in message or "创建订单成功" in message:
                    text.append("✓ ", style="bold green")
                    text.append(message, style="bold green")
                    return text

                if (
                    "接口异常" in message
                    or "请求异常" in message
                    or "程序异常" in message
                ):
                    text.append("✕ ", style="bold red")
                    text.append(message, style="bold red")
                    return text

                if item.kind == "attempt":
                    if "[900001]" in message or "[900002]" in message:
                        text.append("… ", style="yellow")
                        text.append(message, style="yellow")
                    elif "[100041]" in message or "[100009]" in message:
                        text.append("… ", style="magenta")
                        text.append(message, style="magenta")
                    else:
                        text.append("… ", style="dim")
                        text.append(message, style="white")
                    return text

                text.append("  ", style="dim")
                text.append(message, style="white")
                return text

            def render_log_item(self, item: LogItem) -> Text:
                """
                渲染单条日志项（含合并计数）。

                参数：
                    item (LogItem)：日志项对象。
                返回值：Text，带样式的 Rich Text 对象。
                内部逻辑：调用 render_log_message 渲染消息，若 count 大于 1 则追加 "xN" 标记。
                调用位置：update_log 中调用。
                """
                line = self.render_log_message(item.display_message, item)

                if item.count > 1:
                    line.append(f"  x{item.count}", style="bold dim")

                return line

            def add_message(self, event) -> None:
                """
                向日志区添加一条消息，并尝试与上一条合并。

                参数：
                    event：日志对象或字符串。
                返回值：无。
                内部逻辑：递增计数器，判断能否与 log_items 最后一项合并，能则合并，否则追加新 LogItem，最后刷新日志显示。
                调用位置：TextualTerminalRenderer.render_message 中通过 call_from_thread 调用。
                """
                self.message_count += 1

                if self.log_items and _can_merge_log_item(self.log_items[-1], event):
                    _merge_log_item(self.log_items[-1], event)
                else:
                    self.log_items.append(_make_log_item(event))

                self.update_log()

        self.app = TicketTerminalApp()
        self.thread = None

    def _dump_final_snapshot(self) -> None:
        """
        在终端退出后打印最终状态快照。

        参数：无。
        返回值：无。
        内部逻辑：输出配置信息、当前状态行以及所有日志项的 display_message。
        调用位置：close 方法中在退出 Textual 应用后调用。
        """
        state = self.app.state
        print(
            f"[抢票终端] 配置: {self.context.config_name} | 日志: {self.context.log_file}",
            flush=True,
        )
        print(
            (
                "[状态] "
                f"阶段: {state.stage} | "
                f"倒计时: {state.countdown} | "
                f"代理: {state.current_proxy} | "
                f"冷却: {state.cooldown}"
            ),
            flush=True,
        )
        if not self.app.log_items:
            print("等待日志输出...", flush=True)
            return
        for item in self.app.log_items:
            print(item.display_message, flush=True)

    def render_header(self) -> None:
        """
        启动 Textual 应用并等待其就绪。

        参数：无。
        返回值：无。
        内部逻辑：
            1. 构造 run_app 闭包，自动检测 Textual 是否支持 inline / inline_no_clear 参数；
            2. 在守护线程中启动应用；
            3. 等待 ready 事件最多 5 秒，超时抛出 RuntimeError。
        调用位置：render_message_stream 开始时调用。
        """
        def run_app() -> None:
            try:
                signature = inspect.signature(self.app.run)
                params = signature.parameters

                run_kwargs = {}

                # Textual 新版本支持 inline 模式。
                # inline=True 可以避免进入全屏 alternate screen；
                # inline_no_clear=True 可以在退出后保留最后的界面输出，方便继续看日志。
                if "inline" in params:
                    run_kwargs["inline"] = True

                if "inline_no_clear" in params:
                    run_kwargs["inline_no_clear"] = True

                self.app.run(**run_kwargs)
            except TypeError:
                self.app.run()

        self.thread = self.threading.Thread(
            target=run_app,
            daemon=True,
        )
        self.thread.start()

        if not self.ready.wait(timeout=5):
            raise RuntimeError("Textual terminal renderer failed to start")

    def render_message(self, item) -> None:
        """
        在 Textual 日志区添加一条消息。

        参数：
            item：日志对象或字符串。
        返回值：无。
        内部逻辑：通过 call_from_thread 在主线程中调用 app.add_message。
        调用位置：render_message_stream 遍历消息时调用。
        """
        self.app.call_from_thread(self.app.add_message, item)

    def render_state(self, state) -> None:
        """
        同步任务状态到 Textual 界面。

        参数：
            state：包含任务状态字段的对象。
        返回值：无。
        内部逻辑：通过 call_from_thread 在主线程中调用 app.sync_state。
        调用位置：render_message_stream 在消息附带状态时调用。
        """
        self.app.call_from_thread(self.app.sync_state, state)

    def close(self) -> None:
        """
        关闭 Textual 应用并打印最终快照。

        参数：无。
        返回值：无。
        内部逻辑：
            1. 调用 app.exit 退出应用；
            2. 等待线程结束（最多 2 秒）；
            3. 调用 _dump_final_snapshot 保留最终输出。
        调用位置：render_message_stream 结束时调用。
        """
        try:
            self.app.call_from_thread(self.app.exit)
        except Exception:
            pass
        try:
            if self.thread is not None:
                self.thread.join(timeout=2)
        except Exception:
            pass
        self._dump_final_snapshot()


def create_terminal_renderer(
    context: TerminalRenderContext,
    *,
    prefer_rich: bool = True,
) -> BaseTerminalRenderer:
    """
    工厂函数：创建合适的终端渲染器。

    参数：
        context (TerminalRenderContext)：渲染上下文。
        prefer_rich (bool)：是否优先尝试富文本渲染器，默认 True。
    返回值：BaseTerminalRenderer，PlainTerminalRenderer 或 TextualTerminalRenderer 实例。
    内部逻辑：
        - Windows 平台且 prefer_rich 为 True 时优先尝试 Textual；
        - 其他平台同样优先尝试 Textual；
        - Textual 初始化失败时回退到 PlainTerminalRenderer。
    调用位置：抢票任务启动终端展示前调用。
    """
    if context.platform_name == "nt":
        try:
            if prefer_rich:
                return TextualTerminalRenderer(context)
        except Exception:
            pass
        return PlainTerminalRenderer(context)

    if prefer_rich:
        try:
            return TextualTerminalRenderer(context)
        except Exception:
            pass

    return PlainTerminalRenderer(context)


def render_message_stream(
    renderer: BaseTerminalRenderer | None,
    messages: Iterable,
    on_message=None,
) -> None:
    """
    驱动终端渲染器消费消息流。

    参数：
        renderer (BaseTerminalRenderer | None)：终端渲染器实例，None 则不渲染。
        messages (Iterable)：日志/消息可迭代对象。
        on_message (Callable | None)：每条消息的处理回调，接收消息字符串。
    返回值：无。
    内部逻辑：
        1. 若存在 renderer，先调用 render_header；
        2. 遍历 messages，提取 state 和 message；
        3. 有 state 时更新状态，有 message 时调用 on_message 并渲染消息；
        4. finally 中调用 renderer.close() 释放资源。
    调用位置：抢票任务主循环中调用，将日志流交给终端渲染器展示。
    """
    if renderer is not None:
        renderer.render_header()

    try:
        for item in messages:
            state = getattr(item, "state", None)
            message = getattr(item, "message", item)

            if renderer is not None and state is not None:
                renderer.render_state(state)

            if message is None:
                continue

            if on_message is not None:
                on_message(message)

            if renderer is not None:
                renderer.render_message(item)

    finally:
        if renderer is not None:
            renderer.close()
