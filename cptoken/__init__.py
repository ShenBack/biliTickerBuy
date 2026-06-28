"""
文件说明：
- 文件整体功能：生成与维护 B站购票流程中使用的 ctoken（客户端行为令牌）以及本地派生的 ptoken，
  同时提供浏览器窗口状态模拟能力，用于在请求中携带看似真实的客户端环境指纹。
- 所属模块：cptoken 包入口模块，供 task 层或其他业务模块在 prepare/create 等请求前生成 token。
- 依赖文件：依赖 Python 标准库 base64、dataclasses、logging、random、time、typing；
  优先使用 loguru 输出日志，缺失时回退到标准 logging。
- 对外能力：对外暴露 generate_ctoken、CTokenSnapshot、CTokenRuntimeState、
  generate_browser_window_state、init_ctoken_state、sim_ctoken_state、PTokenGenerator 等，
  支持从浏览器状态初始化、快照生成、不同时刻复刻 token，以及基于 ctoken 生成本地 ptoken。
"""

import base64
from dataclasses import asdict, dataclass, field
import logging
import random
import time
from typing import TypedDict

try:
    from loguru import logger
except ImportError:  # pragma: no cover - test/runtime fallback
    logger = logging.getLogger(__name__)


def generate_ctoken(
    m1: int = -1,
    touchend: int = -1,
    m2: int = -1,
    visibilitychange: int = -1,
    m3: int = -1,
    m4: int = -1,
    openWindow: int = -1,
    m5: int = -1,
    timer: int = -1,
    timediff: float = 0,
    m6: int = -1,
    m7: int = -1,
    m8: int = -1,
    m9: int = -1,
    beforeunload: int = -1,
    ticket_collection_t: int = 0,
) -> str:
    """
    根据一组浏览器行为参数字段生成 ctoken 字符串。

    核心作用：将 m1-m9、touch 事件、visibilitychange、timer、timediff 等离散字段编码为
    32 字节二进制数据，再经 base64 编码后作为请求参数提交给 B站接口。
    输入参数：
        m1 (int, 可选)：派生字段 m1，默认 -1。
        touchend (int, 可选)：touchend 事件相关计数值，默认 -1 时随机生成 30-50。
        m2 (int, 可选)：派生字段 m2，默认 -1。
        visibilitychange (int, 可选)：页面可见性变化次数，默认 -1 时随机生成 10-50。
        m3 (int, 可选)：派生字段 m3，默认 -1。
        m4 (int, 可选)：派生字段 m4，默认 -1。
        openWindow (int, 可选)：打开窗口计数，默认 -1。
        m5 (int, 可选)：派生字段 m5，默认 -1。
        timer (int, 可选)：计时器基准值，默认 -1 时随机生成 1-10。
        timediff (float, 可选)：时间差值，默认 0。
        m6-m9 (int, 可选)：派生字段 m6-m9，默认 -1。
        beforeunload (int, 可选)：beforeunload 事件相关计数，默认 -1 时优先使用 openWindow，
            否则随机生成 10-50。
        ticket_collection_t (int, 可选)：供外部传入的时间戳，当前实现中未参与编码，默认 0。
    返回值 (str)：base64 编码后的 ctoken 字符串。
    内部关键执行逻辑：
        1. 对 -1 的 touchend、visibilitychange、beforeunload、timer 使用随机默认值填充；
        2. 定义 _b1 辅助函数，将整数转换为 1 字节 big-endian 字节，溢出时返回 b"\xff"；
        3. 按固定顺序拼接各字段字节，字段之间以 b"\x00" 分隔；
        4. timer 和 timediff 各占用 2 字节，分段编码；
        5. 对最终字节串进行 base64 编码并返回。
    调用位置：由 CTokenSnapshot.generate_prepare_ctoken、generate_create_ctoken 以及
              CTokenRuntimeState.snapshot 等方法间接或直接调用。
    """
    _ = ticket_collection_t

    if touchend == -1:
        touchend = random.randint(30, 50)
    if visibilitychange == -1:
        visibilitychange = random.randint(10, 50)
    if beforeunload == -1:
        beforeunload = openWindow if openWindow != -1 else random.randint(10, 50)
    if timer == -1:
        timer = random.randint(1, 10)

    def _b1(x: int) -> bytes:
        """
        将整数转换为 1 字节 big-endian 字节。

        核心作用：作为 ctoken 编码的底层辅助函数，确保单个字节范围内的数值正确编码。
        输入参数：
            x (int)：待转换的整数值。
        返回值 (bytes)：1 字节字节串；若发生溢出则返回 b"\xff"。
        内部关键执行逻辑：调用 int.to_bytes(1, "big") 进行转换，捕获 OverflowError 后回退为 b"\xff"。
        调用位置：由 generate_ctoken 内部在拼接各字段字节时调用。
        """
        try:
            return int(x).to_bytes(1, "big")
        except OverflowError:
            return b"\xff"

    tb = (
        _b1(m1)
        + b"\x00"
        + _b1(touchend)
        + b"\x00"
        + _b1(m2)
        + b"\x00"
        + _b1(visibilitychange)
        + b"\x00"
        + _b1(m3)
        + b"\x00"
        + _b1(m4)
        + b"\x00"
        + _b1(beforeunload)
        + b"\x00"
        + _b1(m5)
        + b"\x00"
    )
    try:
        tt = int(timer).to_bytes(2, "big")
        tb += _b1(tt[0]) + b"\x00" + _b1(tt[1]) + b"\x00"
    except OverflowError:
        tb += b"\xff\x00\xff\x00"
    try:
        tc = int(float(timediff)).to_bytes(2, "big")
        tb += _b1(tc[0]) + b"\x00" + _b1(tc[1]) + b"\x00"
    except OverflowError:
        tb += b"\xff\x00\xff\x00"
    tb += _b1(m6) + b"\x00" + _b1(m7) + b"\x00" + _b1(m8) + b"\x00" + _b1(m9) + b"\x00"
    return base64.b64encode(tb).decode("utf-8")


@dataclass(slots=True)
class CTokenSnapshot:
    """
    ctoken 静态快照。

    类设计作用：在某一固定时刻封装所有生成 ctoken 所需的字段，提供生成 prepare/create 两种
    场景 ctoken 的能力，并支持字典序列化。
    存储属性：
        m1-m9 (int)：各派生字段；
        touchend (int)：touchend 事件计数；
        visibilitychange (int)：可见性变化计数；
        openWindow (int)：打开窗口计数；
        timer (int)：计时器值；
        timediff (float)：时间差值；
        beforeunload (int)：beforeunload 事件计数，默认 -1；
        ticket_collection_t (int)：收票时刻毫秒时间戳，默认 0；
        base_timer (int)：计时器基准值，默认 0。
    整体承担业务：作为 ctoken 生成参数的不可变（dataclass 语义）数据容器，简化跨函数传递。
    """

    m1: int
    touchend: int
    m2: int
    visibilitychange: int
    m3: int
    m4: int
    openWindow: int
    m5: int
    timer: int
    timediff: float
    m6: int
    m7: int
    m8: int
    m9: int
    beforeunload: int = -1
    ticket_collection_t: int = 0
    base_timer: int = 0

    def to_dict(self) -> dict[str, int | float]:
        """
        将快照转换为字典。

        核心作用：便于日志输出、调试和序列化。
        输入参数：无。
        返回值 (dict[str, int | float])：包含所有字段名与值的字典。
        内部关键执行逻辑：使用 dataclasses.asdict 将实例字段映射为字典。
        调用位置：由 init_ctoken_state 在生成状态后输出日志时调用，也可由调试代码调用。
        """
        return asdict(self)

    def kwargs(self) -> dict[str, int | float]:
        """
        生成供 generate_ctoken 使用的关键字参数字典。

        核心作用：将快照中除 base_timer、ticket_collection_t 之外的字段打包，
        作为 generate_ctoken 的输入。
        输入参数：无。
        返回值 (dict[str, int | float])：字段名与值映射，不含 base_timer。
        内部关键执行逻辑：手动构造包含 m1-m9、touchend、visibilitychange、openWindow、
        timer、timediff、beforeunload 的字典。
        调用位置：由本类的 generate_prepare_ctoken、generate_create_ctoken 调用。
        """
        return {
            "m1": self.m1,
            "touchend": self.touchend,
            "m2": self.m2,
            "visibilitychange": self.visibilitychange,
            "m3": self.m3,
            "m4": self.m4,
            "openWindow": self.openWindow,
            "m5": self.m5,
            "timer": self.timer,
            "timediff": self.timediff,
            "m6": self.m6,
            "m7": self.m7,
            "m8": self.m8,
            "m9": self.m9,
            "beforeunload": self.beforeunload,
        }

    def generate_ctoken(self) -> str:
        """
        生成通用 ctoken（默认使用 prepare 场景逻辑）。

        核心作用：作为 generate_prepare_ctoken 的别名，兼容旧调用方。
        输入参数：无。
        返回值 (str)：base64 编码的 ctoken 字符串。
        内部关键执行逻辑：委托调用 generate_prepare_ctoken()。
        调用位置：由需要生成 ctoken 但不区分 prepare/create 的上层代码调用。
        """
        return self.generate_prepare_ctoken()

    def generate_prepare_ctoken(self) -> str:
        """
        生成 prepare 场景使用的 ctoken。

        核心作用：在 prepare 请求前生成包含完整行为字段的 ctoken。
        输入参数：无。
        返回值 (str)：base64 编码的 ctoken 字符串。
        内部关键执行逻辑：调用 kwargs() 收集字段，再调用 generate_ctoken() 编码。
        调用位置：由上层在发起 prepare 请求前调用。
        """
        return generate_ctoken(**self.kwargs())

    def generate_create_ctoken(self) -> str:
        """
        生成 create 场景使用的 ctoken。

        核心作用：在 create 请求前生成 ctoken，与 prepare 场景相比会移除 openWindow 和
        beforeunload 字段的影响（通过不传这两个字段实现）。
        输入参数：无。
        返回值 (str)：base64 编码的 ctoken 字符串。
        内部关键执行逻辑：复制 kwargs() 字典，移除 openWindow、beforeunload 键，
        再调用 generate_ctoken() 编码。
        调用位置：由上层在发起 create 请求前调用。
        """
        fields = self.kwargs()
        fields.pop("openWindow", None)
        fields.pop("beforeunload", None)
        return generate_ctoken(**fields)


class BrowserWindowState(TypedDict):
    """
    浏览器窗口状态类型定义。

    类设计作用：作为 TypedDict 约束 generate_browser_window_state 返回的字典结构，
    包含浏览器窗口、屏幕、滚动条等维度，用于后续派生 ctoken 的 m 字段。
    存储属性：
        scrollX/scrollY (int)：页面滚动偏移；
        innerWidth/innerHeight (int)：视口尺寸；
        outerWidth/outerHeight (int)：浏览器窗口尺寸；
        screenX/screenY (int)：窗口在屏幕上的位置；
        screenWidth/screenHeight (int)：屏幕分辨率；
        screenAvailWidth/screenAvailHeight (int)：可用屏幕尺寸（扣除任务栏）。
    整体承担业务：为 ctoken 环境指纹生成提供结构化的浏览器窗口状态描述。
    """

    scrollX: int
    scrollY: int
    innerWidth: int
    innerHeight: int
    outerWidth: int
    outerHeight: int
    screenX: int
    screenY: int
    screenWidth: int
    screenHeight: int
    screenAvailWidth: int
    screenAvailHeight: int


def generate_browser_window_state(
    *,
    screen_width: int | None = None,
    screen_height: int | None = None,
    maximized: bool | None = None,
    scroll: bool = False,
) -> BrowserWindowState:
    """
    生成一组模拟的浏览器窗口状态数据。

    核心作用：在没有真实浏览器环境时，构造看起来合理的窗口、屏幕与滚动条参数，
    用于后续 init_ctoken_state 派生 ctoken 字段。
    输入参数：
        screen_width (int | None, 可选)：指定屏幕宽度，为空时从常见分辨率中随机选择。
        screen_height (int | None, 可选)：指定屏幕高度，为空时从常见分辨率中随机选择。
        maximized (bool | None, 可选)：是否最大化窗口，为空时按 65% 概率随机决定。
        scroll (bool, 可选)：是否模拟页面滚动，默认 False。
    返回值 (BrowserWindowState)：包含窗口、屏幕、滚动条等字段的字典。
    内部关键执行逻辑：
        1. 若未指定分辨率则从 common_screens 中随机选择；
        2. 随机选择任务栏高度，计算可用屏幕高度；
        3. 随机决定是否最大化；
        4. 根据最大化/非最大化状态生成 outer/inner 尺寸和窗口位置；
        5. 保证 inner 尺寸不低于最小阈值；
        6. 根据 scroll 参数决定是否生成滚动偏移；
        7. 返回符合 BrowserWindowState 结构的字典。
    调用位置：由 init_ctoken_state 在缺少 browser_window_state 时调用，
              也可由上层在需要模拟环境时直接调用。
    """
    common_screens = [
        (1920, 1080),
        (2560, 1440),
        (1366, 768),
        (1440, 900),
        (1536, 864),
        (1600, 900),
        (1280, 720),
    ]

    if screen_width is None or screen_height is None:
        screen_width, screen_height = random.choice(common_screens)

    taskbar_height = random.choice([40, 48, 56, 64])
    screen_avail_width = screen_width
    screen_avail_height = screen_height - taskbar_height

    if maximized is None:
        maximized = random.random() < 0.65

    chrome_width_delta = random.choice([0, 8, 12, 16])
    chrome_height_delta = random.choice([80, 88, 96, 104, 112, 120])

    if maximized:
        outer_width = screen_avail_width
        outer_height = screen_avail_height
        screen_x = 0
        screen_y = 0
        inner_width = outer_width - chrome_width_delta
        inner_height = outer_height - chrome_height_delta
    else:
        outer_width = random.randint(
            int(screen_avail_width * 0.60),
            int(screen_avail_width * 0.90),
        )
        outer_height = random.randint(
            int(screen_avail_height * 0.60),
            int(screen_avail_height * 0.90),
        )
        max_x = max(0, screen_avail_width - outer_width)
        max_y = max(0, screen_avail_height - outer_height)
        screen_x = random.randint(0, max_x)
        screen_y = random.randint(0, max_y)
        inner_width = outer_width - chrome_width_delta
        inner_height = outer_height - chrome_height_delta

    inner_width = max(320, inner_width)
    inner_height = max(240, inner_height)

    if scroll:
        scroll_x = random.choice([0, 0, 0, random.randint(1, 200)])
        scroll_y = random.choice([0, random.randint(50, 2000)])
    else:
        scroll_x = 0
        scroll_y = 0

    return {
        "scrollX": scroll_x,
        "scrollY": scroll_y,
        "innerWidth": inner_width,
        "innerHeight": inner_height,
        "outerWidth": outer_width,
        "outerHeight": outer_height,
        "screenX": screen_x,
        "screenY": screen_y,
        "screenWidth": screen_width,
        "screenHeight": screen_height,
        "screenAvailWidth": screen_avail_width,
        "screenAvailHeight": screen_avail_height,
    }


@dataclass(slots=True)
class CTokenRuntimeState:
    """
    ctoken 运行时状态。

    类设计作用：保存生成 ctoken 所需的原始环境字段和基准时间戳，支持在任意时刻基于
    经过的秒数生成新的 CTokenSnapshot，从而模拟时间推移后的行为状态。
    存储属性：
        m1-m9 (int)：各派生字段；
        touchend (int)：touchend 事件计数；
        visibilitychange (int)：可见性变化计数；
        openWindow (int)：打开窗口计数；
        m6-m9 (int)：各派生字段；
        beforeunload (int)：beforeunload 事件计数，默认随机 1-3；
        ticket_collection_t (int)：收票时刻毫秒时间戳，默认 0；
        base_timer (int)：计时器基准值，默认随机 10-100；
        base_timediff (float)：基础时间差，默认 0；
        created_at_ms (int)：状态创建时的毫秒时间戳，默认当前时间。
    整体承担业务：作为跨请求持续维护的 ctoken 状态上下文，支持动态快照生成。
    """

    m1: int
    touchend: int
    m2: int
    visibilitychange: int
    m3: int
    m4: int
    openWindow: int
    m5: int
    m6: int
    m7: int
    m8: int
    m9: int
    beforeunload: int = field(default_factory=lambda: random.randint(1, 3))
    ticket_collection_t: int = 0
    base_timer: int = field(default_factory=lambda: random.randint(10, 100))
    base_timediff: float = 0
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def snapshot(self, now_ms: int | None = None) -> CTokenSnapshot:
        """
        基于当前运行时状态生成一个 ctoken 快照。

        核心作用：根据状态创建后的经过时间更新 timer 和 timediff，生成可用于编码 ctoken 的
        CTokenSnapshot。
        输入参数：
            now_ms (int | None, 可选)：指定的当前毫秒时间戳，为空时使用当前系统时间。
        返回值 (CTokenSnapshot)：包含更新后 timer/timediff 的静态快照。
        内部关键执行逻辑：
            1. 若未提供 now_ms 则取当前毫秒时间戳；
            2. 计算 created_at_ms 到 now_ms 的经过秒数；
            3. timediff 在 base_timediff 基础上，若 ticket_collection_t 有效则累加
               从收票时刻到当前的秒数；
            4. timer 为 base_timer 加上经过秒数；
            5. 使用其他字段保持不变的值构造 CTokenSnapshot 并返回。
        调用位置：由 kwargs() 以及上层在需要基于当前时刻生成 ctoken 时调用。
        """
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        elapsed_seconds = max(0.0, (now_ms - self.created_at_ms) / 1000)
        timediff = self.base_timediff
        if self.ticket_collection_t > 0:
            timediff += max(0.0, (now_ms - self.ticket_collection_t) / 1000)
        return CTokenSnapshot(
            m1=self.m1,
            touchend=self.touchend,
            m2=self.m2,
            visibilitychange=self.visibilitychange,
            m3=self.m3,
            m4=self.m4,
            openWindow=self.openWindow,
            m5=self.m5,
            timer=self.base_timer + int(elapsed_seconds),
            timediff=timediff,
            m6=self.m6,
            m7=self.m7,
            m8=self.m8,
            m9=self.m9,
            beforeunload=self.beforeunload,
            ticket_collection_t=self.ticket_collection_t,
            base_timer=self.base_timer,
        )

    def kwargs(self, now_ms: int | None = None) -> dict[str, int | float]:
        """
        生成当前时刻下 generate_ctoken 所需的关键字参数字典。

        核心作用：简化上层调用，直接获取编码 ctoken 所需的参数字典。
        输入参数：
            now_ms (int | None, 可选)：指定的当前毫秒时间戳，为空时使用当前系统时间。
        返回值 (dict[str, int | float])：generate_ctoken 可接受的关键字参数字典。
        内部关键执行逻辑：调用 snapshot(now_ms) 后返回其 kwargs()。
        调用位置：由上层在需要直接传入 generate_ctoken 参数时调用。
        """
        return self.snapshot(now_ms=now_ms).kwargs()


def init_ctoken_state(
    browser_window_state: BrowserWindowState | None = None,
    history_length: int = random.randint(2, 10),
    user_agent_length: int = 140,
    href_length: int = 76,
    device_pixel_ratio: float = 4.0,
    ticket_collection_t: int = 0,
) -> CTokenRuntimeState:
    """
    根据浏览器窗口状态初始化一个 ctoken 运行时状态。

    核心作用：将模拟的浏览器窗口几何、历史长度、UA 长度等环境参数通过 derive_d 算法
    派生为 m1-m9 字段，并构造 CTokenRuntimeState。
    输入参数：
        browser_window_state (BrowserWindowState | None, 可选)：浏览器窗口状态字典，
            为空时调用 generate_browser_window_state() 随机生成。
        history_length (int, 可选)：浏览器历史长度，默认随机 2-10。
        user_agent_length (int, 可选)：User-Agent 字符串长度，默认 140。
        href_length (int, 可选)：当前页面 URL 长度，默认 76。
        device_pixel_ratio (float, 可选)：设备像素比，默认 4.0。
        ticket_collection_t (int, 可选)：收票时刻毫秒时间戳，默认 0；
            传入有效值时同时会作为 created_at_ms 的初始值。
    返回值 (CTokenRuntimeState)：初始化好的运行时状态对象。
    内部关键执行逻辑：
        1. 若未提供 browser_window_state 则随机生成；
        2. 定义内部 derive_d 函数，综合窗口状态、历史长度、UA 长度、href 长度、
           像素比和当前时间模 256 计算派生值；
        3. 使用 derive_d 的结果填充 m1、m2、m3、m4、m5、m6、m7、m8、m9；
        4. 随机生成 openWindow；
        5. 传入 ticket_collection_t 控制时间基准；
        6. 输出并返回构造好的 CTokenRuntimeState。
    调用位置：由上层在购票流程初始化阶段调用，以建立后续生成 ctoken 所需的状态上下文。
    """
    if browser_window_state is None:
        browser_window_state = generate_browser_window_state()

    def derive_d(index: int) -> int:
        """
        根据浏览器环境参数派生一个 0-255 的整数值。

        核心作用：将浏览器窗口状态、历史长度、UA 长度等环境指纹混合为 ctoken 所需的 m 字段。
        输入参数：
            index (int)：派生索引，不同索引会得到不同的混合结果。
        返回值 (int)：0-255 之间的整数。
        内部关键执行逻辑：
            1. 构造 values 列表，包含窗口状态各维度、history_length、user_agent_length、
               href_length、round(10 * device_pixel_ratio) 和当前毫秒时间戳对 256 取模；
            2. 按公式 (values[index % 16] + values[(3 * index) % 16] + 17 * index) & 255 计算结果。
        调用位置：由 init_ctoken_state 在初始化 CTokenRuntimeState 各 m 字段时调用。
        """
        now_mod_256 = int(time.time() * 1000) % 256
        values = [
            browser_window_state["scrollX"],
            browser_window_state["scrollY"],
            browser_window_state["innerWidth"],
            browser_window_state["innerHeight"],
            browser_window_state["outerWidth"],
            browser_window_state["outerHeight"],
            browser_window_state["screenX"],
            browser_window_state["screenY"],
            browser_window_state["screenWidth"],
            browser_window_state["screenHeight"],
            browser_window_state["screenAvailWidth"],
            history_length,
            user_agent_length,
            href_length,
            round(10 * (device_pixel_ratio or 1)),
            now_mod_256,
        ]
        return (values[index % 16] + values[(3 * index) % 16] + 17 * index) & 255

    state = CTokenRuntimeState(
        m1=derive_d(1),
        touchend=0,
        m2=derive_d(2),
        visibilitychange=0,
        m3=derive_d(3),
        m4=derive_d(4),
        openWindow=random.randint(1, 3),
        m5=derive_d(5),
        m6=derive_d(6),
        m7=derive_d(7),
        m8=derive_d(8),
        m9=derive_d(9),
        ticket_collection_t=ticket_collection_t,
        created_at_ms=ticket_collection_t or int(time.time() * 1000),
    )
    logger.info(state.snapshot().to_dict())
    return state


def sim_ctoken_state(
    before_state: CTokenRuntimeState,
    now_ms: int | None = None,
) -> CTokenSnapshot:
    """
    基于上一次状态模拟生成当前时刻的 ctoken 快照。

    核心作用：在两次请求之间模拟 touchend、openWindow、visibilitychange 等事件计数的
    随机增量，并基于 ticket_collection_t 计算 timer 和 timediff。
    输入参数：
        before_state (CTokenRuntimeState)：上一次运行时状态。
        now_ms (int | None, 可选)：指定的当前毫秒时间戳，为空时使用当前系统时间。
    返回值 (CTokenSnapshot)：模拟后的 ctoken 快照。
    内部关键执行逻辑：
        1. 若未提供 now_ms 则取当前毫秒时间戳；
        2. 获取 before_state 在创建时刻的快照；
        3. 随机为 touchend、openWindow、visibilitychange 增加少量计数；
        4. timer 基于 base_timer 和从 ticket_collection_t 到 now_ms 的秒差计算；
        5. timediff 为从 ticket_collection_t 到 now_ms 的秒差；
        6. 返回构造好的 CTokenSnapshot。
    调用位置：由上层在需要基于已有状态模拟下一次请求的行为状态时调用。
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)

    source = before_state.snapshot(now_ms=before_state.created_at_ms)
    ticket_collection_t = source.ticket_collection_t
    base_timer = source.base_timer or source.timer
    touchend_add = random.choice([0, 0, 1, 2])
    open_window_add = random.choices([0, 0, 1], weights=[60, 20, 20], k=1)[0]
    visibilitychange_add = random.choices([0, 0, 1], weights=[60, 20, 20], k=1)[0]

    snapshot = CTokenSnapshot(
        m1=source.m1,
        touchend=source.touchend + touchend_add,
        m2=source.m2,
        visibilitychange=source.visibilitychange + visibilitychange_add,
        m3=source.m3,
        m4=source.m4,
        openWindow=source.openWindow + open_window_add,
        m5=source.m5,
        timer=base_timer + int((now_ms - ticket_collection_t) / 1000),
        timediff=max(0.0, (now_ms - ticket_collection_t) / 1000),
        m6=source.m6,
        m7=source.m7,
        m8=source.m8,
        m9=source.m9,
        ticket_collection_t=ticket_collection_t,
        base_timer=base_timer,
    )
    return snapshot


class PTokenGenerator:
    """
    本地 ptoken 生成器。

    类设计作用：根据已生成的 ctoken 在本地派生 prepare 接口所需的 ptoken，避免每次
    都依赖服务端返回，提高请求效率。
    存储属性：
        _seq (int)：调用序号，用于 ptoken 最后一字节，每次 generate 后自增。
    整体承担业务：基于 ctoken 的特定字节位置，按照固定头部、尾部和自增序号构造 32 字节 ptoken。

    ptoken 是 B站 prepare API 返回的 32 字节二进制 token（base64 编码）。
    根据日志推导，ptoken 由 ctoken 派生而来：

    字节 0-11:  固定头部 00 11 00 00 00 08 00 00 00 02 00 27
    字节 12:    固定 0x00
    字节 13:    ctoken[12]（票类型/场次 T）
    字节 14-18: 固定零
    字节 19:    ctoken[18]（V2）
    字节 20-23: 固定零
    字节 24-30: 固定尾部 00 04 00 08 00 01 00
    字节 31:    调用序号（递增）
    """

    _HEADER = bytes.fromhex("001100000008000000020027")
    _TAIL = bytes.fromhex("00040008000100")

    def __init__(self, start_seq: int = 0):
        """
        初始化 ptoken 生成器。

        核心作用：设置 ptoken 最后一字节调用序号的起始值。
        输入参数：
            start_seq (int, 可选)：初始序号，默认 0。
        返回值：无。
        内部关键执行逻辑：将 _seq 初始化为 start_seq。
        调用位置：由上层在需要生成本地 ptoken 前实例化。
        """
        self._seq = start_seq

    def generate(self, ctoken_b64: str, seq: int | None = None) -> str:
        """
        基于 ctoken 生成本地 ptoken。

        核心作用：将输入 ctoken 解码后，按固定头部、取自 ctoken 的特定字节、固定尾部和
        自增序号拼接为 32 字节 ptoken，再 base64 编码返回。
        输入参数：
            ctoken_b64 (str)：base64 编码的 ctoken 字符串。
            seq (int | None, 可选)：指定本次 ptoken 的序号，为空时使用内部 _seq 并自增。
        返回值 (str)：base64 编码的 32 字节 ptoken 字符串。
        内部关键执行逻辑：
            1. base64 解码 ctoken，校验长度必须为 32 字节；
            2. 若未指定 seq 则使用 self._seq 并自增；
            3. 创建 32 字节缓冲区；
            4. 按固定格式填充头部、ctoken[12]、ctoken[18]、尾部和 seq；
            5. base64 编码并返回。
        调用位置：由上层在发起 prepare 请求前，根据当前 ctoken 生成对应 ptoken 时调用。
        """
        c = base64.b64decode(ctoken_b64)
        if len(c) != 32:
            raise ValueError("ctoken 长度必须是 32 字节")

        if seq is None:
            seq = self._seq
            self._seq += 1

        buf = bytearray(32)
        buf[0:12] = self._HEADER
        buf[12] = 0x00
        buf[13] = c[12]
        buf[14:19] = b"\x00\x00\x00\x00\x00"
        buf[19] = c[18]
        buf[20:24] = b"\x00\x00\x00\x00"
        buf[24:31] = self._TAIL
        buf[31] = seq & 0xFF

        return base64.b64encode(bytes(buf)).decode("utf-8")


__all__ = [
    "CTokenRuntimeState",
    "CTokenSnapshot",
    "generate_ctoken",
    "generate_browser_window_state",
    "init_ctoken_state",
    "sim_ctoken_state",
    "PTokenGenerator",
]
